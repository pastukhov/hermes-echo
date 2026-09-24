#include "voice_settings.h"
#include "voice_turn_client.h"

#include <stdio.h>
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
  copy_field(s->device_token, sizeof(s->device_token), VOICE_DEVICE_TOKEN);
  s->protocol_version = 1;
}

bool voice_settings_valid(const voice_settings_t *s) {
  if (!s || !s->wifi_ssid[0] || !s->device_id[0] ||
      (s->protocol_version != 1 && s->protocol_version != 2)) return false;
  const char *url = s->gateway_url;
  const char *host = NULL;
  if (strncmp(url, "http://", 7) == 0) host = url + 7;
  else if (strncmp(url, "https://", 8) == 0) host = url + 8;
  if (!host || !host[0]) return false;
  if (s->protocol_version == 2) {
    if (!s->device_token[0]) return false;
    char upload_url[256];
    if (!voice_turn_build_upload_url(url, upload_url, sizeof(upload_url))) return false;
  }
  return true;
}

void voice_settings_set_device_id_from_mac(voice_settings_t *s,
                                           const uint8_t mac[6]) {
  if (!s || !mac) return;
  snprintf(s->device_id, sizeof(s->device_id), "%02x%02x%02x%02x%02x%02x",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
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
    {"request_id", s->request_id, sizeof(s->request_id)},
    {"turn_id", s->turn_id, sizeof(s->turn_id)},
  };
  for (size_t i = 0; i < sizeof(fields) / sizeof(fields[0]); ++i) {
    size_t len = fields[i].cap;
    (void)nvs_get_str(h, fields[i].key, fields[i].value, &len);
  }
  int32_t protocol_version = 1;
  if (nvs_get_i32(h, "protocol_version", &protocol_version) == ESP_OK &&
      (protocol_version == 1 || protocol_version == 2))
    s->protocol_version = protocol_version;
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
    {"device_token", s->device_token}, {"request_id", s->request_id},
    {"turn_id", s->turn_id},
  };
  for (size_t i = 0; i < sizeof(fields) / sizeof(fields[0]); ++i) {
    err = nvs_set_str(h, fields[i].key, fields[i].value);
    if (err != ESP_OK) break;
  }
  if (err == ESP_OK && (s->protocol_version == 1 || s->protocol_version == 2))
    err = nvs_set_i32(h, "protocol_version", s->protocol_version);
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
