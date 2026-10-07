# The robot's topics on the PC over Wi-Fi (zenoh)

**Working.** Tested on the robot on 2026-10-07, PC on Wi-Fi through an SSH tunnel.

DDS does not reach the PC over Wi-Fi (see [architecture.md](architecture.md)).
zenoh-bridge-ros2dds carries it instead: one bridge on the Jetson, one on the
PC, connected over TCP. On the PC the robot's topics reappear in a local DDS
domain as plain ROS 2 topics, so RViz2, `ros2 topic`, rqt and rosbag work as if
the robot were wired.

```bash
# once, on each machine (internet)
~/ter-home/ter_go2/zenoh/install_bridge.sh          # PC
~/ter_go2/zenoh/install_bridge.sh                   # Jetson

# on the Jetson
source ~/ter_go2/setup_jetson.bash
ros2 launch go2_bringup go2_remote.launch.py

# on the PC: leave this running (tunnel + bridge, Ctrl-C stops both)
~/ter-home/ter_go2/zenoh/bridge_pc.sh

# on the PC, in any other terminal
source ~/ter-home/ter_go2/setup_go2_remote.bash
rviz2 -d ~/ter-home/ter_go2/src/go2_bringup/rviz/go2_remote.rviz
ros2 topic list
```

## How it is put together

```
 Jetson (ROS 2 Foxy, domain 0)                               PC (ROS 2 Humble, domain 2, localhost)
 ──────────────────────────────────                          ──────────────────────────────────────
 robot_state_publisher  /robot_description ┐                 ┌─▶ RViz2, ros2 CLI, rqt, rosbag
 state_bridge           /tf /joint_states  │  zenoh bridge   │
                        /odom /go2/*       ├─▶ (OUT only) ═══╪═▶ zenoh bridge (IN only) ──┘
 front_camera           /front_camera/...  ┘  127.0.0.1:7447 │   ssh -L 7447 over Wi-Fi
```

* **Jetson side:** `go2_remote.launch.py` starts `robot_state_publisher`,
  `state_bridge` (LiDAR relayed on `/go2/cloud`, capped at 5 Hz), `front_camera`
  (hardware-encoded JPEG, 10 Hz) and the bridge with `zenoh/jetson.json5`. The
  bridge's own DDS participant uses `zenoh/cyclonedds_jetson.xml` (eth0 only,
  same discovery settings as `setup_jetson.bash`).
* **PC side:** `zenoh/bridge_pc.sh` opens the SSH tunnel and runs the bridge with
  `zenoh/pc.json5`, which writes the topics into **domain 2 on localhost**.
  Domain 2 rather than this PC's default 99, so that the robot's `/tf` does not
  mix with anything else running locally. `setup_go2_remote.bash` sets the same
  domain, localhost only and CycloneDDS for RViz2 and the CLI.
* **Meshes are not transferred.** RViz2 loads `package://go2_description/...`
  from the PC's own `install/`, so only the 27 KB URDF crosses the Wi-Fi.
* **Only what is subscribed is sent.** A topic with no subscriber on the PC
  costs nothing on the Wi-Fi.

## One-way, on purpose

Both ends use `allow` lists, and with `allow` an interface kind that is not
listed is denied entirely:

* `jetson.json5` allows **publishers** only, the whitelisted topics. No
  `subscribers` means nothing coming from zenoh is ever written into the
  robot's DDS, so `/api/sport/request` cannot be reached from the PC. No
  services or actions either.
* `pc.json5` allows **subscribers** only: the PC receives, and publishes
  nothing outwards.
* The Jetson's bridge listens on `127.0.0.1` only, the SSH tunnel is the way
  in, and multicast scouting is off on both ends.

Verified: 20 messages published on the PC on `/go2/test_injection`, a name
inside the whitelist, with a listener on the Jetson. The Jetson received 0, and
neither bridge created a route for it.

## Measured

On the PC, through the tunnel over Wi-Fi:

| topic | rate on the PC | at the source |
|---|---|---|
| `/front_camera/image/compressed` | 10.1 Hz | 10 Hz |
| `/go2/cloud` | 4.0 Hz | capped at 5 Hz |
| `/joint_states` | 17.4 Hz | ~20 Hz |
| `/odom` | 112 Hz | 150 Hz |
| `/tf` | 120 Hz | |
| `/robot_description` | latched, received | |

## Things that bit

* **Which binary.** Neither build of 1.10.x runs everywhere: the x86_64 `gnu`
  build needs GLIBC_2.38 (the PC's Ubuntu 22.04 has 2.35), and the `musl`
  builds are not static. They need the musl loader and a musl `libgcc_s`, and
  even with Ubuntu's `musl` package they fail with
  `_Unwind_Backtrace: symbol not found`. 1.9.0 `gnu` needs GLIBC_2.34 on x86_64
  and 2.30 on aarch64 (the Jetson has 2.31), so `install_bridge.sh` pins 1.9.0
  on both ends. Both bridges must run the same version.
* **Do not mix with the wired setup.** `setup_go2.bash` (wired) exports a
  `CYCLONEDDS_URI` for the Ethernet interface and domain 0. `bridge_pc.sh` and
  `setup_go2_remote.bash` clear it. Use one or the other in a given terminal.
* **A launch started in the background ignores Ctrl-C.** In a non-interactive
  shell, `cmd &` starts with SIGINT ignored, and the launch and its nodes inherit
  that. Run `go2_remote.launch.py` in a terminal, where Ctrl-C works, or stop a
  background one with SIGTERM and then check for leftover `state_bridge`,
  `front_camera`, `robot_state_publisher` and `zenoh-bridge-ros2dds`.

## Before this: Foxglove

The same day a `foxglove_bridge` was tried first. It never supported Foxy, so it
ran in a Humble container. It worked, but it ties the PC to the Foxglove app,
and every client downloads ~25 MB of meshes. zenoh gives back the normal ROS
tools on the PC, so the Foxglove setup was removed.
