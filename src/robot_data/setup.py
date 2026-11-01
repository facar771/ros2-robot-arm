from setuptools import find_packages, setup

package_name = 'robot_data'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['tests']),
    package_data={package_name: ['migrations/script.py.mako']},
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools', 'sqlalchemy>=2.0', 'alembic'],
    zip_safe=False,
    maintainer='facar771',
    maintainer_email='facar7711@gmail.com',
    description='Runs, events and error reports (SQLAlchemy 2 + Alembic)',
    license='Apache-2.0',
)
