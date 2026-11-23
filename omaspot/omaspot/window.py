"""The overlay window's background colour and opacity (kitty), from Preferences.

kitty makes the window see-through with two kinds of lines in ~/.config/omaspot/kitty-overlay.conf:
`background_opacity` (kitty's own margin) and `transparent_background_colors` (omaspot's own cell colours:
window, panels, selection, each with an opacity; they must match the colours omaspot paints exactly).
This rewrites those lines and `background`, then asks the overlay kitty to re-read its config (SIGUSR1).
Only the overlay window is signalled (OMASPOT_WINDOW=1 + KITTY_PID), never your normal kitty.
"""
import os
import re
import signal
from pathlib import Path

CONF = Path.home() / ".config/omaspot/kitty-overlay.conf"


def lines_for(bg: str, panel: str, sel: str, opacity: float) -> dict:
    o = max(0.30, min(1.0, opacity))
    a = lambda extra: f"{min(1.0, o + extra):.2f}"
    return {
        "background": f"background {bg}",
        "background_opacity": f"background_opacity {o:.2f}",
        "transparent_background_colors": f"transparent_background_colors {bg}@{a(0)} {panel}@{a(0.06)} {sel}@{a(0.10)}",
    }


def rewrite(text: str, lines: dict) -> str:
    for key, line in lines.items():
        pat = re.compile(rf"^{key}\s.*$", re.M)
        text = pat.sub(line, text, count=1) if pat.search(text) else text.rstrip("\n") + "\n" + line + "\n"
    return text


def apply(bg: str, panel: str, sel: str, opacity: float) -> bool:
    """Write the overlay's colours and opacity; reload its kitty if anything changed. False if no config."""
    try:
        old = CONF.read_text()
    except OSError:
        return False
    new = rewrite(old, lines_for(bg, panel, sel, opacity))
    if new != old:
        tmp = CONF.with_suffix(".tmp")
        tmp.write_text(new)
        tmp.replace(CONF)
        pid = os.environ.get("KITTY_PID")
        if os.environ.get("OMASPOT_WINDOW") == "1" and pid and pid.isdigit():
            try:
                os.kill(int(pid), signal.SIGUSR1)       # kitty re-reads its config
            except OSError:
                pass
    return True
