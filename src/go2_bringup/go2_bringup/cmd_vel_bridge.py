#!/usr/bin/env python3
"""Bridge `/cmd_vel` (geometry_msgs/Twist) to the Go2's sport Move command.

This lets anything that speaks standard ROS 2 drive the robot: a joystick via
`teleop_twist_joy`, Nav2, or a node of your own. The bridge is deliberately not
transparent: it clamps the velocity and runs a watchdog, so a node that stops
publishing or asks for too much cannot launch the robot across the room.

Run it on the Jetson:
    ros2 run go2_bringup cmd_vel_bridge --ros-args -p max_vx:=0.5
"""

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node

from go2_bringup.sport_client import SportClient


def clamp(value, limit):
    return max(-limit, min(limit, value))


class Go2CmdVelBridge(Node):

    def __init__(self):
        super().__init__('go2_cmd_vel_bridge')

        self.declare_parameter('max_vx', 0.6)
        self.declare_parameter('max_vy', 0.4)
        self.declare_parameter('max_vyaw', 0.8)
        self.declare_parameter('timeout', 0.5)   # s without /cmd_vel -> stop
        self.declare_parameter('rate', 20.0)     # Hz at which we resend

        self.max_vx = float(self.get_parameter('max_vx').value)
        self.max_vy = float(self.get_parameter('max_vy').value)
        self.max_vyaw = float(self.get_parameter('max_vyaw').value)
        self.timeout = float(self.get_parameter('timeout').value)
        rate = float(self.get_parameter('rate').value)

        self.sport = SportClient(self)
        self.cmd = (0.0, 0.0, 0.0)
        self.last_cmd_time = None
        self.stopped = True
        self.warned_clamp = False

        self.create_subscription(Twist, 'cmd_vel', self.on_cmd_vel, 10)
        self.create_timer(1.0 / rate, self.on_timer)

        self.get_logger().info(
            f'/cmd_vel -> Go2 | limits vx={self.max_vx} vy={self.max_vy} '
            f'wz={self.max_vyaw}, watchdog {self.timeout}s')

    def on_cmd_vel(self, msg: Twist):
        vx = clamp(msg.linear.x, self.max_vx)
        vy = clamp(msg.linear.y, self.max_vy)
        vyaw = clamp(msg.angular.z, self.max_vyaw)

        if not self.warned_clamp and (
                vx != msg.linear.x or vy != msg.linear.y or vyaw != msg.angular.z):
            self.get_logger().warn(
                'command above the limits, clamped '
                f'({msg.linear.x:.2f},{msg.linear.y:.2f},{msg.angular.z:.2f}) -> '
                f'({vx:.2f},{vy:.2f},{vyaw:.2f})')
            self.warned_clamp = True

        self.cmd = (vx, vy, vyaw)
        self.last_cmd_time = self.get_clock().now()

    def on_timer(self):
        now = self.get_clock().now()
        stale = (self.last_cmd_time is None
                 or (now - self.last_cmd_time).nanoseconds / 1e9 > self.timeout)

        if stale or all(abs(v) < 1e-3 for v in self.cmd):
            if not self.stopped:
                self.sport.stop_move()
                self.stopped = True
                if stale:
                    self.get_logger().warn('watchdog: /cmd_vel went quiet, robot stopped')
            return

        self.sport.move(*self.cmd)
        self.stopped = False


def main(args=None):
    rclpy.init(args=args)
    node = Go2CmdVelBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.sport.stop_move()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
