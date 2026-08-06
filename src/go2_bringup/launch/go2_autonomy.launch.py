"""The CMU autonomy stack, with the actual Go2 model in the visualisation.

Runs `autonomy_stack_go2` (Point-LIO SLAM + terrain analysis + local planner)
and adds what it does not provide: the robot's URDF, its joint states, and an
RViz configuration that is readable.

    ros2 launch go2_bringup go2_autonomy.launch.py

Run it on the Jetson, inside the graphical session (or with DISPLAY exported),
after sourcing both `~/ter_go2/setup_jetson.bash` and the stack's
`install/setup.bash`.

What is fixed relative to the stack's own RViz:

* the robot was drawn as a set of axes 1 m long with a 0.15 m radius, dwarfing
  everything else. Now `go2_description` is rendered and the axes are a
  discreet 0.25 m marker;
* the waypoint sphere had a 1.2 m radius, larger than the robot itself: 0.12 m;
* TerrainMap, Trajectory and TravArea were disabled — the traversability map is
  the single most useful layer for a legged robot, so they are on.

SAFETY. `/api/sport/request` has no arbitration: any node publishing there at
20 Hz overrides everything else, including the Unitree remote controller, which
stops responding while `pathFollower` runs. Verified on the robot. Keep
`jetson/estop.sh` within reach, and connect a joystick to the Jetson — the
stack is designed around one for manual override (`autonomyMode`, `joySpeed`
in localPlanner.cpp).
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, SetRemap
from launch.conditions import IfCondition
from launch.launch_description_sources import AnyLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    description_share = get_package_share_directory('go2_description')
    bringup_share = get_package_share_directory('go2_bringup')
    stack_share = get_package_share_directory('vehicle_simulator')

    urdf_path = os.path.join(description_share, 'urdf', 'go2_description.urdf')
    with open(urdf_path, 'r') as f:
        robot_description = f.read()

    stack_launch = os.path.join(stack_share, 'launch', 'system_real_robot.launch')

    return LaunchDescription([
        # NOT called `rviz`: the included XML launch declares an argument by that
        # name and sets it to false, and ROS 2 launch configurations are not
        # scoped — ours was being silently overwritten and RViz never started.
        DeclareLaunchArgument('use_rviz', default_value='true'),

        # The stack itself, with its own RViz suppressed: ours replaces it.
        # pathFollower is redirected from api/sport/request to auto_cmd, so it
        # can no longer command the robot directly: cmd_mux decides. Without
        # this the keyboard cannot win — the topic has no arbitration and
        # whoever publishes at 20 Hz takes over, remote controller included.
        SetRemap(src='/api/sport/request', dst='/auto_cmd'),
        IncludeLaunchDescription(
            AnyLaunchDescriptionSource(stack_launch),
            launch_arguments={'rvizGA': 'false'}.items(),
        ),

        Node(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            name='robot_state_publisher',
            output='screen',
            parameters=[{'robot_description': robot_description}],
        ),

        # Joint angles only. The odometry and the sensor transforms come from
        # the stack (map -> camera_init -> aft_mapped -> sensor -> vehicle);
        # publishing ours as well would give the tree two parents for the robot
        # body and make RViz flicker between them.
        Node(
            package='go2_bringup',
            executable='state_bridge',
            name='go2_state_bridge',
            output='screen',
            parameters=[{
                'publish_odom_tf': False,
                'publish_static_tf': False,
                'relay_cloud': False,
            }],
        ),

        # Attach the URDF root to the stack's body frame. `vehicle` already has
        # the sensor offset applied upstream, so this is the identity.
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='vehicleToBase',
            arguments=['0', '0', '0', '0', '0', '0', 'vehicle', 'base'],
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
