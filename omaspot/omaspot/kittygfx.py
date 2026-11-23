"""Minimal kitty graphics protocol output for Textual: place one image over a cell region.

The image is drawn with z = -1: under text but over cell backgrounds, so toasts and other text stay on top.
Frames go through a temp file in /dev/shm (t=t), which kitty deletes after reading.
"""
import base64
import fcntl
import itertools
import os
import struct
import sys
import termios

IMAGE_ID = 4242        # focus mode: record + tonearm (RGBA, just the record's box)
BARS_ID = 4243         # focus mode: album-colour backdrop + visualizer (RGBA)
HERO_ID = 4244         # normal mode: album/playlist cover in the header
MINI_ID = 4245         # normal mode: song cover in the player bar
STATIC_ID = 4246       # focus mode: sleeve, big title, up next (re-sent only when they change)
TITLE_ID = 4248        # Turntable: the big title (scrolls when it is too long)
_seq = itertools.count()


def supported() -> bool:
    return os.environ.get("TERM") == "xterm-kitty" or "KITTY_WINDOW_ID" in os.environ


def cell_size():
    """(width, height) of one cell in pixels, from the tty; None if the terminal doesn't say."""
    for stream in (sys.__stdout__, sys.__stdin__):
        try:
            rows, cols, xpix, ypix = struct.unpack("HHHH", fcntl.ioctl(stream.fileno(), termios.TIOCGWINSZ, b"\0" * 8))
            if cols and rows and xpix and ypix:
                return xpix / cols, ypix / rows
        except (OSError, ValueError, AttributeError):
            continue
    return None


def place(write, img, x: int, y: int, cols: int, rows: int, image_id: int = IMAGE_ID, z: int = -1):
    """Send an RGB or RGBA PIL image, scaled by kitty to cols x rows cells at (x, y) (0-based screen cells).
    Lower z draws further back; both layers stay under text."""
    fmt = 32 if img.mode == "RGBA" else 24
    path = f"/dev/shm/tty-graphics-protocol-omaspot-{os.getpid()}-{image_id}-{next(_seq) % 4}.rgb"
    with open(path, "wb") as f:
        f.write(img.tobytes())
    payload = base64.standard_b64encode(path.encode()).decode()
    w, h = img.size
    write(f"\x1b7\x1b[{y + 1};{x + 1}H"
          f"\x1b_Ga=T,f={fmt},t=t,s={w},v={h},i={image_id},p=1,c={cols},r={rows},z={z},C=1,q=2;{payload}\x1b\\"
          f"\x1b8")


def clear(write):
    for image_id in (IMAGE_ID, BARS_ID, STATIC_ID, TITLE_ID):
        write(f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\")


def delete(write, image_id: int):
    write(f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\")


def transmit(write, img, image_id: int):
    """Send an image once (a=t) without showing it; put() then places it, as often as needed."""
    fmt = 32 if img.mode == "RGBA" else 24
    path = f"/dev/shm/tty-graphics-protocol-omaspot-{os.getpid()}-{image_id}-{next(_seq) % 4}.rgb"
    with open(path, "wb") as f:
        f.write(img.tobytes())
    payload = base64.standard_b64encode(path.encode()).decode()
    w, h = img.size
    write(f"\x1b_Ga=t,f={fmt},t=t,s={w},v={h},i={image_id},q=2;{payload}\x1b\\")


def put(write, image_id: int, x: int, y: int, cols: int, rows: int, z: int = -1, src=None, pid: int = 1):
    """Show (or move) an image sent with transmit() over cols x rows cells at (x, y).
    src = (x, y, w, h) in image pixels shows only that part (a cover partly scrolled out of view).
    pid: the placement id; one image can be shown in several pieces (around a menu), one placement each."""
    crop = f",x={src[0]},y={src[1]},w={src[2]},h={src[3]}" if src else ""
    write(f"\x1b7\x1b[{y + 1};{x + 1}H\x1b_Ga=p,i={image_id},p={pid},c={cols},r={rows}{crop},z={z},C=1,q=2\x1b\\\x1b8")


def hide(write, image_id: int):
    """Remove an image's placement but keep its data, so put() can show it again."""
    write(f"\x1b_Ga=d,d=i,i={image_id},q=2\x1b\\")
