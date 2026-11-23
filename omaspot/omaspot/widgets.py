"""Small reusable widgets. They report clicks as Textual messages, so they don't need to know who handles them."""

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.geometry import Region
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, ListItem, OptionList, Static
from textual.widgets.option_list import Option

from .theme import C, mix
from .util import artists, bar


class IconButton(Button, can_focus=False):
    pass


class SmoothWheel:
    """Mouse-wheel / touchpad scrolling in one-row steps.

    A terminal can only move content in whole rows. Textual's default jumps 2 rows per wheel event, which reads
    as a stutter (2-line song rows jump ~40 px at once). One row per event halves each jump; a touchpad sends
    events fast enough that the steps blur into motion. Gliding the 2 rows over a few frames (the first
    version) only added lag. Preferences > Smooth scrolling off: Textual's own 2-row jump."""

    STEP = 1

    def _glide(self, event, sign: int) -> bool:
        prefs = getattr(self.app, "settings", None)
        if event.ctrl or event.shift or not self.allow_vertical_scroll or (prefs and not prefs.scroll_glide):
            return False                        # Textual's own scroll handles it
        start = self.scroll_target_y
        target = max(0.0, min(self.max_scroll_y, start + sign * self.STEP))
        if target == start:
            return False                        # at the end: let the parent scroll
        self.scroll_to(y=target, animate=False)
        return True

    # Textual calls the _on_ handler of every class in the MRO, so Widget's instant 2-row jump would run too:
    # prevent_default() stops it when the one-row step took the event; otherwise (at an end) it runs as usual.
    def _on_mouse_scroll_down(self, event) -> None:
        if self._glide(event, 1):
            event.prevent_default()
            event.stop()

    def _on_mouse_scroll_up(self, event) -> None:
        if self._glide(event, -1):
            event.prevent_default()
            event.stop()


class Scroller(SmoothWheel, VerticalScroll):
    """A VerticalScroll with one-row wheel steps (queue list). Scroller(step=2) for pages of big items."""

    def __init__(self, *a, step: int = 1, **kw):
        super().__init__(*a, **kw)
        self.STEP = step


class Slider(Static):
    """A ━━●── bar you can click to set a value between 0 and 1; it fills its width. With scroll_step,
    the mouse wheel moves it too."""

    def __init__(self, cells: int, on_set, scroll_step: float = 0.0, **kw):
        super().__init__(**kw)
        self.cells, self.on_set, self.frac, self.scroll_step = cells, on_set, 0.0, scroll_step
        self._drawn = None

    def set_frac(self, frac: float, force: bool = False):
        self.frac = max(0.0, min(1.0, frac))
        k = (round(self.frac * (self.cells - 1)), self.cells)
        if force or k != self._drawn:          # the 4 Hz tick only redraws when the knob moves a cell
            self._drawn = k
            self.update(bar(self.frac, self.cells))

    def on_resize(self, event: events.Resize):
        if event.size.width and event.size.width != self.cells:
            self.cells = event.size.width
            self.set_frac(self.frac)

    # press grabs the knob, moving drags it, release lets go (a click alone still jumps to that spot)
    def _frac_at(self, x: int) -> float:
        return max(0.0, min(1.0, (x + 0.5) / max(1, self.cells)))

    def on_mouse_down(self, event: events.MouseDown):
        if event.button != 1:
            return
        event.stop()
        self.capture_mouse()
        self._dragging = True
        self.on_set(self._frac_at(event.x))

    def on_mouse_move(self, event: events.MouseMove):
        if getattr(self, "_dragging", False):
            event.stop()
            f = self._frac_at(event.x)
            if abs(f - self.frac) >= 0.5 / max(1, self.cells):     # only when the knob moves a cell
                self.set_frac(f)
                self.on_set(f)

    def on_mouse_up(self, event: events.MouseUp):
        if getattr(self, "_dragging", False):
            event.stop()
            self._dragging = False
            self.release_mouse()

    def on_mouse_scroll_up(self, event):
        if self.scroll_step:
            event.stop()
            self.on_set(min(1.0, self.frac + self.scroll_step))

    def on_mouse_scroll_down(self, event):
        if self.scroll_step:
            event.stop()
            self.on_set(max(0.0, self.frac - self.scroll_step))


class Marquee(Static):
    """One line of text that scrolls when it doesn't fit: holds at the start for a moment, scrolls through,
    loops ("text  ·  text"), holds again. Text that fits stays still. Pauses while Omaspot is hidden.
    style="" leaves the colour to CSS (so :hover still works); on_press makes it clickable."""
    TICK = 0.15                  # seconds per one-cell step
    HOLD = 12                    # ticks to wait at the start of each pass (~1.8 s)
    GAP = "    ·    "

    def __init__(self, text: str = "", style: str = "", on_press=None, **kw):
        super().__init__("", **kw)
        self._text, self._style, self.on_press = text, style, on_press
        self._off, self._hold = 0, self.HOLD

    def set(self, text: str, style: str = ""):
        if (text, style) == (self._text, self._style):
            return
        self._text, self._style = text, style
        self._off, self._hold = 0, self.HOLD
        # full text first, so an auto width can grow (up to max-width) before the scroll window is measured
        self.update(Text(text, style=style, no_wrap=True, overflow="ellipsis"))
        self.call_after_refresh(self._draw)

    def on_mount(self):
        self.set_interval(self.TICK, self._step)
        self._draw()

    def on_resize(self, event):
        self._off, self._hold = 0, self.HOLD
        self._draw()

    def _fits(self) -> bool:
        return cell_len(self._text) <= self.size.width

    def _step(self):
        if not self.size.width or self._fits() or not getattr(self.app, "shown", True):
            return
        if self._hold > 0:
            self._hold -= 1
            return
        self._off = (self._off + 1) % len(self._text + self.GAP)
        if self._off == 0:
            self._hold = self.HOLD
        self._draw()

    def _draw(self):
        w = self.size.width
        if not w or self._fits():
            self.update(Text(self._text, style=self._style, no_wrap=True, overflow="ellipsis"))
            return
        loop = self._text + self.GAP
        self.update(Text((loop + loop)[self._off:self._off + w], style=self._style, no_wrap=True))

    def on_click(self, event: events.Click):
        if self.on_press:
            event.stop()
            self.on_press()


class Link(Static):
    """Clickable text ("Show all", the artist under a title)."""

    def __init__(self, text, on_press, **kw):
        super().__init__(text, **kw)
        self.on_press = on_press

    def on_click(self, event: events.Click):
        event.stop()
        self.on_press()


class Nav(ListItem):
    """A sidebar entry: an icon (or, for playlists, a small cover drawn over it) and a label."""

    def __init__(self, icon: str, label: str, key: tuple, dim: bool = False, thumb: str | None = None, sub: str = ""):
        self.icon, self.text, self.dim, self.thumb_url, self.has_img = icon, label, dim, thumb, False
        self.sub, self.marker = sub, None          # marker: ▁▃▅ frame while this playlist is playing
        super().__init__(Horizontal(Static(self._icon_text(), classes="thumb"), Label(self._label_text()), classes="navrow"),
                         classes="pl" if sub else "")
        self.key = key

    def _icon_text(self) -> Text:
        return Text(self.icon, style=mix(C.bg, C.acc, 0.55) if self.dim else C.acc)

    def _label_text(self) -> Text:
        name = (self.text, f"bold {C.acc}" if self.marker else C.dim if self.dim else C.fg)
        head = Text.assemble((self.marker + " ", f"bold {C.acc}"), name) if self.marker else Text.assemble(name)
        if self.sub:
            head.append("\n")
            head.append(self.sub, style=C.dim)
        head.no_wrap, head.overflow = True, "ellipsis"
        return head

    def set_playing(self, marker: str | None):
        if marker != self.marker:
            self.marker = marker
            labels = self.query(Label)           # empty until mounted; on_mount applies the marker then
            if labels:
                labels.first().update(self._label_text())

    def on_mount(self):
        if self.marker:
            self.query_one(Label).update(self._label_text())

    def recolor(self):
        self.query_one(".thumb", Static).update("" if self.has_img else self._icon_text())
        self.query_one(Label).update(self._label_text())


class QRow(Horizontal):
    """A song in the queue panel: small cover, title and artist. Right-click opens its menu."""

    def __init__(self, tr: dict, now: bool = False):
        super().__init__(classes="qrow")
        self.tr, self.now = tr, now

    def compose(self):
        yield Static("", classes="qart")
        yield Static(Text.assemble((self.tr.get("name", ""), f"bold {C.acc if self.now else C.fg}"), "\n",
                                   (artists(self.tr), C.soft), no_wrap=True, overflow="ellipsis"), classes="qtxt")

    def on_click(self, event: events.Click):
        if event.button == 3:
            event.stop()
            self.app.track_menu(self.tr, event.screen_x, event.screen_y)


class Tile(Vertical):
    """A cover with a title and a subtitle under it. Click opens or plays it; right-click opens its menu."""

    class Clicked(Message):
        def __init__(self, tile: "Tile", event: events.Click):
            super().__init__()
            self.tile, self.event = tile, event

    def __init__(self, item: dict, art_rows: int, icon: str, title: str, sub: str):
        super().__init__(classes="tile ph" if item.get("kind") == "ph" else "tile")
        self.item, self.art_rows = item, art_rows
        self._icon, self._title, self._sub = icon, title, sub

    def compose(self):
        art = Static(self._icon, classes="art")
        art.styles.height = self.art_rows
        yield art
        yield Static(self._title_text(getattr(self, "_marker", None)), classes="tt")
        yield Static(Text(self._sub, no_wrap=True, overflow="ellipsis"), classes="ts")

    def _title_text(self, marker: str | None) -> Text:
        if marker:
            return Text.assemble((marker + " ", f"bold {C.acc}"), (self._title, f"bold {C.acc}"), no_wrap=True, overflow="ellipsis")
        return Text(self._title, no_wrap=True, overflow="ellipsis")

    def set_playing(self, marker: str | None):
        """The ▁▃▅ marker and accent title while this album / playlist / song is playing (None: plain)."""
        if marker != getattr(self, "_marker", None):
            self._marker = marker
            tt = self.query(".tt")               # empty until composed; compose() uses _marker then
            if tt:
                tt.first().update(self._title_text(marker))

    def on_click(self, event: events.Click):
        event.stop()
        self.post_message(self.Clicked(self, event))


class Head(Static):
    """The track table's column header, drawn outside the table so it can carry the rule under it.
    Clicking a column sorts by it."""

    class Sort(Message):
        def __init__(self, name: str):
            super().__init__()
            self.name = name

    def __init__(self, **kw):
        super().__init__("", **kw)
        self.spans: list[tuple[int, int, str]] = []

    def on_click(self, event: events.Click):
        for a, b, name in self.spans:
            if a <= event.x < b:
                self.post_message(self.Sort(name))
                return


class Tracks(SmoothWheel, DataTable):
    """The track table. Double-click (or enter) plays; right-click opens the song's menu; the heart column
    likes; the artist line under a title and the album column open those pages."""

    class Clicked(Message):
        def __init__(self, row: int, col: int, line: int, event: events.Click):
            super().__init__()
            self.row, self.col, self.line, self.event = row, col, line, event

    class Hot(Message):
        """The rows that should show the ▶ button changed: the hovered row (-1: none) or focus moved."""
        def __init__(self, hover: int):
            super().__init__()
            self.hover = hover

    def watch_hover_coordinate(self, old, value) -> None:
        super().watch_hover_coordinate(old, value)
        if old.row != value.row:
            self.post_message(self.Hot(value.row if self._show_hover_cursor else -1))

    def _on_leave(self, _event) -> None:          # DataTable's own _on_leave also runs (Textual walks the MRO)
        self.post_message(self.Hot(-1))

    def on_focus(self) -> None:
        self.post_message(self.Hot(self.hover_row if self._show_hover_cursor else -1))

    def on_blur(self) -> None:
        self.post_message(self.Hot(self.hover_row if self._show_hover_cursor else -1))

    def _line_in_row(self, event: events.Click) -> int:
        try:
            y = event.y + int(self.scroll_y)
            return self._y_offsets[y][1]
        except (IndexError, AttributeError):
            return 0

    async def _on_click(self, event: events.Click) -> None:
        event.prevent_default()        # DataTable's own handler would play the row on a single click
        meta = event.style.meta
        if "row" not in meta or "column" not in meta or meta.get("out_of_bounds", False):
            return
        row, col = meta["row"], meta["column"]
        if row < 0:
            return
        event.stop()
        self.cursor_coordinate = Coordinate(row, col)
        self.post_message(self.Clicked(row, col, self._line_in_row(event), event))


class Menu(ModalScreen):
    """Right-click menu at the mouse. items: [(label, callback)]; a callback of None draws a divider."""
    BINDINGS = [("escape", "dismiss", "Close")]
    DEFAULT_CSS = """
    Menu { background: $vbg 0%; }
    #menu { pointer: pointer; width: 32; height: auto; max-height: 20; background: $vpanel; border: round $vdim; padding: 0;
            scrollbar-size-vertical: 0; }
    #menu > .option-list--option { padding: 0 1; color: $vfg; }
    #menu > .option-list--option-highlighted { background: $vaccbg; color: $vhi; text-style: none; }
    #menu:focus > .option-list--option-highlighted { background: $vaccbg; color: $vhi; text-style: none; }
    #menu > .option-list--option-disabled { color: $vdim; }
    """
    WIDTH = 32

    def __init__(self, items, x: int, y: int):
        super().__init__()
        self.items, self.x, self.y = items, x, y

    @classmethod
    def region_for(cls, items, x: int, y: int, size) -> Region:
        h = len(items) + 2
        x = max(0, min(x, size.width - cls.WIDTH))
        y = max(0, min(y, size.height - h))
        return Region(x, y, cls.WIDTH, h)

    def compose(self):
        yield OptionList(*[Option(Text(label), disabled=cb is None) for label, cb in self.items], id="menu")

    def on_mount(self):
        r = self.region_for(self.items, self.x, self.y, self.app.size)
        m = self.query_one("#menu", OptionList)
        m.styles.offset = (r.x, r.y)
        m.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        self.dismiss(self.items[event.option_index][1])

    def on_click(self, event: events.Click):
        if event.widget is self:
            self.dismiss(None)


class Help(ModalScreen):
    """? shows every shortcut; esc, ? or q closes it."""
    BINDINGS = [("escape,question_mark,q", "dismiss", "Close")]
    DEFAULT_CSS = """
    Help { align: center middle; background: $vbg 55%; }
    #helpbox { width: 62; height: auto; padding: 1 3; background: $vpanel; border: round $vdim; }
    """

    def compose(self) -> ComposeResult:
        txt = Text()
        from . import keys
        mine = keys.effective(getattr(getattr(self.app, "settings", None), "keys", {}) or {})
        txt.append("Shortcuts", style=f"bold {C.hi}")
        txt.append("   (change them in Preferences)\n", style=C.dim)
        rows: dict[str, list] = {}
        for sid, _action, _default, desc, group in keys.SHORTCUTS:
            rows.setdefault(group, []).append((keys.labels(mine[sid]), desc))
        rows["Library"] += [("enter / ▶", "play song, open album or playlist"), ("double-click", "play song"),
                            ("right-click", "song / album menu")]
        rows["Window"] += [(k, d.split(" (")[0]) for k, d in keys.FIXED]
        for section, items in rows.items():
            txt.append(f"\n{section.upper()}\n", style=f"bold {C.dim}")
            for k, what in items:
                txt.append(f"  {k:<14}", style=f"bold {C.acc}")
                txt.append(f"{what}\n", style=C.fg)
        txt.append("\nesc to close", style=C.dim)
        yield Static(txt, id="helpbox")

    def on_click(self):
        self.dismiss()
