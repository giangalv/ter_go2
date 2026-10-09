#!/usr/bin/env bash
# Copy this repository to the Jetson and rebuild it there.
#
#     ./deploy.sh                    # over the cable (go2jetson-eth)
#     ./deploy.sh go2jetson          # over Wi-Fi
#     ./deploy.sh --dry-run          # show what would change, touch nothing
#     ./deploy.sh --no-build         # copy only
#
# The Jetson's ~/ter_go2 is a copy, not a clone, laid out differently (README):
#
#     src/go2_bringup, src/go2_description  ->  ~/ter_go2/src/    (built there, Foxy)
#     jetson/*                              ->  ~/ter_go2/        (scripts at the root)
#     zenoh/ (not bin/)                     ->  ~/ter_go2/zenoh/
#
# The unitree_* message packages are not copied: on the Jetson they come from
# ~/unitree_ros2/cyclonedds_ws, and a second copy in this overlay would shadow
# them.
#
# Not updated by this script, because they are installed outside ~/ter_go2 and
# need sudo: /usr/local/sbin/usb_wifi_watchdog.sh, /usr/local/sbin/devmem_write,
# /etc/rc.local, and the systemd units written by setup_vnc.sh. When one of those
# changes, reinstall it explicitly (see docs/jetson.md and docs/wifi.md).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST=go2jetson-eth
DRY=()
BUILD=1
for arg in "$@"; do
    case "$arg" in
        --dry-run)  DRY=(--dry-run) ;;
        --no-build) BUILD=0 ;;
        -*) echo "deploy: unknown option $arg" >&2; exit 2 ;;
        *)  HOST="$arg" ;;
    esac
done

sync() { rsync -az --itemize-changes "${DRY[@]}" "$@"; }

echo "deploy: ter_go2 -> $HOST:~/ter_go2 ${DRY:+(dry run)}"
# --delete inside the packages and zenoh/, which this repo owns entirely;
# __pycache__ and zenoh/bin (downloaded on the Jetson) are left alone.
sync --delete --exclude __pycache__ "$HERE/src/go2_bringup/"     "$HOST:ter_go2/src/go2_bringup/"
sync --delete --exclude __pycache__ "$HERE/src/go2_description/" "$HOST:ter_go2/src/go2_description/"
sync --delete --exclude bin/        "$HERE/zenoh/"               "$HOST:ter_go2/zenoh/"
# No --delete at the root: it also holds build/, install/, log/ and src/.
sync "$HERE/jetson/" "$HOST:ter_go2/"

if [ "${#DRY[@]}" -eq 0 ] && [ "$BUILD" -eq 1 ]; then
    echo "deploy: building on $HOST"
    ssh "$HOST" 'set -e; cd ~/ter_go2
        source /opt/ros/foxy/setup.bash
        source ~/unitree_ros2/cyclonedds_ws/install/setup.bash
        colcon build --packages-select go2_description go2_bringup 2>&1 | tail -3'
fi
