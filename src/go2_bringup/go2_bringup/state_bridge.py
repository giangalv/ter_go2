#!/usr/bin/env python3
"""Bridge from the Go2's native DDS topics to standard ROS 2 topics.

The Go2 already publishes the LiDAR (`/utlidar/cloud`, `/utlidar/cloud_base`),
the head IMU (`/utlidar/imu`) and odometry (`/utlidar/robot_odom`) as standard
ROS 2 messages. What is missing for a complete TF tree and an animated model in
RViz2 is:

* `/joint_states`, derived from the 12 motors inside `/lowstate`;
* the `odom -> base_link` transform (the Go2 publishes odometry but no TF);
* the static transforms to the sensor frames (`utlidar_lidar`, `utlidar_imu`)
  and to the URDF root, which `go2_description` calls `base` while Unitree uses
  `base_link`.

This node is read only: it never publishes a command to the robot.
"""

import math

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import BatteryState, Imu, JointState, PointCloud2
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster
from unitree_go.msg import LowState, SportModeState

# Motor order inside LowState.motor_state for the Go2 (indices 0..11).
JOINT_NAMES = [
    'FR_hip_joint', 'FR_thigh_joint', 'FR_calf_joint',
    'FL_hip_joint', 'FL_thigh_joint', 'FL_calf_joint',
    'RR_hip_joint', 'RR_thigh_joint', 'RR_calf_joint',
    'RL_hip_joint', 'RL_thigh_joint', 'RL_calf_joint',
]

# From go2_description/urdf/go2_description.urdf, both children of link `base`.
# (x, y, z, roll, pitch, yaw)
RADAR_JOINT = (0.28945, 0.0, -0.046825, 0.0, 2.8782, 0.0)
IMU_JOINT = (-0.02557, 0.0, 0.04232, 0.0, 0.0, 0.0)


def quat_from_rpy(roll, pitch, yaw):
    """Quaternion (x, y, z, w) from RPY Euler angles, URDF convention."""
    cr, sr = math.cos(roll * 0.5), math.sin(roll * 0.5)
    cp, sp = math.cos(pitch * 0.5), math.sin(pitch * 0.5)
    cy, sy = math.cos(yaw * 0.5), math.sin(yaw * 0.5)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def make_static_tf(parent, child, xyzrpy):
    tf = TransformStamped()
    tf.header.frame_id = parent
    tf.child_frame_id = child
    tf.transform.translation.x = float(xyzrpy[0])
    tf.transform.translation.y = float(xyzrpy[1])
    tf.transform.translation.z = float(xyzrpy[2])
    qx, qy, qz, qw = quat_from_rpy(xyzrpy[3], xyzrpy[4], xyzrpy[5])
    tf.transform.rotation.x = qx
    tf.transform.rotation.y = qy
    tf.transform.rotation.z = qz
    tf.transform.rotation.w = qw
    return tf


class Go2StateBridge(Node):

    def __init__(self):
        super().__init__('go2_state_bridge')

        self.declare_parameter('odom_frame', 'odom')
        self.declare_parameter('base_frame', 'base_link')
        self.declare_parameter('publish_odom_tf', True)
        self.declare_parameter('publish_static_tf', True)
        # Relaying the point cloud is only useful when this node runs on the
        # Jetson: an untethered PC cannot discover the robot's participants and
        # therefore never sees `/utlidar/cloud`. On a cabled PC it is pointless.
        self.declare_parameter('relay_cloud', False)
        self.declare_parameter('cloud_max_hz', 0.0)  # 0 = no limit
        self.declare_parameter('joint_rate', 50.0)
        self.declare_parameter('imu_rate', 50.0)
        self.declare_parameter('battery_rate', 1.0)
        # The Go2 publishes every state in two variants. Measured on the robot:
        #   /lowstate        499.7 Hz     /lf/lowstate        20.0 Hz
        #   /sportmodestate  300.0 Hz     /lf/sportmodestate  20.0 Hz
        # The `lf` ("low frequency") ones exist precisely for consumers that are
        # not closing a control loop. Deserialising 500 Hz in Python cost 74-86%
        # of one core on the Orin NX and, because of the GIL, starved DDS
        # discovery to the point where topics stopped appearing on the PC. For
        # visualisation, navigation and telemetry 20 Hz is plenty; switching to
        # /lowstate only makes sense for low-level control.
        self.declare_parameter('lowstate_topic', 'lf/lowstate')
        self.declare_parameter('sportmodestate_topic', 'lf/sportmodestate')

        self.odom_frame = self.get_parameter('odom_frame').value
        self.base_frame = self.get_parameter('base_frame').value
        self.publish_odom_tf = self.get_parameter('publish_odom_tf').value

        # Unitree publishes best effort: with the default reliable QoS nothing
        # would arrive.
        sensor_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
            durability=QoSDurabilityPolicy.VOLATILE,
        )

        self.joint_pub = self.create_publisher(JointState, 'joint_states', 10)
        self.battery_pub = self.create_publisher(BatteryState, 'go2/battery', 10)
        self.imu_pub = self.create_publisher(Imu, 'go2/imu', 10)
        self.odom_pub = self.create_publisher(Odometry, 'odom', 10)

        self.tf_broadcaster = TransformBroadcaster(self)
        self.static_broadcaster = StaticTransformBroadcaster(self)

        self.create_subscription(
            LowState, self.get_parameter('lowstate_topic').value,
            self.on_lowstate, sensor_qos)
        self.create_subscription(
            SportModeState, self.get_parameter('sportmodestate_topic').value,
            self.on_sportmodestate, sensor_qos)
        self.create_subscription(
            Odometry, 'utlidar/robot_odom', self.on_robot_odom, sensor_qos)

        if self.get_parameter('publish_static_tf').value:
            self.static_broadcaster.sendTransform([
                # The URDF root is `base`, Unitree uses `base_link`.
                make_static_tf(self.base_frame, 'base', (0, 0, 0, 0, 0, 0)),
                make_static_tf(self.base_frame, 'utlidar_lidar', RADAR_JOINT),
                make_static_tf(self.base_frame, 'utlidar_imu', IMU_JOINT),
            ])

        # Last known velocity, used to fill in the odometry twist
        # (`/utlidar/robot_odom` leaves it at zero).
        self._velocity = (0.0, 0.0, 0.0)
        self._lowstate_count = 0

        def period(name):
            hz = float(self.get_parameter(name).value)
            return 1.0 / hz if hz > 0 else 0.0

        self._joint_period = period('joint_rate')
        self._imu_period = period('imu_rate')
        self._battery_period = period('battery_rate')
        self._last_pub = {'joint': 0.0, 'imu': 0.0, 'battery': 0.0}

        self._cloud_pub = None
        self._cloud_min_period = 0.0
        self._cloud_last = 0.0
        if self.get_parameter('relay_cloud').value:
            max_hz = float(self.get_parameter('cloud_max_hz').value)
            self._cloud_min_period = 1.0 / max_hz if max_hz > 0 else 0.0
            self._cloud_pub = self.create_publisher(PointCloud2, 'go2/cloud', sensor_qos)
            self.create_subscription(
                PointCloud2, 'utlidar/cloud', self.on_cloud, sensor_qos)
            limit = f'capped at {max_hz:g} Hz' if max_hz > 0 else 'uncapped'
            self.get_logger().info(f'relaying LiDAR cloud on go2/cloud ({limit})')

        self.get_logger().info(
            'go2_state_bridge running: joint_states, go2/battery, go2/imu, odom, TF.')

    def _due(self, key, period):
        """True when enough time has passed since the last publication."""
        if period <= 0.0:
            return True
        t = self.get_clock().now().nanoseconds / 1e9
        if t - self._last_pub[key] < period:
            return False
        self._last_pub[key] = t
        return True

    def on_lowstate(self, msg: LowState):
        now = self.get_clock().now().to_msg()

        if self._due('joint', self._joint_period):
            js = JointState()
            js.header.stamp = now
            js.name = JOINT_NAMES
            js.position = [float(msg.motor_state[i].q) for i in range(12)]
            js.velocity = [float(msg.motor_state[i].dq) for i in range(12)]
            js.effort = [float(msg.motor_state[i].tau_est) for i in range(12)]
            self.joint_pub.publish(js)

        if not self._due('imu', self._imu_period):
            self._count_and_log(msg)
            return

        imu = Imu()
        imu.header.stamp = now
        imu.header.frame_id = 'imu'
        # Unitree orders the quaternion as (w, x, y, z).
        qw, qx, qy, qz = msg.imu_state.quaternion
        imu.orientation.x = float(qx)
        imu.orientation.y = float(qy)
        imu.orientation.z = float(qz)
        imu.orientation.w = float(qw)
        imu.angular_velocity.x = float(msg.imu_state.gyroscope[0])
        imu.angular_velocity.y = float(msg.imu_state.gyroscope[1])
        imu.angular_velocity.z = float(msg.imu_state.gyroscope[2])
        imu.linear_acceleration.x = float(msg.imu_state.accelerometer[0])
        imu.linear_acceleration.y = float(msg.imu_state.accelerometer[1])
        imu.linear_acceleration.z = float(msg.imu_state.accelerometer[2])
        self.imu_pub.publish(imu)

        if self._due('battery', self._battery_period):
            # BmsState reserves 15 cell slots, but the Go2 pack is 8S (~28.8 V
            # nominal, measured 32.35 V at 83%): the unused slots stay at zero
            # and must be discarded rather than summed.
            cells = [v / 1000.0 for v in msg.bms_state.cell_vol if v > 0]
            bat = BatteryState()
            bat.header.stamp = now
            bat.percentage = float(msg.bms_state.soc) / 100.0
            bat.voltage = sum(cells)
            bat.current = float(msg.bms_state.current) / 1000.0
            bat.cell_voltage = cells
            bat.temperature = float(max(msg.bms_state.bq_ntc))
            bat.power_supply_technology = BatteryState.POWER_SUPPLY_TECHNOLOGY_LION
            bat.present = True
            self.battery_pub.publish(bat)

        self._count_and_log(msg)

    def _count_and_log(self, msg: LowState):
        self._lowstate_count += 1
        if self._lowstate_count % 5000 == 0:
            self.get_logger().info(
                f'battery {msg.bms_state.soc}% | '
                f'max motor temp {max(m.temperature for m in msg.motor_state[:12])} C')

    def on_cloud(self, msg: PointCloud2):
        if self._cloud_min_period > 0.0:
            now = self.get_clock().now().nanoseconds / 1e9
            if now - self._cloud_last < self._cloud_min_period:
                return
            self._cloud_last = now
        self._cloud_pub.publish(msg)

    def on_sportmodestate(self, msg: SportModeState):
        self._velocity = (
            float(msg.velocity[0]), float(msg.velocity[1]), float(msg.yaw_speed))

    def on_robot_odom(self, msg: Odometry):
        msg.child_frame_id = self.base_frame
        msg.twist.twist.linear.x = self._velocity[0]
        msg.twist.twist.linear.y = self._velocity[1]
        msg.twist.twist.angular.z = self._velocity[2]
        self.odom_pub.publish(msg)

        if not self.publish_odom_tf:
            return

        tf = TransformStamped()
        tf.header = msg.header
        tf.child_frame_id = self.base_frame
        tf.transform.translation.x = msg.pose.pose.position.x
        tf.transform.translation.y = msg.pose.pose.position.y
        tf.transform.translation.z = msg.pose.pose.position.z
        tf.transform.rotation = msg.pose.pose.orientation
        self.tf_broadcaster.sendTransform(tf)


def main(args=None):
    rclpy.init(args=args)
    node = Go2StateBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
