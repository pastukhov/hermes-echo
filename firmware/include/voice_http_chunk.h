#ifndef VOICE_HTTP_CHUNK_H
#define VOICE_HTTP_CHUNK_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef int (*voice_http_write_fn)(void *context, const char *data, size_t length);

bool voice_http_chunk_write(voice_http_write_fn write, void *context,
                            const uint8_t *data, size_t length);
bool voice_http_chunk_finish(voice_http_write_fn write, void *context);

#endif
