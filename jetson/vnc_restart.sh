#!/usr/bin/env bash
# Clean restart of the remote desktop, for when the browser connects and the
# screen stays black.
#
#     ~/ter_go2/vnc_restart.sh                on the Jetson
#     ssh go2jetson '~/ter_go2/vnc_restart.sh'   from the PC
#
# CLOSE THE BROWSER TAB FIRST. That is the whole point of this script.
#
# The failure it fixes: a browser retrying against a session that will not
# start fills x11vnc's accept queue with connections that never complete the
# RFB handshake (seen climbing to 7). x11vnc then goes into a livelock at ~95%
# CPU: it accepts the TCP connection, logs "Got connection from client", and
# never sends another byte. From the outside it looks exactly like a network
# problem, and it is not.
#
# Restarting the service alone does not help, because the browser starts
# hammering it again immediately. Everything has to come down, the stale
# processes have to go, and x11vnc has to come up with nobody attached.
#
# Verified: after a clean start x11vnc answers RFB 003.008 at 0.1% CPU, and
# stays healthy when websockify comes back.

SUDO="sudo"
[ -n "$SUDO_ASKPASS" ] && SUDO="sudo -A"

echo "vnc_restart: stopping both services..."
$SUDO systemctl stop go2-novnc go2-x11vnc
sleep 3

# systemd may leave a wedged process behind: it is spinning, not blocked, so it
# does not always die on SIGTERM.
if pgrep -f "[x]11vnc" >/dev/null 2>&1; then
    echo "vnc_restart: killing a leftover x11vnc..."
    $SUDO pkill -9 -f "[x]11vnc"
    sleep 2
fi
echo "vnc_restart: leftover processes: $(pgrep -cf '[x]11vnc')"

echo "vnc_restart: starting x11vnc with nobody attached..."
$SUDO systemctl start go2-x11vnc
sleep 10

PID=$(pgrep -f "[x]11vnc" | head -1)
CPU=$(ps -p "$PID" -o pcpu= 2>/dev/null | tr -d ' ')
echo "vnc_restart: x11vnc pid=$PID cpu=${CPU}%"

# Check it really answers, rather than assuming: this is the exact failure mode
# we are recovering from, and it is invisible from the outside.
python3 - <<'PY'
import socket
try:
    s = socket.create_connection(('127.0.0.1', 5900), timeout=8)
    data = s.recv(24)
    s.close()
    print(f"vnc_restart: x11vnc says {data!r} -> "
          + ("OK" if b'RFB' in data else "NOT the RFB greeting"))
except Exception as exc:
    print(f"vnc_restart: x11vnc did NOT answer ({exc}). Still wedged.")
PY

echo "vnc_restart: starting noVNC..."
$SUDO systemctl start go2-novnc
sleep 6

echo
systemctl is-active go2-x11vnc go2-novnc
ss -tln 2>/dev/null | grep -E ":(5900|6080)"
cat <<'DONE'

Ready. From the PC:

    ssh -L 6080:localhost:6080 go2jetson
    http://localhost:6080/vnc.html

Open the page fresh (Ctrl+Shift+R) rather than reusing a tab that was retrying.
DONE
