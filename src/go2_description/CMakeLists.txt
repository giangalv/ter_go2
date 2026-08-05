cmake_minimum_required(VERSION 3.8)
project(go2_description)

find_package(ament_cmake REQUIRED)

install(
  DIRECTORY urdf dae meshes xacro
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
