from setuptools import setup

package_name = 'robot_perception'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/perception.launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='facar771',
    maintainer_email='facar7711@gmail.com',
    description='Colour-object detection node for the simulated RGB-D camera',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'perception_node = robot_perception.perception_node:main',
            'perception_check = robot_perception.perception_check:main',
        ],
    },
)
