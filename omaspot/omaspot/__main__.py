import argparse
import asyncio
import sys

from . import auth, config


async def _check():
    from .api import Spotify
    sp = Spotify()
    try:
        me = await sp.me()
        print(f"signed in as: {me.get('display_name') or me.get('id')} ({me.get('product', '?')})")
        devs = await sp.devices()
        print("devices:", ", ".join(f"{d.get('name')}{' (active)' if d.get('is_active') else ''}" for d in devs) or "none")
        print(f"{config.DEVICE_NAME} online:", any(d.get("name") == config.DEVICE_NAME for d in devs))
        pls = await sp.playlists()
        print(f"playlists: {len(pls)}")
        _, total = await sp.liked(max_items=1)
        print(f"liked songs: {total}")
        s = await sp.search("nujabes")
        print("search ok:", len((s.get("tracks") or {}).get("items", [])), "tracks")
    finally:
        await sp.close()


def main():
    ap = argparse.ArgumentParser(prog="omaspot", description="Terminal Spotify controller (plays on Soloist).")
    ap.add_argument("--login", action="store_true", help="sign in to Spotify in the browser and exit")
    ap.add_argument("--check", action="store_true", help="test the connection without opening the UI")
    args = ap.parse_args()
    if args.login or not auth.load():
        auth.login()
        print("Signed in. Token saved to", config.TOKEN_FILE)
        if args.login:
            return
    if args.check:
        asyncio.run(_check())
        return
    from .api import NotSignedIn
    from .app import Omaspot
    try:
        app = Omaspot()
    except NotSignedIn:
        raise SystemExit("omaspot: not signed in. Run `omaspot --login` first.")
    app.run()


if __name__ == "__main__":
    sys.exit(main())
