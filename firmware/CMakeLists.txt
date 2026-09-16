# ESP-IDF project CMakeLists.txt (spec-required top-level file).
#
# PlatformIO's espidf framework auto-discovers src/ and include/ as the main
# component and does not require this file to build via `pio run`. It is
# kept for spec compliance (structure listed in ТЗ) and for anyone building
# this tree directly with `idf.py` instead of PlatformIO.

cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(hermes_voice_terminal)
