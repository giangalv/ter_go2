"""Full visualisation RUN ON THE JETSON, viewed in a browser.

Everything runs onboard — bridge, robot model and RViz2 — and only the window's
pixels cross the Wi-Fi. This is how you get RViz without depending on DDS
transport over wireless, which does not work towards the PC (see
docs/architecture.md).

From the PC, open the tunnel and go to the browser (see docs/remote-desktop.md):

    ssh -L 6080:localhost:6080 go2jetson
    http://localhost:6080/vnc.html

Then, in a terminal INSIDE that graphical session:

    source ~/ter_go2/setup_jetson.bash
    ros2 launch go2_bringup go2_view_jetson.launch.py

It has to run there, not from a plain ssh without a display: RViz needs an X
server and the Orin's GPU. Alternatively, export DISPLAY=:0 and
XAUTHORITY=/run/user/1000/gdm/Xauthority first.
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

    rviz_arg = DeclareLaunchArgument('rviz', default_value='true')
    cloud_arg = DeclareLaunchArgument(
        'relay_cloud', default_value='false',
        description='Pointless here: RViz runs on the same machine and reads '
                    '/utlidar/cloud straight from the robot.')

    return LaunchDescription([
        rviz_arg,
        cloud_arg,
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
            parameters=[{'relay_cloud': LaunchConfiguration('relay_cloud')}],
        ),
        Node(
            package='rviz2',
            executable='rviz2',
            name='rviz2',
            output='screen',
            arguments=['-d', os.path.join(bringup_share, 'rviz', 'go2.rviz')],
            condition=IfCondition(LaunchConfiguration('rviz')),
        ),
    ])
