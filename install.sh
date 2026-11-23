#!/bin/bash
# Omaspot installer: a guided setup from nothing to music playing. Safe to run again: done steps are skipped,
# files are only changed when they differ, and configs are backed up first.
#
#   ./install.sh              guided install / update (asks only for what's missing)
#   ./install.sh --check      check that everything works and say how to fix what doesn't
#   ./install.sh --yes        no questions (for scripts); prints what's left to do by hand
#   ./install.sh --uninstall  remove Omaspot (keeps Soloist, its login, your Spotify credentials and sign-in)
#
# The steps: 1 check this computer · 2 Spotify credentials (~/.config/soloist/.env) · 3 install files and services
# · 4 connect Soloist (API key, pairing, optional firewall rules) · 5 sign in to Spotify · 6 final check.
#
# What step 3 sets up:
#   packages   python-textual python-httpx python-pillow python-dbus-fast kitty cava jq (only missing ones)
#   commands   ~/.local/bin/{omaspot, omaspot-overlay, soloist-update}   (omaspot and soloist-update link here)
#   soloist    downloads the official build if it isn't installed (soloist-update)
#   services   soloist, soloist-mpris (media keys / MPRIS), soloist-update.timer (weekly; also on exit 10)
#   overlay    ~/.config/omaspot/kitty-overlay.conf, ~/.config/hypr/omaspot.lua + require in hyprland.lua (Super+M)
#   theme      the theme-set hook that recolours omaspot and its window
#   plugin     ~/.config/omarchy/plugins/PrashantGC00.omaspot, on the bar
#   launcher   ~/.local/share/applications/omaspot.desktop + icon, so "Omaspot" opens from the app launcher
# Configs are backed up to ~/.config/omarchy/backups/omaspot-YYYYMMDD/ before they're changed.
set -euo pipefail

REPO="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PLUGIN_ID="PrashantGC00.omaspot"
OLD_PLUGIN_ID="gcprashant555.omaspot"     # the id until 2026-11-23 (renamed to the GitHub username)
OLD_SPOT=""
PLUGIN_DIR="$HOME/.config/omarchy/plugins/$PLUGIN_ID"
UNITS="$HOME/.config/systemd/user"
BIN="$HOME/.local/bin"
HYPR="$HOME/.config/hypr"
BACKUP="$HOME/.config/omarchy/backups/omaspot-$(date +%Y%m%d)"

say() { printf '\033[1m›\033[0m %s\n' "$*"; }
backup() {     # backup <file>: once per day per file
    [ -e "$1" ] || return 0
    mkdir -p "$BACKUP"
    [ -e "$BACKUP/$(basename "$1")" ] || cp -a "$1" "$BACKUP/"
}
put() {        # put <src> <dst> [mode]: copy only when different, backing up what it replaces
    if ! cmp -s "$1" "$2" 2>/dev/null; then
        backup "$2"
        install -Dm"${3:-644}" "$1" "$2"
        say "updated ${2/#$HOME/\~}"
    fi
}
link() {       # link <target> <name>
    if [ "$(readlink -f "$2" 2>/dev/null)" != "$(readlink -f "$1")" ]; then
        backup "$2"
        mkdir -p "$(dirname "$2")"
        ln -sfn "$1" "$2"
        say "linked ${2/#$HOME/\~}"
    fi
}

ENV="$HOME/.config/soloist/.env"
TOKEN="$HOME/.cache/omaspot/token.json"
MARKER="$HOME/.local/state/omaspot/installed"      # the bar widget switches from "Install" to its normal mode
DASHBOARD="https://developer.spotify.com/dashboard"
SOLOIST_GUIDE="https://developer.spotify.com/documentation/soloist/tutorials/getting-started"
REDIRECT="http://127.0.0.1:8888/callback"
ASK=1                                   # 0: never prompt (--yes, or not in a terminal)
[ -t 0 ] && [ -t 1 ] || ASK=0

bold() { printf '\033[1m%s\033[0m' "$*"; }
step() { printf '\n\033[1;38;5;173m%s\033[0m  \033[1m%s\033[0m\n' "$1" "$2"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
hint() { printf '    \033[2m%s\033[0m\n' "$*"; }
open_url() { command -v xdg-open >/dev/null && xdg-open "$1" >/dev/null 2>&1 & }
confirm() {    # confirm <question> [default y|n]
    [ "$ASK" = 1 ] || { [ "${2:-n}" = y ]; return; }
    local a; read -r -p "  $1 [$( [ "${2:-n}" = y ] && echo Y/n || echo y/N )] " a
    a=${a:-${2:-n}}; [[ "$a" =~ ^[Yy] ]]
}
env_get() { { sed -n "s/^$1=//p" "$ENV" 2>/dev/null || true; } | tr -d '"\r\n '"'"; }   # empty if missing

uninstall() {
    say "stopping services"
    systemctl --user disable --now soloist-mpris.service soloist-update.timer 2>/dev/null || true
    rm -f "$UNITS/soloist-mpris.service" "$UNITS/soloist-update.service" "$UNITS/soloist-update.timer"
    systemctl --user daemon-reload
    shell="$HOME/.config/omarchy/shell.json"
    if grep -q "\"$PLUGIN_ID\"" "$shell" 2>/dev/null; then         # there's no `omarchy bar remove`
        backup "$shell"
        jq --arg id "$PLUGIN_ID" '.bar.layout |= map_values(if type == "array" then map(select(.id != $id)) else . end)' \
            "$shell" > "$shell.tmp" && mv "$shell.tmp" "$shell"
    fi
    rm -f "$MARKER"
    if [ "$(readlink -f "$REPO")" = "$(readlink -f "$PLUGIN_DIR")" ]; then
        say "the bar widget stays (this folder is the plugin): remove it with  omarchy plugin remove $PLUGIN_ID"
    else
        omarchy plugin disable "$PLUGIN_ID" >/dev/null 2>&1 || true
        rm -rf "$PLUGIN_DIR"
    fi
    if grep -q 'require("hypr.omaspot")' "$HYPR/hyprland.lua" 2>/dev/null; then
        backup "$HYPR/hyprland.lua"
        sed -i '/require("hypr.omaspot")/d' "$HYPR/hyprland.lua"
    fi
    rm -f "$HOME/.local/share/applications/omaspot.desktop" "$HOME/.local/share/icons/hicolor/scalable/apps/omaspot.svg"
    rm -f "$HYPR/omaspot.lua" "$HOME/.config/omarchy/hooks/theme-set.d/omaspot-theme" "$BIN/omaspot-overlay" "$BIN/omaspot" "$BIN/soloist-update"
    say "removed (Soloist itself, ~/.config/soloist and omaspot's sign-in are kept; backups in ${BACKUP/#$HOME/\~})"
}

install_files() {
    # ---- migrate from the old name (vinyl, before 2026-11-22) ----
    migrate() {
        local old_plugin="$HOME/.config/omarchy/plugins/gcprashant555.vinyl"
        local found=0
        for d in "$HOME/.config/vinyl" "$HOME/.cache/vinyl" "$old_plugin" "$HYPR/vinyl.lua" "$BIN/vinyl" "$BIN/spotify-overlay"; do
            [ -e "$d" ] || [ -L "$d" ] && found=1
        done
        [ "$found" = 1 ] || return 0
        say "migrating from vinyl to Omaspot"
        # the running old window runs code from paths that no longer exist; Soloist keeps playing
        pkill -f 'python3 -m vinyl$' 2>/dev/null || true
        # settings, kitty conf, sign-in token and cover cache move over (kept if the new folder already exists)
        for pair in "$HOME/.config/vinyl:$HOME/.config/omaspot" "$HOME/.cache/vinyl:$HOME/.cache/omaspot"; do
            from=${pair%%:*}; to=${pair#*:}
            if [ -d "$from" ] && [ ! -e "$to" ]; then mv "$from" "$to"; say "moved ${from/#$HOME/\~} to ${to/#$HOME/\~}"
            elif [ -d "$from" ]; then backup "$from"; rm -rf "$from"; fi
        done
        # Hyprland: swap the require in place (its position after the tiling block matters)
        if grep -q 'require("hypr.vinyl")' "$HYPR/hyprland.lua" 2>/dev/null; then
            backup "$HYPR/hyprland.lua"
            sed -i 's/require("hypr\.vinyl")/require("hypr.omaspot")/' "$HYPR/hyprland.lua"
        fi
        [ -e "$HYPR/vinyl.lua" ] && { backup "$HYPR/vinyl.lua"; rm -f "$HYPR/vinyl.lua"; }
        rm -f "$BIN/vinyl" "$HOME/.config/omarchy/hooks/theme-set.d/vinyl-theme"
        [ -e "$BIN/spotify-overlay" ] && { backup "$BIN/spotify-overlay"; rm -f "$BIN/spotify-overlay"; }
        # the old bar plugin: off the bar and gone (the new one is enabled in the same place below)
        if [ -d "$old_plugin" ]; then
            command -v omarchy >/dev/null && omarchy plugin disable gcprashant555.vinyl >/dev/null 2>&1 || true
            shell="$HOME/.config/omarchy/shell.json"
            if grep -q '"gcprashant555.vinyl"' "$shell" 2>/dev/null; then
                backup "$shell"
                jq '.bar.layout |= map_values(if type == "array" then map(select(.id != "gcprashant555.vinyl")) else . end)' \
                    "$shell" > "$shell.tmp" && mv "$shell.tmp" "$shell"
            fi
            rm -rf "$old_plugin"
        fi
    }
    migrate

    # ---- the plugin id changed (gcprashant555.omaspot -> PrashantGC00.omaspot): same spot on the bar ----
    migrate_id() {
        local old="$HOME/.config/omarchy/plugins/$OLD_PLUGIN_ID" shell="$HOME/.config/omarchy/shell.json"
        [ -d "$old" ] || return 0
        say "moving the bar widget from $OLD_PLUGIN_ID to $PLUGIN_ID"
        if grep -q "\"$OLD_PLUGIN_ID\"" "$shell" 2>/dev/null; then
            backup "$shell"
            # its section and the widget before it, so the new id goes back in the same place
            OLD_SPOT=$(jq -r --arg id "$OLD_PLUGIN_ID" '.bar.layout | to_entries[] | select(.value | type == "array")
                | .key as $k | .value as $v | $v | to_entries[] | select(.value.id == $id)
                | "\($k) \(if .key > 0 then $v[.key - 1].id else "-" end)"' "$shell" | head -1)
        fi
        command -v omarchy >/dev/null && omarchy plugin disable "$OLD_PLUGIN_ID" >/dev/null 2>&1 || true
        if grep -q "\"$OLD_PLUGIN_ID\"" "$shell" 2>/dev/null; then
            jq --arg id "$OLD_PLUGIN_ID" '.bar.layout |= map_values(if type == "array" then map(select(.id != $id)) else . end)' \
                "$shell" > "$shell.tmp" && mv "$shell.tmp" "$shell"
        fi
        # a clone made by `omarchy plugin add` under the old id may be what's running this script: keep it then
        if [ "$(realpath "$old")" = "$(realpath "$REPO")" ]; then
            say "remove the old copy afterwards:  omarchy plugin remove $OLD_PLUGIN_ID"
        else
            rm -rf "$old"
        fi
    }
    migrate_id

    # ---- packages ----
    need=()
    for p in python-textual python-httpx python-pillow python-dbus-fast kitty cava jq; do
        pacman -Q "$p" >/dev/null 2>&1 || need+=("$p")
    done
    if [ ${#need[@]} -gt 0 ]; then
        say "installing ${need[*]}"
        omarchy pkg add "${need[@]}"
    fi

    # ---- commands ----
    link "$REPO/omaspot/bin/omaspot" "$BIN/omaspot"
    link "$REPO/soloist/soloist-update" "$BIN/soloist-update"
    put "$REPO/overlay/omaspot-overlay" "$BIN/omaspot-overlay" 755

    # ---- soloist ----
    [ -f "$HOME/.local/lib/soloist-fixport.so" ] || {
        say "building soloist-fixport.so (pins the pairing port)"
        mkdir -p "$HOME/.local/lib"
        cc -shared -fPIC -O2 -o "$HOME/.local/lib/soloist-fixport.so" "$REPO/soloist/fixport.c" -ldl
    }
    for u in soloist.service soloist-update.service soloist-update.timer; do
        put "$REPO/soloist/$u" "$UNITS/$u"
    done
    # the bridge runs omaspot's code from this checkout
    tmp=$(mktemp)
    sed "s|^Environment=PYTHONPATH=.*|Environment=PYTHONPATH=$REPO/omaspot|" "$REPO/soloist/soloist-mpris.service" > "$tmp"
    put "$tmp" "$UNITS/soloist-mpris.service"
    rm -f "$tmp"
    systemctl --user daemon-reload
    if [ ! -x "$BIN/soloist" ]; then
        say "downloading Spotify Soloist"
        "$BIN/soloist-update"
    fi
    systemctl --user enable --now soloist.service soloist-update.timer >/dev/null
    systemctl --user enable soloist-mpris.service >/dev/null
    systemctl --user restart soloist-mpris.service           # picks up new bridge code

    # ---- overlay window + keybind ----
    [ -f "$HOME/.config/omaspot/kitty-overlay.conf" ] || put "$REPO/overlay/kitty-overlay.conf" "$HOME/.config/omaspot/kitty-overlay.conf"
    put "$REPO/overlay/omaspot.lua" "$HYPR/omaspot.lua"
    if ! grep -q 'require("hypr.omaspot")' "$HYPR/hyprland.lua"; then
        backup "$HYPR/hyprland.lua"
        # must load last: Omarchy's familiar-desktop block tiles every window and the overlay's float rule has to win
        printf '\n-- omaspot overlay (Super+M); keep it last so its float rule wins\nrequire("hypr.omaspot")\n' >> "$HYPR/hyprland.lua"
        say "added require(\"hypr.omaspot\") to hyprland.lua"
    fi
    if command -v hyprctl >/dev/null && [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]; then
        hyprctl reload >/dev/null
        errs=$(hyprctl configerrors 2>/dev/null | grep -v '^$' || true)
        [ -z "$errs" ] || [ "$errs" = "no errors" ] || { echo "Hyprland config errors:"; echo "$errs"; }
    fi

    # ---- app launcher entry + icon (Omarchy's launcher lists "Omaspot") ----
    put "$REPO/assets/omaspot.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/omaspot.svg"
    tmp=$(mktemp)
    sed "s|@BIN@|$BIN|" "$REPO/assets/omaspot.desktop" > "$tmp"
    put "$tmp" "$HOME/.local/share/applications/omaspot.desktop"
    rm -f "$tmp"
    command -v update-desktop-database >/dev/null && update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true

    # ---- workspace icon: a record for the Omaspot window in the decent-workspaces bar widget ----
    # That plugin has no user icon map, so a marked rule goes at the top of its IconRules.js (first match wins);
    # a plugin update may drop it, and running install.sh again puts it back.
    ICONRULES="$HOME/.config/omarchy/plugins/io.github.thetrueferret.decent-workspaces/IconRules.js"
    if [ -f "$ICONRULES" ] && ! grep -q 'omaspot' "$ICONRULES"; then
        backup "$ICONRULES"
        sed -i '0,/^var rules = \[$/s//var rules = [\n  { pattern: "^omaspot$", icon: "\\uDB80\\uDC25" },   \/\/ Omaspot (a record) - added by omaspot install.sh/' "$ICONRULES"
        say "added Omaspot's record icon to the workspace indicator"
    fi

    # ---- theme hook ----
    put "$REPO/hooks/omaspot-theme" "$HOME/.config/omarchy/hooks/theme-set.d/omaspot-theme" 755

    # ---- Omarchy plugin ----
    # added with `omarchy plugin add`, this checkout IS the plugin folder; otherwise copy the widget there
    if [ "$(readlink -f "$REPO")" != "$(readlink -f "$PLUGIN_DIR")" ]; then
        mkdir -p "$PLUGIN_DIR/i18n"
        for f in manifest.json BarWidget.qml i18n/en.json; do
            put "$REPO/$f" "$PLUGIN_DIR/$f"
        done
    fi
    if command -v omarchy >/dev/null; then
        omarchy plugin validate "$PLUGIN_DIR" >/dev/null
        omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
        for _ in 1 2 3 4 5; do omarchy plugin list | grep -q "^$PLUGIN_ID " && break; sleep 1; done
        if ! omarchy plugin list | grep -E "^$PLUGIN_ID +enabled" >/dev/null; then
            backup "$HOME/.config/omarchy/shell.json"
            omarchy plugin enable "$PLUGIN_ID" --before gcprashant555.media 2>/dev/null \
                || omarchy plugin enable "$PLUGIN_ID" --section right
            say "enabled the omaspot plugin on the bar"
        fi
        if [ -n "$OLD_SPOT" ]; then                     # back where the old id sat
            local osec oprev
            read -r osec oprev <<< "$OLD_SPOT"
            if [ "$oprev" = "-" ]; then
                omarchy bar move "$PLUGIN_ID" --section "$osec" --index 0 >/dev/null 2>&1 || true
            else
                omarchy bar move "$PLUGIN_ID" --after "$oprev" >/dev/null 2>&1 || true
            fi
        fi
        # Omaspot's widget includes the "Media (fixed marquee)" widget: take its place on the bar, switch it off
        if omarchy plugin list | grep -qE "^gcprashant555.media +enabled"; then
            local shell="$HOME/.config/omarchy/shell.json" sec prev
            backup "$shell"
            # remember where the Media widget sits (its section and the widget just before it), switch it off,
            # then put Omaspot right there (by neighbour: an index would shift if Omaspot sat earlier in the row)
            read -r sec prev < <(jq -r '.bar.layout | to_entries[] | select(.value | type == "array") | .key as $k
                | .value as $v | $v | to_entries[] | select(.value.id == "gcprashant555.media")
                | "\($k) \(if .key > 0 then $v[.key - 1].id else "-" end)"' "$shell" | head -1)
            omarchy plugin disable gcprashant555.media >/dev/null 2>&1 || true
            if [ -n "${sec:-}" ] && [ "$prev" != "$PLUGIN_ID" ]; then
                if [ "$prev" = "-" ]; then
                    omarchy bar move "$PLUGIN_ID" --section "$sec" --index 0 >/dev/null 2>&1 || true
                else
                    omarchy bar move "$PLUGIN_ID" --after "$prev" >/dev/null 2>&1 || true
                fi
            fi
            say "Omaspot's widget replaces the Media widget (turn it back on: omarchy plugin enable gcprashant555.media)"
        fi
    fi
}


# ======================================================================================================
# the guided steps
# ======================================================================================================

preflight() {
    step 1 "Check this computer"
    local fail=0
    if command -v omarchy >/dev/null; then ok "Omarchy"; else bad "Omarchy isn't installed: Omaspot is built for it (omarchy.org)"; fail=1; fi
    if [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]; then ok "Hyprland is running"
    else bad "not inside a Hyprland session: run this from a terminal in your Omarchy desktop"; fail=1; fi
    if [ "$(uname -m)" = x86_64 ]; then ok "x86_64 (Spotify publishes Soloist for it)"
    else bad "$(uname -m): Spotify's Soloist build is x86_64 only"; fail=1; fi
    hint "You need Spotify Premium: Spotify only lets Premium accounts control playback."
    [ "$fail" = 0 ] || { echo; echo "  Fix the above, then run ./install.sh again."; exit 1; }
}

credentials() {
    step 2 "Spotify credentials"
    local id key
    id=$(env_get CLIENT_ID); key=$(env_get SOLOIST_API_KEY)
    if [ -n "$id" ] && [ -n "$key" ]; then
        ok "already saved in ${ENV/#$HOME/\~}"
        return
    fi
    if [ "$ASK" = 0 ]; then
        bad "missing: put CLIENT_ID and SOLOIST_API_KEY in ${ENV/#$HOME/\~} (run ./install.sh in a terminal to be guided)"
        return
    fi
    echo "  Omaspot talks to Spotify with two keys that are yours, from Spotify's developer site."
    echo "  They're saved to ${ENV/#$HOME/\~}, readable only by you, and never leave this computer."
    if [ -z "$id" ]; then
        echo
        echo "  $(bold "a) A Spotify app (for the Client ID)")"
        echo "     1. Sign in at $DASHBOARD and choose $(bold "Create app")."
        echo "     2. Any name and description. Redirect URI, exactly:  $(bold "$REDIRECT")"
        echo "     3. Tick $(bold "Web API"), agree, $(bold Save). Open the app's $(bold Settings) and copy its $(bold "Client ID")."
        confirm "Open the dashboard in your browser now?" y && open_url "$DASHBOARD"
        while :; do
            read -r -p "  Paste the Client ID: " id
            id=$(printf '%s' "$id" | tr -d ' \t')
            [[ "$id" =~ ^[0-9a-fA-F]{32}$ ]] && break
            echo "  That doesn't look right: a Client ID is 32 letters and digits (0-9, a-f)."
        done
    fi
    if [ -z "$key" ]; then
        echo
        echo "  $(bold "b) A Soloist API key")  (Soloist is the Spotify player that plays the music on this computer)"
        echo "     Follow \"API key\" in Spotify's Soloist guide: $SOLOIST_GUIDE"
        confirm "Open the guide in your browser now?" y && open_url "$SOLOIST_GUIDE"
        while :; do
            read -r -s -p "  Paste the Soloist API key (hidden): " key; echo
            key=$(printf '%s' "$key" | tr -d ' \t')
            [ ${#key} -ge 16 ] && break
            echo "  That looks too short; paste the whole key."
        done
    fi
    mkdir -p "$(dirname "$ENV")"
    ( umask 077
      { [ -f "$ENV" ] && grep -v '^\(CLIENT_ID\|SOLOIST_API_KEY\)=' "$ENV"
        printf 'CLIENT_ID=%s\nSOLOIST_API_KEY=%s\n' "$id" "$key"; } > "$ENV.tmp" )
    mv "$ENV.tmp" "$ENV"; chmod 600 "$ENV"
    ok "saved to ${ENV/#$HOME/\~} (only you can read it)"
}

soloist_logged_in() { "$BIN/soloist" ctl status 2>/dev/null | grep -q 'logged in: yes'; }

firewall() {
    # pairing needs mDNS (udp 5353) and Soloist's pinned port (tcp 47621) from the local network
    systemctl is-active --quiet ufw 2>/dev/null || return 0
    local nets n
    nets=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' |
           /usr/bin/python3 -c 'import sys, ipaddress; print(" ".join(sorted({str(ipaddress.ip_interface(l.strip()).network) for l in sys.stdin if l.strip()})))')
    [ -n "$nets" ] || return 0
    if [ -f "$HOME/.config/omaspot/.firewall-done" ]; then ok "firewall allows pairing (set up before)"; return; fi
    if [ "$ASK" = 0 ]; then hint "ufw is on: pairing needs udp 5353 and tcp 47621 from $nets (run ./install.sh in a terminal to add them)"; return; fi
    echo "  Your firewall (ufw) is on. Pairing from your phone needs two rules for your local network ($nets):"
    echo "  udp 5353 (devices finding each other) and tcp 47621 (Soloist). Adding them asks for your password."
    if confirm "Add the firewall rules?" y; then
        for n in $nets; do
            sudo ufw allow from "$n" to any port 5353 proto udp comment "omaspot: Spotify Connect discovery" >/dev/null &&
            sudo ufw allow from "$n" to any port 47621 proto tcp comment "omaspot: Soloist pairing" >/dev/null ||
                { bad "couldn't add the rules"; return; }
        done
        mkdir -p "$HOME/.config/omaspot"; touch "$HOME/.config/omaspot/.firewall-done"
        ok "firewall rules added"
    else
        hint "skipped: without them your phone may not see \"Omarchy\""
    fi
}

connect_soloist() {
    step 4 "Connect Soloist"
    local key i k
    key=$(env_get SOLOIST_API_KEY)
    if [ -n "$key" ] && ! soloist_logged_in; then
        if printf '%s' "$key" | "$BIN/soloist" setup-api-key >/dev/null 2>&1; then ok "API key stored in Soloist"
        else bad "Soloist didn't accept the API key: check it in ${ENV/#$HOME/\~}, then run ./install.sh again"; fi
        systemctl --user restart soloist.service
        sleep 2
    fi
    firewall
    if soloist_logged_in; then ok "Soloist is signed in to your Spotify account"; return; fi
    if [ "$ASK" = 0 ]; then
        bad "Soloist isn't signed in yet: on your phone, open Spotify, tap the devices icon, pick \"Omarchy\""
        return
    fi
    echo "  One-time pairing signs Soloist in with your account:"
    echo "    on your phone (same Wi-Fi as this computer), open Spotify, play anything, tap the $(bold devices) icon,"
    echo "    and pick $(bold Omarchy)."
    echo "  Waiting for it (press $(bold s) to skip and pair later)..."
    for i in $(seq 90); do
        if soloist_logged_in; then ok "paired: Soloist is signed in"; return; fi
        if read -r -s -n 1 -t 2 k && [ "$k" = s ]; then
            hint "skipped: pick \"Omarchy\" in Spotify's devices list any time later"; return
        fi
        : "$i"
    done
    bad "no pairing after 3 minutes: check your phone is on the same network, then pick \"Omarchy\" again"
}

sign_in() {
    step 5 "Sign in to Spotify"
    if [ -s "$TOKEN" ]; then ok "already signed in"; return; fi
    if [ -z "$(env_get CLIENT_ID)" ]; then bad "needs the Client ID from step 2 first"; return; fi
    if [ "$ASK" = 0 ]; then bad "not signed in: run  omaspot --login"; return; fi
    echo "  Your browser opens Spotify's sign-in; approve it, then come back here."
    if "$BIN/omaspot" --login; then ok "signed in"; else bad "sign-in didn't finish: run  omaspot --login  to try again"; fi
}

CHECK_FAILS=0
item() {    # item <label> <test command> <how to fix>
    if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; hint "$3"; CHECK_FAILS=$((CHECK_FAILS + 1)); fi
}

check() {
    CHECK_FAILS=0
    local days
    days=$("$BIN/soloist-update" --status 2>/dev/null | sed -n 's/.*expires in \([0-9-]*\) days.*/\1 days left/p')
    item "Spotify credentials saved" '[ -n "$(env_get CLIENT_ID)" ] && [ -n "$(env_get SOLOIST_API_KEY)" ]' \
         "run ./install.sh in a terminal (step 2)"
    item "Soloist installed${days:+ ($days)}" '"$BIN/soloist" --version' "run: soloist-update --force"
    item "Soloist running" 'systemctl --user is-active soloist.service' "see why: journalctl --user -u soloist -n 30"
    item "Soloist signed in to Spotify" 'soloist_logged_in' \
         "pair once: Spotify on your phone > devices > Omarchy (same Wi-Fi)"
    item "Signed in to Spotify (Omaspot)" '[ -s "$TOKEN" ]' "run: omaspot --login"
    item "Spotify sees the Omarchy device" '"$BIN/omaspot" --check | grep -q "online: True"' \
         "is Soloist running and signed in? then: systemctl --user restart soloist"
    item "Media keys (MPRIS bridge)" 'busctl --user list | grep -q org.mpris.MediaPlayer2.soloist' \
         "run: systemctl --user restart soloist-mpris"
    item "Weekly Soloist updates" 'systemctl --user is-enabled soloist-update.timer' \
         "run: systemctl --user enable --now soloist-update.timer"
    item "Super+M opens Omaspot" 'hyprctl binds -j | grep -q "Omaspot"' \
         "check ~/.config/hypr/hyprland.lua has require(\"hypr.omaspot\"), then: hyprctl configerrors"
    item "Bar widget" 'omarchy plugin list | grep -qE "^$PLUGIN_ID +enabled"' "run: omarchy plugin enable $PLUGIN_ID"
    item "App launcher entry" '[ -f "$HOME/.local/share/applications/omaspot.desktop" ]' "run ./install.sh again"
    [ "$CHECK_FAILS" = 0 ]
}

final_check() {
    step 6 "Final check"
    if check; then
        echo
        echo "  $(bold "All set.") Open Omaspot with $(bold Super+M), or search \"Omaspot\" in the app launcher."
        echo "  Press $(bold "?") inside it for shortcuts; $(bold Preferences) is in its sidebar."
    else
        echo
        echo "  $(bold "Almost there:") fix the items marked ✗ above, then run  ./install.sh --check"
    fi
}

# tests load the functions without running anything: OMASPOT_INSTALL_SOURCED=1 source install.sh
if [ "${OMASPOT_INSTALL_SOURCED:-}" = 1 ]; then return 0 2>/dev/null || exit 0; fi

case "${1:-}" in
    --uninstall) uninstall; exit 0 ;;
    --check)
        if check; then echo; echo "  Everything works."; exit 0; else echo; echo "  Fix the items marked ✗ above."; exit 1; fi ;;
    --yes) ASK=0 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    "") ;;
    *) echo "usage: ./install.sh [--check|--yes|--uninstall|--help]" >&2; exit 2 ;;
esac

echo "$(bold "Omaspot setup")  · a drop-down Spotify player for Omarchy"
preflight
credentials
step 3 "Install Omaspot"
install_files
mkdir -p "$(dirname "$MARKER")"
printf 'installed %s from %s\n' "$(date -Iseconds)" "$REPO" > "$MARKER"
ok "files and services in place"
connect_soloist
sign_in
final_check
