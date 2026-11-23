"""Grid tiles and page keys built from Web API objects (pure data, no UI)."""
from .util import artists, pick_image


def playlist_key(p: dict) -> tuple:
    return ("playlist", p.get("id"), p.get("name") or "Untitled", p.get("uri"),
            (p.get("owner") or {}).get("id") == "spotify", pick_image(p.get("images")))


def album_tile(a: dict) -> dict:
    year = (a.get("release_date") or "")[:4]
    return {"kind": "album", "title": a.get("name", ""), "sub": " · ".join(x for x in (year, artists(a)) if x),
            "url": pick_image(a.get("images"), 200), "key": ("album", a.get("id")), "uri": a.get("uri")}


def playlist_tile(p: dict) -> dict:
    return {"kind": "playlist", "title": p.get("name") or "Untitled",
            "sub": f"by {(p.get('owner') or {}).get('display_name') or 'you'}",
            "url": pick_image(p.get("images"), 200), "key": playlist_key(p), "uri": p.get("uri")}


def artist_tile(a: dict) -> dict:
    return {"kind": "artist", "title": a.get("name", ""), "sub": "Artist", "url": pick_image(a.get("images"), 200),
            "key": ("artist", a.get("id"), a.get("name", "")), "uri": a.get("uri")}


def track_tile(t: dict) -> dict:
    album = t.get("album") or {}
    play = {"context": album["uri"], "offset": {"uri": t["uri"]}} if album.get("uri") else {"uris": [t["uri"]]}
    return {"kind": "track", "title": t.get("name", ""), "sub": artists(t), "url": pick_image(album.get("images"), 200),
            "tr": t, "play": play, "uri": t.get("uri")}


def play_args(p: dict) -> tuple:
    """A row's/tile's `play` dict → Spotify.play(uris, context, offset)."""
    return p.get("uris"), p.get("context"), p.get("offset")
