# Teleoperation

**Working.** Tested on the robot on 2026-08-05.

```bash
ssh -t go2jetson
source ~/ter_go2/setup_jetson.bash
ros2 run go2_bringup teleop_key
```

The `-t` is not optional: without a TTY the keyboard reader will not start.

It has to run **on the Jetson**, not on the PC: the commands travel over the
robot's internal DDS network, which an untethered PC cannot reach.

| key | effect |
|---|---|
| `w` `s` | forward / backward |
| `a` `d` | strafe |
| `q` `e` | yaw |
| `1` | stand up |
| `2` | lie down |
| `3` | recover from a fall |
| SPACE | stop, stay standing |
| `x` | DAMP: soft motors, the robot collapses |
| `-` `+` | speed limit -10% / +10% |

Defaults: 0.6 m/s forward, 0.4 m/s lateral, 0.8 rad/s yaw.

Note: the first trial was run at 0.2 m/s "to be safe" and the robot appeared not
to move at all. Nothing was broken — 0.2 m/s is nearly stationary for a Go2.
Below roughly 0.3 m/s the gait is not meaningful.

## The onboard controller: `mcf`

The Go2 selects its motion controller through
`/api/motion_switcher/request`. On this unit **the only selectable one is
`mcf`** (multi-contact framework).

Verified by trying eight names with `SelectMode` (api_id 1002):

| name | status |
|---|---|
| `normal`, `advanced`, `advanced_sport`, `sport`, `sport_mode` | 7004 — does not exist |
| `mcf` | 0 — accepted (after `ReleaseMode`) |

The 7002 codes seen while a controller is already loaded do **not** indicate a
valid name: after releasing, those return 7004 too. Only `mcf` loads.

### What works and what does not under `mcf`

The classic sport API is only partly implemented:

| command | api_id | result |
|---|---|---|
| `Move` | 1008 | works |
| `StandUp` | 1004 | works |
| `StandDown` | 1005 | works |
| `RecoveryStand` | 1006 | works |
| `StopMove` | 1003 | works |
| **`BalanceStand`** | 1002 | **accepted (status=0) but has no effect** |
| `BodyHeight` | 1013 | rejected, status 3203 |
| `FootRaiseHeight` | 1014 | rejected, status 3203 |
| `GetBodyHeight` / `GetFootRaiseHeight` | 1024 / 1025 | rejected, status 3203 |

This is why key `1` calls `RecoveryStand` rather than `BalanceStand`.

Likewise, in `/sportmodestate` the fields `mode`, `gait_type` and
`foot_raise_height` stay **permanently zero**: `mcf` does not maintain them.
This is not a fault, and they must not be used to infer robot state.
`body_height` is valid (0.07 lying down, 0.32 standing).

## If the robot ignores posture commands

Symptom: the sport service answers queries (`GetSpeedLevel` returns status=0)
but `StandUp` and friends produce no movement, and `/lf/sportmodestate` reports
**`error_code: 1001`**.

The controller is stuck. Reload it like this — it was enough to bring it back
(the robot stood up on its own):

```
/api/motion_switcher/request  api_id 1003  ReleaseMode
/api/motion_switcher/request  api_id 1002  {"name":"mcf"}
```

After the reload `error_code` returns to 100, which is the normal value.

**Careful:** between the release and the selection the robot is left **without a
controller** and the sport API stops responding. Do not leave it in that state,
especially while it is standing.

## Where the advanced control lives

`mcf` exposes only a subset of the classic API, which is consistent with a
controller built to host reinforcement-learning policies rather than to take
scripted locomotion commands.

The Jetson already carries the pieces for that path:

* `~/go2_policies` — trained locomotion policies, versions v1 to v3, flat and
  rough terrain, exported as both `.pt` and `.onnx`;
* `~/isaaclab_sim2real/deploy_policy.py` — deploys an ONNX policy trained in
  Isaac Lab to the real robot, using `unitree_sdk2py`, `LowCmd_`,
  `MotionSwitcherClient` and the remote controller's button bitmasks.

That deployment path releases the high-level controller and drives the motors
directly through `LowCmd` at high rate. It bypasses the protections this
workspace relies on, so it belongs with the robot suspended or in a clear area,
with the remote controller in reach.

For navigation, the high-level `Move` interface used by `cmd_vel_bridge` is the
right layer: Nav2 publishes `/cmd_vel`, the bridge clamps it and forwards it,
and the onboard controller keeps the robot upright.
