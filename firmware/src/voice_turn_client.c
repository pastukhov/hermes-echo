#include "voice_turn_client.h"

#include <stddef.h>
#include <string.h>

#define VOICE_TURN_DEADLINE_MS 180000u
#define VOICE_TURN_POLL_INTERVAL_MS 1000u

uint32_t voice_turn_retry_delay_ms(unsigned failed_attempts) {
  static const uint32_t delays[] = {1000, 2000, 4000, 5000};
  const size_t count = sizeof(delays) / sizeof(delays[0]);
  return delays[failed_attempts < count ? failed_attempts : count - 1];
}

bool voice_turn_format_uuid(const uint8_t random_bytes[16], char *out,
                            size_t capacity) {
  static const char hex[] = "0123456789abcdef";
  if (!random_bytes || !out || capacity < VOICE_TURN_REQUEST_ID_CAPACITY) return false;
  uint8_t bytes[16];
  memcpy(bytes, random_bytes, sizeof(bytes));
  bytes[6] = (uint8_t)((bytes[6] & 0x0fu) | 0x40u);
  bytes[8] = (uint8_t)((bytes[8] & 0x3fu) | 0x80u);
  size_t pos = 0;
  for (size_t i = 0; i < sizeof(bytes); ++i) {
    if (i == 4 || i == 6 || i == 8 || i == 10) out[pos++] = '-';
    out[pos++] = hex[bytes[i] >> 4];
    out[pos++] = hex[bytes[i] & 0x0fu];
  }
  out[pos] = '\0';
  return true;
}

bool voice_turn_build_upload_url(const char *gateway_base, char *out,
                                 size_t capacity) {
  if (!gateway_base || !out || capacity == 0) return false;
  const size_t base_len = strlen(gateway_base);
  const char *host = NULL;
  if (strncmp(gateway_base, "http://", 7) == 0) host = gateway_base + 7;
  else if (strncmp(gateway_base, "https://", 8) == 0) host = gateway_base + 8;
  if (!host || !host[0] || strchr(host, '?') || strchr(host, '#')) return false;
  const char *path = strchr(host, '/');
  if (path && path[1] != '\0') return false;
  const char *host_end = path ? path : gateway_base + base_len;
  if (host == host_end || *host == '.' || *host == '-' || *host == ':') return false;
  for (const char *p = host; p < host_end; ++p) {
    const unsigned char ch = (unsigned char)*p;
    const bool allowed = (ch >= 'a' && ch <= 'z') ||
                         (ch >= 'A' && ch <= 'Z') ||
                         (ch >= '0' && ch <= '9') ||
                         ch == '.' || ch == '-' || ch == ':' ||
                         ch == '[' || ch == ']';
    if (!allowed) return false;
  }
  const size_t trim = path ? 1 : 0;
  const size_t prefix_len = base_len - trim;
  static const char suffix[] = "/api/v2/voice/turns";
  if (prefix_len + sizeof(suffix) > capacity) return false;
  memcpy(out, gateway_base, prefix_len);
  memcpy(out + prefix_len, suffix, sizeof(suffix));
  return true;
}

bool voice_turn_audio_response_valid(int http_status, const char *content_type,
                                     uint32_t sample_rate, uint16_t channels,
                                     uint16_t bits_per_sample) {
  if (http_status != 200 || !content_type) return false;
  const char expected[] = "audio/wav";
  size_t type_len = strlen(content_type);
  while (type_len && (content_type[type_len - 1] == ' ' ||
                      content_type[type_len - 1] == '\t')) type_len--;
  if (type_len < sizeof(expected) - 1) return false;
  for (size_t i = 0; i < sizeof(expected) - 1; ++i) {
    char ch = content_type[i];
    if (ch >= 'A' && ch <= 'Z') ch = (char)(ch - 'A' + 'a');
    if (ch != expected[i]) return false;
  }
  if (type_len > sizeof(expected) - 1 &&
      content_type[sizeof(expected) - 1] != ';') return false;
  return sample_rate == 24000 && channels == 1 && bits_per_sample == 16;
}

static bool copy_uuid(char *dst, size_t cap, const char *src) {
  if (!dst || !src || strlen(src) != 36 || cap < 37) return false;
  for (size_t i = 0; i < 36; ++i) {
    const char ch = src[i];
    if (i == 8 || i == 13 || i == 18 || i == 23) {
      if (ch != '-') return false;
    } else if (!((ch >= '0' && ch <= '9') ||
                 (ch >= 'a' && ch <= 'f') ||
                 (ch >= 'A' && ch <= 'F'))) {
      return false;
    }
  }
  memcpy(dst, src, 37);
  return true;
}

bool voice_turn_parse_status(const char *json, voice_turn_status_t *status) {
  if (!json || !status) return false;
  const char *key = strstr(json, "\"status\"");
  if (!key) return false;
  const char *p = key + sizeof("\"status\"") - 1;
  while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') ++p;
  if (*p++ != ':') return false;
  while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') ++p;
  if (*p++ != '\"') return false;
  const char *end = strchr(p, '\"');
  if (!end || (size_t)(end - p) >= 24) return false;
  char value[24];
  memcpy(value, p, (size_t)(end - p));
  value[end - p] = '\0';
  if (strcmp(value, "queued") == 0 || strcmp(value, "uploading") == 0)
    *status = VOICE_TURN_STATUS_QUEUED;
  else if (strcmp(value, "running") == 0) *status = VOICE_TURN_STATUS_RUNNING;
  else if (strcmp(value, "transcribing") == 0) *status = VOICE_TURN_STATUS_TRANSCRIBING;
  else if (strcmp(value, "thinking") == 0) *status = VOICE_TURN_STATUS_THINKING;
  else if (strcmp(value, "synthesizing") == 0) *status = VOICE_TURN_STATUS_SYNTHESIZING;
  else if (strcmp(value, "ready") == 0) *status = VOICE_TURN_STATUS_READY;
  else if (strcmp(value, "failed") == 0 || strcmp(value, "upload_failed") == 0 ||
           strcmp(value, "interrupted") == 0) *status = VOICE_TURN_STATUS_FAILED;
  else if (strcmp(value, "cancelled") == 0) *status = VOICE_TURN_STATUS_CANCELLED;
  else return false;
  return true;
}

bool voice_turn_parse_turn_id(const char *json, char *turn_id,
                              size_t capacity) {
  if (!json || !turn_id || capacity < VOICE_TURN_ID_CAPACITY) return false;
  const char *key = strstr(json, "\"turn_id\"");
  if (!key) return false;
  const char *p = key + sizeof("\"turn_id\"") - 1;
  while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') ++p;
  if (*p++ != ':') return false;
  while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') ++p;
  if (*p++ != '\"') return false;
  char value[VOICE_TURN_ID_CAPACITY];
  size_t n = 0;
  while (*p && *p != '\"') {
    if (*p == '\\' || n + 1 >= sizeof(value)) return false;
    value[n++] = *p++;
  }
  if (*p != '\"') return false;
  value[n] = '\0';
  return copy_uuid(turn_id, capacity, value);
}

static void clear_ids(voice_turn_client_t *client) {
  if (!client->ids_cleared && client->ops && client->ops->clear_ids) {
    client->ops->clear_ids(client->ctx);
    client->ids_cleared = true;
  }
  client->request_id[0] = '\0';
  client->turn_id[0] = '\0';
}

static voice_turn_result_t finish(voice_turn_client_t *client,
                                  voice_turn_result_t result) {
  client->result = result;
  clear_ids(client);
  return result;
}

static voice_turn_result_t retry_later(voice_turn_client_t *client,
                                       uint64_t now_ms) {
  client->next_poll_ms = now_ms + voice_turn_retry_delay_ms(client->retry_attempt);
  client->retry_attempt++;
  return VOICE_TURN_WAITING;
}

static bool pending_status(voice_turn_status_t status) {
  return status == VOICE_TURN_STATUS_QUEUED ||
         status == VOICE_TURN_STATUS_RUNNING ||
         status == VOICE_TURN_STATUS_TRANSCRIBING ||
         status == VOICE_TURN_STATUS_THINKING ||
         status == VOICE_TURN_STATUS_SYNTHESIZING;
}

void voice_turn_client_init(voice_turn_client_t *client,
                            const voice_turn_ops_t *ops, void *ctx,
                            const char *request_id, const char *saved_turn_id,
                            uint64_t now_ms) {
  if (!client) return;
  memset(client, 0, sizeof(*client));
  client->ops = ops;
  client->ctx = ctx;
  client->result = VOICE_TURN_WAITING;
  client->status = VOICE_TURN_STATUS_QUEUED;
  client->deadline_ms = now_ms + VOICE_TURN_DEADLINE_MS;
  client->next_poll_ms = now_ms + VOICE_TURN_POLL_INTERVAL_MS;
  if (!ops || !copy_uuid(client->request_id, sizeof(client->request_id), request_id) ||
      (saved_turn_id && saved_turn_id[0] &&
       !copy_uuid(client->turn_id, sizeof(client->turn_id), saved_turn_id))) {
    client->result = VOICE_TURN_FAILED;
    clear_ids(client);
  }
}

static voice_turn_result_t download_ready_audio(voice_turn_client_t *client,
                                                uint64_t now_ms) {
  if (!client->ops->download_audio)
    return finish(client, VOICE_TURN_FAILED);
  voice_turn_response_t response = {0};
  const voice_turn_io_result_t io = client->ops->download_audio(
      client->ctx, client->turn_id, &response);
  if (io == VOICE_TURN_IO_RETRY) return retry_later(client, now_ms);
  if (io != VOICE_TURN_IO_OK ||
      !voice_turn_audio_response_valid(response.http_status,
                                       response.content_type,
                                       response.sample_rate,
                                       response.channels,
                                       response.bits_per_sample)) {
    return finish(client, VOICE_TURN_FAILED);
  }
  return finish(client, VOICE_TURN_READY);
}

voice_turn_result_t voice_turn_client_tick(voice_turn_client_t *client,
                                           uint64_t now_ms) {
  if (!client) return VOICE_TURN_FAILED;
  if (client->result != VOICE_TURN_WAITING) return client->result;
  if (!client->ops || now_ms >= client->deadline_ms)
    return finish(client, VOICE_TURN_FAILED);
  if (now_ms < client->next_poll_ms) return VOICE_TURN_WAITING;
  if (client->cancel_requested && client->turn_id[0]) {
    if (!client->ops->cancel_turn)
      return finish(client, VOICE_TURN_CANCELLED);
    const voice_turn_io_result_t cancel_result = client->ops->cancel_turn(
        client->ctx, client->turn_id);
    if (cancel_result == VOICE_TURN_IO_RETRY)
      return retry_later(client, now_ms);
    return finish(client, VOICE_TURN_CANCELLED);
  }
  if (client->audio_pending) return download_ready_audio(client, now_ms);

  voice_turn_response_t response = {0};
  voice_turn_io_result_t io;
  if (!client->turn_id[0]) {
    if (!client->ops->lookup_request)
      return finish(client, VOICE_TURN_FAILED);
    io = client->ops->lookup_request(client->ctx, client->request_id, &response);
    if (io == VOICE_TURN_IO_RETRY) return retry_later(client, now_ms);
    if (io != VOICE_TURN_IO_OK ||
        !copy_uuid(client->turn_id, sizeof(client->turn_id), response.turn_id) ||
        !client->ops->save_turn_id ||
        !client->ops->save_turn_id(client->ctx, client->turn_id)) {
      return finish(client, VOICE_TURN_FAILED);
    }
    client->retry_attempt = 0;
    client->status = response.status;
    client->next_poll_ms = now_ms + VOICE_TURN_POLL_INTERVAL_MS;
    return VOICE_TURN_WAITING;
  }

  if (!client->ops->poll_turn) return finish(client, VOICE_TURN_FAILED);
  io = client->ops->poll_turn(client->ctx, client->turn_id, &response);
  if (io == VOICE_TURN_IO_RETRY) return retry_later(client, now_ms);
  if (io != VOICE_TURN_IO_OK) return finish(client, VOICE_TURN_FAILED);
  client->retry_attempt = 0;
  client->status = response.status;
  if (pending_status(response.status)) {
    client->next_poll_ms = now_ms + VOICE_TURN_POLL_INTERVAL_MS;
    return VOICE_TURN_WAITING;
  }
  if (response.status == VOICE_TURN_STATUS_FAILED)
    return finish(client, VOICE_TURN_FAILED);
  if (response.status == VOICE_TURN_STATUS_CANCELLED)
    return finish(client, VOICE_TURN_CANCELLED);
  if (response.status != VOICE_TURN_STATUS_READY)
    return finish(client, VOICE_TURN_FAILED);
  client->audio_pending = true;
  return download_ready_audio(client, now_ms);
}

voice_turn_result_t voice_turn_client_cancel(voice_turn_client_t *client,
                                             uint64_t now_ms) {
  if (!client) return VOICE_TURN_FAILED;
  if (client->result != VOICE_TURN_WAITING) return client->result;
  const bool cancellation_was_pending = client->cancel_requested;
  client->cancel_requested = true;
  if (cancellation_was_pending && now_ms < client->next_poll_ms)
    return VOICE_TURN_WAITING;
  if (!client->turn_id[0]) return VOICE_TURN_WAITING;
  if (!client->ops || !client->ops->cancel_turn)
    return finish(client, VOICE_TURN_CANCELLED);
  if (client->ops->cancel_turn(client->ctx, client->turn_id) == VOICE_TURN_IO_RETRY)
    return retry_later(client, now_ms);
  return finish(client, VOICE_TURN_CANCELLED);
}
