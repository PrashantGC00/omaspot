"""The parts of omaspot's window: the player controls (shared by the player bar and focus mode), the player bar,
focus mode, the queue panel and the sidebar. Each one draws itself from a PlayerState in show()."""
import asyncio

from rich.text import Text
from textual import work
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, ListItem, ListView, Label, Static

from . import config, kittygfx
from . import icons as I
from .covers import load_thumb, to_hex
from .focus import FocusCanvas
from .items import playlist_key
from .state import PlayerState
from .theme import C, mix
from .util import artists, fmt, pick_image
from .widgets import IconButton, Marquee, Nav, QRow, Scroller, Slider, SmoothWheel

NAV_INDEX = {"home": 0, "search": 1, "query": 1, "liked": 2, "queue": 3, "focus": 4, "devices": 5, "prefs": 6}


class Transport(Vertical):
    """Shuffle at the left edge, previous / play-pause / next packed in the middle, repeat at the right edge,
    and the seek bar under them."""

    ACTIONS = {"shuf": "shuffle", "prev": "prev", "play": "toggle", "next": "next", "rep": "repeat"}

    def compose(self):
        with Horizontal(classes="btns"):
            yield IconButton(I.SHUFFLE, classes="shuf")
            yield Static("", classes="gap")
            yield IconButton(I.PREV, classes="prev")
            yield IconButton(I.PLAY, classes="play")
            yield IconButton(I.NEXT, classes="next")
            yield Static("", classes="gap")
            yield IconButton(I.REPEAT, classes="rep")
        with Horizontal(classes="seekrow"):
            yield Static("0:00", classes="pos")
            yield Slider(44, lambda f: self.app.seek_frac(f), classes="seek")
            yield Static("0:00", classes="len")

    def on_mount(self):
        q = self.query_one
        self._play, self._shuf, self._rep = q(".play", IconButton), q(".shuf", IconButton), q(".rep", IconButton)
        self._pos, self._len, self._seek = q(".pos", Static), q(".len", Static), q(".seek", Slider)
        self._times = None

    def on_resize(self, event):
        """Shuffle and repeat sit 15 cells in from the edges when there's room; less in a narrow window, so the
        five buttons always fit (they need ~23 cells)."""
        pad = max(1, min(15, (event.size.width - 30) // 2))
        self.query_one(".btns").styles.padding = (0, pad)

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        for cls, action in self.ACTIONS.items():
            if event.button.has_class(cls):
                getattr(self.app, f"action_{action}")()

    def show(self, st: PlayerState):
        self.set_class(not st.item, "idle")              # greyed out until a song is loaded
        self._play.label = I.PAUSE if st.playing else I.PLAY
        self._shuf.set_class(st.shuffle, "on")
        self._rep.set_class(st.repeat != "off", "on")

    def tick(self, pos: int, dur: int, force: bool = False):
        times = (pos // 1000, dur // 1000)
        if force or times != self._times:        # text only changes once a second
            self._times = times
            self._pos.update(fmt(pos))
            self._len.update(fmt(dur))
        self._seek.set_frac(pos / dur if dur else 0, force)


class VolumeRow(Horizontal):
    def compose(self):
        yield Static(I.VOL, classes="volic")
        yield Slider(10, lambda f: self.app.volume_frac(f), 0.05, classes="vol")

    def show(self, pct: int, force: bool = False):
        self.query_one(".volic", Static).update(I.VOL_OFF if pct == 0 else I.VOL_LOW if pct < 50 else I.VOL)
        self.query_one(".vol", Slider).set_frac(pct / 100, force)


def _swatches(cols) -> Text:
    return Text.assemble(("██", cols[0]), ("██", cols[1]), "\n", ("██", cols[2]), ("██", cols[3]))


class PlayerBar(Horizontal):
    """Along the bottom: mini cover, song and artist, the controls, queue toggle, device and volume."""

    def compose(self):
        with Horizontal(id="npbox"):
            yield Static("", id="mini")          # empty until a song is playing (no placeholder cover)
            with Vertical(id="npcol"):
                with Horizontal(id="nprow"):
                    yield Marquee(id="np")
                    yield IconButton(I.FOCUS, id="b-focus")
                yield Marquee(on_press=lambda: self.app.goto_np_artist(), id="npartist")
        with Vertical(id="ctr"):
            yield Transport()
        with Vertical(id="rt"):
            with Horizontal(id="rtrow"):
                yield IconButton(I.QUEUE, id="b-queue")
                yield Static("", id="dev")
            yield VolumeRow()

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        if event.button.id == "b-focus":
            self.app.action_focus_mode()
        elif event.button.id == "b-queue":
            self.app.action_toggle_queue()

    def show(self, st: PlayerState):
        item = st.item
        np = self.query_one("#np", Marquee)
        np.set(item.get("name", ""), f"bold {C.hi}") if item else np.set("Nothing playing", C.dim)
        self.query_one("#npartist", Marquee).set(artists(item) if item else "")      # long ones scroll
        self.query_one(Transport).show(st)
        if st.device:
            name = st.device.get("name", "")
            here = name == config.DEVICE_NAME
            self.query_one("#dev", Static).update(Text.assemble(
                (I.DEVICE + " ", C.acc), ("" if here else "playing on ", C.soft), (name, C.soft if here else "#e0af68")))

    def device_offline(self):
        self.query_one("#dev", Static).update(Text(f"{I.DEVICE} {config.DEVICE_NAME} offline", style="#de6145"))

    def clear_mini(self):
        """Nothing playing: no cover at all."""
        mini = self.query_one("#mini", Static)
        mini.update("")
        self.app.pics.set("mini", mini, None)
        self.app.pics.request()

    def set_mini(self, img=None, palette=None):
        """The song's cover (kitty) or its 4 colours as swatches (other terminals)."""
        mini = self.query_one("#mini", Static)
        if img is not None:
            mini.update("")
            self.app.pics.set("mini", mini, img)
            self.app.pics.request()
        elif palette:
            mini.update(_swatches([to_hex(x) for x in palette]))


class FocusView(Vertical):
    """Full-window focus mode: the canvas (visualizer, cover, record) with the controls under it."""

    def compose(self):
        yield FocusCanvas(id="canvas")
        with Horizontal(id="fctl"):
            yield Static("", id="f-np")
            with Vertical(id="fctr"):
                yield Transport()
            with Vertical(id="frt"):
                yield VolumeRow()

    def show(self, st: PlayerState):
        item = st.item
        album = ((item or {}).get("album") or {}).get("name", "")
        sub = " · ".join(x for x in (artists(item), album) if x) if item else ""
        canvas = self.query_one(FocusCanvas)
        canvas.playing = st.playing
        canvas.set_info(item.get("name", "") if item else "", sub)
        if canvas.gfx:          # kitty: the big title is part of the scene
            self.query_one("#f-np", Static).update("")
        else:
            self.query_one("#f-np", Static).update(
                Text.assemble(("NOW PLAYING\n", f"bold {C.acc}"), (item.get("name", ""), f"bold {C.hi}"), "\n",
                              (sub, C.soft)) if item else Text("Nothing playing", style=C.dim))
        self.query_one(Transport).show(st)


class QueuePanel(Vertical):
    """Now playing and next up, on the right (u). Rebuilt when the song or the queue changes."""

    def compose(self):
        yield Static("Queue", id="qtitle")
        yield Static("NOW PLAYING", classes="qlabel")
        yield Vertical(id="qnow")
        yield Static("NEXT UP", classes="qlabel")
        yield Scroller(id="qlist")

    @work(exclusive=True, group="queue")
    async def load(self):
        app = self.app
        try:
            nxt = await app.sp.queue()
        except Exception as e:
            app.fail(e)
            return
        cur = app.player.item
        app.pics.drop("q:")
        now, lst = self.query_one("#qnow"), self.query_one("#qlist", VerticalScroll)
        await now.remove_children()
        await lst.remove_children()
        rows = []
        if cur:
            rows.append(QRow(cur, now=True))
            await now.mount(rows[0])
        else:
            await now.mount(Static("Nothing playing", id="qempty"))
        nrows = [QRow(t) for t in nxt]
        if nrows:
            await lst.mount_all(nrows)
        else:
            await lst.mount(Static("Nothing queued. Right-click a song and choose Add to queue.", id="qempty"))
        rows += nrows
        if not kittygfx.supported():
            return
        sem = asyncio.Semaphore(6)

        async def thumb(i, row):
            url = _thumb_url(row.tr)
            if not url:
                return
            async with sem:
                try:
                    img = await load_thumb(url)
                except Exception:
                    return
            if row.is_attached:
                app.pics.set(f"q:{i}", row.query_one(".qart"), img)
                app.pics.request()

        await asyncio.gather(*(thumb(i, r) for i, r in enumerate(rows)))


def _thumb_url(tr: dict) -> str | None:
    imgs = (tr.get("album") or {}).get("images")
    small = next((i.get("url") for i in imgs or [] if i.get("size") == "small"), None)    # Soloist shape
    return small or pick_image(imgs, 64)


def playlist_sub(p: dict) -> str:
    """The sidebar's second line: "26 songs" (every playlist there is the user's, so not the owner)."""
    n = (p.get("tracks") or p.get("items") or {}).get("total")
    return f"{n} song{'s' if n != 1 else ''}" if isinstance(n, int) else "Playlist"


class Sidebar(SmoothWheel, ListView):
    """Home, Search, Liked Songs, Queue, Focus, Devices, then the user's playlists with small covers."""

    def __init__(self, **kw):
        super().__init__(
            Nav(I.HOME, "Home", ("home",)),
            Nav(I.SEARCH, "Search", ("search",)),
            Nav(I.HEART, "Liked Songs", ("liked",)),
            Nav(I.QUEUE, "Queue", ("queue",)),
            Nav(I.FOCUS, "Turntable", ("focus",)),
            Nav(I.DEVICE, "Devices", ("devices",)),
            Nav(I.SETTINGS, "Preferences", ("prefs",)),
            ListItem(Label(self._pl_label(), id="pl-label"), disabled=True),
            **kw)

    @staticmethod
    def _pl_label() -> Text:
        return Text("\nPLAYLISTS", style=f"bold {mix(C.bg, C.acc, 0.65)}")

    def recolor(self):
        for nav in self.query(Nav):
            nav.recolor()
        self.query_one("#pl-label", Label).update(self._pl_label())

    def highlight(self, key: tuple):
        idx = NAV_INDEX.get(key[0])
        if idx is None:
            idx = next((i for i, n in enumerate(self.children) if getattr(n, "key", None) == key), None)
        if idx is not None and self.index != idx:
            self.index = idx

    def mark_context(self, st, marker: str | None = None):
        """The playlist that's playing gets the ▁▃▅ marker (marker: the current animation frame)."""
        for nav in self.query(Nav):
            on = len(nav.key) > 3 and bool(st.context) and nav.key[3] == st.context
            nav.set_playing((marker or "▁▃▅") if on else None)

    @work(group="side")
    async def load(self):
        app = self.app
        try:
            pls = await app.sp.playlists()
        except Exception as e:
            app.fail(e)
            return
        app.playlists = pls
        navs = [Nav(I.MUSIC, p.get("name") or "Untitled", playlist_key(p),
                    dim=(p.get("owner") or {}).get("id") == "spotify", thumb=pick_image(p.get("images"), 60),
                    sub=playlist_sub(p)) for p in pls]
        await self.extend(navs)
        self.highlight(app.view_key)
        self.mark_context(app.player)
        if not kittygfx.supported():
            return
        sem = asyncio.Semaphore(6)

        async def thumb(i, nav):
            if not nav.thumb_url:
                return
            async with sem:
                try:
                    img = await load_thumb(nav.thumb_url)
                except Exception:
                    return
            nav.has_img = True
            box = nav.query_one(".thumb", Static)
            box.update("")
            app.pics.set(f"nav:{i}", box, img)
            app.pics.request()

        await asyncio.gather(*(thumb(i, n) for i, n in enumerate(navs)))
