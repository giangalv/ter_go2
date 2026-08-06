#!/usr/bin/env bash
# ROS 2 environment for talking to the onboard Jetson OVER WI-FI.
#
#   source ~/ter-home/ter_go2/setup_go2_wifi.bash [jetson_ip] [pc_wifi_iface]
#
# Use this when the Ethernet cable is unplugged and the robot is walking: the
# nodes run on the Jetson (which talks to the robot over the internal wired
# network) and the PC only receives the topics the Jetson republishes.
#
# Why this differs from setup_go2.bash:
#
#   * discovery is UNICAST, without multicast. On a campus Wi-Fi multicast is
#     filtered between clients (verified: even mDNS does not get through), but
#     here we control both ends and can list each other as peers;
#   * the domain stays 0, the same as the robot, because the node on the Jetson
#     has to talk to the robot and a process cannot join two domains. That is
#     not a problem: the PC's only peer is the Jetson, so it discovers only the
#     Jetson's topics. The robot's 23 participants stay invisible from here,
#     which is exactly what we want — the Wi-Fi carries the chosen data, not the
#     127 onboard topics;
#   * <ParticipantIndex>auto</ParticipantIndex> is MANDATORY. Without it,
#     CycloneDDS gives the participant a random ephemeral port and the
#     participant becomes discoverable ONLY via multicast: unicast peers never
#     find it. This is the same reason the robot itself cannot be reached over
#     unicast.

GO2_WIFI_IFACE="${2:-wlp0s20f3}"
GO2_WS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GO2_DOMAIN=0

# MAC of the Jetson's wlan0 (TP-Link Archer T3U dongle). The IP comes from DHCP
# and changes; the MAC does not, so we look it up by MAC. Once there is a DHCP
# reservation on the router this lookup becomes redundant, but it stays
# harmless: it will simply keep finding the same address.
GO2_JETSON_MAC="0c:ef:15:39:51:c6"

if ! ip link show "$GO2_WIFI_IFACE" >/dev/null 2>&1; then
    echo "setup_go2_wifi: interface '$GO2_WIFI_IFACE' does not exist." >&2
    ip -br link | awk '{print "  " $1}' >&2
    return 1 2>/dev/null || exit 1
fi

go2_ip_from_mac() {
    ip neigh show dev "$GO2_WIFI_IFACE" 2>/dev/null \
        | awk -v m="$GO2_JETSON_MAC" 'tolower($0) ~ tolower(m) && $1 ~ /^[0-9]+\./ {print $1; exit}'
}

GO2_JETSON_IP="$1"

if [ -z "$GO2_JETSON_IP" ]; then
    # 1) the ARP cache may already know it: costs no traffic
    GO2_JETSON_IP="$(go2_ip_from_mac)"

    # 2) otherwise populate the cache with a subnet sweep
    if [ -z "$GO2_JETSON_IP" ]; then
        GO2_SUBNET="$(ip -4 -o addr show dev "$GO2_WIFI_IFACE" \
                      | awk '{print $4}' | cut -d/ -f1 | cut -d. -f1-3)"
        if [ -n "$GO2_SUBNET" ]; then
            echo "setup_go2_wifi: looking for the Jetson (MAC $GO2_JETSON_MAC) on ${GO2_SUBNET}.0/24..."
            for i in $(seq 1 254); do
                ping -c1 -W1 "${GO2_SUBNET}.$i" >/dev/null 2>&1 &
            done
            wait 2>/dev/null
            GO2_JETSON_IP="$(go2_ip_from_mac)"
        fi
    fi
fi

if [ -z "$GO2_JETSON_IP" ]; then
    echo "setup_go2_wifi: Jetson not found on the Wi-Fi network." >&2
    echo "  Check that it is powered and connected: attach the cable and run" >&2
    echo "  'ssh go2jetson-eth ip -br addr show wlan0'." >&2
    echo "  You can also pass the address by hand: source setup_go2_wifi.bash <ip>" >&2
    return 1 2>/dev/null || exit 1
fi

if ! ping -c1 -W2 "$GO2_JETSON_IP" >/dev/null 2>&1; then
    echo "setup_go2_wifi: $GO2_JETSON_IP does not answer." >&2
    return 1 2>/dev/null || exit 1
fi

source /opt/ros/humble/setup.bash
[ -f "$GO2_WS/install/setup.bash" ] && source "$GO2_WS/install/setup.bash"

unset ROS_LOCALHOST_ONLY
export ROS_DOMAIN_ID=$GO2_DOMAIN
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
GO2_CFG="${TMPDIR:-/tmp}/cyclonedds_go2_pc.xml"
cat > "$GO2_CFG" <<XMLCFG
<?xml version="1.0" encoding="UTF-8" ?>
<CycloneDDS xmlns="https://cdds.io/config">
  <Domain id="any">
    <General>
      <Interfaces>
        <NetworkInterface name="${GO2_WIFI_IFACE}" priority="default" multicast="false"/>
      </Interfaces>
      <AllowMulticast>false</AllowMulticast>
    </General>
    <Discovery>
      <ParticipantIndex>auto</ParticipantIndex>
      <MaxAutoParticipantIndex>120</MaxAutoParticipantIndex>
      <Peers><Peer address="${GO2_JETSON_IP}"/></Peers>
    </Discovery>
  </Domain>
</CycloneDDS>
XMLCFG
export CYCLONEDDS_URI="file://$GO2_CFG"

echo "Go2 over wifi: jetson=${GO2_JETSON_IP}  interface=${GO2_WIFI_IFACE}  domain=${GO2_DOMAIN}"
