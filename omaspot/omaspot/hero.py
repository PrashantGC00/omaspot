"""Normal mode's big type, drawn as kitty images (like focus mode's scene.py): pure drawing, no UI.

  wash()          a flat wash of a cover's colours, fading out downwards (behind page headers and Home's top)
  title_block()   a page header's kind label, big bold title (shrinks to fit) and subtitle, bottom-aligned
  greeting()      Home's "Good afternoon" (over a wash)
  section()       a Home section title

Each takes the pixel size of the widget it's drawn over, so kitty shows it 1:1. Flat: no shadow or glow.
"""
from PIL import Image, ImageDraw, ImageFilter

from .scene import fit, font
from .theme import rgb


def _alpha(c: str, a: int):
    return rgb(c) + (a,)


def wash(px, palette, strength: int = 95) -> Image.Image:
    """Album colours left to right (palette[0] → palette[2]), fading to nothing at the bottom and the sides."""
    w, h = px
    g = Image.linear_gradient("L")
    across = g.rotate(90).resize((w, h))                       # 0 at the left → 255 at the right
    img = Image.composite(Image.new("RGB", (w, h), tuple(palette[2])), Image.new("RGB", (w, h), tuple(palette[0])), across)
    down = g.transpose(Image.Transpose.FLIP_TOP_BOTTOM).resize((w, h)).point(lambda v: v * strength // 255)
    side = max(2, int(w * 0.04))                               # soft left/right ends instead of a hard box
    edge = Image.new("L", (w, h), 0)
    ImageDraw.Draw(edge).rectangle([side, 0, w - 1 - side, h], fill=255)
    edge = edge.filter(ImageFilter.GaussianBlur(side * 0.6))
    alpha = Image.composite(down, Image.new("L", (w, h), 0), edge)
    img = img.convert("RGBA")
    img.putalpha(alpha)
    return img


def title_block(px, ch: float, kind: str, title: str, sub: str, colors, underline_sub: bool = False) -> Image.Image:
    """Bottom-aligned: KIND (accent, small caps), the title as large as fits (2.6 → 1.4 rows), the subtitle."""
    hi, soft, acc = colors
    w, h = px
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    small, label = font(ch * 0.9), font(ch * 0.8, bold=True)
    size = ch * 2.6
    while size > ch * 1.4 and font(size, bold=True).getlength(title) > w:
        size -= ch * 0.1
    big = font(size, bold=True)
    y_sub = h - ch * 1.05
    y_title = y_sub - size * 1.2
    y_kind = y_title - ch * 0.95
    d.text((0, y_kind), kind.upper(), font=label, fill=_alpha(acc, 255))
    d.text((0, y_title), fit(title, big, w), font=big, fill=_alpha(hi, 255))
    sub_txt = fit(sub, small, w)
    d.text((0, y_sub), sub_txt, font=small, fill=_alpha(soft, 255))
    if underline_sub and sub_txt:
        uy = y_sub + ch * 0.95
        d.line([(0, uy), (small.getlength(sub_txt), uy)], fill=_alpha(soft, 200), width=1)
    return img


def greeting(px, ch: float, text: str, palette, hi: str, with_wash: bool = True) -> Image.Image:
    img = wash(px, palette, strength=85) if with_wash else Image.new("RGBA", px, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    big = font(ch * 2.4, bold=True)
    d.text((0, px[1] - ch * 2.9), fit(text, big, px[0]), font=big, fill=_alpha(hi, 255))
    return img


def section(px, ch: float, text: str, hi: str) -> Image.Image:
    img = Image.new("RGBA", px, (0, 0, 0, 0))
    f = font(ch * 1.3, bold=True)
    ImageDraw.Draw(img).text((0, px[1] - ch * 1.55), fit(text, f, px[0]), font=f, fill=_alpha(hi, 255))
    return img
