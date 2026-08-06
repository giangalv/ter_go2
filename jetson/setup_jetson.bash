#!/usr/bin/env bash
# ROS 2 environment ON THE ONBOARD JETSON (ROS 2 Foxy).
#
#   source ~/ter_go2/setup_jetson.bash [pc_ip]
#
# The Jetson straddles two networks and has to speak to both with the same DDS
# participant:
#
#   eth0  192.168.123.18   the robot's internal network — MULTICAST is
#                          mandatory, it is the only way to discover the Go2's
#                          23 participants, which use random ephemeral ports;
#   wlan0 (DHCP)           Wi-Fi towards the PC — the PC is reached through
#                          <Peers> in unicast, because the campus network
#                          filters multicast between clients.
#
# CAREFUL: wlan0 must keep multicast="default", NOT "false".
# In CycloneDDS the flag is not per-interface the way it looks: marking a single
# interface as non-multicast disables multicast for the ENTIRE participant, and
# the node stops seeing the robot. Measured:
#   wlan0 multicast="false"    -> 2 topics   (robot invisible)
#   wlan0 multicast="default"  -> 121 topics (robot visible)
# With "default", multicast is also attempted on wlan0: the access point drops
# it, with no consequences, and the PC is still reached through <Peers>.
#
# The domain stays 0, the same as the robot: a single process cannot join two
# different domains, so the PC also connects on domain 0 and sees only what this
# node publishes (the robot's participants are not reachable from there, so it
# never discovers them).
#
# <ParticipantIndex>auto</ParticipantIndex> is indispensable: without it,
# CycloneDDS assigns an ephemeral port and the PC, which searches over unicast
# on the deterministic ports 7410+2i, would never find this node.

GO2_PC_IP="${1:-130.251.13.105}"
GO2_ETH_IFACE="${GO2_ETH_IFACE:-eth0}"
GO2_WLAN_IFACE="${GO2_WLAN_IFACE:-wlan0}"
GO2_JETSON_WS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source /opt/ros/foxy/setup.bash

# CycloneDDS 0.10.2: the one shipped with Foxy is 0.7 and does not know the
# <Interfaces><NetworkInterface> syntax, failing with
# "Interfaces: unknown element".
if [ -f "$HOME/unitree_ros2/cyclonedds_ws/install/setup.bash" ]; then
    source "$HOME/unitree_ros2/cyclonedds_ws/install/setup.bash"
else
    echo "setup_jetson: unitree_ros2/cyclonedds_ws is missing — the unitree_go" >&2
    echo "  messages and CycloneDDS 0.10.2 will not be available." >&2
fi

[ -f "$GO2_JETSON_WS/install/setup.bash" ] && source "$GO2_JETSON_WS/install/setup.bash"

unset ROS_LOCALHOST_ONLY
export ROS_DOMAIN_ID=0
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
# The wireless interface only exists when the USB dongle is plugged in. Listing
# a missing interface makes CycloneDDS refuse to start altogether
# ("does not match an available interface"), which would break the wired path
# too — so it is only added when actually present.
GO2_IFACE_XML="        <NetworkInterface name=\"${GO2_ETH_IFACE}\" priority=\"default\" multicast=\"default\"/>"
if ip link show "$GO2_WLAN_IFACE" >/dev/null 2>&1; then
    GO2_IFACE_XML="$GO2_IFACE_XML
        <NetworkInterface name=\"${GO2_WLAN_IFACE}\" priority=\"default\" multicast=\"default\"/>"
else
    echo "setup_jetson: ${GO2_WLAN_IFACE} absent (USB Wi-Fi dongle not plugged in?)." >&2
    echo "  Continuing on ${GO2_ETH_IFACE} only: the robot works, the PC is only" >&2
    echo "  reachable over the cable." >&2
fi

GO2_CFG="${TMPDIR:-/tmp}/cyclonedds_go2_jetson.xml"
cat > "$GO2_CFG" <<XMLCFG
<?xml version="1.0" encoding="UTF-8" ?>
<CycloneDDS xmlns="https://cdds.io/config">
  <Domain id="any">
    <General>
      <Interfaces>
$GO2_IFACE_XML
      </Interfaces>
    </General>
    <Discovery>
      <ParticipantIndex>auto</ParticipantIndex>
      <MaxAutoParticipantIndex>30</MaxAutoParticipantIndex>
      <Peers><Peer address="${GO2_PC_IP}"/></Peers>
    </Discovery>
  </Domain>
</CycloneDDS>
XMLCFG
export CYCLONEDDS_URI="file://$GO2_CFG"

echo "Jetson: robot on ${GO2_ETH_IFACE} (multicast), PC ${GO2_PC_IP} on ${GO2_WLAN_IFACE} (unicast), domain 0"
