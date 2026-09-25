#ifndef VOICE_WIREGUARD_H
#define VOICE_WIREGUARD_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
  bool enabled;
  char address[16];
  char netmask[16];
  char private_key[45];
  char public_key[45];
  char preshared_key[45];
  char endpoint[128];
  char ntp_server[128];
  uint16_t port;
  uint16_t keepalive;
} voice_wireguard_settings_t;

bool voice_wireguard_valid(const voice_wireguard_settings_t *settings);
bool voice_wireguard_parse_u16(const char *value, uint16_t *result);
bool voice_wireguard_ipv4(const char *value, uint32_t *result);
void voice_wireguard_start(const voice_wireguard_settings_t *settings);
const char *voice_wireguard_status(void);
bool voice_wireguard_ready(void);
bool voice_wireguard_should_connect(bool enabled, bool station_online, bool setup_ap_active);
#ifdef ESP_PLATFORM
#include "lwip/sockets.h"
struct ifreq *voice_wireguard_interface(void);
#endif

#endif
