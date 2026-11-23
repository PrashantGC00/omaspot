"""Focus mode: the whole view is composed as one grid of terminal cells.

Back to front:
  1. cava visualizer, 24 bars in the album's 4 colours, blurred and half see-through (Pillow, half-block pixels)
  2. the song cover, centred (half-block pixels)
  3. the record, drawn with text symbols (░▒▓█, ◉│╯╭─■), its colours mixed with what's behind it so it
     reads as translucent and slightly soft. The label is the cover itself, turning with the record.

A terminal can't layer an image under text or blur anything, so all mixing happens here, per cell.
"""
import asyncio
import math
import time

from PIL import Image, ImageDraw, ImageFilter
from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widget import Widget

from . import config, kittygfx, scene
from . import theme as T
from .covers import DEFAULT_PALETTE, mix, to_hex

OMEGA = 2 * math.pi / 1.8          # 33⅓ rpm
CAVA_CONF = config.CACHE / "cava.conf"


def _bg():
    """The theme background as RGB (read live, so a theme reload applies)."""
    return T.rgb(T.C.bg)


# ---------------- cava ----------------
class Cava:
    """Runs cava with raw ASCII output on stdout and keeps the latest 24 levels (0..1)."""

    def __init__(self, bars: int = 24):
        self.bars = bars
        self.levels = [0.0] * bars
        self.proc = None
        self.task = None

    def _write_conf(self):
        CAVA_CONF.parent.mkdir(parents=True, exist_ok=True)
        CAVA_CONF.write_text(
            "[general]\nbars = %d\nframerate = 30\n\n[input]\nmethod = pipewire\nsource = auto\n\n"
            "[output]\nmethod = raw\nraw_target = /dev/stdout\ndata_format = ascii\nascii_max_range = 100\n"
            "bar_delimiter = 59\nframe_delimiter = 10\n\n[smoothing]\nnoise_reduction = 70\n" % self.bars)

    async def start(self):
        if self.proc:
            return
        self._write_conf()
        try:
            self.proc = await asyncio.create_subprocess_exec(
                "cava", "-p", str(CAVA_CONF), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            self.proc = None
            return
        self.task = asyncio.create_task(self._read())

    async def _read(self):
        try:
            while self.proc and self.proc.stdout:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                vals = [v for v in line.decode(errors="ignore").strip().split(";") if v]
                if len(vals) >= self.bars:
                    self.levels = [min(1.0, int(v) / 100) for v in vals[: self.bars]]
        except (asyncio.CancelledError, ValueError):
            pass

    async def stop(self):
        if self.task:
            self.task.cancel()
            self.task = None
        if self.proc:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), 2)
            except (ProcessLookupError, asyncio.TimeoutError):
                pass
            self.proc = None
        self.levels = [0.0] * self.bars


# ---------------- the canvas ----------------
class FocusCanvas(Widget):
    DEFAULT_CSS = "FocusCanvas { width: 1fr; height: 1fr; }"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.playing = False
        self.cover: Image.Image | None = None
        self.palette = list(DEFAULT_PALETTE)
        self.cava = Cava(bars=40)
        self.rot = 0.0
        self.speed = 0.0
        self.arm = 0.42
        self.peaks = [0.0] * 40
        self.shown = [0.0] * 40
        self.title, self.sub, self.up_next = "", "", []
        self._static_key = None    # what the sleeve/type layer was last drawn for
        self._discs = (None, {})   # (size key, {phase: disc image})
        self._backdrop = None
        self.pulse = scene.Pulse()  # the album-colour wash beats with the bass
        self._last = time.monotonic()
        self._rows: list[Strip] = []
        self._styles: dict = {}
        self._timer = None
        self._cover_fit = None   # (size, image) resized cover cache
        self.gfx = kittygfx.supported()   # kitty: draw the scene as one real image (crisp); else half-block cells
        self._font = None
        self.visible = True      # False while the overlay is hidden (app lost focus): nothing is drawn
        self._idle_frames = 0

    # lifecycle, driven by the app when focus mode opens/closes
    async def activate(self):
        await self.cava.start()
        if not self._timer:
            self._timer = self.set_interval(1 / (30 if self.gfx else 20), self._frame)

    async def deactivate(self):
        if self._timer:
            self._timer.stop()
            self._timer = None
        if self.gfx:
            self._write(kittygfx.clear)
            self._static_key = self._title_key = None
        await self.cava.stop()

    def _write(self, fn, *args):
        drv = getattr(self.app, "_driver", None)
        if drv is not None:
            fn(drv.write, *args)

    def set_cover(self, img, palette):
        # drop both resized-cover caches, or a new song with a same-size cover keeps showing the old one
        self.cover, self.palette, self._cover_fit = img, palette, None
        self._idle_frames = 0

    def redraw(self):
        """Force the next frames to draw (window shown again, resized, track changed)."""
        self._idle_frames = 0

    def _style(self, fg, bg):
        k = (fg, bg)
        s = self._styles.get(k)
        if s is None:
            if len(self._styles) > 20000:
                self._styles.clear()
            s = self._styles[k] = Style(color=to_hex(fg), bgcolor=to_hex(bg))
        return s

    async def _frame(self):
        # drawing happens in a worker thread (Pillow releases the GIL), so keys and clicks stay responsive
        if getattr(self, "_busy", False):
            return
        self._busy = True
        try:
            await self._frame_inner()
        finally:
            self._busy = False

    async def _frame_inner(self):
        now = time.monotonic()
        dt, self._last = min(0.1, now - self._last), now
        self.speed += ((OMEGA if self.playing else 0.0) - self.speed) * (0.06 if self.playing else 0.1)
        self.rot += self.speed * dt
        self.arm += ((0.42 if self.playing else 0.08) - self.arm) * 0.08
        w, h = self.size.width, self.size.height
        if w < 20 or h < 8 or not self.visible:
            return
        # paused and settled (record stopped, bars down): draw a few more frames, then stop until something moves
        moving = (self.playing or self.speed > 0.02 or self.pulse.value > 0.01 or getattr(self, "marquee", False)
                  or any(v > 0.01 for v in self.shown) or any(v > 0.01 for v in self.peaks))
        self._idle_frames = 0 if moving else self._idle_frames + 1
        if self._idle_frames > 6:
            return
        if self.gfx:
            cell = kittygfx.cell_size()
            if cell:
                await self._frame_kitty(w, h, dt, cell[0], cell[1])
                return
            self.gfx = False
        else:
            self._cell_dt = dt
        self._rows = await asyncio.to_thread(self._paint, w, h)
        self.refresh()

    def render_line(self, y: int) -> Strip:
        if not self.gfx and 0 <= y < len(self._rows):
            return self._rows[y]
        return Strip.blank(self.size.width, Style(bgcolor=to_hex(_bg())))

    def _glyph(self, glyph, fg, alpha, font, tw, th):
        key = (glyph, fg, alpha, tw, th)
        cache = self.__dict__.setdefault("_tiles", {})
        tile = cache.get(key)
        if tile is None:
            if len(cache) > 4000:
                cache.clear()
            tile = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
            ImageDraw.Draw(tile).text((0, 0), glyph, font=font, fill=fg + (alpha,))
            cache[key] = tile
        return tile

    # ---- kitty renderer: the sleeve scene (scene.py), three images ----
    def set_info(self, title: str, sub: str):
        if (title, sub) != (self.title, self.sub):
            self.title, self.sub, self._idle_frames = title, sub, 0

    def set_up_next(self, items):
        items = list(items)[:1]                # focus mode shows just the next song
        if items != self.up_next:
            self.up_next, self._idle_frames = items, 0

    def _step_levels(self, dt: float):
        n = len(self.shown)
        levels = self.cava.levels if self.playing else [0.0] * n
        rise, fall = 1 - math.exp(-dt * 22), 1 - math.exp(-dt * 7)
        for i in range(n):
            target = levels[i] if i < len(levels) else 0.0
            self.shown[i] += (target - self.shown[i]) * (rise if target > self.shown[i] else fall)
            self.peaks[i] = max(self.shown[i], self.peaks[i] - 0.18 * dt)

    def _paint_bars_k(self, L, dt: float):
        self._step_levels(dt)
        size = (max(1, L.FW // 4), max(1, L.FH // 4))
        key = (size, tuple(map(tuple, self.palette)))
        if not self._backdrop or self._backdrop[0] != key:
            self._backdrop = (key, scene.backdrop(size, self.palette))
        levels = self.cava.levels if self.playing else [0.0] * len(self.shown)
        beat = self.pulse.step(levels, dt) if getattr(self.app, "settings", None) is None or self.app.settings.focus_pulse else 0.0
        base = scene.pulse_backdrop(self._backdrop[1], beat)
        return scene.paint_bars(base, L, self.shown, self.peaks, self.palette, 0.25)

    def _paint_record_k(self, L, box, cw: float, ch: float):
        tw, th = cw / 2, ch / 2                       # grooves at half-cell size
        key = (round(L.R, 1), round(tw, 2), round(th, 2))
        if self._discs[0] != key:
            self._discs = (key, {})
        idx = int((self.rot % math.pi) / math.pi * scene.PHASES) % scene.PHASES
        img = self._discs[1].get(idx)
        if img is None:
            tile = lambda g, fg, a, w, h: self._glyph(g, fg, a, scene.font(h * 0.86), w, h)
            img = self._discs[1][idx] = scene.disc(L.R, L.hole, tw, th, idx * math.pi / scene.PHASES, tile)
        lift = max(0.0, min(1.0, (0.42 - self.arm) / 0.34))     # 0 = on the record, 1 = lifted
        return scene.paint_record(L, box, cw, ch, img, lift)

    async def _frame_kitty(self, w: int, h: int, dt: float, cw: float, ch: float):
        L = scene.layout(int(w * cw), int(h * ch), ch)
        r = self.region
        bars = await asyncio.to_thread(self._paint_bars_k, L, dt)
        self._write(kittygfx.place, bars, r.x, r.y, w, h, kittygfx.BARS_ID, -3)
        skey = (r.x, r.y, w, h, id(self.cover), self.title, self.sub, tuple(self.up_next), T.C.acc, T.C.hi)
        if skey != self._static_key:
            self._static_key = skey
            colors = (T.C.hi, T.C.soft, T.C.acc, T.C.dim)
            st = await asyncio.to_thread(scene.paint_static, L, cw, ch, self.cover, self.title, self.sub, self.up_next, colors)
            self._write(kittygfx.place, st, r.x, r.y, w, h, kittygfx.STATIC_ID, -1)
        await self._title_k(L, r, dt, cw, ch)
        self._tick = getattr(self, "_tick", 0) + 1
        if self._tick % 2 == 0 or self._idle_frames:      # record + arm at 15 fps
            box = scene.record_box(L, cw, ch)
            rec = await asyncio.to_thread(self._paint_record_k, L, box, cw, ch)
            self._write(kittygfx.place, rec, r.x + box[0], r.y + box[1], box[2], box[3], kittygfx.IMAGE_ID, -2)

    async def _title_k(self, L, r, dt: float, cw: float, ch: float):
        """The big title: drawn once when it fits; a long one slides left (~70 px/s), holds 2 s at the start of
        each pass, and loops. Its own small image, so the rest of the scene isn't redrawn for it."""
        box = scene.title_box(L, cw, ch)
        text = self.title or "Nothing playing"
        self.marquee = scene.title_overflows(L, text, ch)
        key = (r.x, r.y, box, text, T.C.hi)
        if key != getattr(self, "_title_key", None):
            self._title_key, self._title_off, self._title_hold = key, 0.0, 2.0
        elif not self.marquee:
            return                                     # fits and already shown
        if self.marquee:
            if self._title_hold > 0:
                self._title_hold -= dt
                if getattr(self, "_title_sent", None) == key:
                    return
            else:
                loop = scene.title_font(ch).getlength(text) + scene.TITLE_GAP * cw * 2
                self._title_off += 70 * dt
                if self._title_off >= loop:
                    self._title_off, self._title_hold = 0.0, 2.0
        img = await asyncio.to_thread(scene.paint_title, L, box, cw, ch, text, T.C.hi, self._title_off)
        self._write(kittygfx.place, img, r.x + box[0], r.y + box[1], box[2], box[3], kittygfx.TITLE_ID, -1)
        self._title_sent = key

    # ---- composition ----
    def _paint(self, W: int, H: int):
        PW, PH = W, H * 2                       # one pixel per column, two per row (▀ top / bottom)
        pal = self.palette

        # 1. visualizer: bars drawn on the background colour, blurred, then mixed in at ~55%
        viz = Image.new("RGB", (PW, PH), _bg())
        d = ImageDraw.Draw(viz)
        n = len(self.shown)
        levels = self.cava.levels if self.playing else [0.0] * n
        inset = PW * 0.08                         # bars stop short of the edges, as in the design
        slot = (PW - 2 * inset) / n
        bar_w = max(1, int(round(slot * 0.55)))
        top_limit = PH * 0.70
        for i in range(n):
            target = levels[i] if i < len(levels) else 0.0
            self.shown[i] += (target - self.shown[i]) * (0.5 if target > self.shown[i] else 0.2)
            self.peaks[i] = max(self.shown[i], self.peaks[i] - 0.012)
            bh = self.shown[i] * top_limit
            x0 = int(inset + i * slot + (slot - bar_w) / 2)
            col = pal[min(3, i * 4 // n)]
            if bh >= 1:
                d.rectangle([x0, PH - bh, x0 + bar_w - 1, PH - 1], fill=col)
            ph = self.peaks[i] * top_limit
            if ph > bh + 2:
                d.rectangle([x0, PH - ph - 1, x0 + bar_w - 1, PH - ph], fill=pal[3])
        viz = viz.filter(ImageFilter.GaussianBlur(0.9))     # soft, but still clearly bars
        base = Image.blend(Image.new("RGB", (PW, PH), _bg()), viz, 0.62)

        # 2. cover: a crisp square, centred, in full colour
        side = int(min(PH * 0.78, PW * 0.6))
        side -= side % 2
        cx_px, cy_px = PW // 2, int(PH * 0.47)
        if self.cover is not None and side > 4:
            if not self._cover_fit or self._cover_fit[0] != side:
                fit = self.cover.resize((side, side), Image.LANCZOS)
                self._cover_fit = (side, fit)
            base.paste(self._cover_fit[1], (cx_px - side // 2, cy_px - side // 2))
        px = base.load()

        # cells: ▀ with fg = top pixel, bg = bottom pixel
        grid = [[("▀", px[x, 2 * y], px[x, 2 * y + 1]) for x in range(W)] for y in range(H)]

        # 3. record, in text symbols, centred over the cover
        R = (side / 2) * 0.86 / 2               # radius in rows (a row is 2 px tall)
        ccx, ccy = cx_px / 1.0, cy_px / 2.0   # record centred on the cover
        label_r = R * 0.36
        for y in range(max(0, int(ccy - R) - 1), min(H, int(ccy + R) + 2)):
            for x in range(max(0, int(ccx - 2 * R) - 1), min(W, int(ccx + 2 * R) + 2)):
                dx, dy = (x + 0.5 - ccx) / 2.0, (y + 0.5 - ccy)
                r = math.hypot(dx, dy)
                if r > R or r < label_r:                         # hollow centre
                    continue
                under = mix(grid[y][x][1], grid[y][x][2], 0.5)
                t = (math.atan2(dy, dx) - self.rot) % (2 * math.pi)
                edge = max(0.0, (r - (R - 1.2)) / 1.2)           # soft rim: blends into what's behind
                sheen = math.cos(2 * t)                          # two highlight bands that turn with the record
                if sheen > 0.86:
                    ch, fg = "▓", (160, 166, 170)
                elif sheen > 0.62:
                    ch, fg = "▒", (104, 111, 116)
                else:
                    ch, fg = ("░", (78, 84, 88)) if int(r * 2) % 2 else ("▒", (50, 56, 60))
                body = mix((12, 13, 14), under, 0.45 + 0.45 * edge)   # ~55% opaque: the cover shows through
                fg = mix(fg, under, 0.30 + 0.45 * edge)               # translucent and soft, more so at the rim
                grid[y][x] = (ch, fg, body)


        # tonearm: pivot top-right of the record, one cell per row, ╯──╭ where it steps left
        arm_col = (195, 199, 201)
        px0, py0 = int(ccx + 2 * R * 0.95), max(0, int(ccy - R) - 1)
        length = int(R * 1.15)
        if 0 <= px0 < W:
            grid[py0][px0] = ("◉", (157, 162, 165), grid[py0][px0][2])
        last = px0
        for ay in range(1, length + 1):
            yy = py0 + ay
            if yy >= H:
                break
            ax = max(0, min(W - 1, round(px0 - math.tan(self.arm) * ay * 2)))
            if ax < last:
                if not (ay == 1):
                    grid[yy - 1][last] = ("╯", arm_col, grid[yy - 1][last][2])
                for k in range(ax + 1, last):
                    grid[yy - 1][k] = ("─", arm_col, grid[yy - 1][k][2])
                grid[yy - 1][ax] = ("╭", arm_col, grid[yy - 1][ax][2])
            grid[yy][ax] = ("■" if ay == length else "│", arm_col if ay < length else (157, 162, 165), grid[yy][ax][2])
            last = ax

        return [Strip([Segment(ch, self._style(fg, bg)) for ch, fg, bg in row], W) for row in grid]

    def _label_colour(self, rr: float, t: float, pal):
        """The label is the cover, sampled in polar coordinates so it turns with the record."""
        if self.cover is None:
            return pal[int(t / (math.pi / 2)) % 4]
        sw, sh = self.cover.size
        u = 0.5 + 0.5 * rr * math.cos(t)
        v = 0.5 + 0.5 * rr * math.sin(t)
        return self.cover.getpixel((min(sw - 1, int(u * sw)), min(sh - 1, int(v * sh))))
