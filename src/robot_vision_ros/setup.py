from setuptools import setup

package_name = 'robot_vision_ros'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/tracking.launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='facar771',
    maintainer_email='facar7711@gmail.com',
    description='ROS 2 adapters for robot_vision: tracking node and selftest',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'tracking_node = robot_vision_ros.tracking_node:main',
            'tracking_check = robot_vision_ros.tracking_check:main',
        ],
    },
)
