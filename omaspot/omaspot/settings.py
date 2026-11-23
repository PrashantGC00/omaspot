"""omaspot's preferences, saved as JSON in ~/.config/omaspot/settings.json (the Preferences page edits them).

Unknown or broken values fall back to the defaults, so a hand-edited file can't stop omaspot from starting.
"""
import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omaspot" / "settings.json"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class Settings:
    # appearance
    album_accent: bool = True       # accent colour from the playing song's cover (else the theme's)
    header_wash: bool = True        # wash of the page cover's colours behind headers and Home's greeting
    opacity: float = 0.82           # window background opacity (kitty), 0.30–1.00
    bg: str = "theme"               # window background: "theme", "album" (follows the song) or "#rrggbb"
    # motion
    focus_pulse: bool = True        # focus mode's album-colour wash beats with the music
    scroll_glide: bool = True       # wheel moves one row per tick (off: Textual's 2)
    # notifications
    notifications: bool = True      # desktop notification when the song changes while omaspot is hidden
    # keyboard shortcuts the user changed: {shortcut id: "key[,key]"} (keys.py has the ids and defaults)
    keys: dict = field(default_factory=dict)

    LIMITS = {"opacity": (0.30, 1.00)}

    def clean(self) -> "Settings":
        """Clamp numbers, check colours, and reset anything of the wrong type to its default."""
        default = Settings()
        for f in fields(self):
            v, d = getattr(self, f.name), getattr(default, f.name)
            if isinstance(d, bool):
                v = v if isinstance(v, bool) else d
            elif isinstance(d, float):
                try:
                    lo, hi = self.LIMITS[f.name]
                    v = round(min(hi, max(lo, float(v))), 2)
                except (TypeError, ValueError):
                    v = d
            elif f.name == "keys":
                from .keys import DEFAULTS
                v = {k: str(x) for k, x in v.items() if k in DEFAULTS and isinstance(x, str) and x.strip()} \
                    if isinstance(v, dict) else {}
            elif f.name == "bg":
                v = v if v in ("theme", "album") or (isinstance(v, str) and HEX.match(v)) else d
                v = v.lower()
            setattr(self, f.name, v)
        return self


def load() -> Settings:
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        return Settings()
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in data.items() if k in known}).clean() if isinstance(data, dict) else Settings()


def save(s: Settings):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(s), indent=2) + "\n")
    tmp.replace(PATH)                   # never a half-written file
