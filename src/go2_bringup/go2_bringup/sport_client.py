#!/usr/bin/env python3
"""Minimal client for the Go2's "sport" service (high-level control).

High-level commands travel on `/api/sport/request` as `unitree_api/Request`
messages: the action is selected by `header.identity.api_id` and its arguments
are a JSON string in `parameter`.

High level means the onboard controller stays in the loop: it handles balance,
gait and joint limits. This is the safe way to move the robot from a computer.
Low-level control (`/lowcmd`) bypasses all of it.

Note that on this robot the active motion controller is `mcf`, which implements
only part of this API — see docs/teleoperation.md. In particular
`BALANCE_STAND` is accepted but has no effect, and the body-height and
foot-raise-height calls are rejected with status 3203.
"""

import json
import time

from unitree_api.msg import Request

# API ids of the Go2 sport service.
DAMP = 1001            # soft motors: the robot collapses. Emergency stop.
BALANCE_STAND = 1002   # standing, active balancing. No effect under `mcf`.
STOP_MOVE = 1003       # stop translating, stay standing
STAND_UP = 1004        # stand up (stiff)
STAND_DOWN = 1005      # lie down
RECOVERY_STAND = 1006  # get back up after a fall
EULER = 1007           # body attitude
MOVE = 1008            # velocity (x forward, y lateral, z yaw)
SIT = 1009
RISE_SIT = 1010
SPEED_LEVEL = 1015
HELLO = 1016
POSE = 1028

ACTION_NAMES = {
    DAMP: 'Damp', BALANCE_STAND: 'BalanceStand', STOP_MOVE: 'StopMove',
    STAND_UP: 'StandUp', STAND_DOWN: 'StandDown', RECOVERY_STAND: 'RecoveryStand',
    MOVE: 'Move', SIT: 'Sit', RISE_SIT: 'RiseSit', HELLO: 'Hello',
}


class SportClient:
    """Builds and publishes Requests to `/api/sport/request`."""

    def __init__(self, node, topic='api/sport/request'):
        self._node = node
        self._pub = node.create_publisher(Request, topic, 10)

    def _send(self, api_id, parameter=''):
        req = Request()
        # `id` only correlates request and response: it just has to be unique.
        req.header.identity.id = int(time.time() * 1e6) % (2 ** 31)
        req.header.identity.api_id = api_id
        req.header.lease.id = 0
        req.header.policy.priority = 0
        req.header.policy.noreply = False
        req.parameter = parameter
        self._pub.publish(req)

    def move(self, vx, vy, vyaw):
        """Velocity in the body frame: vx forward [m/s], vy left [m/s],
        vyaw counter-clockwise [rad/s]."""
        self._send(MOVE, json.dumps({'x': float(vx), 'y': float(vy), 'z': float(vyaw)}))

    def stop_move(self):
        self._send(STOP_MOVE)

    def damp(self):
        self._send(DAMP)

    def balance_stand(self):
        self._send(BALANCE_STAND)

    def stand_up(self):
        self._send(STAND_UP)

    def stand_down(self):
        self._send(STAND_DOWN)

    def recovery_stand(self):
        self._send(RECOVERY_STAND)

    def hello(self):
        self._send(HELLO)

    def speed_level(self, level):
        """-1 slow, 0 normal, 1 fast."""
        self._send(SPEED_LEVEL, json.dumps({'data': int(level)}))
