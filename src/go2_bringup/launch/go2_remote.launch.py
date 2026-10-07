"""The robot's topics on the PC over Wi-Fi, as plain ROS 2 topics. RUN ON THE JETSON.

DDS does not reach the PC over Wi-Fi (endpoint discovery never completes, see
docs/architecture.md). zenoh-bridge-ros2dds carries it instead: a bridge here
and one on the PC, connected over TCP. On the PC the topics reappear in a local
DDS domain, so RViz2, the ros2 CLI, rqt and rosbag work as if the robot were
wired.

Once, on each machine: zenoh/install_bridge.sh. Then on the Jetson:

    source ~/ter_go2/setup_jetson.bash
    ros2 launch go2_bringup go2_remote.launch.py

and on the PC:

    ~/ter-home/ter_go2/zenoh/bridge_pc.sh             # tunnel + PC bridge
    source ~/ter-home/ter_go2/setup_go2_remote.bash    # in another terminal
    rviz2 -d ~/ter-home/ter_go2/src/go2_bringup/rviz/go2_remote.rviz

The bridge here is OUTGOING ONLY and listens on localhost (zenoh/jetson.json5):
nothing published on the PC is written into the robot's DDS, and the way in is
the SSH tunnel. See docs/remote-ros.md.

What crosses: the front camera (hardware-encoded JPEG, front_camera.py), the
robot model, joints and TF (state_bridge + robot_state_publisher), odometry,
battery, IMU and the LiDAR cloud capped at `cloud_max_hz`. Only the topics the
PC actually subscribes to are sent.
"""

import os

from ament_index_python.packages import get_package_prefix, get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    description_share = get_package_share_directory('go2_description')
    urdf_path = os.path.join(description_share, 'urdf', 'go2_description.urdf')
    with open(urdf_path, 'r') as f:
        robot_description = f.read()

    # The workspace root holds zenoh/ (binary from install_bridge.sh + config).
    workspace = os.path.dirname(os.path.dirname(get_package_prefix('go2_description')))
    zenoh = os.path.join(workspace, 'zenoh')

    args = [
        DeclareLaunchArgument('camera_rate', default_value='10.0',
                              description='Front camera frames per second (max ~15).'),
        DeclareLaunchArgument('camera_width', default_value='640'),
        DeclareLaunchArgument('camera_height', default_value='360'),
        DeclareLaunchArgument('cloud_max_hz', default_value='5.0',
                              description='LiDAR cloud rate on /go2/cloud. The source is '
                                          '~15 Hz for 5.4 Mbit/s.'),
    ]

    return LaunchDescription(args + [
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
                'relay_cloud': True,
                'cloud_max_hz': LaunchConfiguration('cloud_max_hz'),
            }],
        ),
        Node(
            package='go2_bringup',
            executable='front_camera',
            name='go2_front_camera',
            output='screen',
            parameters=[{
                'max_rate': LaunchConfiguration('camera_rate'),
                'width': LaunchConfiguration('camera_width'),
                'height': LaunchConfiguration('camera_height'),
            }],
        ),
        ExecuteProcess(
            cmd=[os.path.join(zenoh, 'bin', 'zenoh-bridge-ros2dds'),
                 '-c', os.path.join(zenoh, 'jetson.json5')],
            additional_env={
                'CYCLONEDDS_URI': 'file://' + os.path.join(zenoh, 'cyclonedds_jetson.xml'),
            },
            name='zenoh_bridge',
            output='screen',
        ),
    ])
