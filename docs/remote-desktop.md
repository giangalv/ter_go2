# Viewing the Jetson's RViz2 from the PC

**Working.** RViz runs on the Jetson and is viewed in a browser on the PC.

Only the window's pixels cross the Wi-Fi. This is needed because DDS transport
to the PC over wireless does not work — see the known limitations in the README.

## noVNC: nothing to install on the PC

```bash
# once, on the Jetson
~/ter_go2/setup_vnc.sh
```

It installs `x11vnc` and `noVNC`, attaches them to the graphical session
already running (the HDMI one, typically `:0`) and creates two systemd services
that come back on their own after a reboot.

From the PC:

```bash
ssh -L 6080:localhost:6080 go2jetson
```

then in the browser: `http://localhost:6080/vnc.html`

In the remote session:

```bash
source ~/ter_go2/setup_jetson.bash
ros2 launch go2_bringup go2_view_jetson.launch.py
```

You can also launch it from the same SSH session instead of typing inside the
browser, as long as you point it at the display first:

```bash
export DISPLAY=:0
export XAUTHORITY=/run/user/1000/gdm/Xauthority
```

Without those two lines RViz exits with `could not connect to display`.

## Automatic login: required

On a headless robot nobody logs in. After a reboot the Jetson sits at the GDM
greeter, where the X server runs as user `gdm` (uid 124) with
`-auth /run/user/124/gdm/Xauthority`: x11vnc cannot attach and fails with
`-auth guess: failed`.

Automatic login is therefore needed, in `/etc/gdm3/custom.conf`:

```
[daemon]
AutomaticLoginEnable=true
AutomaticLogin=unitree
```

After `sudo systemctl restart gdm3` the session starts by itself on `:0` with
`-auth /run/user/1000/gdm/Xauthority`. That is the value the service must use;
`setup_vnc.sh` derives it from the Xorg command line, which is the only reliable
source.

The trade-off: anyone with physical access finds a desktop already open. On a
lab robot that is normal practice; to undo it, set
`AutomaticLoginEnable=false` (original kept at
`/etc/gdm3/custom.conf.pre-go2setup`).

## Why the SSH tunnel

x11vnc and websockify listen on **localhost only** (`-localhost`,
`127.0.0.1:6080`). VNC passwords are limited to eight characters and this is a
campus network: exposing a desktop there makes no sense when the SSH key is
already configured and the tunnel costs one extra command. For the same reason
x11vnc runs with `-nopw` — the only way to reach it is already authenticated.

If direct access without a tunnel is ever needed, remove `-localhost` from
`/etc/systemd/system/go2-x11vnc.service` and add a password with
`x11vnc -storepasswd`.

## Three ways this failed to start at boot

All three cost a session each, and all three are fixed in `setup_vnc.sh`.

**An ordering cycle.** `go2-x11vnc` was both `WantedBy=graphical.target` and
`After=graphical.target` — a unit ordered after the target that pulls it in.
systemd tolerates the loop on x11vnc itself but breaks it by dropping the
dependent job:

```
graphical.target: Found ordering cycle on go2-novnc.service/start
Job go2-novnc.service/start deleted to break ordering cycle
```

So noVNC never started, silently, without a single line in its journal. The fix
is to drop the target ordering entirely: `ExecStartPre` already waits for the X
socket, which is what the dependency was trying to express.

**`StartLimitIntervalSec` in the wrong section.** It belongs in `[Unit]`. In
`[Service]` systemd ignores it — it says so, with `Unknown key name` — and the
default limit of five attempts in ten seconds silently applies. websockify
starting before x11vnc had bound port 5900 burned through that budget and the
unit gave up for good.

**A bash-only test under dash.** The readiness check used `/dev/tcp`, which is
a bash feature, in a `/bin/sh -c` — and `/bin/sh` is dash on Ubuntu. The test
always failed, so the wait loop ran its full 180 seconds every time.

## When the page connects but stays black

The browser reports a connection and nothing appears. This is not a network
problem, however much it looks like one.

A browser retrying against a session that will not start fills x11vnc's accept
queue with connections that never complete the RFB handshake — observed
climbing to 7 pending. x11vnc then **livelocks at ~95% CPU**: it accepts the TCP
connection, logs `Got connection from client`, and never sends another byte.

How it was isolated: two x11vnc instances side by side, identical options and
identical environment, differing only in who was connecting to them.

| | CPU | answer |
|---|---|---|
| manual, port 5903, nobody attached | 0.1% | `RFB 003.008` |
| the service, port 5900, browser retrying | 94.9% | timeout |

Restarting the service alone does not fix it, because the browser resumes
hammering immediately. **Close the tab first**, then:

```bash
~/ter_go2/vnc_restart.sh
```

It stops both services, kills any leftover spinning process (it is spinning,
not blocked, so SIGTERM does not always take), brings x11vnc up with nobody
attached, **verifies it actually answers RFB** rather than assuming, and only
then starts noVNC.

Two hypotheses were tried and disproved along the way: `-noxdamage` and the
timing of the `xrandr` call. Neither was the cause. `-noxdamage` was dropped
anyway — it disables XDAMAGE and forces a full framebuffer re-read for nothing.

## Screen lock

GNOME locks the session after a few minutes and the browser then shows the lock
screen rather than RViz. On a headless robot this is pure nuisance:

```bash
gsettings set org.gnome.desktop.screensaver lock-enabled false
gsettings set org.gnome.desktop.screensaver idle-activation-enabled false
gsettings set org.gnome.desktop.session idle-delay 0
```

## Resolution: the Jetson is headless

With no HDMI attached, outputs DP-0 and DP-1 report `disconnected`, the NVIDIA
driver finds no EDID and falls back to **640x480**, where RViz is unusable.

`/etc/X11/xorg.conf` was rewritten (it also had two duplicate `Screen0`
sections, a leftover from `nvidia-xconfig`) with:

```
Section "Device"
    Option "ConnectedMonitor" "DP-0"
    Option "ModeValidation" "AllowNonEdidModes, NoEdidModes"
    Option "AllowEmptyInitialConfiguration" "True"
EndSection
```

That makes DP-0 be treated as connected and populates the mode pool up to
2048x1152. The driver still picks 1024x768, so the resolution is forced once the
session is up: `go2-x11vnc.service` has an `ExecStartPre` running
`xrandr --output DP-0 --mode 1600x900`.

Original kept at `/etc/X11/xorg.conf.pre-headless`.

## Why not NoMachine

The Jetson shipped with NoMachine 8.13.1. The client currently downloadable from
the vendor is version 10, and the two do not get along. Authentication succeeds:

```
NXSERVER User 'unitree' logged in from '130.251.13.105' using NX-password
NXSERVER ERROR! Wrong session type physicalDesktop. Cannot set limits
NXSERVER ERROR! Cannot specify is local node base on ':'
```

then session start fails. The v10 client asks for
`--type="physical-desktop"`, the v8 server reasons in terms of
`physicalDesktop`.

Attempts made and failed:

* restarting the server after the hostname change (a hypothesis, and a wrong
  one: the error is identical);
* locating a v8 client for amd64 or a v10 server for arm64 — the vendor's pages
  generate their links via JavaScript and direct paths return HTML, for every
  combination of version and build suffix tried.

To stay on NoMachine, the route would be downloading the arm64 v10 package from
a browser **on the Jetson itself**, where the page's JavaScript runs.

NoMachine was removed on 2026-08-05 after noVNC was verified. It freed 244 MB
and shut down the service on port 4000.

```bash
sudo systemctl stop nxserver
sudo apt-get remove --purge -y nomachine
sudo rm -rf /usr/NX
```
