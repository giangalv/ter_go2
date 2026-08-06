#!/usr/bin/env python3
"""Command arbiter for the Go2: one mode at a time, and SPACE always stops.

The problem this solves: `/api/sport/request` has no arbitration. Whoever
publishes at 20 Hz wins, so while the autonomy stack's `pathFollower` runs,
neither the keyboard teleop nor the Unitree remote controller can stop the
robot. Verified on the robot — and the robot did walk off on its own, because
`localPlanner` defaults its goal to the map origin and returns to it.

Note that intercepting `/cmd_vel` would NOT help: in this stack `pathFollower`
publishes on both `/cmd_vel` and `/api/sport/request`, and `vel_ctrl_repub` has
its `/cmd_vel` subscription commented out. `/cmd_vel` is informational only —
blocking it would stop the topic while the robot kept walking. The only path
that reaches the motors is the sport request, so that is where this node sits:

    pathFollower --(auto_cmd)--> cmd_mux --(api/sport/request)--> robot
    keyboard ------------------->

## Modes

Exactly one is active, and the mode follows what you actually do:

    STOPPED   nothing moves. Autonomy is ignored and StopMove is sent
              continuously. This is the state the node STARTS in.
    MANUAL    you drive. Autonomy is ignored entirely, not just while a key is
              held: releasing the keys stops the robot but stays in MANUAL.
    AUTO      the stack drives towards its waypoint. The keyboard does not send
              motion, but SPACE still stops everything.

Switching is implicit:

    press a movement key  -> MANUAL
    a waypoint arrives    -> AUTO
    SPACE                 -> STOPPED

so you never end up in autonomy by accident, and driving by hand always takes
the robot away from the planner.

Run it on the Jetson, in a terminal (it needs a TTY):

    ssh -t go2jetson
    source ~/ter_go2/setup_jetson.bash
    ros2 run go2_bringup cmd_mux

This is a software interlock: it protects against the autonomy stack, not
against a crash of this node or a dropped SSH session. The robot's power button
remains the only stop that depends on nothing.
"""

import select
import sys
import termios
import tty

import rclpy
from geometry_msgs.msg import PointStamped
from rclpy.node import Node
from unitree_api.msg import Request

from go2_bringup.sport_client import SportClient

HELP = """
+----------------------------------------------------------------+
|  GO2 - command arbiter. One mode at a time.                    |
+----------------------------------------------------------------+
|  SPACE   STOP. Blocks everything, from any mode.               |
|  x       DAMP: soft motors, the robot collapses. Blocks too.   |
|                                                                |
|  MANUAL  - entered by pressing any movement key                |
|      w             w / s   forward / backward                  |
|    a s d           a / d   strafe                              |
|      q e           q / e   turn                                |
|                                                                |
|  AUTO    - entered when a waypoint arrives, or with g          |
|            the stack drives; the keyboard only stops           |
|                                                                |
|  POSTURE   1 stand up   2 lie down   3 recover                 |
|  LIMIT     - / +   speed limit down / up                       |
|  QUIT      ESC or CTRL-C  (the robot is stopped)               |
+----------------------------------------------------------------+
"""

MOVE_KEYS = {
    'w': (1.0, 0.0, 0.0),
    's': (-1.0, 0.0, 0.0),
    'a': (0.0, 1.0, 0.0),
    'd': (0.0, -1.0, 0.0),
    'q': (0.0, 0.0, 1.0),
    'e': (0.0, 0.0, -1.0),
}

STOPPED, MANUAL, AUTO = 'STOPPED', 'MANUAL', 'AUTO'


class Go2CmdMux(Node):

    def __init__(self):
        super().__init__('go2_cmd_mux')

        self.declare_parameter('auto_topic', 'auto_cmd')
        self.declare_parameter('waypoint_topic', 'way_point')
        self.declare_parameter('max_vx', 0.6)
        self.declare_parameter('max_vy', 0.4)
        self.declare_parameter('max_vyaw', 0.8)
        self.declare_parameter('accel_step', 0.08)
        self.declare_parameter('key_timeout', 0.4)   # s without keys -> stand still
        self.declare_parameter('rate', 20.0)

        self.max_vx = float(self.get_parameter('max_vx').value)
        self.max_vy = float(self.get_parameter('max_vy').value)
        self.max_vyaw = float(self.get_parameter('max_vyaw').value)
        self.accel_step = float(self.get_parameter('accel_step').value)
        self.key_timeout = float(self.get_parameter('key_timeout').value)
        self.rate = float(self.get_parameter('rate').value)

        # Fail safe: start blocked. Autonomy is never assumed.
        self.mode = STOPPED
        self.scale = 1.0
        self.vx = self.vy = self.vyaw = 0.0
        self.last_key_time = 0.0
        self._auto_msg = None
        self._stop_ticks = 0

        self.sport = SportClient(self)   # the only publisher on api/sport/request
        self.create_subscription(
            Request, self.get_parameter('auto_topic').value, self.on_auto, 10)
        self.create_subscription(
            PointStamped, self.get_parameter('waypoint_topic').value,
            self.on_waypoint, 10)

    def on_auto(self, msg: Request):
        """Autonomy commands are held, never forwarded on their own."""
        self._auto_msg = msg

    def on_waypoint(self, msg: PointStamped):
        """A new waypoint is a request to drive autonomously.

        Deliberately ignored while STOPPED: once you have blocked the robot,
        a waypoint arriving from RViz must not start it again behind your back.
        """
        if self.mode == STOPPED:
            self.get_logger().warn(
                'waypoint ignored: STOPPED. Press g to hand control back.')
            return
        if self.mode != AUTO:
            self.mode = AUTO
            self.vx = self.vy = self.vyaw = 0.0
            self.get_logger().info(
                f'AUTO — waypoint ({msg.point.x:.2f}, {msg.point.y:.2f})')

    def _approach(self, current, target):
        delta = target - current
        if delta > self.accel_step:
            return current + self.accel_step
        if delta < -self.accel_step:
            return current - self.accel_step
        return target

    def status(self):
        label = {STOPPED: 'STOPPED', MANUAL: 'MANUAL ', AUTO: 'AUTO   '}[self.mode]
        return (f'\r[{label}] vx={self.vx:+.2f} vy={self.vy:+.2f} '
                f'wz={self.vyaw:+.2f}  limit {self.scale * 100:3.0f}%   ')

    def _stop(self, damp=False, reason=''):
        self.mode = STOPPED
        self.vx = self.vy = self.vyaw = 0.0
        self._stop_ticks = 0
        # Drop whatever the planner had queued, so returning to AUTO cannot
        # replay a command from before the stop.
        self._auto_msg = None
        if damp:
            self.sport.damp()
        self.get_logger().warn(reason)

    def handle_key(self, key, now):
        if key in ('\x1b', '\x03'):
            return False

        if key in MOVE_KEYS:
            self.last_key_time = now
            if self.mode != MANUAL:
                self.mode = MANUAL
                self.vx = self.vy = self.vyaw = 0.0
                self.get_logger().info('MANUAL — autonomy excluded')
            return True

        if key == ' ':
            self._stop(reason='STOP — everything blocked. Press g for autonomy.')
        elif key == 'x':
            self._stop(damp=True, reason='DAMP — soft motors. Everything blocked.')
        elif key == 'g':
            self.mode = AUTO
            self.vx = self.vy = self.vyaw = 0.0
            self.get_logger().info('AUTO — the stack drives')
        elif key == '1':
            self.sport.recovery_stand()
        elif key == '2':
            self.sport.stand_down()
        elif key == '3':
            self.sport.recovery_stand()
        elif key in ('-', '_'):
            self.scale = max(0.1, self.scale - 0.1)
        elif key in ('+', '='):
            self.scale = min(1.0, self.scale + 0.1)
        return True

    def spin_mux(self):
        if not sys.stdin.isatty():
            self.get_logger().error(
                'cmd_mux needs a terminal: it reads the keyboard. '
                'Over ssh use "ssh -t go2jetson", and do not redirect stdin.')
            return
        settings = termios.tcgetattr(sys.stdin)
        print(HELP)
        print('Mode: STOPPED. Nothing can move the robot until you drive or press g.\n')
        try:
            tty.setcbreak(sys.stdin.fileno())
            period = 1.0 / self.rate
            while rclpy.ok():
                now = self.get_clock().now().nanoseconds / 1e9

                target = (0.0, 0.0, 0.0)
                pressed = False
                while select.select([sys.stdin], [], [], 0.0)[0]:
                    key = sys.stdin.read(1)
                    if not self.handle_key(key, now):
                        return
                    if key in MOVE_KEYS and self.mode == MANUAL:
                        target = MOVE_KEYS[key]
                        pressed = True

                if self.mode == STOPPED:
                    # Keep repeating: a single message can be lost, and something
                    # else may still be publishing while it shuts down.
                    if self._stop_ticks < 20 or self._stop_ticks % 10 == 0:
                        self.sport.stop_move()
                    self._stop_ticks += 1

                elif self.mode == MANUAL:
                    # Releasing the keys stops the robot but does NOT hand
                    # control back to the planner: the mode is sticky.
                    if not pressed and now - self.last_key_time > self.key_timeout:
                        target = (0.0, 0.0, 0.0)
                    self.vx = self._approach(self.vx, target[0] * self.max_vx * self.scale)
                    self.vy = self._approach(self.vy, target[1] * self.max_vy * self.scale)
                    self.vyaw = self._approach(self.vyaw, target[2] * self.max_vyaw * self.scale)
                    if any(abs(v) > 1e-3 for v in (self.vx, self.vy, self.vyaw)):
                        self.sport.move(self.vx, self.vy, self.vyaw)
                    else:
                        self.sport.stop_move()

                elif self.mode == AUTO and self._auto_msg is not None:
                    # Forwarded verbatim: the planner knows what it is asking.
                    self.sport._pub.publish(self._auto_msg)
                    self._auto_msg = None

                sys.stdout.write(self.status())
                sys.stdout.flush()
                rclpy.spin_once(self, timeout_sec=period)
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
            for _ in range(5):
                self.sport.stop_move()
            print('\ncmd_mux closed, robot stopped.')


def main(args=None):
    rclpy.init(args=args)
    node = Go2CmdMux()
    try:
        node.spin_mux()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
