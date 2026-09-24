#ifndef VOICE_WIFI_SETUP_H
#define VOICE_WIFI_SETUP_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
  bool configured;
  bool connected;
  bool ap_active;
  uint32_t disconnected_at_ms;
} voice_wifi_setup_t;

void voice_wifi_setup_init(voice_wifi_setup_t *state, bool configured, uint32_t now_ms);
void voice_wifi_setup_set_connected(voice_wifi_setup_t *state, bool connected,
                                   uint32_t now_ms);
void voice_wifi_setup_set_ap_active(voice_wifi_setup_t *state, bool active);
bool voice_wifi_setup_should_start_ap(const voice_wifi_setup_t *state, uint32_t now_ms);
bool voice_wifi_setup_should_stop_ap(const voice_wifi_setup_t *state);
bool voice_wifi_setup_ssid(char *out, size_t capacity, const uint8_t mac[6]);

#endif
