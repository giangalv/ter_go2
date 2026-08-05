#!/usr/bin/env bash
# ROS 2 environment for talking to the Unitree Go2 over the wired link.
#
#   source ~/ter-home/ter_go2/setup_go2.bash            # ethernet (default)
#   source ~/ter-home/ter_go2/setup_go2.bash wlp0s20f3  # name another interface
#
# The Go2 does not speak "plain" ROS 2. It publishes straight onto CycloneDDS,
# domain 0, in the clear, without registering ROS nodes. Three things are
# therefore required that this machine's default environment does NOT provide:
#
#   * RMW_IMPLEMENTATION=rmw_cyclonedds_cpp  (the default is Fast DDS, which
#     sees nothing of the robot);
#   * ROS_DOMAIN_ID=0                        (the local .bashrc sets 99);
#   * ROS_LOCALHOST_ONLY unset               (the local .bashrc sets it to 1,
#     which blocks all outbound traffic).
#
# CYCLONEDDS_URI pins CycloneDDS to a single interface: without it, on a machine
# running Wi-Fi, Ethernet, Docker, Tailscale and a VPN at the same time,
# discovery latches onto the wrong one and the robot stays invisible.

GO2_IFACE="${1:-enp59s0}"
GO2_WS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! ip link show "$GO2_IFACE" >/dev/null 2>&1; then
    echo "setup_go2: interface '$GO2_IFACE' does not exist. Available:" >&2
    ip -br link | awk '{print "  " $1}' >&2
    return 1 2>/dev/null || exit 1
fi

source /opt/ros/humble/setup.bash
if [ -f "$GO2_WS/install/setup.bash" ]; then
    source "$GO2_WS/install/setup.bash"
else
    echo "setup_go2: workspace not built yet (no install/). "\
"Run: cd $GO2_WS && colcon build" >&2
fi

unset ROS_LOCALHOST_ONLY
export ROS_DOMAIN_ID=0
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="<CycloneDDS><Domain><General><Interfaces><NetworkInterface name=\"${GO2_IFACE}\" priority=\"default\" multicast=\"default\" /></Interfaces></General></Domain></CycloneDDS>"
export GO2_IFACE

echo "Go2: interface=${GO2_IFACE}  domain=${ROS_DOMAIN_ID}  rmw=${RMW_IMPLEMENTATION}"
