import time

from discord import Embed, Member, VoiceState
from discord.ext import commands
from discord.ext.commands import Context, UserInputError, guild_only

from PyDrocsid.cog import Cog
from PyDrocsid.command import docs, reply
from PyDrocsid.redis import redis
from PyDrocsid.translations import t

from .colors import Colors
from .permissions import SpamDetectionPermission
from .settings import SpamDetectionSettings
from ...contributor import Contributor
from ...pubsub import mute_user, send_alert, send_to_changelog


tg = t.g
t = t.spam_detection


class SpamDetectionCog(Cog, name="Spam Detection"):
    CONTRIBUTORS = [Contributor.ce_phox, Contributor.Defelo, Contributor.NekoFanatic]

    async def on_voice_state_update(self, member: Member, before: VoiceState, after: VoiceState):
        """
        Checks for channel-hopping
        """

        if await SpamDetectionPermission.bypass.check_permissions(member):
            return

        if before.channel == after.channel:
            return

        hops_alert: int = await SpamDetectionSettings.max_hops_alert.get()
        hops_warning: int = await SpamDetectionSettings.max_hops_warning.get()
        hops_mute: int = await SpamDetectionSettings.max_hops_temp_mute.get()
        duration: int = await SpamDetectionSettings.temp_mute_duration.get()
        if hops_alert <= 0 and hops_warning <= 0 and hops_mute <= 0:
            return

        ts = time.time()
        await redis.zremrangebyscore(key := f"channel_hops:user={member.id}", min="-inf", max=ts - 60)
        await redis.zadd(key, {str(ts): ts})
        await redis.expire(key, 60)
        hops: int = await redis.zcount(key, "-inf", "inf")

        if hops >= hops_alert and not await redis.exists(key := f"channel_hops_alert_sent:user={member.id}"):
            await redis.setex(key, 10, 1)
            embed = Embed(
                title=t.channel_hopping, color=Colors.SpamDetection, description=t.hops_in_last_minute(cnt=hops)
            )
            embed.add_field(name=tg.member, value=member.mention)
            embed.add_field(name=t.member_id, value=member.id)
            embed.set_author(name=str(member), icon_url=member.display_avatar.url)
            if after.channel:
                embed.add_field(name=t.current_channel, value=after.channel.name)
            await send_alert(member.guild, embed)

        if hops >= hops_warning and not await redis.exists(key := f"channel_hops_warning_sent:user={member.id}"):
            await redis.setex(key, 10, 1)
            embed = Embed(title=t.channel_hopping_warning_sent, color=Colors.SpamDetection)
            await member.send(embed=embed)

        if (hops >= hops_mute or duration > 0) and not await redis.exists(key := f"channel_hops_mute:user={member.id}"):
            mute_user(member, duration)  # TODO Missing pub-sub channel for that
            await redis.setex(key, 10, 1)

    @commands.group(aliases=["spam", "sd"])
    @SpamDetectionPermission.read.check
    @guild_only()
    @docs(t.commands.spam_detection)
    async def spam_detection(self, ctx: Context):

        if ctx.subcommand_passed is not None:
            if ctx.invoked_subcommand is None:
                raise UserInputError
            return

        embed = Embed(title=t.spam_detection, color=Colors.SpamDetection)

        if (alert := await SpamDetectionSettings.max_hops_alert.get()) <= 0:
            embed.add_field(name=t.channel_hopping_alert, value=tg.disabled, inline=False)
        else:
            embed.add_field(name=t.channel_hopping_alert, value=t.max_x_hops(cnt=alert), inline=False)
        if (dm_warning := await SpamDetectionSettings.max_hops_warning.get()) <= 0:
            embed.add_field(name=t.channel_hopping_warning, value=tg.disabled, inline=False)
        else:
            embed.add_field(name=t.channel_hopping_warning, value=t.max_x_hops(cnt=dm_warning), inline=False)
        if (mute_hops := await SpamDetectionSettings.max_hops_temp_mute.get()) <= 0:
            embed.add_field(name=t.channel_hopping_mute, value=tg.disabled, inline=False)
        else:
            embed.add_field(name=t.channel_hopping_mute, value=t.max_x_hops(cnt=mute_hops), inline=False)
        mute_duration = await SpamDetectionSettings.temp_mute_duration.get()
        embed.add_field(name=t.mute_duration, value=t.seconds_muted(cnt=mute_duration), inline=False)

        await reply(ctx, embed=embed)

    @spam_detection.group(name="spam_detection_settings", aliases=["s", "settings"])
    @SpamDetectionPermission.write.check
    @docs(t.commands.spam_detection_settings)
    async def spam_detection_settings(self, ctx: Context):

        if ctx.subcommand_passed is not None:
            if ctx.invoked_subcommand is None:
                raise UserInputError
            return

    @spam_detection_settings.command(name="alert", aliases=["a"])
    @docs(t.commands.alert)
    async def alert(self, ctx: Context, amount: int):

        await SpamDetectionSettings.max_hops_alert.set(amount if amount > 0 else 0)
        embed = Embed(
            title=t.channel_hopping,
            description=t.hop_amount_set(amount, "alerts") if amount > 0 else t.hop_detection_disabled("alerts"),
            colour=Colors.SpamDetection,
        )
        await reply(ctx, embed=embed)
        await send_to_changelog(
            ctx.guild, t.hop_amount_set(amount, "alerts") if amount > 0 else t.hop_detection_disabled("alerts")
        )

    @spam_detection_settings.command(name="warning", aliases=["warn"])
    @docs(t.commands.warning)
    async def warning(self, ctx: Context, amount: int):

        await SpamDetectionSettings.max_hops_warning.set(amount if amount > 0 else 0)
        embed = Embed(
            title=t.channel_hopping,
            description=t.hop_amount_set(amount, "warnings") if amount > 0 else t.hop_detection_disabled("warnings"),
            colour=Colors.SpamDetection,
        )
        await reply(ctx, embed=embed)
        await send_to_changelog(
            ctx.guild, t.hop_amount_set(amount, "warnings") if amount > 0 else t.hop_detection_disabled("warnings")
        )

    @spam_detection_settings.group(name="temp_mute", aliases=["m"])
    @docs(t.commands.temp_mute)
    async def temp_mute(self, ctx: Context):

        if ctx.subcommand_passed is not None:
            if ctx.invoked_subcommand is None:
                raise UserInputError
            return

    @temp_mute.command(name="temp_mute_hops", aliases=["hops", "h"])
    @docs(t.commands.temp_mute_hops)
    async def temp_mute_hops(self, ctx: Context, amount: int):

        await SpamDetectionSettings.max_hops_temp_mute.set(amount if amount > 0 else 0)
        embed = Embed(
            title=t.channel_hopping,
            description=t.hop_amount_set(amount, "mutes") if amount > 0 else t.hop_detection_disabled("mutes"),
            colour=Colors.SpamDetection,
        )
        await reply(ctx, embed=embed)
        await send_to_changelog(
            ctx.guild, t.hop_amount_set(amount, "mutes") if amount > 0 else t.hop_detection_disabled("mutes")
        )

    @temp_mute.command(name="temp_mute_duration", aliases=["duration", "d"])
    @docs(t.commands.temp_mute_duration)
    async def temp_mute_duration(self, ctx: Context, amount: int):

        await SpamDetectionSettings.temp_mute_duration.set(amount if amount > 0 else 0)
        embed = Embed(
            title=t.channel_hopping,
            description=t.mute_time_set(amount) if amount > 0 else t.hop_detection_disabled("mutes"),
            colour=Colors.SpamDetection,
        )
        await reply(ctx, embed=embed)
        await send_to_changelog(ctx.guild, t.mute_time_set(amount) if amount > 0 else t.hop_detection_disabled("mutes"))
