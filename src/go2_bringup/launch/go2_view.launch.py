"""Full Go2 visualisation on the PC: TF, 3D model, LiDAR, RViz2.

For use with the Ethernet cable attached, where the PC can reach the robot's DDS
directly.

    ros2 launch go2_bringup go2_view.launch.py
    ros2 launch go2_bringup go2_view.launch.py rviz:=false   # bridges only
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    description_share = get_package_share_directory('go2_description')
    bringup_share = get_package_share_directory('go2_bringup')

    urdf_path = os.path.join(description_share, 'urdf', 'go2_description.urdf')
    with open(urdf_path, 'r') as f:
        robot_description = f.read()

    rviz_arg = DeclareLaunchArgument(
        'rviz', default_value='true', description='Also start RViz2')
    rviz_config_arg = DeclareLaunchArgument(
        'rviz_config',
        default_value=os.path.join(bringup_share, 'rviz', 'go2.rviz'),
        description='RViz2 configuration file')

    robot_state_publisher = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        name='robot_state_publisher',
        output='screen',
        parameters=[{'robot_description': robot_description}],
    )

    state_bridge = Node(
        package='go2_bringup',
        executable='state_bridge',
        name='go2_state_bridge',
        output='screen',
    )

    rviz = Node(
        package='rviz2',
        executable='rviz2',
        name='rviz2',
        output='screen',
        arguments=['-d', LaunchConfiguration('rviz_config')],
        condition=IfCondition(LaunchConfiguration('rviz')),
    )

    return LaunchDescription([
        rviz_arg,
        rviz_config_arg,
        robot_state_publisher,
        state_bridge,
        rviz,
    ])
