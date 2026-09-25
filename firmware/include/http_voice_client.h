#ifndef HTTP_VOICE_CLIENT_H
#define HTTP_VOICE_CLIENT_H

#include <stddef.h>
#include <stdint.h>
#include "voice_transport.h"

typedef struct {
  const char *url;
  const char *device_id;
  const char *token;
  int timeout_ms;
  const char *request_id;
} http_voice_config_t;

typedef struct {
  voice_transport_t transport;
  http_voice_config_t config;
  char upload_url[256];
  char turn_id[37];
  void *client;
  int status_code;
} http_voice_client_t;

int http_voice_client_init(http_voice_client_t *c, const http_voice_config_t *config);
voice_transport_t *http_voice_client_transport(http_voice_client_t *c);
void http_voice_client_deinit(http_voice_client_t *c);
int http_voice_client_status(const http_voice_client_t *c);
const char *http_voice_client_turn_id(const http_voice_client_t *c);

#endif
