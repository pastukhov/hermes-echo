#include "voice_turn_http.h"

#ifdef ESP_PLATFORM

#include "esp_http_client.h"
#include "esp_timer.h"
#include "freertos/task.h"
#include "wav_parser.h"

#include <stdio.h>
#include <string.h>

#define TURN_HTTP_TIMEOUT_MS 5000
#define TURN_JSON_CAPACITY 384

typedef struct {
  voice_turn_http_t *http;
  wav_parser_t *parser;
  bool invalid_format;
} wav_stream_sink_t;

static bool make_url(const voice_turn_http_t *http, const char *path,
                     char *out, size_t capacity) {
  const size_t base_len = strlen(http->gateway_base);
  const bool slash = base_len && http->gateway_base[base_len - 1] == '/';
  int n = snprintf(out, capacity, "%s%s%s", http->gateway_base,
                   slash ? "" : "/", path[0] == '/' ? path + 1 : path);
  return n > 0 && (size_t)n < capacity;
}

static esp_http_client_handle_t open_request(voice_turn_http_t *http,
                                              const char *method,
                                              const char *path,
                                              int *status) {
  char url[256];
  if (!make_url(http, path, url, sizeof(url))) return NULL;
  esp_http_client_config_t config = {.url = url, .timeout_ms = TURN_HTTP_TIMEOUT_MS};
  esp_http_client_handle_t client = esp_http_client_init(&config);
  if (!client) return NULL;
  esp_http_client_set_method(client, strcmp(method, "POST") == 0
                                        ? HTTP_METHOD_POST : HTTP_METHOD_GET);
  if (esp_http_client_set_header(client, "X-Device-Id", http->device_id) != ESP_OK ||
      esp_http_client_set_header(client, "X-Protocol-Version", "2") != ESP_OK) {
    esp_http_client_cleanup(client);
    return NULL;
  }
  char authorization[sizeof(http->token) + sizeof("Bearer ")];
  int auth_len = snprintf(authorization, sizeof(authorization), "Bearer %s", http->token);
  if (auth_len <= 0 || (size_t)auth_len >= sizeof(authorization) ||
      esp_http_client_set_header(client, "Authorization", authorization) != ESP_OK ||
      esp_http_client_open(client, 0) != ESP_OK) {
    esp_http_client_cleanup(client);
    return NULL;
  }
  int64_t content_length = esp_http_client_fetch_headers(client);
  if (content_length < 0) {
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return NULL;
  }
  *status = esp_http_client_get_status_code(client);
  return client;
}

static void close_request(esp_http_client_handle_t client) {
  if (!client) return;
  esp_http_client_close(client);
  esp_http_client_cleanup(client);
}

static voice_turn_io_result_t read_json(esp_http_client_handle_t client,
                                        int status, char *body, size_t cap,
                                        bool needs_turn_id,
                                        voice_turn_response_t *response) {
  if (status == 404) return VOICE_TURN_IO_NOT_FOUND;
  if (status != 200) return status >= 500 ? VOICE_TURN_IO_RETRY : VOICE_TURN_IO_FATAL;
  size_t used = 0;
  while (used + 1 < cap) {
    int n = esp_http_client_read(client, body + used, (int)(cap - used - 1));
    if (n < 0) return VOICE_TURN_IO_RETRY;
    if (n == 0) break;
    used += (size_t)n;
  }
  if (used + 1 == cap) return VOICE_TURN_IO_FATAL;
  body[used] = '\0';
  if (!voice_turn_parse_status(body, &response->status)) return VOICE_TURN_IO_FATAL;
  if (needs_turn_id && !voice_turn_parse_turn_id(body, response->turn_id,
                                                  sizeof(response->turn_id)))
    return VOICE_TURN_IO_FATAL;
  return VOICE_TURN_IO_OK;
}

static voice_turn_io_result_t lookup_request(void *ctx, const char *request_id,
                                             voice_turn_response_t *response) {
  voice_turn_http_t *http = ctx;
  char path[96], body[TURN_JSON_CAPACITY];
  int n = snprintf(path, sizeof(path), "/api/v2/voice/requests/%s", request_id);
  if (n <= 0 || (size_t)n >= sizeof(path)) return VOICE_TURN_IO_FATAL;
  int status = 0;
  esp_http_client_handle_t client = open_request(http, "GET", path, &status);
  if (!client) return VOICE_TURN_IO_RETRY;
  voice_turn_io_result_t result = read_json(client, status, body, sizeof(body), true, response);
  close_request(client);
  return result;
}

static voice_turn_io_result_t poll_turn(void *ctx, const char *turn_id,
                                        voice_turn_response_t *response) {
  voice_turn_http_t *http = ctx;
  char path[96], body[TURN_JSON_CAPACITY];
  int n = snprintf(path, sizeof(path), "/api/v2/voice/turns/%s", turn_id);
  if (n <= 0 || (size_t)n >= sizeof(path)) return VOICE_TURN_IO_FATAL;
  int status = 0;
  esp_http_client_handle_t client = open_request(http, "GET", path, &status);
  if (!client) return VOICE_TURN_IO_RETRY;
  voice_turn_io_result_t result = read_json(client, status, body, sizeof(body), false, response);
  close_request(client);
  return result;
}

static size_t stream_pcm(void *ctx, const uint8_t *data, size_t len) {
  wav_stream_sink_t *sink = ctx;
  const wav_audio_format_t *format = &sink->parser->format;
  if (format->sample_rate != 24000 || format->channels != 1 ||
      format->bits_per_sample != 16) {
    sink->invalid_format = true;
    return 0;
  }
  if (sink->http->cancel_requested && *sink->http->cancel_requested) return 0;
  if (sink->http->audio_started) *sink->http->audio_started = true;
  return xStreamBufferSend(sink->http->audio_stream, data, len,
                           pdMS_TO_TICKS(100));
}

static voice_turn_io_result_t download_audio(void *ctx, const char *turn_id,
                                              voice_turn_response_t *response) {
  voice_turn_http_t *http = ctx;
  char path[104];
  int n = snprintf(path, sizeof(path), "/api/v2/voice/turns/%s/audio", turn_id);
  if (n <= 0 || (size_t)n >= sizeof(path)) return VOICE_TURN_IO_FATAL;
  int status = 0;
  esp_http_client_handle_t client = open_request(http, "GET", path, &status);
  if (!client) return VOICE_TURN_IO_RETRY;
  response->http_status = status;
  char *content_type = NULL;
  if (esp_http_client_get_header(client, "Content-Type", &content_type) != ESP_OK)
    content_type = NULL;
  if (content_type) strlcpy(response->content_type, content_type,
                            sizeof(response->content_type));
  if (status != 200 || !content_type ||
      !voice_turn_audio_response_valid(status, content_type, 24000, 1, 16)) {
    close_request(client);
    return status >= 500 ? VOICE_TURN_IO_RETRY : VOICE_TURN_IO_FATAL;
  }
  wav_parser_t parser;
  wav_stream_sink_t sink = {.http = http, .parser = &parser};
  wav_parser_init(&parser, stream_pcm, &sink);
  uint8_t block[512];
  voice_turn_io_result_t result = VOICE_TURN_IO_OK;
  for (;;) {
    if (http->cancel_requested && *http->cancel_requested) {
      result = VOICE_TURN_IO_RETRY;
      break;
    }
    int got = esp_http_client_read(client, (char *)block, sizeof(block));
    if (got < 0) { result = VOICE_TURN_IO_RETRY; break; }
    if (got == 0) break;
    size_t offset = 0;
    while (offset < (size_t)got) {
      if (http->cancel_requested && *http->cancel_requested) {
        result = VOICE_TURN_IO_RETRY;
        break;
      }
      wav_result_t wav_result = WAV_OK;
      size_t consumed = wav_parser_feed(&parser, block + offset,
                                        (size_t)got - offset, &wav_result);
      offset += consumed;
      if (wav_result == WAV_ERROR || sink.invalid_format) {
        result = VOICE_TURN_IO_FATAL;
        break;
      }
      if (offset < (size_t)got && consumed == 0) vTaskDelay(pdMS_TO_TICKS(20));
    }
    if (result != VOICE_TURN_IO_OK) break;
  }
  if (result == VOICE_TURN_IO_OK && wav_parser_finish(&parser) != WAV_OK)
    result = VOICE_TURN_IO_FATAL;
  if (result == VOICE_TURN_IO_OK) {
    response->sample_rate = parser.format.sample_rate;
    response->channels = parser.format.channels;
    response->bits_per_sample = parser.format.bits_per_sample;
  }
  close_request(client);
  if (result == VOICE_TURN_IO_RETRY && http->audio_started &&
      *http->audio_started &&
      !(http->cancel_requested && *http->cancel_requested))
    result = VOICE_TURN_IO_FATAL;
  return result;
}

static voice_turn_io_result_t cancel_turn(void *ctx, const char *turn_id) {
  voice_turn_http_t *http = ctx;
  char path[104], body[TURN_JSON_CAPACITY];
  int n = snprintf(path, sizeof(path), "/api/v2/voice/turns/%s/cancel", turn_id);
  if (n <= 0 || (size_t)n >= sizeof(path)) return VOICE_TURN_IO_FATAL;
  int status = 0;
  esp_http_client_handle_t client = open_request(http, "POST", path, &status);
  if (!client) return VOICE_TURN_IO_RETRY;
  voice_turn_response_t response = {0};
  voice_turn_io_result_t result = read_json(client, status, body, sizeof(body), false, &response);
  close_request(client);
  return result;
}

bool voice_turn_http_init(voice_turn_http_t *http, const char *gateway_base,
                          const char *device_id, const char *token,
                          StreamBufferHandle_t audio_stream,
                          volatile bool *cancel_requested,
                          volatile bool *audio_started) {
  if (!http || !gateway_base || !device_id || !token || !token[0] ||
      !audio_stream || !voice_turn_build_upload_url(gateway_base,
          (char[256]){0}, 256)) return false;
  memset(http, 0, sizeof(*http));
  if (strlcpy(http->gateway_base, gateway_base, sizeof(http->gateway_base)) >= sizeof(http->gateway_base) ||
      strlcpy(http->device_id, device_id, sizeof(http->device_id)) >= sizeof(http->device_id) ||
      strlcpy(http->token, token, sizeof(http->token)) >= sizeof(http->token)) return false;
  http->audio_stream = audio_stream;
  http->cancel_requested = cancel_requested;
  http->audio_started = audio_started;
  return true;
}

const voice_turn_ops_t *voice_turn_http_ops(void) {
  static const voice_turn_ops_t ops = {
    .lookup_request = lookup_request,
    .poll_turn = poll_turn,
    .download_audio = download_audio,
    .cancel_turn = cancel_turn,
  };
  return &ops;
}

#endif
