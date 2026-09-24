#ifndef VOICE_SETUP_ACCESS_H
#define VOICE_SETUP_ACCESS_H

#include <stdbool.h>
#include <stdint.h>

bool voice_setup_ipv4_allowed(int family, const uint8_t *address,
                              uint32_t setup_ip_host, uint32_t netmask_host);

#endif
