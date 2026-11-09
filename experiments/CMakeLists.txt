cmake_minimum_required(VERSION 3.18)
project(rdma_hnsw_experiments LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
option(ENABLE_RDMA "Build RDMA transport" OFF)
option(ENABLE_RDMA_CM "Build RDMA CM transport" OFF)
include(CTest)
add_subdirectory(.. upstream)
add_library(lab_strategies src/header_actual_strategy.cc src/strategy_common.cc src/cached_length_strategy.cc src/prefix_strategy.cc src/workload.cc)
target_link_libraries(lab_strategies PUBLIC rdma_hnsw)
target_include_directories(lab_strategies PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_compile_options(lab_strategies PRIVATE -Wall -Wextra -Wpedantic)
add_executable(lab_client src/lab_client.cc)
target_link_libraries(lab_client PRIVATE lab_strategies)
target_compile_options(lab_client PRIVATE -Wall -Wextra -Wpedantic)
find_package(Threads REQUIRED)
add_executable(lab_epoch_client src/epoch_client.cc)
target_link_libraries(lab_epoch_client PRIVATE lab_strategies Threads::Threads)
target_compile_options(lab_epoch_client PRIVATE -Wall -Wextra -Wpedantic)
add_executable(lab_workload src/workload_main.cc)
target_link_libraries(lab_workload PRIVATE lab_strategies)
add_executable(lab_dynamic_client src/dynamic_client.cc)
target_link_libraries(lab_dynamic_client PRIVATE lab_strategies)
target_compile_options(lab_dynamic_client PRIVATE -Wall -Wextra -Wpedantic)
add_executable(lab_update_pilot src/update_pilot.cc)
target_link_libraries(lab_update_pilot PRIVATE rdma_hnsw)
target_compile_options(lab_update_pilot PRIVATE -Wall -Wextra -Wpedantic)
if(BUILD_TESTING)
  add_executable(lab_remote_snapshot_test tests/remote_snapshot_test.cc)
  target_link_libraries(lab_remote_snapshot_test PRIVATE lab_strategies)
  add_test(NAME lab_remote_snapshot_test COMMAND lab_remote_snapshot_test)
  add_executable(lab_transport_options_test tests/transport_options_test.cc)
  target_link_libraries(lab_transport_options_test PRIVATE lab_strategies)
  add_test(NAME lab_transport_options_test COMMAND lab_transport_options_test)
  add_executable(lab_strategy_test tests/strategy_test.cc)
  target_link_libraries(lab_strategy_test PRIVATE lab_strategies)
  target_compile_options(lab_strategy_test PRIVATE -Wall -Wextra -Wpedantic)
  add_test(NAME lab_strategy_test COMMAND lab_strategy_test)
  add_executable(lab_dynamic_test tests/dynamic_test.cc)
  target_link_libraries(lab_dynamic_test PRIVATE lab_strategies)
  add_test(NAME lab_dynamic_test COMMAND lab_dynamic_test)
  add_executable(lab_workload_test tests/workload_test.cc)
  target_link_libraries(lab_workload_test PRIVATE lab_strategies)
  add_test(NAME lab_workload_test COMMAND lab_workload_test)
  find_package(Python3 REQUIRED COMPONENTS Interpreter)
  add_test(NAME lab_build_configuration_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_build_configuration.py -v)
  add_test(NAME lab_prepare_data_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_prepare_data.py -v)
  add_test(NAME lab_analyze_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_analyze.py -v)
  add_test(NAME lab_main_analysis_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_main_analysis.py -v)
  add_test(NAME lab_rdma_preflight_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_rdma_preflight.py -v)
  add_test(NAME lab_rdma_analysis_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_analyze_rdma.py -v)
  add_test(NAME lab_rdma_smoke_test
    COMMAND ${CMAKE_COMMAND} -E env
      LAB_CLIENT=$<TARGET_FILE:lab_client>
      ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_rdma_smoke.py -v)
  add_test(NAME lab_simulate_reads_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_simulate_reads.py -v)
  add_test(NAME lab_client_test
    COMMAND ${CMAKE_COMMAND} -E env
      LAB_CLIENT=$<TARGET_FILE:lab_client>
      HNSW_BUILD=$<TARGET_FILE:hnsw_build>
      ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_lab_client.py -v)
  add_test(NAME lab_epoch_client_test
    COMMAND ${CMAKE_COMMAND} -E env
      LAB_EPOCH_CLIENT=$<TARGET_FILE:lab_epoch_client>
      HNSW_BUILD=$<TARGET_FILE:hnsw_build>
      ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_epoch_client.py -v)
  add_test(NAME lab_rdma_entrypoints_test
    COMMAND ${CMAKE_COMMAND} -E env
      LAB_CLIENT=$<TARGET_FILE:lab_client>
      LAB_DYNAMIC_CLIENT=$<TARGET_FILE:lab_dynamic_client>
      LAB_EPOCH_CLIENT=$<TARGET_FILE:lab_epoch_client>
      ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_rdma_entrypoints.py -v)
  add_test(NAME lab_dynamic_client_test
    COMMAND ${CMAKE_COMMAND} -E env
      LAB_DYNAMIC_CLIENT=$<TARGET_FILE:lab_dynamic_client>
      HNSW_BUILD=$<TARGET_FILE:hnsw_build>
      ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_dynamic_client.py -v)
  add_test(NAME lab_update_pilot_test
    COMMAND ${CMAKE_COMMAND} -E env
      LAB_UPDATE_PILOT=$<TARGET_FILE:lab_update_pilot>
      HNSW_BUILD=$<TARGET_FILE:hnsw_build>
      ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_update_pilot.py -v)
endif()
