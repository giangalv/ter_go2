from glob import glob

from setuptools import setup

package_name = 'go2_bringup'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob('launch/*.launch.py')),
        ('share/' + package_name + '/rviz', glob('rviz/*.rviz')),
        ('share/' + package_name + '/config', glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Gianluca Galvagni',
    maintainer_email='gianluca.galvagni99@gmail.com',
    description='Bringup, visualisation and safe teleoperation for the Unitree Go2 EDU.',
    license='BSD-3-Clause',
    entry_points={
        'console_scripts': [
            'state_bridge = go2_bringup.state_bridge:main',
            'teleop_key = go2_bringup.teleop_key:main',
            'cmd_vel_bridge = go2_bringup.cmd_vel_bridge:main',
            'cmd_mux = go2_bringup.cmd_mux:main',
            'robot_info = go2_bringup.robot_info:main',
            'front_camera = go2_bringup.front_camera:main',
        ],
    },
)
