#!/usr/bin/env bash
# Adapts jizhang-cmu/autonomy_stack_go2 to run on the Go2's own Jetson.
#
#     ./patch_autonomy_stack.sh              on the Jetson, after cloning the stack
#
# Idempotent: running it twice changes nothing. The original is kept next to the
# file as system_real_robot.launch.orig.
#
# Four changes, all to system_real_robot.launch:
#
# 1. go2_h264_repub (the front camera) ships commented out, and with
#    multicast_iface=enp3s0 — the interface name on the author's external PC.
#    On the Jetson it is eth0. Without this, /camera/image/raw has no publisher
#    and RViz shows "No Image".
#
# 2. sensor_scan_generation ships commented out, so /sensor_scan never exists
#    and the matching RViz display stays empty.
#
# 3. visualization_tools ships commented out, so /explored_areas and
#    /trajectory never exist — two more empty displays.
#
# 4. The RViz node is unconditional. Ours replaces it (go2_autonomy.rviz shows
#    the robot model instead of 1 m axes), so it needs an `if` to be switchable
#    off from our launch files.
#
# None of this is a fault in the upstream repo: it ships configured for the
# author's own setup, an external PC talking to the robot over Ethernet.

set -e

STACK="${1:-$HOME/autonomy_stack_go2}"
LAUNCH="$STACK/src/base_autonomy/vehicle_simulator/launch/system_real_robot.launch"

if [ ! -f "$LAUNCH" ]; then
    echo "patch_autonomy_stack: $LAUNCH not found." >&2
    echo "  Pass the stack directory as the first argument if it is elsewhere." >&2
    exit 1
fi

[ -f "$LAUNCH.orig" ] || cp "$LAUNCH" "$LAUNCH.orig"

python3 - "$LAUNCH" <<'PY'
import sys
import pathlib

p = pathlib.Path(sys.argv[1])
t = p.read_text()
before = t

# 1. camera, uncommented and pointed at the Jetson's own interface
t = t.replace('''  <!-- <node pkg="go2_h264_repub" exec="go2_h264_repub" name="go2_h264_repub" output="screen">
    <param name="multicast_iface" value="enp3s0"/>
  </node> -->''',
'''  <node pkg="go2_h264_repub" exec="go2_h264_repub" name="go2_h264_repub" output="screen">
    <param name="multicast_iface" value="eth0"/>
  </node>''')

# 2. sensor_scan_generation
t = t.replace(
    '''  <!-- <include file="$(find-pkg-share sensor_scan_generation)/launch/sensor_scan_generation.launch" /> -->''',
    '''  <include file="$(find-pkg-share sensor_scan_generation)/launch/sensor_scan_generation.launch" />''')

# 3. visualization_tools
t = t.replace('''  <!-- <include file="$(find-pkg-share visualization_tools)/launch/visualization_tools.launch" >
    <arg name="world_name" value="$(var world_name)"/>
  </include> -->''',
'''  <include file="$(find-pkg-share visualization_tools)/launch/visualization_tools.launch" >
    <arg name="world_name" value="$(var world_name)"/>
  </include>''')

# 4. make their RViz switchable, so ours can take over
if 'if="$(var rvizGA)"' not in t:
    t = t.replace(
        '''  <node launch-prefix="nice" pkg="rviz2" exec="rviz2" name="rvizGA" args="-d $(find-pkg-share vehicle_simulator)/rviz/vehicle_simulator.rviz"/>''',
        '''  <arg name="rvizGA" default="true"/>
  <node launch-prefix="nice" pkg="rviz2" exec="rviz2" name="rvizGA" if="$(var rvizGA)" args="-d $(find-pkg-share vehicle_simulator)/rviz/vehicle_simulator.rviz"/>''')

if t == before:
    print("  already patched, nothing to do")
else:
    p.write_text(t)
    print("  patched")
PY

echo
echo "state of each change:"
for key in go2_h264_repub sensor_scan_generation visualization_tools 'var rvizGA'; do
    if grep -q "$key" "$LAUNCH"; then
        line=$(grep -m1 -- "$key" "$LAUNCH")
        case "$line" in
            *'<!--'*) state="STILL COMMENTED" ;;
            *)        state="active" ;;
        esac
    else
        state="not found"
    fi
    printf "  %-24s %s\n" "$key" "$state"
done

echo
echo "Rebuild the stack for the launch changes to take effect:"
echo "    cd $STACK && colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release"
