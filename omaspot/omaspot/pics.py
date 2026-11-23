"""Cover images drawn with the kitty graphics protocol over Textual widgets."""
import itertools

from PIL import ImageOps
from textual.geometry import Region

from . import kittygfx


def parts_outside(vis: Region, occ: Region | None) -> list[Region]:
    """vis minus occ, as up to 4 rectangles (above, below, left, right of it): what stays visible of an image
    when a menu sits on top of it. Kitty draws images over cell backgrounds, so the part under the menu is
    left out instead of hiding the whole image."""
    if occ is None or not vis.overlaps(occ):
        return [vis]
    i = vis.intersection(occ)
    parts = [Region(vis.x, vis.y, vis.width, i.y - vis.y),
             Region(vis.x, i.bottom, vis.width, vis.bottom - i.bottom),
             Region(vis.x, i.y, i.x - vis.x, i.height),
             Region(i.right, i.y, vis.right - i.right, i.height)]
    return [r for r in parts if r.width > 0 and r.height > 0]


def put_parts(write, image_id: int, region: Region, px, parts, z: int, prev=None):
    """Show image_id (px = its pixel size, drawn over region) as one placement per part, each cropped to
    its piece. prev: the parts shown before; if their count changes, old placements are removed first."""
    if prev is not None and len(prev) != len(parts):
        kittygfx.hide(write, image_id)
    sx, sy = px[0] / region.width, px[1] / region.height
    for n, p in enumerate(parts, 1):
        src = None
        if p != region:
            src = (int((p.x - region.x) * sx), int((p.y - region.y) * sy), max(1, int(p.width * sx)), max(1, int(p.height * sy)))
        kittygfx.put(write, image_id, p.x, p.y, p.width, p.height, z=z, src=src, pid=n)


class Pics:
    """Cover images drawn with kitty graphics over widgets, under their text.

    Each slot pairs a widget with a PIL image. An image is sent once at its widget's pixel size, then only
    re-placed when the widget moves (scrolling) and hidden when the widget is clipped, hidden, covered by a
    menu, or omaspot is in focus mode."""

    def __init__(self, app):
        self.app = app
        self.slots: dict[str, dict] = {}
        self._ids = itertools.count(5000)
        self.occlude: Region | None = None      # a menu or modal: images under it are hidden
        self.dirty = False                      # placements are out of date; the next frame re-places them

    def request(self):
        """Re-place images with the next frame (inside its synchronized update, see Omaspot._end_update), or
        right after the refresh if no frame gets drawn."""
        self.dirty = True
        self.app.call_after_refresh(self._fallback)

    def _fallback(self):
        if self.dirty:
            self.sync()

    def _write(self):
        drv = getattr(self.app, "_driver", None)
        return drv.write if drv is not None else None

    def set(self, key: str, widget, img, z: int = -1, sig=None):
        """img: a PIL image (scaled to fit the widget), or a function (width, height) -> image drawn at the widget's
        exact pixel size (big type). sig: what the function draws; the same sig over the same widget is a no-op."""
        old = self.slots.get(key)
        if old and old["widget"] is widget and (old["img"] is img or (sig is not None and old.get("sig") == sig)):
            return
        if old:
            self._free(old)
        if img is None:
            self.slots.pop(key, None)
            return
        self.slots[key] = {"widget": widget, "img": img, "id": next(self._ids), "sent": None, "at": None, "z": z, "sig": sig}

    def drop(self, prefix: str):
        for key in [k for k in self.slots if k.startswith(prefix)]:
            self._free(self.slots.pop(key))

    def _free(self, slot):
        w = self._write()
        if w and slot["sent"]:
            kittygfx.delete(w, slot["id"])

    def sync(self):
        self.dirty = False
        if not kittygfx.supported():
            return
        write, cell = self._write(), kittygfx.cell_size()
        if write is None or not cell:
            return
        hidden_all = getattr(self.app, "in_focus", False)
        # Only what's on screen: asking Textual for an off-screen widget's place (screen.find_widget) rebuilds the
        # layout of every widget, and each scroll step invalidates it again (~40 ms a step on Home / Preferences).
        maps: dict = {}
        for slot in self.slots.values():
            w, region, vis = slot["widget"], None, None
            if not hidden_all and w.is_attached:
                try:
                    scr = w.screen
                    if scr not in maps:
                        maps[scr] = scr._compositor.visible_widgets
                    hit = maps[scr].get(w)
                    if hit is not None:
                        r, v = hit[0], hit[0].intersection(hit[1])
                        if r.width and r.height and v.width and v.height:
                            region, vis = r, v
                except Exception:
                    region = None
            parts = parts_outside(vis, self.occlude) if region is not None else []
            if not parts:
                region = None
            if region is None:
                if slot["at"] is not None:
                    kittygfx.hide(write, slot["id"])
                    slot["at"] = None
                continue
            size = (region.width, region.height)
            if slot["sent"] != size:
                px = (max(1, int(region.width * cell[0])), max(1, int(region.height * cell[1])))
                im = slot["img"](px) if callable(slot["img"]) else ImageOps.fit(slot["img"], px)
                kittygfx.transmit(write, im, slot["id"])
                slot["sent"], slot["at"], slot["px"] = size, None, px
            at = tuple(parts)
            if slot["at"] != at:
                put_parts(write, slot["id"], region, slot["px"], parts, slot["z"], slot["at"])
                slot["at"] = at
