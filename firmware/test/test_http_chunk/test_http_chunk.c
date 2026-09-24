#include <unity.h>
#include <string.h>
#include "voice_http_chunk.h"
#include "../test_main/fakes/hw_fakes.c"

typedef struct {
  char bytes[128];
  size_t count;
  int max_write;
} sink_t;

void setUp(void) {}
void tearDown(void) {}

static int capture_write(void *context, const char *data, size_t length) {
  sink_t *sink = context;
  if (sink->max_write == 0) return -1;
  if (sink->max_write > 0 && length > (size_t)sink->max_write)
    length = (size_t)sink->max_write;
  if (length > sizeof(sink->bytes) - sink->count) return -1;
  memcpy(sink->bytes + sink->count, data, length);
  sink->count += length;
  return (int)length;
}

void test_pcm_is_sent_as_complete_http_chunks(void) {
  sink_t sink = {.max_write = -1};
  const uint8_t pcm[] = {0x00, 0x0d, 0x0a, 0xff};
  TEST_ASSERT_TRUE(voice_http_chunk_write(capture_write, &sink, pcm, sizeof(pcm)));
  TEST_ASSERT_TRUE(voice_http_chunk_finish(capture_write, &sink));
  const char expected[] = {'4', '\r', '\n', 0x00, 0x0d, 0x0a, (char)0xff,
                           '\r', '\n', '0', '\r', '\n', '\r', '\n'};
  TEST_ASSERT_EQUAL_UINT(sizeof(expected), sink.count);
  TEST_ASSERT_EQUAL_MEMORY(expected, sink.bytes, sizeof(expected));
}

void test_chunk_writer_handles_partial_socket_writes(void) {
  sink_t sink = {.max_write = 2};
  const uint8_t pcm[] = {'a', 'b', 'c'};
  TEST_ASSERT_TRUE(voice_http_chunk_write(capture_write, &sink, pcm, sizeof(pcm)));
  TEST_ASSERT_EQUAL_MEMORY("3\r\nabc\r\n", sink.bytes, sink.count);
}

void test_chunk_writer_reports_socket_failure(void) {
  sink_t sink = {.max_write = 0};
  const uint8_t pcm[] = {'a'};
  TEST_ASSERT_FALSE(voice_http_chunk_write(capture_write, &sink, pcm, sizeof(pcm)));
  TEST_ASSERT_FALSE(voice_http_chunk_finish(capture_write, &sink));
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_pcm_is_sent_as_complete_http_chunks);
  RUN_TEST(test_chunk_writer_handles_partial_socket_writes);
  RUN_TEST(test_chunk_writer_reports_socket_failure);
  return UNITY_END();
}
