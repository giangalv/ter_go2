#!/usr/bin/env bash
# ROS 2 environment for the robot's topics carried over Wi-Fi by zenoh.
#
#   source ~/ter-home/ter_go2/setup_go2_remote.bash
#
# Source it in every terminal where you run RViz2, ros2, rqt or rosbag against
# the robot while zenoh/bridge_pc.sh is running. The PC bridge writes the
# robot's topics into domain 2, on localhost only (zenoh/pc.json5); this matches
# it. Domain 2 rather than this machine's default 99, so that the robot's /tf
# and friends do not mix with anything else running locally.
#
# For the wired link use setup_go2.bash instead. See docs/remote-ros.md.

GO2_WS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source /opt/ros/humble/setup.bash
if [ -f "$GO2_WS/install/setup.bash" ]; then
    # go2_description: RViz2 loads the robot meshes from here, locally.
    source "$GO2_WS/install/setup.bash"
fi

export ROS_DOMAIN_ID=2
export ROS_LOCALHOST_ONLY=1
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
unset CYCLONEDDS_URI

echo "Go2 remote: domain ${ROS_DOMAIN_ID}, localhost only, rmw=${RMW_IMPLEMENTATION}"
