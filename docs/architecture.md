# Architecture, and how it was arrived at

Three machines, two networks, and one constraint that determined everything.

```
                  internal wired network 192.168.123.0/24
                  (robot DDS, multicast mandatory)
   .161 ------------------+------------------ .18
   main board             |                   Jetson Orin NX
   127 topics, 23 DDS     |                   ROS 2 Foxy
   participants           |                   130.251.13.140 on wlan0
                          |                          |
                        .99                          | Wi-Fi, unicast DDS
                    PC (bench only)                  |
                                                     |
                                              PC — RViz2 / browser
```

## The constraint that decided everything

The robot's DDS **requires multicast** and admits no fallback. Measured:

| | topics seen |
|---|---|
| multicast off + unicast peer `.161` | 2 |
| multicast on | 127 |

The reason is in the CycloneDDS trace: the robot exposes **23 participants, each
on a random ephemeral port** that changes at every boot.

```
uslam_server                  -> udp/192.168.123.161:47539
voxel_height_mapping_dds_node -> udp/192.168.123.161:50913
unitreeWebRTCClientMaster     -> udp/192.168.123.161:54411
```

Listing them as static peers is impossible. The robot replies
`ICMP port unreachable` on the standard SPDP ports 7410-7420 because nothing is
listening there.

**Consequence:** the link to the robot has to stay wired, where multicast works.
This is not a PC limitation: over Wi-Fi the Go2 speaks WebRTC, not DDS
(verified: 118 topics over Ethernet, 0 over wireless).

## The solution: the Jetson as a bridge

The Jetson has two interfaces and straddles both networks:

* `eth0` `192.168.123.18` on the internal network — sees all 127 robot topics;
* `wlan0` on Wi-Fi — talks to the PC.

The nodes run on the Jetson and the PC receives only what it needs. Heavy
traffic (LiDAR 5.36 Mbit/s, lowstate 2.80 Mbit/s) stays on the internal cable.

## The technical key: ParticipantIndex

Between PC and Jetson we control both ends, so multicast can be avoided — useful
because campus networks routinely filter it between clients. But the unicast
configuration only works if you add:

```xml
<Discovery>
  <ParticipantIndex>auto</ParticipantIndex>
  <Peers><Peer address="..."/></Peers>
</Discovery>
```

Without `ParticipantIndex`, CycloneDDS uses its default behaviour: a random
ephemeral port, which makes the participant discoverable **only via multicast**.
With `auto` the participant binds the deterministic port
`7400 + 250*domain + 10 + 2*index` — on domain 42 that is `17910`, `17912`, …
which is exactly where peers look for it.

Verified: without `auto` the Jetson bound `130.251.13.140:38705` while the PC
probed `17910-17926` in vain. With `auto`, the test topic arrives.

This is the same mechanism that makes the robot unreachable over unicast — the
difference is that on the robot we cannot change the configuration.

## Domains

* **domain 0** — the robot, on the wired network. Crowded: 127 topics, 23
  participants.
* **domain 42** — PC ↔ Jetson over Wi-Fi in the standalone test. Note that the
  production bridge runs on domain 0, because a single process cannot join two
  domains and the bridge has to talk to the robot.

## Usage

```bash
# PC cabled to the robot (bench, robot stationary)
source ~/ter-home/ter_go2/setup_go2.bash

# PC untethered, robot walking: the nodes run on the Jetson and the topics
# reach the PC through the zenoh bridge (DDS itself does not cross the Wi-Fi,
# see "Open issues" and remote-ros.md)
ssh go2jetson                                   # shell on the Jetson
~/ter-home/ter_go2/zenoh/bridge_pc.sh           # topics on the PC
source ~/ter-home/ter_go2/setup_go2_remote.bash
```

`setup_go2_wifi.bash` was the first attempt at the untethered case: DDS from
the Jetson to the PC over Wi-Fi with unicast peers. Participant discovery works,
endpoint discovery never completes, and no topic ever appears, so it is not a
way to get the topics on the PC.

On the Jetson, always:

```bash
source /opt/ros/foxy/setup.bash
source ~/unitree_ros2/cyclonedds_ws/install/setup.bash   # CycloneDDS 0.10.2
```

The second `source` is not optional: Foxy ships CycloneDDS 0.7, which does not
know the `<Interfaces><NetworkInterface>` syntax and fails with
`config: Interfaces: unknown element`.

## Open issues

* The Jetson's Wi-Fi address comes from DHCP and changes, while the `go2jetson`
  alias in `~/.ssh/config` points at a fixed address. The MAC
  (`0c:ef:15:39:51:c6`) does not change: `setup_go2_wifi.bash` contains a
  lookup by MAC (ARP cache first, then a sweep of the subnet), and with the
  cable attached `ssh go2jetson-eth 'ip -br addr show wlan0'` gives it
  directly. The permanent fix is a **DHCP reservation** on the router — ask
  whoever administers it, quoting the MAC and the name `ter-go2-jetson`.

  A hand-picked static address on this subnet is **not advisable**: it is a
  campus-managed network, the DHCP pool boundaries are unknown, and you would
  risk the same address conflict that kept the Jetson hidden in the first place.

  The `ter-go2-jetson.local` route is not available here: avahi runs on both
  machines and the PC has `mdns4_minimal` in `/etc/nsswitch.conf`, but
  resolution times out because **the Wi-Fi network filters multicast between
  clients**. Same reason DDS discovery over Wi-Fi has to be unicast.

* `TheEngineRoom-WiFi` exists only on 2.4 GHz: 4.9 ms average latency but peaks
  of 45 ms. `DrayTek-4E4BC0` offers 5 GHz at 270 Mbit/s with a stronger signal.

* The `go2_bringup` nodes are written for Humble and also run on Foxy. The
  `unitree_go`/`unitree_api` messages recompile without changes; the `rclpy`
  APIs in use are compatible with both.
