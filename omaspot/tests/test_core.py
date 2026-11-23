"""Unit tests for the parts of omaspot that don't need a terminal or Spotify.

Run: cd omaspot && /usr/bin/python3 -m unittest discover -s tests
"""
import asyncio
import math
import os
import struct
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from omaspot import soloist, util  # noqa: E402
from omaspot.covers import extract_palette, pick_cover_url  # noqa: E402
from omaspot.pane import liked_cover, sort_tracks  # noqa: E402
from omaspot.state import PlayerState, progress_from, track_from_soloist  # noqa: E402

# shaped like a real `soloist ctl now --json` / WebSocket playback_state
SOLOIST_STATE = {
    "type": "playback_state", "status": "playing", "is_active": True, "volume": 55,
    "position": {"position_ms": 10_000, "timestamp_ms": 1_000_000, "speed": 1},
    "options": {"shuffle": True, "repeat": "context"},
    "item": {
        "uri": "spotify:track:T1", "entity_type": "track",
        "decorations": {
            "identity": {"name": "Song"},
            "visual_identity": {"cover": [{"url": "https://i/small", "size": "small"},
                                          {"url": "https://i/large", "size": "large"}]},
            "parent": {"entity": {"uri": "spotify:album:A1", "decorations": {"identity": {"name": "Album"}}}},
            "creators": [{"entity": {"uri": "spotify:artist:R1", "decorations": {"identity": {"name": "Artist"}}}}],
            "playback": {"duration_ms": 200_000},
        },
    },
}


class UtilTests(unittest.TestCase):
    def test_fmt_hours(self):
        self.assertEqual(util.fmt(0), "0:00")
        self.assertEqual(util.fmt(61_500), "1:01")
        self.assertEqual(util.hours(59 * 60_000), "59 min")
        self.assertEqual(util.hours(130 * 60_000), "2 hr 10 min")

    def test_ago(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(util.ago(None), "")
        self.assertEqual(util.ago("garbage"), "")
        self.assertEqual(util.ago(now.isoformat()), "today")
        self.assertEqual(util.ago("2001-02-03T00:00:00Z"), "3 Feb 2001")

    def test_links_and_ids(self):
        self.assertEqual(util.web_link("spotify:track:X"), "https://open.spotify.com/track/X")
        self.assertEqual(util.uri_id("spotify:artist:R1"), "R1")
        self.assertIsNone(util.uri_id("nope"))

    def test_pick_image(self):
        imgs = [{"url": "big", "width": 640}, {"url": "mid", "width": 300}, {"url": "small", "width": 64}]
        self.assertEqual(util.pick_image(imgs), "mid")
        self.assertEqual(util.pick_image(imgs, 60), "small")
        self.assertIsNone(util.pick_image([]))


class StateTests(unittest.TestCase):
    def test_from_soloist(self):
        st = PlayerState.from_soloist(SOLOIST_STATE, wall_ms=1_002_000)
        self.assertEqual(st.source, "soloist")
        self.assertTrue(st.playing and st.shuffle)
        self.assertEqual(st.repeat, "context")
        self.assertEqual(st.progress_ms, 12_000)          # 10 s + 2 s since the timestamp
        self.assertEqual(st.duration, 200_000)
        self.assertEqual(st.volume, 55)
        self.assertEqual(st.item["artists"][0], {"name": "Artist", "uri": "spotify:artist:R1", "id": "R1"})
        self.assertEqual(st.item["album"]["id"], "A1")
        self.assertEqual(pick_cover_url(st.item), "https://i/large")

    def test_inactive_is_none(self):
        self.assertIsNone(PlayerState.from_soloist({**SOLOIST_STATE, "is_active": False}))

    def test_from_web(self):
        st = PlayerState.from_web({"is_playing": False, "progress_ms": 5, "shuffle_state": False,
                                   "repeat_state": "track", "device": {"name": "Phone"}, "item": {"uri": "u"}})
        self.assertEqual((st.source, st.playing, st.repeat, st.uri), ("web", False, "track", "u"))
        self.assertEqual(PlayerState.from_web(None).source, "none")

    def test_position_clock(self):
        st = PlayerState(item={"duration_ms": 10_000}, playing=True, progress_ms=1_000, at=100.0)
        self.assertEqual(st.position(now=101.5), 2_500)
        self.assertEqual(st.position(now=200.0), 10_000)              # clamped to the song
        self.assertEqual(PlayerState(playing=False, progress_ms=7, at=0).position(now=50), 7)

    def test_with_reanchors(self):
        st = PlayerState(item={"duration_ms": 100_000}, playing=True, progress_ms=0, at=time.monotonic() - 5)
        paused = st.with_(playing=False)
        self.assertFalse(paused.playing)
        self.assertAlmostEqual(paused.progress_ms, 5_000, delta=200)   # froze where it was
        self.assertEqual(st.with_(shuffle=True).at, st.at)            # unrelated change keeps the clock

    def test_progress_paused(self):
        self.assertEqual(progress_from({"position_ms": 3, "timestamp_ms": 1, "speed": 0}, False, 999_999), 3)

    def test_track_from_soloist_missing_bits(self):
        tr = track_from_soloist({"uri": "spotify:track:X"})
        self.assertEqual((tr["name"], tr["artists"], tr["duration_ms"]), ("", [], 0))


class WebSocketTests(unittest.TestCase):
    def _reader(self, data: bytes) -> asyncio.StreamReader:
        r = asyncio.StreamReader()
        r.feed_data(data)
        r.feed_eof()
        return r

    @staticmethod
    def server_frame(op: int, payload: bytes, fin: bool = True) -> bytes:
        n = len(payload)
        head = bytes([(0x80 if fin else 0) | op])
        head += bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536 else bytes([127]) + struct.pack(">Q", n)
        return head + payload

    def test_client_frame_is_masked(self):
        payload = os.urandom(300)
        raw = soloist.frame(0x1, payload)
        self.assertEqual(raw[0], 0x81)
        self.assertEqual(raw[1], 0x80 | 126)

        async def go():
            return await soloist.read_frame(self._reader(raw))
        fin, op, data = asyncio.run(go())
        self.assertEqual((fin, op, data), (True, 1, payload))

    def test_messages_reassemble_and_ping(self):
        class W:
            sent = []

            def write(self, b):
                self.sent.append(b)
        big = b'{"type":"x","pad":"' + b"a" * 70_000 + b'"}'
        stream = (self.server_frame(0x9, b"hi") + self.server_frame(0x1, b'{"type":', fin=False)
                  + self.server_frame(0x0, b'"a"}') + self.server_frame(0x1, big) + self.server_frame(0x8, b""))
        w = W()

        async def go():
            return [m async for m in soloist.messages(self._reader(stream), w)]
        msgs = asyncio.run(go())
        self.assertEqual(msgs[0], '{"type":"a"}')
        self.assertEqual(len(msgs[1]), len(big))
        self.assertEqual(len(w.sent), 1)                      # answered the ping with a pong
        self.assertEqual(w.sent[0][0], 0x8A)


class PaneTests(unittest.TestCase):
    TRACKS = [
        {"_i": 0, "name": "b", "artists": [{"name": "Z"}], "album": {"name": "y"}, "_added": "2026-01-02", "duration_ms": 3},
        {"_i": 1, "name": "A", "artists": [{"name": "x"}], "album": {"name": "Z"}, "_added": "2026-03-01", "duration_ms": 1},
        {"_i": 2, "name": "c", "artists": [{"name": "m"}], "album": {"name": "a"}, "_added": "2025-12-31", "duration_ms": 2},
    ]

    def names(self, col, desc=False):
        return [t["name"] for t in sort_tracks(self.TRACKS, col, desc)]

    def test_sorts(self):
        self.assertEqual(self.names("Title"), ["A", "b", "c"])          # case-insensitive
        self.assertEqual(self.names("Artist"), ["c", "A", "b"])
        self.assertEqual(self.names("Album"), ["c", "b", "A"])
        self.assertEqual(self.names("Added", True), ["A", "b", "c"])
        self.assertEqual(self.names("Len"), ["A", "c", "b"])
        self.assertEqual(self.names("#"), ["b", "A", "c"])

    def test_liked_cover_gradient(self):
        img = liked_cover("#ff0000", "#000000", "#ffffff")
        tl, br, tr, bl = (img.getpixel(p) for p in ((2, 2), (157, 157), (157, 2), (2, 157)))
        self.assertGreater(tl[0], 240)                  # accent at the top-left
        self.assertLess(br[0], 90)                      # 30% mix towards the background at the bottom-right
        self.assertEqual(tr, bl)                        # diagonal, symmetric


class CoverTests(unittest.TestCase):
    def test_palette(self):
        img = Image.new("RGB", (64, 64), (250, 0, 0))
        img.paste((0, 0, 0), (0, 0, 32, 64))
        pal = extract_palette(img)
        self.assertEqual(len(pal), 4)
        self.assertTrue(all(sum(c) >= 120 or c == (201, 139, 75) for c in pal[:2]))   # no near-black bars

    def test_pick_cover_web_shape(self):
        item = {"album": {"images": [{"url": "640", "width": 640}, {"url": "300", "width": 300}]}}
        self.assertEqual(pick_cover_url(item), "300")


if __name__ == "__main__":
    unittest.main()


class SceneTests(unittest.TestCase):
    def test_layout_fits(self):
        from omaspot import scene
        for FW, FH in ((1224, 589), (1800, 900), (700, 600), (1224, 300)):
            L = scene.layout(FW, FH)
            self.assertGreaterEqual(L.sx, 0)
            self.assertLessEqual(L.right, FW)
            self.assertLessEqual(L.sy + L.S, L.band_y - L.band_h)        # sleeve above the bars
            self.assertLess(L.text_y + L.S * 0 + 19 * 3.5, FH + 1)        # title block fits (19 px cells)
            self.assertGreater(L.cx - L.R, L.sx)                         # record starts behind the sleeve...
            self.assertGreater(L.cx + L.R, L.sx + L.S)                   # ...and comes out of it

    def test_groove_strokes_follow_the_circle(self):
        from omaspot.scene import groove_glyph
        self.assertEqual(groove_glyph(math.pi / 2, False), "─")         # bottom of the record: horizontal
        self.assertEqual(groove_glyph(0.0, False), "│")                 # right side: vertical
        self.assertEqual(groove_glyph(0.0, True), "┃")
        self.assertEqual(groove_glyph(math.pi / 4, False), "╱")         # bottom-right
        self.assertEqual(groove_glyph(-math.pi / 4, False), "╲")        # top-right

    def test_record_box_holds_the_arm(self):
        from omaspot import scene
        L = scene.layout(1224, 589)
        c0, r0, cols, rows = scene.record_box(L, 9.0, 19.0)
        for x, y in (L.pivot, L.on, L.rest, (L.cx - L.R, L.cy - L.R), (L.cx + L.R, L.cy + L.R)):
            self.assertTrue(c0 * 9 <= x <= (c0 + cols) * 9 and r0 * 19 <= y <= (r0 + rows) * 19, (x, y))


class MprisTests(unittest.TestCase):
    def test_metadata_and_inactive(self):
        from omaspot.mpris import NO_TRACK, metadata, track_path
        st = PlayerState.from_soloist({**SOLOIST_STATE, "is_active": False}, wall_ms=1_002_000, require_active=False)
        self.assertFalse(st.playing)                                   # inactive reads as paused, not gone
        md = metadata(st)
        self.assertEqual(md["xesam:title"].value, "Song")
        self.assertEqual(md["xesam:artist"].value, ["Artist"])
        self.assertEqual(md["mpris:length"].value, 200_000_000)        # microseconds
        self.assertEqual(md["mpris:artUrl"].value, "https://i/large")
        self.assertEqual(md["mpris:trackid"].value, track_path("spotify:track:T1"))
        self.assertEqual(metadata(None)["mpris:trackid"].value, NO_TRACK)
        self.assertIsNone(PlayerState.from_soloist({"is_active": False}, require_active=False))


class NormalModeTests(unittest.TestCase):
    def test_jump_back_in_groups_by_context(self):
        from omaspot.views import jump_back_in
        alb = lambda u: {"uri": u, "name": u, "images": [], "artists": []}
        recent = [
            {"track": {"uri": "t1", "album": alb("A")}, "context": {"type": "album", "uri": "A"}},
            {"track": {"uri": "t2", "album": alb("A")}, "context": {"type": "album", "uri": "A"}},
            {"track": {"uri": "t3", "album": alb("B")}, "context": {"type": "playlist", "uri": "P"}},
            {"track": {"uri": "t4", "album": alb("C")}, "context": {"type": "playlist", "uri": "someone-elses"}},
        ]
        tiles = jump_back_in(recent, [{"uri": "P", "name": "Mine", "images": [], "owner": {}}])
        self.assertEqual([(t["kind"], t["uri"]) for t in tiles], [("album", "A"), ("playlist", "P"), ("album", "C")])

    def test_tile_playing(self):
        from omaspot.pane import MainPane
        st = PlayerState(item={"uri": "t1", "album": {"uri": "A"}}, context="P")
        self.assertTrue(MainPane.tile_playing({"kind": "playlist", "uri": "P"}, st))
        self.assertTrue(MainPane.tile_playing({"kind": "album", "uri": "A"}, st))     # its album, from any context
        self.assertTrue(MainPane.tile_playing({"kind": "track", "uri": "t1"}, st))
        self.assertFalse(MainPane.tile_playing({"kind": "artist", "uri": "R"}, st))
        self.assertFalse(MainPane.tile_playing({"kind": "playlist", "uri": "P"}, PlayerState()))

    def test_playlist_sub(self):
        from omaspot.components import playlist_sub
        self.assertEqual(playlist_sub({"tracks": {"total": 26}}), "26 songs")
        self.assertEqual(playlist_sub({"items": {"total": 1}}), "1 song")
        self.assertEqual(playlist_sub({}), "Playlist")

    def test_hero_images(self):
        from omaspot import hero
        pal = [(200, 100, 50), (0, 0, 0), (50, 100, 200), (255, 255, 255)]
        w = hero.wash((400, 200), pal)
        self.assertEqual(w.size, (400, 200))
        self.assertGreater(w.getpixel((200, 2))[3], w.getpixel((200, 190))[3])      # fades out downwards
        self.assertLess(w.getpixel((0, 2))[3], w.getpixel((200, 2))[3] // 5)         # soft side edge
        t = hero.title_block((600, 160), 19.0, "playlist", "A very long title " * 6, "26 songs", ("#ffffff", "#aaaaaa", "#ff8800"))
        self.assertEqual(t.size, (600, 160))
        self.assertIsNotNone(t.getbbox())


class SceneCentringTests(unittest.TestCase):
    def test_sleeve_and_record_are_centred(self):
        from omaspot import scene
        for FW, FH in ((1224, 646), (1224, 589), (1800, 900), (900, 700)):
            ch = 19.0
            L = scene.layout(FW, FH, ch)
            left, right = L.sx, L.cx + L.R
            self.assertAlmostEqual(left, FW - right, delta=1, msg=(FW, FH))             # equal gaps left / right
            top, bottom = L.sy, L.text_y + ch * scene.TEXT_ROWS
            if top > FH * 0.02 + 1:                                                 # not squeezed against the top
                self.assertAlmostEqual(top, FH - bottom, delta=1, msg=(FW, FH))     # equal gaps above / below
            self.assertAlmostEqual(L.cy, L.sy + L.S / 2)                            # record centred on the sleeve


class PulseTests(unittest.TestCase):
    def test_beats_not_loudness(self):
        from omaspot.scene import Pulse
        p, dt = Pulse(), 1 / 30
        steady = [0.8] * 6 + [0.3] * 34
        for _ in range(90):                                  # 3 s of a steady loud bass
            p.step(steady, dt)
        settled = p.value
        self.assertLess(settled, 0.4)                       # loud but steady: no constant flash
        kick = [1.0] * 6 + [0.3] * 34
        p.step(kick, dt)
        p.step(kick, dt)
        self.assertGreater(p.value, settled + 0.15)          # a kick jumps above it
        for _ in range(9):                                   # 0.3 s of silence
            p.step([0.0] * 40, dt)
        self.assertLess(p.value, 0.3)                        # and decays

    def test_pulse_strengthens_the_wash(self):
        from PIL import Image
        from omaspot.scene import pulse_backdrop
        base = Image.new("RGBA", (10, 10), (200, 100, 50, 46))
        self.assertIs(pulse_backdrop(base, 0.0), base)
        self.assertGreater(pulse_backdrop(base, 1.0).getpixel((5, 5))[3], 100)
        self.assertEqual(pulse_backdrop(base, 1.0).getpixel((5, 5))[:3], (200, 100, 50))   # colour unchanged


class PreferencesTests(unittest.TestCase):
    def test_settings_survive_bad_files(self):
        import tempfile
        from omaspot import settings
        with tempfile.TemporaryDirectory() as d:
            settings.PATH, old = Path(d) / "settings.json", settings.PATH
            try:
                self.assertEqual(settings.load(), settings.Settings())                       # no file
                settings.PATH.write_text("{not json")
                self.assertEqual(settings.load(), settings.Settings())                       # broken file
                settings.PATH.write_text('{"opacity": 9, "bg": "red", "unknown": 1}')
                s = settings.load()
                self.assertEqual((s.opacity, s.bg), (1.0, "theme"))
                s.bg, s.opacity = "#ABCDEF", 0.1
                settings.save(s.clean())
                s2 = settings.load()
                self.assertEqual((s2.bg, s2.opacity), ("#abcdef", 0.3))
            finally:
                settings.PATH = old

    def test_window_rewrite(self):
        from omaspot import window
        conf = "include x.conf\nbackground #101315\nbackground_opacity 0.82\ntransparent_background_colors #101315@0.82\nfont_size 13\n"
        out = window.rewrite(conf, window.lines_for("#000000", "#111111", "#222222", 0.9))
        self.assertIn("background #000000\n", out)
        self.assertIn("background_opacity 0.90\n", out)
        self.assertIn("transparent_background_colors #000000@0.90 #111111@0.96 #222222@1.00\n", out)
        self.assertIn("include x.conf\n", out)
        self.assertEqual(out.count("background "), 1)                     # replaced, not appended twice
        self.assertIn("background_opacity 0.50", window.rewrite("font_size 13\n", window.lines_for("#000000", "#111111", "#222222", 0.5)))


class DeviceRecoveryTests(unittest.TestCase):
    """Playing when Omarchy isn't the active Connect device (e.g. after Soloist restarted) must recover."""

    def make(self, fail_first_play: bool):
        from omaspot.api import ApiError, Spotify
        sp = Spotify.__new__(Spotify)                    # no token, no network
        calls = []

        class Local:
            active = False
            async def _run(self, *args, out=False):
                calls.append(("soloist", args)); self.active = True; return 0, b""
            async def ctl(self, *args):
                calls.append(("ctl", args)); return self.active
        sp.local, sp.device_id = Local(), "old-id"
        state = {"plays": 0}

        async def req(method, path, params=None, json=None, retry=True):
            calls.append(("web", method, path, (params or {}).get("device_id")))
            if path == "/me/player/play":
                state["plays"] += 1
                if fail_first_play and state["plays"] == 1:
                    raise ApiError(404, "Device not found")
            return None

        async def find_device():
            calls.append(("find",)); sp.device_id = "new-id"
        sp.req, sp.find_device = req, find_device
        return sp, calls

    def test_play_activates_then_plays(self):
        sp, calls = self.make(False)
        asyncio.run(sp.play(context="spotify:album:A"))
        self.assertEqual(calls[0], ("soloist", ("activate",)))
        self.assertEqual(calls[-1], ("web", "PUT", "/me/player/play", "old-id"))

    def test_play_recovers_from_device_not_found(self):
        sp, calls = self.make(True)
        asyncio.run(sp.play(uris=["spotify:track:T"]))
        self.assertIn(("find",), calls)
        self.assertEqual(calls[-1], ("web", "PUT", "/me/player/play", "new-id"))     # retried on the fresh id

    def test_resume_brings_omarchy_back(self):
        sp, calls = self.make(False)
        asyncio.run(sp.resume())
        self.assertEqual([c[0] for c in calls], ["ctl", "soloist", "ctl"])           # failed ctl, activate, ctl play


class MarqueeTests(unittest.TestCase):
    def test_turntable_title_scrolls_only_when_too_long(self):
        from PIL import ImageChops
        from omaspot import scene
        L = scene.layout(1080, 589, 19.0)
        box = scene.title_box(L, 9.0, 19.0)
        long_t = "Don't Look Back in Anger - Remastered 2014 (Live at Knebworth, 10 August 1996)"
        self.assertTrue(scene.title_overflows(L, long_t, 19.0))
        self.assertFalse(scene.title_overflows(L, "Lonely", 19.0))
        a = scene.paint_title(L, box, 9.0, 19.0, long_t, "#ffffff", 0)
        b = scene.paint_title(L, box, 9.0, 19.0, long_t, "#ffffff", 40)
        self.assertIsNotNone(ImageChops.difference(a, b).getbbox())                 # it moves
        short0 = scene.paint_title(L, box, 9.0, 19.0, "Lonely", "#ffffff", 0)
        short1 = scene.paint_title(L, box, 9.0, 19.0, "Lonely", "#ffffff", 40)
        self.assertIsNone(ImageChops.difference(short0, short1).getbbox())          # a short one never moves

    def test_player_bar_marquee_window(self):
        from omaspot.widgets import Marquee
        m = Marquee.__new__(Marquee)
        m._text, m._off = "abcdefghij", 3
        loop = m._text + Marquee.GAP
        self.assertEqual((loop + loop)[m._off:m._off + 5], "defgh")                 # the window it shows


class ShortcutTests(unittest.TestCase):
    def test_labels_and_owners(self):
        from omaspot import keys as K
        self.assertEqual(K.labels("plus,equals_sign"), "+  =")
        self.assertEqual(K.label("alt+left"), "alt+←")
        self.assertEqual(K.owner("n", {}), "next")
        self.assertIsNone(K.owner("n", {"next": "m"}))                 # moved away from n
        self.assertEqual(K.owner("m", {"next": "m"}, besides="previous"), "next")
        self.assertEqual(len({b.id for b in K.bindings()}), len(K.SHORTCUTS) + 1)   # unique ids + esc
        defaults = [k for keys in K.DEFAULTS.values() for k in keys.split(",")]
        self.assertEqual(len(defaults), len(set(defaults)))            # no two defaults share a key

    def test_saved_keys_are_cleaned(self):
        from omaspot.settings import Settings
        s = Settings(keys={"next": "m", "nonsense": "x", "repeat": 5, "shuffle": "  "}).clean()
        self.assertEqual(s.keys, {"next": "m"})
        self.assertEqual(Settings(keys="bad").clean().keys, {})
