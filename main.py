import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
import json
import os
import re
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from flask import Flask
from threading import Thread
import itertools

# ==================== CONFIG ====================
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is required")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.bans = True

bot = commands.Bot(
    command_prefix="?",
    intents=intents,
    case_insensitive=True,
    help_command=None
)

# ==================== DATA FILES ====================
WELCOME_FILE = "welcome.json"
WARNS_FILE = "warns.json"
NOTES_FILE = "notes.json"
LOCKS_FILE = "locks.json"
TEMPBANS_FILE = "tempbans.json"

def load_json(path: str, default: Any = None) -> Any:
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ==================== TIME PARSER ====================
def parse_time(time_str: str) -> Optional[timedelta]:
    if not time_str:
        return None
    match = re.fullmatch(r"(\d+)([smhdw])", time_str.lower().strip())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    mapping = {
        "s": timedelta(seconds=value),
        "m": timedelta(minutes=value),
        "h": timedelta(hours=value),
        "d": timedelta(days=value),
        "w": timedelta(weeks=value),
    }
    return mapping.get(unit)

def format_timedelta(td: timedelta) -> str:
    total = int(td.total_seconds())
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h"
    return f"{total // 86400}d"

# ==================== HELPERS ====================
async def resolve_member(ctx: commands.Context, arg: str) -> Optional[discord.Member]:
    if not arg:
        return None
    # Mention
    if arg.startswith("<@") and arg.endswith(">"):
        arg = arg.strip("<@!>")
    try:
        user_id = int(arg)
        member = ctx.guild.get_member(user_id)
        if member:
            return member
        # Fetch if not in cache
        try:
            return await ctx.guild.fetch_member(user_id)
        except discord.NotFound:
            return None
    except ValueError:
        # Name search
        arg_lower = arg.lower()
        for m in ctx.guild.members:
            if arg_lower in m.name.lower() or (m.nick and arg_lower in m.nick.lower()):
                return m
    return None

def can_moderate(mod: discord.Member, target: discord.Member) -> bool:
    if mod.id == target.id:
        return False
    if mod.guild.owner_id == mod.id:
        return True
    return mod.top_role > target.top_role and mod.guild_permissions.ban_members

# ==================== KEEP ALIVE (Render + UptimeRobot) ====================
app = Flask(__name__)

@app.route("/")
def home():
    return "MydinoBot is online", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

# ==================== PRESENCE ====================
presence_cycle = itertools.cycle([
    "↪ my dino park is peak",
    "↪ Developer: ¿Brashannn?"
])

@tasks.loop(seconds=10)
async def rotate_presence():
    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name=next(presence_cycle)
    )
    await bot.change_presence(activity=activity, status=discord.Status.online)

# ==================== BACKGROUND TASKS ====================
@tasks.loop(seconds=30)
async def check_tempbans_and_locks():
    now = datetime.now(timezone.utc).timestamp()

    # Tempbans
    tempbans = load_json(TEMPBANS_FILE, {})
    to_remove = []
    for guild_id_str, users in list(tempbans.items()):
        guild = bot.get_guild(int(guild_id_str))
        if not guild:
            continue
        for user_id_str, end_ts in list(users.items()):
            if now >= end_ts:
                try:
                    await guild.unban(discord.Object(id=int(user_id_str)), reason="Temporary ban expired")
                except Exception:
                    pass
                to_remove.append((guild_id_str, user_id_str))
    for g, u in to_remove:
        tempbans.get(g, {}).pop(u, None)
        if not tempbans.get(g):
            tempbans.pop(g, None)
    if to_remove:
        save_json(TEMPBANS_FILE, tempbans)

    # Locks
    locks = load_json(LOCKS_FILE, {})
    to_unlock = []
    for ch_id_str, end_ts in list(locks.items()):
        if now >= end_ts:
            channel = bot.get_channel(int(ch_id_str))
            if channel and isinstance(channel, discord.TextChannel):
                overwrite = channel.overwrites_for(channel.guild.default_role)
                overwrite.send_messages = None
                try:
                    await channel.set_permissions(channel.guild.default_role, overwrite=overwrite, reason="Lock expired")
                except Exception:
                    pass
            to_unlock.append(ch_id_str)
    for ch in to_unlock:
        locks.pop(ch, None)
    if to_unlock:
        save_json(LOCKS_FILE, locks)

# ==================== WELCOME SYSTEM ====================
class WelcomeSetupView(ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.config: Dict[str, Any] = {
            "channel_id": None,
            "message": "Welcome {mention} to **{server}**! We now have {membercount} members.",
            "color": 0x2ecc71,
            "footer": "My Dino Park",
            "image": None
        }

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This panel is not for you.", ephemeral=True)
            return False
        return True

    @ui.select(
        cls=ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Select welcome channel",
        min_values=1,
        max_values=1,
        row=0
    )
    async def channel_select(self, interaction: discord.Interaction, select: ui.ChannelSelect):
        self.config["channel_id"] = select.values[0].id
        await interaction.response.send_message(f"Channel set to {select.values[0].mention}", ephemeral=True)

    @ui.button(label="Set Message", style=discord.ButtonStyle.secondary, row=1)
    async def set_message(self, interaction: discord.Interaction, button: ui.Button):
        class MessageModal(ui.Modal, title="Welcome Message"):
            message = ui.TextInput(
                label="Embed Description",
                style=discord.TextStyle.paragraph,
                default=self.config["message"],
                max_length=2000,
                required=True
            )
            async def on_submit(modal_self, inter: discord.Interaction):
                self.config["message"] = modal_self.message.value
                await inter.response.send_message("Message updated.", ephemeral=True)
        await interaction.response.send_modal(MessageModal())

    @ui.button(label="Set Color", style=discord.ButtonStyle.secondary, row=1)
    async def set_color(self, interaction: discord.Interaction, button: ui.Button):
        class ColorModal(ui.Modal, title="Embed Color (HEX)"):
            color = ui.TextInput(
                label="HEX Color (e.g. #2ecc71 or 2ecc71)",
                placeholder="#2ecc71",
                max_length=7,
                required=True
            )
            async def on_submit(modal_self, inter: discord.Interaction):
                raw = modal_self.color.value.strip().lstrip("#")
                try:
                    self.config["color"] = int(raw, 16)
                    await inter.response.send_message(f"Color set to #{raw.upper()}", ephemeral=True)
                except ValueError:
                    await inter.response.send_message("Invalid HEX color.", ephemeral=True)
        await interaction.response.send_modal(ColorModal())

    @ui.button(label="Set Footer", style=discord.ButtonStyle.secondary, row=1)
    async def set_footer(self, interaction: discord.Interaction, button: ui.Button):
        class FooterModal(ui.Modal, title="Footer Text"):
            footer = ui.TextInput(
                label="Footer",
                default=self.config["footer"],
                max_length=200,
                required=True
            )
            async def on_submit(modal_self, inter: discord.Interaction):
                self.config["footer"] = modal_self.footer.value
                await inter.response.send_message("Footer updated.", ephemeral=True)
        await interaction.response.send_modal(FooterModal())

    @ui.button(label="Set Image", style=discord.ButtonStyle.secondary, row=2)
    async def set_image(self, interaction: discord.Interaction, button: ui.Button):
        class ImageModal(ui.Modal, title="Image URL"):
            image = ui.TextInput(
                label="Image URL (leave empty to remove)",
                required=False,
                max_length=300
            )
            async def on_submit(modal_self, inter: discord.Interaction):
                url = modal_self.image.value.strip()
                self.config["image"] = url if url else None
                await inter.response.send_message("Image updated." if url else "Image removed.", ephemeral=True)
        await interaction.response.send_modal(ImageModal())

    @ui.button(label="Preview", style=discord.ButtonStyle.secondary, row=2)
    async def preview(self, interaction: discord.Interaction, button: ui.Button):
        embed = self.build_embed(interaction.user, interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Accept", style=discord.ButtonStyle.secondary, row=2)
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if not self.config["channel_id"]:
            await interaction.response.send_message("You must select a channel first.", ephemeral=True)
            return
        data = load_json(WELCOME_FILE, {})
        data[str(interaction.guild_id)] = self.config
        save_json(WELCOME_FILE, data)
        await interaction.response.send_message("Welcome system configured successfully!", ephemeral=True)
        self.stop()

    def build_embed(self, user: discord.Member | discord.User, guild: discord.Guild) -> discord.Embed:
        msg = self.config["message"]
        msg = msg.replace("{user}", user.name)
        msg = msg.replace("{mention}", user.mention)
        msg = msg.replace("{server}", guild.name)
        msg = msg.replace("{membercount}", str(guild.member_count))
        embed = discord.Embed(
            description=msg,
            color=self.config["color"],
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=self.config["footer"])
        if self.config.get("image"):
            embed.set_image(url=self.config["image"])
        embed.set_thumbnail(url=user.display_avatar.url)
        return embed

@bot.tree.command(name="welcome-setup", description="Configure the welcome system")
@app_commands.checks.has_permissions(administrator=True)
async def welcome_setup(interaction: discord.Interaction):
    view = WelcomeSetupView(interaction.user.id)
    embed = discord.Embed(
        title="Welcome System Setup",
        description=(
            "Configure the welcome message that will be sent when a member joins.\n\n"
            "**Available placeholders:**\n"
            "`{user}` - Username\n"
            "`{mention}` - User mention\n"
            "`{server}` - Server name\n"
            "`{membercount}` - Member count"
        ),
        color=0x5865F2
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.event
async def on_member_join(member: discord.Member):
    data = load_json(WELCOME_FILE, {})
    conf = data.get(str(member.guild.id))
    if not conf or not conf.get("channel_id"):
        return
    channel = member.guild.get_channel(conf["channel_id"])
    if not channel:
        return
    view = WelcomeSetupView(0)  # dummy
    view.config = conf
    embed = view.build_embed(member, member.guild)
    try:
        await channel.send(embed=embed)
    except Exception:
        pass

# ==================== MODERATION COMMANDS ====================
@bot.command(name="lock")
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True)
async def lock(ctx: commands.Context, channel: Optional[discord.TextChannel] = None, time: Optional[str] = None):
    channel = channel or ctx.channel
    overwrite = channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Locked by {ctx.author}")

    td = parse_time(time) if time else None
    if td:
        end_ts = (datetime.now(timezone.utc) + td).timestamp()
        locks = load_json(LOCKS_FILE, {})
        locks[str(channel.id)] = end_ts
        save_json(LOCKS_FILE, locks)
        await ctx.send(f"🔒 {channel.mention} has been locked for **{format_timedelta(td)}**.")
    else:
        # Permanent until unlock
        locks = load_json(LOCKS_FILE, {})
        locks.pop(str(channel.id), None)
        save_json(LOCKS_FILE, locks)
        await ctx.send(f"🔒 {channel.mention} has been locked.")

@bot.command(name="unlock")
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True)
async def unlock(ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
    channel = channel or ctx.channel
    overwrite = channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = None
    await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Unlocked by {ctx.author}")
    locks = load_json(LOCKS_FILE, {})
    locks.pop(str(channel.id), None)
    save_json(LOCKS_FILE, locks)
    await ctx.send(f"🔓 {channel.mention} has been unlocked.")

@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def ban(ctx: commands.Context, target: str, *, reason: str = "No reason provided"):
    member = await resolve_member(ctx, target)
    if not member:
        # Try ban by ID even if not in server
        try:
            user_id = int(target.strip("<@!>"))
            await ctx.guild.ban(discord.Object(id=user_id), reason=f"{reason} | By {ctx.author}")
            await ctx.send(f"🔨 User `{user_id}` has been banned.\nReason: {reason}")
            return
        except Exception:
            await ctx.send("User not found.")
            return

    if not can_moderate(ctx.author, member):
        await ctx.send("You cannot ban this user (hierarchy).")
        return
    try:
        await member.ban(reason=f"{reason} | By {ctx.author}")
        await ctx.send(f"🔨 **{member}** has been banned.\nReason: {reason}")
    except discord.Forbidden:
        await ctx.send("I don't have permission to ban this user.")

@bot.command(name="tempban")
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def tempban(ctx: commands.Context, target: str, time: str, *, reason: str = "No reason provided"):
    td = parse_time(time)
    if not td:
        await ctx.send("Invalid time format. Use `30s`, `5m`, `2h`, `1d`, `1w`.")
        return

    member = await resolve_member(ctx, target)
    user_id = None
    if member:
        if not can_moderate(ctx.author, member):
            await ctx.send("You cannot ban this user (hierarchy).")
            return
        user_id = member.id
        try:
            await member.ban(reason=f"[TEMP {format_timedelta(td)}] {reason} | By {ctx.author}")
        except discord.Forbidden:
            await ctx.send("I don't have permission to ban this user.")
            return
    else:
        try:
            user_id = int(target.strip("<@!>"))
            await ctx.guild.ban(discord.Object(id=user_id), reason=f"[TEMP {format_timedelta(td)}] {reason} | By {ctx.author}")
        except Exception:
            await ctx.send("User not found.")
            return

    end_ts = (datetime.now(timezone.utc) + td).timestamp()
    tempbans = load_json(TEMPBANS_FILE, {})
    gkey = str(ctx.guild.id)
    if gkey not in tempbans:
        tempbans[gkey] = {}
    tempbans[gkey][str(user_id)] = end_ts
    save_json(TEMPBANS_FILE, tempbans)

    await ctx.send(f"⏳ **{member or user_id}** has been temporarily banned for **{format_timedelta(td)}**.\nReason: {reason}")

@bot.command(name="unban")
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def unban(ctx: commands.Context, user_id: str):
    try:
        uid = int(user_id.strip("<@!>"))
        await ctx.guild.unban(discord.Object(id=uid), reason=f"Unbanned by {ctx.author}")
        # Clean tempban record
        tempbans = load_json(TEMPBANS_FILE, {})
        gkey = str(ctx.guild.id)
        if gkey in tempbans:
            tempbans[gkey].pop(str(uid), None)
            if not tempbans[gkey]:
                tempbans.pop(gkey, None)
            save_json(TEMPBANS_FILE, tempbans)
        await ctx.send(f"✅ User `{uid}` has been unbanned.")
    except Exception as e:
        await ctx.send(f"Could not unban: {e}")

@bot.command(name="warn")
@commands.has_permissions(moderate_members=True)
async def warn(ctx: commands.Context, target: str, *, reason: str = "No reason provided"):
    member = await resolve_member(ctx, target)
    if not member:
        await ctx.send("User not found.")
        return
    if not can_moderate(ctx.author, member):
        await ctx.send("You cannot warn this user (hierarchy).")
        return

    warns = load_json(WARNS_FILE, {})
    gkey = str(ctx.guild.id)
    ukey = str(member.id)
    if gkey not in warns:
        warns[gkey] = {}
    if ukey not in warns[gkey]:
        warns[gkey][ukey] = []

    warn_id = len(warns[gkey][ukey]) + 1
    warns[gkey][ukey].append({
        "id": warn_id,
        "reason": reason,
        "moderator": str(ctx.author),
        "moderator_id": ctx.author.id,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })
    save_json(WARNS_FILE, warns)
    await ctx.send(f"⚠️ **{member}** has been warned (ID: `{warn_id}`).\nReason: {reason}")

@bot.command(name="warnings")
@commands.has_permissions(moderate_members=True)
async def warnings(ctx: commands.Context, target: str):
    member = await resolve_member(ctx, target)
    if not member:
        await ctx.send("User not found.")
        return
    warns = load_json(WARNS_FILE, {})
    user_warns = warns.get(str(ctx.guild.id), {}).get(str(member.id), [])
    if not user_warns:
        await ctx.send(f"**{member}** has no warnings.")
        return
    embed = discord.Embed(title=f"Warnings for {member}", color=0xe74c3c)
    for w in user_warns:
        embed.add_field(
            name=f"ID: {w['id']}",
            value=f"**Reason:** {w['reason']}\n**By:** {w['moderator']}\n**Date:** {w['timestamp'][:19]}",
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command(name="delwarn")
@commands.has_permissions(moderate_members=True)
async def delwarn(ctx: commands.Context, target: str, warn_id: int):
    member = await resolve_member(ctx, target)
    if not member:
        await ctx.send("User not found.")
        return
    warns = load_json(WARNS_FILE, {})
    gkey = str(ctx.guild.id)
    ukey = str(member.id)
    user_warns = warns.get(gkey, {}).get(ukey, [])
    new_list = [w for w in user_warns if w["id"] != warn_id]
    if len(new_list) == len(user_warns):
        await ctx.send("Warn ID not found.")
        return
    # Re-index
    for i, w in enumerate(new_list, 1):
        w["id"] = i
    warns[gkey][ukey] = new_list
    save_json(WARNS_FILE, warns)
    await ctx.send(f"Warn `{warn_id}` deleted from **{member}**.")

@bot.command(name="note")
@commands.has_permissions(moderate_members=True)
async def note(ctx: commands.Context, target: str, *, note_text: str):
    member = await resolve_member(ctx, target)
    if not member:
        await ctx.send("User not found.")
        return
    notes = load_json(NOTES_FILE, {})
    gkey = str(ctx.guild.id)
    ukey = str(member.id)
    if gkey not in notes:
        notes[gkey] = {}
    if ukey not in notes[gkey]:
        notes[gkey][ukey] = []
    note_id = len(notes[gkey][ukey]) + 1
    notes[gkey][ukey].append({
        "id": note_id,
        "note": note_text,
        "moderator": str(ctx.author),
        "timestamp": datetime.now(timezone.utc).isoformat()
    })
    save_json(NOTES_FILE, notes)
    await ctx.send(f"📝 Note `{note_id}` added to **{member}**.")

@bot.command(name="viewnotes")
@commands.has_permissions(moderate_members=True)
async def viewnotes(ctx: commands.Context, target: str):
    member = await resolve_member(ctx, target)
    if not member:
        await ctx.send("User not found.")
        return
    notes = load_json(NOTES_FILE, {})
    user_notes = notes.get(str(ctx.guild.id), {}).get(str(member.id), [])
    if not user_notes:
        await ctx.send(f"**{member}** has no notes.")
        return
    embed = discord.Embed(title=f"Notes for {member}", color=0x3498db)
    for n in user_notes:
        embed.add_field(
            name=f"ID: {n['id']}",
            value=f"{n['note']}\n*By {n['moderator']} — {n['timestamp'][:19]}*",
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command(name="delnote")
@commands.has_permissions(moderate_members=True)
async def delnote(ctx: commands.Context, target: str, note_id: int):
    member = await resolve_member(ctx, target)
    if not member:
        await ctx.send("User not found.")
        return
    notes = load_json(NOTES_FILE, {})
    gkey = str(ctx.guild.id)
    ukey = str(member.id)
    user_notes = notes.get(gkey, {}).get(ukey, [])
    new_list = [n for n in user_notes if n["id"] != note_id]
    if len(new_list) == len(user_notes):
        await ctx.send("Note ID not found.")
        return
    for i, n in enumerate(new_list, 1):
        n["id"] = i
    notes[gkey][ukey] = new_list
    save_json(NOTES_FILE, notes)
    await ctx.send(f"Note `{note_id}` deleted from **{member}**.")

@bot.command(name="cmds")
async def cmds(ctx: commands.Context):
    embed = discord.Embed(
        title="MydinoBot Commands",
        description="Prefix: `?` (case-insensitive)",
        color=0x2ecc71
    )
    embed.add_field(
        name="Moderation",
        value=(
            "`?lock [channel] [time]`\n"
            "`?unlock [channel]`\n"
            "`?ban <user> [reason]`\n"
            "`?tempban <user> <time> [reason]`\n"
            "`?unban <user-id>`\n"
            "`?warn <user> [reason]`\n"
            "`?warnings <user>`\n"
            "`?delwarn <user> <warn-id>`\n"
            "`?note <user> <note>`\n"
            "`?viewnotes <user>`\n"
            "`?delnote <user> <note-id>`"
        ),
        inline=False
    )
    embed.add_field(
        name="Admin",
        value="`/welcome-setup` — Configure welcome messages",
        inline=False
    )
    embed.set_footer(text="Time format: 30s, 5m, 2h, 1d, 1w")
    await ctx.send(embed=embed)

# ==================== ERROR HANDLING ====================
@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You don't have permission to use this command.")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send("I don't have the required permissions.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: `{error.param.name}`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Invalid argument.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        print(f"Error in {ctx.command}: {error}")

@welcome_setup.error
async def welcome_setup_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("Administrator permission required.", ephemeral=True)
    else:
        await interaction.response.send_message("An error occurred.", ephemeral=True)

# ==================== READY ====================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Slash sync failed: {e}")
    if not rotate_presence.is_running():
        rotate_presence.start()
    if not check_tempbans_and_locks.is_running():
        check_tempbans_and_locks.start()

# ==================== START ====================
if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)
