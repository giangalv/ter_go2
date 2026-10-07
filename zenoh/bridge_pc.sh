#!/usr/bin/env bash
# The PC end of the remote ROS link: SSH tunnel to the Jetson + zenoh bridge.
#
#     ~/ter-home/ter_go2/zenoh/bridge_pc.sh [ssh_host]      # default: go2jetson
#
# Leave it running while you work; Ctrl-C closes both. The Jetson must be
# running go2_remote.launch.py. Then, in other terminals:
#
#     source ~/ter-home/ter_go2/setup_go2_remote.bash
#     rviz2 -d ~/ter-home/ter_go2/src/go2_bringup/rviz/go2_remote.rviz
#
# The Jetson's bridge listens on its own localhost only, so the tunnel is the
# way in. On this side the bridge writes the robot's topics into domain 2 on
# localhost (pc.json5) and sends nothing back. See docs/remote-ros.md.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${1:-go2jetson}"
BRIDGE="$HERE/bin/zenoh-bridge-ros2dds"

if [ ! -x "$BRIDGE" ]; then
    echo "bridge_pc: $BRIDGE missing, run $HERE/install_bridge.sh first" >&2
    exit 1
fi

ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=5 -N \
    -L 7447:localhost:7447 "$HOST" &
TUNNEL=$!
trap 'kill $TUNNEL 2>/dev/null' EXIT
sleep 2
if ! kill -0 "$TUNNEL" 2>/dev/null; then
    echo "bridge_pc: SSH tunnel to $HOST failed (is port 7447 already in use?)" >&2
    exit 1
fi
echo "bridge_pc: tunnel to $HOST open, starting the bridge (Ctrl-C to stop)"

# The local DDS side follows pc.json5 (domain 2, localhost only). Clear what
# setup_go2.bash may have exported for the wired link, which would point
# CycloneDDS at the Ethernet interface instead.
unset CYCLONEDDS_URI
export ROS_DOMAIN_ID=2

"$BRIDGE" -c "$HERE/pc.json5"
