#include <unity.h>
#include "screen_brightness.h"
#include "../test_main/fakes/hw_fakes.c"
void setUp(void) {}
void tearDown(void) {}
static bool click(screen_brightness_t *b, uint32_t t) {
  screen_brightness_tick(b, true, t);
  screen_brightness_tick(b, true, t + 30);
  screen_brightness_tick(b, false, t + 100);
  return screen_brightness_tick(b, false, t + 130);
}
static void cycle(void) {
  screen_brightness_t b = {0};
  screen_brightness_tick(&b, false, 0);
  const int expected[] = {60,30,10,5,2,100};
  TEST_ASSERT_EQUAL(100, screen_brightness_percent(&b));
  for (unsigned i=0;i<6;i++) {
    TEST_ASSERT_TRUE(click(&b, 200+i*200));
    TEST_ASSERT_EQUAL(expected[i], screen_brightness_percent(&b));
  }
}
static void bounce_and_long_hold(void) {
  screen_brightness_t b = {0};
  screen_brightness_tick(&b, false, 0);
  screen_brightness_tick(&b, true, 100);
  screen_brightness_tick(&b, false, 110);
  TEST_ASSERT_FALSE(screen_brightness_tick(&b, false, 150));
  screen_brightness_tick(&b, true, 200);
  screen_brightness_tick(&b, true, 230);
  screen_brightness_tick(&b, false, 3200);
  TEST_ASSERT_FALSE(screen_brightness_tick(&b, false, 3230));
  TEST_ASSERT_EQUAL(100, screen_brightness_percent(&b));
  TEST_ASSERT_TRUE(click(&b, 3400));
}
static void wake_button_and_clock_wrap(void) {
  screen_brightness_t b = {0};
  screen_brightness_tick(&b, true, 0);
  screen_brightness_tick(&b, false, 100);
  TEST_ASSERT_FALSE(screen_brightness_tick(&b, false, 130));
  TEST_ASSERT_TRUE(click(&b, UINT32_MAX-50));
  TEST_ASSERT_EQUAL(60, screen_brightness_percent(&b));
}
static void restore_saved_level(void) {
  screen_brightness_t b = {0};
  screen_brightness_restore(&b, 2);
  TEST_ASSERT_EQUAL(30, screen_brightness_percent(&b));
  screen_brightness_tick(&b, false, 0);
  TEST_ASSERT_TRUE(click(&b, 100));
  TEST_ASSERT_EQUAL(10, screen_brightness_percent(&b));
  screen_brightness_restore(&b, 5);
  TEST_ASSERT_EQUAL(2, screen_brightness_percent(&b));
  screen_brightness_restore(&b, 255);
  TEST_ASSERT_EQUAL(100, screen_brightness_percent(&b));
}
int main(void) {
  UNITY_BEGIN();
  RUN_TEST(restore_saved_level);
  RUN_TEST(cycle); RUN_TEST(bounce_and_long_hold); RUN_TEST(wake_button_and_clock_wrap);
  return UNITY_END();
}
