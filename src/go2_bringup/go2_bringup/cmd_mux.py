#!/usr/bin/env python3
"""Command arbiter for the Go2: the keyboard always wins.

The problem this solves: `/api/sport/request` has no arbitration. Whoever
publishes at 20 Hz wins, so while the autonomy stack's `pathFollower` runs,
neither the keyboard teleop nor the Unitree remote controller can stop the
robot. Verified on the robot — and the robot did run off on its own, because
`localPlanner` defaults its goal to the map origin and walks back to it.

The fix is to make one node the ONLY publisher of motion commands. The autonomy
stack is remapped to publish on `auto_cmd` instead, and this node decides what
actually reaches the robot:

    pathFollower --(auto_cmd)--> cmd_mux --(api/sport/request)--> robot
    keyboard ------------------->

Priority, highest first:

    STOPPED   latched. Autonomy is ignored and StopMove is sent continuously.
              This is the state the node STARTS in: nothing moves until you
              explicitly allow it.
    MANUAL    a movement key is held: your command goes through, autonomy is
              ignored for as long as you keep driving.
    AUTO      nothing else is happening: autonomy commands are forwarded.

Run it on the Jetson, in a terminal (it needs a TTY):

    ssh -t go2jetson
    source ~/ter_go2/setup_jetson.bash
    ros2 run go2_bringup cmd_mux

Note that this is a software interlock: it protects against the autonomy stack,
not against a crash of this node itself. The robot's power button remains the
only stop that depends on nothing.
"""

import select
import sys
import termios
import tty

import rclpy
from rclpy.node import Node
from unitree_api.msg import Request

from go2_bringup.sport_client import SportClient

HELP = """
+----------------------------------------------------------------+
|  GO2 - command arbiter. The keyboard has priority.             |
+----------------------------------------------------------------+
|  STOP                                                          |
|    SPACE   stop and BLOCK autonomy (latched)                   |
|    x       DAMP: soft motors, the robot collapses, and block   |
|                                                                |
|  AUTONOMY                                                      |
|    g       allow autonomy to drive (go)                        |
|    SPACE   take it away again                                  |
|                                                                |
|  MANUAL DRIVING (hold; overrides autonomy)                     |
|      w             w / s   forward / backward                  |
|    a s d           a / d   strafe                              |
|      q e           q / e   turn                                |
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

STOPPED, AUTO, MANUAL = 'STOPPED', 'AUTO', 'MANUAL'


class Go2CmdMux(Node):

    def __init__(self):
        super().__init__('go2_cmd_mux')

        self.declare_parameter('auto_topic', 'auto_cmd')
        self.declare_parameter('max_vx', 0.6)
        self.declare_parameter('max_vy', 0.4)
        self.declare_parameter('max_vyaw', 0.8)
        self.declare_parameter('accel_step', 0.08)
        self.declare_parameter('manual_timeout', 0.4)   # s without keys -> release
        self.declare_parameter('rate', 20.0)

        self.max_vx = float(self.get_parameter('max_vx').value)
        self.max_vy = float(self.get_parameter('max_vy').value)
        self.max_vyaw = float(self.get_parameter('max_vyaw').value)
        self.accel_step = float(self.get_parameter('accel_step').value)
        self.manual_timeout = float(self.get_parameter('manual_timeout').value)
        self.rate = float(self.get_parameter('rate').value)

        # Fail safe: start blocked. Autonomy has to be granted, never assumed.
        self.state = STOPPED
        self.scale = 1.0
        self.vx = self.vy = self.vyaw = 0.0
        self.last_key_time = 0.0
        self._auto_msg = None
        self._stop_sent = 0

        self.sport = SportClient(self)   # publishes on api/sport/request
        self.create_subscription(
            Request, self.get_parameter('auto_topic').value, self.on_auto, 10)

    def on_auto(self, msg: Request):
        """Autonomy commands are only kept, never forwarded directly."""
        self._auto_msg = msg

    def _approach(self, current, target):
        delta = target - current
        if delta > self.accel_step:
            return current + self.accel_step
        if delta < -self.accel_step:
            return current - self.accel_step
        return target

    def status(self):
        colour = {STOPPED: 'STOPPED  ', AUTO: 'AUTONOMY ', MANUAL: 'MANUAL   '}[self.state]
        return (f'\r[{colour}] vx={self.vx:+.2f} vy={self.vy:+.2f} '
                f'wz={self.vyaw:+.2f}  limit {self.scale * 100:3.0f}%   ')

    def handle_key(self, key, now):
        if key in ('\x1b', '\x03'):
            return False

        if key in MOVE_KEYS:
            # Driving by hand always takes precedence, unless we are latched.
            if self.state != STOPPED:
                self.state = MANUAL
                self.last_key_time = now
            return True

        if key == ' ':
            self.state = STOPPED
            self.vx = self.vy = self.vyaw = 0.0
            self._stop_sent = 0
            self.get_logger().warn('STOP — autonomy blocked. Press g to allow it again.')
        elif key == 'x':
            self.state = STOPPED
            self.vx = self.vy = self.vyaw = 0.0
            self._stop_sent = 0
            self.sport.damp()
            self.get_logger().warn('DAMP — soft motors, the robot collapses. Autonomy blocked.')
        elif key == 'g':
            self.state = AUTO
            self.get_logger().info('autonomy ALLOWED')
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
        settings = termios.tcgetattr(sys.stdin)
        print(HELP)
        print('State: STOPPED. Autonomy cannot move the robot until you press g.\n')
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
                    if key in MOVE_KEYS and self.state == MANUAL:
                        target = MOVE_KEYS[key]
                        pressed = True

                # Manual control lapses when you stop pressing keys.
                if (self.state == MANUAL and not pressed
                        and now - self.last_key_time > self.manual_timeout):
                    self.state = AUTO
                    self.vx = self.vy = self.vyaw = 0.0
                    self.sport.stop_move()

                if self.state == STOPPED:
                    # Keep saying stop: something else may still be publishing,
                    # and a single message can be lost.
                    if self._stop_sent < 20 or self._stop_sent % 10 == 0:
                        self.sport.stop_move()
                    self._stop_sent += 1

                elif self.state == MANUAL:
                    self.vx = self._approach(self.vx, target[0] * self.max_vx * self.scale)
                    self.vy = self._approach(self.vy, target[1] * self.max_vy * self.scale)
                    self.vyaw = self._approach(self.vyaw, target[2] * self.max_vyaw * self.scale)
                    self.sport.move(self.vx, self.vy, self.vyaw)

                elif self.state == AUTO and self._auto_msg is not None:
                    # Forward verbatim: the stack knows what it is asking for.
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
