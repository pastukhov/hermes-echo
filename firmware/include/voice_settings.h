#ifndef VOICE_SETTINGS_H
#define VOICE_SETTINGS_H

#include "voice_wireguard.h"
#include "voice_wifi_profiles.h"
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

#define VOICE_SLEEP_DEFAULT_SECONDS 30U
#define VOICE_SLEEP_MIN_SECONDS 5U
#define VOICE_SLEEP_MAX_SECONDS 3600U

typedef struct {
  voice_wireguard_settings_t wireguard;
  voice_wifi_profile_t wifi[VOICE_WIFI_PROFILE_COUNT];
  char gateway_url[192];
  char device_id[64];
  char device_token[192];
  uint32_t sleep_timeout_seconds;
  char request_id[37];
  char turn_id[37];
} voice_settings_t;

esp_err_t voice_settings_load(voice_settings_t *settings);
esp_err_t voice_settings_save(const voice_settings_t *settings);
void voice_settings_factory_defaults(voice_settings_t *settings);
esp_err_t voice_settings_reset(void);
uint8_t voice_settings_load_brightness(void);
esp_err_t voice_settings_save_brightness(uint8_t level);
bool voice_settings_parse_sleep_timeout(const char *value, uint32_t *seconds);
void voice_settings_migrate_gateway(voice_settings_t *settings);
bool voice_settings_valid(const voice_settings_t *settings);
void voice_settings_set_device_id_from_mac(voice_settings_t *settings,
                                           const uint8_t mac[6]);

#endif
