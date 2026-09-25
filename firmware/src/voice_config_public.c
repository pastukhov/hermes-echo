#include "voice_config_httpd.h"
#include <stdio.h>

// Never serialize server/VPN values: only an explicit allowlist of presence flags.
bool voice_config_public_status(const voice_settings_t *s, char *out, size_t capacity) {
  if (!s || !out || !capacity) return false;
  int length = snprintf(out, capacity,
      "{\"gateway_url_set\":%s,\"device_token_set\":%s,\"wg_address_set\":%s,\"wg_netmask_set\":%s,\"wg_endpoint_set\":%s,\"wg_public_key_set\":%s,\"wg_private_key_set\":%s,\"wg_preshared_key_set\":%s,\"wg_ntp_server_set\":%s,\"wg_port_set\":true,\"wg_keepalive_set\":true,\"wg_enabled\":%s,\"sleep_timeout_seconds\":%lu}",
      s->gateway_url[0] ? "true" : "false",
      s->device_token[0] ? "true" : "false",
      s->wireguard.address[0] ? "true" : "false",
      s->wireguard.netmask[0] ? "true" : "false",
      s->wireguard.endpoint[0] ? "true" : "false",
      s->wireguard.public_key[0] ? "true" : "false",
      s->wireguard.private_key[0] ? "true" : "false",
      s->wireguard.preshared_key[0] ? "true" : "false",
      s->wireguard.ntp_server[0] ? "true" : "false",
      s->wireguard.enabled ? "true" : "false",
      (unsigned long)s->sleep_timeout_seconds);
  if (length < 0 || (size_t)length >= capacity) { out[0] = 0; return false; }
  return true;
}
