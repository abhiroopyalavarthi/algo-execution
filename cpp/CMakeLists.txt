cmake_minimum_required(VERSION 3.20)
project(algo_cpp LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# The order book comes from the limit-order-book repo (git submodule).
set(ORDERBOOK_DIR ${CMAKE_CURRENT_SOURCE_DIR}/extern/limit-order-book)
if(NOT EXISTS ${ORDERBOOK_DIR}/src/FastOrderBook.cpp)
  message(FATAL_ERROR "order book sources missing, run: git submodule update --init")
endif()

find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
find_package(pybind11 CONFIG REQUIRED)

pybind11_add_module(algo_cpp
  src/bindings.cpp
  src/BarExchange.cpp
  ${ORDERBOOK_DIR}/src/FastOrderBook.cpp)
target_include_directories(algo_cpp PRIVATE src ${ORDERBOOK_DIR}/include)
target_compile_options(algo_cpp PRIVATE -O2 -Wall -Wextra)

install(TARGETS algo_cpp LIBRARY DESTINATION .)
