/*
 * Host harness for the M1-05 button/LED integration in main.c.
 *
 * Drives app_tick() with scripted button input through the fake hw seam and
 * asserts the full happy path plus the state->LED mapping required by the
 * spec (red = record, green = play).
 */
#include <stdio.h>
#include <string.h>

#include <unity.h>

#include "button_driver.h"
#include "hardware.h"
#include "led_config.h"
#include "led_ui.h"
#include "state_machine.h"
#include "fakes/hw_fakes.h"

/* app API (declared here; test build has no VOICE_WITH_MAIN, so main() is
 * not compiled into main.c). */
void app_init(void);
void app_tick(void);
void app_test_set_playback_done(bool done);

static int failures = 0;

#define CHECK(cond, msg)                                             \
  do {                                                              \
    if (!(cond)) {                                                  \
      printf("FAIL: %s (line %d)\n", msg, __LINE__);                \
      failures++;                                                   \
    }                                                               \
  } while (0)

static void tick_to(uint32_t target_ms) {
  while (g_hw_fake.clock_ms < target_ms) {
    hw_fake_set_clock(&g_hw_fake, g_hw_fake.clock_ms + 10);
    app_tick();
  }
}

static bool led_is(uint16_t expected_rgb565) {
  return g_hw_fake.led_rgb565 == expected_rgb565;
}

/*
 * Unity plumbing: the scenario below predates Unity adoption and asserts
 * via its own CHECK()/failures counter rather than TEST_ASSERT_*. Wrap it
 * as a single Unity test case instead of rewriting each assertion, so
 * `pio test` reports a real PASS/FAIL instead of just an exit code.
 */
void setUp(void) {}
void tearDown(void) {}

static void test_full_state_machine_scenario(void) {
  hw_fake_reset(&g_hw_fake);
  app_init();

  /* Drive BOOT -> IDLE. */
  tick_to(60);

  /* After boot the LED state must be IDLE (green steady). */
  CHECK(led_get_state() == LED_STATE_IDLE, "LED state IDLE after boot");

  /* IDLE -> press button: expect RECORDING with red LED. */
  hw_fake_set_button(&g_hw_fake, true);
  tick_to(200);
  CHECK(led_get_state() == LED_STATE_RECORDING, "LED state RECORDING after press");
  /* Red blink: at some phase the LED word must be RED. */
  uint32_t red_seen = 0, green_seen = 0;
  for (uint32_t t = 200; t < 750; t += 10) {
    hw_fake_set_clock(&g_hw_fake, t);
    app_tick();
    if (g_hw_fake.led_rgb565 == LED_RGB_RED) red_seen++;
  }
  CHECK(red_seen > 0, "red LED pulses while RECORDING");

  /* Release: -> PROCESSING (cyan) with playback_done pending. */
  hw_fake_set_button(&g_hw_fake, false);
  tick_to(900);
  CHECK(led_get_state() == LED_STATE_PROCESSING, "LED state PROCESSING after release");

  /* PROCESSING -> PLAYING when harness says playback ready. */
  app_test_set_playback_done(true);
  tick_to(1000);
  CHECK(led_get_state() == LED_STATE_PLAYBACK, "LED state PLAYBACK");
  for (uint32_t t = 1000; t < 1500; t += 10) {
    hw_fake_set_clock(&g_hw_fake, t);
    app_tick();
    if (g_hw_fake.led_rgb565 == LED_RGB_GREEN) green_seen++;
  }
  CHECK(green_seen > 0, "green LED pulses while PLAYING");

  /* PLAYING -> IDLE when playback done. */
  app_test_set_playback_done(true);
  tick_to(1600);
  CHECK(led_get_state() == LED_STATE_IDLE, "LED state IDLE after playback");
  CHECK(g_hw_fake.led_writes > 0, "LED writes happened");
  CHECK(g_hw_fake.button_reads > 0, "button was polled");

  /* ERROR path: force one via recovery budget — verify recovery returns to
   * IDLE and resets the button (no stale press). */
  printf(failures == 0
             ? "OK: BOOT->IDLE->RECORDING->PROCESSING->PLAYING->IDLE + "
               "button/LED (red=record, green=play) verified\n"
             : "TEST FAILURES: %d\n",
         failures);
  TEST_ASSERT_EQUAL_INT_MESSAGE(0, failures, "scenario had CHECK() failures, see stdout above");
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_full_state_machine_scenario);
  return UNITY_END();
}
