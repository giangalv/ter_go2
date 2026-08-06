"""Mapping and visualisation only — the robot physically cannot be commanded.

Same stack as `go2_autonomy.launch.py` (Point-LIO SLAM, terrain analysis, the
Go2 model in RViz) but with the two nodes that drive the robot left out:

* `localPlanner` — computes the local path towards the goal;
* `pathFollower` — turns that path into velocity commands on
  `/api/sport/request`.

Use this whenever you are working on maps, frames or visualisation. The robot
stays where it is because nothing is telling it to move.

    ros2 launch go2_bringup go2_mapping.launch.py

WHY THIS EXISTS. `/api/sport/request` has no arbitration: whoever publishes at
20 Hz wins, and `pathFollower` publishes continuously. While it runs, neither
the keyboard teleop nor the Unitree remote controller can stop the robot —
verified on the robot, the remote reconnected the instant pathFollower was
killed. On top of that, `localPlanner` defaults its goal to the map origin
(`goalX=0, goalY=0`), so the moment the robot is moved away from where SLAM
initialised — carried by hand, or after the estimate converges — it will walk
back there on its own. That is not a malfunction, it is a standing order.

So: mapping work happens here. Driving happens in `go2_autonomy.launch.py`, in
a clear area, with a joystick connected to the Jetson for manual override.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import AnyLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    description_share = get_package_share_directory('go2_description')
    bringup_share = get_package_share_directory('go2_bringup')
    slam_share = get_package_share_directory('point_lio_unilidar')
    terrain_share = get_package_share_directory('terrain_analysis')
    scan_share = get_package_share_directory('sensor_scan_generation')

    urdf_path = os.path.join(description_share, 'urdf', 'go2_description.urdf')
    with open(urdf_path, 'r') as f:
        robot_description = f.read()

    return LaunchDescription([
        # Not called `rviz`: the included SLAM launch declares an argument by
        # that name and sets it to false. ROS 2 launch configurations are not
        # scoped, so ours would be silently overwritten.
        DeclareLaunchArgument('use_rviz', default_value='true'),

        # --- SLAM: Point-LIO on the L1 lidar and its built-in IMU ---
        IncludeLaunchDescription(
            AnyLaunchDescriptionSource(
                os.path.join(slam_share, 'launch', 'mapping_utlidar.launch')),
            launch_arguments={'rviz': 'false'}.items(),
        ),

        # --- perception, no motion ---
        IncludeLaunchDescription(
            AnyLaunchDescriptionSource(
                os.path.join(terrain_share, 'launch', 'terrain_analysis.launch'))),
        IncludeLaunchDescription(
            AnyLaunchDescriptionSource(
                os.path.join(scan_share, 'launch', 'sensor_scan_generation.launch'))),

        # --- frames the stack expects ---
        Node(package='tf2_ros', executable='static_transform_publisher',
             name='mapToCameraInit',
             arguments=['0', '0', '0', '0', '0', '0', 'map', 'camera_init']),
        Node(package='tf2_ros', executable='static_transform_publisher',
             name='aftMappedToSensor',
             arguments=['0', '0', '0', '0', '0', '0', 'aft_mapped', 'sensor']),
        Node(package='tf2_ros', executable='static_transform_publisher',
             name='sensorToVehicle',
             arguments=['0', '0', '0', '0', '0', '0', 'sensor', 'vehicle']),
        # URDF root onto the stack's body frame.
        Node(package='tf2_ros', executable='static_transform_publisher',
             name='vehicleToBase',
             arguments=['0', '0', '0', '0', '0', '0', 'vehicle', 'base']),

        # --- front camera ---
        # multicast_iface is eth0: the stack ships it set to enp3s0, which is
        # the interface name on the author's external PC, not on the Jetson.
        Node(
            package='go2_h264_repub',
            executable='go2_h264_repub',
            name='go2_h264_repub',
            output='screen',
            parameters=[{'multicast_iface': 'eth0'}],
        ),

        # --- the robot model, animated from the real joint angles ---
        Node(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            name='robot_state_publisher',
            output='screen',
            parameters=[{'robot_description': robot_description}],
        ),
        Node(
            package='go2_bringup',
            executable='state_bridge',
            name='go2_state_bridge',
            output='screen',
            parameters=[{
                'publish_odom_tf': False,     # odometry comes from Point-LIO
                'publish_static_tf': False,   # frames are declared above
                'relay_cloud': False,
            }],
        ),

        Node(
            package='rviz2',
            executable='rviz2',
            name='rviz2',
            output='screen',
            arguments=['-d', os.path.join(bringup_share, 'rviz', 'go2_autonomy.rviz')],
            condition=IfCondition(LaunchConfiguration('use_rviz')),
        ),
    ])
