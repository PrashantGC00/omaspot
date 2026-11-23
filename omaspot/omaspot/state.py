"""What's playing, in one shape whether it came from Soloist (local, pushed) or the Web API (other devices)."""
import time
from dataclasses import dataclass, field, replace

from . import config


def track_from_soloist(item: dict) -> dict:
    """A Soloist item (`decorations`…) as a Web API track dict, so tables, menus and covers take either.
    Soloist gives entity URIs, so artist and album ids come along (no search needed to open them)."""
    deco = item.get("decorations") or {}

    def ent(e):
        e = e or {}
        uri = e.get("uri") or ""
        parts = uri.split(":")
        return {"name": ((e.get("decorations") or {}).get("identity") or {}).get("name", ""),
                "uri": uri or None, "id": parts[2] if len(parts) == 3 else None}

    covers = (deco.get("visual_identity") or {}).get("cover") or []
    album = ent((deco.get("parent") or {}).get("entity"))
    album["images"] = [{"url": c.get("url"), "size": c.get("size")} for c in covers]
    return {
        "uri": item.get("uri"),
        "name": (deco.get("identity") or {}).get("name", ""),
        "artists": [ent(c.get("entity")) for c in deco.get("creators") or []],
        "album": album,
        "duration_ms": (deco.get("playback") or {}).get("duration_ms") or 0,
    }


@dataclass
class PlayerState:
    item: dict | None = None          # the track, Web API shape
    playing: bool = False
    progress_ms: int = 0              # position at `at`
    at: float = field(default_factory=time.monotonic)
    shuffle: bool = False
    repeat: str = "off"               # off | context | track
    device: dict | None = None        # {"name", "volume_percent", …}
    source: str = "none"              # soloist | web | none
    context: str | None = None        # uri of the album / playlist / artist it's playing from

    @property
    def uri(self) -> str | None:
        return (self.item or {}).get("uri")

    @property
    def duration(self) -> int:
        return (self.item or {}).get("duration_ms") or 0

    @property
    def album_uri(self) -> str | None:
        return ((self.item or {}).get("album") or {}).get("uri")

    @property
    def volume(self) -> int | None:
        return (self.device or {}).get("volume_percent")

    def position(self, now: float | None = None) -> int:
        pos = self.progress_ms
        if self.playing:
            pos += ((now if now is not None else time.monotonic()) - self.at) * 1000
        dur = self.duration
        return int(min(pos, dur)) if dur else int(pos)

    def with_(self, **changes) -> "PlayerState":
        """A copy with changes; moving the position or play state re-anchors the clock."""
        if "progress_ms" in changes or "playing" in changes:
            changes.setdefault("progress_ms", self.position())
            changes["at"] = time.monotonic()
        return replace(self, **changes)

    @classmethod
    def from_soloist(cls, d: dict, wall_ms: float | None = None, require_active: bool = True) -> "PlayerState | None":
        """`soloist ctl now --json` or a WebSocket `playback_state`; None if Omarchy isn't the active device
        (or, with require_active=False, if Soloist has no song at all: then an inactive one reads as paused)."""
        if not d.get("item") or (require_active and not d.get("is_active")):
            return None
        playing = d.get("status") == "playing" and bool(d.get("is_active"))
        opts = d.get("options") or {}
        return cls(item=track_from_soloist(d["item"]), playing=playing,
                   progress_ms=progress_from(d.get("position"), playing, wall_ms),
                   shuffle=bool(opts.get("shuffle")), repeat=opts.get("repeat", "off"),
                   device={"name": config.DEVICE_NAME, "volume_percent": d.get("volume")}, source="soloist",
                   context=(d.get("context") or {}).get("uri"))

    @classmethod
    def from_web(cls, d: dict | None) -> "PlayerState":
        """GET /me/player (another device is playing)."""
        d = d or {}
        return cls(item=d.get("item"), playing=bool(d.get("is_playing")), progress_ms=d.get("progress_ms") or 0,
                   shuffle=bool(d.get("shuffle_state")), repeat=d.get("repeat_state", "off"),
                   device=d.get("device"), source="web" if d else "none", context=(d.get("context") or {}).get("uri"))


def progress_from(pos: dict | None, playing: bool, wall_ms: float | None = None) -> int:
    """Soloist's {position_ms, timestamp_ms, speed} → the position now."""
    pos = pos or {}
    progress = pos.get("position_ms") or 0
    if playing and pos.get("timestamp_ms"):
        now = wall_ms if wall_ms is not None else time.time() * 1000
        progress += max(0, now - pos["timestamp_ms"]) * (pos.get("speed") or 1)
    return int(progress)
