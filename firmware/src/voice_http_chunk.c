#include "voice_http_chunk.h"

#include <stdio.h>

static bool write_all(voice_http_write_fn write, void *context,
                      const char *data, size_t length) {
  while (length) {
    int written = write(context, data, length);
    if (written <= 0 || (size_t)written > length) return false;
    data += written;
    length -= (size_t)written;
  }
  return true;
}

bool voice_http_chunk_write(voice_http_write_fn write, void *context,
                            const uint8_t *data, size_t length) {
  if (!write || !data || !length) return false;
  char header[sizeof(size_t) * 2 + 3];
  int header_length = snprintf(header, sizeof(header), "%zx\r\n", length);
  if (header_length <= 0 || (size_t)header_length >= sizeof(header)) return false;
  return write_all(write, context, header, (size_t)header_length) &&
         write_all(write, context, (const char *)data, length) &&
         write_all(write, context, "\r\n", 2);
}

bool voice_http_chunk_finish(voice_http_write_fn write, void *context) {
  return write && write_all(write, context, "0\r\n\r\n", 5);
}
