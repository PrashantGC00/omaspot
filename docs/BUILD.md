# Build instructions

How to build the Spotify overlay, step by step. The design is in `docs/design.html` (published at https://claude.ai/artifact/4jfU1uWhWjKL1smv97sMmo). The decisions and their reasons are in `CLAUDE.md`.

Nothing is built yet. Do the steps in order. Step 1 decides whether the plan works at all.

## Before you start

**Account and keys** (all done):
- Spotify Premium.
- `~/.config/soloist/.env` holds `SOLOIST_API_KEY` and `CLIENT_ID`. Lock it down with `chmod 600 ~/.config/soloist/.env`, and never print, log or commit its values.
- In the Spotify Developer dashboard, the app for `CLIENT_ID` needs the redirect URI `http://127.0.0.1:8888/callback`. Spotify rejects `localhost` now, so use `127.0.0.1`.

**Packages:**

```sh
sudo pacman -S --needed kitty cava python-textual python-httpx python-pillow
```

Already installed: Python 3.14, ImageMagick 7, JetBrainsMono Nerd Font, Alacritty.

Also needed: `textual-image`, which shows the cover in kitty. Check the AUR for `python-textual-image` first; otherwise install it into omaspot's own venv. Review any AUR package before installing it.

kitty is only for the overlay window. Alacritty stays the default terminal.

## Project layout

Repository layout:

```
omaspot/
├── README.md            overview, install, usage
├── CLAUDE.md            decisions, constraints, status
├── install.sh           installs everything (idempotent; --uninstall)
├── docs/                BUILD.md (this file), design.html (local copy of the design artifact)
├── omaspot/               the terminal app (Python + Textual)
│   ├── bin/omaspot        launcher (opens the overlay, or omaspot in its own kitty window)
│   ├── omaspot/           package: app, views, pane, components, widgets, focus, scene, state, soloist, mpris, api…
│   └── tests/           unittest suite
├── manifest.json, BarWidget.qml, i18n/   the Omarchy plugin (the repo root is the plugin)
├── soloist/             soloist.service, soloist-mpris.service, soloist-update (+ .service/.timer), fixport.c
├── overlay/             omaspot-overlay script, kitty-overlay.conf, Hyprland rules + keybind (omaspot.lua)
└── hooks/               theme-set hook
```

Where the files get installed:

| File | Installed to |
|---|---|
| `soloist` binary | `~/.local/bin/soloist` |
| `soloist.service`, `soloist-update.{service,timer}` | `~/.config/systemd/user/` |
| `omaspot` | `~/.local/bin/omaspot` (wrapper that runs the package) |
| `omaspot-overlay` | `~/.local/bin/omaspot-overlay` |
| `kitty-overlay.conf` | `~/.config/omaspot/kitty-overlay.conf` |
| window rules and keybind | `~/.config/hypr/` (Lua). Use the `omarchy` skill and back up first. |
| theme hook | `~/.config/omarchy/hooks/theme-set.d/omaspot-theme` (from `hooks/omaspot-theme`) |
| caches (token, covers, palettes, cava fifo) | `~/.cache/omaspot/` |

Back up any config before editing it, to `~/.config/omarchy/backups/<name>-YYYYMMDD/`.

---

## Step 1: Soloist plays on this PC

**Goal:** pick "Omarchy" in the Spotify app on your phone and hear the music from this PC.

1. Check the architecture: `uname -m` should print `x86_64`.
2. Get the build file name from Spotify's "Downloads and updates" page at https://developer.spotify.com/documentation/soloist/. The getting-started guide gives the pattern `soloist_release_<arch>.tar.gz`. Then download the build into an empty folder:
   ```sh
   mkdir -p ~/.local/share/soloist/dl && cd ~/.local/share/soloist/dl
   curl --fail --location -o soloist.tar.gz https://soloist-builds.spotifycdn.com/soloist_release_x86_64.tar.gz   # verified 2026-11-20 (linux_x86_64 is 404)
   tar -xzf soloist.tar.gz
   install -m 755 soloist ~/.local/bin/soloist
   soloist --version && soloist --help
   ```
   Use the official tarball, not the AUR packages `spotify-soloist-bin` and `soloistd`. Don't redistribute the archive.
3. Read `--help` and check two things:
   - Can the key come from the `SOLOIST_API_KEY` environment variable? Prefer that over `--api-key`, because command-line arguments show up in `ps`.
   - Do `--device-name` and `--ws` work as the guide describes?
4. Run it in the foreground:
   ```sh
   # done 2026-11-20: key stored once via stdin (never on the command line)
   sed -n 's/^SOLOIST_API_KEY=//p' ~/.config/soloist/.env | tr -d '"\r\n ' | soloist setup-api-key
   soloist --device-name Omarchy --ws 127.0.0.1:0
   ```
5. On your phone, on the same Wi-Fi, choose **Omarchy** as the device and play a song.
   - **It works:** you hear audio from this PC. Write "Soloist works on this account" with today's date in `CLAUDE.md`.
   - **It fails:** save the log output, and check whether the error looks like the librespot block. The fallback is the official desktop app running hidden as the playback device. Note what happened in `CLAUDE.md` before going further.
6. Make it a user service. Done 2026-11-20: see `soloist/soloist.service`. It loads `~/.local/lib/soloist-fixport.so` (built from `soloist/fixport.c`) with `SOLOIST_ZC_PORT=47621`, so pairing always uses the same port. ufw needs `udp 5353` and `tcp 47621` from your local subnet (install.sh offers the rules). Install it with `install -Dm644 soloist/soloist.service ~/.config/systemd/user/`, then run `systemctl --user enable --now soloist`. Check it with `soloist ctl status` (should say `logged in: yes`).
7. Builds expire 90 days after their build date. Write `soloist/soloist-update` to download the current build, swap it in and restart the service. Run it from a weekly `soloist-update.timer`, and also when the service exits with code 10.
8. Write down the WebSocket port. Soloist writes it to `ws.addr` and `ws.port` in its data directory. Keep it on `127.0.0.1` only, because the socket has no authentication.

**Done when:** after a reboot, the phone can play on Omarchy without you starting anything.

## Step 2: omaspot, the terminal app

**Goal:** browse your library and play it on Omarchy from a terminal app laid out like a desktop music app.

1. Create `omaspot/` as a Python package that uses Textual and httpx.
2. **Sign-in** uses PKCE, with no client secret:
   - Client ID: `CLIENT_ID` from `~/.config/soloist/.env`.
   - Redirect URI: `http://127.0.0.1:8888/callback`. Run a one-shot local server to catch the code.
   - Scopes:
     - `user-read-playback-state`
     - `user-modify-playback-state`
     - `user-read-currently-playing`
     - `playlist-read-private`
     - `playlist-read-collaborative`
     - `user-library-read`
     - `user-library-modify` (for liking songs)
   - Cache the token in `~/.cache/omaspot/token.json` with mode 600, and refresh it automatically.
3. **Layout** (match the design page):
   - Title bar: back and forward, breadcrumb, search box, and the window icons.
   - Sidebar: Search, Liked Songs, Queue, Focus and Devices, then your playlists.
   - Main pane: playlist header with Play, Shuffle and Sort, then the track table.
   - Player bar: mini cover, prev / play-pause / next, `▰▱` seek bar, focus button, device, and a `▰▱` volume slider.
   - Icons are JetBrainsMono Nerd Font codepoints. The list is in `CLAUDE.md`.
   - Both mouse and keys work: `/` search, `enter` play, `space` play/pause, `n` and `p` next and previous, `f` focus, `esc` leave focus or hide.
4. **Device:** find "Omarchy" in `GET /me/player/devices` and send playback there. If it's missing, say so in the app and show `journalctl --user -u soloist` as the fix.
5. **Limits:** Spotify-made playlists such as Discover Weekly won't load for a new developer app, so hide them or label them. Poll player state every 1–2 s at most.

**Done when:** you can open Liked Songs, play it on Omarchy, search "nujabes", queue a track and skip, all from omaspot.

## Step 3: the overlay window

**Goal:** Super+Shift+M drops omaspot down over everything and hides it again, keeping its state.

1. Write `overlay/kitty-overlay.conf`:
   - JetBrainsMono Nerd Font
   - `background_opacity` around 0.85
   - `clear_all_shortcuts yes`, so every key reaches omaspot
   - colours included from the current Omarchy theme
2. Write `overlay/omaspot-overlay` with `toggle`, `ensureRunning`, `show`, `hide` and `isOpen`:
   - Start the window with `kitty --class omaspot -c ~/.config/omaspot/kitty-overlay.conf omaspot`.
   - Hide it by moving it to `special:omaspot-overlay` with `hyprctl`, and show it by bringing it back. Don't close it.
   - `ensureRunning` also checks `systemctl --user is-active soloist`.
3. **Hyprland:** use the `omarchy` skill and back up first.
   - Window rules for class `omaspot-overlay`: float, size 1100×680, centred, blur on.
   - Override Super+Shift+M in `~/.config/hypr/bindings.lua`. It currently opens the Spotify desktop app, which stays installed as a fallback.

**Done when:** pressing the key repeatedly toggles the window instantly, and music keeps playing while it's hidden.

## Step 4: focus mode

**Goal:** a full-window view with the cava visualizer translucent and blurred at the back, the song cover in front of it, and the spinning record in the centre. The player bar hides and its controls sit over the art.

1. **Visualizer.** Use a `cava` config at `~/.config/omaspot/cava.conf`:
   - `method = pipewire`
   - `bars = 24`
   - `output.method = raw`, `data_format = ascii`, `raw_target = ~/.cache/omaspot/cava.fifo`

   Draw 8 rows of `▁▂▃▄▅▆▇█` in the album's colours, with `━` peak caps that fall slowly.
2. **Cover.**
   - Download it once to `~/.cache/omaspot/covers/<album>.jpg`.
   - Make a blurred copy with `magick in.jpg -blur 0x12 out.jpg`.
   - Show it with `textual-image`, which uses the kitty graphics protocol.
3. **Palette.** Use Pillow `quantize(4)` on the cover, and cache the result in `~/.cache/omaspot/palette/<album>.json`.
4. **Record.**
   - Draw a 26×13 text grid at about 30 fps: `░ ▒` rings, a turning `▓` highlight, a `█` label in the album colours, and a `◉ │ ╯ ╭ ■` tonearm.
   - It spins at 33⅓ rpm, one turn every 1.8 s. Pausing eases it to a stop and lifts the arm.
   - Fake the "blur" with dimmer colours and soft edges. If that looks weak, try drawing the view as an image each frame and measure the CPU cost.
5. **Now playing.** Try both sources and keep the better one:
   - Plan A: omaspot's own player polling.
   - Plan B: Soloist's WebSocket `track_changed` events, which include cover URLs under `decorations.visual_identity.cover[]`.
6. **Controls:**
   - Title and artist on the left.
   - Shuffle, prev, play/pause, next and repeat in the middle, with the seek bar under them.
   - The volume slider on the right.
   - Exit focus at the top right.

**Done when:** focus mode follows track changes within about a second, and its CPU use stays reasonable. Measure it with `top`.

## Step 5: theme sync

1. (As built: omaspot reads the theme's `colors.toml` itself, see `omaspot/theme.py`; the hook `hooks/omaspot-theme` only recolours the kitty window and signals omaspot. Original plan:) `hooks/theme-set.d/omaspot` reads `/usr/share/omarchy/themes/<theme>/colors.toml` and writes `~/.config/omaspot/theme.tcss`. It also rewrites the colours section of `kitty-overlay.conf`.
2. It tells a running omaspot to reload, for example with `pkill -USR1 -f omaspot`.
3. Install it to `~/.config/omarchy/hooks/theme-set.d/` (use the `omarchy` skill).

**Done when:** `omarchy theme set <other>` recolours the open overlay.

## Step 6: Bar B

1. With Soloist playing, run `playerctl -l`. If Soloist shows up as an MPRIS player, the Bar B music widget may already work.
2. If it doesn't show up, have omaspot write now-playing to the shared bridge file in `~/.config/omarchy/qml/gcprashant555/`. Earlier Bar B fixes used the same approach, because Omarchy doesn't let a custom bar connect to a plugin's service.
