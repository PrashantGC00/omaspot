"""omaspot's Textual app: the window frame, player state, playback controls, navigation and menus.

Layout (omaspot.tcss): title bar (back/forward, breadcrumb, search, window buttons) · body: Sidebar | MainPane |
QueuePanel · FocusView (full window, replaces the body) · PlayerBar. Pages live in views.py.

Player state comes from Soloist's WebSocket while Omarchy plays (pushed, no polling) and from polling
`soloist ctl now` / the Web API otherwise. Controls update the UI first (optimistic) and are confirmed after.
"""
import asyncio
import os
import json
import shutil
import signal
import subprocess
import time
from dataclasses import replace

from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.geometry import Region
from textual.widgets import Button, Input, ListView, Static

from . import config, covers, keys, kittygfx, settings, views, window
from . import icons as I
from . import theme as T
from .api import ApiError, Spotify
from .components import FocusView, PlayerBar, QueuePanel, Sidebar, Transport, VolumeRow
from .covers import DEFAULT_PALETTE, cover_path, load_cover, load_thumb, pick_cover_url
from .focus import FocusCanvas
from .items import play_args
from .pane import EQ, TILE_W, MainPane
from .pics import Pics
from .soloist import Soloist
from .state import PlayerState, progress_from
from .theme import C
from .util import artists, web_link
from .widgets import Help, IconButton, Menu

DIVIDER = "─" * 30
COALESCE_S = 0.08          # a burst of volume / seek changes sends only its last value


class Omaspot(App):
    TITLE = "omaspot"
    ALLOW_SELECT = False      # no text selection: dragging a slider selected its characters (the "highlight")
    CSS_PATH = "omaspot.tcss"
    BINDINGS = keys.bindings()     # keys.py: one list for the bindings, ? help and Preferences > shortcuts

    def get_css_variables(self):
        v = super().get_css_variables()
        v.update(C.css())
        return v

    def __init__(self):
        super().__init__()
        self.settings = settings.load()      # Preferences page; ~/.config/omaspot/settings.json
        self.theme_bg = C.bg                  # the theme's own background (C.bg may be overridden)
        self.soloist = Soloist()
        self.sp = Spotify(self.soloist)
        self.pics = Pics(self)
        self.player = PlayerState()
        self.volume_pct = 40
        self.liked: dict[str, bool] = {}      # uri -> in Liked Songs (filled as pages load)
        self.playlists = None
        self.view_key: tuple = ("home",)
        self.history: list[tuple] = []
        self.hist_i = -1
        self.in_focus = False
        self.shown = True                     # the window is on screen (Hyprland), not just focused
        self.cover_url = None
        self.palette = list(DEFAULT_PALETTE)  # the playing song's cover colours (Home's wash, focus mode)
        self.last_uri = None
        self.notif_id = None
        self.art_rows = 8
        self.ctl_busy = 0                     # controls in flight: state that overlaps them is stale and dropped
        self.ctl_at = 0.0                     # when the last control finished
        self.poll_n = 0
        self.apply_appearance()
        self.ready = False                    # widget references below exist (set at the end of on_mount)

    # ---------- layout ----------
    def compose(self) -> ComposeResult:
        with Vertical(id="frame"):
            with Horizontal(id="titlebar"):
                yield Static("◉ omaspot", id="brand")
                yield IconButton(I.UNFOCUS, id="w-unfocus")      # only shown in focus mode: leaves it
                with Horizontal(id="hist"):
                    yield IconButton(I.LEFT, id="w-back", classes="off")
                    yield IconButton(I.RIGHT, id="w-fwd", classes="off")
                yield Static("Home", id="crumb")
                yield Input(placeholder=f"{I.SEARCH}  Search songs, artists, albums", id="search")
            with Horizontal(id="body"):
                yield Sidebar(id="side")
                yield MainPane(id="main")
                yield QueuePanel(id="qpanel")
            yield FocusView(id="focus")
            yield PlayerBar(id="player")

    async def on_mount(self):
        cell = kittygfx.cell_size()
        if cell:
            self.art_rows = max(4, round(TILE_W * cell[0] / cell[1]))
        q = self.query_one
        self.pane, self.side, self.queue_panel = q(MainPane), q(Sidebar), q(QueuePanel)
        self.bar, self.focus_view, self.canvas = q(PlayerBar), q(FocusView), q(FocusCanvas)
        self.search = q("#search", Input)
        self.transports = (self.bar.query_one(Transport), self.focus_view.query_one(Transport))
        for t in self.transports:
            t.tick(0, 0, True)
        self.show_volume(self.volume_pct, True)
        self.pane.build_columns()
        self.side.load()
        self.check_device()
        self.go(("home",))
        self.run_worker(self.soloist.listen(self.on_soloist_event), group="ws")
        self.set_interval(2.0, self.poll_timer)
        self.set_interval(0.25, self.tick)
        self.set_interval(0.18, self.animate_marker)
        self.set_interval(1.0, self.check_shown)          # hidden by Super+M or a workspace switch: stop drawing
        self.set_keymap(self.settings.keys)               # the user's own keys (Preferences > shortcuts)
        for sel in ("#grid", "#side", "#qlist", "#prefs"):
            self.watch(q(sel), "scroll_y", lambda *_: self.pics.request(), init=False)
        try:   # the theme-set hook sends SIGUSR1 after `omarchy theme set`
            asyncio.get_running_loop().add_signal_handler(signal.SIGUSR1, lambda: self.call_later(self.reload_theme))
        except (NotImplementedError, RuntimeError):
            pass
        self.ready = True
        self.pane.table.focus()

    def _end_update(self):
        # Kitty covers are placed inside the same synchronized update as the frame's text, so they move with
        # the page; placed after it, they trailed the text by a frame and scrolling looked choppy.
        # (_end_update is Textual's private hook that closes a frame's sync block; checked on Textual 8.2.)
        if self.ready and self.pics.dirty:
            self.pics.sync()
        super()._end_update()

    async def on_unmount(self):
        await self.sp.close()
        await covers.close()

    def set_crumb(self, text: str):
        self.query_one("#crumb", Static).update(text)

    def fail(self, e: Exception):
        if isinstance(e, ApiError):
            if e.status == 404 and "device" in e.message.lower():
                self.notify(f"{config.DEVICE_NAME} isn't online. Check: systemctl --user status soloist", severity="error")
            elif e.status == 403:
                self.notify(f"Spotify refused that: {e.message}", severity="warning")
            else:
                self.notify(f"Spotify error {e.status}: {e.message}", severity="error")
        else:
            self.notify(f"{type(e).__name__}: {e}", severity="error")

    # ---------- player state ----------
    def set_player(self, st: PlayerState):
        self.player = st
        self.render_player()

    def render_player(self):
        st = self.player
        self.bar.show(st)
        self.focus_view.show(st)
        for row in (self.bar.query_one(VolumeRow), self.focus_view.query_one(VolumeRow)):
            row.set_class(not st.item, "idle")           # greyed out until a song is loaded
        if st.volume is not None and st.volume != self.volume_pct:
            self.volume_pct = st.volume
            self.show_volume(st.volume)
        url = pick_cover_url(st.item or {})
        if not st.item and self.cover_url:           # nothing playing: drop the last cover everywhere
            self.cover_url = None
            self.bar.clear_mini()
            self.canvas.set_cover(None, list(DEFAULT_PALETTE))
        if url and url != self.cover_url:
            self.cover_url = url
            self.fetch_cover(url)
        if st.uri and st.uri != self.last_uri:
            if self.last_uri is not None:
                self.song_changed(st.item)
            self.last_uri = st.uri
        self.pane.mark_playing(st.uri)
        self.pane.refresh_num(self.pane.marked)      # its hover button follows play / pause
        self.pane.mark_context(st)
        self.side.mark_context(st)
        self.tick(force=True)

    def on_soloist_event(self, ev: dict):
        """Pushed by Soloist's WebSocket while it runs."""
        kind = ev.get("type")
        if kind == "queue_changed":
            if self.queue_panel.display:
                self.queue_panel.load()
            if self.in_focus:
                self.load_up_next()
            return
        if kind in ("auth_state", "ws_closed") or self.ctl_busy:
            return              # a control is in flight: its own refresh will confirm the result
        st = self.player
        if kind == "playback_state":
            new = PlayerState.from_soloist(ev)
            if new:
                self.set_player(new)
        elif st.source != "soloist":
            self.refresh_state()
        elif kind == "position_sync":
            self.set_player(st.with_(progress_ms=progress_from(ev.get("position"), st.playing)))
        elif kind == "playback_changed":
            self.set_player(st.with_(playing=ev.get("status") == "playing"))
        elif kind == "volume_changed" and ev.get("volume") is not None:
            self.set_player(replace(st, device={**(st.device or {}), "volume_percent": ev["volume"]}))
        else:
            self.refresh_state()        # track_changed and anything else: read the whole state (~10 ms)

    @work(group="refresh", exclusive=True)
    async def refresh_state(self):
        await self.poll()

    async def poll_timer(self):
        if self.soloist.connected and self.soloist.active:
            return              # Soloist pushes everything
        # another device (or Soloist without its WebSocket): poll, every 2 s shown, every 6 s hidden
        self.poll_n += 1
        if self.shown or self.poll_n % 3 == 0:
            await self.poll()

    async def poll(self):
        # Soloist first: the Web API reports no player state while Omarchy plays.
        started = time.monotonic()
        d = await self.soloist.now()
        st = PlayerState.from_soloist(d) if d else None
        if st is None:
            try:
                st = await self.sp.state()
            except Exception:
                return
        if self.ctl_busy or started < self.ctl_at:
            return     # a control ran meanwhile: this reading predates it and would undo the optimistic UI
        self.set_player(st)

    def tick(self, force: bool = False):
        """Seek bar and times. Only the visible set, and nothing while omaspot is hidden."""
        if not self.shown or not self.ready:
            return
        st = self.player
        self.transports[1 if self.in_focus else 0].tick(st.position(), st.duration, force)

    def animate_marker(self):
        if self.shown and not self.in_focus and self.player.playing:
            self.pane.animate_marker()
            self.side.mark_context(self.player, EQ[self.pane.eq_i % len(EQ)])

    def show_volume(self, pct: int, force: bool = False):
        for row in self.query(VolumeRow):
            row.show(pct, force)

    # ---------- controls ----------
    async def _send(self, coro_fn, *args):
        try:
            await coro_fn(*args)
        except Exception as e:
            self.fail(e)

    async def _controlled(self, coro):
        """Run a control; state that overlaps it is dropped, then one fresh read confirms the result."""
        self.ctl_busy += 1
        try:
            await coro
        finally:
            self.ctl_busy -= 1
            self.ctl_at = time.monotonic()
        if not self.ctl_busy:
            await self.poll()

    def control(self, coro_fn, *args):
        self.run_worker(self._controlled(self._send(coro_fn, *args)), group="ctl")

    def control_latest(self, kind: str, coro_fn, *args):
        """Volume / seek: each call cancels the pending one, so a wheel or key burst sends only its last value."""
        async def later():
            await asyncio.sleep(COALESCE_S)
            await self._send(coro_fn, *args)
        self.run_worker(self._controlled(later()), group=f"ctl-{kind}", exclusive=True)

    def optimistic(self, **changes):
        """Show the result of a control right away; the refresh after it confirms or corrects it."""
        if self.player.source != "none":
            self.set_player(self.player.with_(**changes))

    def has_song(self) -> bool:
        """The player controls only act on a loaded song; without one, Spotify answers "Device not found"
        (shown as "Omarchy isn't online"). So they're greyed out and give a short hint instead."""
        if self.player.item:
            return True
        now = time.monotonic()
        if now - getattr(self, "_hint_at", 0.0) > 4:
            self._hint_at = now
            self.notify("Pick a song to play first", timeout=2)
        return False

    def seek_frac(self, frac: float):
        if not self.has_song():
            self.tick(force=True)                 # put the knob back
            return
        if self.player.duration:
            self.seek_to(int(frac * self.player.duration))

    def seek_to(self, ms: int):
        self.optimistic(progress_ms=ms)
        self.control_latest("seek", self.sp.seek, ms)

    def volume_frac(self, frac: float):
        if not self.has_song():
            self.show_volume(self.volume_pct, True)       # put the slider back
            return
        self.volume_pct = round(frac * 100)
        self.show_volume(self.volume_pct)
        if self.player.device:
            self.player = replace(self.player, device={**self.player.device, "volume_percent": self.volume_pct})
        self.control_latest("vol", self.sp.volume, self.volume_pct)

    def action_toggle(self):
        if not self.has_song():
            return
        st = self.player
        was_playing, has_device = st.playing, bool(st.device)     # read before the optimistic flip
        if has_device:
            self.optimistic(playing=not was_playing)
        if was_playing:
            self.control(self.sp.pause)
        elif has_device:
            self.control(self.sp.resume)
        else:
            self.control(self.sp.play)

    def action_next(self):
        if self.has_song():
            self.control(self.sp.next)

    def action_prev(self):
        if self.has_song():
            self.control(self.sp.previous)

    def action_volume(self, delta: int):
        self.volume_frac(max(0, min(100, self.volume_pct + delta)) / 100)

    def action_seek(self, seconds: int):
        if not self.has_song():
            return
        self.seek_to(max(0, self.player.position() + seconds * 1000))

    def action_shuffle(self):
        if not self.has_song():
            return
        on = not self.player.shuffle
        self.optimistic(shuffle=on)
        self.control(self.sp.shuffle, on)

    def action_repeat(self):
        if not self.has_song():
            return
        nxt = {"off": "context", "context": "track", "track": "off"}.get(self.player.repeat, "off")
        self.optimistic(repeat=nxt)
        self.control(self.sp.repeat, nxt)

    def action_queue_add(self):
        tr = self.pane.cursor_track()
        if tr:
            self.queue_track(tr)

    @work(group="dev")
    async def check_device(self):
        try:
            d = await self.sp.find_device()
        except Exception as e:
            self.fail(e)
            return
        if not d:
            self.bar.device_offline()
            self.notify(f"Device \"{config.DEVICE_NAME}\" not found. Is Soloist running? systemctl --user status soloist",
                        severity="warning", timeout=8)

    # ---------- song change, cover, accent ----------
    def song_changed(self, item: dict):
        if self.queue_panel.display:
            self.queue_panel.load()
        if self.in_focus:
            self.load_up_next()
        if not self.shown and self.settings.notifications and shutil.which("notify-send"):
            self.notify_song(item)

    @work(group="upnext", exclusive=True)
    async def load_up_next(self):
        """Focus mode's "up next": the next song (from Soloist while Omarchy plays, ~10 ms)."""
        try:
            items = await self.sp.queue()
        except Exception:
            return              # up next just stays as it was
        self.canvas.set_up_next([(t.get("name", ""), artists(t)) for t in items[:3]])

    @work(group="notify", exclusive=True)
    async def notify_song(self, item: dict):
        """Desktop notification with the cover when the song changes while omaspot is hidden."""
        url, icon = pick_cover_url(item), []
        if url:
            try:
                await load_thumb(url)
                icon = ["-i", str(cover_path(url))]
            except Exception:
                pass
        album = (item.get("album") or {}).get("name", "")
        args = ["notify-send", "-a", "omaspot", "-p", *icon, *(["-r", str(self.notif_id)] if self.notif_id else []),
                item.get("name", ""), " · ".join(x for x in (artists(item), album) if x)]
        try:
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 3)
            if out.strip().isdigit():
                self.notif_id = int(out.strip())
        except (OSError, asyncio.TimeoutError):
            pass

    @work(exclusive=True, group="cover")
    async def fetch_cover(self, url: str):
        try:
            img, pal = await load_cover(url)
        except Exception:
            return
        self.canvas.set_cover(img, pal)
        if pal != self.palette:
            self.palette = list(pal)
            self.pane.decorate_grid()          # Home's wash follows the playing album
            if self.settings.bg == "album":    # Preferences > Background > Album follows the song
                self.apply_appearance()
                self.recolor()
        self.set_accent(self.accent_for(pal))
        if kittygfx.supported():
            self.bar.set_mini(img=img)
        else:
            self.bar.set_mini(palette=pal)

    def set_accent(self, colour: str):
        """Recolour the accents (play buttons, seek/volume fill, ▁▃▅ marker, hearts, sidebar highlight) to the album."""
        if colour == C.acc:
            return
        C.acc = colour
        self.recolor()
        if self.view_key[0] == "liked" and not self.in_focus:
            self.pane.refresh_liked_cover()

    def recolor(self):
        self.refresh_css()
        self.side.recolor()
        self.pane.recolor()
        self.render_player()
        self.show_volume(self.volume_pct, True)

    def accent_for(self, pal) -> str:
        return T.accent_from_palette(pal, C.theme_accent) if self.settings.album_accent else C.theme_accent

    def apply_appearance(self):
        """Theme colours + Preferences (background colour, opacity) into the palette and the kitty window."""
        from .prefs import album_bg
        s = self.settings
        pal = T.load()
        self.theme_bg = pal["bg"]
        C.bg_override = None if s.bg == "theme" else album_bg(self.palette) if s.bg == "album" else s.bg
        C.apply(pal)
        window.apply(C.bg, C.panel, C.sel, s.opacity)

    def set_pref(self, key: str, value):
        """A change on the Preferences page: save it and apply just what it affects."""
        setattr(self.settings, key, value)
        self.settings.clean()
        try:
            settings.save(self.settings)
        except OSError as e:
            self.notify(f"Couldn't save preferences: {e.strerror}", severity="error")
        if key == "album_accent":
            self.set_accent(self.accent_for(self.palette))
        elif key == "header_wash":
            self.pane.draw_wash()
            self.pane.decorate_grid()
        elif key == "keys":
            self.set_keymap(self.settings.keys)
        elif key in ("bg", "opacity"):
            self.apply_appearance()
            self.recolor()
            self.canvas.redraw()
        for page in self.query("Preferences"):
            page.redraw()

    def reload_theme(self):
        """Omarchy theme changed (SIGUSR1 from the theme-set hook): reload colours and redraw."""
        self.apply_appearance()
        self.recolor()
        if not self.in_focus:
            self.open_view(self.view_key)
        self.canvas.redraw()

    # ---------- navigation ----------
    def go(self, key: tuple):
        """Open a page and remember it for back/forward."""
        if key[0] == "focus":
            self.open_view(key)
            return
        if key[0] == "queue":
            self.action_toggle_queue()
            self.side.highlight(self.view_key)
            return
        if not (0 <= self.hist_i < len(self.history) and self.history[self.hist_i] == key):
            self.history = self.history[:self.hist_i + 1] + [key]
            self.hist_i = len(self.history) - 1
        self.update_hist_buttons()
        self.open_view(key)

    def action_back(self):
        if self.in_focus:
            self.exit_focus()
            return
        if self.hist_i > 0:
            self.hist_i -= 1
            self.update_hist_buttons()
            self.open_view(self.history[self.hist_i])

    def action_forward(self):
        if self.hist_i < len(self.history) - 1:
            self.hist_i += 1
            self.update_hist_buttons()
            self.open_view(self.history[self.hist_i])

    def update_hist_buttons(self):
        self.query_one("#w-back").set_class(self.hist_i <= 0, "off")
        self.query_one("#w-fwd").set_class(self.hist_i >= len(self.history) - 1, "off")

    @work(exclusive=True, group="view")
    async def open_view(self, key: tuple):
        if key[0] == "focus":
            self.side.highlight(self.view_key)
            await self.enter_focus()
            return
        self.view_key = key
        if self.in_focus:
            self.exit_focus()
        self.side.highlight(key)
        page = views.ROUTES.get(key[0])
        if page is None:
            return
        try:
            await page(self, key)
        except Exception as e:
            self.fail(e)
            self.pane.show_empty(I.WARN, "Couldn't load this page", str(e)[:200])

    @work(group="npartist", exclusive=True)
    async def goto_np_artist(self):
        first = next(iter((self.player.item or {}).get("artists") or []), None)
        if not first:
            return
        if first.get("id"):          # Soloist and the Web API both give ids now; search is the last resort
            self.go(("artist", first["id"], first.get("name", "")))
            return
        try:
            a = await self.sp.find_artist(first.get("name", ""))
        except Exception as e:
            self.fail(e)
            return
        if a:
            self.go(("artist", a["id"], a.get("name", "")))

    # ---------- queue panel ----------
    def action_toggle_queue(self):
        p = self.queue_panel
        p.display = not p.display
        self.bar.query_one("#b-queue").set_class(bool(p.display), "on")
        if p.display:
            p.load()
        else:
            self.pics.drop("q:")
        self.pics.request()

    def queue_track(self, tr: dict):
        self.control(self.sp.add_to_queue, tr["uri"])
        self.notify(f"Added to queue: {tr.get('name', '')}")
        if self.queue_panel.display and not self.soloist.connected:
            self.set_timer(0.8, self.queue_panel.load)     # with the WebSocket, queue_changed reloads it

    # ---------- menus ----------
    def open_menu(self, items, x: int, y: int):
        # covers under the menu are cut around it (Pics.sync), inside the frame that draws the menu
        self.pics.occlude = Menu.region_for(items, x, y, self.size)
        self.pics.request()

        def done(cb=None):
            self.pics.occlude = None
            self.pics.request()
            if cb:
                cb()
        self.push_screen(Menu(items, x, y), done)

    def track_menu(self, tr: dict, x: int, y: int, row: dict | None = None):
        uri = tr.get("uri")
        album = tr.get("album") or {}
        play = (row or {}).get("play") or {"uris": [uri]}
        items = [(f"{I.PLAY}  Play", lambda: self.control(self.sp.play, *play_args(play))),
                 (f"{I.QUEUE}  Add to queue", lambda: self.queue_track(tr)),
                 (f"{I.HEART}  Remove from Liked Songs" if self.liked.get(uri) else f"{I.HEART_O}  Save to Liked Songs",
                  lambda: self.toggle_like(uri)),
                 (DIVIDER, None)]
        if album.get("id"):
            items.append(("◆  Go to album", lambda: self.go(("album", album["id"]))))
        for a in [a for a in tr.get("artists", []) if a.get("id")][:3]:
            items.append((f"{I.ARTIST}  Go to {a.get('name', 'artist')}", lambda a=a: self.go(("artist", a["id"], a.get("name", "")))))
        items += [(DIVIDER, None), (f"{I.LINK}  Copy song link", lambda: self.copy_link(uri))]
        self.open_menu(items, x, y)

    def tile_menu(self, it: dict, x: int, y: int):
        if it["kind"] == "track":
            self.track_menu(it["tr"], x, y)
            return
        items = [(f"{I.MUSIC}  Open", lambda: self.go(it["key"]))]
        if it["kind"] in ("album", "playlist") and it.get("uri"):
            items.append((f"{I.PLAY}  Play", lambda: self.control(self.sp.play, None, it["uri"], None)))
        items += [(DIVIDER, None), (f"{I.LINK}  Copy link", lambda: self.copy_link(it.get("uri")))]
        self.open_menu(items, x, y)

    @work(group="like")
    async def toggle_like(self, uri: str):
        cur = self.liked.get(uri)
        try:
            if cur is None:
                cur = (await self.sp.liked_flags([uri]))[0]
            self.liked[uri] = not cur
            self.pane.refresh_uri(uri)
            await self.sp.like(uri, not cur)
        except Exception as e:
            self.liked[uri] = bool(cur)
            self.pane.refresh_uri(uri)
            self.fail(e)
            return
        self.notify("Removed from Liked Songs" if cur else "Saved to Liked Songs")

    def copy_link(self, uri: str | None):
        if not uri:
            return
        link = web_link(uri)
        try:
            subprocess.run(["wl-copy"], input=link.encode(), timeout=2, check=True)
            self.notify("Link copied")
        except (OSError, subprocess.SubprocessError):
            self.notify(link)

    def action_help(self):
        self.pics.occlude = Region(0, 0, self.size.width, self.size.height)   # kitty images would show through
        self.pics.sync()

        def closed(_=None):
            self.pics.occlude = None
            self.pics.sync()
        self.push_screen(Help(), closed)

    # ---------- window ----------
    # Drawing (Turntable, marquees, the seek bar) runs while the window is *on screen*, not only while it
    # has keyboard focus: Hyprland moves focus with the mouse, and pausing whenever the pointer left the window
    # stopped Turntable mid-song. Visibility comes from Hyprland (our kitty window, by KITTY_PID, on a workspace
    # a monitor is showing); without Hyprland, focus is the fallback.
    def on_app_blur(self):
        if self.ready and not self.can_see_windows():
            self.set_shown(False)
        self.check_shown()

    def on_app_focus(self):
        if self.ready:
            self.set_shown(True)
        self.check_shown()

    def can_see_windows(self) -> bool:
        return bool(os.environ.get("KITTY_PID")) and bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")) \
            and shutil.which("hyprctl") is not None

    def set_shown(self, shown: bool):
        if shown == self.shown:
            return
        self.shown = shown
        self.canvas.visible = shown
        if shown:
            self.canvas.redraw()
            self.tick(force=True)
            self.refresh_state()

    @work(group="shown", exclusive=True)
    async def check_shown(self):
        """Is our window on a workspace that a monitor is showing (a normal one, or the Super+M overlay)?"""
        if not self.ready or not self.can_see_windows():
            return
        try:
            async def hypr(what):
                proc = await asyncio.create_subprocess_exec("hyprctl", "-j", what, stdout=asyncio.subprocess.PIPE,
                                                            stderr=asyncio.subprocess.DEVNULL)
                out, _ = await asyncio.wait_for(proc.communicate(), 2)
                return json.loads(out or b"[]")
            clients, monitors = await asyncio.gather(hypr("clients"), hypr("monitors"))
        except (OSError, ValueError, asyncio.TimeoutError):
            return
        pid = int(os.environ["KITTY_PID"])
        win = next((c for c in clients if c.get("pid") == pid), None)
        if win is None:
            return
        showing = {m.get("activeWorkspace", {}).get("name") for m in monitors}
        showing |= {m.get("specialWorkspace", {}).get("name") for m in monitors if m.get("specialWorkspace", {}).get("name")}
        self.set_shown(win.get("workspace", {}).get("name") in showing)

    def on_resize(self, event: events.Resize):
        # the main pane re-flows itself when its own size changes (MainPane.on_resize); here the window's size
        # isn't laid out into the pane yet, which made the tile grid re-flow one resize late
        if self.ready:
            self.canvas.redraw()

    async def enter_focus(self):
        if self.in_focus:
            return
        self.in_focus = True
        for sel, show in (("#body", False), ("#player", False), ("#focus", True), ("#w-unfocus", True)):
            self.query_one(sel).display = show
        self.set_crumb("Turntable")
        # nothing focusable is visible here; without this the search box takes focus and swallows keys (space, n, p…)
        self.set_focus(None)
        self.pics.sync()          # removes the normal-mode covers
        self.tick(force=True)
        self.load_up_next()
        await self.canvas.activate()

    @work(group="focus")
    async def exit_focus(self):
        if not self.in_focus:
            return
        self.in_focus = False
        for sel, show in (("#body", True), ("#player", True), ("#focus", False), ("#w-unfocus", False)):
            self.query_one(sel).display = show
        await self.canvas.deactivate()
        self.tick(force=True)
        self.pics.request()
        self.side.highlight(self.view_key)
        self.pane.table.focus()

    def hide_overlay(self):
        exe = shutil.which("omaspot-overlay")
        if exe:
            subprocess.Popen([exe, "hide"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        else:
            self.notify("The overlay script isn't installed. Press q to quit.")

    # ---------- events ----------
    def on_list_view_selected(self, event: ListView.Selected):
        key = getattr(event.item, "key", None)
        if key:
            self.go(key)

    def on_input_submitted(self, event: Input.Submitted):
        q = event.value.strip()
        if q:
            self.go(("query", q))

    def on_button_pressed(self, event: Button.Pressed):
        fn = {"w-back": self.action_back, "w-fwd": self.action_forward,
              "w-unfocus": self.exit_focus}.get(event.button.id)
        if fn:
            fn()

    def action_focus_search(self):
        self.search.focus()

    def action_open(self, what: str):
        self.go((what,))

    def action_focus_mode(self):
        if self.in_focus:
            self.exit_focus()
        else:
            self.open_view(("focus",))

    def action_escape(self):
        if self.focused is self.search:
            self.pane.table.focus()
            return
        if self.in_focus:
            self.exit_focus()
            return
        self.hide_overlay()
