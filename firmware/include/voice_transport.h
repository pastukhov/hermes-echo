#ifndef VOICE_TRANSPORT_H
#define VOICE_TRANSPORT_H

#include <stddef.h>
#include <stdint.h>

typedef enum {
  VOICE_TRANSPORT_OK = 0,
  VOICE_TRANSPORT_WOULD_BLOCK = 1,
  VOICE_TRANSPORT_EOF = 2,
  VOICE_TRANSPORT_FATAL = -1,
} voice_transport_result_t;

typedef struct {
  size_t accepted_bytes;
  voice_transport_result_t result;
} voice_transport_write_result_t;

typedef struct voice_transport voice_transport_t;
typedef struct {
  voice_transport_result_t (*begin)(voice_transport_t *t);
  voice_transport_write_result_t (*write)(voice_transport_t *t,
                                           const uint8_t *data, size_t len);
  voice_transport_result_t (*finish)(voice_transport_t *t);
  voice_transport_result_t (*poll)(voice_transport_t *t, uint8_t *data,
                                   size_t capacity, size_t *received);
  void (*abort)(voice_transport_t *t);
} voice_transport_ops_t;

struct voice_transport {
  const voice_transport_ops_t *ops;
  void *ctx;
  int begun;
  int finished;
};

voice_transport_result_t voice_transport_begin(voice_transport_t *t);
voice_transport_write_result_t voice_transport_write(voice_transport_t *t,
                                                      const uint8_t *data,
                                                      size_t len);
voice_transport_result_t voice_transport_finish(voice_transport_t *t);
voice_transport_result_t voice_transport_poll(voice_transport_t *t,
                                               uint8_t *data, size_t capacity,
                                               size_t *received);
void voice_transport_abort(voice_transport_t *t);

#endif
