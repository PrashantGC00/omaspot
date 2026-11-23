"""omaspot's colours, from the current Omarchy theme (~/.local/state/omarchy/current/theme/colors.toml).

The theme-set hook (hooks/omaspot-theme) sends SIGUSR1 after a theme change and omaspot calls load() again.
Falls back to the Solitude palette omaspot was designed with.
"""
import colorsys
import tomllib
from pathlib import Path

COLORS = Path.home() / ".local/state/omarchy/current/theme/colors.toml"

SOLITUDE = {
    "bg": "#101315", "panel": "#0c0e10", "sel": "#343d41", "fg": "#cacccc", "dim": "#4b4e55",
    "soft": "#a5aeb4", "accent": "#798186", "hi": "#d9dbdc", "border": "#a8adb0", "theme_accent": "#c98b4b",
}


def rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def hexc(c) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in c)


def mix(a: str, b: str, t: float) -> str:
    ca, cb = rgb(a), rgb(b)
    return hexc(tuple(x + (y - x) * t for x, y in zip(ca, cb)))


def luminance(h: str) -> float:
    r, g, b = (v / 255 for v in rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def load() -> dict:
    try:
        c = tomllib.loads(COLORS.read_text())
    except (OSError, ValueError):
        return dict(SOLITUDE)
    bg = c.get("background", SOLITUDE["bg"])
    fg = c.get("foreground", SOLITUDE["fg"])
    return {
        "bg": bg,
        "panel": c.get("dark_background", mix(bg, "#000000", 0.25)),
        "sel": c.get("selection", mix(bg, fg, 0.18)),
        "fg": fg,
        "dim": c.get("dark_foreground", mix(fg, bg, 0.6)),
        "soft": c.get("light_foreground", mix(fg, bg, 0.2)),
        "accent": c.get("light_foreground", mix(fg, bg, 0.35)),
        "hi": mix(c.get("bright_foreground", fg), "#ffffff", 0.25),
        "border": c.get("light_foreground", fg),
        "theme_accent": c.get("accent", SOLITUDE["theme_accent"]),
    }


def accent_from_palette(palette, fallback: str) -> str:
    """The album colour that reads best as an accent: the most saturated of the 4, lifted until it's
    bright enough to sit on a dark background."""
    best, score = None, -1.0
    for c in palette or []:
        h, l, s = colorsys.rgb_to_hls(*(v / 255 for v in c))
        sc = s * (0.4 + min(l, 1 - l))
        if sc > score:
            best, score = c, sc
    if best is None:
        return fallback
    h, l, s = colorsys.rgb_to_hls(*(v / 255 for v in best))
    l = max(l, 0.58)
    s = min(1.0, max(s, 0.35)) if s > 0.08 else s
    return hexc(tuple(v * 255 for v in colorsys.hls_to_rgb(h, l, s)))


def on_accent(accent: str, dark: str, light: str) -> str:
    """Text colour for a button filled with the accent."""
    return dark if luminance(accent) > 0.45 else light


class Colors:
    """The live palette: the Omarchy theme's colours plus `acc`, the accent taken from the playing album.

    Everything reads it at render time (C.acc, C.bg…) instead of copying values, so a theme reload or a new
    album accent only has to change this object and redraw."""

    ALBUM = ["#c98b4b", "#7a4e2d", "#2f5d62", "#d9c7a3"]      # player-bar swatches before a cover loads

    def __init__(self):
        self.acc = ""
        self.bg_override = None     # Preferences > Background: "#rrggbb" replaces the theme's window colours
        self.apply(load())

    def apply(self, pal: dict):
        for k, v in pal.items():
            setattr(self, k, v)
        if self.bg_override:
            self.bg = self.bg_override
            self.panel = mix(self.bg_override, "#000000", 0.25)
            self.sel = mix(self.bg_override, self.fg, 0.18)
        self.acc = self.acc or self.theme_accent

    def css(self) -> dict:
        """Textual CSS variables ($vbg, $vacc…)."""
        return dict(vbg=self.bg, vpanel=self.panel, vsel=self.sel, vfg=self.fg, vdim=self.dim, vsoft=self.soft,
                    vaccent=self.accent, vhi=self.hi, vborder=self.border, vacc=self.acc,
                    vaccfg=on_accent(self.acc, self.bg, self.hi), vaccbg=mix(self.bg, self.acc, 0.28),
                    vaccdim=mix(self.bg, self.acc, 0.65), vhover=mix(self.bg, self.sel, 0.6))


C = Colors()
