#ifndef VOICE_SETTINGS_H
#define VOICE_SETTINGS_H

#include <stddef.h>
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
} voice_settings_t;

esp_err_t voice_settings_load(voice_settings_t *settings);
esp_err_t voice_settings_save(const voice_settings_t *settings);

#endif
