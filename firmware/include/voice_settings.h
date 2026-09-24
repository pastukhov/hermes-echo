#ifndef VOICE_SETTINGS_H
#define VOICE_SETTINGS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef ESP_PLATFORM
#include "esp_err.h"
#else
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_INVALID_ARG 0x102
#endif

typedef struct {
  char wifi_ssid[33];
  char wifi_password[65];
  char gateway_url[192];
  char device_id[64];
  char device_token[192];
  int32_t protocol_version;
  char request_id[37];
  char turn_id[37];
} voice_settings_t;

esp_err_t voice_settings_load(voice_settings_t *settings);
esp_err_t voice_settings_save(const voice_settings_t *settings);
bool voice_settings_valid(const voice_settings_t *settings);
void voice_settings_set_device_id_from_mac(voice_settings_t *settings,
                                           const uint8_t mac[6]);

#endif
