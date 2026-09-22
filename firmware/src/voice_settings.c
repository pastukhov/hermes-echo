#include "voice_settings.h"

#include <string.h>

#ifdef ESP_PLATFORM
#include "nvs.h"
#include "nvs_flash.h"
#endif

#ifndef VOICE_WIFI_SSID
#define VOICE_WIFI_SSID ""
#endif
#ifndef VOICE_WIFI_PASSWORD
#define VOICE_WIFI_PASSWORD ""
#endif
#ifndef VOICE_GATEWAY_URL
#define VOICE_GATEWAY_URL "http://192.168.1.10:8000/api/v1/voice/turn"
#endif
#ifndef VOICE_DEVICE_ID
#define VOICE_DEVICE_ID "sticks3-01"
#endif
#ifndef VOICE_DEVICE_TOKEN
#define VOICE_DEVICE_TOKEN ""
#endif

static void copy_field(char *dst, size_t cap, const char *src) {
  if (!dst || cap == 0) return;
  if (!src) src = "";
  strncpy(dst, src, cap - 1);
  dst[cap - 1] = '\0';
}

static void defaults(voice_settings_t *s) {
  memset(s, 0, sizeof(*s));
  copy_field(s->wifi_ssid, sizeof(s->wifi_ssid), VOICE_WIFI_SSID);
  copy_field(s->wifi_password, sizeof(s->wifi_password), VOICE_WIFI_PASSWORD);
  copy_field(s->gateway_url, sizeof(s->gateway_url), VOICE_GATEWAY_URL);
  copy_field(s->device_id, sizeof(s->device_id), VOICE_DEVICE_ID);
  copy_field(s->device_token, sizeof(s->device_token), VOICE_DEVICE_TOKEN);
}

#ifdef ESP_PLATFORM
typedef struct { const char *key; char *value; size_t cap; } field_t;

esp_err_t voice_settings_load(voice_settings_t *s) {
  if (!s) return ESP_ERR_INVALID_ARG;
  defaults(s);
  esp_err_t flash_err = nvs_flash_init();
  if (flash_err == ESP_ERR_NVS_NO_FREE_PAGES ||
      flash_err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    if (nvs_flash_erase() != ESP_OK) return flash_err;
    flash_err = nvs_flash_init();
  }
  if (flash_err != ESP_OK) return flash_err;
  nvs_handle_t h;
  esp_err_t err = nvs_open("hermes", NVS_READONLY, &h);
  if (err == ESP_ERR_NVS_NOT_FOUND) return ESP_OK;
  if (err != ESP_OK) return err;
  field_t fields[] = {
    {"wifi_ssid", s->wifi_ssid, sizeof(s->wifi_ssid)},
    {"wifi_password", s->wifi_password, sizeof(s->wifi_password)},
    {"gateway_url", s->gateway_url, sizeof(s->gateway_url)},
    {"device_id", s->device_id, sizeof(s->device_id)},
    {"device_token", s->device_token, sizeof(s->device_token)},
  };
  for (size_t i = 0; i < sizeof(fields) / sizeof(fields[0]); ++i) {
    size_t len = fields[i].cap;
    (void)nvs_get_str(h, fields[i].key, fields[i].value, &len);
  }
  nvs_close(h);
  return ESP_OK;
}

esp_err_t voice_settings_save(const voice_settings_t *s) {
  if (!s) return ESP_ERR_INVALID_ARG;
  nvs_handle_t h;
  esp_err_t err = nvs_open("hermes", NVS_READWRITE, &h);
  if (err != ESP_OK) return err;
  const struct { const char *key; const char *value; } fields[] = {
    {"wifi_ssid", s->wifi_ssid}, {"wifi_password", s->wifi_password},
    {"gateway_url", s->gateway_url}, {"device_id", s->device_id},
    {"device_token", s->device_token},
  };
  for (size_t i = 0; i < sizeof(fields) / sizeof(fields[0]); ++i) {
    err = nvs_set_str(h, fields[i].key, fields[i].value);
    if (err != ESP_OK) break;
  }
  if (err == ESP_OK) err = nvs_commit(h);
  nvs_close(h);
  return err;
}
#else
esp_err_t voice_settings_load(voice_settings_t *s) {
  if (!s) return ESP_ERR_INVALID_ARG;
  defaults(s);
  return ESP_OK;
}
esp_err_t voice_settings_save(const voice_settings_t *s) {
  return s ? ESP_OK : ESP_ERR_INVALID_ARG;
}
#endif
