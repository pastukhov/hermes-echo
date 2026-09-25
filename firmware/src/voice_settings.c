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
  copy_field(s->wifi[0].ssid, sizeof(s->wifi[0].ssid), VOICE_WIFI_SSID);
  copy_field(s->wifi[0].password, sizeof(s->wifi[0].password), VOICE_WIFI_PASSWORD);
  copy_field(s->gateway_url, sizeof(s->gateway_url), VOICE_GATEWAY_URL);
  copy_field(s->device_token, sizeof(s->device_token), VOICE_DEVICE_TOKEN);
  s->wireguard.port = 51820;
  s->wireguard.keepalive = 25;
  copy_field(s->wireguard.netmask, sizeof(s->wireguard.netmask), "255.255.255.0");
  copy_field(s->wireguard.ntp_server, sizeof(s->wireguard.ntp_server), "pool.ntp.org");
  s->protocol_version = 1;
  s->sleep_timeout_seconds = VOICE_SLEEP_DEFAULT_SECONDS;
}

bool voice_settings_parse_sleep_timeout(const char *value, uint32_t *seconds) {
  if (!value || !value[0] || !seconds) return false;
  uint32_t n = 0;
  for (const char *p = value; *p; ++p) {
    if (*p < '0' || *p > '9') return false;
    n = n * 10 + (uint32_t)(*p - '0');
    if (n > VOICE_SLEEP_MAX_SECONDS) return false;
  }
  if (n < VOICE_SLEEP_MIN_SECONDS) return false;
  *seconds = n;
  return true;
}

bool voice_settings_valid(const voice_settings_t *s) {
  if (!s || !voice_wireguard_valid(&s->wireguard) || !voice_wifi_profiles_valid(s->wifi) || !s->device_id[0] ||
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
    {"wg_address", s->wireguard.address, sizeof(s->wireguard.address)},
    {"wg_netmask", s->wireguard.netmask, sizeof(s->wireguard.netmask)},
    {"wg_private_key", s->wireguard.private_key, sizeof(s->wireguard.private_key)},
    {"wg_public_key", s->wireguard.public_key, sizeof(s->wireguard.public_key)},
    {"wg_psk", s->wireguard.preshared_key, sizeof(s->wireguard.preshared_key)},
    {"wg_endpoint", s->wireguard.endpoint, sizeof(s->wireguard.endpoint)},
    {"wg_ntp_server", s->wireguard.ntp_server, sizeof(s->wireguard.ntp_server)},
    {"wifi_ssid", s->wifi[0].ssid, sizeof(s->wifi[0].ssid)},
    {"wifi_password", s->wifi[0].password, sizeof(s->wifi[0].password)},
    {"wifi1_ssid", s->wifi[1].ssid, sizeof(s->wifi[1].ssid)},
    {"wifi1_password", s->wifi[1].password, sizeof(s->wifi[1].password)},
    {"wifi2_ssid", s->wifi[2].ssid, sizeof(s->wifi[2].ssid)},
    {"wifi2_password", s->wifi[2].password, sizeof(s->wifi[2].password)},
    {"wifi3_ssid", s->wifi[3].ssid, sizeof(s->wifi[3].ssid)},
    {"wifi3_password", s->wifi[3].password, sizeof(s->wifi[3].password)},
    {"wifi4_ssid", s->wifi[4].ssid, sizeof(s->wifi[4].ssid)},
    {"wifi4_password", s->wifi[4].password, sizeof(s->wifi[4].password)},
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
  uint8_t enabled = 0, full_tunnel = 0;
  (void)nvs_get_u8(h, "wg_enabled", &enabled);
  (void)nvs_get_u8(h, "wg_full_tunnel", &full_tunnel);
  s->wireguard.enabled = enabled != 0;
  s->wireguard.full_tunnel = full_tunnel != 0;
  (void)nvs_get_u16(h, "wg_port", &s->wireguard.port);
  (void)nvs_get_u16(h, "wg_keepalive", &s->wireguard.keepalive);
  int32_t protocol_version = 1;
  if (nvs_get_i32(h, "protocol_version", &protocol_version) == ESP_OK &&
      (protocol_version == 1 || protocol_version == 2))
    s->protocol_version = protocol_version;
  uint32_t sleep_seconds;
  if (nvs_get_u32(h, "sleep_seconds", &sleep_seconds) == ESP_OK &&
      sleep_seconds >= VOICE_SLEEP_MIN_SECONDS && sleep_seconds <= VOICE_SLEEP_MAX_SECONDS)
    s->sleep_timeout_seconds = sleep_seconds;
  nvs_close(h);
  return ESP_OK;
}

esp_err_t voice_settings_save(const voice_settings_t *s) {
  if (!s) return ESP_ERR_INVALID_ARG;
  nvs_handle_t h;
  esp_err_t err = nvs_open("hermes", NVS_READWRITE, &h);
  if (err != ESP_OK) return err;
  const struct { const char *key; const char *value; } fields[] = {
    {"wg_address", s->wireguard.address},
    {"wg_netmask", s->wireguard.netmask},
    {"wg_private_key", s->wireguard.private_key},
    {"wg_public_key", s->wireguard.public_key},
    {"wg_psk", s->wireguard.preshared_key},
    {"wg_endpoint", s->wireguard.endpoint},
    {"wg_ntp_server", s->wireguard.ntp_server},
    {"wifi_ssid", s->wifi[0].ssid}, {"wifi_password", s->wifi[0].password},
    {"wifi1_ssid", s->wifi[1].ssid},
    {"wifi1_password", s->wifi[1].password},
    {"wifi2_ssid", s->wifi[2].ssid},
    {"wifi2_password", s->wifi[2].password},
    {"wifi3_ssid", s->wifi[3].ssid},
    {"wifi3_password", s->wifi[3].password},
    {"wifi4_ssid", s->wifi[4].ssid},
    {"wifi4_password", s->wifi[4].password},
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
  if (err == ESP_OK) err = nvs_set_u32(h, "sleep_seconds", s->sleep_timeout_seconds);
  if (err == ESP_OK) err = nvs_set_u8(h, "wg_enabled", s->wireguard.enabled);
  if (err == ESP_OK) err = nvs_set_u8(h, "wg_full_tunnel", s->wireguard.full_tunnel);
  if (err == ESP_OK) err = nvs_set_u16(h, "wg_port", s->wireguard.port);
  if (err == ESP_OK) err = nvs_set_u16(h, "wg_keepalive", s->wireguard.keepalive);
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
