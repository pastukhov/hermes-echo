#include "voice_wireguard.h"
#include <stddef.h>
#include <string.h>

bool voice_wireguard_parse_u16(const char *value, uint16_t *result) {
  if (!value || !*value || !result) return false;
  uint32_t n = 0;
  for (; *value; ++value) {
    if (*value < '0' || *value > '9') return false;
    n = n * 10 + (unsigned)(*value - '0');
    if (n > 65535) return false;
  }
  *result = (uint16_t)n;
  return true;
}

bool voice_wireguard_ipv4(const char *value, uint32_t *result) {
  if (!value || !result) return false;
  uint32_t ip = 0;
  for (int i = 0; i < 4; ++i) {
    unsigned octet = 0, digits = 0;
    while (*value >= '0' && *value <= '9') {
      octet = octet * 10 + (unsigned)(*value++ - '0');
      if (++digits > 3 || octet > 255) return false;
    }
    if (!digits || (i < 3 ? *value++ != '.' : *value != '\0')) return false;
    ip = (ip << 8) | octet;
  }
  *result = ip;
  return true;
}

static bool valid_key(const char *key) {
  /* 32 bytes in canonical base64: 43 characters and one padding '='. */
  static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  if (strlen(key) != 44 || key[43] != '=') return false;
  bool nonzero = false;
  for (unsigned i = 0; i < 43; ++i) {
    const char *p = strchr(alphabet, key[i]);
    if (!p || (i == 42 && ((p - alphabet) & 3))) return false;
    nonzero |= p != alphabet;
  }
  return nonzero;
}

static bool valid_host(const char *host) {
  size_t n = strlen(host);
  if (!n || n > 127 || host[0] == '-' || host[n - 1] == '-') return false;
  for (; *host; ++host)
    if (!((*host >= 'a' && *host <= 'z') || (*host >= 'A' && *host <= 'Z') ||
          (*host >= '0' && *host <= '9') || *host == '-' || *host == '.')) return false;
  return true;
}

bool voice_wireguard_valid(const voice_wireguard_settings_t *s) {
  if (!s) return false;
  if (!s->enabled) return true;
  uint32_t address, mask;
  if (!voice_wireguard_ipv4(s->address, &address) ||
      !voice_wireguard_ipv4(s->netmask, &mask)) return false;
  uint32_t inverse = ~mask;
  if (!mask || (inverse & (inverse + 1)) || !address ||
      (address >> 24) == 127 || (address >> 24) >= 224) return false;
  if (inverse > 1 && (!(address & inverse) || (address & inverse) == inverse)) return false;
  return valid_key(s->private_key) && valid_key(s->public_key) &&
         (!s->preshared_key[0] || valid_key(s->preshared_key)) &&
         valid_host(s->endpoint) && valid_host(s->ntp_server) && s->port > 0;
}
