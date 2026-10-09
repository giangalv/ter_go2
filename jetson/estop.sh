#!/usr/bin/env bash
# Emergency stop for the autonomy stack.
#
#     ~/ter_go2/estop.sh          on the Jetson
#     ssh go2jetson '~/ter_go2/estop.sh'     from the PC
#
# Why this exists: `/api/sport/request` has no arbitration. Any node publishing
# there at 20 Hz wins over everything else — including the teleop node AND the
# Unitree remote controller, which stops responding while the stack is running.
# Verified on the robot: the remote reconnected the instant pathFollower was
# killed.
#
# So while the autonomy stack is up there is no working emergency stop. This
# script is the fallback: it kills whatever is commanding the robot and then
# sends an explicit stop.
#
# The proper fix is a joystick connected to the Jetson: the stack is designed
# around one (`autonomyMode`, `joySpeed`, `joyManualFwd` in localPlanner.cpp),
# and it provides manual override the way the author intended.

# No `set -u`/`set -e` here on purpose: the ROS setup scripts reference unset
# variables, and an emergency stop must never abort halfway through.

echo "estop: killing the nodes that command the robot..."
pkill -f "[p]athFollower"
pkill -f "[l]ocalPlanner"
pkill -f "[t]eleop_key"
pkill -f "[c]md_vel_bridge"
sleep 1

echo "estop: sending StopMove..."
source /opt/ros/foxy/setup.bash 2>/dev/null
source "$HOME/unitree_ros2/cyclonedds_ws/install/setup.bash" 2>/dev/null
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ROS_DOMAIN_ID=0
unset ROS_LOCALHOST_ONLY
export CYCLONEDDS_URI="<CycloneDDS><Domain id=\"any\"><General><Interfaces><NetworkInterface name=\"eth0\" priority=\"default\" multicast=\"default\"/></Interfaces></General></Domain></CycloneDDS>"

python3 - <<'PY'
import time
import rclpy
from rclpy.node import Node
from unitree_api.msg import Request

STOP_MOVE = 1003

rclpy.init()
n = Node('estop')
pub = n.create_publisher(Request, '/api/sport/request', 10)
time.sleep(1.0)
# Repeat: a single message can be lost, and something else may still be
# publishing while we shut it down.
for _ in range(10):
    r = Request()
    r.header.identity.id = int(time.time() * 1e6) % (2 ** 31)
    r.header.identity.api_id = STOP_MOVE
    r.header.policy.noreply = True
    pub.publish(r)
    time.sleep(0.1)
print("estop: StopMove sent")
rclpy.shutdown()
PY

echo "estop: remaining command nodes: $(pgrep -cf '[p]athFollower|[l]ocalPlanner|[t]eleop_key' || true)"
echo "estop: done. The Unitree remote should respond again."
