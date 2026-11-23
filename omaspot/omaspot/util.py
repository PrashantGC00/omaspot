"""Small formatting helpers shared by the UI."""
from datetime import datetime, timezone

from rich.text import Text

from .theme import C



def fmt(ms) -> str:
    s = int((ms or 0) // 1000)
    return f"{s // 60}:{s % 60:02d}"


def hours(ms) -> str:
    m = int((ms or 0) // 60000)
    return f"{m // 60} hr {m % 60} min" if m >= 60 else f"{m} min"


def ago(iso: str) -> str:
    """Spotify-style 'Date added': '3 days ago' for the last month, '2 Nov 2026' before that."""
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    days = (datetime.now(timezone.utc) - d).days
    if days < 1:
        return "today"
    if days < 7:
        return f"{days} day{'s' * (days > 1)} ago"
    if days < 30:
        return f"{days // 7} week{'s' * (days >= 14)} ago"
    return f"{d.day} {d.strftime('%b %Y')}"


def pick_image(images, target: int = 300) -> str | None:
    """The image closest to target px from a Web API images list."""
    imgs = [i for i in (images or []) if i and i.get("url")]
    if not imgs:
        return None
    return min(imgs, key=lambda i: abs((i.get("width") or 300) - target))["url"]


def artists(t) -> str:
    return ", ".join(a.get("name", "") for a in (t or {}).get("artists", []) if a)


def bar(frac: float, cells: int) -> Text:
    """━━━━●──── slider. JetBrains Mono has no ▰▱, and fallback glyphs overflow their cells."""
    k = max(0, min(cells - 1, round(frac * (cells - 1))))
    return Text.assemble(("━" * k, C.acc), ("●", f"bold {C.hi}"), ("─" * (cells - 1 - k), C.dim))


def web_link(uri: str) -> str:
    parts = (uri or "").split(":")
    return f"https://open.spotify.com/{parts[1]}/{parts[2]}" if len(parts) == 3 else uri


def uri_id(uri: str | None) -> str | None:
    """spotify:artist:ID → ID"""
    parts = (uri or "").split(":")
    return parts[2] if len(parts) == 3 else None
