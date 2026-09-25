# This file was automatically generated for projects
# without default 'CMakeLists.txt' file.

FILE(GLOB_RECURSE app_sources ${CMAKE_SOURCE_DIR}/src/*.*)

idf_component_register(SRCS ${app_sources} REQUIRES espressif__mdns esp_codec_dev esphome__wireguard)

# Upstream defaults to one AllowedIPs slot, consumed by its own-address /32.
# Reserve another slot for our configured VPN subnet or full-tunnel route.
idf_component_get_property(wireguard_lib esphome__wireguard COMPONENT_LIB)
target_compile_definitions(${wireguard_lib} PUBLIC CONFIG_WIREGUARD_MAX_SRC_IPS=2)
