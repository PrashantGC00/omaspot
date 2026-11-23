"""The Preferences page: switches for omaspot's features, window opacity and background colour.

Each row is focusable: ↑ ↓ move between rows, enter / space toggle, ← → adjust a slider (space doesn't
play/pause here). Mouse works too. Every change applies at once and is saved (settings.py) by the app.
"""
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from . import icons as I
from . import keys as K
from .settings import HEX, Settings
from .theme import C, mix
from .util import bar

# background swatches: (label, value); "album" = a very dark version of the playing song's colour
SWATCHES = [("Theme", "theme"), ("Black", "#000000"), ("Ink", "#0b0d12"), ("Slate", "#16181d"),
            ("Navy", "#0d1424"), ("Plum", "#1a1020"), ("Forest", "#0e1a14"), ("Album", "album"), ("Custom", "custom")]

ROWS = [
    ("APPEARANCE", None),
    ("album_accent", ("toggle", "Album colours", "Take the accent colour from the playing song's cover")),
    ("header_wash", ("toggle", "Header colour wash", "A wash of the cover's colours behind page headers and Home")),
    ("opacity", ("slider", "Window opacity", "How see-through the omaspot window is (Hyprland blurs what's behind)")),
    ("bg", ("swatches", "Background colour", "The window's background; Album follows the playing song")),
    ("MOTION", None),
    ("focus_pulse", ("toggle", "Beat pulse in Turntable", "Turntable's colour wash beats with the bass")),
    ("scroll_glide", ("toggle", "Smooth scrolling", "The wheel moves one row at a time (off: two)")),
    ("NOTIFICATIONS", None),
    ("notifications", ("toggle", "Song notifications", "A desktop notification when the song changes while omaspot is hidden")),
    ("KEYBOARD SHORTCUTS", None),
] + [(sid, ("key", desc, "")) for sid, _a, _default, desc, _g in K.SHORTCUTS] + [
    ("reset_keys", ("action", "Reset all shortcuts", "Put every key back to its default")),
]


def album_bg(palette) -> str:
    """A background from the playing song: its first colour, darkened to sit behind text."""
    r, g, b = palette[0]
    return mix("#%02x%02x%02x" % (r, g, b), "#000000", 0.86)


class PrefRow(Horizontal, can_focus=True):
    BINDINGS = [
        Binding("enter,space", "activate", "Toggle", show=False),
        Binding("left", "adjust(-1)", "Less", show=False),
        Binding("right", "adjust(1)", "More", show=False),
        Binding("up", "move(-1)", "Up", show=False),
        Binding("down", "move(1)", "Down", show=False),
    ]

    def __init__(self, key: str, kind: str, label: str, desc: str):
        super().__init__(classes=f"prow {kind}")
        self.key, self.kind, self.label, self.desc = key, kind, label, desc

    @property
    def s(self) -> Settings:
        return self.app.settings

    def compose(self):
        with Vertical(classes="ptext"):
            yield Static(Text(self.label, style=f"bold {C.hi}"), classes="plabel")
            yield Static(Text(self.desc, style=C.soft), classes="pdesc")
        if self.kind == "slider":
            lo, hi = Settings.LIMITS[self.key]
            yield PrefSlider(self, lo, hi, classes="pslider")
            yield Static("", classes="pvalue")
        elif self.kind == "swatches":
            with Vertical(classes="pctl"):
                yield Swatches(self, classes="pswatch")
                with Horizontal(classes="pcustom"):
                    yield Static(Text("Custom colour", style=C.soft), classes="pclabel")
                    yield Static("", classes="pcprev")
                    yield Input(placeholder="#1a1d2e", classes="phex", max_length=7)
                    yield Static(Text("enter to apply", style=C.soft), classes="pchint")
        elif self.kind == "key":
            yield Static("", classes="pkey")
        else:
            yield Static("", classes="ptoggle")

    capturing = False

    def on_mount(self):
        self.redraw()

    def redraw(self):
        if self.kind == "key":
            self.set_class(self.capturing, "capturing")
            box = self.query_one(".pkey", Static)
            if self.capturing:
                box.update(Text.assemble(("esc cancels · ⌫ default   ", C.soft), ("press a key…", f"bold {C.acc}")))
            else:
                mine = K.effective(self.s.keys)[self.key]
                default = f"   · default {K.labels(K.DEFAULTS[self.key])}" if self.key in self.s.keys else ""
                box.update(Text.assemble((default, C.dim), ("   " if default else "", ""), (K.labels(mine), f"bold {C.acc}")))
            return
        if self.kind == "action":
            self.query_one(".ptoggle", Static).update(Text("↺  Reset", style=f"bold {C.acc}" if self.s.keys else C.dim))
            return
        v = getattr(self.s, self.key)
        if self.kind == "toggle":
            self.query_one(".ptoggle", Static).update(
                Text.assemble((I.TOGGLE_ON + "  On", f"bold {C.acc}")) if v else Text(I.TOGGLE_OFF + "  Off", style=C.dim))
        elif self.kind == "slider":
            sl = self.query_one(PrefSlider)
            sl.set_value(v)
            self.query_one(".pvalue", Static).update(Text(f"{round(v * 100)}%", style=C.soft, justify="right"))
        elif self.kind == "swatches":
            self.query_one(Swatches).refresh_swatches()

    def set(self, value):
        self.app.set_pref(self.key, value)

    def preview(self, value: float):
        """While a slider is dragged: show the value. Opacity changes
        rewrite kitty's config and reload the window, so they apply (and everything saves) on release."""
        if value == getattr(self.s, self.key):
            return
        setattr(self.s, self.key, value)
        self.redraw()

    # ---- input ----
    def action_activate(self):
        if self.kind == "key":
            self.capturing = True                       # the next key press becomes this shortcut
            self.redraw()
            return
        if self.kind == "action":
            if self.s.keys:
                self.app.set_pref("keys", {})
                self.app.notify("Shortcuts back to their defaults", timeout=2)
            return
        if self.kind == "toggle":
            self.set(not getattr(self.s, self.key))
        elif self.kind == "swatches":
            self.query_one(Swatches).step(1)

    def action_adjust(self, d: int):
        if self.kind == "slider":
            lo, hi = Settings.LIMITS[self.key]
            self.set(round(min(hi, max(lo, getattr(self.s, self.key) + d * 0.05)), 2))
        elif self.kind == "swatches":
            self.query_one(Swatches).step(d)

    def action_move(self, d: int):
        """↑ ↓ to the previous / next row. If this row was scrolled out of view with the mouse wheel, start from
        what's on screen (the first visible row going down, the last going up) instead of jumping back to it."""
        rows = list(self.screen.query(PrefRow))
        view = self.parent.parent.region if self.parent and self.parent.parent else self.screen.region
        visible = [r for r in rows if view.contains_region(r.region)]
        if visible and self not in visible:
            target = visible[0] if d > 0 else visible[-1]
        else:
            i = rows.index(self) + d
            target = rows[i] if 0 <= i < len(rows) else None
        if target is not None:
            target.focus(scroll_visible=False)     # Textual's default centres the row: the page jumped
            target.scroll_visible(animate=False)

    def on_click(self, event: events.Click):
        self.focus(scroll_visible=False)     # no jump: the row is already where you clicked
        if self.kind in ("toggle", "key", "action"):
            self.action_activate()

    def _stop_capture(self):
        self.capturing = False
        self.redraw()

    def on_blur(self):
        if self.capturing:
            self._stop_capture()

    def on_key(self, event: events.Key):
        """While a shortcut row is listening, the next key press is the new key (before any app shortcut)."""
        if not self.capturing:
            return
        event.stop()
        event.prevent_default()
        key = event.key
        if key == "escape":
            return self._stop_capture()
        mine = dict(self.s.keys)
        if key == "backspace":
            mine.pop(self.key, None)
            self.app.set_pref("keys", mine)
            return self._stop_capture()
        if key in K.RESERVED:
            self.app.notify(f"{K.label(key)} is used to move around here: pick another key", severity="warning", timeout=3)
            return
        other = K.owner(key, self.s.keys, besides=self.key)
        if other:
            self.app.notify(f"{K.label(key)} is already \"{K.NAMES[other]}\": pick another key, or change that one first",
                            severity="warning", timeout=4)
            return
        mine[self.key] = key
        if mine[self.key] == K.DEFAULTS[self.key]:
            mine.pop(self.key)
        self.app.set_pref("keys", mine)
        self._stop_capture()

    @staticmethod
    def _hex(value: str) -> str | None:
        v = value.strip().lower()
        v = v if v.startswith("#") else "#" + v
        return v if HEX.match(v) else None

    def on_input_changed(self, event: Input.Changed):
        """Live preview of the colour being typed."""
        event.stop()
        v = self._hex(event.value)
        prev = self.query_one(".pcprev", Static)
        prev.update(Text.assemble(("▐", C.soft), ("████", v), ("▌", C.soft)) if v else Text("▐░░░░▌", style=C.dim))

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        v = self._hex(event.value)
        if v:
            self.set(v)
            self.focus(scroll_visible=False)
        else:
            self.app.notify("Type a colour like #1a1d2e (six hex digits)", severity="warning")


class PrefSlider(Static):
    """━━●── slider for a setting between lo and hi; click or ← → to change."""

    def __init__(self, row: PrefRow, lo: float, hi: float, **kw):
        super().__init__(**kw)
        self.row, self.lo, self.hi, self.value = row, lo, hi, lo

    def set_value(self, v: float):
        self.value = v
        self.update(bar((v - self.lo) / (self.hi - self.lo), max(4, self.size.width or 24)))

    def on_resize(self, event: events.Resize):
        self.set_value(self.value)

    # press grabs, moving drags (a live preview), release saves and applies
    def _value_at(self, x: int) -> float:
        frac = max(0.0, min(1.0, (x + 0.5) / max(1, self.size.width)))
        return round(self.lo + frac * (self.hi - self.lo), 2)

    def on_mouse_down(self, event: events.MouseDown):
        if event.button != 1:
            return
        event.stop()
        self.row.focus(scroll_visible=False)
        self.capture_mouse()
        self._dragging = True
        self.row.preview(self._value_at(event.x))

    def on_mouse_move(self, event: events.MouseMove):
        if getattr(self, "_dragging", False):
            event.stop()
            self.row.preview(self._value_at(event.x))

    def on_mouse_up(self, event: events.MouseUp):
        if getattr(self, "_dragging", False):
            event.stop()
            self._dragging = False
            self.release_mouse()
            self.row.set(self._value_at(event.x))


class Swatches(Static):
    """Colour chips, two lines: framed chips (so dark colours stay visible on a dark window) and their names.
    The chosen one has an accent frame and a ✓. "Album" follows the playing song; "Custom" is the colour typed
    in the box below (choosing it puts the cursor there)."""
    CELL = 7                      # ▕████▏ + a space

    def __init__(self, row: PrefRow, **kw):
        super().__init__(**kw)
        self.row = row

    def values(self):
        return [v for _, v in SWATCHES]

    def current(self) -> str:
        bg = self.row.s.bg
        return bg if bg in self.values() else "custom"

    def colour(self, value: str) -> str | None:
        if value == "theme":
            return self.app.theme_bg
        if value == "album":
            return album_bg(self.app.palette)
        if value == "custom":
            bg = self.row.s.bg
            return bg if bg.startswith("#") and bg not in self.values() else None
        return value

    def refresh_swatches(self):
        cur = self.current()
        chips, names = Text(), Text()
        for label, value in SWATCHES:
            chosen = value == cur
            frame = f"bold {C.acc}" if chosen else C.soft
            col = self.colour(value)
            chips.append("▐", style=frame)                  # half-block edges: a frame even dark chips show in
            chips.append("████", style=col) if col else chips.append("░░░░", style=C.dim)
            chips.append("▌ ", style=frame)
            names.append(label[:6].center(6), style=f"bold underline {C.acc}" if chosen else C.soft)
            names.append(" ")
        self.update(Text.assemble(chips, "\n", names))
        hexbox, prev = self.row.query(".phex"), self.row.query(".pcprev")
        if hexbox and cur == "custom" and not hexbox.first().has_focus:
            hexbox.first().value = self.row.s.bg            # show the custom colour in use
        elif prev and hexbox and not hexbox.first().value:
            prev.first().update(Text("▐░░░░▌", style=C.dim))   # nothing typed yet

    def pick(self, value: str):
        if value == "custom":
            box = self.row.query_one(".phex", Input)
            if self._custom_ok():
                self.row.set(self.colour("custom"))
            box.focus()                                      # type a colour, enter applies it
            return
        self.row.set(value)

    def _custom_ok(self) -> bool:
        return self.colour("custom") is not None

    def step(self, d: int):
        vals = self.values()
        cur = self.current()
        i = vals.index(cur) if cur in vals else 0
        self.pick(vals[(i + d) % len(vals)])

    def on_click(self, event: events.Click):
        event.stop()
        self.row.focus(scroll_visible=False)
        i = int(event.x // self.CELL)
        if 0 <= i < len(SWATCHES):
            self.pick(SWATCHES[i][1])


class Preferences(Vertical):
    """The page body: sections of PrefRows."""

    def compose(self):
        title = Static(Text("Preferences", style=f"bold {C.hi}"), classes="ptitle")
        title.big_text = "Preferences"
        yield title
        yield Static(Text("Changes apply right away and are saved.", style=C.soft), classes="psub")
        for key, spec in ROWS:
            if spec is None:
                sec = Static(Text(key, style=f"bold {mix(C.bg, C.acc, 0.65)}"), classes="psection")
                sec.title = key
                yield sec
            else:
                kind, label, desc = spec
                yield PrefRow(key, kind, label, desc)
        fixed = Text()
        for k, what in K.FIXED:
            fixed.append(f"  {k:<10}", style=f"bold {C.soft}")
            fixed.append(what + "\n", style=C.dim)
        yield Static(fixed, classes="pfixed")
        yield Static(Text("Saved to ~/.config/omaspot/settings.json", style=C.dim), classes="pfoot")

    def redraw(self):
        for row in self.query(PrefRow):
            row.redraw()
        for sec in self.query(".psection"):
            sec.update(Text(sec.title, style=f"bold {mix(C.bg, C.acc, 0.65)}"))
