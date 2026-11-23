"""The main pane: a page header (cover, kind, title, subtitle, Play/Shuffle/Sort), the track table, the tile
grid and the empty-state message. Pages (views.py) fill it through set_mode / set_header / show_tracks /
show_grid / show_empty; it handles its own clicks."""
import asyncio
import itertools
import random

from PIL import Image, ImageDraw, ImageFont
from rich.text import Text
from textual import work
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Static

from . import icons as I
from . import hero as art      # "hero" is also set_mode()'s parameter
from . import kittygfx
from .covers import load_cover, load_thumb
from .items import play_args
from .theme import C, mix, rgb
from .util import ago, artists, fmt
from .widgets import Head, IconButton, Link, Scroller, Tile, Tracks

COLS = ["#", "Title", "Album", "Added", "", "Len"]
C_NUM, C_TITLE, C_ALBUM, C_ADDED, C_LIKE, C_LEN = range(6)
SORTS = [("#", "Custom order"), ("Title", "Title"), ("Artist", "Artist"), ("Album", "Album"), ("Added", "Date added")]
SORT_KEYS = {
    "#": lambda t: t.get("_i", 0),
    "Title": lambda t: (t.get("name") or "").lower(),
    "Artist": lambda t: artists(t).lower(),
    "Album": lambda t: ((t.get("album") or {}).get("name") or "").lower(),
    "Added": lambda t: t.get("_added") or "",
    "Len": lambda t: t.get("duration_ms") or 0,
}
TILE_W = 16
EQ = ["▁▃▅", "▂▅▇", "▃▇▅", "▅▆▃", "▇▄▂", "▆▂▃", "▄▃▆", "▂▅▄"]     # the playing song's marker, animated
TILE_ICON = {"album": "◆", "playlist": I.MUSIC, "artist": I.ARTIST, "track": I.MUSIC, "ph": ""}
MAX_PAGES = 40
FONT = "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf"


def sort_tracks(tracks: list[dict], name: str, desc: bool) -> list[dict]:
    return sorted(tracks, key=SORT_KEYS[name], reverse=desc)


def liked_cover(acc: str, bg: str, hi: str, size: int = 160) -> Image.Image:
    """Liked Songs cover: a diagonal gradient in the accent colour with a heart."""
    g = Image.linear_gradient("L").resize((size, size))
    mask = Image.blend(g, g.rotate(90), 0.5)          # (x + y) / 2: top-left 0 → bottom-right 255
    img = Image.composite(Image.new("RGB", (size, size), rgb(mix(bg, acc, 0.30))), Image.new("RGB", (size, size), rgb(acc)), mask)
    try:
        font = ImageFont.truetype(FONT, 64)
        ImageDraw.Draw(img).text((size / 2, size / 2), I.HEART, font=font, anchor="mm", fill=rgb(mix(hi, "#ffffff", 0.5)))
    except OSError:
        pass
    return img


class MainPane(Vertical):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.rows: list[dict] = []
        self.tracks: list[dict] = []      # tracks of the current list view, in their original order
        self.play_mode = None             # ("uris",) or ("context", uri) for the current list
        self.sort = ("#", False)
        self.marked = -1                  # table row showing the ▁▃▅ now-playing marker
        self.hover = -1                   # table row under the mouse: its number turns into a ▶ button
        self.eq_i = 0
        self.mode = "list"
        self.widths = [4, 40, 30, 11, 2, 6]
        self.hdr = None                   # (kind, title, sub)
        self.hero_url = self.hero_img = self.hero_artist = None
        self.hero_palette = None          # the header's cover colours, for the wash behind it
        self.playing_tiles: list[Tile] = []
        self.grid_sections: list[dict] = []
        self.grid_gen = 0
        self.grid_per = 0
        self.grid_lock = asyncio.Lock()
        self.pages: dict[int, tuple] = {}  # "Show all" grids: id -> (title, items)
        self._page_ids = itertools.count()

    def compose(self):
        with Horizontal(id="hero"):
            yield Static(I.HEART, id="cover")
            with Vertical(id="hinfo"):
                yield Link("", self.hero_clicked, id="hdr")
                with Horizontal(id="acts"):
                    yield IconButton(f"{I.PLAY}  Play", id="a-play")
                    yield IconButton(f"{I.SHUFFLE}  Shuffle", id="a-shuf")
                    yield IconButton(f"{I.SORT}  {SORTS[0][1]}", id="a-sort")
        yield Static("", id="ttitle")
        yield Head(id="thead")
        yield Tracks(id="table", cursor_type="row", zebra_stripes=False, show_header=False,
                     cursor_foreground_priority="renderable")   # cell colours (accent marker) win on the cursor row
        yield Scroller(id="grid", step=2)
        prefs = Scroller(id="prefs", step=2)
        prefs.can_focus = False        # focus stays on a row: clicking empty space used to leave the arrows only scrolling
        yield prefs
        yield Static("", id="empty")

    def on_mount(self):
        self.table = self.query_one("#table", Tracks)

    # ---------- table ----------
    def build_columns(self):
        """Size Title/Album to the pane (55/45), then re-add the current rows."""
        t = self.table
        width = t.size.width or 90
        free = max(30, width - (4 + 11 + 2 + 6) - 2 * 6 - 2)
        widths = [4, free * 55 // 100, free - free * 55 // 100, 11, 2, 6]
        if widths == self.widths and t.columns:
            return                          # same width: nothing to rebuild
        t.clear(columns=True)
        for name, w in zip(COLS, widths):
            t.add_column(name, width=w)
        self.widths = widths
        self.draw_head()
        self.fill_table()

    def draw_head(self):
        head, spans, x = Text(), [], 0
        sort_col, desc = self.sort
        for name, w in zip(COLS, self.widths):
            label = name + ((" " + (I.DOWN if desc else I.UP)) if name == sort_col and name and (name != "#" or desc) else "")
            cell = label.rjust(w) if name in ("#", "Len") else label.ljust(w)
            head.append(" " + cell[:w] + " ", style=f"bold {C.acc}" if name == sort_col and name != "#" else "")
            if name:
                spans.append((x, x + w + 2, name))
            x += w + 2
        h = self.query_one("#thead", Head)
        h.spans = spans
        h.update(head)

    def hot(self, i: int) -> bool:
        """Show the ▶ button on this row: it's under the mouse, or it's the cursor row while the table has focus."""
        return i >= 0 and (i == self.hover or (i == self.table.cursor_row and self.table.has_focus))

    def num_cell(self, i: int) -> Text:
        """The # column: the track number, ▶ on a hot row, the ▁▃▅ marker on the playing row (pause on hover)."""
        tr = self.rows[i]["tr"]
        if i == self.marked:
            if self.hot(i):
                return Text(I.PAUSE if self.app.player.playing else I.PLAY, style=f"bold {C.acc}", justify="right")
            return self.marker_text()
        if self.hot(i):
            return Text(I.PLAY, style=f"bold {C.acc}", justify="right")
        return Text(str(tr.get("_i", 0) + 1), style=C.dim, justify="right")

    def refresh_num(self, i: int):
        cols = self._cols()
        if cols and 0 <= i < len(self.rows) and self.rows[i]["kind"] == "track":
            self.table.update_cell(str(i), cols[C_NUM], self.num_cell(i))

    def cells_for(self, i: int):
        r = self.rows[i]
        if r["kind"] != "track":
            return r["cells"]
        tr, playing = r["tr"], i == self.marked
        num = self.num_cell(i)
        title = Text.assemble((tr.get("name", ""), f"bold {C.hi}" if playing else C.fg), "\n", (artists(tr), C.soft),
                              no_wrap=True, overflow="ellipsis")
        album = Text((tr.get("album") or {}).get("name", ""), style=C.hi if playing else C.soft, no_wrap=True, overflow="ellipsis")
        return (num, title, album, Text(ago(tr.get("_added")), style=C.dim, no_wrap=True), self.heart(tr.get("uri")),
                Text(fmt(tr.get("duration_ms")), style=C.soft, justify="right"))

    def heart(self, uri) -> Text:
        liked = self.app.liked.get(uri)
        return Text(I.HEART, style=C.acc) if liked else Text(I.HEART_O, style=C.dim) if liked is False else Text("")

    def fill_table(self):
        t = self.table
        t.clear()
        self.marked = -1
        for i, r in enumerate(self.rows):
            t.add_row(*self.cells_for(i), key=str(i), height=r.get("h", 1))
        self.mark_playing(self.app.player.uri)

    def _cols(self):
        cols = list(self.table.columns.keys())
        return cols if len(cols) == 6 else None

    def refresh_row(self, i: int):
        cols = self._cols()
        if not cols or not (0 <= i < len(self.rows)):
            return
        for c, v in zip(cols, self.cells_for(i)):
            self.table.update_cell(str(i), c, v)

    def recolor(self):
        """Accent changed: only the cells drawn in it change (playing marker, filled hearts, loading bars),
        instead of rebuilding the whole table on every song change."""
        self.draw_head()
        if self.hdr:
            self.draw_hdr()
            self.draw_wash()
        self.decorate_grid()
        self.mark_context(self.app.player, force=True)
        cols = self._cols()
        if not cols:
            return
        if any(r["kind"] == "placeholder" for r in self.rows):
            self.rows = self.placeholder_rows(len(self.rows))
            return self.fill_table()
        for i, r in enumerate(self.rows):
            if i == self.marked:
                self.refresh_row(i)
            elif r["kind"] == "track" and self.app.liked.get(r["tr"].get("uri")):
                self.table.update_cell(str(i), cols[C_LIKE], self.heart(r["tr"].get("uri")))

    def refresh_uri(self, uri: str):
        for i, r in enumerate(self.rows):
            if r["kind"] == "track" and r["tr"].get("uri") == uri:
                self.refresh_row(i)

    def show_rows(self, rows: list[dict]):
        self.rows = rows
        self.fill_table()
        if rows:
            first = next((i for i, r in enumerate(rows) if r["kind"] != "placeholder"), 0)
            self.table.move_cursor(row=first)

    def mark_playing(self, uri: str | None):
        """Bold title and the animated marker on the row of the song that's playing."""
        new = next((i for i, r in enumerate(self.rows) if r["kind"] == "track" and r["tr"].get("uri") == uri), -1)
        if new == self.marked:
            return
        old, self.marked = self.marked, new
        self.refresh_row(old)
        self.refresh_row(new)

    def marker_text(self) -> Text:
        """▁▃▅ bars that move while the song plays and freeze when it's paused."""
        return Text(EQ[self.eq_i % len(EQ)], style=f"bold {C.acc}", justify="right")

    def animate_marker(self):
        self.eq_i += 1
        self.animate_tiles()
        cols = self._cols()
        if self.marked < 0 or not cols or not self.table.display or self.marked >= len(self.rows) or self.hot(self.marked):
            return                          # (a hovered playing row shows its pause button, not the marker)
        self.table.update_cell(str(self.marked), cols[C_NUM], self.marker_text())

    def placeholder_rows(self, n: int = 8):
        """Dim bars where songs will appear, while a list loads."""
        widths = [18, 26, 14, 22, 30, 16, 24, 12, 20, 28]
        c = mix(C.bg, C.acc, 0.35)         # loading bars in a dim album accent
        return [{"kind": "placeholder", "h": 2,
                 "cells": (Text("·", style=c, justify="right"),
                           Text.assemble(("━" * widths[i % 10], c), "\n", ("━" * (widths[(i + 3) % 10] // 2 + 4), c)),
                           Text("━" * (widths[(i + 6) % 10] // 2 + 6), style=c), Text("━━━━━", style=c), "",
                           Text("━━━", style=c, justify="right"))}
                for i in range(n)]

    def show_tracks(self, tracks, mode):
        """mode: ("uris",) plays the list itself; ("context", uri) plays inside a playlist or album."""
        for i, t in enumerate(tracks):
            t["_i"] = i
        self.tracks, self.play_mode = list(tracks), mode
        self.sort = ("#", False)
        self.draw_head()
        self.query_one("#a-sort", IconButton).label = f"{I.SORT}  {SORTS[0][1]}"
        self.show_rows(self._rows_for(self.tracks))
        self.fetch_flags(self.rows)

    def _rows_for(self, tracks):
        if self.play_mode and self.play_mode[0] == "context":
            ctx = self.play_mode[1]
            return [{"kind": "track", "h": 2, "tr": t, "play": {"context": ctx, "offset": {"uri": t["uri"]}}} for t in tracks]
        uris = [t["uri"] for t in tracks]
        return [{"kind": "track", "h": 2, "tr": t, "play": {"uris": uris, "offset": {"position": i}}}
                for i, t in enumerate(tracks)]

    def sort_by(self, name: str):
        """Header click: sort by that column; clicking it again flips the order."""
        if not self.tracks or name not in SORT_KEYS:
            return
        col, desc = self.sort
        self.sort = (name, not desc if col == name else name in ("Added",))
        label = dict(SORTS).get(name, name)
        self.query_one("#a-sort", IconButton).label = \
            f"{I.SORT}  {label}{' ' + (I.DOWN if self.sort[1] else I.UP) if name != '#' else ''}"
        self.draw_head()
        self.show_rows(self._rows_for(sort_tracks(self.tracks, name, self.sort[1])))

    def cycle_sort(self):
        names = [n for n, _ in SORTS]
        cur = self.sort[0] if self.sort[0] in names else "#"
        self.sort = ("", False)
        self.sort_by(names[(names.index(cur) + 1) % len(names)])

    @work(group="flags", exclusive=True)
    async def fetch_flags(self, rows):
        """Fill the heart column: which of these songs are in Liked Songs."""
        liked = self.app.liked
        uris = list(dict.fromkeys(r["tr"]["uri"] for r in rows
                                  if r["kind"] == "track" and r["tr"].get("uri") and r["tr"]["uri"] not in liked))
        if not uris:
            return
        try:
            flags = await self.app.sp.liked_flags(uris)
        except Exception:
            return              # hearts just stay blank
        liked.update(zip(uris, flags))
        cols = self._cols()
        if self.rows is rows and cols:
            for i, r in enumerate(rows):
                if r["kind"] == "track":
                    self.table.update_cell(str(i), cols[C_LIKE], self.heart(r["tr"].get("uri")))

    def cursor_track(self) -> dict | None:
        i = self.table.cursor_row
        return self.rows[i]["tr"] if 0 <= i < len(self.rows) and self.rows[i]["kind"] == "track" else None

    # ---------- modes ----------
    def set_mode(self, mode: str, hero: bool = True, table_lines: int | None = None, title: str | None = None):
        """list: header + table. grid: cover tiles. mixed: a short table over tiles (artist, search). empty: a message."""
        q = self.query_one
        q("#hero").display = hero
        tt = q("#ttitle", Static)
        tt.display = bool(title) and mode == "mixed"
        cell = kittygfx.cell_size() if kittygfx.supported() else None
        if cell and title:          # kitty: "Popular" / "Songs" in the same big type as the grid's section titles
            tt.update("")
            ch, hi = cell[1], C.hi
            self.app.pics.set("ttitle", tt, lambda px: art.section(px, ch, title, hi), sig=("tt", title, hi))
        else:
            tt.update(title or "")
        table_on = mode in ("list", "mixed")
        q("#thead").display = table_on
        self.table.display = table_on
        self.table.styles.height = table_lines if mode == "mixed" and table_lines else "1fr"
        q("#grid").display = mode in ("grid", "mixed")
        q("#empty").display = mode == "empty"
        q("#prefs").display = mode == "prefs"
        q("#a-sort").display = mode == "list"
        if mode not in ("grid", "mixed"):
            self.grid_sections = []
            self.app.pics.drop("tile:")
        self.mode = mode
        self.app.pics.request()

    def show_empty(self, icon: str, title: str, msg: str, hero: bool = False):
        self.tracks, self.play_mode = [], None
        self.rows = []
        self.fill_table()
        self.set_mode("empty", hero=hero)
        self.query_one("#empty", Static).update(Text.assemble((icon + "\n\n", f"bold {C.acc}"), (title + "\n", f"bold {C.hi}"),
                                                              (msg, C.soft), justify="center"))

    def show_devices(self, ds: list[dict]):
        self.set_mode("list")
        self.tracks, self.play_mode = [], None
        self.show_rows([{
            "kind": "device", "id": d.get("id"),
            "cells": (Text(I.DEVICE, style=C.acc if d.get("is_active") else C.dim, justify="right"), d.get("name", ""),
                      Text(d.get("type", ""), style=C.soft), Text("playing here" if d.get("is_active") else "", style=C.acc), "",
                      Text(f"{d.get('volume_percent') or 0}%", justify="right", style=C.soft)),
        } for d in ds])

    # ---------- header ----------
    def set_header(self, kind: str, title: str, sub: str, cover: str = I.MUSIC, actions: bool = True,
                   cover_url: str | None = None, cover_img=None, artist=None):
        self.hero_artist = artist
        box = self.query_one("#cover", Static)
        if cover_img is not None and kittygfx.supported():
            self.hero_url, self.hero_img = None, cover_img
            box.update("")
        elif cover_url and kittygfx.supported():
            if cover_url != self.hero_url:
                self.hero_url, self.hero_img = cover_url, None
                box.update(Text(cover, style=f"bold {C.hi}"))
                self.fetch_hero(cover_url)
        else:
            self.hero_url, self.hero_img = None, None
            box.update(Text(cover, style=f"bold {C.hi}"))
        self.app.pics.set("hero", box, self.hero_img)
        if cover_img is not None or not cover_url:
            self.hero_palette = None          # accent wash (Liked Songs, devices, covers still loading)
        self.hdr = (kind, title, sub)
        self.draw_hdr()
        self.draw_wash()
        self.query_one("#acts").display = actions
        self.app.pics.request()

    def draw_hdr(self):
        kind, title, sub = self.hdr
        hdr = self.query_one("#hdr", Static)
        cell = kittygfx.cell_size() if kittygfx.supported() else None
        if not cell:
            hdr.update(Text.assemble((kind.upper() + "\n", f"bold {C.acc}"), (title + "\n", f"bold {C.hi}"),
                                     (sub, f"underline {C.soft}" if self.hero_artist else C.soft)))
            return
        hdr.update("")                       # kitty: big type drawn as an image; the Link still takes clicks
        ch, colors, link = cell[1], (C.hi, C.soft, C.acc), bool(self.hero_artist)
        self.app.pics.set("herotitle", hdr, lambda px: art.title_block(px, ch, kind, title, sub, colors, link),
                          sig=("title", kind, title, sub, link, colors))
        self.app.pics.request()

    def wash_palette(self):
        if self.hero_palette:
            return self.hero_palette
        return [rgb(C.acc), rgb(mix(C.bg, C.acc, 0.55)), rgb(mix(C.bg, C.acc, 0.35)), rgb(C.hi)]

    def draw_wash(self):
        """The flat wash of the header cover's colours behind the header, fading into the list (kitty only)."""
        if not kittygfx.supported():
            return
        if not self.app.settings.header_wash:
            self.app.pics.set("herowash", self.query_one("#hero"), None)     # removes it
            self.app.pics.request()
            return
        pal = [tuple(c) for c in self.wash_palette()]
        self.app.pics.set("herowash", self.query_one("#hero"), lambda px: art.wash(px, pal), z=-3, sig=("wash", tuple(pal)))
        self.app.pics.request()

    def hero_clicked(self):
        if self.hero_artist:
            self.app.go(("artist", *self.hero_artist))

    def liked_tile(self):
        return liked_cover(C.acc, C.bg, C.hi)

    def refresh_liked_cover(self):
        if kittygfx.supported():
            self.hero_img = self.liked_tile()
            self.app.pics.set("hero", self.query_one("#cover"), self.hero_img)
            self.app.pics.request()

    @work(exclusive=True, group="hero")
    async def fetch_hero(self, url: str):
        try:
            img, pal = await load_cover(url)
        except Exception:
            return
        if url == self.hero_url:
            self.hero_img, self.hero_palette = img, pal
            self.draw_wash()
            self.query_one("#cover", Static).update("")
            self.app.pics.set("hero", self.query_one("#cover"), img)
            self.app.pics.request()

    # ---------- grid ----------
    def tile_for(self, it: dict) -> Tile:
        return Tile(it, self.app.art_rows, TILE_ICON.get(it["kind"], I.MUSIC), it.get("title", ""), it.get("sub", ""))

    def grid_per_row(self) -> int:
        w = (self.content_size.width or 80) - 1
        return max(1, (w + 2) // (TILE_W + 2))

    def ph_section(self, title: str) -> dict:
        return {"title": title, "one_row": True, "items": [{"kind": "ph", "title": "━" * 10, "sub": "━" * 6}] * self.grid_per_row()}

    def add_page(self, title: str, items: list) -> int:
        """A "Show all" page; only the most recent MAX_PAGES are kept (every Home load and resize adds some)."""
        pid = next(self._page_ids)
        self.pages[pid] = (title, items)
        for old in [k for k in self.pages if k <= pid - MAX_PAGES]:
            del self.pages[old]
        return pid

    async def show_grid(self, sections: list[dict]):
        self.grid_sections = sections
        await self.render_grid()

    async def render_grid(self):
        async with self.grid_lock:
            self.grid_gen += 1
            gen = self.grid_gen
            grid = self.query_one("#grid", VerticalScroll)
            self.app.pics.drop("tile:")
            await grid.remove_children()
            per = self.grid_per = self.grid_per_row()
            big = kittygfx.supported() and kittygfx.cell_size() is not None    # kitty: big type as images
            widgets = []
            for sec in self.grid_sections:
                if "greeting" in sec:
                    g = Static("" if big else Text(sec["greeting"]), classes="greet")
                    g.big_text = sec["greeting"]
                    widgets.append(g)
                    continue
                items = sec.get("items") or []
                if not items:
                    continue
                one_row = sec.get("one_row")
                if sec.get("title"):
                    t = Static("" if big else sec["title"], classes="gt")
                    t.big_text = sec["title"]
                    kids = [t]
                    if one_row and len(items) > per:
                        pid = self.add_page(sec["title"], items)
                        kids.append(Link("Show all", lambda pid=pid: self.app.go(("grid", pid)), classes="more"))
                    widgets.append(Horizontal(*kids, classes="ghead"))
                shown = items[:per] if one_row else items
                for i in range(0, len(shown), per):
                    widgets.append(Horizontal(*[self.tile_for(it) for it in shown[i:i + per]], classes="trow"))
                widgets.append(Static("", classes="gap1"))
            await grid.mount_all(widgets)
            grid.scroll_home(animate=False)
        self.decorate_grid()
        self.mark_context(self.app.player, force=True)
        self.load_tiles(gen)

    def decorate_grid(self):
        """Home's greeting (over a wash of the playing album's colours) and section titles in big type."""
        cell = kittygfx.cell_size() if kittygfx.supported() else None
        if not cell:
            return
        ch, pics, gen = cell[1], self.app.pics, self.grid_gen
        pal = [tuple(c) for c in self.app.palette]
        for i, w in enumerate(self.query(".greet")):
            text = getattr(w, "big_text", "")
            washed = self.app.settings.header_wash
            pics.set(f"tile:greet:{gen}:{i}", w, lambda px, t=text: art.greeting(px, ch, t, pal, C.hi, washed),
                     sig=("greet", text, tuple(pal), C.hi, washed))
        for i, w in enumerate(self.query(".gt")):
            text = getattr(w, "big_text", "")
            pics.set(f"tile:gt:{gen}:{i}", w, lambda px, t=text: art.section(px, ch, t, C.hi), sig=("gt", text, C.hi))
        pics.request()

    async def show_prefs(self):
        from .prefs import Preferences
        box = self.query_one("#prefs", Scroller)
        page = box.query(Preferences)
        if page:
            page.first().redraw()
        else:
            await box.mount(Preferences())
        cell = kittygfx.cell_size() if kittygfx.supported() else None
        title = box.query(".ptitle")
        if cell and title:                    # kitty: the page title in big type, like Home's
            t, ch = title.first(), cell[1]
            t.update("")
            self.app.pics.set("ptitle", t, lambda px: art.section(px, ch, "Preferences", C.hi), sig=("ptitle", C.hi))
            self.app.pics.request()
        rows = box.query("PrefRow")
        if rows:
            rows.first().focus()

    # ---------- what's playing, on tiles ----------
    @staticmethod
    def tile_playing(it: dict, st) -> bool:
        if not st.item:
            return False
        kind, uri = it.get("kind"), it.get("uri")
        if kind == "track":
            return uri == st.uri
        if kind == "playlist":
            return bool(uri) and uri == st.context
        if kind == "album":
            return bool(uri) and uri in (st.context, st.album_uri)
        return False

    def mark_context(self, st, force: bool = False):
        """The tile that's playing (album, playlist or song) gets the ▁▃▅ marker and an accent title."""
        now = [t for t in self.query(Tile) if self.tile_playing(t.item, st)]
        if not force and now == self.playing_tiles:
            return
        for t in self.playing_tiles:
            if t not in now and t.is_attached:
                t.set_playing(None)
        self.playing_tiles = now
        for t in now:
            t.set_playing(EQ[self.eq_i % len(EQ)])

    def animate_tiles(self):
        for t in self.playing_tiles:
            if t.is_attached:
                t.set_playing(EQ[self.eq_i % len(EQ)])

    @work(group="tiles", exclusive=True)
    async def load_tiles(self, gen: int):
        if not kittygfx.supported():
            return
        tiles = list(self.query(Tile))
        sem = asyncio.Semaphore(6)
        pics = self.app.pics

        async def one(i, tile):
            url = tile.item.get("url")
            if not url:
                return
            async with sem:
                try:
                    img = await load_thumb(url)
                except Exception:
                    return
            if gen != self.grid_gen or not tile.is_attached:
                return
            box = tile.query_one(".art", Static)
            box.update("")
            pics.set(f"tile:{gen}:{i}", box, img)
            pics.request()

        await asyncio.gather(*(one(i, t) for i, t in enumerate(tiles)))

    def on_resize(self, event):
        """Window tiled/resized or queue panel toggled: this event comes with the pane's *new* size, so the
        grid re-flows to fit (measuring at the window's resize used the old width: 5 tiles in room for 3)."""
        self.call_after_refresh(self.relayout)

    def relayout(self):
        """The pane changed width (window resize, queue panel): resize columns and re-flow tiles."""
        self.build_columns()
        if self.grid_sections and self.grid_per_row() != self.grid_per:
            self.run_worker(self.render_grid, group="regrid", exclusive=True)
        self.app.pics.request()

    # ---------- input ----------
    def activate_row(self, i: int):
        if not (0 <= i < len(self.rows)):
            return
        r = self.rows[i]
        if r["kind"] == "track" and r.get("play"):
            self.app.control(self.app.sp.play, *play_args(r["play"]))
        elif r["kind"] == "device":
            self.app.control(self.app.sp.transfer, r["id"])

    def play_first(self):
        i = next((i for i, r in enumerate(self.rows) if r["kind"] == "track"), None)
        if i is not None:
            self.activate_row(i)

    def shuffle_play(self):
        idx = [i for i, r in enumerate(self.rows) if r["kind"] == "track"]
        if idx:
            self.app.control(self.app.sp.shuffle, True)
            self.activate_row(random.choice(idx))

    def on_button_pressed(self, event: Button.Pressed):
        fn = {"a-play": self.play_first, "a-shuf": self.shuffle_play, "a-sort": self.cycle_sort}.get(event.button.id)
        if fn:
            event.stop()
            fn()

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        event.stop()
        self.activate_row(event.cursor_row)

    def on_tracks_hot(self, msg: Tracks.Hot):
        old, self.hover = self.hover, msg.hover
        for i in {old, self.hover, self.table.cursor_row}:
            self.refresh_num(i)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        prev, self._cursor = getattr(self, "_cursor", -1), event.cursor_row
        self.refresh_num(prev)
        self.refresh_num(event.cursor_row)

    def on_head_sort(self, event: Head.Sort):
        self.sort_by(event.name)

    def on_tile_clicked(self, msg: Tile.Clicked):
        it, event, app = msg.tile.item, msg.event, self.app
        if it["kind"] == "ph":
            return
        if event.button == 3:
            app.tile_menu(it, event.screen_x, event.screen_y)
        elif event.button == 1:
            if it["kind"] == "track":
                app.control(app.sp.play, *play_args(it["play"]))
            else:
                app.go(it["key"])

    def on_tracks_clicked(self, msg: Tracks.Clicked):
        row, col, event, app = msg.row, msg.col, msg.event, self.app
        if not (0 <= row < len(self.rows)):
            return
        r = self.rows[row]
        if event.button == 3:
            if r["kind"] == "track":
                app.track_menu(r["tr"], event.screen_x, event.screen_y, r)
            return
        if event.button != 1:
            return
        if r["kind"] == "track" and col == C_NUM:
            if event.chain < 2:                 # the ▶ button: one click plays (or pauses the playing song)
                app.action_toggle() if row == self.marked else self.activate_row(row)
            return
        if r["kind"] == "track" and event.chain < 2:
            tr = r["tr"]
            if col == C_LIKE:
                app.toggle_like(tr["uri"])
                return
            if col == C_TITLE and msg.line == 1:
                a = next((a for a in tr.get("artists", []) if a.get("id")), None)
                if a:
                    app.go(("artist", a["id"], a.get("name", "")))
                return
            if col == C_ALBUM and (tr.get("album") or {}).get("id") and app.view_key[0] != "album":
                app.go(("album", tr["album"]["id"]))
                return
        if event.chain >= 2:
            self.activate_row(row)
