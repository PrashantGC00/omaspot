"""Spotify sign-in with PKCE (no client secret) and a token cache at ~/.cache/omaspot/token.json (mode 600)."""
import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"


def load():
    try:
        return json.loads(config.TOKEN_FILE.read_text())
    except (OSError, ValueError):
        return None


def _save(tok: dict) -> dict:
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    config.CACHE.mkdir(parents=True, exist_ok=True)
    tmp = config.TOKEN_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(tok, f)
    os.replace(tmp, config.TOKEN_FILE)
    return tok


def _post(data: dict) -> dict:
    req = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"token request failed ({e.code}): {body}") from None


def refresh(tok: dict) -> dict:
    new = _post({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": config.client_id()})
    new.setdefault("refresh_token", tok["refresh_token"])
    return _save(new)


def login(timeout: int = 300) -> dict:
    cid = config.client_id()
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": config.REDIRECT_URI,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
        "scope": config.SCOPES,
    })
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            result.update({k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()})
            ok = "code" in result and result.get("state") == state
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "omaspot is signed in. You can close this tab." if ok else "omaspot sign-in failed. Check the terminal."
            self.wfile.write(f"<!doctype html><title>omaspot</title><h2 style='font-family:monospace'>{msg}</h2>".encode())

        def log_message(self, *args):
            pass

    host, port = "127.0.0.1", int(urllib.parse.urlparse(config.REDIRECT_URI).port or 8888)
    server = http.server.HTTPServer((host, port), Handler)
    server.timeout = 2
    print("Opening Spotify sign-in in your browser. If it doesn't open, visit:\n\n  " + url + "\n")
    try:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass
    deadline = time.time() + timeout
    try:
        while "code" not in result and "error" not in result and time.time() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "error" in result:
        raise SystemExit(f"omaspot: Spotify sign-in was refused: {result['error']}")
    if "code" not in result:
        raise SystemExit("omaspot: sign-in timed out. Run `omaspot --login` again.")
    if result.get("state") != state:
        raise SystemExit("omaspot: sign-in state didn't match; try again.")
    tok = _post({
        "grant_type": "authorization_code",
        "code": result["code"],
        "redirect_uri": config.REDIRECT_URI,
        "client_id": cid,
        "code_verifier": verifier,
    })
    return _save(tok)
