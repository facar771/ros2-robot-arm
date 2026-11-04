from glob import glob

from setuptools import find_packages, setup

package_name = 'robot_hw_bridge'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['tests']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob('launch/*.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='facar771',
    maintainer_email='facar7711@gmail.com',
    description='ESP32 hardware link (B1) and ROS 2 bridge node (B2)',
    license='Apache-2.0',
    entry_points={
        'console_scripts': ['esp32_sim = robot_hw_bridge.cli:main',
                            'hw_bridge_node = robot_hw_bridge.node:main'],
    },
)
