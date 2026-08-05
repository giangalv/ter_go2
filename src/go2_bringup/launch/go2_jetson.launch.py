"""Jetson side: read the robot on the internal network and republish to the PC.

Run this on the onboard Jetson (ROS 2 Foxy), with the environment from
`jetson/setup_jetson.bash`, which pins CycloneDDS to `eth0` (multicast, towards
the robot) and `wlan0` (unicast peer, towards the PC).

    ros2 launch go2_bringup go2_jetson.launch.py

Neither robot_state_publisher nor RViz2 run here: they stay on the PC, so the
Wi-Fi carries data rather than the URDF meshes.

Note that DDS from the Jetson to the PC currently does not complete endpoint
discovery — see the known limitations in the README. The working alternative is
`go2_view_jetson.launch.py` plus the browser-based remote desktop.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    relay_cloud_arg = DeclareLaunchArgument(
        'relay_cloud', default_value='true',
        description='Republish the LiDAR cloud on /go2/cloud')
    cloud_max_hz_arg = DeclareLaunchArgument(
        'cloud_max_hz', default_value='0.0',
        description='Rate cap for the cloud (0 = no cap). The source runs at '
                    '~15 Hz for 5.4 Mbit/s: if the Wi-Fi struggles, drop to 5.0')

    state_bridge = Node(
        package='go2_bringup',
        executable='state_bridge',
        name='go2_state_bridge',
        output='screen',
        parameters=[{
            'relay_cloud': LaunchConfiguration('relay_cloud'),
            'cloud_max_hz': LaunchConfiguration('cloud_max_hz'),
        }],
    )

    return LaunchDescription([relay_cloud_arg, cloud_max_hz_arg, state_bridge])
