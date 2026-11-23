"""The pages. Each takes the app and its page key and fills the main pane; ROUTES maps key[0] to the page.

Keys: ("home",) ("search",) ("query", q) ("liked",) ("devices",) ("grid", page_id) ("album", id)
("artist", id, name) ("playlist", id, name, uri, spotify_made, image_url)
"""
import asyncio
from datetime import datetime

from . import icons as I
from .api import ApiError
from .items import album_tile, artist_tile, playlist_tile, track_tile
from .util import artists, hours, pick_image


def _total_ms(tracks) -> int:
    return sum(t.get("duration_ms") or 0 for t in tracks)


async def home(app, key):
    p = app.pane
    app.set_crumb("Home")
    p.set_mode("grid", hero=False)
    h = datetime.now().hour
    greet = "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"
    await p.show_grid([{"greeting": greet}, p.ph_section("Jump back in"), p.ph_section("Your playlists"),
                       p.ph_section("Saved albums")])
    recent, pls, albums, top = await asyncio.gather(
        app.sp.recent(), app.sp.playlists() if app.playlists is None else asyncio.sleep(0, app.playlists),
        app.sp.saved_albums(), app.sp.top_artists(), return_exceptions=True)
    ok = lambda x: x if not isinstance(x, BaseException) else []
    await p.show_grid([
        {"greeting": greet},
        {"title": "Jump back in", "one_row": True, "items": jump_back_in(ok(recent), ok(pls) or app.playlists or [])},
        {"title": "Your playlists", "one_row": True, "items": [playlist_tile(x) for x in ok(pls)]},
        {"title": "Saved albums", "one_row": True, "items": [album_tile(a) for a in ok(albums)]},
        {"title": "Your top artists", "one_row": True, "items": [artist_tile(a) for a in ok(top)]},
    ])


def jump_back_in(recent: list[dict], playlists: list[dict]) -> list[dict]:
    """Recently played, one tile per album or playlist it came from (newest first), not one per song.
    Playlists are the user's own (the only ones whose names and covers we have); anything else shows its album."""
    by_uri = {p.get("uri"): p for p in playlists if p.get("uri")}
    seen, tiles = set(), []
    for r in recent:
        t, ctx = r.get("track") or {}, r.get("context") or {}
        pl = by_uri.get(ctx.get("uri")) if ctx.get("type") == "playlist" else None
        if pl:
            key, tile = pl["uri"], playlist_tile(pl)
        elif (t.get("album") or {}).get("uri"):
            key, tile = t["album"]["uri"], album_tile(t["album"])
        else:
            key, tile = t.get("uri"), track_tile(t)
        if key and key not in seen:
            seen.add(key)
            tiles.append(tile)
    return tiles


async def grid(app, key):
    p = app.pane
    title, items = p.pages.get(key[1], ("", []))
    app.set_crumb(title)
    p.set_mode("grid", hero=False)
    await p.show_grid([{"greeting": title}, {"items": items}])


async def liked(app, key):
    p = app.pane
    app.set_crumb("Liked Songs")
    p.set_mode("list")
    p.set_header("playlist", "Liked Songs", "loading…", I.HEART, cover_img=p.liked_tile())
    p.show_rows(p.placeholder_rows())
    tracks, total = await app.sp.liked()
    for t in tracks:
        app.liked[t["uri"]] = True
    p.set_header("playlist", "Liked Songs", f"{total:,} songs · {hours(_total_ms(tracks))}", I.HEART, cover_img=p.hero_img)
    if not tracks:
        p.show_empty(I.HEART, "Songs you like will appear here", "Right-click a song and choose Save to Liked Songs.", True)
        return
    p.show_tracks(tracks, ("uris",))


async def playlist(app, key):
    p = app.pane
    _, pid, name, uri, spotify_made, *rest = key
    img = rest[0] if rest else None
    app.set_crumb(f"Playlists / {name}")
    if spotify_made:
        p.set_header("playlist", name, "Made by Spotify", I.MUSIC, False, img)
        p.show_empty(I.WARN, "Spotify doesn't share this playlist", "Playlists made by Spotify (Discover Weekly, Daily Mix…) "
                     "can't be read by new developer apps.", True)
        return
    p.set_mode("list")
    p.set_header("playlist", name, "loading…", cover_url=img)
    p.show_rows(p.placeholder_rows())
    try:
        tracks, total = await app.sp.playlist_tracks(pid)
    except ApiError as e:
        if e.status != 403:
            raise
        p.set_header("playlist", name, "someone else's playlist", I.MUSIC, False, img)
        p.show_empty(I.WARN, "Spotify doesn't let omaspot read this playlist",
                     "Songs of other people's playlists are closed to new developer apps. Open it in the Spotify app.", True)
        return
    p.set_header("playlist", name, f"{total:,} songs · {hours(_total_ms(tracks))}", cover_url=img)
    if not tracks:
        p.show_empty(I.MUSIC, "This playlist is empty", "Add songs to it from the Spotify app.", True)
        return
    p.show_tracks(tracks, ("context", uri))


async def album(app, key):
    p = app.pane
    p.set_mode("list")
    p.show_rows(p.placeholder_rows())
    a, tracks = await app.sp.album(key[1])
    first = next((x for x in a.get("artists", []) if x.get("id")), None)
    app.set_crumb(f"Albums / {a.get('name', '')}")
    p.set_header(a.get("album_type") or "album", a.get("name", ""),
                 f"{artists(a)} · {(a.get('release_date') or '')[:4]} · {len(tracks)} songs · {hours(_total_ms(tracks))}",
                 "◆", cover_url=pick_image(a.get("images")), artist=(first["id"], first["name"]) if first else None)
    p.show_tracks(tracks, ("context", a.get("uri")))


async def artist(app, key):
    p = app.pane
    _, aid, name = key
    app.set_crumb(f"Artists / {name}")
    p.set_mode("list")
    p.set_header("artist", name, "loading…", I.ARTIST, False)
    p.show_rows(p.placeholder_rows(5))
    a, albums, top = await asyncio.gather(app.sp.artist(aid), app.sp.artist_albums(aid), app.sp.artist_top(aid, name),
                                          return_exceptions=True)
    a = a if isinstance(a, dict) else {}
    albums = albums if isinstance(albums, list) else []
    top = (top if isinstance(top, list) else [])[:10]
    full = [x for x in albums if x.get("album_group", x.get("album_type")) == "album"]
    singles = [x for x in albums if x not in full]
    p.set_header("artist", a.get("name") or name, f"{len(full)} albums · {len(singles)} singles and EPs", I.ARTIST,
                 bool(top), pick_image(a.get("images")))
    sections = [{"title": "Albums", "items": [album_tile(x) for x in full]},
                {"title": "Singles and EPs", "items": [album_tile(x) for x in singles]}]
    if top:
        p.set_mode("mixed", hero=True, table_lines=min(len(top), 5) * 2, title="Popular")
        p.show_tracks(top, ("uris",))
    else:
        p.set_mode("grid", hero=True)
    await p.show_grid(sections)


async def devices(app, key):
    p = app.pane
    app.set_crumb("Devices")
    ds = await app.sp.devices()
    p.set_header("devices", "Devices", f"{len(ds)} online · double-click or enter plays there", I.DEVICE, False)
    if not ds:
        p.show_empty(I.DEVICE, "No devices online", "Start Soloist: systemctl --user start soloist", True)
        return
    p.show_devices(ds)


async def search(app, key):
    app.set_crumb("Search")
    app.pane.show_empty(I.SEARCH, "Search Spotify", "Type in the search box at the top and press enter.")
    app.action_focus_search()


async def query(app, key):
    p, q = app.pane, key[1]
    app.set_crumb(f"Search / “{q}”")
    p.set_mode("mixed", hero=False, table_lines=10, title="Songs")
    p.show_rows(p.placeholder_rows(5))
    await p.show_grid([p.ph_section("Artists"), p.ph_section("Albums")])
    d = await app.sp.search(q)
    get = lambda k: [x for x in (d.get(k) or {}).get("items", []) if x]
    tracks, arts, albums, pls = get("tracks"), get("artists"), get("albums"), get("playlists")
    if not (tracks or arts or albums or pls):
        p.show_empty(I.SEARCH, f"No results for “{q}”", "Check the spelling, or try fewer words.")
        return
    if tracks:
        p.set_mode("mixed", hero=False, table_lines=min(len(tracks), 6) * 2, title="Songs")
        p.show_tracks(tracks, ("uris",))
    else:
        p.set_mode("grid", hero=False)
    await p.show_grid([
        {"title": "Artists", "one_row": True, "items": [artist_tile(a) for a in arts]},
        {"title": "Albums", "one_row": True, "items": [album_tile(a) for a in albums]},
        {"title": "Playlists", "one_row": True, "items": [playlist_tile(x) for x in pls]},
    ])
    p.table.focus()


async def prefs(app, key):
    p = app.pane
    app.set_crumb("Preferences")
    p.set_mode("prefs", hero=False)
    await p.show_prefs()


ROUTES = {"prefs": prefs, "home": home, "grid": grid, "liked": liked, "playlist": playlist, "album": album, "artist": artist,
          "devices": devices, "search": search, "query": query}
