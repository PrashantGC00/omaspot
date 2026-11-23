"""MPRIS bridge for Soloist: `python3 -m omaspot.mpris` (runs as the soloist-mpris user service).

Soloist has no MPRIS, so the laptop's media keys (Omarchy binds them to its shell's MPRIS media service),
the media OSD and bar widgets couldn't see it. This registers org.mpris.MediaPlayer2.soloist on the session
bus, fills it from Soloist's WebSocket (pushed, no polling) and turns MPRIS calls into `soloist ctl` (~10 ms).
Works whether or not omaspot is open. While Omarchy isn't the active device it shows Soloist's last song as
paused, and Play makes Omarchy the active device again (`ctl activate`) and resumes here.
"""
import asyncio
import re
import shutil
import subprocess
import sys

from dbus_fast import PropertyAccess, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, dbus_property, method, signal

from .soloist import Soloist
from .state import PlayerState, progress_from
from .util import web_link

BUS_NAME = "org.mpris.MediaPlayer2.soloist"
PATH = "/org/mpris/MediaPlayer2"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
LOOP = {"off": "None", "context": "Playlist", "track": "Track"}
REPEAT = {v: k for k, v in LOOP.items()}


def track_path(uri: str | None) -> str:
    return "/org/mpris/MediaPlayer2/track/" + re.sub(r"[^A-Za-z0-9_]", "_", uri) if uri else NO_TRACK


def cover_url(item: dict) -> str:
    imgs = (item.get("album") or {}).get("images") or []
    by = {i.get("size"): i.get("url") for i in imgs}
    return by.get("large") or by.get("default") or next((i.get("url") for i in imgs if i.get("url")), "") or ""


def metadata(st: PlayerState | None) -> dict:
    if not st or not st.item:
        return {"mpris:trackid": Variant("o", NO_TRACK)}
    it = st.item
    md = {
        "mpris:trackid": Variant("o", track_path(st.uri)),
        "mpris:length": Variant("x", int(st.duration) * 1000),
        "xesam:title": Variant("s", it.get("name", "")),
        "xesam:artist": Variant("as", [a.get("name", "") for a in it.get("artists") or [] if a.get("name")]),
        "xesam:album": Variant("s", (it.get("album") or {}).get("name", "")),
    }
    art = cover_url(it)
    if art:
        md["mpris:artUrl"] = Variant("s", art)
    if st.uri:
        md["xesam:url"] = Variant("s", web_link(st.uri))
    return md


class Root(ServiceInterface):
    def __init__(self):
        super().__init__("org.mpris.MediaPlayer2")

    @method()
    def Raise(self):
        exe = shutil.which("omaspot-overlay")
        if exe:
            subprocess.Popen([exe, "show"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    @method()
    def Quit(self):
        pass

    @dbus_property(access=PropertyAccess.READ)
    def CanQuit(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def CanRaise(self) -> "b":
        return bool(shutil.which("omaspot-overlay"))

    @dbus_property(access=PropertyAccess.READ)
    def HasTrackList(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> "s":
        return "Spotify"

    @dbus_property(access=PropertyAccess.READ)
    def DesktopEntry(self) -> "s":
        return "spotify"

    @dbus_property(access=PropertyAccess.READ)
    def SupportedUriSchemes(self) -> "as":
        return ["spotify"]

    @dbus_property(access=PropertyAccess.READ)
    def SupportedMimeTypes(self) -> "as":
        return []


class Player(ServiceInterface):
    def __init__(self, soloist: Soloist):
        super().__init__("org.mpris.MediaPlayer2.Player")
        self.so = soloist
        self.st: PlayerState | None = None
        self._last: dict = {}

    # ---- state ----
    def props(self) -> dict:
        st = self.st
        return {
            "PlaybackStatus": self.PlaybackStatus,
            "LoopStatus": self.LoopStatus,
            "Shuffle": self.Shuffle,
            "Volume": self.Volume,
            "Metadata": metadata(st),
            "CanGoNext": self.CanGoNext,
            "CanGoPrevious": self.CanGoPrevious,
            "CanSeek": self.CanSeek,
        } if st is not None or self._last else {}

    def update(self, st: PlayerState | None, seeked: bool = False):
        self.st = st
        new = self.props()
        changed = {k: v for k, v in new.items() if self._last.get(k) != v
                   and not (k == "Metadata" and _md_eq(self._last.get(k), v))}
        self._last = new
        if changed:
            self.emit_properties_changed(changed)
        if seeked and st:
            self.Seeked(st.position() * 1000)

    def on_event(self, ev: dict):
        kind, st = ev.get("type"), self.st
        if kind == "playback_state":
            self.update(PlayerState.from_soloist(ev, require_active=False), seeked=True)
        elif kind == "ws_closed":
            self.update(None)
        elif kind == "auth_state" and not ev.get("logged_in"):
            self.update(None)
        elif st is None:
            return
        elif kind == "position_sync":
            self.update(st.with_(progress_ms=progress_from(ev.get("position"), st.playing)), seeked=True)
        elif kind == "playback_changed":
            self.update(st.with_(playing=ev.get("status") == "playing"))
        elif kind == "volume_changed" and ev.get("volume") is not None:
            self.update(st.with_(device={**(st.device or {}), "volume_percent": ev["volume"]}))

    async def refresh(self):
        d = await self.so.now()
        # not the active device (another device took over, or Soloist restarted) but it still has a song:
        # show it paused, so the media keys reach Soloist and Play brings playback back here
        self.update(PlayerState.from_soloist(d, require_active=False) if d else None, seeked=True)

    async def ctl(self, *args):
        if not await self.so.ctl(*args) and args[0] in ("play",):
            await self.so._run("activate")            # another device is playing: bring it here, then play
            self.so.active = True
            await self.so.ctl(*args)
        if not self.so.connected:
            await self.refresh()

    # ---- methods ----
    @method()
    async def Next(self):
        await self.ctl("next")

    @method()
    async def Previous(self):
        await self.ctl("prev")

    @method()
    async def Pause(self):
        await self.ctl("pause")

    @method()
    async def Play(self):
        await self.ctl("play")

    @method()
    async def PlayPause(self):
        await self.ctl("pause" if self.st and self.st.playing else "play")

    @method()
    async def Stop(self):
        await self.ctl("pause")

    @method()
    async def Seek(self, offset: "x"):
        if self.st:
            await self.ctl("seek", max(0, self.st.position() + offset // 1000))

    @method()
    async def SetPosition(self, track_id: "o", position: "x"):
        if self.st and track_id == track_path(self.st.uri) and 0 <= position // 1000 <= self.st.duration:
            await self.ctl("seek", position // 1000)

    @method()
    async def OpenUri(self, uri: "s"):
        if uri.startswith("spotify:"):
            await self.ctl("play", uri)

    @signal()
    def Seeked(self, position) -> "x":
        return position

    # ---- properties ----
    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":
        if not self.st:
            return "Stopped"
        return "Playing" if self.st.playing else "Paused"

    @dbus_property()
    def LoopStatus(self) -> "s":
        return LOOP.get(self.st.repeat if self.st else "off", "None")

    @LoopStatus.setter
    def LoopStatus(self, value: "s"):
        asyncio.ensure_future(self.ctl("repeat", REPEAT.get(value, "off")))

    @dbus_property()
    def Shuffle(self) -> "b":
        return bool(self.st and self.st.shuffle)

    @Shuffle.setter
    def Shuffle(self, value: "b"):
        asyncio.ensure_future(self.ctl("shuffle", "on" if value else "off"))

    @dbus_property()
    def Volume(self) -> "d":
        v = self.st.volume if self.st else None
        return (v or 0) / 100

    @Volume.setter
    def Volume(self, value: "d"):
        asyncio.ensure_future(self.ctl("volume", int(round(max(0.0, min(1.0, value)) * 100))))

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":
        return metadata(self.st)

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> "x":
        return self.st.position() * 1000 if self.st else 0

    @dbus_property(access=PropertyAccess.READ)
    def Rate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MinimumRate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MaximumRate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> "b":
        return self.st is not None

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> "b":
        return self.st is not None

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> "b":
        return self.st is not None and self.st.duration > 0

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> "b":
        return True


def _md_eq(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return a.keys() == b.keys() and all(a[k].value == b[k].value for k in a)


async def main():
    so = Soloist()
    player = Player(so)
    bus = await MessageBus().connect()
    bus.export(PATH, Root())
    bus.export(PATH, player)
    await bus.request_name(BUS_NAME)
    await player.refresh()

    async def fallback_poll():          # only while the WebSocket is down (Soloist restarting, old build)
        while True:
            await asyncio.sleep(5)
            if not so.connected:
                await player.refresh()

    asyncio.ensure_future(fallback_poll())
    await asyncio.gather(so.listen(player.on_event), bus.wait_for_disconnect())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
