#include "http_voice_client.h"
#include "voice_turn_client.h"
#include "voice_http_chunk.h"
#include <string.h>
#include <stdio.h>

#ifdef ESP_PLATFORM
#include "esp_http_client.h"
#include "voice_wireguard.h"
#include "esp_log.h"
static const char *TAG = "voice_http";

static int write_socket(void *context, const char *data, size_t length) {
  return esp_http_client_write((esp_http_client_handle_t)context, data, (int)length);
}

static voice_transport_result_t begin(voice_transport_t *t) {
  if (!voice_wireguard_ready()) return VOICE_TRANSPORT_FATAL;
  http_voice_client_t *c = (http_voice_client_t *)t->ctx;
  if (c->config.protocol_version == 2) c->turn_id[0] = '\0';
  const char *url = c->config.url;
  if (c->config.protocol_version == 2) {
    if (!voice_turn_build_upload_url(c->config.url, c->upload_url,
                                     sizeof(c->upload_url)))
      return VOICE_TRANSPORT_FATAL;
    url = c->upload_url;
  }
  esp_http_client_config_t cfg = {.url = url,
                                  .if_name = voice_wireguard_interface(),
                                  .timeout_ms = c->config.timeout_ms > 0 ? c->config.timeout_ms : 15000};
  c->client = esp_http_client_init(&cfg);
  if (!c->client) return VOICE_TRANSPORT_FATAL;
  esp_http_client_set_method(c->client, HTTP_METHOD_POST);
  esp_http_client_set_header(c->client, "Content-Type", "audio/L16");
  esp_http_client_set_header(c->client, "X-Sample-Rate", "16000");
  esp_http_client_set_header(c->client, "X-Channels", "1");
  esp_http_client_set_header(c->client, "X-Sample-Format", "s16le");
  esp_http_client_set_header(c->client, "X-Protocol-Version",
                             c->config.protocol_version == 2 ? "2" : "1");
  if (c->config.protocol_version == 2) {
    if (!c->config.request_id ||
        strlen(c->config.request_id) != VOICE_TURN_REQUEST_ID_CAPACITY - 1 ||
        esp_http_client_set_header(c->client, "X-Request-Id",
                                   c->config.request_id) != ESP_OK)
      return VOICE_TRANSPORT_FATAL;
  }
  if (c->config.device_id) esp_http_client_set_header(c->client, "X-Device-Id", c->config.device_id);
  if (c->config.token) {
    char auth[192];
    int n = snprintf(auth, sizeof(auth), "Bearer %s", c->config.token);
    if (n <= 0 || (size_t)n >= sizeof(auth)) return VOICE_TRANSPORT_FATAL;
    esp_http_client_set_header(c->client, "Authorization", auth);
  }
  return esp_http_client_open(c->client, -1) == ESP_OK ? VOICE_TRANSPORT_OK : VOICE_TRANSPORT_FATAL;
}

static voice_transport_write_result_t write_body(voice_transport_t *t, const uint8_t *data, size_t len) {
  http_voice_client_t *c = (http_voice_client_t *)t->ctx;
  if (!voice_http_chunk_write(write_socket, c->client, data, len))
    return (voice_transport_write_result_t){0, VOICE_TRANSPORT_FATAL};
  return (voice_transport_write_result_t){len, VOICE_TRANSPORT_OK};
}

static voice_transport_result_t finish_body(voice_transport_t *t) {
  http_voice_client_t *c = (http_voice_client_t *)t->ctx;
  if (!voice_http_chunk_finish(write_socket, c->client)) return VOICE_TRANSPORT_FATAL;
  int n = esp_http_client_fetch_headers(c->client);
  if (n < 0) return VOICE_TRANSPORT_FATAL;
  c->status_code = esp_http_client_get_status_code(c->client);
  ESP_LOGI(TAG, "voice gateway HTTP %d", c->status_code);
  if (c->config.protocol_version == 2) {
    if (c->status_code != 202) return VOICE_TRANSPORT_FATAL;
    char body[256];
    size_t used = 0;
    for (;;) {
      int n = esp_http_client_read(c->client, body + used,
                                   (int)(sizeof(body) - used - 1));
      if (n < 0) return VOICE_TRANSPORT_FATAL;
      if (n == 0) break;
      used += (size_t)n;
      if (used >= sizeof(body) - 1) return VOICE_TRANSPORT_FATAL;
    }
    body[used] = '\0';
    if (!voice_turn_parse_turn_id(body, c->turn_id, sizeof(c->turn_id)))
      return VOICE_TRANSPORT_FATAL;
    return VOICE_TRANSPORT_OK;
  }
  c->response_ready = 1;
  return VOICE_TRANSPORT_OK;
}

static voice_transport_result_t poll_response(voice_transport_t *t, uint8_t *data, size_t cap, size_t *received) {
  http_voice_client_t *c = (http_voice_client_t *)t->ctx;
  if (!c->response_ready) return VOICE_TRANSPORT_FATAL;
  int n = esp_http_client_read(c->client, (char *)data, (int)cap);
  if (n < 0) return VOICE_TRANSPORT_FATAL;
  *received = (size_t)n;
  return n == 0 ? VOICE_TRANSPORT_EOF : VOICE_TRANSPORT_OK;
}

static void abort_body(voice_transport_t *t) {
  http_voice_client_t *c = (http_voice_client_t *)t->ctx;
  if (c->client) { esp_http_client_close(c->client); esp_http_client_cleanup(c->client); c->client = NULL; }
}

static const voice_transport_ops_t OPS = {begin, write_body, finish_body, poll_response, abort_body};
#else
static const voice_transport_ops_t OPS = {0};
#endif

int http_voice_client_init(http_voice_client_t *c, const http_voice_config_t *config) {
  if (!c || !config || !config->url) return -1;
  if (config->protocol_version != 1 && config->protocol_version != 2) return -1;
  if (config->protocol_version == 2 && (!config->token || !config->token[0])) return -1;
  memset(c, 0, sizeof(*c)); c->config = *config; c->transport.ops = &OPS; c->transport.ctx = c;
#ifndef ESP_PLATFORM
  return -2;
#else
  return 0;
#endif
}
voice_transport_t *http_voice_client_transport(http_voice_client_t *c) { return c ? &c->transport : NULL; }
void http_voice_client_deinit(http_voice_client_t *c) { if (c) { voice_transport_abort(&c->transport); } }
int http_voice_client_status(const http_voice_client_t *c) { return c ? c->status_code : 0; }
const char *http_voice_client_turn_id(const http_voice_client_t *c) { return c ? c->turn_id : NULL; }
