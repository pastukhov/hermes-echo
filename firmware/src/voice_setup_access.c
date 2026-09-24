#include "voice_setup_access.h"

#include <sys/socket.h>

bool voice_setup_ipv4_allowed(int family, const uint8_t *address,
                              uint32_t setup_ip_host, uint32_t netmask_host) {
  if (!address) return false;
  const uint8_t *ipv4 = address;
  if (family == AF_INET6) {
    static const uint8_t mapped_prefix[12] = {
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xff, 0xff};
    if (__builtin_memcmp(address, mapped_prefix, sizeof(mapped_prefix)) != 0) return false;
    ipv4 = address + 12;
  } else if (family != AF_INET) {
    return false;
  }
  uint32_t peer_ip = ((uint32_t)ipv4[0] << 24) |
                     ((uint32_t)ipv4[1] << 16) |
                     ((uint32_t)ipv4[2] << 8) |
                     (uint32_t)ipv4[3];
  return (peer_ip & netmask_host) == (setup_ip_host & netmask_host);
}
