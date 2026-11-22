cmake_minimum_required(VERSION 3.8)
project(robot_description)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)
# uncomment the following section in order to fill in
# further dependencies manually.
# find_package(<dependency> REQUIRED)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  # the following line skips the linter which checks for copyrights
  # comment the line when a copyright and license is added to all source files
  set(ament_cmake_copyright_FOUND TRUE)
  # the following line skips cpplint (only works in a git repo)
  # comment the line when this package is in a git repo and when
  # a copyright and license is added to all source files
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

install(DIRECTORY urdf launch rviz DESTINATION share/${PROJECT_NAME})
# Karma robot (KUCUK_ROBOT): generated URDF + home pose for display.launch.py robot:=karma_robot,
# and its link-frame CAD meshes (package://robot_description/meshes/karma_robot/link/*.stl).
# The raw CAD exports (meshes/karma_robot/cad) are inputs of robot_core.mesh_import: not installed.
install(FILES profile/generated/karma_robot/robot.urdf
              profile/generated/karma_robot/initial_positions.yaml
        DESTINATION share/${PROJECT_NAME}/profile/generated/karma_robot)
install(DIRECTORY meshes/karma_robot/link
        DESTINATION share/${PROJECT_NAME}/meshes/karma_robot)
# L9b: the Robot API serves this read-only view model (GET /api/model)
install(FILES profile/generated/view_model.json
        DESTINATION share/${PROJECT_NAME}/profile/generated)

ament_package()
