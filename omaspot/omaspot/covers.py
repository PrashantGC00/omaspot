"""Cover images: download once, cache on disk (~/.cache/omaspot/covers) and in memory, extract 4-colour palettes.

One shared HTTP client serves every download (a grid of 30 tiles used to open 30 TLS connections), and
concurrent requests for the same URL share one download.
"""
import asyncio
import hashlib
import json
from collections import OrderedDict
from pathlib import Path

import httpx
from PIL import Image

from . import config

COVERS = config.CACHE / "covers"
DEFAULT_PALETTE = [(201, 139, 75), (122, 78, 45), (47, 93, 98), (217, 199, 163)]
MEM_ITEMS = 160                      # decoded images kept in memory (sidebar + grid + queue covers)

_client: httpx.AsyncClient | None = None
_inflight: dict[str, asyncio.Future] = {}
_mem: "OrderedDict[str, Image.Image]" = OrderedDict()


def mix(a, b, t: float):
    """t=0 → a, t=1 → b (RGB tuples)"""
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), int(a[2] + (b[2] - a[2]) * t))


def to_hex(c) -> str:
    return "#%02x%02x%02x" % tuple(c)


def pick_cover_url(item: dict) -> str | None:
    """Best cover URL of a track, in either Soloist's shape (size names) or the Web API's (widths)."""
    imgs = ((item or {}).get("album") or {}).get("images") or []
    by_size = {i.get("size"): i.get("url") for i in imgs if i.get("size")}
    for s in ("large", "default", "xlarge", "small"):
        if by_size.get(s):
            return by_size[s]
    sized = sorted((i for i in imgs if i.get("url")), key=lambda i: abs((i.get("width") or 300) - 300))
    return sized[0]["url"] if sized else None


def cover_path(url: str) -> Path:
    return COVERS / f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.jpg"


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=8))
    return _client


async def close():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _download(url: str, path: Path):
    r = await _http().get(url)
    r.raise_for_status()
    COVERS.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(r.content)
    tmp.replace(path)                 # never leave a half-written cover behind


async def _fetch(url: str) -> Path:
    path = cover_path(url)
    if path.exists():
        return path
    fut = _inflight.get(url)
    if fut is None:
        fut = _inflight[url] = asyncio.ensure_future(_download(url, path))
        fut.add_done_callback(lambda _f: _inflight.pop(url, None))
    await asyncio.shield(fut)
    return path


async def load_thumb(url: str) -> Image.Image:
    """A cover as a PIL RGB image (no palette): grid tiles, sidebar and queue thumbnails."""
    img = _mem.get(url)
    if img is not None:
        _mem.move_to_end(url)
        return img
    path = await _fetch(url)
    img = await asyncio.to_thread(lambda: Image.open(path).convert("RGB"))
    _mem[url] = img
    while len(_mem) > MEM_ITEMS:
        _mem.popitem(last=False)
    return img


async def load_cover(url: str):
    """(PIL RGB image, 4-colour palette) for the playing song or a page header."""
    img = await load_thumb(url)
    pal_path = cover_path(url).with_suffix(".json")
    try:
        return img, [tuple(p) for p in json.loads(pal_path.read_text())]
    except (OSError, ValueError):
        palette = await asyncio.to_thread(extract_palette, img)
        pal_path.write_text(json.dumps(palette))
        return img, palette


def extract_palette(img: Image.Image):
    """4 dominant colours, most common first, nudged away from near-black so bars stay visible."""
    small = img.resize((64, 64))
    q = small.quantize(colors=4, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[:12]
    out = []
    for _, idx in sorted(q.getcolors(), reverse=True):
        c = tuple(pal[idx * 3: idx * 3 + 3])
        if sum(c) < 120:
            c = mix(c, (200, 200, 200), 0.35)
        out.append(c)
    while len(out) < 4:
        out.append(DEFAULT_PALETTE[len(out)])
    return out[:4]
