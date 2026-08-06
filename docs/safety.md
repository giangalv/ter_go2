# Safety: who is allowed to move the robot

Read this before running anything that drives the Go2.

## The core problem

`/api/sport/request` has **no arbitration**. It is a plain topic: every node
that publishes on it is heard, and the robot simply obeys the most recent
message. A node publishing at 20 Hz therefore wins against everything else.

Measured on the robot: with the autonomy stack running there were **12
publishers** on that topic. In that state,

* the keyboard teleop cannot stop the robot — you press SPACE, `StopMove` goes
  out, and `pathFollower` overwrites it 50 ms later;
* **the Unitree remote controller stops responding.** It came back the instant
  `pathFollower` was killed.

## What actually happened

The robot walked off on its own and had to be caught by hand.

It was not a malfunction. `localPlanner` takes its goal from

```xml
<arg name="goalX" value="$(var vehicleX)"/>   <!-- 0.0 -->
<arg name="goalY" value="$(var vehicleY)"/>   <!-- 0.0 -->
```

that is, **the origin of the map** — the point where SLAM initialised. While the
robot sits there, goal and position coincide and nothing moves. Move the robot
away from it, by hand or because the estimate converged elsewhere, and it will
walk back. That order was standing from the moment the stack started.

The mistake was leaving the driving nodes running while working on
visualisation, which needs none of them.

## How to stop the robot, in order of reliability

1. **The robot's power button.** Depends on nothing — no network, no software,
   no radio. On a walking quadruped this is the real emergency stop.
2. **`cmd_mux`** (below), if it is the only publisher of motion commands.
3. **The Unitree remote**, but only when no node is flooding the sport topic.
4. **`jetson/estop.sh` from the PC** — kills the driving nodes and sends
   `StopMove`. Requires the network to be up, which is exactly what failed at
   the worst moment.
5. **RViz: not possible.** The stack's plugins add waypoints, they do not remove
   them. There is no stop button.

## Two safe ways to work

### Mapping and visualisation — the robot cannot move

```bash
ros2 launch go2_bringup go2_mapping.launch.py
```

Same SLAM, terrain analysis, robot model and RViz, but `localPlanner` and
`pathFollower` are simply not started. Nothing can command the robot. Use this
for anything that is not driving.

### Driving — through the arbiter

```bash
# terminal 1: the stack, with pathFollower redirected to /auto_cmd
ros2 launch go2_bringup go2_autonomy.launch.py

# terminal 2, with a TTY: the arbiter
ssh -t go2jetson
source ~/ter_go2/setup_jetson.bash
ros2 run go2_bringup cmd_mux
```

`cmd_mux` is then the **only** publisher of motion commands:

```
pathFollower --(auto_cmd)--> cmd_mux --(api/sport/request)--> robot
keyboard ------------------->
```

Exactly one mode is active, and the mode follows what you actually do:

| mode | behaviour | entered by |
|---|---|---|
| `STOPPED` | nothing moves, `StopMove` sent continuously | SPACE, or `x` to also go limp |
| `MANUAL` | you drive, autonomy excluded entirely | pressing any movement key |
| `AUTO` | the stack drives towards its waypoint | a waypoint arriving, or `g` |

`MANUAL` is sticky: releasing the keys stops the robot but does **not** hand
control back to the planner. You leave manual mode deliberately, never by
letting go.

A waypoint arriving while `STOPPED` is **ignored**, with a warning. Once you
have blocked the robot, a click in RViz must not restart it behind your back.

It **starts in `STOPPED`**: autonomy has to be asked for, never assumed.

### Why not intercept /cmd_vel

The obvious idea is to block the navigation's `/cmd_vel`. In this stack that
would do nothing: `pathFollower` publishes on **both** `/cmd_vel` and
`/api/sport/request`, and `vel_ctrl_repub` has its `/cmd_vel` subscription
commented out. Nothing consumes `/cmd_vel` to move the robot — it is
informational only. Blocking it would stop the topic while the robot kept
walking, which is worse than doing nothing because it looks like it worked.

The sport request is the only path to the motors, so that is where the arbiter
sits. (With Nav2 this would be different: it really does drive through
`/cmd_vel`, and `twist_mux` is the standard answer there.)

This is a software interlock. It protects against the autonomy stack, not
against a crash of the arbiter itself — the power button stays the last resort.

## The upstream design assumes a joystick

`localPlanner.cpp` has `autonomyMode`, `joySpeed`, `joyManualFwd`: the author
expects a joystick connected to the machine running the stack, providing manual
override without going through the network. There is none here, and `joy_node`
spins on a `/dev/input/js0` that does not exist.

`cmd_mux` replaces that role over SSH. A real gamepad would still be better:
it does not depend on a terminal, an SSH session or the Wi-Fi holding up.
