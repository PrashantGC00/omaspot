# Omaspot

A drop-down terminal Spotify player for [Omarchy](https://omarchy.org/). Press **Super+M** and **omaspot** drops
over your workspace, laid out like a desktop music app: Home, search, your playlists, Liked Songs, albums,
artists, a queue panel and a player bar. Press **f** for **Turntable**: the album sleeve with a pixel-art record
sliding out of it and spinning at 33⅓ rpm, a visualizer horizon in the album's colours, big type and "up next".

Audio comes from **Spotify Soloist**, Spotify's official headless Spotify Connect client. omaspot only *controls*
playback (Web API + `soloist ctl`); it never streams audio itself, so it works for accounts that librespot-based
players can't play on.

## What you get

- **omaspot**: Python + [Textual](https://textual.textualize.io/) app running in kitty, with real cover art via the
  kitty graphics protocol, the accent colour taken from the playing album, and the Omarchy theme followed live.
- **Omarchy plugin** `PrashantGC00.omaspot`: a bar widget, a record that spins while music plays. Left click
  opens omaspot, middle-click plays/pauses, scroll skips, right-click opens a popup with controls.
- **Media keys**: `soloist-mpris` registers Soloist as an MPRIS player, so the laptop's play/pause/next keys,
  Omarchy's media OSD and media bar widgets all control it, with or without omaspot open.
- **Soloist that doesn't expire**: Soloist builds stop working 90 days after they're built. `soloist-update`
  checks Spotify's CDN weekly (and whenever Soloist exits as expired), swaps in a new build and restarts it.

## Requirements

- Omarchy (Hyprland with the Lua config) on an **x86_64** PC; kitty and JetBrainsMono Nerd Font (Omarchy ships both)
- Spotify **Premium**: Soloist only plays for Premium accounts
- **Your own Spotify developer app**, made at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard): its
  **Client ID**, with the redirect URI `http://127.0.0.1:8888/callback`. Everyone makes their own app (free, a
  couple of minutes); it stays in Spotify's development mode, which is meant for personal use like this.
- A **Soloist API key** from the same dashboard. Soloist is Spotify's official headless player; see Spotify's
  [Soloist getting-started guide](https://developer.spotify.com/documentation/soloist/tutorials/getting-started).
  If your dashboard doesn't offer Soloist, Omaspot can't play: it has no audio of its own.
- A phone or computer with Spotify, to pair with Soloist once (the installer waits for it)

Omaspot only controls Spotify through the Web API, so it follows Spotify's rules for it: Spotify-made playlists
(Discover Weekly, Daily Mix) and other people's playlists can't be opened (see Limits).

## Install

Omaspot is an Omarchy plugin:

```bash
omarchy plugin add https://github.com/PrashantGC00/omaspot
```

Say yes to enabling it, and a record icon appears in your top bar. **Click it**: a popup explains Omaspot and has an
**Install** button, which runs the guided setup below in a terminal. When it finishes, the icon becomes Omaspot's
bar widget by itself.

(Or from a clone: `git clone … && cd omaspot && ./install.sh`.)

The installer is a guided setup. It walks you through everything, asks only for what's missing, and is safe to
run again:

1. **Checks this computer** (Omarchy, Hyprland, x86_64).
2. **Spotify credentials:** opens Spotify's developer dashboard and tells you exactly what to create (an app with
   the redirect URI `http://127.0.0.1:8888/callback`), then asks for its **Client ID** and a **Soloist API key**.
   They're saved to `~/.config/soloist/.env`, readable only by you.
3. **Installs** packages, Soloist, the background services, Super+M, the theme hook, the bar widget and the app
   launcher entry. Configs are backed up to `~/.config/omarchy/backups/omaspot-YYYYMMDD/` first.
4. **Connects Soloist:** stores the API key, offers the firewall rules your phone needs (if `ufw` is on), and waits
   while you pair once: in Spotify on your phone, tap the devices icon and pick **Omarchy**.
5. **Signs you in** to Spotify in the browser.
6. **Checks** that every piece works and says how to fix anything that doesn't.

Afterwards: `./install.sh --check` re-runs the check, `./install.sh --uninstall` removes Omaspot (keeping Soloist,
your credentials and sign-in), and `./install.sh --yes` installs without questions. You need **Spotify Premium**.

## Use

| Key | Action | Key | Action |
|---|---|---|---|
| Super+M | show / hide omaspot (or open **Omaspot** from the app launcher) | `/` | search |
| space | play / pause | `n` `p` | next / previous |
| `,` `.` | back / forward 10 s | `+` `-` | volume |
| `s` `r` | shuffle / repeat | `z` | add song to queue |
| `h` `l` `d` | Home / Liked Songs / Devices | `u` | queue panel |
| `f` | Turntable view | alt+← alt+→ | back / forward |
| `?` | all shortcuts | `esc` | leave Turntable / hide |

**The bar widget** shows a record that spins while Spotify plays and a scrolling "title · artist" for whatever is
playing (any media player). Left click opens Omaspot, middle click plays/pauses, scroll changes the track, right click
opens a popup with the cover, controls, the list of players, **Open Omaspot** and **Check setup**.

Mouse works everywhere: double-click plays, right-click opens a menu, click the artist or album to open it.

**Preferences** (in the sidebar) switches features on and off and sets the window's opacity and background
colour: album colours, header wash, music waves and their strength, Turntable's beat pulse, smooth scrolling and
song notifications, and **keyboard shortcuts** (change any key: pick the action, press the new key). Changes apply at once
and are saved to `~/.config/omaspot/settings.json`.

`soloist-update --status` shows how many days the installed Soloist build has left.

## Layout

```
install.sh         installer (idempotent; --uninstall)
omaspot/             the app: bin/omaspot launcher, omaspot/ package, tests/
manifest.json, BarWidget.qml, i18n/   the Omarchy plugin (this repo is the plugin)
soloist/           systemd units, soloist-update, the MPRIS bridge unit, fixport.c (pins the pairing port)
overlay/           omaspot-overlay script, kitty config, Hyprland rules + Super+M (omaspot.lua)
hooks/             Omarchy theme-set hook
docs/              BUILD.md (how it was built, step by step), design.html
```

Tests: `cd omaspot && python3 -m unittest discover -s tests`

## Limits

Spotify restricts new developer apps: playlists made by Spotify (Discover Weekly, Daily Mix) and other users'
playlists can't be read, and the queue can't be reordered. omaspot shows a message instead of an empty list.
The Soloist archive is downloaded from Spotify and never included in this repository.
