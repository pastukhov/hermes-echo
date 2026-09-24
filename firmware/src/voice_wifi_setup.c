#include "voice_wifi_setup.h"

#include <stdio.h>

#define SETUP_AP_DELAY_MS 60000U

void voice_wifi_setup_init(voice_wifi_setup_t *state, bool configured,
                           uint32_t now_ms) {
  if (!state) return;
  *state = (voice_wifi_setup_t){
      .configured = configured,
      .disconnected_at_ms = now_ms,
  };
}

void voice_wifi_setup_set_connected(voice_wifi_setup_t *state, bool connected,
                                   uint32_t now_ms) {
  if (!state || state->connected == connected) return;
  state->connected = connected;
  if (!connected) state->disconnected_at_ms = now_ms;
}

void voice_wifi_setup_set_ap_active(voice_wifi_setup_t *state, bool active) {
  if (state) state->ap_active = active;
}

bool voice_wifi_setup_should_start_ap(const voice_wifi_setup_t *state,
                                      uint32_t now_ms) {
  return state && !state->ap_active && !state->connected &&
         (!state->configured ||
          (uint32_t)(now_ms - state->disconnected_at_ms) >= SETUP_AP_DELAY_MS);
}

bool voice_wifi_setup_should_stop_ap(const voice_wifi_setup_t *state) {
  return state && state->ap_active && state->connected;
}

bool voice_wifi_setup_ssid(char *out, size_t capacity, const uint8_t mac[6]) {
  if (!out || !mac || !capacity) return false;
  int length = snprintf(out, capacity, "Hermes-StickS3-Setup-%02x", mac[5]);
  return length > 0 && (size_t)length < capacity;
}
