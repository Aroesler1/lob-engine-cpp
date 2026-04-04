cmake_minimum_required(VERSION 3.20)

project(real_time_limit_order_book_engine LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(
    lobster_engine_lib
    src/analytics_engine.cpp
    src/csv_exporter.cpp
    src/lobster_parser.cpp
    src/order_book.cpp
)

target_include_directories(
    lobster_engine_lib
    PUBLIC
        ${CMAKE_SOURCE_DIR}/src
)

target_compile_features(lobster_engine_lib PUBLIC cxx_std_17)

add_executable(lobster_cli src/main.cpp)
target_link_libraries(lobster_cli PRIVATE lobster_engine_lib)

include(CTest)

if(BUILD_TESTING)
    add_executable(parser_tests tests/test_lobster_parser.cpp)
    target_link_libraries(parser_tests PRIVATE lobster_engine_lib)
    target_include_directories(parser_tests PRIVATE ${CMAKE_SOURCE_DIR}/third_party)
    add_test(NAME parser_cpp COMMAND parser_tests)

    add_executable(order_book_tests tests/test_order_book.cpp)
    target_link_libraries(order_book_tests PRIVATE lobster_engine_lib)
    target_include_directories(order_book_tests PRIVATE ${CMAKE_SOURCE_DIR}/third_party)
    target_compile_definitions(order_book_tests PRIVATE TEST_DATA_DIR="${CMAKE_SOURCE_DIR}/data")

    add_test(NAME order_book_cpp COMMAND order_book_tests)

    add_executable(analytics_tests tests/test_analytics_engine.cpp)
    target_link_libraries(analytics_tests PRIVATE lobster_engine_lib)
    target_include_directories(analytics_tests PRIVATE ${CMAKE_SOURCE_DIR}/third_party)
    add_test(NAME analytics_cpp COMMAND analytics_tests)

    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    add_test(
        NAME order_book_python_integration
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_order_book_integration.py
    )
    set_tests_properties(
        order_book_python_integration
        PROPERTIES WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    )

    add_test(
        NAME analytics_python_integration
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_analytics.py
    )
    set_tests_properties(
        analytics_python_integration
        PROPERTIES WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    )
endif()
