#!/usr/bin/env bash
# Remote desktop for the Jetson, in a browser, with nothing to install on the PC.
#
# Run ONCE on the Jetson:
#     ./setup_vnc.sh
#
# How it works: x11vnc attaches to the graphical session that is already running
# (the HDMI one), websockify serves it as a web page through noVNC. Both listen
# on localhost ONLY: you reach them from the PC through an SSH tunnel.
#
# Why not expose them on the network: VNC passwords are limited to eight
# characters and this is a campus network. The tunnel reuses the SSH key that is
# already configured and costs nothing extra.
#
# From the PC:
#     ssh -L 6080:localhost:6080 go2jetson
#     then open  http://localhost:6080/vnc.html
#
# This replaces NoMachine, whose 8.13 server rejects the sessions requested by
# v10 clients ("Wrong session type physicalDesktop").

set -e

# `sudo` without a terminal (for instance over a non-interactive ssh) cannot
# prompt for a password. When SUDO_ASKPASS is set we use `sudo -A`, which gets
# it from a helper. Normal interactive use keeps plain `sudo`.
SUDO="sudo"
[ -n "$SUDO_ASKPASS" ] && SUDO="sudo -A"

VNC_PORT=5900
WEB_PORT=6080
SCREEN_MODE=1600x900

if [ "$(id -u)" -eq 0 ]; then
    echo "Do not run this as root: it needs the user's graphical session." >&2
    exit 1
fi

echo "== 1. packages =="
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq x11vnc novnc websockify

# noVNC's path differs between distributions.
NOVNC_DIR=""
for d in /usr/share/novnc /usr/share/webapps/novnc; do
    [ -f "$d/vnc.html" ] && NOVNC_DIR="$d" && break
done
if [ -z "$NOVNC_DIR" ]; then
    echo "noVNC is installed but vnc.html was not found." >&2
    exit 1
fi
echo "   noVNC in $NOVNC_DIR"

echo "== 2. which graphical session to attach to =="
# The reliable source is Xorg's own command line: the display and the
# authorisation file differ between the login greeter (user gdm, uid 124) and a
# real user session, and "-auth guess" fails when x11vnc is started by systemd,
# which has no session context.
XLINE=$(ps -eo args | grep "[X]org" | head -1)
XAUTH=$(echo "$XLINE" | grep -oE '\-auth [^ ]+' | awk '{print $2}')
DISPLAY_NUM=$(who | grep -oE '\(:[0-9]+\)' | head -1 | tr -d '():')
DISPLAY_NUM=":${DISPLAY_NUM:-0}"

if [ -z "$XAUTH" ] || ! echo "$XAUTH" | grep -q "/run/user/$(id -u)/"; then
    echo
    echo "WARNING: no graphical session for user $USER." >&2
    echo "Xorg was started with: ${XAUTH:-(no -auth)}" >&2
    echo "If that path contains /run/user/124/ the machine is sitting at the" >&2
    echo "login screen. A headless robot needs automatic login:" >&2
    echo "  in /etc/gdm3/custom.conf, section [daemon], add" >&2
    echo "      AutomaticLoginEnable=true" >&2
    echo "      AutomaticLogin=$USER" >&2
    echo "  then:  sudo systemctl restart gdm3" >&2
    exit 1
fi
echo "   display: $DISPLAY_NUM"
echo "   xauthority: $XAUTH"

echo "== 3. services =="
# Deliberately no -noxdamage: it disables the XDAMAGE extension and forces a
# full framebuffer re-read on every pass, for no benefit here. (It was also
# suspected of causing the x11vnc livelock — that turned out to be wrong, see
# docs/remote-desktop.md, but it was dropped anyway.)
#
# ExecStartPre forces the resolution: with no HDMI attached the NVIDIA driver
# finds no EDID and settles on 1024x768 (or 640x480 without the ConnectedMonitor
# option in xorg.conf), which is too small to use RViz. See docs/remote-desktop.md.
$SUDO tee /etc/systemd/system/go2-x11vnc.service >/dev/null <<UNIT
[Unit]
Description=x11vnc on the Jetson's graphical session
# Deliberately NO After=/Wants=graphical.target. Combined with a WantedBy on the
# same target that forms an ordering cycle, and systemd breaks it by silently
# dropping the dependent job — which is why go2-novnc never started at boot,
# without a single log line. Waiting for the X socket below does the same job
# with no ordering dependency at all.
# StartLimitIntervalSec belongs in [Unit]: in [Service] systemd ignores it
# ("Unknown key name") and the default 5-tries-in-10s limit silently applies.
StartLimitIntervalSec=0

[Service]
Type=simple
User=$USER
# DISPLAY is as necessary as XAUTHORITY: without it xrandr does not know which
# screen to act on and fails silently behind the "|| true".
Environment=DISPLAY=$DISPLAY_NUM
Environment=XAUTHORITY=$XAUTH
ExecStartPre=/bin/sh -c 'for i in \$(seq 1 90); do [ -e /tmp/.X11-unix/X${DISPLAY_NUM#:} ] && exit 0; sleep 2; done; exit 0'
ExecStartPre=/bin/sh -c '/usr/bin/xrandr --output DP-0 --mode $SCREEN_MODE || true'
ExecStart=/usr/bin/x11vnc -display $DISPLAY_NUM -auth $XAUTH -forever -shared -localhost -rfbport $VNC_PORT -nopw
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

$SUDO tee /etc/systemd/system/go2-novnc.service >/dev/null <<UNIT
[Unit]
Description=noVNC: the VNC session served as a web page
After=go2-x11vnc.service
Requires=go2-x11vnc.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=$USER
# websockify connects to the VNC port at startup: if x11vnc has not bound it
# yet the process exits, and with a restart limit it gives up for good.
# Check that the port is LISTENING, without connecting to it. The previous probe
# opened a connection, wrote a newline and closed it without the RFB handshake,
# and that alone put x11vnc 0.9.16 into its livelock at every boot: 100% CPU, no
# answer to anyone (reproduced on 2026-10-08, see docs/remote-desktop.md).
ExecStartPre=/bin/sh -c 'for i in \$(seq 1 90); do ss -ltn "sport = :$VNC_PORT" | grep -q LISTEN && exit 0; sleep 2; done; exit 0'
ExecStart=/usr/bin/websockify --web=$NOVNC_DIR 127.0.0.1:$WEB_PORT 127.0.0.1:$VNC_PORT
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

echo "== 4. screen lock off =="
# On a headless robot the session locks after a few minutes and the VNC view
# shows the lock screen instead of whatever is running.
gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true
gsettings set org.gnome.desktop.screensaver idle-activation-enabled false 2>/dev/null || true
gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true

echo "== 5. services =="
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now go2-x11vnc.service go2-novnc.service
sleep 3

echo "== 6. check =="
systemctl is-active go2-x11vnc.service go2-novnc.service
ss -tlnp 2>/dev/null | grep -E ":($VNC_PORT|$WEB_PORT)" || true

cat <<DONE

Ready. From the PC:

    ssh -L $WEB_PORT:localhost:$WEB_PORT go2jetson

then in the browser:

    http://localhost:$WEB_PORT/vnc.html

In the remote session, to visualise the robot:

    source ~/ter_go2/setup_jetson.bash
    ros2 launch go2_bringup go2_view_jetson.launch.py

DONE
