# ter_go2 — Unitree Go2 EDU on ROS 2

Workspace to drive a Unitree Go2 EDU from a desktop machine: read robot state,
visualise it in RViz2, and move the robot safely.

Two machines are involved, running different ROS 2 distributions:

| machine | role | ROS 2 |
|---|---|---|
| desktop PC | RViz2, development | Humble |
| onboard Jetson Orin NX | talks to the robot, runs the nodes | Foxy |

The same workspace is deployed to both, at `~/ter_go2` on the Jetson and
`~/Documents/GitHub/ter_go2` on the PC.

---

## How everything is wired

```
                  internal wired network 192.168.123.0/24
                  (robot DDS — multicast is mandatory here)
   .161 --------------------+------------------- .18
   main board               |                    Jetson Orin NX
   127 topics, 23 DDS       |                    ROS 2 Foxy
   participants             |                    + Wi-Fi dongle
                            |                          |
                          .99                          | Wi-Fi
                    PC (bench only,                    |
                     cable attached)             PC — RViz2 / browser
```

The robot only speaks DDS on its **internal wired network**. Over Wi-Fi it
speaks WebRTC, which is what the phone app uses. Measured: 118 topics over
Ethernet, 0 over wireless.

That leaves two ways to work, and you pick one depending on whether the robot
is tethered:

* **cable attached** — the PC talks to the robot directly. Best on the bench.
* **robot untethered** — everything runs on the Jetson and you look at it
  through a browser. This is the normal mode of operation.

See [docs/architecture.md](docs/architecture.md) for why, with measurements.

---

## Connecting

### 1. On the bench, with the Ethernet cable

Plug the cable into the robot. The PC needs a static address on the robot's
subnet — `192.168.123.99/24`, no gateway. Then:

```bash
source ~/ter-home/ter_go2/setup_go2.bash        # defaults to enp59s0
source ~/ter-home/ter_go2/setup_go2.bash eth1   # or name the interface
ros2 topic list                                 # expect ~120 topics
```

The script exists because this machine's default environment is incompatible
with the robot: `.bashrc` sets `ROS_DOMAIN_ID=99` and `ROS_LOCALHOST_ONLY=1`,
and the default middleware is Fast DDS. With those settings the robot is
invisible. The script forces domain 0, CycloneDDS, and pins discovery to a
single interface — necessary on a machine that also has Wi-Fi, Docker,
Tailscale and a VPN up.

Then:

```bash
ros2 run go2_bringup robot_info                 # pre-flight report
ros2 launch go2_bringup go2_view.launch.py      # RViz2 + model + LiDAR
```

### 2. Untethered, over Wi-Fi

Unplug the cable. The Jetson keeps talking to the robot over the internal link
and you reach the Jetson over Wi-Fi.

```bash
ssh go2jetson            # Wi-Fi
ssh go2jetson-eth        # when the cable is attached
```

Both aliases live in `~/.ssh/config` and use the key `~/.ssh/id_go2_jetson`.
The Wi-Fi address comes from DHCP; `setup_go2_wifi.bash` finds the Jetson by
its MAC address, so a changed lease does not break anything.

Run the visualisation **on the Jetson** and watch it in a browser on the PC:

```bash
# terminal 1 on the PC — tunnel
ssh -L 6080:localhost:6080 go2jetson

# browser
http://localhost:6080/vnc.html

# terminal 2, or inside that remote desktop
ssh -t go2jetson
source ~/ter_go2/setup_jetson.bash
ros2 launch go2_bringup go2_mapping.launch.py    # SLAM + RViz, robot cannot move
ros2 launch go2_bringup go2_autonomy.launch.py   # adds driving — see docs/safety.md
```

Only pixels cross the Wi-Fi. See
[docs/remote-desktop.md](docs/remote-desktop.md).

If you launch RViz from a plain SSH session it dies with
`could not connect to display`. Either run it from inside the remote desktop,
or export the display first:

```bash
export DISPLAY=:0
export XAUTHORITY=/run/user/1000/gdm/Xauthority
```

### On the Jetson, always

```bash
source /opt/ros/foxy/setup.bash
source ~/unitree_ros2/cyclonedds_ws/install/setup.bash   # CycloneDDS 0.10.2
```

The second line is not optional. Foxy ships CycloneDDS 0.7, which does not
understand the `<Interfaces><NetworkInterface>` syntax and fails with
`config: Interfaces: unknown element`. `setup_jetson.bash` does both for you.

### Addresses

| host | address | notes |
|---|---|---|
| PC | `192.168.123.99/24` | wired, static |
| main board | `192.168.123.161` | publishes all robot topics |
| Jetson | `192.168.123.18/24` | wired, static via netplan |
| Jetson | DHCP | Wi-Fi, found by MAC |

Credentials for the Jetson are the Unitree factory defaults: user `unitree`,
password `123`, same for `sudo`.

---

## Packages

| package | contents |
|---|---|
| `unitree_go`, `unitree_api`, `unitree_hg` | message definitions, from `unitreerobotics/unitree_ros2` |
| `go2_description` | URDF and meshes, from `unitreerobotics/unitree_ros`, repackaged for ament |
| `go2_bringup` | bridges, launch files, RViz config, teleoperation |

## Nodes

### `state_bridge` — read only

The Go2 already publishes the LiDAR, the head IMU and odometry as standard ROS 2
messages. What is missing for a complete TF tree and an animated model is the
joint states and the transforms, which this node adds.

Publishes `/joint_states` (12 joints), `/go2/battery`, `/go2/imu`, `/odom`, and:

```
odom -> base_link -> base            (URDF root is `base`, Unitree uses `base_link`)
                  -> utlidar_lidar   (from the URDF radar_joint)
                  -> utlidar_imu
```

It subscribes to `/lf/lowstate` and `/lf/sportmodestate` — the low-frequency
variants — rather than the full-rate ones. Measured on the robot:

| topic | rate |
|---|---|
| `/lowstate` | 499.7 Hz |
| `/lf/lowstate` | 20.0 Hz |
| `/sportmodestate` | 300.0 Hz |
| `/lf/sportmodestate` | 20.0 Hz |

Deserialising 500 Hz in Python costs 86% of one core on the Orin NX and, because
of the GIL, starves DDS discovery to the point where topics stop appearing.
20 Hz is plenty for visualisation and navigation; switch to the full-rate topics
only for closed-loop control.

### `teleop_key` — keyboard driving

`w/s` forward and back, `a/d` strafe, `q/e` yaw, `1` stand, `2` lie down,
`3` recover from a fall, `SPACE` stop, `x` damp, `-`/`+` speed limit.

Defaults are 0.6 m/s forward, 0.4 m/s lateral, 0.8 rad/s yaw. Below roughly
0.3 m/s a Go2 barely moves, so do not set these too low "for safety" — you will
think the robot is broken.

Protections: configurable limits, a watchdog that stops the robot 0.4 s after
the last keypress, acceleration limiting, and a guaranteed stop on exit even if
an exception is raised.

Must run **on the Jetson**, with a TTY:

```bash
ssh -t go2jetson
source ~/ter_go2/setup_jetson.bash
ros2 run go2_bringup teleop_key
```

### `cmd_mux` — command arbiter, the keyboard wins

The only node that should publish motion commands when the autonomy stack is
running. It forwards the stack's commands only while you allow it, and starts
blocked. See [docs/safety.md](docs/safety.md).

### `cmd_vel_bridge` — for Nav2, joysticks, your own nodes

Takes `geometry_msgs/Twist` on `/cmd_vel`, clamps it to the limits and stops the
robot if the stream goes quiet for longer than `timeout`.

### `robot_info` — pre-flight check

Prints battery, the temperature of all 12 motors, IMU, foot contact forces and
the active mode. Run it **before** moving the robot.

---

## Safety

**Read [docs/safety.md](docs/safety.md) before running anything that drives the
robot.** In short: `/api/sport/request` has no arbitration, so while the
autonomy stack runs neither the keyboard nor the Unitree remote can stop the
Go2. Work on mapping with `go2_mapping.launch.py`, which starts no driving
nodes at all; drive only through `cmd_mux`, which makes itself the sole
publisher of motion commands and starts in a blocked state.

Every command goes through `/api/sport/request`, the **high-level** interface:
the onboard controller keeps balance and joint limits.

`/lowcmd` — direct motor control, the feature unlocked on the EDU version — is
**not** used by this workspace. It bypasses every protection and should only be
used with the robot suspended or lying down in a clear area.

Two things learned the hard way, both documented in
[docs/teleoperation.md](docs/teleoperation.md):

* the only selectable motion controller on this robot is `mcf`, and it
  implements only part of the classic sport API — `BalanceStand` is accepted but
  does nothing;
* if the robot answers queries but ignores posture commands and reports
  `error_code: 1001`, the controller is stuck and needs reloading.

Leaving the robot standing still heats the rear hip motors — 76 °C observed
against 33-42 °C for the other joints. Lie it down when you are not using it.

---

## The autonomy stack

SLAM and navigation come from
[jizhang-cmu/autonomy_stack_go2](https://github.com/jizhang-cmu/autonomy_stack_go2):
Point-LIO on the L1 lidar and its built-in IMU, terrain traversability
analysis, a local planner, and FAR Planner for global routes. It is a good fit
here — ROS 2 Foxy on Ubuntu 20.04, built-in sensors only, meant to run on the
robot's own computer. Its README warns that Humble sees data delays above one
second, so it belongs on the Jetson rather than the PC.

Install it on the Jetson:

```bash
sudo apt install -y libusb-dev ros-foxy-perception-pcl ros-foxy-sensor-msgs-py \
  ros-foxy-tf-transformations ros-foxy-joy ros-foxy-rmw-cyclonedds-cpp \
  ros-foxy-rosidl-generator-dds-idl
pip3 install transforms3d pyyaml

git clone https://github.com/jizhang-cmu/autonomy_stack_go2.git ~/autonomy_stack_go2
~/ter_go2/patch_autonomy_stack.sh          # see below
cd ~/autonomy_stack_go2
colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
```

`patch_autonomy_stack.sh` adapts four things the stack ships configured for the
author's own setup: the camera node is commented out and points at `enp3s0`
(the Jetson's interface is `eth0`), `sensor_scan_generation` and
`visualization_tools` are commented out so four RViz displays never get data,
and the RViz node is unconditional so ours cannot replace it. It is idempotent
and keeps the original alongside.

### Lidar IMU calibration, once per robot

```bash
ros2 run calibrate_imu calibrate_imu
```

The node drives the robot itself: two seconds settling, ten standing still,
then **twenty seconds spinning in place at 1.4 rad/s**. It never translates, but
give it clear space. The result lands in `~/Desktop/imu_calib_data.yaml`, which
is where `transform_sensors` looks for it.

### If the map disappears or the robot is in the wrong place

Point-LIO has no recovery from divergence. Picking the robot up and carrying it
is exactly the motion lidar-inertial odometry cannot follow — the estimate was
seen jumping to 46 m away and 17 m up, at which point RViz frames empty space.

Check `map -> base`: if the numbers are absurd it is divergence, not a
visualisation problem. Restart the SLAM.

## Documentation

| document | subject |
|---|---|
| [architecture.md](docs/architecture.md) | the three machines, why DDS cannot cross Wi-Fi, the ParticipantIndex trap |
| [wifi.md](docs/wifi.md) | multicast requirement, bandwidth measurements, hardware options |
| [jetson.md](docs/jetson.md) | how the Jetson was found and addressed, what not to do |
| [remote-desktop.md](docs/remote-desktop.md) | RViz over noVNC, autologin, headless resolution |
| [teleoperation.md](docs/teleoperation.md) | the `mcf` controller, what works and what does not |
| [safety.md](docs/safety.md) | who is allowed to move the robot, and how to stop it |
| [hdmi-checklist.md](docs/hdmi-checklist.md) | first console access, kept for reference |

## Known limitations

DDS does not reach the PC over Wi-Fi. Participant discovery succeeds in both
directions, but endpoint discovery (SEDP) never synchronises and no topics
appear. MTU, routing, CPU load and configuration have all been ruled out by
measurement; the cause is still unknown. If you need live topics on the PC,
`foxglove_bridge` or `rosbridge_suite` are the short path — they run over TCP
and need neither multicast nor DDS discovery.
