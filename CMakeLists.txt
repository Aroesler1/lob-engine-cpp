cmake_minimum_required(VERSION 3.14)

project(lob_engine LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(lob_parser
    src/parser.cpp
    src/analytics.cpp
    src/pipeline.cpp
)

target_include_directories(lob_parser
    PUBLIC
        ${CMAKE_SOURCE_DIR}/include
)

target_compile_definitions(lob_parser
    PUBLIC
        LOB_ENGINE_SOURCE_DIR="${CMAKE_SOURCE_DIR}"
)

add_executable(lob_engine
    src/main.cpp
)

target_link_libraries(lob_engine
    PRIVATE
        lob_parser
)

add_executable(test_parser
    tests/test_parser.cpp
)

target_link_libraries(test_parser
    PRIVATE
        lob_parser
)

add_executable(run_benchmarks
    benchmark/run_benchmarks.cpp
)

target_link_libraries(run_benchmarks
    PRIVATE
        lob_parser
)

add_executable(verify_correctness
    benchmark/verify_correctness.cpp
)

target_link_libraries(verify_correctness
    PRIVATE
        lob_parser
)
