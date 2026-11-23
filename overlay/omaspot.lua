-- omaspot: the Spotify overlay (source: ~/Projects/spotify-overlay/overlay/omaspot.lua).
-- Super+M shows omaspot over the current workspace, tiled like any app, and hides it again. Like Omarchy's
-- Quake console (default/hypr/qconsole.lua), it lives on its own special workspace, so hiding
-- keeps its state and music keeps playing. The first toggle starts it.
local WS = "special:omaspot"
local HOME = os.getenv("HOME")
-- `omaspot-overlay spawn` starts Omaspot only when no window exists anywhere: moving the window into the overlay
-- creates this workspace empty for an instant, and that must not start a second copy. (It execs kitty, so the
-- workspace rule below still applies to the window.)
local LAUNCH = HOME .. "/.local/bin/omaspot-overlay spawn"

-- tiled, like a normal app (user's choice 2026-11-22; it used to float centred at 1280x800)
o.window({ class = "^omaspot$" }, { float = false })

-- Seed the workspace with omaspot when it's created empty (first toggle, or after quitting with q).
-- The exec rule pins the workspace itself, as qconsole does.
hl.on("workspace.created", function(ws)
  if ws and ws.name == WS and ws.is_empty then
    hl.exec_cmd(LAUNCH, { workspace = WS .. " silent" })
  end
end)

-- Replaces Omarchy's default Super+M ("Minimize window to the dock"). The script keeps one window: if Omaspot
-- was opened as an app into a normal workspace, Super+M takes that window into the overlay (no second copy).
o.rebind("SUPER + M", "Omaspot (Spotify)", HOME .. "/.local/bin/omaspot-overlay toggle")
