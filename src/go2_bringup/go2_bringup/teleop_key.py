#!/usr/bin/env python3
"""Keyboard teleoperation for the Go2: high level, with protections.

Active safeguards:

* configurable speed limits;
* a watchdog: if no key arrives within `deadman_timeout` seconds the robot is
  sent StopMove. Holding a key keeps it moving, releasing it stops it;
* acceleration limiting, to avoid jerks that upset the gait;
* an emergency stop always available (SPACE stops, X goes soft);
* the terminal is always restored on exit, even after an exception.

Run it on the Jetson, with a TTY:
    ssh -t go2jetson
    ros2 run go2_bringup teleop_key
    ros2 run go2_bringup teleop_key --ros-args -p max_vx:=0.8
"""

import select
import sys
import termios
import tty

import rclpy
from rclpy.node import Node

from go2_bringup.sport_client import SportClient

HELP = """
+--------------------------------------------------------------+
|  UNITREE GO2 - safe teleoperation                            |
+--------------------------------------------------------------+
|  MOTION (hold the key)                                       |
|      w              w / s   forward / backward               |
|    a s d            a / d   strafe left / right              |
|      q e            q / e   turn left / right                |
|                                                              |
|  POSTURE                                                     |
|    1  stand up                2  lie down                    |
|    3  recover from a fall     4  say hello                   |
|                                                              |
|  SAFETY                                                      |
|    SPACE   stop now (stays standing)                         |
|    x       DAMP: soft motors, the robot collapses            |
|    -  +    lower / raise the speed limit                     |
|    ESC or CTRL-C  quit (the robot is stopped)                |
+--------------------------------------------------------------+
"""

MOVE_KEYS = {
    'w': (1.0, 0.0, 0.0),
    's': (-1.0, 0.0, 0.0),
    'a': (0.0, 1.0, 0.0),
    'd': (0.0, -1.0, 0.0),
    'q': (0.0, 0.0, 1.0),
    'e': (0.0, 0.0, -1.0),
}


class Go2TeleopKey(Node):

    def __init__(self):
        super().__init__('go2_teleop_key')

        # Below roughly 0.3 m/s a Go2 barely moves: setting these very low "to
        # be safe" makes it look broken rather than cautious.
        self.declare_parameter('max_vx', 0.6)          # m/s forward/backward
        self.declare_parameter('max_vy', 0.4)          # m/s lateral
        self.declare_parameter('max_vyaw', 0.8)        # rad/s yaw
        self.declare_parameter('accel_step', 0.08)     # max change per cycle
        self.declare_parameter('deadman_timeout', 0.4)  # s without keys -> stop
        self.declare_parameter('rate', 20.0)           # Hz

        self.max_vx = float(self.get_parameter('max_vx').value)
        self.max_vy = float(self.get_parameter('max_vy').value)
        self.max_vyaw = float(self.get_parameter('max_vyaw').value)
        self.accel_step = float(self.get_parameter('accel_step').value)
        self.deadman_timeout = float(self.get_parameter('deadman_timeout').value)
        self.rate = float(self.get_parameter('rate').value)

        self.scale = 1.0     # multiplier adjusted with - and +
        self.vx = self.vy = self.vyaw = 0.0
        self.last_key_time = 0.0
        self.stopped = True

        self.sport = SportClient(self)

    def _approach(self, current, target):
        """Move `current` towards `target` by at most `accel_step`."""
        delta = target - current
        if delta > self.accel_step:
            return current + self.accel_step
        if delta < -self.accel_step:
            return current - self.accel_step
        return target

    def status(self):
        return (f'\rvx={self.vx:+.2f} vy={self.vy:+.2f} wz={self.vyaw:+.2f} m/s '
                f'| limit {self.scale * 100:3.0f}% '
                f'| {"STOPPED " if self.stopped else "MOVING  "}')

    def handle_key(self, key, now):
        """Handle one key. Returns False to quit."""
        if key == '\x1b' or key == '\x03':
            return False

        if key in MOVE_KEYS:
            self.last_key_time = now
            return True

        if key == ' ':
            self.vx = self.vy = self.vyaw = 0.0
            self.sport.stop_move()
            self.stopped = True
            self.get_logger().info('STOP')
        elif key == 'x':
            self.vx = self.vy = self.vyaw = 0.0
            self.sport.damp()
            self.stopped = True
            self.get_logger().warn('DAMP - soft motors, the robot collapses')
        elif key == '1':
            # Under the `mcf` controller (the only selectable one on this Go2)
            # BalanceStand is accepted with status=0 but has no effect: the
            # robot stays as it is. RecoveryStand does stand it up, both from
            # lying down and after a fall. Verified on the robot.
            self.sport.recovery_stand()
            self.get_logger().info('RecoveryStand (standing up)')
        elif key == '2':
            self.sport.stand_down()
            self.get_logger().info('StandDown')
        elif key == '3':
            self.sport.recovery_stand()
            self.get_logger().info('RecoveryStand')
        elif key == '4':
            self.sport.hello()
        elif key in ('-', '_'):
            self.scale = max(0.1, self.scale - 0.1)
        elif key in ('+', '='):
            self.scale = min(1.0, self.scale + 0.1)
        return True

    def spin_teleop(self):
        settings = termios.tcgetattr(sys.stdin)
        print(HELP)
        print('The robot must be standing: press 1 if it is lying down.\n')
        try:
            tty.setcbreak(sys.stdin.fileno())
            period = 1.0 / self.rate
            while rclpy.ok():
                now = self.get_clock().now().nanoseconds / 1e9

                # Drain the buffer: key auto-repeat piles up several keystrokes
                # per cycle and only the latest state matters.
                target = (0.0, 0.0, 0.0)
                pressed = False
                while select.select([sys.stdin], [], [], 0.0)[0]:
                    key = sys.stdin.read(1)
                    if not self.handle_key(key, now):
                        return
                    if key in MOVE_KEYS:
                        target = MOVE_KEYS[key]
                        pressed = True

                if not pressed and (now - self.last_key_time) > self.deadman_timeout:
                    target = (0.0, 0.0, 0.0)

                self.vx = self._approach(self.vx, target[0] * self.max_vx * self.scale)
                self.vy = self._approach(self.vy, target[1] * self.max_vy * self.scale)
                self.vyaw = self._approach(
                    self.vyaw, target[2] * self.max_vyaw * self.scale)

                moving = any(abs(v) > 1e-3 for v in (self.vx, self.vy, self.vyaw))
                if moving:
                    self.sport.move(self.vx, self.vy, self.vyaw)
                    self.stopped = False
                elif not self.stopped:
                    # First cycle at zero velocity: stop explicitly.
                    self.sport.stop_move()
                    self.stopped = True

                sys.stdout.write(self.status())
                sys.stdout.flush()
                rclpy.spin_once(self, timeout_sec=period)
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
            # Stop the robot however the session ended.
            for _ in range(3):
                self.sport.stop_move()
            print('\nTeleoperation ended, robot stopped.')


def main(args=None):
    rclpy.init(args=args)
    node = Go2TeleopKey()
    try:
        node.spin_teleop()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
