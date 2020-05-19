"""
The IdleRPG Discord Bot
Copyright (C) 2018-2020 Diniboy and Gelbpunkt
This program is free software: you can redistribute it and/or modify

it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
import asyncio

from base64 import b64encode
from collections import defaultdict
from datetime import timedelta
from json import dumps

import discord
import wavelink

from discord.ext import commands

from classes.converters import IntFromTo
from cogs.help import chunks
from utils.i18n import _, locale_doc
from utils.misc import nice_join


class VoteDidNotPass(commands.CheckFailure):
    pass


class NeedsToBeInVoiceChat(commands.CheckFailure):
    pass


class NeedsToBePlaying(commands.CheckFailure):
    pass


class Artist:
    def __init__(self, raw_data):
        self.url = raw_data.get("external_urls", {}).get("spotify")
        self.id = raw_data.get("id")
        self.name = raw_data.get("name")
        self.uri = raw_data.get("uri")


class Album:
    def __init__(self, raw_data):
        self.artists = [Artist(d) for d in raw_data.get("artists", [])]
        self.url = raw_data.get("external_urls", {}).get("spotify")
        self.id = raw_data.get("id")
        self.images = raw_data.get("images", [])
        self.name = raw_data.get("name")
        self.release_date = raw_data.get("release_date")
        self.total_tracks = raw_data.get("total_tracks", 0)
        self.uri = raw_data.get("uri")


class Track:
    def __init__(self, raw_data, playlist_entry=False):
        self.added_at = raw_data.get("added_at")
        self.is_local = raw_data.get("is_local", False)
        self.primary_color = raw_data.get("primary_color")
        if playlist_entry:
            raw_data = raw_data["track"]
        if (album := raw_data.get("album")) :
            self.album = Album(album)
        self.artists = [Artist(d) for d in raw_data.get("artists", [])]
        self.disc_number = raw_data.get("disc_number", 1)
        self.duration = raw_data.get("duration_ms") or raw_data.get("duration") or 0
        self.episode = raw_data.get("episode")
        self.explicit = raw_data.get("explicit", False)
        self.url = raw_data.get("external_urls", {}).get("spotify")
        self.id = raw_data.get("id")
        self.is_playable = raw_data.get("is_playable", True)
        self.name = raw_data.get("name")
        self.isrc = raw_data.get("external_ids", {}).get("isrc")
        self.popularity = raw_data.get("popularity", 0)
        self.preview_url = raw_data.get("preview_url")
        self.track_number = raw_data.get("track_number", 1)
        self.uri = raw_data.get("uri")
        self.media_data = raw_data["MEDIA_DATA"]


def is_in_vc():
    def predicate(ctx):
        try:
            ctx.voice_channel = ctx.author.voice.channel.id
        except AttributeError:
            raise NeedsToBeInVoiceChat()
        return True

    return commands.check(predicate)


def is_playing():
    def predicate(ctx):
        try:
            ctx.voice_channel = ctx.author.voice.channel.id
        except AttributeError:
            raise NeedsToBeInVoiceChat()
        if not ctx.guild.me.voice:
            raise NeedsToBePlaying()
        return True

    return commands.check(predicate)


def get_player():
    def predicate(ctx):
        ctx.player = ctx.bot.wavelink.get_player(ctx.guild.id)
        return True

    return commands.check(predicate)


def is_not_locked():
    def predicate(ctx):
        return (
            not getattr(ctx.player, "locked", False)
            or getattr(ctx.player, "dj", None) == ctx.author
        )

    return commands.check(predicate)


def is_dj():
    def predicate(ctx):
        return getattr(ctx.player, "dj", None) == ctx.author

    return commands.check(predicate)


def vote(action):
    async def predicate(ctx):
        if ctx.author == ctx.player.dj:
            return True
        if action == "skip":
            text = _(
                "{user} wants to skip a track. React if you agree."
                " **{current}/{total}** voted for it!"
            )
        elif action == "pause_resume":
            text = _(
                "{user} wants to pause/resume the player. React if you agree."
                " **{current}/{total}** voted for it!"
            )
        elif action == "stop":
            text = _(
                "{user} wants to stop playback. React if you agree."
                " **{current}/{total}** voted for it!"
            )
        elif action == "volume":
            text = _(
                "{user} wants to change the volume. React if you agree."
                " **{current}/{total}** voted for it!"
            )
        elif action == "loop":
            text = _(
                "{user} wants to toggle repeating. React if you agree."
                " **{current}/{total}** voted for it!"
            )
        elif action == "equalizer":
            text = _(
                "{user} wants to change the equalizer. React if you agree."
                " **{current}/{total}** voted for it!"
            )
        members = [
            m
            for m in ctx.bot.get_channel(int(ctx.player.channel_id)).members
            if m != ctx.guild.me
        ]
        accepted = {ctx.author}
        needed = int(len(members) / 2) + 1

        msg = await ctx.send(
            text.format(user=ctx.author.mention, current=len(accepted), total=needed)
        )

        def check(r, u):
            return (
                u in members
                and u not in accepted
                and str(r.emoji) == "\U00002705"
                and r.message.id == msg.id
            )

        await msg.add_reaction("\U00002705")

        while len(accepted) < needed:
            try:
                r, u = await ctx.bot.wait_for("reaction_add", check=check, timeout=10)
            except asyncio.TimeoutError:
                raise VoteDidNotPass()
            accepted.add(u)
            await msg.edit(
                content=text.format(
                    user=ctx.author.mention, current=len(accepted), total=needed
                )
            )

        await msg.delete()
        await ctx.send(_("Vote passed!"))
        return True

    return commands.check(predicate)


class FakeTrack(wavelink.Track):
    __slots__ = (
        "id",
        "info",
        "query",
        "title",
        "ytid",
        "length",
        "duration",
        "uri",
        "is_stream",
        "dead",
        "thumb",
        "requester_id",
        "channel_id",
        "track_obj",
    )

    def __init__(self, *args, **kwargs):
        self.requester_id = kwargs.pop("requester_id", None)
        self.channel_id = kwargs.pop("channel_id", None)
        self.id = kwargs.pop("id", None)
        self.track_obj = kwargs.pop("track_obj", None)
        super().__init__(*args, **kwargs)
        self.title = self.track_obj.name
        self.length = self.track_obj.duration


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = defaultdict(lambda: [])  # Redis is not needed because why

        if not hasattr(self.bot, "wavelink"):
            self.bot.wavelink = wavelink.Client(bot=self.bot)

        self.bot.loop.create_task(self.connect())

    async def connect(self):
        node = await self.bot.wavelink.initiate_node(**self.bot.config.lava_creds_new)
        node.set_hook(self.event_hook)
        await asyncio.sleep(5)
        if (
            not self.bot.wavelink.nodes
            or not self.bot.wavelink.nodes["MAIN"].is_available
        ):
            print("FAILED to connect to lavalink backend, unloading music cog...")
            self.bot.unload_extension("cogs.music")

    @is_not_locked()
    @get_player()
    @is_in_vc()
    @commands.command(aliases=["cp"])
    @locale_doc
    async def chooseplay(self, ctx, *, query: str):
        _("""Query for a track and play or add any result to the playlist.""")
        async with self.bot.trusted_session.get(
            f"{self.bot.config.query_endpoint}?limit=5&q={query}"
        ) as r:
            results = await r.json()
        if not results.get("items"):
            return await ctx.send(_("No results..."))
        track_objs = [Track(i) for i in results["items"]]
        if len(track_objs) > 1:
            track_idx = await self.bot.paginator.Choose(
                title=_("Song results"),
                footer=_("Hit a button to play one"),
                return_index=True,
                entries=[
                    f"**{i.name}** by {nice_join([a.name for a in i.artists])} on"
                    f" {i.album.name}"
                    f" ({str(timedelta(milliseconds=i.duration)).split('.')[0]})"
                    for i in track_objs
                ],
            ).paginate(ctx)
            track_obj = track_objs[track_idx]
        else:
            track_obj = track_objs[0]
            if not await ctx.confirm(
                f"The only result was **{track_obj.name}** by"
                f" {nice_join([a.name for a in track_obj.artists])} on"
                f" {track_obj.album.name}. Play it?"
            ):
                return

        msg = await ctx.send(_("Loading track... This might take up to 3 seconds..."))
        b64 = b64encode(dumps(track_obj.media_data).encode()).decode()
        tracks = await self.bot.wavelink.get_tracks(
            f"{self.bot.config.resolve_endpoint}?data={b64}"
        )
        if not tracks:
            return await msg.edit(content=_("No results..."))
        track = tracks[0]
        track = self.update_track(
            track,
            requester_id=ctx.author.id,
            channel_id=ctx.channel.id,
            track_obj=track_obj,
        )

        if not ctx.player.is_connected:
            await ctx.player.connect(ctx.voice_channel)
            # Setup some attributes
            ctx.player.dj = ctx.author
            ctx.player.locked = False
            ctx.player.loop = False
            ctx.player.eq = "Flat"

        await self.add_entry_to_queue(track, ctx.player, msg=msg)

    @is_not_locked()
    @get_player()
    @is_in_vc()
    @commands.command()
    @locale_doc
    async def play(self, ctx, *, query: str):
        _("""Query for a track and play or add the first result to the playlist.""")
        msg = await ctx.send(
            _("Downloading track... This might take up to 3 seconds...")
        )
        async with self.bot.trusted_session.get(
            f"{self.bot.config.query_endpoint}?limit=1&q={query}"
        ) as r:
            results = await r.json()
        try:
            track_obj = Track(results["items"][0])
            b64 = b64encode(dumps(track_obj.media_data).encode()).decode()
            tracks = await self.bot.wavelink.get_tracks(
                f"{self.bot.config.resolve_endpoint}?data={b64}"
            )
            if not tracks:
                return await msg.edit(content=_("No results..."))
            track = tracks[0]
            track = self.update_track(
                track,
                requester_id=ctx.author.id,
                channel_id=ctx.channel.id,
                track_obj=track_obj,
            )
        except (KeyError, IndexError):
            return await msg.edit(content=_("No results..."))

        if not ctx.player.is_connected:
            await ctx.player.connect(ctx.voice_channel)
            # Setup some attributes
            ctx.player.dj = ctx.author
            ctx.player.locked = False
            ctx.player.loop = False
            ctx.player.eq = "Flat"

        await self.add_entry_to_queue(track, ctx.player, msg=msg)

    @is_dj()
    @get_player()
    @is_playing()
    @commands.command(aliases=["unlock"])
    @locale_doc
    async def lock(self, ctx):
        _(
            """Lock/Unlock the player if you are the DJ. Allows noone else to control music."""
        )
        if ctx.player.locked:
            ctx.player.locked = False
        else:
            ctx.player.locked = True
        await ctx.message.add_reaction("✅")

    @vote("loop")
    @is_not_locked()
    @get_player()
    @is_playing()
    @commands.command(aliases=["repeat"])
    @locale_doc
    async def loop(self, ctx):
        _("""Toggle repeat of the current track.""")
        if ctx.player.loop:
            ctx.player.loop = False
        else:
            ctx.player.loop = True
        await ctx.message.add_reaction("✅")

    @vote("skip")
    @is_not_locked()
    @get_player()
    @is_playing()
    @commands.command()
    @locale_doc
    async def skip(self, ctx):
        _("""Skip the currently playing song.""")
        await ctx.player.stop()
        await ctx.message.add_reaction("✅")

    @vote("stop")
    @is_not_locked()
    @get_player()
    @is_playing()
    @commands.command(aliases=["leave"])
    @locale_doc
    async def stop(self, ctx):
        _("""Stops the music and leaves voice chat.""")
        del self.queue[ctx.guild.id]
        await ctx.player.destroy()
        await ctx.message.add_reaction("✅")

    @vote("volume")
    @is_not_locked()
    @get_player()
    @is_playing()
    @commands.command(aliases=["vol"])
    @locale_doc
    async def volume(self, ctx, volume: IntFromTo(0, 100)):
        _("""Changes the playback's volume""")
        if volume > ctx.player.volume:
            vol_warn = await ctx.send(
                _(
                    ":warning:`Playback volume is going to change to {volume} in 5"
                    " seconds. To avoid the sudden earrape, control the volume on"
                    " client side!`"
                ).format(volume=volume)
            )
            await asyncio.sleep(5)
            await ctx.player.set_volume(volume)
            await vol_warn.delete()
        else:
            await ctx.player.set_volume(volume)
        await ctx.send(
            _(":white_check_mark:` Volume successfully changed to {volume}!`").format(
                volume=volume
            ),
            delete_after=5,
        )

    @vote("pause_resume")
    @is_not_locked()
    @get_player()
    @is_playing()
    @commands.command(aliases=["resume"])
    @locale_doc
    async def pause(self, ctx):
        _("""Toggles the music playback's paused state""")
        if not ctx.player.paused:
            await ctx.player.set_pause(True)
            await ctx.send(_(":white_check_mark:`Song paused!`"), delete_after=5)
        else:
            await ctx.player.set_pause(False)
            await ctx.send(_(":white_check_mark:`Song resumed!`"), delete_after=5)

    @vote("equalizer")
    @is_not_locked()
    @get_player()
    @is_playing()
    @commands.command(aliases=["equaliser", "eq"])
    @locale_doc
    async def equalizer(self, ctx, eq: str.upper):
        _("""Sets the equalizer. May be flat, piano, metal or boost.""")
        if eq not in ctx.player.equalizers:
            return await ctx.send(
                _("Not a valid equalizer. May be flat, piano, metal or boost.")
            )
        await ctx.player.set_eq(getattr(wavelink.Equalizer, eq.lower())())
        ctx.player.eq = eq.title()
        await ctx.message.add_reaction("✅")

    @get_player()
    @is_playing()
    @commands.command(aliases=["np"])
    @locale_doc
    async def now_playing(self, ctx):
        _("""Displays some information about the current song.""")
        current_song = self.queue[ctx.guild.id][0]

        if not (ctx.guild and ctx.author.color == discord.Color.default()):
            embed_color = ctx.author.color
        else:
            embed_color = self.bot.config.primary_colour

        playing_embed = discord.Embed(title=_("Now playing..."), colour=embed_color)
        playing_embed.add_field(
            name=_("Title"), value=f"```{current_song.title}```", inline=False
        )
        playing_embed.add_field(
            name=_("Artist"),
            value=nice_join([a.name for a in current_song.track_obj.artists]),
        )
        text = _("Click me!")
        if current_song.uri:
            playing_embed.add_field(
                name=_("Link to the original"),
                value=f"**[{text}]({current_song.track_obj.url})**",
            )
        if current_song.track_obj.album.images:
            best_image = sorted(
                current_song.track_obj.album.images, key=lambda x: -x["width"]
            )[0]
            playing_embed.set_thumbnail(url=best_image["url"])
        playing_embed.add_field(name=_("Volume"), value=f"{ctx.player.volume} %")
        if ctx.player.paused:
            playing_embed.add_field(name=_("Playing status"), value=_("`⏸Paused`"))
        playing_embed.add_field(name=_("DJ"), value=ctx.player.dj.mention)
        playing_embed.add_field(
            name=_("Locked"), value=_("Yes") if ctx.player.locked else _("No")
        )
        playing_embed.add_field(
            name=_("Looping"), value=_("Yes") if ctx.player.loop else _("No")
        )
        playing_embed.add_field(
            name=_("Explicit"),
            value=_("Yes") if current_song.track_obj.explicit else _("No"),
        )
        playing_embed.add_field(name=_("Equalizer"), value=ctx.player.eq)
        button_position = int(100 * (ctx.player.position / current_song.length) / 2.5)
        controller = (
            "```ɴᴏᴡ ᴘʟᴀʏɪɴɢ:"
            f" {current_song.title}\n{(button_position - 1) * '─'}⚪{(40 - button_position) * '─'}\n"
            f" ◄◄⠀{'▐▐' if not ctx.player.paused else '▶'} ⠀►►⠀⠀　　⠀"
            f" {str(timedelta(milliseconds=ctx.player.position)).split('.')[0]} /"
            f" {timedelta(seconds=int(current_song.length / 1000))}```"
        )
        playing_embed.description = controller
        if (
            user := ctx.guild.get_member(current_song.requester_id)
        ) :  # check to avoid errors on guild leave
            playing_embed.set_footer(
                text=_("Song requested by: {user}").format(user=user.display_name),
                icon_url=user.avatar_url_as(format="png", size=64),
            )
        await ctx.send(embed=playing_embed)

    @get_player()
    @is_playing()
    @commands.command(aliases=["q", "que", "cue"])
    @locale_doc
    async def queue(self, ctx):
        _("""Show the next (maximum 5) tracks in the queue.""")
        entries = self.queue[ctx.guild.id][1:6]
        if entries:
            paginator = commands.Paginator()
            for entry in entries:
                paginator.add_line(
                    f"• {entry.title}"
                    f" ({str(timedelta(milliseconds=entry.length)).split('.')[0]}) -"
                    f" {ctx.guild.get_member(entry.requester_id).display_name}"
                )
            queue_length = self.get_queue_length(ctx.guild.id) - 1
            text = _("Upcoming entries")
            await ctx.send(
                embed=discord.Embed(
                    title=f"{text} ({len(entries)}/{queue_length})",
                    description=paginator.pages[0],
                    color=discord.Color.gold(),
                )
            )
        else:
            await ctx.send(_(":warning:`No more entries left.`"))

    @commands.command()
    @locale_doc
    async def lyrics(self, ctx, *, query: str = None):
        _(
            """Retrieves song lyrics. If no song specified, will check the current playing song."""
        )
        await ctx.trigger_typing()
        if query is None and ctx.guild:
            track = self.bot.wavelink.get_player(ctx.guild.id).current
            if not track:
                return await ctx.send(
                    _("I am not playing. Please specify a song to look for.")
                )
            query = f"{track.title} {track.track_obj.artists[0].name}"
        elif query is None and not ctx.guild:
            return await ctx.send(_("Please specify a song."))
        elif len(query) < 3:
            return await ctx.send(_(":x: Look for a longer query!"), delete_after=5)
        async with self.bot.session.get(f"https://lyrics.tsu.sh/v1?q={query}") as r:
            result = await r.json()
        if "error" in result:
            return await ctx.send(_(":warning: No results!"))
        p = commands.Paginator()
        for line in result["content"].split("\n"):
            for i in chunks(line, 1900):
                p.add_line(i)
        await self.bot.paginator.Paginator(
            title=result["song"]["full_title"], entries=p.pages, length=1,
        ).paginate(ctx)

    def update_track(
        self,
        track: wavelink.Track,
        requester_id: int,
        channel_id: int,
        track_obj: Track,
    ):
        return FakeTrack(
            track.id,
            track.info,
            query=track.query,
            requester_id=requester_id,
            channel_id=channel_id,
            track_obj=track_obj,
        )

    async def add_entry_to_queue(
        self, track: FakeTrack, player: wavelink.Player, msg: discord.Message = None
    ):
        if not self.get_queue_length(player.guild_id):
            self.queue[player.guild_id].append(track)
            await self.play_track(track, player, msg=msg)
        else:
            self.queue[player.guild_id].append(track)
            await msg.edit(
                content=_("🎧 Added {title} to the queue...").format(title=track.title,)
            )

    async def play_track(self, track: FakeTrack, player: wavelink.Player, msg=None):
        if msg is None:
            await self.bot.get_channel(track.channel_id).send(
                _("🎧 Playing {title}...").format(title=track.title)
            )
        else:
            await msg.edit(content=_("🎧 Playing {title}...").format(title=track.title))
        await player.play(track)

    def get_queue_length(self, guild_id: int) -> int:
        """Returns the queue's length or False if there is no upcoming songs"""
        return len(self.queue[guild_id])

    async def on_track_end(self, player: wavelink.Player):
        if not player.loop:
            try:
                self.queue[player.guild_id].pop(0)  # remove the previous entry
            except IndexError:
                pass
        if (
            not self.get_queue_length(player.guild_id)
            or len(self.bot.get_channel(int(player.channel_id)).members) == 1
        ):
            # That was the last track
            await player.destroy()
            del self.queue[player.guild_id]
        else:
            await self.play_track(
                self.queue[player.guild_id][0], player,
            )

    async def event_hook(self, event):
        """Handle wavelink events"""
        if isinstance(event, wavelink.TrackEnd):
            await self.on_track_end(event.player)

    async def cleanup(self):
        for player in self.bot.wavelink.players.values():
            await player.destroy()
        await self.bot.wavelink.destroy_node(identifier="MAIN")

    def cog_unload(self):
        self.bot.queue.put_nowait(self.cleanup())


def setup(bot):
    bot.add_cog(Music(bot))
