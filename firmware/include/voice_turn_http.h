#ifndef VOICE_TURN_HTTP_H
#define VOICE_TURN_HTTP_H

#include "voice_turn_client.h"

#ifdef ESP_PLATFORM
#include "freertos/FreeRTOS.h"
#include "freertos/stream_buffer.h"

typedef struct {
  char gateway_base[192];
  char device_id[64];
  char token[192];
  char response_content_type[64];
  StreamBufferHandle_t audio_stream;
  volatile bool *cancel_requested;
  volatile bool *audio_started;
} voice_turn_http_t;

bool voice_turn_http_init(voice_turn_http_t *http, const char *gateway_base,
                          const char *device_id, const char *token,
                          StreamBufferHandle_t audio_stream,
                          volatile bool *cancel_requested,
                          volatile bool *audio_started);
const voice_turn_ops_t *voice_turn_http_ops(void);
#endif

#endif
