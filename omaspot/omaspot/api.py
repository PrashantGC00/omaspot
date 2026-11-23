"""Async Spotify Web API client: library, search and playback control. Never streams audio."""
import asyncio
import time

import httpx

from . import auth, config
from .soloist import Soloist
from .state import PlayerState, track_from_soloist

BASE = "https://api.spotify.com/v1"


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


class NotSignedIn(Exception):
    pass


class Spotify:
    """Web API client. Playback controls go to the local Soloist (`local`) while Omarchy is the active device."""

    def __init__(self, local: Soloist | None = None):
        self.tok = auth.load()
        if not self.tok:
            raise NotSignedIn("not signed in")
        self.http = httpx.AsyncClient(base_url=BASE, timeout=15)
        self.device_id: str | None = None
        self.local = local or Soloist()

    async def close(self):
        await self.http.aclose()

    async def _token(self) -> str:
        if time.time() >= self.tok.get("expires_at", 0):
            self.tok = await asyncio.to_thread(auth.refresh, self.tok)
        return self.tok["access_token"]

    async def req(self, method: str, path: str, params=None, json=None, retry: bool = True):
        headers = {"Authorization": "Bearer " + await self._token()}
        r = await self.http.request(method, path, params=params, json=json, headers=headers)
        if r.status_code == 401 and retry:
            self.tok["expires_at"] = 0
            return await self.req(method, path, params, json, False)
        if r.status_code == 429 and retry:
            await asyncio.sleep(min(int(r.headers.get("Retry-After", "1") or 1), 10))
            return await self.req(method, path, params, json, False)
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except ValueError:
                msg = r.text
            raise ApiError(r.status_code, msg or r.reason_phrase)
        if r.status_code == 204 or not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    async def get(self, path: str, **params):
        return await self.req("GET", path, params=params or None)

    async def _pages(self, path: str, params: dict, max_items: int):
        items, total, url = [], 0, path
        while url and len(items) < max_items:
            d = await self.req("GET", url, params=params) or {}
            items += [i for i in d.get("items", []) if i]
            total = d.get("total", len(items))
            nxt = d.get("next")
            url, params = (nxt.replace(BASE, "") if nxt else None), None
        return items, total

    # ---- library ----
    async def me(self):
        return await self.get("/me")

    async def playlists(self):
        items, _ = await self._pages("/me/playlists", {"limit": 50}, 500)
        return items

    async def liked(self, max_items: int = 200):
        items, total = await self._pages("/me/tracks", {"limit": 50}, max_items)
        out = []
        for i in items:
            if i.get("track"):
                i["track"]["_added"] = i.get("added_at") or ""
                out.append(i["track"])
        return out, total

    async def playlist_tracks(self, pid: str, max_items: int = 300):
        try:
            items, total = await self._pages(f"/playlists/{pid}/items", {"limit": 50}, max_items)
        except ApiError as e:
            if e.status != 404:
                raise
            items, total = await self._pages(f"/playlists/{pid}/tracks", {"limit": 50}, max_items)
        tracks = []
        for i in items:
            t = i.get("item") or i.get("track")
            if t and t.get("type", "track") == "track" and t.get("uri"):
                t["_added"] = i.get("added_at") or ""
                tracks.append(t)
        return tracks, total

    async def album(self, aid: str):
        a = await self.get(f"/albums/{aid}")
        tracks = []
        for t in (a.get("tracks") or {}).get("items", []):
            if t:
                t["album"] = {"name": a.get("name"), "uri": a.get("uri"), "id": a.get("id"), "images": a.get("images")}
                tracks.append(t)
        return a, tracks

    async def search(self, q: str):
        return await self.get("/search", q=q, type="track,artist,album,playlist", limit=10) or {}

    # ---- home / artist ----
    async def recent(self, limit: int = 50):
        """Recently played: [{"track", "context"}], newest first (context: the album/playlist it played from)."""
        d = await self.get("/me/player/recently-played", limit=limit) or {}
        return [{"track": i["track"], "context": i.get("context") or {}} for i in d.get("items", []) if i and i.get("track")]

    async def top_artists(self, limit: int = 20):
        return [a for a in (await self.get("/me/top/artists", limit=limit) or {}).get("items", []) if a]

    async def saved_albums(self, max_items: int = 100):
        items, _ = await self._pages("/me/albums", {"limit": 50}, max_items)
        return [i["album"] for i in items if i.get("album")]

    async def artist(self, aid: str):
        return await self.get(f"/artists/{aid}") or {}

    async def artist_albums(self, aid: str):
        items, _ = await self._pages(f"/artists/{aid}/albums", {"include_groups": "album,single", "limit": 10}, 60)
        return items

    async def artist_top(self, aid: str, name: str):
        """Popular songs. /artists/{id}/top-tracks is closed to new developer apps (403), so search for the artist's songs."""
        try:
            return ((await self.get(f"/artists/{aid}/top-tracks", market="from_token")) or {}).get("tracks", [])
        except ApiError as e:
            if e.status not in (403, 404):
                raise
        d = await self.get("/search", q=f'artist:"{name}"', type="track", limit=10) or {}
        return [t for t in (d.get("tracks") or {}).get("items", []) if t and any(a.get("id") == aid for a in t.get("artists", []))]

    async def find_artist(self, name: str):
        d = await self.get("/search", q=name, type="artist", limit=5) or {}
        items = [a for a in (d.get("artists") or {}).get("items", []) if a]
        return next((a for a in items if a.get("name", "").lower() == name.lower()), items[0] if items else None)

    # ---- library: liking (Spotify's /me/tracks endpoints are closed to new apps; /me/library works) ----
    async def liked_flags(self, uris: list[str]) -> list[bool]:
        out = []
        for i in range(0, len(uris), 40):
            part = uris[i:i + 40]
            d = await self.get("/me/library/contains", uris=",".join(part))
            out += list(d) if isinstance(d, list) else [False] * len(part)
        return out

    async def like(self, uri: str, on: bool):
        await self.req("PUT" if on else "DELETE", "/me/library", params={"uris": uri})

    # ---- player ----
    async def state(self) -> PlayerState:
        """Player state from the Web API (other devices; it returns nothing while Soloist plays)."""
        return PlayerState.from_web(await self.get("/me/player"))

    async def devices(self):
        return (await self.get("/me/player/devices") or {}).get("devices", [])

    async def find_device(self):
        for d in await self.devices():
            if d.get("name") == config.DEVICE_NAME:
                self.device_id = d.get("id")
                return d
        self.device_id = None
        return None

    async def queue(self) -> list[dict]:
        """Upcoming tracks: from Soloist while Omarchy plays (~10 ms), else the Web API (0.5-0.9 s)."""
        if self.local.active:
            items = await self.local.queue(30)
            if items:
                return [track_from_soloist(i) for i in items]
        q = await self.get("/me/player/queue") or {}
        return [t for t in q.get("queue", []) if t and t.get("type", "track") == "track"][:30]

    def _dev(self):
        return {"device_id": self.device_id} if self.device_id else None

    async def play(self, uris=None, context=None, offset=None):
        """Start playback on the Omarchy device (transfers to it if something else is active)."""
        body = {}
        if context:
            body["context_uri"] = context
        if uris:
            body["uris"] = uris
        if offset is not None:
            body["offset"] = offset
        await self.make_here()
        try:
            await self.req("PUT", "/me/player/play", params=self._dev(), json=body or None)
        except ApiError as e:
            if e.status != 404:
                raise
            # "Device not found": the id went stale (Soloist restarted or updated) or Spotify hadn't seen the
            # switch yet. Look the device up again, make it active, and try once more.
            await self.find_device()
            await self.make_here(force=True)
            await asyncio.sleep(0.5)
            await self.req("PUT", "/me/player/play", params=self._dev(), json=body or None)

    async def make_here(self, force: bool = False) -> bool:
        """Make Omarchy the active Spotify Connect device through Soloist (local, ~10 ms). Spotify answers 404
        "Device not found" when asked to play on a device that isn't active, e.g. after Soloist restarted."""
        if self.local.active and not force:
            return True
        try:
            rc, _ = await self.local._run("activate")
        except (OSError, asyncio.TimeoutError):
            return False
        if rc == 0:
            self.local.active = True
            await asyncio.sleep(0.3)           # give Spotify a moment to see the switch
        return rc == 0

    # Controls go to Soloist directly while Omarchy is playing (the Web API takes 0.5-0.9 s per call);
    # otherwise, or if that fails, through the Web API.
    async def resume(self):
        if await self.local.ctl("play"):
            return
        if await self.make_here() and await self.local.ctl("play"):     # Omarchy wasn't active: bring it back
            return
        await self.req("PUT", "/me/player/play", params=self._dev())

    async def pause(self):
        if not await self.local.ctl("pause"):
            await self.req("PUT", "/me/player/pause")

    async def next(self):
        if not await self.local.ctl("next"):
            await self.req("POST", "/me/player/next")

    async def previous(self):
        if not await self.local.ctl("prev"):
            await self.req("POST", "/me/player/previous")

    async def seek(self, ms: int):
        ms = max(0, int(ms))
        if not await self.local.ctl("seek", ms):
            await self.req("PUT", "/me/player/seek", params={"position_ms": ms})

    async def volume(self, pct: int):
        pct = max(0, min(100, int(pct)))
        if not await self.local.ctl("volume", pct):
            await self.req("PUT", "/me/player/volume", params={"volume_percent": pct})

    async def shuffle(self, on: bool):
        if not await self.local.ctl("shuffle", "on" if on else "off"):
            await self.req("PUT", "/me/player/shuffle", params={"state": "true" if on else "false"})

    async def repeat(self, mode: str):
        if not await self.local.ctl("repeat", mode):
            await self.req("PUT", "/me/player/repeat", params={"state": mode})

    async def add_to_queue(self, uri: str):
        if not await self.local.ctl("add-to-queue", uri):
            await self.req("POST", "/me/player/queue", params={"uri": uri})

    async def transfer(self, device_id: str, play: bool = True):
        await self.req("PUT", "/me/player", json={"device_ids": [device_id], "play": play})
