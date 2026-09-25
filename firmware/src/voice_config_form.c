#include "voice_config_httpd.h"
#include <stdlib.h>
#include <string.h>

static bool decode_form_component(const char *src, size_t len, char *dst, size_t cap) {
  size_t out = 0;
  for (size_t i = 0; i < len; ++i) {
    unsigned char ch = (unsigned char)src[i];
    if (ch == '+') ch = ' ';
    else if (ch == '%') {
      if (i + 2 >= len) return false;
      char hex[3] = {src[i + 1], src[i + 2], 0};
      char *end = NULL;
      unsigned long value = strtoul(hex, &end, 16);
      if (end != hex + 2 || value == 0 || value > 0xff) return false;
      ch = (unsigned char)value;
      i += 2;
    }
    if (ch < 0x20 || ch == 0x7f || out + 1 >= cap) return false;
    dst[out++] = (char)ch;
  }
  dst[out] = '\0';
  return true;
}

static bool update_form_field(char *dst, size_t cap, const char *value) {
  size_t n = strlen(value);
  if (n >= cap) return false;
  memcpy(dst, value, n + 1);
  return true;
}

bool voice_config_parse_form(char *body, voice_settings_t *next) {
  if (!body || !next) return false;
  bool clear_psk = false;
  char old_ssids[VOICE_WIFI_PROFILE_COUNT][33];
  bool password_given[VOICE_WIFI_PROFILE_COUNT] = {0};
  bool open_network[VOICE_WIFI_PROFILE_COUNT] = {0};
  for (int i = 0; i < VOICE_WIFI_PROFILE_COUNT; ++i)
    memcpy(old_ssids[i], next->wifi[i].ssid, sizeof(old_ssids[i]));
  char *cursor = body;
  while (*cursor) {
    char *pair_end = strchr(cursor, '&');
    if (!pair_end) pair_end = cursor + strlen(cursor);
    char *equals = memchr(cursor, '=', (size_t)(pair_end - cursor));
    if (equals) {
      char key[40], value[256];
      if (!decode_form_component(cursor, (size_t)(equals - cursor), key, sizeof(key)) ||
          !decode_form_component(equals + 1, (size_t)(pair_end - equals - 1), value, sizeof(value))) return false;
      char *dst = NULL; size_t cap = 0;
      if (strcmp(key, "protocol_version") == 0) {
        if (strcmp(value, "1") == 0) next->protocol_version = 1;
        else if (strcmp(value, "2") == 0) next->protocol_version = 2;
        else return false;
      }
      else if (strcmp(key, "sleep_timeout_seconds") == 0) {
        if (!voice_settings_parse_sleep_timeout(value, &next->sleep_timeout_seconds)) return false;
      }
      else if (strncmp(key, "wifi", 4) == 0) {
        int index;
        const char *field;
        if (strcmp(key, "wifi_ssid") == 0 || strcmp(key, "wifi_password") == 0) {
          index = 0;
          field = key + 5;
        } else {
          if (key[4] < '0' || key[4] >= '0' + VOICE_WIFI_PROFILE_COUNT || key[5] != '_') return false;
          index = key[4] - '0';
          field = key + 6;
        }
        if (strcmp(field, "ssid") == 0) {
          if (!update_form_field(next->wifi[index].ssid, sizeof(next->wifi[index].ssid), value)) return false;
        } else if (strcmp(field, "password") == 0) {
          if (value[0]) {
            if (!update_form_field(next->wifi[index].password, sizeof(next->wifi[index].password), value)) return false;
            password_given[index] = true;
          }
        } else if (strcmp(field, "open") == 0) {
          if (strcmp(value, "0") != 0 && strcmp(value, "1") != 0) return false;
          open_network[index] = value[0] == '1';
        } else return false;
      }
      else if (strcmp(key, "gateway_url") == 0) { dst = next->gateway_url; cap = sizeof(next->gateway_url); }
      else if (strcmp(key, "device_token") == 0) { dst = next->device_token; cap = sizeof(next->device_token); }
      else if (strcmp(key, "wg_enabled") == 0 || strcmp(key, "wg_full_tunnel") == 0 ||
               strcmp(key, "wg_clear_psk") == 0) {
        if (strcmp(value, "0") != 0 && strcmp(value, "1") != 0) return false;
        if (strcmp(key, "wg_enabled") == 0) next->wireguard.enabled = value[0] == '1';
        else if (strcmp(key, "wg_full_tunnel") == 0) next->wireguard.full_tunnel = value[0] == '1';
        else clear_psk = value[0] == '1';
      }
      else if (strcmp(key, "wg_port") == 0) {
        if (!voice_wireguard_parse_u16(value, &next->wireguard.port)) return false;
      }
      else if (strcmp(key, "wg_keepalive") == 0) {
        if (!voice_wireguard_parse_u16(value, &next->wireguard.keepalive)) return false;
      }
      else if (strcmp(key, "wg_address") == 0) { dst = next->wireguard.address; cap = sizeof(next->wireguard.address); }
      else if (strcmp(key, "wg_netmask") == 0) { dst = next->wireguard.netmask; cap = sizeof(next->wireguard.netmask); }
      else if (strcmp(key, "wg_endpoint") == 0) { dst = next->wireguard.endpoint; cap = sizeof(next->wireguard.endpoint); }
      else if (strcmp(key, "wg_public_key") == 0) { dst = next->wireguard.public_key; cap = sizeof(next->wireguard.public_key); }
      else if (strcmp(key, "wg_private_key") == 0) { dst = next->wireguard.private_key; cap = sizeof(next->wireguard.private_key); }
      else if (strcmp(key, "wg_preshared_key") == 0) { dst = next->wireguard.preshared_key; cap = sizeof(next->wireguard.preshared_key); }
      else if (strcmp(key, "wg_ntp_server") == 0) { dst = next->wireguard.ntp_server; cap = sizeof(next->wireguard.ntp_server); }
      /* Empty secret fields mean "keep the saved secret" in this UI. */
      bool secret = strcmp(key, "wifi_password") == 0 || strcmp(key, "device_token") == 0 ||
        strcmp(key, "wg_private_key") == 0 || strcmp(key, "wg_preshared_key") == 0;
      if (dst && !(secret && !value[0]) && !update_form_field(dst, cap, value)) return false;
    }
    cursor = *pair_end ? pair_end + 1 : pair_end;
  }
  for (int i = 0; i < VOICE_WIFI_PROFILE_COUNT; ++i) {
    if (!next->wifi[i].ssid[0] || open_network[i]) next->wifi[i].password[0] = '\0';
    else if (strcmp(old_ssids[i], next->wifi[i].ssid) != 0 && !password_given[i]) return false;
  }
  if (clear_psk) next->wireguard.preshared_key[0] = '\0';
  return voice_settings_valid(next);
}

