from setuptools import setup

package_name = 'robot_agent'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='facar771',
    maintainer_email='facar7711@gmail.com',
    description='Safe LLM tool layer over the Robot API v1',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'robot_agent = robot_agent.cli:main',
        ],
    },
)
