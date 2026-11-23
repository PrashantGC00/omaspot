"""Focus mode's "sleeve" scene for kitty, as three images (back to front):

  1. BARS   (redrawn every frame, small): a soft flat wash of the album's colours + the visualizer, a mirrored
            "horizon" of bars running the full width under the sleeve and the record, peak caps falling slowly.
  2. RECORD (every other frame, only the record's box, full resolution): the record pulled half out of the
            sleeve, spinning, drawn with the terminal font's own glyphs (fine ░▒▓ grooves at half-cell size),
            hollow centre, and the pixel-stepped tonearm (rests on the record while playing, lifts when paused).
            The disc is rendered once per rotation phase and cached, so a frame is a paste plus the arm.
  3. STATIC (only when the song, queue or window changes, full resolution): the cover as the sleeve, the big
            title and artist, and "up next".

Everything is flat: no shadow, glow or bevel. Geometry comes from layout(), shared by all three.
"""
import math
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

FONT = "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf"
FONT_BOLD = "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Bold.ttf"
TEXT_ROWS = 6.2                 # height of the stacked details under the sleeve, in cell rows
PHASES = 16                     # cached disc rotations over half a turn (the sheen repeats every π)
_fonts: dict = {}


def font(size: float, bold: bool = False):
    key = (max(6, int(size)), bold)
    f = _fonts.get(key)
    if f is None:
        try:
            f = ImageFont.truetype(FONT_BOLD if bold else FONT, key[0])
        except OSError:
            f = ImageFont.load_default()
        _fonts[key] = f
    return f


@dataclass(frozen=True)
class Layout:
    """Positions in full-resolution pixels of the canvas (W*cw x H*ch)."""
    FW: int
    FH: int
    sx: float          # sleeve left / top / side
    sy: float
    S: float
    cx: float          # record centre / radius / hole radius
    cy: float
    R: float
    hole: float
    pivot: tuple       # tonearm pivot, needle on the record, needle at rest
    on: tuple
    rest: tuple
    band_y: float      # bars: centre line, half height, left / right
    band_h: float
    bx0: float
    bx1: float
    text_y: float      # title block top
    right: float       # right edge of the sleeve + record group


def layout(FW: int, FH: int, ch: float | None = None) -> Layout:
    """Everything centred in the canvas, by what you see:
    horizontally the sleeve's left edge to the record's right edge (the thin tonearm may stick out a little);
    vertically the stack of sleeve, bars (with their reflection) and the title / up-next block."""
    ch = ch or FH / 34
    gap = FH * 0.03
    text_h = ch * TEXT_ROWS                # title, artist, then UP NEXT + the next song, stacked
    band_h = FH * 0.09
    bars_h = band_h * 1.45                 # bars above the centre line + their reflection below it
    S = min(FH * 0.50, FW * 0.30, FH * 0.94 - (gap * 2 + bars_h + text_h))
    S = max(S, ch * 4)
    R = S * 0.46
    off = S * 0.58                         # record centre, right of the sleeve's centre: it's pulled half out
    visible_w = S / 2 + off + R            # sleeve left edge → record right edge
    sx = (FW - visible_w) / 2
    total_h = S + gap + bars_h + gap + text_h
    sy = max(FH * 0.02, (FH - total_h) / 2)
    cx, cy = sx + S / 2 + off, sy + S / 2
    pivot = (cx + R * 1.05, cy - R * 0.92)
    on = (cx + R * 0.50, cy + R * 0.30)
    rest = (pivot[0] + R * 0.10, pivot[1] + R * 1.05)
    band_y = sy + S + gap + band_h         # the bars' centre line
    text_y = sy + S + gap + bars_h + gap
    return Layout(FW, FH, sx, sy, S, cx, cy, R, R * 0.30, pivot, on, rest,
                  band_y, band_h, FW * 0.06, FW * 0.94, text_y, cx + R)


# ---------------- 1. backdrop + bars ----------------
def backdrop(size, palette, alpha: int = 46) -> Image.Image:
    """A flat diagonal wash between two album colours, mostly see-through."""
    w, h = size
    g = Image.linear_gradient("L")
    mask = Image.blend(g, g.rotate(90), 0.5).resize((w, h))      # square first: rotating a non-square one leaves a seam
    img = Image.composite(Image.new("RGB", (w, h), tuple(palette[2])), Image.new("RGB", (w, h), tuple(palette[0])), mask)
    img = img.convert("RGBA")
    # fade out towards every edge, so the wash has no hard rectangle against the rows around the canvas
    f = max(2, int(min(w, h) * 0.12))
    edge = Image.new("L", (w, h), 0)
    ImageDraw.Draw(edge).rectangle([f, f, w - 1 - f, h - 1 - f], fill=alpha)
    img.putalpha(edge.filter(ImageFilter.GaussianBlur(f * 0.6)))
    return img


class Pulse:
    """A 0..1 beat from cava's low bars (bass and kick, the left of the 40). It flashes when the bass jumps
    above its recent level, so a loud song isn't simply brighter but beats, and decays in ~0.25 s."""

    def __init__(self):
        self.avg = 0.0          # the bass's recent level (~0.4 s memory)
        self.value = 0.0
        self.age = 0.0          # seconds of audio seen: eases in, so cava starting from silence isn't a "beat"

    def step(self, levels, dt: float) -> float:
        n = max(1, len(levels) // 6)
        bass = sum(levels[:n]) / n if levels else 0.0
        if bass > 0.01 and self.age == 0.0:
            self.avg = bass                  # first sound: start from its level, or the catch-up reads as a beat
        self.age = self.age + dt if bass > 0.01 else 0.0
        self.avg += (bass - self.avg) * (1 - math.exp(-dt * 2.5))
        onset = max(0.0, bass - self.avg)
        target = min(1.0, bass * 0.35 + onset * 2.2) * min(1.0, self.age / 0.6)
        rate = 30 if target > self.value else 9             # fast attack, quick release
        self.value += (target - self.value) * (1 - math.exp(-dt * rate))
        return self.value


_pulse_luts: dict = {}


def pulse_backdrop(base: Image.Image, pulse: float, gain: float = 1.7) -> Image.Image:
    """The wash, stronger by up to (1 + gain)x on a beat. Flat: only its opacity changes (no glow or blur)."""
    step = round(pulse * 20)                                 # 21 levels, so the lookup tables are reused
    if step == 0:
        return base
    lut = _pulse_luts.get(step)
    if lut is None:
        k = 1 + gain * step / 20
        lut = _pulse_luts[step] = [min(255, int(v * k)) for v in range(256)]
    img = base.copy()
    img.putalpha(base.getchannel("A").point(lut))
    return img


def paint_bars(base: Image.Image, L: Layout, shown, peaks, palette, scale: float) -> Image.Image:
    """Mirrored bars on the horizon: up from the centre line in the album's colours, a fainter reflection down."""
    img = base.copy()
    bars = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(bars)
    n = len(shown)
    x0, x1 = L.bx0 * scale, L.bx1 * scale
    cy, hh = L.band_y * scale, L.band_h * scale
    slot = (x1 - x0) / n
    bw = max(1.0, slot * 0.55)
    for i in range(n):
        col = tuple(palette[min(3, i * 4 // n)])
        x = x0 + i * slot + (slot - bw) / 2
        bh = shown[i] * hh
        if bh >= 0.5:
            d.rectangle([x, cy - bh, x + bw, cy], fill=col + (170,))
            d.rectangle([x, cy + 1, x + bw, cy + 1 + bh * 0.45], fill=col + (60,))
        ph = peaks[i] * hh
        if ph > bh + 1:
            d.rectangle([x, cy - ph - 1, x + bw, cy - ph], fill=tuple(palette[3]) + (170,))
    bars = bars.convert("RGBa").filter(ImageFilter.GaussianBlur(0.8)).convert("RGBA")   # soft edges, premultiplied
    img.alpha_composite(bars)
    return img


# ---------------- 2. record ----------------
def record_box(L: Layout, cw: float, ch: float):
    """The cell box (col, row, cols, rows) holding the record and the tonearm at any lift."""
    xs = [L.cx - L.R, L.cx + L.R, L.pivot[0] + cw, L.rest[0] + cw]
    ys = [L.cy - L.R, L.cy + L.R, L.pivot[1] - ch, L.rest[1] + ch]
    c0, r0 = int(min(xs) // cw), int(min(ys) // ch)
    c1, r1 = int(math.ceil(max(xs) / cw)), int(math.ceil(max(ys) / ch))
    return c0, r0, c1 - c0, r1 - r0


def groove_glyph(angle: float, heavy: bool) -> str:
    """The box-drawing stroke along a circle at `angle` (radians, screen coordinates, y down)."""
    t = math.degrees(angle + math.pi / 2) % 180            # direction of the tangent, 0-180°
    if t < 22.5 or t >= 157.5:
        return "━" if heavy else "─"
    if t < 67.5:
        return "╲"
    if t < 112.5:
        return "┃" if heavy else "│"
    return "╱"


def disc(R: float, hole: float, tw: float, th: float, phase: float, glyph_tile) -> Image.Image:
    """The record at one rotation: grooves drawn as box-drawing strokes along each ring (─ │ ╱ ╲), shades
    alternating ring by ring, a sheen of heavy strokes (━ ┃) that turns with the record, a brighter rim,
    edges fading out. Hollow centre."""
    size = int(math.ceil(R * 2)) + 2
    c = size / 2
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([c - R, c - R, c + R, c + R], fill=(14, 15, 16, 215))   # one smooth translucent body
    grooves = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cols, rows = int(size / tw) + 1, int(size / th) + 1
    shades = ((92, 98, 102), (58, 64, 68), (76, 82, 86))
    for gy in range(rows):
        for gx in range(cols):
            px, py = (gx + 0.5) * tw, (gy + 0.5) * th
            dx, dy = px - c, py - c
            r = math.hypot(dx, dy)
            if r > R or r < hole:
                continue
            a = math.atan2(dy, dx)
            sheen = math.cos(2 * ((a - phase) % (2 * math.pi)))
            if r > R - th * 0.8:
                glyph, fg = groove_glyph(a, False), (118, 124, 128)          # rim
            elif sheen > 0.93:
                glyph, fg = groove_glyph(a, True), (196, 202, 206)
            elif sheen > 0.78:
                glyph, fg = groove_glyph(a, False), (142, 148, 152)
            else:
                ring = int(r / min(tw, th) * 0.9) % 3
                glyph, fg = groove_glyph(a, False), shades[ring]
            edge = max(0.0, (r - (R - th)) / th)
            alpha = 235 if edge < 0.34 else 170 if edge < 0.67 else 110
            grooves.alpha_composite(glyph_tile(glyph, fg, alpha, int(math.ceil(tw)), int(math.ceil(th))),
                                    (int(gx * tw), int(gy * th)))
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([c - R, c - R, c + R, c + R], fill=255)
    md.ellipse([c - hole, c - hole, c + hole, c + hole], fill=0)
    grooves.putalpha(ImageChops.multiply(grooves.getchannel("A"), mask))
    img.alpha_composite(grooves)
    hole_mask = Image.new("L", (size, size), 255)
    ImageDraw.Draw(hole_mask).ellipse([c - hole, c - hole, c + hole, c + hole], fill=0)
    img.putalpha(ImageChops.multiply(img.getchannel("A"), hole_mask))   # hollow centre (user's choice)
    return img.filter(ImageFilter.GaussianBlur(0.3))


def paint_record(L: Layout, box, cw: float, ch: float, disc_img, lift: float) -> Image.Image:
    """The record's box: the cached disc phase plus the tonearm (0 = on the record, 1 = lifted to rest)."""
    c0, r0, cols, rows = box
    ox, oy = c0 * cw, r0 * ch
    img = Image.new("RGBA", (int(round(cols * cw)), int(round(rows * ch))), (0, 0, 0, 0))
    if disc_img is not None:
        img.alpha_composite(disc_img, (int(L.cx - ox - disc_img.width / 2), int(L.cy - oy - disc_img.height / 2)))
    px, py = L.pivot[0] - ox, L.pivot[1] - oy
    ex = L.on[0] + (L.rest[0] - L.on[0]) * lift - ox
    ey = L.on[1] + (L.rest[1] - L.on[1]) * lift - oy
    k = max(2, int(cw * 0.45))             # one "pixel" of the stepped arm
    coarse = Image.new("RGBA", (img.width // k + 1, img.height // k + 1), (0, 0, 0, 0))
    ImageDraw.Draw(coarse).line([(px / k, py / k), (ex / k, ey / k)], fill=(200, 204, 206, 255), width=1)
    img.alpha_composite(coarse.resize((coarse.width * k, coarse.height * k), Image.NEAREST).crop((0, 0, img.width, img.height)))
    d = ImageDraw.Draw(img)
    f = font(ch * 0.86)
    d.text((px - cw / 2, py - ch / 2), "◉", font=f, fill=(157, 162, 165, 255))
    d.text((ex - cw / 2, ey - ch / 2), "■", font=f, fill=(200, 204, 206, 255))
    return img


# ---------------- 3b. the big title (its own layer, so a long one can scroll) ----------------
TITLE_GAP = 3.0                 # cells between the end of a scrolling title and its next pass


def title_font(ch: float):
    return font(ch * 2.1, bold=True)


def title_box(L: Layout, cw: float, ch: float):
    """The cell box (col, row, cols, rows) of the title band: the sleeve's left edge to the record's right."""
    c0, r0 = int(L.sx // cw), int(L.text_y // ch)
    c1, r1 = int(math.ceil(L.right / cw)), int(math.ceil((L.text_y + ch * 2.45) / ch))
    return c0, r0, c1 - c0, r1 - r0


def title_overflows(L: Layout, title: str, ch: float) -> bool:
    return title_font(ch).getlength(title) > (L.right - L.sx)


def paint_title(L: Layout, box, cw: float, ch: float, title: str, hi: str, offset: float = 0.0) -> Image.Image:
    """The title band. offset (px) slides a long title left; it loops with a gap and fades at the right edge."""
    c0, r0, cols, rows = box
    img = Image.new("RGBA", (int(round(cols * cw)), int(round(rows * ch))), (0, 0, 0, 0))
    f = title_font(ch)
    x0, y0 = L.sx - c0 * cw, L.text_y - r0 * ch
    band = L.right - L.sx
    text = title or "Nothing playing"
    d = ImageDraw.Draw(img)
    if f.getlength(text) <= band:
        d.text((x0, y0), text, font=f, fill=hi)
        return img
    loop = f.getlength(text) + TITLE_GAP * cw * 2
    for k in (0, 1):
        d.text((x0 - offset + k * loop, y0), text, font=f, fill=hi)
    # clip to the band, with a soft fade where the text runs out of it
    mask = Image.new("L", img.size, 0)
    fade = int(cw * 3)
    md = ImageDraw.Draw(mask)
    md.rectangle([x0, 0, x0 + band - fade, img.height], fill=255)
    for i in range(fade):
        md.line([(x0 + band - fade + i, 0), (x0 + band - fade + i, img.height)], fill=int(255 * (1 - i / fade)))
        if offset > 0:                                   # mid-scroll: soft on the left too
            md.line([(x0 + i, 0), (x0 + i, img.height)], fill=int(255 * i / fade))
    img.putalpha(ImageChops.multiply(img.getchannel("A"), mask))
    return img


# ---------------- 3. sleeve + type ----------------
def fit(text: str, f, width: float) -> str:
    """Shorten with … to fit a pixel width."""
    if f.getlength(text) <= width:
        return text
    while text and f.getlength(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


def paint_static(L: Layout, cw: float, ch: float, cover, title: str, sub: str, up_next, colors) -> Image.Image:
    """Full-resolution layer: the cover as the sleeve (flat), the big title and artist, and up next."""
    hi, soft, acc, dim = colors
    img = Image.new("RGBA", (L.FW, L.FH), (0, 0, 0, 0))
    side = int(L.S)
    if cover is not None and side > 8:                 # nothing playing: no placeholder sleeve
        img.paste(cover.resize((side, side), Image.LANCZOS), (int(L.sx), int(L.sy)))
    d = ImageDraw.Draw(img)
    # stacked, left-aligned with the sleeve, as wide as the sleeve + record:
    #   Title (big) / artist · album / UP NEXT / the next song (small)
    w = L.right - L.sx
    small = font(ch * 0.95)
    y = L.text_y                      # the big title itself is its own layer (paint_title): long ones scroll
    d.text((L.sx, y + ch * 2.5), fit(sub, small, w), font=small, fill=soft)
    if up_next:
        label, line = font(ch * 0.65, bold=True), font(ch * 0.72)
        top = y + ch * 4.0
        d.text((L.sx, top), "UP NEXT", font=label, fill=acc)
        for name, artist in up_next[:1]:
            yy = top + ch * 0.95
            n_txt = fit(name, line, w * 0.7)                    # the song name gets the room first
            d.text((L.sx, yy), n_txt, font=line, fill=hi)
            if artist:                                           # Title • Artist
                dot_x = L.sx + line.getlength(n_txt + " ")
                d.text((dot_x, yy), "•", font=line, fill=acc)
                a_x = dot_x + line.getlength("• ")
                d.text((a_x, yy), fit(artist, line, L.sx + w - a_x), font=line, fill=dim)
    return img
