"""Omaspot's keyboard shortcuts, in one place: the app's bindings, the ? help screen and Preferences >
Keyboard shortcuts all read this list. Users can change any key except esc (it cancels and leaves, everywhere);
their choices live in settings.keys ({binding id: "key[,key]"}) and are applied with Textual's set_keymap.
"""
from textual.binding import Binding

# (id, action, default keys, what it does, help group)
SHORTCUTS = [
    ("play_pause", "toggle", "space", "Play / pause", "Playback"),
    ("next", "next", "n", "Next song", "Playback"),
    ("previous", "prev", "p", "Previous song", "Playback"),
    ("seek_back", "seek(-10)", "comma", "Back 10 s", "Playback"),
    ("seek_forward", "seek(10)", "full_stop", "Forward 10 s", "Playback"),
    ("volume_up", "volume(5)", "plus,equals_sign", "Volume up", "Playback"),
    ("volume_down", "volume(-5)", "minus", "Volume down", "Playback"),
    ("shuffle", "shuffle", "s", "Shuffle", "Playback"),
    ("repeat", "repeat", "r", "Repeat", "Playback"),
    ("search", "focus_search", "slash", "Search", "Library"),
    ("queue_add", "queue_add", "z", "Add song to queue", "Library"),
    ("home", "open('home')", "h", "Home", "Library"),
    ("liked", "open('liked')", "l", "Liked Songs", "Library"),
    ("devices", "open('devices')", "d", "Devices", "Library"),
    ("queue", "toggle_queue", "u", "Queue panel", "Library"),
    ("turntable", "focus_mode", "f", "Turntable", "Window"),
    ("back", "back", "alt+left", "Back", "Window"),
    ("forward", "forward", "alt+right", "Forward", "Window"),
    ("help", "help", "question_mark", "All shortcuts", "Window"),
    ("quit", "quit", "q", "Quit Omaspot", "Window"),
]
DEFAULTS = {sid: keys for sid, _a, keys, _d, _g in SHORTCUTS}
NAMES = {sid: desc for sid, _a, _k, desc, _g in SHORTCUTS}
FIXED = [("esc", "Leave Turntable / hide Omaspot (not changeable: it cancels everywhere)"),
         ("Super+M", "Show / hide Omaspot (Hyprland: ~/.config/hypr/omaspot.lua)")]

# keys the shortcut editor won't take: they're needed to edit (esc cancels, backspace resets) or to type/move
RESERVED = {"escape", "backspace", "enter", "tab", "shift+tab", "up", "down", "left", "right"}

_SYMBOLS = {"space": "space", "comma": ",", "full_stop": ".", "slash": "/", "question_mark": "?", "plus": "+",
            "minus": "-", "equals_sign": "=", "left": "←", "right": "→", "up": "↑", "down": "↓",
            "escape": "esc", "enter": "enter", "backspace": "⌫", "tab": "tab", "underscore": "_",
            "semicolon": ";", "colon": ":", "apostrophe": "'", "quotation_mark": '"', "left_square_bracket": "[",
            "right_square_bracket": "]", "exclamation_mark": "!", "number_sign": "#", "asterisk": "*"}


def bindings() -> list[Binding]:
    """The app's BINDINGS (esc is fixed: it leaves the search box / Turntable, or hides Omaspot)."""
    out = [Binding(keys, action, desc, id=sid) for sid, action, keys, desc, _g in SHORTCUTS]
    out.append(Binding("escape", "escape", "Back / hide", id="escape"))
    return out


def label(key: str) -> str:
    """One key as people write it: "alt+left" → "alt+←", "full_stop" → "."."""
    parts = key.split("+")
    return "+".join(_SYMBOLS.get(p, p) for p in parts)


def labels(keys: str) -> str:
    """"plus,equals_sign" → "+  =" """
    return "  ".join(label(k) for k in keys.split(",") if k)


def effective(overrides: dict) -> dict:
    """{id: keys} with the user's changes applied."""
    return {sid: overrides.get(sid, default) for sid, default in DEFAULTS.items()}


def owner(key: str, overrides: dict, besides: str | None = None) -> str | None:
    """The shortcut id already using `key` (other than `besides`), if any."""
    for sid, keys in effective(overrides).items():
        if sid != besides and key in keys.split(","):
            return sid
    return None
