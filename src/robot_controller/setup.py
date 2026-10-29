from setuptools import setup
import os
from glob import glob

package_name = 'robot_controller'

setup(
    name=package_name,
    version='0.0.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='facar771',
    maintainer_email='facar771@todo.todo',
    description='Robot Controller CLI',
    license='Apache-2.0',
    tests_require=['setuptools'],
    entry_points={
        'console_scripts': [
            'move_robot = robot_controller.move_robot:main',
            'cli_moveit = robot_controller.cli_moveit:main',
        ],
    },
)
