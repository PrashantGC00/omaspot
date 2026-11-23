import os
from pathlib import Path

HOME = Path.home()
ENV_FILE = HOME / ".config/soloist/.env"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / "omaspot"
TOKEN_FILE = CACHE / "token.json"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
DEVICE_NAME = os.environ.get("OMASPOT_DEVICE", "Omarchy")
SCOPES = " ".join([
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-library-read",
    "user-library-modify",
])


def client_id() -> str:
    """CLIENT_ID from the private env file. The value is never printed."""
    try:
        lines = ENV_FILE.read_text().splitlines()
    except OSError as e:
        raise SystemExit(f"omaspot: can't read {ENV_FILE}: {e.strerror}")
    for line in lines:
        line = line.strip()
        if line.startswith("CLIENT_ID="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    raise SystemExit(f"omaspot: CLIENT_ID is missing from {ENV_FILE}")
