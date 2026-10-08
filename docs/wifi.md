# Reaching the Go2 over Wi-Fi

## The problem, measured

```
source setup_go2.bash enp59s0   && ros2 topic list | wc -l   # 118
source setup_go2.bash wlp0s20f3 && ros2 topic list | wc -l   #   2  (locals only)
```

The Go2 publishes DDS **only** on its internal wired network
`192.168.123.0/24`. Over Wi-Fi it exposes WebRTC, which is what the phone app
uses. This is not a PC configuration issue: the DDS packets never leave the
robot's wireless interface.

## The hard constraint: multicast is required, unicast is not a fallback

Verified experimentally, and this determines what hardware can work:

| configuration | topics seen |
|---|---|
| multicast off + `<Peer address="192.168.123.161"/>` | **2** (locals only) |
| same configuration, multicast on | **127** |

`tcpdump` on the cable shows why: the robot answers
`ICMP udp port 7410/7412/.../7420 unreachable` to unicast discovery probes.
Nothing listens on the standard SPDP ports.

The CycloneDDS trace (`<Tracing><Category>discovery</Category>`) gives the
definitive reason: the robot exposes **23 distinct DDS participants**, one per
onboard service, each on a **random ephemeral port**:

```
uslam_server                  -> udp/192.168.123.161:47539
voxel_height_mapping_dds_node -> udp/192.168.123.161:50913
unitreeWebRTCClientMaster     -> udp/192.168.123.161:54411
obstacles_avoid               -> udp/192.168.123.161:37868
...                              33484, 38451, 43812, 46685, 57281, 60042, ...
```

The ports change at every boot, so static unicast peers are impossible.
**Multicast (239.255.0.1, UDP 7400/7401) is mandatory.**

Practical consequence: any Wi-Fi device in the path must forward IP multicast
transparently in both directions, without NAT and without IGMP snooping
dropping it. If it blocks it, there is no way to work around it from the PC.

---

## A. Wi-Fi/Ethernet bridge on the robot

A small router attached to the robot's Ethernet port extends the
`192.168.123.0/24` segment over the air. From the robot's point of view
**nothing changes**: it keeps speaking DDS on its Ethernet, and the bridge
carries the multicast that discovery needs.

The decisive advantage: it requires neither touching the robot nor a working
Jetson. Everything in this workspace keeps working identically — only the
interface passed to `setup_go2.bash` changes.

### What to buy

| model | price | weight | notes |
|---|---|---|---|
| **GL.iNet GL-SFT1200 "Opal"** | ~30 € | 118 g | dual band 5 GHz, gigabit LAN. **Recommended**: the bandwidth matters for the LiDAR point cloud |
| GL.iNet GL-MT300N-V2 "Mango" | ~25 € | 39 g | 2.4 GHz only, 100 Mbit Ethernet. Lighter, but bandwidth is marginal |
| GL.iNet GL-AR300M16 "Shadow" | ~35 € | 45 g | middle ground |

All are OpenWrt-based and powered over USB at 5 V — from the robot's USB
connector or a small power bank.

Why OpenWrt matters more than the radio specs: with SSH access to the router you
can **verify and correct** its multicast behaviour, which is the critical point.
On closed firmware, if multicast is filtered you have neither diagnosis nor
remedy.

```bash
ssh root@192.168.8.1
bridge link                       # both interfaces must be on the same bridge
uci set network.@device[0].igmp_snooping='0' && uci commit && reload_config
tcpdump -i br-lan -n 'udp port 7400 or udp port 7401'   # traffic must flow
```

### Bandwidth needed (measured)

| topic | rate | bandwidth |
|---|---|---|
| `/utlidar/cloud` | 14.7 Hz | 5.36 Mbit/s |
| `/lowstate` | 500 Hz | 2.80 Mbit/s |
| **total without camera** | | **8.16 Mbit/s** |

Comfortably within 2.4 GHz on paper. The problem with 2.4 GHz in a university
building is congestion: DDS discovery uses reliable traffic and suffers packet
loss far more than sensor data does. Adding the RTSP camera (port 8551) eats the
remaining margin.

### Configure it as an Access Point, not WISP

* **Access Point** — the router creates its own network and the PC joins it.
  This is a pure layer-2 bridge: multicast passes and DDS discovery works with
  nothing else to configure. **Use this.**
* **WISP / Repeater** — the router joins the lab Wi-Fi and performs **NAT**.
  NAT breaks multicast and therefore discovery. Avoid.

Settings:

1. Router in **Access Point** mode.
2. Router LAN: `192.168.123.2/24`, **DHCP server disabled** (the robot already
   has its fixed `.161`; a second DHCP server must not appear).
3. Cable from the router's LAN port to the robot's Ethernet port.
4. On the PC, join that Wi-Fi with a **static `192.168.123.99/24`**, no gateway.
5. `source setup_go2.bash <wifi-interface>`.

The PC loses the lab Wi-Fi, but `enp59s0` is then free for wired internet.
Alternatively, a USB Wi-Fi dongle on the PC keeps the two networks separate.

### If you already own a Vonets (or similar): a ten-minute test

The Vonets VAP11N-300 / VAR11N-300 can in principle work, but only in
**Access Point** mode, not in their native ones:

* the VAP11N-300 is designed as a *client bridge with MAC cloning* — it would
  attach the robot to the lab Wi-Fi, but the robot's fixed `192.168.123.161`
  makes no sense on that subnet, and we cannot change it without SSH;
* the VAR11N-300 is a small router: by default it performs **NAT**, which breaks
  discovery.

Definitive test, valid for any device:

```bash
# 1. device in AP mode, DHCP off, cable to the robot's Ethernet port
# 2. PC on that Wi-Fi with static 192.168.123.99/24, no gateway
source ~/ter-home/ter_go2/setup_go2.bash <wifi-interface>
ros2 topic list | wc -l
```

118 or more means it works. 2 means multicast is not getting through, and that
is not recoverable.

---

## B. Onboard computer as a bridge — the option in use

The Jetson sits on the robot, speaks DDS on the internal network and publishes
towards the PC over Wi-Fi. This is the right architecture for research work:
perception and policies run onboard, and only the chosen topics cross the air.

The Jetson has **no built-in wireless**, so it needs a USB Wi-Fi dongle. See
[jetson.md](jetson.md) for the one in use (TP-Link Archer T3U, RTL8812BU) and
how its driver was built.

DDS from the Jetson to the PC currently does not complete endpoint discovery —
see the known limitations in the README. Two setups work around it: RViz on
the Jetson viewed in a browser ([remote-desktop.md](remote-desktop.md)), or the
robot's topics carried to the PC by a zenoh bridge
([remote-ros.md](remote-ros.md)).

### The dongle, the USB bus and the watchdog

`go2-usb-wifi-watchdog.service` (`jetson/usb_wifi_watchdog.sh`, installed as
`/usr/local/sbin/usb_wifi_watchdog.sh`) brings the dongle back when it
disappears. The Jetson has a **single USB controller**, `3610000.xhci`, and the
RealSense on the USB-C port hangs off it too, so the watchdog resets the
controller only when the controller itself is dead. Measured on 2026-10-08:

* **The dongle itself dropping off the bus.** It disconnected and re-enumerated
  every 3-15 s from 27 s after boot (201 disconnects in 36 min), with no kernel
  error before any of them, while the controller was healthy. The old watchdog
  answered every disappearance with a full controller reset, and each reset took
  the RealSense down as well. The third left neither device on the bus.
  **Re-seating the dongle fixed it:** after a reboot, 0 disconnects, still 0
  after several minutes with the RealSense streaming. A dongle that keeps
  disconnecting is a connector or power problem first, not a software one.
* **What the watchdog does now** when `wlan0` is missing: it waits 20 s (a
  dropped dongle comes back by itself). If the controller is dead (no USB device
  left, or `HC died` / `controller error detected` in the kernel log), it resets
  it as before. If the dongle is on the bus without `wlan0`, it reloads the
  `88x2bu` driver only. If the dongle is off the bus and the controller is
  alive, it leaves everything alone and logs a hint to check the dongle.
* The kernel warnings `NULL pointer, cannot free irq` with `88x2bu(OE)` in the
  module list come from the watchdog's own unbind of the controller. They are a
  side effect of a reset, not a driver crash.

```bash
journalctl -b -u go2-usb-wifi-watchdog            # what it did
journalctl -k -b | grep -c "USB disconnect"       # how often anything dropped off
```

---

## C. WebRTC, the way the app does it

`go2_ros2_sdk` implements the protocol the phone uses, so it works over the
robot's existing Wi-Fi with no extra hardware. In exchange it is reverse
engineered, has higher latency and covers fewer topics than DDS. A sensible
fallback only if a bridge is impractical.

---

## References

* [Receiving ROS2 topics over wireless — MYBOTSHOP forum](https://forum.mybotshop.de/t/unitree-go2-the-method-to-receive-ros2-topics-over-a-wireless-connection/1044)
  — confirms that "the default topics of unitree do not go over WiFi" and that
  `cyclonedds.xml` has to be modified on the robot.
* [DroneBlocks/go2-wifi-adapter](https://github.com/DroneBlocks/go2-wifi-adapter)
  — USB Wi-Fi dongle (BrosTrend AC1L) in AP mode attached **to the Jetson**:
  the variant of (B) without an external router.
