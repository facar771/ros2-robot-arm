from setuptools import find_packages, setup

package_name = 'robot_core'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['tests']),
    package_data={'robot_core': ['web/*']},
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='facar771',
    maintainer_email='facar7711@gmail.com',
    description='ROS-independent core logic for the robot arm project',
    license='Apache-2.0',
)
