#include <string.h>
#include <unity.h>

#include "ring_buffer.h"
#include "../test_main/fakes/hw_fakes.c"

void setUp(void) {}
void tearDown(void) {}

static void test_pop_preserves_audio_across_storage_wrap(void) {
  uint8_t storage[16];
  memset(storage, 0xee, sizeof(storage));
  ring_buffer_t rb;
  const uint8_t first[] = {1, 2, 3, 4, 5, 6};
  const uint8_t second[] = {7, 8, 9, 10};
  const uint8_t expected[] = {6, 7, 8, 9, 10};
  uint8_t out[5] = {0};

  TEST_ASSERT_TRUE(ring_buffer_init(&rb, storage, 8));
  TEST_ASSERT_EQUAL_UINT(6, ring_buffer_push(&rb, first, sizeof(first)));
  TEST_ASSERT_EQUAL_UINT(5, ring_buffer_pop(&rb, out, 5));
  TEST_ASSERT_EQUAL_UINT(4, ring_buffer_push(&rb, second, sizeof(second)));
  memset(out, 0, sizeof(out));
  TEST_ASSERT_EQUAL_UINT(5, ring_buffer_pop(&rb, out, sizeof(out)));
  TEST_ASSERT_EQUAL_UINT8_ARRAY(expected, out, sizeof(expected));
  TEST_ASSERT_TRUE(ring_buffer_empty(&rb));
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_pop_preserves_audio_across_storage_wrap);
  return UNITY_END();
}
