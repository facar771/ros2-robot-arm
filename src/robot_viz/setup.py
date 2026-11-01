from setuptools import setup

package_name = 'robot_viz'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/web_view.launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='facar771',
    maintainer_email='facar7711@gmail.com',
    description='Read-only browser view of the robot (camera, detections, joints)',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'web_view = robot_viz.web_view:main',
            'viz_check = robot_viz.viz_check:main',
        ],
    },
)
