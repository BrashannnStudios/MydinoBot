import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
import os
import re
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from flask import Flask
from threading import Thread
import itertools
from motor.motor_asyncio import AsyncIOMotorClient
import commands as bot_commands   # <-- cargamos los comandos

# ==================== CONFIG ====================
TOKEN = os.getenv("DISCORD_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is required")
if not MONGO_URI:
    raise RuntimeError("MONGO_URI environment variable is required")

SYSTEM_COLOR = 0xcef3f1

# Custom Emojis
EMOJI_LUPA      = "<:Lupaemoji:1541092110869205023>"
EMOJI_DENEGADO  = "<:DenegadoEmoji:1541092093395734579>"
EMOJI_RELOJ     = "<:RelojEmoji:1541092062097707059>"
EMOJI_ARENA     = "<:RelojArenaEmoji:1541092043231731833>"
EMOJI_ACEPTAR   = "<:Aceptar:1541092022486835250>"
EMOJI_AVISO     = "<:AvisoEmoji:1541092005751431288>"
EMOJI_PLUMA     = "<:PlumaEmoji:1541091981424590948>"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.bans = True
intents.moderation = True

bot = commands.Bot(
    command_prefix="?",
    intents=intents,
    case_insensitive=True,
    help_command=None
)

# ==================== DATABASE ====================
mongo_client: AsyncIOMotorClient = None
db = None

async def init_db():
    global mongo_client, db
    mongo_client = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client["mydinobot"]
    await db.command("ping")
    print("Successfully connected to MongoDB")

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
    if arg.startswith("<@") and arg.endswith(">"):
        arg = arg.strip("<@!>")
    try:
        user_id = int(arg)
        member = ctx.guild.get_member(user_id)
        if member:
            return member
        try:
            return await ctx.guild.fetch_member(user_id)
        except discord.NotFound:
            return None
    except ValueError:
        arg_lower = arg.lower()
        for m in ctx.guild.members:
            if arg_lower in m.name.lower() or (m.nick and arg_lower in m.nick.lower()):
                return m
    return None

async def resolve_role(ctx: commands.Context, arg: str) -> Optional[discord.Role]:
    if not arg:
        return None
    if arg.startswith("<@&") and arg.endswith(">"):
        arg = arg.strip("<@&>")
    try:
        role_id = int(arg)
        return ctx.guild.get_role(role_id)
    except ValueError:
        arg_lower = arg.lower()
        for role in ctx.guild.roles:
            if arg_lower == role.name.lower():
                return role
    return None

def can_moderate(mod: discord.Member, target: discord.Member) -> bool:
    if mod.id == target.id:
        return False
    if mod.guild.owner_id == mod.id:
        return True
    return mod.top_role > target.top_role

async def get_bot_config(guild_id: int) -> dict:
    doc = await db.bot_config.find_one({"guild_id": guild_id})
    if doc:
        return doc
    return {"guild_id": guild_id, "log_channel": None, "staff_roles": [], "admin_roles": []}

async def get_welcome_config(guild_id: int) -> dict:
    doc = await db.welcome.find_one({"guild_id": guild_id})
    if doc:
        return doc
    return {
        "guild_id": guild_id,
        "channel_id": None,
        "message": "Welcome {user} to **{server}**!\nWe now have **{membercount}** members.",
        "color": 0x2ecc71,
        "footer": "My Dino Park • Enjoy your stay!",
        "image": None,
        "recommended_channels": []
    }

async def send_dm_sanction(user: discord.User | discord.Member, title: str, description: str, extra_fields: dict = None):
    try:
        embed = discord.Embed(
            title=title,
            description=description,
            color=SYSTEM_COLOR,
            timestamp=datetime.now(timezone.utc)
        )
        if extra_fields:
            for name, value in extra_fields.items():
                embed.add_field(name=name, value=value, inline=False)
        embed.set_footer(text="My Dino Park • Staff Team")
        await user.send(embed=embed)
    except Exception:
        pass

async def log_action(guild: discord.Guild, title: str, fields: dict):
    conf = await get_bot_config(guild.id)
    if not conf.get("log_channel"):
        return
    channel = guild.get_channel(conf["log_channel"])
    if not channel:
        return

    embed = discord.Embed(title=title, color=SYSTEM_COLOR, timestamp=datetime.now(timezone.utc))
    for name, value in fields.items():
        embed.add_field(name=name, value=value, inline=True)
    embed.set_footer(text="My Dino Park • Moderation Logs")
    try:
        await channel.send(embed=embed)
    except Exception:
        pass

def usage_embed(command: str, usage: str, example: str = None) -> discord.Embed:
    embed = discord.Embed(title=f"{EMOJI_DENEGADO} Incorrect Usage", color=SYSTEM_COLOR)
    embed.add_field(name="Command", value=f"`{command}`", inline=False)
    embed.add_field(name="Correct Usage", value=f"`{usage}`", inline=False)
    if example:
        embed.add_field(name="Example", value=f"`{example}`", inline=False)
    embed.set_footer(text="My Dino Park")
    return embed

# ==================== KEEP ALIVE ====================
app = Flask(__name__)

@app.route("/")
def home():
    return "MydinoBot is online", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

# ==================== PRESENCE ====================
presence_cycle = itertools.cycle([
    "↪ my dino park is peak",
    "↪ Developer: Supskevv"
])

@tasks.loop(seconds=10)
async def rotate_presence():
    activity = discord.Activity(type=discord.ActivityType.watching, name=next(presence_cycle))
    await bot.change_presence(activity=activity, status=discord.Status.online)

# ==================== BACKGROUND TASKS ====================
@tasks.loop(seconds=30)
async def check_tempbans_and_locks():
    now = datetime.now(timezone.utc).timestamp()

    async for doc in db.tempbans.find({"end_ts": {"$lte": now}}):
        guild = bot.get_guild(doc["guild_id"])
        if not guild:
            continue
        try:
            user = await bot.fetch_user(doc["user_id"])
            await guild.unban(discord.Object(id=doc["user_id"]), reason="Temporary ban expired")
            await send_dm_sanction(
                user,
                f"{EMOJI_ACEPTAR} Temporary Ban Expired",
                f"Your temporary ban in **{guild.name}** has expired.\nYou can now rejoin the server."
            )
        except Exception:
            pass
        await db.tempbans.delete_one({"_id": doc["_id"]})

    async for doc in db.locks.find({"end_ts": {"$lte": now}}):
        channel = bot.get_channel(doc["channel_id"])
        if channel and isinstance(channel, discord.TextChannel):
            overwrite = channel.overwrites_for(channel.guild.default_role)
            overwrite.send_messages = None
            try:
                await channel.set_permissions(channel.guild.default_role, overwrite=overwrite, reason="Lock expired")
            except Exception:
                pass
        await db.locks.delete_one({"_id": doc["_id"]})

# ==================== WELCOME SYSTEM ====================
class WelcomeSetupView(ui.View):
    def __init__(self, author_id: int, guild_id: int, config: dict):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.guild_id = guild_id
        self.config = config

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This panel is not for you.", ephemeral=True)
            return False
        return True

    @ui.select(cls=ui.ChannelSelect, channel_types=[discord.ChannelType.text],
               placeholder="Select welcome channel", min_values=1, max_values=1, row=0)
    async def channel_select(self, interaction: discord.Interaction, select: ui.ChannelSelect):
        self.config["channel_id"] = select.values[0].id
        await interaction.response.send_message(f"Welcome channel set to {select.values[0].mention}", ephemeral=True)

    @ui.select(cls=ui.ChannelSelect, channel_types=[discord.ChannelType.text],
               placeholder="Select recommended channels (optional)", min_values=0, max_values=5, row=1)
    async def recommended_select(self, interaction: discord.Interaction, select: ui.ChannelSelect):
        self.config["recommended_channels"] = [c.id for c in select.values]
        mentions = ", ".join(c.mention for c in select.values) if select.values else "None"
        await interaction.response.send_message(f"Recommended channels: {mentions}", ephemeral=True)

    @ui.button(label="Set Message", style=discord.ButtonStyle.secondary, row=2)
    async def set_message(self, interaction: discord.Interaction, button: ui.Button):
        class MessageModal(ui.Modal, title="Welcome Message"):
            message = ui.TextInput(label="Embed Description", style=discord.TextStyle.paragraph,
                                   default=self.config.get("message", ""), max_length=2000, required=True)
            async def on_submit(modal_self, inter: discord.Interaction):
                self.config["message"] = modal_self.message.value
                await inter.response.send_message("Message updated.", ephemeral=True)
        await interaction.response.send_modal(MessageModal())

    @ui.button(label="Set Color", style=discord.ButtonStyle.secondary, row=2)
    async def set_color(self, interaction: discord.Interaction, button: ui.Button):
        current = f"#{self.config.get('color', 0x2ecc71):06x}"
        class ColorModal(ui.Modal, title="Embed Color (HEX)"):
            color = ui.TextInput(label="HEX Color", placeholder="#2ecc71", default=current, max_length=7, required=True)
            async def on_submit(modal_self, inter: discord.Interaction):
                raw = modal_self.color.value.strip().lstrip("#")
                try:
                    self.config["color"] = int(raw, 16)
                    await inter.response.send_message(f"Color set to #{raw.upper()}", ephemeral=True)
                except ValueError:
                    await inter.response.send_message("Invalid HEX color.", ephemeral=True)
        await interaction.response.send_modal(ColorModal())

    @ui.button(label="Set Footer", style=discord.ButtonStyle.secondary, row=2)
    async def set_footer(self, interaction: discord.Interaction, button: ui.Button):
        class FooterModal(ui.Modal, title="Footer Text"):
            footer = ui.TextInput(label="Footer (supports variables)", default=self.config.get("footer", "My Dino Park"),
                                  max_length=200, required=True)
            async def on_submit(modal_self, inter: discord.Interaction):
                self.config["footer"] = modal_self.footer.value
                await inter.response.send_message("Footer updated.", ephemeral=True)
        await interaction.response.send_modal(FooterModal())

    @ui.button(label="Set Image", style=discord.ButtonStyle.secondary, row=3)
    async def set_image(self, interaction: discord.Interaction, button: ui.Button):
        class ImageModal(ui.Modal, title="Image URL"):
            image = ui.TextInput(label="Image URL (leave empty to remove)", default=self.config.get("image") or "",
                                 required=False, max_length=300)
            async def on_submit(modal_self, inter: discord.Interaction):
                url = modal_self.image.value.strip()
                self.config["image"] = url if url else None
                await inter.response.send_message("Image updated." if url else "Image removed.", ephemeral=True)
        await interaction.response.send_modal(ImageModal())

    @ui.button(label="Preview", style=discord.ButtonStyle.secondary, row=3)
    async def preview(self, interaction: discord.Interaction, button: ui.Button):
        embed = self.build_embed(interaction.user, interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Accept", style=discord.ButtonStyle.secondary, row=3)
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if not self.config.get("channel_id"):
            await interaction.response.send_message("You must select a welcome channel first.", ephemeral=True)
            return
        self.config["guild_id"] = self.guild_id
        await db.welcome.update_one(
            {"guild_id": self.guild_id},
            {"$set": self.config},
            upsert=True
        )
        await interaction.response.send_message(f"{EMOJI_ACEPTAR} Welcome system saved successfully!", ephemeral=True)
        self.stop()

    def build_embed(self, user: discord.Member | discord.User, guild: discord.Guild) -> discord.Embed:
        def replace_vars(text: str) -> str:
            if not text:
                return ""
            return (text.replace("{user}", user.mention)
                        .replace("{username}", user.name)
                        .replace("{server}", guild.name)
                        .replace("{membercount}", str(guild.member_count)))

        description = replace_vars(self.config.get("message", ""))
        footer_text = replace_vars(self.config.get("footer", "My Dino Park"))

        if self.config.get("recommended_channels"):
            channels = []
            for cid in self.config["recommended_channels"]:
                ch = guild.get_channel(cid)
                if ch:
                    channels.append(f"• {ch.mention}")
            if channels:
                description += "\n\n**Recommended Channels**\n" + "\n".join(channels)

        embed = discord.Embed(
            description=description,
            color=self.config.get("color", 0x2ecc71),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=footer_text)
        if self.config.get("image"):
            embed.set_image(url=self.config["image"])
        embed.set_thumbnail(url=user.display_avatar.url)
        return embed

@bot.tree.command(name="welcome-setup", description="Configure the welcome system")
@app_commands.checks.has_permissions(administrator=True)
async def welcome_setup(interaction: discord.Interaction):
    config = await get_welcome_config(interaction.guild_id)
    view = WelcomeSetupView(interaction.user.id, interaction.guild_id, config)
    embed = discord.Embed(
        title=f"{EMOJI_LUPA} Welcome System Setup",
        description=(
            "Configure the welcome message. **Current settings are loaded automatically.**\n\n"
            "**Available variables (also work in footer):**\n"
            "`{user}` → Mentions the user\n"
            "`{username}` → Username only\n"
            "`{server}` → Server name\n"
            "`{membercount}` → Member count"
        ),
        color=SYSTEM_COLOR
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# ==================== BOT SETUP ====================
class BotSetupView(ui.View):
    def __init__(self, author_id: int, guild_id: int, config: dict):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.guild_id = guild_id
        self.config = config

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This panel is not for you.", ephemeral=True)
            return False
        return True

    @ui.select(cls=ui.ChannelSelect, channel_types=[discord.ChannelType.text],
               placeholder="Select Logs Channel", min_values=0, max_values=1, row=0)
    async def log_channel_select(self, interaction: discord.Interaction, select: ui.ChannelSelect):
        if select.values:
            self.config["log_channel"] = select.values[0].id
            await interaction.response.send_message(f"Logs channel set to {select.values[0].mention}", ephemeral=True)
        else:
            self.config["log_channel"] = None
            await interaction.response.send_message("Logs channel removed.", ephemeral=True)

    @ui.select(cls=ui.RoleSelect, placeholder="Select Staff Roles", min_values=0, max_values=10, row=1)
    async def staff_roles_select(self, interaction: discord.Interaction, select: ui.RoleSelect):
        self.config["staff_roles"] = [r.id for r in select.values]
        names = ", ".join(r.mention for r in select.values) if select.values else "None"
        await interaction.response.send_message(f"Staff roles: {names}", ephemeral=True)

    @ui.select(cls=ui.RoleSelect, placeholder="Select Admin / Owner Roles", min_values=0, max_values=10, row=2)
    async def admin_roles_select(self, interaction: discord.Interaction, select: ui.RoleSelect):
        self.config["admin_roles"] = [r.id for r in select.values]
        names = ", ".join(r.mention for r in select.values) if select.values else "None"
        await interaction.response.send_message(f"Admin roles: {names}", ephemeral=True)

    @ui.button(label="Save Configuration", style=discord.ButtonStyle.secondary, row=3)
    async def save(self, interaction: discord.Interaction, button: ui.Button):
        self.config["guild_id"] = self.guild_id
        await db.bot_config.update_one(
            {"guild_id": self.guild_id},
            {"$set": self.config},
            upsert=True
        )
        await interaction.response.send_message(f"{EMOJI_ACEPTAR} Bot configuration saved!", ephemeral=True)
        self.stop()

@bot.tree.command(name="bot-setup", description="Configure bot settings (logs, staff roles, etc.)")
@app_commands.checks.has_permissions(administrator=True)
async def bot_setup(interaction: discord.Interaction):
    config = await get_bot_config(interaction.guild_id)
    view = BotSetupView(interaction.user.id, interaction.guild_id, config)
    embed = discord.Embed(
        title=f"{EMOJI_LUPA} Bot Setup",
        description=(
            "Configure global bot settings. **Current settings are loaded automatically.**\n\n"
            "• **Logs Channel** → Moderation logs\n"
            "• **Staff Roles** → Staff roles\n"
            "• **Admin Roles** → Admin / Owner roles"
        ),
        color=SYSTEM_COLOR
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# ==================== MEMBER JOIN ====================
@bot.event
async def on_member_join(member: discord.Member):
    conf = await get_welcome_config(member.guild.id)
    if not conf.get("channel_id"):
        return
    channel = member.guild.get_channel(conf["channel_id"])
    if not channel:
        return
    view = WelcomeSetupView(0, member.guild.id, conf)
    embed = view.build_embed(member, member.guild)
    try:
        await channel.send(embed=embed)
    except Exception:
        pass

# ==================== ERROR HANDLING ====================
@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.MissingRequiredArgument):
        return
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send(f"{EMOJI_DENEGADO} You don't have permission to use this command.")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send(f"{EMOJI_DENEGADO} I don't have the required permissions.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"{EMOJI_DENEGADO} Invalid argument provided.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        print(f"Error in {ctx.command}: {error}")

@welcome_setup.error
@bot_setup.error
async def setup_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(f"{EMOJI_DENEGADO} Administrator permission required.", ephemeral=True)
    else:
        await interaction.response.send_message("An error occurred.", ephemeral=True)

# ==================== READY ====================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        await init_db()
    except Exception as e:
        print(f"Failed to connect to MongoDB: {e}")
        return

    # Adjuntar helpers y DB al bot para que commands.py pueda usarlos
    bot.db = db
    bot.parse_time = parse_time
    bot.format_timedelta = format_timedelta
    bot.resolve_member = resolve_member
    bot.resolve_role = resolve_role
    bot.can_moderate = can_moderate
    bot.send_dm_sanction = send_dm_sanction
    bot.log_action = log_action
    bot.usage_embed = usage_embed
    bot.SYSTEM_COLOR = SYSTEM_COLOR
    bot.EMOJI_LUPA = EMOJI_LUPA
    bot.EMOJI_DENEGADO = EMOJI_DENEGADO
    bot.EMOJI_RELOJ = EMOJI_RELOJ
    bot.EMOJI_ARENA = EMOJI_ARENA
    bot.EMOJI_ACEPTAR = EMOJI_ACEPTAR
    bot.EMOJI_AVISO = EMOJI_AVISO
    bot.EMOJI_PLUMA = EMOJI_PLUMA

    # Cargar todos los comandos
    bot_commands.setup(bot)

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
