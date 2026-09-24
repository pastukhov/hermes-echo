#ifndef VOICE_TURN_CLIENT_H
#define VOICE_TURN_CLIENT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define VOICE_TURN_ID_CAPACITY 37
#define VOICE_TURN_REQUEST_ID_CAPACITY 37

typedef enum {
  VOICE_TURN_IO_OK,
  VOICE_TURN_IO_RETRY,
  VOICE_TURN_IO_NOT_FOUND,
  VOICE_TURN_IO_FATAL,
} voice_turn_io_result_t;

typedef enum {
  VOICE_TURN_STATUS_UNKNOWN,
  VOICE_TURN_STATUS_QUEUED,
  VOICE_TURN_STATUS_RUNNING,
  VOICE_TURN_STATUS_TRANSCRIBING,
  VOICE_TURN_STATUS_THINKING,
  VOICE_TURN_STATUS_SYNTHESIZING,
  VOICE_TURN_STATUS_READY,
  VOICE_TURN_STATUS_FAILED,
  VOICE_TURN_STATUS_CANCELLED,
} voice_turn_status_t;

typedef struct {
  voice_turn_status_t status;
  char turn_id[VOICE_TURN_ID_CAPACITY];
  int http_status;
  char content_type[32];
  uint32_t sample_rate;
  uint16_t channels;
  uint16_t bits_per_sample;
} voice_turn_response_t;

typedef struct {
  voice_turn_io_result_t (*lookup_request)(void *ctx, const char *request_id,
                                            voice_turn_response_t *response);
  voice_turn_io_result_t (*poll_turn)(void *ctx, const char *turn_id,
                                       voice_turn_response_t *response);
  voice_turn_io_result_t (*download_audio)(void *ctx, const char *turn_id,
                                            voice_turn_response_t *response);
  voice_turn_io_result_t (*cancel_turn)(void *ctx, const char *turn_id);
  bool (*save_turn_id)(void *ctx, const char *turn_id);
  void (*clear_ids)(void *ctx);
} voice_turn_ops_t;

typedef enum {
  VOICE_TURN_WAITING,
  VOICE_TURN_READY,
  VOICE_TURN_FAILED,
  VOICE_TURN_CANCELLED,
} voice_turn_result_t;

typedef struct {
  const voice_turn_ops_t *ops;
  void *ctx;
  char request_id[VOICE_TURN_REQUEST_ID_CAPACITY];
  char turn_id[VOICE_TURN_ID_CAPACITY];
  uint64_t deadline_ms;
  uint64_t next_poll_ms;
  unsigned retry_attempt;
  voice_turn_result_t result;
  voice_turn_status_t status;
  bool audio_pending;
  bool cancel_requested;
  bool ids_cleared;
} voice_turn_client_t;

void voice_turn_client_init(voice_turn_client_t *client,
                            const voice_turn_ops_t *ops, void *ctx,
                            const char *request_id, const char *saved_turn_id,
                            uint64_t now_ms);
voice_turn_result_t voice_turn_client_tick(voice_turn_client_t *client,
                                           uint64_t now_ms);
voice_turn_result_t voice_turn_client_cancel(voice_turn_client_t *client,
                                             uint64_t now_ms);

/* Retry delays for the v2 status poll, in milliseconds. */
uint32_t voice_turn_retry_delay_ms(unsigned failed_attempts);
bool voice_turn_format_uuid(const uint8_t random_bytes[16], char *out,
                            size_t capacity);
bool voice_turn_build_upload_url(const char *gateway_base, char *out,
                                 size_t capacity);
bool voice_turn_parse_turn_id(const char *json, char *turn_id,
                              size_t capacity);
bool voice_turn_parse_status(const char *json, voice_turn_status_t *status);

/* Validate HTTP metadata and the parsed PCM format before playback starts. */
bool voice_turn_audio_response_valid(int http_status, const char *content_type,
                                     uint32_t sample_rate, uint16_t channels,
                                     uint16_t bits_per_sample);

#endif
