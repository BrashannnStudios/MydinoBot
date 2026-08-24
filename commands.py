import discord
from discord.ext import commands
from datetime import datetime, timezone
from typing import Optional

def setup(bot):
    # ==================== MODERATION ====================

    @bot.command(name="lock")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def lock(ctx: commands.Context, channel: Optional[discord.TextChannel] = None, time: Optional[str] = None):
        channel = channel or ctx.channel
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Locked by {ctx.author}")

        td = bot.parse_time(time) if time else None
        if td:
            end_ts = (datetime.now(timezone.utc) + td).timestamp()
            await bot.db.locks.update_one(
                {"channel_id": channel.id},
                {"$set": {"channel_id": channel.id, "guild_id": ctx.guild.id, "end_ts": end_ts}},
                upsert=True
            )
            msg = f"{bot.EMOJI_ACEPTAR} {channel.mention} has been locked for **{bot.format_timedelta(td)}**."
        else:
            await bot.db.locks.delete_one({"channel_id": channel.id})
            msg = f"{bot.EMOJI_ACEPTAR} {channel.mention} has been locked."

        await ctx.send(msg)
        await bot.log_action(ctx.guild, f"{bot.EMOJI_AVISO} Channel Locked", {
            "Channel": channel.mention,
            "Moderator": ctx.author.mention,
            "Duration": bot.format_timedelta(td) if td else "Permanent"
        })

    @bot.command(name="unlock")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def unlock(ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        channel = channel or ctx.channel
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Unlocked by {ctx.author}")
        await bot.db.locks.delete_one({"channel_id": channel.id})
        await ctx.send(f"{bot.EMOJI_ACEPTAR} {channel.mention} has been unlocked.")
        await bot.log_action(ctx.guild, f"{bot.EMOJI_ACEPTAR} Channel Unlocked", {
            "Channel": channel.mention,
            "Moderator": ctx.author.mention
        })

    @bot.command(name="ban")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(ctx: commands.Context, target: str = None, *, reason: str = "No reason provided"):
        if target is None:
            return await ctx.send(embed=bot.usage_embed("?ban", "?ban <user> [reason]", "?ban @User Breaking rules"))
        member = await bot.resolve_member(ctx, target)
        if member:
            if not bot.can_moderate(ctx.author, member):
                await ctx.send(f"{bot.EMOJI_DENEGADO} You cannot ban this user (hierarchy).")
                return
            try:
                await bot.send_dm_sanction(
                    member,
                    f"{bot.EMOJI_DENEGADO} You have been banned",
                    f"You have been **banned** from **{ctx.guild.name}**.\n\n**Reason:** {reason}"
                )
                await member.ban(reason=f"{reason} | By {ctx.author}")
                await ctx.send(f"{bot.EMOJI_ACEPTAR} **{member}** has been banned.\nReason: {reason}")
            except discord.Forbidden:
                await ctx.send(f"{bot.EMOJI_DENEGADO} I don't have permission to ban this user.")
                return
        else:
            try:
                user_id = int(target.strip("<@!>"))
                user = await bot.fetch_user(user_id)
                await bot.send_dm_sanction(
                    user,
                    f"{bot.EMOJI_DENEGADO} You have been banned",
                    f"You have been **banned** from **{ctx.guild.name}**.\n\n**Reason:** {reason}"
                )
                await ctx.guild.ban(discord.Object(id=user_id), reason=f"{reason} | By {ctx.author}")
                await ctx.send(f"{bot.EMOJI_ACEPTAR} User `{user_id}` has been banned.\nReason: {reason}")
            except Exception:
                await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
                return

        await bot.log_action(ctx.guild, f"{bot.EMOJI_DENEGADO} User Banned", {
            "User": str(member or target),
            "Moderator": ctx.author.mention,
            "Reason": reason
        })

    @bot.command(name="tempban")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def tempban(ctx: commands.Context, target: str = None, time: str = None, *, reason: str = "No reason provided"):
        if target is None or time is None:
            return await ctx.send(embed=bot.usage_embed("?tempban", "?tempban <user> <time> [reason]", "?tempban @User 7d Toxic behavior"))
        td = bot.parse_time(time)
        if not td:
            await ctx.send(f"{bot.EMOJI_DENEGADO} Invalid time format. Use `30s`, `5m`, `2h`, `1d`, `1w`.")
            return

        member = await bot.resolve_member(ctx, target)
        user_id = None
        display = None

        if member:
            if not bot.can_moderate(ctx.author, member):
                await ctx.send(f"{bot.EMOJI_DENEGADO} You cannot ban this user (hierarchy).")
                return
            user_id = member.id
            display = str(member)
            try:
                await bot.send_dm_sanction(
                    member,
                    f"{bot.EMOJI_ARENA} You have been temporarily banned",
                    f"You have been **temporarily banned** from **{ctx.guild.name}**.\n\n**Duration:** {bot.format_timedelta(td)}\n**Reason:** {reason}"
                )
                await member.ban(reason=f"[TEMP {bot.format_timedelta(td)}] {reason} | By {ctx.author}")
            except discord.Forbidden:
                await ctx.send(f"{bot.EMOJI_DENEGADO} I don't have permission to ban this user.")
                return
        else:
            try:
                user_id = int(target.strip("<@!>"))
                user = await bot.fetch_user(user_id)
                display = str(user)
                await bot.send_dm_sanction(
                    user,
                    f"{bot.EMOJI_ARENA} You have been temporarily banned",
                    f"You have been **temporarily banned** from **{ctx.guild.name}**.\n\n**Duration:** {bot.format_timedelta(td)}\n**Reason:** {reason}"
                )
                await ctx.guild.ban(discord.Object(id=user_id), reason=f"[TEMP {bot.format_timedelta(td)}] {reason} | By {ctx.author}")
            except Exception:
                await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
                return

        end_ts = (datetime.now(timezone.utc) + td).timestamp()
        await bot.db.tempbans.update_one(
            {"guild_id": ctx.guild.id, "user_id": user_id},
            {"$set": {"guild_id": ctx.guild.id, "user_id": user_id, "end_ts": end_ts}},
            upsert=True
        )

        await ctx.send(f"{bot.EMOJI_ARENA} **{display}** has been temporarily banned for **{bot.format_timedelta(td)}**.\nReason: {reason}")
        await bot.log_action(ctx.guild, f"{bot.EMOJI_ARENA} User Temporarily Banned", {
            "User": display,
            "Duration": bot.format_timedelta(td),
            "Moderator": ctx.author.mention,
            "Reason": reason
        })

    @bot.command(name="unban")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(ctx: commands.Context, user_id: str = None):
        if user_id is None:
            return await ctx.send(embed=bot.usage_embed("?unban", "?unban <user-id>", "?unban 123456789012345678"))
        try:
            uid = int(user_id.strip("<@!>"))
            user = await bot.fetch_user(uid)
            await ctx.guild.unban(discord.Object(id=uid), reason=f"Unbanned by {ctx.author}")

            await bot.send_dm_sanction(
                user,
                f"{bot.EMOJI_ACEPTAR} Your ban has been removed",
                f"Your ban has been **removed** from **{ctx.guild.name}**.\nYou can now rejoin the server."
            )

            await bot.db.tempbans.delete_one({"guild_id": ctx.guild.id, "user_id": uid})
            await ctx.send(f"{bot.EMOJI_ACEPTAR} User `{uid}` has been unbanned.")
            await bot.log_action(ctx.guild, f"{bot.EMOJI_ACEPTAR} User Unbanned", {
                "User": f"{user} (`{uid}`)",
                "Moderator": ctx.author.mention
            })
        except Exception as e:
            await ctx.send(f"{bot.EMOJI_DENEGADO} Could not unban: {e}")

    @bot.command(name="kick")
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(ctx: commands.Context, target: str = None, *, reason: str = "No reason provided"):
        if target is None:
            return await ctx.send(embed=bot.usage_embed("?kick", "?kick <user> [reason]", "?kick @User Breaking rules"))
        member = await bot.resolve_member(ctx, target)
        if not member:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
            return
        if not bot.can_moderate(ctx.author, member):
            await ctx.send(f"{bot.EMOJI_DENEGADO} You cannot kick this user (hierarchy).")
            return
        try:
            await bot.send_dm_sanction(
                member,
                f"{bot.EMOJI_AVISO} You have been kicked",
                f"You have been **kicked** from **{ctx.guild.name}**.\n\n**Reason:** {reason}"
            )
            await member.kick(reason=f"{reason} | By {ctx.author}")
            await ctx.send(f"{bot.EMOJI_ACEPTAR} **{member}** has been kicked.\nReason: {reason}")
            await bot.log_action(ctx.guild, f"{bot.EMOJI_AVISO} User Kicked", {
                "User": str(member),
                "Moderator": ctx.author.mention,
                "Reason": reason
            })
        except discord.Forbidden:
            await ctx.send(f"{bot.EMOJI_DENEGADO} I don't have permission to kick this user.")

    @bot.command(name="mute")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def mute(ctx: commands.Context, target: str = None, time: str = None, *, reason: str = "No reason provided"):
        if target is None or time is None:
            return await ctx.send(embed=bot.usage_embed("?mute", "?mute <user> <time> [reason]", "?mute @User 1h Spamming"))
        member = await bot.resolve_member(ctx, target)
        if not member:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
            return
        if not bot.can_moderate(ctx.author, member):
            await ctx.send(f"{bot.EMOJI_DENEGADO} You cannot mute this user (hierarchy).")
            return
        td = bot.parse_time(time)
        if not td:
            await ctx.send(f"{bot.EMOJI_DENEGADO} Invalid time format. Use `30s`, `5m`, `2h`, `1d`.")
            return
        if td.total_seconds() > 2419200:  # 28 days max for timeout
            await ctx.send(f"{bot.EMOJI_DENEGADO} Maximum mute time is 28 days.")
            return
        try:
            await member.timeout(td, reason=f"{reason} | By {ctx.author}")
            await bot.send_dm_sanction(
                member,
                f"{bot.EMOJI_AVISO} You have been muted",
                f"You have been **muted** in **{ctx.guild.name}**.\n\n**Duration:** {bot.format_timedelta(td)}\n**Reason:** {reason}"
            )
            await ctx.send(f"{bot.EMOJI_ACEPTAR} **{member}** has been muted for **{bot.format_timedelta(td)}**.\nReason: {reason}")
            await bot.log_action(ctx.guild, f"{bot.EMOJI_AVISO} User Muted", {
                "User": member.mention,
                "Duration": bot.format_timedelta(td),
                "Moderator": ctx.author.mention,
                "Reason": reason
            })
        except discord.Forbidden:
            await ctx.send(f"{bot.EMOJI_DENEGADO} I don't have permission to mute this user.")

    @bot.command(name="unmute")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(ctx: commands.Context, target: str = None):
        if target is None:
            return await ctx.send(embed=bot.usage_embed("?unmute", "?unmute <user>", "?unmute @User"))
        member = await bot.resolve_member(ctx, target)
        if not member:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
            return
        try:
            await member.timeout(None, reason=f"Unmuted by {ctx.author}")
            await bot.send_dm_sanction(
                member,
                f"{bot.EMOJI_ACEPTAR} You have been unmuted",
                f"Your mute has been **removed** in **{ctx.guild.name}**."
            )
            await ctx.send(f"{bot.EMOJI_ACEPTAR} **{member}** has been unmuted.")
            await bot.log_action(ctx.guild, f"{bot.EMOJI_ACEPTAR} User Unmuted", {
                "User": member.mention,
                "Moderator": ctx.author.mention
            })
        except discord.Forbidden:
            await ctx.send(f"{bot.EMOJI_DENEGADO} I don't have permission to unmute this user.")

    @bot.command(name="timeout")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def timeout(ctx: commands.Context, target: str = None, time: str = None, *, reason: str = "No reason provided"):
        if target is None or time is None:
            return await ctx.send(embed=bot.usage_embed("?timeout", "?timeout <user> <time> [reason]", "?timeout @User 30m Spamming"))
        member = await bot.resolve_member(ctx, target)
        if not member:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
            return
        if not bot.can_moderate(ctx.author, member):
            await ctx.send(f"{bot.EMOJI_DENEGADO} You cannot timeout this user (hierarchy).")
            return
        td = bot.parse_time(time)
        if not td:
            await ctx.send(f"{bot.EMOJI_DENEGADO} Invalid time format. Use `30s`, `5m`, `2h`, `1d`.")
            return
        if td.total_seconds() > 2419200:
            await ctx.send(f"{bot.EMOJI_DENEGADO} Maximum timeout is 28 days.")
            return
        try:
            await member.timeout(td, reason=f"{reason} | By {ctx.author}")
            await bot.send_dm_sanction(
                member,
                f"{bot.EMOJI_AVISO} You have been timed out",
                f"You have been **timed out** in **{ctx.guild.name}**.\n\n**Duration:** {bot.format_timedelta(td)}\n**Reason:** {reason}"
            )
            await ctx.send(f"{bot.EMOJI_ACEPTAR} **{member}** has been timed out for **{bot.format_timedelta(td)}**.\nReason: {reason}")
            await bot.log_action(ctx.guild, f"{bot.EMOJI_AVISO} User Timed Out", {
                "User": member.mention,
                "Duration": bot.format_timedelta(td),
                "Moderator": ctx.author.mention,
                "Reason": reason
            })
        except discord.Forbidden:
            await ctx.send(f"{bot.EMOJI_DENEGADO} I don't have permission to timeout this user.")

    @bot.command(name="warn")
    @commands.has_permissions(moderate_members=True)
    async def warn(ctx: commands.Context, target: str = None, *, reason: str = "No reason provided"):
        if target is None:
            return await ctx.send(embed=bot.usage_embed("?warn", "?warn <user> [reason]", "?warn @User Spamming"))
        member = await bot.resolve_member(ctx, target)
        if not member:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
            return
        if not bot.can_moderate(ctx.author, member):
            await ctx.send(f"{bot.EMOJI_DENEGADO} You cannot warn this user (hierarchy).")
            return

        count = await bot.db.warns.count_documents({"guild_id": ctx.guild.id, "user_id": member.id})
        warn_id = count + 1

        await bot.db.warns.insert_one({
            "guild_id": ctx.guild.id,
            "user_id": member.id,
            "warn_id": warn_id,
            "reason": reason,
            "moderator": str(ctx.author),
            "moderator_id": ctx.author.id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        await bot.send_dm_sanction(
            member,
            f"{bot.EMOJI_AVISO} You have received a warning",
            f"You have received a **warning** in **{ctx.guild.name}**.\n\n**Warn ID:** `{warn_id}`\n**Reason:** {reason}"
        )
        await ctx.send(f"{bot.EMOJI_AVISO} **{member}** has been warned (ID: `{warn_id}`).\nReason: {reason}")
        await bot.log_action(ctx.guild, f"{bot.EMOJI_AVISO} User Warned", {
            "User": member.mention,
            "Warn ID": str(warn_id),
            "Moderator": ctx.author.mention,
            "Reason": reason
        })

    @bot.command(name="warnings")
    @commands.has_permissions(moderate_members=True)
    async def warnings(ctx: commands.Context, target: str = None):
        if target is None:
            return await ctx.send(embed=bot.usage_embed("?warnings", "?warnings <user>", "?warnings @User"))
        member = await bot.resolve_member(ctx, target)
        if not member:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
            return

        cursor = bot.db.warns.find({"guild_id": ctx.guild.id, "user_id": member.id}).sort("warn_id", 1)
        user_warns = await cursor.to_list(length=50)

        if not user_warns:
            await ctx.send(f"{bot.EMOJI_LUPA} **{member}** has no warnings.")
            return

        embed = discord.Embed(title=f"{bot.EMOJI_AVISO} Warnings — {member}", color=bot.SYSTEM_COLOR, timestamp=datetime.now(timezone.utc))
        for w in user_warns:
            embed.add_field(
                name=f"Warn ID: `{w['warn_id']}`",
                value=f"**Reason:** {w['reason']}\n**Moderator:** {w['moderator']}\n**Date:** {w['timestamp'][:19]}",
                inline=False
            )
        embed.set_footer(text="My Dino Park • Moderation")
        await ctx.send(embed=embed)

    @bot.command(name="delwarn")
    @commands.has_permissions(moderate_members=True)
    async def delwarn(ctx: commands.Context, target: str = None, warn_id: int = None):
        if target is None or warn_id is None:
            return await ctx.send(embed=bot.usage_embed("?delwarn", "?delwarn <user> <warn-id>", "?delwarn @User 2"))
        member = await bot.resolve_member(ctx, target)
        if not member:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
            return

        result = await bot.db.warns.delete_one({
            "guild_id": ctx.guild.id,
            "user_id": member.id,
            "warn_id": warn_id
        })

        if result.deleted_count == 0:
            await ctx.send(f"{bot.EMOJI_DENEGADO} Warn ID not found.")
            return

        await bot.send_dm_sanction(
            member,
            f"{bot.EMOJI_ACEPTAR} Warning removed",
            f"Your warning **#{warn_id}** has been **removed** from **{ctx.guild.name}**."
        )
        await ctx.send(f"{bot.EMOJI_ACEPTAR} Warn `{warn_id}` deleted from **{member}**.")
        await bot.log_action(ctx.guild, f"{bot.EMOJI_ACEPTAR} Warning Removed", {
            "User": member.mention,
            "Warn ID": str(warn_id),
            "Moderator": ctx.author.mention
        })

    @bot.command(name="editreason")
    @commands.has_permissions(moderate_members=True)
    async def editreason(ctx: commands.Context, target: str = None, warn_id: int = None, *, new_reason: str = None):
        if target is None or warn_id is None or new_reason is None:
            return await ctx.send(embed=bot.usage_embed("?editreason", "?editreason <user> <warn-id> <new reason>", "?editreason @User 1 New reason here"))
        member = await bot.resolve_member(ctx, target)
        if not member:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
            return

        result = await bot.db.warns.update_one(
            {"guild_id": ctx.guild.id, "user_id": member.id, "warn_id": warn_id},
            {"$set": {"reason": new_reason}}
        )

        if result.modified_count == 0:
            await ctx.send(f"{bot.EMOJI_DENEGADO} Warn ID not found.")
            return

        await ctx.send(f"{bot.EMOJI_ACEPTAR} Reason of warn `{warn_id}` for **{member}** updated.")
        await bot.log_action(ctx.guild, f"{bot.EMOJI_PLUMA} Warn Reason Edited", {
            "User": member.mention,
            "Warn ID": str(warn_id),
            "New Reason": new_reason,
            "Moderator": ctx.author.mention
        })

    @bot.command(name="note")
    @commands.has_permissions(moderate_members=True)
    async def note(ctx: commands.Context, target: str = None, *, note_text: str = None):
        if target is None or note_text is None:
            return await ctx.send(embed=bot.usage_embed("?note", "?note <user> <note>", "?note @User Suspicious behavior"))
        member = await bot.resolve_member(ctx, target)
        if not member:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
            return

        count = await bot.db.notes.count_documents({"guild_id": ctx.guild.id, "user_id": member.id})
        note_id = count + 1

        await bot.db.notes.insert_one({
            "guild_id": ctx.guild.id,
            "user_id": member.id,
            "note_id": note_id,
            "note": note_text,
            "moderator": str(ctx.author),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        await ctx.send(f"{bot.EMOJI_PLUMA} Note `{note_id}` added to **{member}**.")

    @bot.command(name="viewnotes")
    @commands.has_permissions(moderate_members=True)
    async def viewnotes(ctx: commands.Context, target: str = None):
        if target is None:
            return await ctx.send(embed=bot.usage_embed("?viewnotes", "?viewnotes <user>", "?viewnotes @User"))
        member = await bot.resolve_member(ctx, target)
        if not member:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
            return

        cursor = bot.db.notes.find({"guild_id": ctx.guild.id, "user_id": member.id}).sort("note_id", 1)
        user_notes = await cursor.to_list(length=50)

        if not user_notes:
            await ctx.send(f"{bot.EMOJI_LUPA} **{member}** has no notes.")
            return

        embed = discord.Embed(title=f"{bot.EMOJI_PLUMA} Notes — {member}", color=bot.SYSTEM_COLOR, timestamp=datetime.now(timezone.utc))
        for n in user_notes:
            embed.add_field(
                name=f"Note ID: `{n['note_id']}`",
                value=f"{n['note']}\n*By {n['moderator']} — {n['timestamp'][:19]}*",
                inline=False
            )
        embed.set_footer(text="My Dino Park • Staff Notes")
        await ctx.send(embed=embed)

    @bot.command(name="delnote")
    @commands.has_permissions(moderate_members=True)
    async def delnote(ctx: commands.Context, target: str = None, note_id: int = None):
        if target is None or note_id is None:
            return await ctx.send(embed=bot.usage_embed("?delnote", "?delnote <user> <note-id>", "?delnote @User 1"))
        member = await bot.resolve_member(ctx, target)
        if not member:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
            return

        result = await bot.db.notes.delete_one({
            "guild_id": ctx.guild.id,
            "user_id": member.id,
            "note_id": note_id
        })

        if result.deleted_count == 0:
            await ctx.send(f"{bot.EMOJI_DENEGADO} Note ID not found.")
            return

        await ctx.send(f"{bot.EMOJI_ACEPTAR} Note `{note_id}` deleted from **{member}**.")

    # ==================== UTILITY ====================

    @bot.command(name="slowmode")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def slowmode(ctx: commands.Context, channel: Optional[discord.TextChannel] = None, time: Optional[str] = None):
        channel = channel or ctx.channel
        if time is None:
            await channel.edit(slowmode_delay=0)
            await ctx.send(f"{bot.EMOJI_ACEPTAR} Slowmode disabled in {channel.mention}.")
            return

        td = bot.parse_time(time)
        if not td:
            try:
                seconds = int(time)
            except ValueError:
                await ctx.send(f"{bot.EMOJI_DENEGADO} Invalid time. Use `5s`, `10m`, `1h` or seconds.")
                return
        else:
            seconds = int(td.total_seconds())

        if seconds > 21600:
            await ctx.send(f"{bot.EMOJI_DENEGADO} Slowmode cannot be higher than 6 hours.")
            return

        await channel.edit(slowmode_delay=seconds)
        await ctx.send(f"{bot.EMOJI_RELOJ} Slowmode set to **{seconds}s** in {channel.mention}.")

    @bot.command(name="clear")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def clear(ctx: commands.Context, amount: int = None):
        if amount is None:
            return await ctx.send(embed=bot.usage_embed("?clear", "?clear <amount>", "?clear 20"))
        if amount < 1 or amount > 100:
            await ctx.send(f"{bot.EMOJI_DENEGADO} Amount must be between 1 and 100.")
            return
        deleted = await ctx.channel.purge(limit=amount + 1)
        await ctx.send(f"{bot.EMOJI_ACEPTAR} Deleted **{len(deleted)-1}** messages.", delete_after=5)

    @bot.command(name="dm")
    @commands.has_permissions(moderate_members=True)
    async def dm(ctx: commands.Context, target: str = None, *, message: str = None):
        if target is None or message is None:
            return await ctx.send(embed=bot.usage_embed("?dm", "?dm <user> <message>", "?dm @User Please read the rules"))
        member = await bot.resolve_member(ctx, target)
        if not member:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
            return
        try:
            embed = discord.Embed(
                title=f"{bot.EMOJI_PLUMA} Message from Staff Team",
                description=message,
                color=bot.SYSTEM_COLOR,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text="My Dino Park • Staff Team")
            await member.send(embed=embed)
            await ctx.send(f"{bot.EMOJI_ACEPTAR} DM sent to **{member}**.")
        except discord.Forbidden:
            await ctx.send(f"{bot.EMOJI_DENEGADO} I couldn't send the DM (user has DMs closed).")

    @bot.command(name="addrole")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def addrole(ctx: commands.Context, target: str = None, role: str = None):
        if target is None or role is None:
            return await ctx.send(embed=bot.usage_embed("?addrole", "?addrole <user> <role>", "?addrole @User @Member"))
        member = await bot.resolve_member(ctx, target)
        role_obj = await bot.resolve_role(ctx, role)
        if not member or not role_obj:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User or role not found.")
            return
        if role_obj >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            await ctx.send(f"{bot.EMOJI_DENEGADO} You cannot assign a role equal or higher than yours.")
            return
        if role_obj >= ctx.guild.me.top_role:
            await ctx.send(f"{bot.EMOJI_DENEGADO} I cannot assign a role higher than my highest role.")
            return
        await member.add_roles(role_obj, reason=f"Added by {ctx.author}")
        await ctx.send(f"{bot.EMOJI_ACEPTAR} Role {role_obj.mention} added to **{member}**.")

    @bot.command(name="removerole")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def removerole(ctx: commands.Context, target: str = None, role: str = None):
        if target is None or role is None:
            return await ctx.send(embed=bot.usage_embed("?removerole", "?removerole <user> <role>", "?removerole @User @Member"))
        member = await bot.resolve_member(ctx, target)
        role_obj = await bot.resolve_role(ctx, role)
        if not member or not role_obj:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User or role not found.")
            return
        if role_obj >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            await ctx.send(f"{bot.EMOJI_DENEGADO} You cannot remove a role equal or higher than yours.")
            return
        await member.remove_roles(role_obj, reason=f"Removed by {ctx.author}")
        await ctx.send(f"{bot.EMOJI_ACEPTAR} Role {role_obj.mention} removed from **{member}**.")

    @bot.command(name="nick")
    @commands.has_permissions(manage_nicknames=True)
    @commands.bot_has_permissions(manage_nicknames=True)
    async def nick(ctx: commands.Context, target: str = None, *, new_nick: str = None):
        if target is None:
            return await ctx.send(embed=bot.usage_embed("?nick", "?nick <user> [new nick]", "?nick @User CoolName"))
        member = await bot.resolve_member(ctx, target)
        if not member:
            await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
            return
        if not bot.can_moderate(ctx.author, member) and ctx.author.id != ctx.guild.owner_id:
            await ctx.send(f"{bot.EMOJI_DENEGADO} You cannot change this user's nickname (hierarchy).")
            return
        try:
            await member.edit(nick=new_nick)
            if new_nick:
                await ctx.send(f"{bot.EMOJI_ACEPTAR} Nickname of **{member}** changed to `{new_nick}`.")
            else:
                await ctx.send(f"{bot.EMOJI_ACEPTAR} Nickname of **{member}** has been reset.")
        except discord.Forbidden:
            await ctx.send(f"{bot.EMOJI_DENEGADO} I don't have permission to change this nickname.")

    @bot.command(name="userinfo")
    async def userinfo(ctx: commands.Context, target: str = None):
        if target is None:
            member = ctx.author
        else:
            member = await bot.resolve_member(ctx, target)
            if not member:
                await ctx.send(f"{bot.EMOJI_DENEGADO} User not found.")
                return

        roles = [r.mention for r in member.roles if r != ctx.guild.default_role]
        roles_text = ", ".join(roles[:15]) if roles else "None"
        if len(roles) > 15:
            roles_text += f" (+{len(roles)-15} more)"

        embed = discord.Embed(title=f"{bot.EMOJI_LUPA} User Info — {member}", color=bot.SYSTEM_COLOR, timestamp=datetime.now(timezone.utc))
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Nickname", value=member.nick or "None", inline=True)
        embed.add_field(name="Bot", value="Yes" if member.bot else "No", inline=True)
        embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
        embed.add_field(name="Joined Server", value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "Unknown", inline=True)
        embed.add_field(name="Roles", value=roles_text, inline=False)
        embed.set_footer(text=f"Requested by {ctx.author}")
        await ctx.send(embed=embed)

    @bot.command(name="cmds")
    async def cmds(ctx: commands.Context):
        embed = discord.Embed(
            title=f"{bot.EMOJI_LUPA} MydinoBot Commands",
            description="Prefix: `?`  •  Case-insensitive",
            color=bot.SYSTEM_COLOR
        )
        embed.add_field(
            name="Moderation",
            value=(
                "`?lock` `?unlock` `?ban` `?tempban` `?unban`\n"
                "`?kick` `?mute` `?unmute` `?timeout`\n"
                "`?warn` `?warnings` `?delwarn` `?editreason`\n"
                "`?note` `?viewnotes` `?delnote`\n"
                "`?slowmode` `?clear`"
            ),
            inline=True
        )
        embed.add_field(
            name="Utility",
            value=(
                "`?dm` `?addrole` `?removerole`\n"
                "`?nick` `?userinfo` `?cmds`"
            ),
            inline=True
        )
        embed.add_field(
            name="Admin (Slash)",
            value="`/welcome-setup`\n`/bot-setup`",
            inline=True
        )
        embed.add_field(name="Time Format", value="`30s` `5m` `2h` `1d` `1w`", inline=False)
        embed.set_footer(text="My Dino Park")
        await ctx.send(embed=embed)
