import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Services.Mpris
import qs.Ui
import qs.Commons
import qs.Commons as Commons

// Omaspot's bar widget. Two states:
//   not set up yet (just added with `omarchy plugin add`): a record icon; a click opens a popup with an
//     Install button that runs the guided installer (install.sh) in a terminal. The widget notices the
//     installer's marker file and switches to the normal state by itself.
//   set up: the record (turning at 33⅓ rpm while Spotify/Soloist plays) and a scrolling "title · artist" for
//     whatever is playing (any MPRIS player, the former "Media (fixed marquee)" widget merged in).
//     Left click: open/hide Omaspot · middle: play/pause · wheel: previous/next · right click: the popup
//     (cover, controls, player list, Open Omaspot, Check setup).
BarWidget {
  id: root
  moduleName: "PrashantGC00.omaspot"

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  // ---- paths ----
  readonly property string home: Quickshell.env("HOME")
  readonly property string pluginDir: String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "").replace(/\/$/, "")
  readonly property string overlayCmd: home + "/.local/bin/omaspot-overlay"
  readonly property string terminalCmd: "omarchy-launch-floating-terminal-with-presentation"

  // ---- installed? install.sh writes this marker when it finishes ----
  property bool installed: false
  FileView {
    id: marker
    path: root.home + "/.local/state/omaspot/installed"
    printErrors: false
    watchChanges: true
    onLoaded: root.installed = true
    onLoadFailed: root.installed = false
    onFileChanged: reload()
  }
  Timer {          // the file may not exist yet, so look again every few seconds until it does
    interval: 3000
    running: !root.installed
    repeat: true
    onTriggered: marker.reload()
  }

  function runInTerminal(args) {
    Quickshell.execDetached([root.terminalCmd, args])
  }
  function install() { root.popupOpen = false; root.runInTerminal("'" + root.pluginDir + "/install.sh'") }
  function checkSetup() { root.popupOpen = false; root.runInTerminal("'" + root.pluginDir + "/install.sh' --check") }
  function openOmaspot() { root.popupOpen = false; Quickshell.execDetached([root.overlayCmd, "toggle"]) }

  // ---- media (any MPRIS player; the same small API the stock media widget uses) ----
  readonly property var mediaService: localMedia
  QtObject {
    id: localMedia
    property string preferredKey: ""
    readonly property var players: Mpris.players ? Mpris.players.values : []
    readonly property var sourcePlayers: players.filter(function(p) { return p && (p.trackTitle || p.trackArtist) })
    readonly property var activePlayer: {
      var list = sourcePlayers
      for (var i = 0; i < list.length; i++) if (playerKey(list[i]) === preferredKey) return list[i]
      for (var j = 0; j < list.length; j++) if (list[j].isPlaying) return list[j]
      for (var k = 0; k < list.length; k++) if (playerKey(list[k]) === "org.mpris.MediaPlayer2.soloist") return list[k]
      return list.length > 0 ? list[0] : null
    }
    function playerKey(p) { return p ? String(p.dbusName || p.identity || "") : "" }
    function selectPlayer(key) { preferredKey = String(key || "") }
    function runAction(action, showFeedback, key) {
      var p = activePlayer
      if (key) for (var i = 0; i < sourcePlayers.length; i++) if (playerKey(sourcePlayers[i]) === key) p = sourcePlayers[i]
      if (!p) return
      if (action === "playPause") { if (p.canTogglePlaying) p.togglePlaying() }
      else if (action === "next") { if (p.canGoNext) p.next() }
      else if (action === "previous") { if (p.canGoPrevious) p.previous() }
    }
  }
  readonly property var activePlayer: mediaService.activePlayer
  readonly property var sourcePlayers: mediaService.sourcePlayers
  readonly property var soloist: {
    var list = mediaService.players
    for (var i = 0; i < list.length; i++)
      if (list[i] && String(list[i].dbusName || "") === "org.mpris.MediaPlayer2.soloist") return list[i]
    return null
  }
  readonly property bool spinning: soloist !== null && soloist.isPlaying
  readonly property bool hasMedia: activePlayer !== null && (activePlayer.trackTitle || activePlayer.trackArtist)
  readonly property string title: activePlayer ? (activePlayer.trackTitle || "") : ""
  readonly property string artist: activePlayer ? (activePlayer.trackArtist || "") : ""
  readonly property color fg: root.bar ? root.bar.barForeground : "#cacccc"     // bar is null briefly on reload
  readonly property string fontFamily: root.bar ? root.bar.fontFamily : "JetBrainsMono Nerd Font"

  property bool popupOpen: false
  function close() { popupOpen = false }
  property real maxLabelWidth: 180

  function tooltipText() {
    if (!root.installed) return strings.t("tooltip.setup")
    if (!root.hasMedia) return strings.t("tooltip.idle")
    return strings.t("tooltip.playing").arg(root.title).arg(root.artist)
  }

  implicitWidth: row.implicitWidth + Style.space(14)
  implicitHeight: barSize

  Row {
    id: row
    anchors.centerIn: parent
    spacing: Style.space(6)

    Text {
      id: disc
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: "󰀥"              // U+F0025, a record (Omaspot's icon)
      color: root.spinning || !root.installed ? root.fg : Qt.darker(root.fg, 1.5)
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
      // constant motion, so linear; off when animations are off (Style.duration returns 0)
      RotationAnimation on rotation {
        running: Style.duration(1800) > 0
        paused: !root.spinning
        from: 0; to: 360
        duration: 1800
        loops: Animation.Infinite
      }
    }

    Item {        // the scrolling title (from the "Media (fixed marquee)" widget)
      id: scrollClip
      width: Math.min(root.maxLabelWidth, labelText.implicitWidth)
      height: disc.height
      clip: true
      anchors.verticalCenter: parent.verticalCenter
      visible: root.installed && root.bar && !root.bar.vertical && root.title !== ""

      Text {
        id: labelText
        textFormat: Text.PlainText
        text: root.title + (root.artist ? "  ·  " + root.artist : "")
        color: root.fg
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        anchors.verticalCenter: parent.verticalCenter

        property bool needsScroll: implicitWidth > scrollClip.width
        readonly property bool scrolling: needsScroll && !root.popupOpen && root.bar && !root.bar.vertical

        // starts from the left edge, holds for a beat, scrolls out, comes back in from the right, holds again
        x: 0
        onScrollingChanged: { scrollAnim.stop(); labelText.x = 0; if (scrolling) scrollAnim.start() }
        onTextChanged: { scrollAnim.stop(); labelText.x = 0; if (scrolling) scrollAnim.start() }
        Component.onCompleted: if (scrolling) scrollAnim.start()

        SequentialAnimation {
          id: scrollAnim
          loops: Animation.Infinite
          PauseAnimation { duration: 1800 }
          NumberAnimation {
            target: labelText; property: "x"
            from: 0; to: -labelText.implicitWidth
            duration: Math.max(3000, labelText.implicitWidth * 25)
            easing.type: Easing.Linear
          }
          NumberAnimation {
            target: labelText; property: "x"
            from: scrollClip.width; to: 0
            duration: Math.max(1000, scrollClip.width * 25)
            easing.type: Easing.Linear
          }
        }
      }
    }
  }

  MouseArea {
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton

    onClicked: function(mouse) {
      if (!root.installed || mouse.button === Qt.RightButton) {
        root.popupOpen = !root.popupOpen
      } else if (mouse.button === Qt.MiddleButton) {
        root.mediaService.runAction("playPause", false)
      } else {
        root.openOmaspot()
      }
    }
    onWheel: function(wheel) {
      if (!root.installed || !root.activePlayer) return
      if (wheel.angleDelta.y > 0) root.mediaService.runAction("previous", false)
      else if (wheel.angleDelta.y < 0) root.mediaService.runAction("next", false)
    }
    onEntered: if (root.bar) root.bar.showTooltip(root, root.tooltipText())
    onExited: if (root.bar) root.bar.hideTooltip(root)
  }

  PopupCard {
    id: popup
    anchorItem: root
    bar: root.bar
    owner: root
    open: root.popupOpen
    contentWidth: popup.fittedContentWidth(Style.space(320))
    contentHeight: popup.fittedContentHeight(root.installed ? column.implicitHeight : setup.implicitHeight)

    // ---- not set up yet: what Omaspot is, and the Install button ----
    Column {
      id: setup
      visible: !root.installed
      anchors.fill: parent
      spacing: Style.space(10)

      Row {
        spacing: Style.space(10)
        Text {
          textFormat: Text.PlainText
          text: "󰀥"
          color: Commons.Color.accent
          font.family: root.fontFamily
          font.pixelSize: Style.font.displayLarge
          anchors.verticalCenter: parent.verticalCenter
        }
        Text {
          textFormat: Text.PlainText
          text: strings.t("setup.title")
          color: root.fg
          font.family: root.fontFamily
          font.pixelSize: Style.font.subtitle
          font.bold: true
          anchors.verticalCenter: parent.verticalCenter
        }
      }
      Text {
        textFormat: Text.PlainText
        text: strings.t("setup.body")
        color: Qt.darker(root.fg, 1.2)
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.WordWrap
        width: parent.width
      }
      Text {
        textFormat: Text.PlainText
        text: strings.t("setup.premium")
        color: Qt.darker(root.fg, 1.6)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        width: parent.width
      }
      Button {
        anchors.horizontalCenter: parent.horizontalCenter
        text: strings.t("setup.install")
        foreground: root.fg
        horizontalPadding: Style.spacing.panelGap
        verticalPadding: Style.spacing.controlPaddingY
        onClicked: root.install()
      }
    }

    // ---- set up: now playing, controls, players, Omaspot buttons ----
    Column {
      id: column
      visible: root.installed
      anchors.fill: parent
      spacing: Style.space(10)

      Row {
        spacing: Style.space(10)
        width: parent.width

        BorderSurface {
          width: Style.space(64)
          height: Style.space(64)
          radius: Style.spacing.labelGap
          color: Style.normalFillFor(root.fg, Commons.Color.accent)
          borderSpec: Border.controlSpec("normal", root.fg, Commons.Color.accent)

          Image {
            anchors.fill: parent
            anchors.margins: Style.space(2)
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            source: root.activePlayer && root.activePlayer.trackArtUrl ? root.activePlayer.trackArtUrl : ""
            visible: source !== ""
          }
          Text {
            anchors.centerIn: parent
            visible: !root.activePlayer || !root.activePlayer.trackArtUrl
            textFormat: Text.PlainText
            text: "󰀥"
            color: root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.displayLarge
          }
        }

        Column {
          spacing: Style.space(4)
          width: parent.width - Style.space(74)

          Text {
            textFormat: Text.PlainText
            text: root.title || strings.t("media.nothing")
            color: root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.subtitle
            font.bold: true
            elide: Text.ElideRight
            width: parent.width
          }
          Text {
            textFormat: Text.PlainText
            text: root.artist
            color: Qt.darker(root.fg, 1.3)
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            elide: Text.ElideRight
            width: parent.width
            visible: text !== ""
          }
          Text {
            textFormat: Text.PlainText
            text: root.activePlayer && root.activePlayer.trackAlbum ? root.activePlayer.trackAlbum : ""
            color: Qt.darker(root.fg, 1.6)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
            width: parent.width
            visible: text !== ""
          }
        }
      }

      Row {
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: Style.space(6)

        Button {
          iconText: "󰒮"
          foreground: root.fg
          horizontalPadding: Style.spacing.controlPaddingX
          verticalPadding: Style.spacing.controlPaddingY
          enabled: root.activePlayer && root.activePlayer.canGoPrevious
          opacity: enabled ? 1.0 : 0.4
          onClicked: root.mediaService.runAction("previous", false, root.mediaService.playerKey(root.activePlayer))
        }
        Button {
          iconText: root.activePlayer && root.activePlayer.isPlaying ? "󰏤" : "󰐊"
          foreground: root.fg
          horizontalPadding: Style.spacing.panelGap
          verticalPadding: Style.spacing.controlPaddingY
          iconSize: Style.font.iconLarge
          enabled: root.activePlayer && (root.activePlayer.canTogglePlaying || root.activePlayer.canPlay || root.activePlayer.canPause)
          opacity: enabled ? 1.0 : 0.4
          onClicked: root.mediaService.runAction("playPause", false, root.mediaService.playerKey(root.activePlayer))
        }
        Button {
          iconText: "󰒭"
          foreground: root.fg
          horizontalPadding: Style.spacing.controlPaddingX
          verticalPadding: Style.spacing.controlPaddingY
          enabled: root.activePlayer && root.activePlayer.canGoNext
          opacity: enabled ? 1.0 : 0.4
          onClicked: root.mediaService.runAction("next", false, root.mediaService.playerKey(root.activePlayer))
        }
      }

      PanelSeparator {
        visible: root.sourcePlayers.length > 1
        foreground: root.fg
      }

      Column {
        id: sourceList
        visible: root.sourcePlayers.length > 1
        width: parent.width
        spacing: Style.space(4)

        Repeater {
          model: root.sourcePlayers

          BorderSurface {
            id: sourceRow
            required property var modelData

            readonly property var player: modelData
            readonly property bool selected: root.activePlayer && player
              && root.mediaService.playerKey(root.activePlayer) === root.mediaService.playerKey(player)
            readonly property string sourceTitle: player ? (player.trackTitle || player.identity || player.desktopEntry || "Media source") : "Media source"
            readonly property string sourceDetail: player && player.trackArtist ? player.trackArtist : (player && player.identity ? player.identity : "")

            width: sourceList.width
            height: sourceInner.implicitHeight + Style.space(10)
            radius: Style.spacing.labelGap
            color: selected ? Style.selectedFillFor(root.fg, Commons.Color.accent) : "transparent"
            borderSpec: selected ? Border.controlSpec("normal", root.fg, Commons.Color.accent) : Border.none()

            Row {
              id: sourceInner
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: sourceRow.borderLeft + Style.space(8)
              anchors.rightMargin: sourceRow.borderRight + Style.space(8)
              spacing: Style.space(8)

              Text {
                textFormat: Text.PlainText
                text: sourceRow.player && sourceRow.player.isPlaying ? "󰏤" : "󰐊"
                color: root.fg
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                width: Style.space(18)
                horizontalAlignment: Text.AlignHCenter
                anchors.verticalCenter: parent.verticalCenter
              }
              Column {
                width: parent.width - Style.space(26)
                spacing: Style.space(1)
                anchors.verticalCenter: parent.verticalCenter
                Text {
                  textFormat: Text.PlainText
                  text: sourceRow.sourceTitle
                  color: root.fg
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: sourceRow.selected
                  elide: Text.ElideRight
                  width: parent.width
                }
                Text {
                  textFormat: Text.PlainText
                  text: sourceRow.sourceDetail
                  color: Qt.darker(root.fg, 1.5)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                  width: parent.width
                  visible: text !== ""
                }
              }
            }

            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: root.mediaService.selectPlayer(root.mediaService.playerKey(sourceRow.player))
            }
          }
        }
      }

      PanelSeparator { foreground: root.fg }

      Row {
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: Style.space(8)
        Button {
          iconText: "󰀥"
          text: strings.t("media.open")
          foreground: root.fg
          horizontalPadding: Style.spacing.controlPaddingX
          verticalPadding: Style.spacing.controlPaddingY
          onClicked: root.openOmaspot()
        }
        Button {
          iconText: "󰄬"
          text: strings.t("media.check")
          foreground: root.fg
          horizontalPadding: Style.spacing.controlPaddingX
          verticalPadding: Style.spacing.controlPaddingY
          onClicked: root.checkSetup()
        }
      }
    }
  }
}
