#!/usr/bin/env python3
"""A datasheet of the robot, built from what it actually publishes.

Listens for a few seconds on `/lowstate` and `/sportmodestate` and prints a
summary: firmware, battery, the state of all 12 motors, IMU, foot contacts, the
active motion mode. Useful as a pre-flight check before moving the robot, and to
see what this particular EDU unit really exposes.

Usage:
    ros2 run go2_bringup robot_info [--ros-args -p duration:=5.0]
"""

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from unitree_go.msg import LowState, SportModeState

LEG_NAMES = ['FR', 'FL', 'RR', 'RL']
JOINT_SUFFIX = ['hip', 'thigh', 'calf']

# SportModeState.mode -> meaning (Go2, "normal" motion mode).
# Note: under the `mcf` controller this field stays at 0 and must not be used to
# infer the robot's state. See docs/teleoperation.md.
MODES = {
    0: 'idle / damping',
    1: 'balanceStand (standing, active balancing)',
    2: 'pose',
    3: 'locomotion (walking)',
    4: 'reserve',
    5: 'lieDown',
    6: 'jointLock / standUp',
    7: 'damping (soft motors)',
    8: 'recoveryStand',
    9: 'sit',
    10: 'frontFlip',
    11: 'frontJump',
    12: 'frontPounce',
}

GAITS = {
    0: 'idle', 1: 'trot', 2: 'trot running', 3: 'climb stair', 4: 'trot obstacle',
}


class Go2RobotInfo(Node):

    def __init__(self):
        super().__init__('go2_robot_info')
        self.declare_parameter('duration', 5.0)
        self.duration = float(self.get_parameter('duration').value)

        qos = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
            durability=QoSDurabilityPolicy.VOLATILE,
        )
        self.low = None
        self.sport = None
        self.low_count = 0
        self.sport_count = 0

        self.create_subscription(LowState, 'lowstate', self._on_low, qos)
        self.create_subscription(SportModeState, 'sportmodestate', self._on_sport, qos)

    def _on_low(self, msg):
        self.low = msg
        self.low_count += 1

    def _on_sport(self, msg):
        self.sport = msg
        self.sport_count += 1

    def report(self):
        line = '=' * 68
        print(f'\n{line}\n  UNITREE GO2 - AS REPORTED BY THE ROBOT\n{line}')

        if self.low is None:
            print('\n!! Nothing received on /lowstate.')
            print('   Check: Ethernet cable, CYCLONEDDS_URI on the right interface,')
            print('   ROS_DOMAIN_ID=0 and ROS_LOCALHOST_ONLY unset.')
        else:
            m = self.low
            print(f'\n-- IDENTITY AND FIRMWARE  (from /lowstate, {self.low_count} msgs)')
            print(f'   serial (sn)       : {m.sn[0]} / {m.sn[1]}')
            print(f'   firmware version  : {m.version[0]} / {m.version[1]}')
            print(f'   level_flag        : {m.level_flag}   (low=low-level, high=high-level)')
            print(f'   bandwidth         : {m.bandwidth}')

            b = m.bms_state
            cells = [v for v in b.cell_vol if v > 0]
            print('\n-- BATTERY (BMS)')
            print(f'   charge            : {b.soc} %')
            print(f'   current           : {b.current / 1000.0:+.2f} A '
                  f'({"discharging" if b.current < 0 else "charging/idle"})')
            print(f'   pack voltage      : {sum(cells) / 1000.0:.2f} V over {len(cells)} cells')
            print(f'   charge cycles     : {b.cycle}')
            print(f'   BQ temperatures   : {list(b.bq_ntc)} C   MCU: {list(b.mcu_ntc)} C')
            print(f'   supply            : {m.power_v:.2f} V  {m.power_a:.2f} A')

            print('\n-- MOTORS (12 active joints)')
            print(f'   {"joint":<12}{"q [rad]":>10}{"dq [rad/s]":>12}'
                  f'{"tau [Nm]":>10}{"T [C]":>7}{"lost":>7}')
            for leg in range(4):
                for j in range(3):
                    i = leg * 3 + j
                    ms = m.motor_state[i]
                    name = f'{LEG_NAMES[leg]}_{JOINT_SUFFIX[j]}'
                    print(f'   {name:<12}{ms.q:>10.3f}{ms.dq:>12.3f}'
                          f'{ms.tau_est:>10.2f}{ms.temperature:>7}{ms.lost:>7}')
            temps = [m.motor_state[i].temperature for i in range(12)]
            lost = sum(m.motor_state[i].lost for i in range(12))
            print(f'   max temperature {max(temps)} C, min {min(temps)} C | '
                  f'total lost packets: {lost}')

            print('\n-- IMU (body)')
            print(f'   quaternion (w,x,y,z)  : '
                  f'{[round(v, 4) for v in m.imu_state.quaternion]}')
            print(f'   rpy [rad]             : {[round(v, 4) for v in m.imu_state.rpy]}')
            print(f'   gyroscope [rad/s]     : '
                  f'{[round(v, 4) for v in m.imu_state.gyroscope]}')
            print(f'   accelerometer [m/s2]  : '
                  f'{[round(v, 3) for v in m.imu_state.accelerometer]}')
            print(f'   IMU temperature       : {m.imu_state.temperature} C')

            print('\n-- FEET')
            print(f'   contact force     : {list(m.foot_force)}')
            print(f'   estimated         : {list(m.foot_force_est)}')
            print(f'   (order {LEG_NAMES}; a high value means the foot is down)')

            print('\n-- THERMAL')
            print(f'   board NTC         : {m.temperature_ntc1} C / {m.temperature_ntc2} C')
            print(f'   fans [RPM]        : {list(m.fan_frequency)}')

        if self.sport is None:
            print('\n-- MOTION: nothing received on /sportmodestate.')
            print('   If /lowstate arrives but this does not, the sport service is')
            print('   stopped (typical in low-level mode).')
        else:
            s = self.sport
            print(f'\n-- MOTION  (from /sportmodestate, {self.sport_count} msgs)')
            print(f'   mode              : {s.mode} - {MODES.get(s.mode, "unknown")}')
            print(f'   gait              : {s.gait_type} - {GAITS.get(s.gait_type, "?")}')
            print(f'   error code        : {s.error_code}')
            print(f'   body height       : {s.body_height:.3f} m')
            print(f'   foot raise height : {s.foot_raise_height:.3f} m')
            print(f'   position (odom)   : '
                  f'x={s.position[0]:.3f}  y={s.position[1]:.3f}  z={s.position[2]:.3f}')
            print(f'   velocity          : '
                  f'vx={s.velocity[0]:+.3f}  vy={s.velocity[1]:+.3f}  '
                  f'wz={s.yaw_speed:+.3f}')
            print(f'   obstacle ranges   : {[round(v, 2) for v in s.range_obstacle]} m')
            print('\n   Under the `mcf` controller mode, gait and foot raise height')
            print('   stay at zero: that is expected, not a fault.')

        print(f'\n{line}')
        print('  Note: low-level control (/lowcmd) is the feature reserved to the EDU')
        print('  version. It drives the motors directly and WITHOUT protections: use')
        print('  it only with the robot suspended or lying down in a clear area.')
        print(f'{line}\n')


def main(args=None):
    rclpy.init(args=args)
    node = Go2RobotInfo()
    print(f'Listening to the robot for {node.duration:.0f} s...')
    end = node.get_clock().now().nanoseconds + node.duration * 1e9
    try:
        while rclpy.ok() and node.get_clock().now().nanoseconds < end:
            rclpy.spin_once(node, timeout_sec=0.1)
        node.report()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
