/*
 * Host harness for the M1-03/M1-04 audio integration in main.c (built on
 * top of the M1-05 button/LED integration).
 *
 * Drives app_tick() with scripted button + microphone input through the
 * fake hw seam and asserts the full state machine path, the state->LED
 * mapping required by the spec (red = record, green = play), and that
 * captured audio actually reaches the speaker via the loopback path
 * (M1 has no backend yet, see main.c's file header).
 */
#include <stdio.h>
#include <string.h>

#include <unity.h>

#include "button_driver.h"
#include "hardware.h"
#include "http_session.h"
#include "led_config.h"
#include "led_ui.h"
#include "state_machine.h"
#include "fakes/hw_fakes.h"

/* app API (declared here; test build has no VOICE_WITH_MAIN, so main() is
 * not compiled into main.c). */
void app_init(void);
void app_tick(void);
state_t app_state(void);
http_session_state_t app_session_state(void);
bool app_session_aborted(void);
void app_test_simulate_ring_overflow(void);

static int failures = 0;

#define CHECK(cond, msg)                                             \
  do {                                                              \
    if (!(cond)) {                                                  \
      printf("FAIL: %s (line %d)\n", msg, __LINE__);                \
      failures++;                                                   \
    }                                                               \
  } while (0)

/* Forward declarations: these scenarios are defined below main() but must
 * be visible to the RUN_TEST() calls inside it. */
static void test_ring_buffer_overflow_recovery(void);
static void test_max_record_seconds_auto_finish(void);

static void tick_to(uint32_t target_ms) {
  while (g_hw_fake.clock_ms < target_ms) {
    hw_fake_set_clock(&g_hw_fake, g_hw_fake.clock_ms + 10);
    app_tick();
  }
}

/* Advance one 10ms tick and return the resulting app state, for tests that
 * need to observe a transient single-tick state (e.g. PROCESSING). */
static state_t step_tick(void) {
  hw_fake_set_clock(&g_hw_fake, g_hw_fake.clock_ms + 10);
  app_tick();
  return app_state();
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

  /* Script the microphone to have one short phrase ready (M1-03): the
   * loopback buffer should end up with exactly this many bytes. */
  const size_t phrase_bytes = 512;
  hw_fake_set_capture_available(&g_hw_fake, phrase_bytes);
  /* Hold the speaker "still playing" until the test says otherwise, so
   * PLAYING is observable for more than a single tick (see below). */
  hw_fake_set_playback_drained(&g_hw_fake, false);

  /* IDLE -> press button: expect RECORDING with red LED and the
   * microphone (M1-03) actually started. */
  hw_fake_set_button(&g_hw_fake, true);
  tick_to(200);
  CHECK(app_state() == STATE_RECORDING, "RECORDING after press");
  CHECK(led_get_state() == LED_STATE_RECORDING, "LED state RECORDING after press");
  CHECK(g_hw_fake.capture_start_calls == 1, "microphone started once on press");
  /* Red blink: at some phase the LED word must be RED. */
  uint32_t red_seen = 0, green_seen = 0;
  for (uint32_t t = 200; t < 750; t += 10) {
    hw_fake_set_clock(&g_hw_fake, t);
    app_tick();
    if (g_hw_fake.led_rgb565 == LED_RGB_RED) red_seen++;
  }
  CHECK(red_seen > 0, "red LED pulses while RECORDING");
  CHECK(g_hw_fake.capture_bytes_read_total == phrase_bytes,
        "all scripted mic audio was captured (M1-03) while RECORDING");

  /* Release: microphone stops, session closes, and the state machine
   * advances RECORDING -> PROCESSING -> PLAYING automatically (M1
   * loopback: there is no backend to wait on, see main.c file header).
   * Step tick-by-tick (not via tick_to, which could run far enough past
   * the transition to skip over observing PROCESSING) through the
   * debounce window and the automatic PROCESSING->PLAYING hop. */
  hw_fake_set_button(&g_hw_fake, false);
  int saw_processing = 0;
  int saw_playing = 0;
  for (int i = 0; i < 40 && !saw_playing; i++) {
    state_t s = step_tick();
    if (s == STATE_PROCESSING) saw_processing = 1;
    if (s == STATE_PLAYING) saw_playing = 1;
  }
  CHECK(g_hw_fake.capture_stop_calls == 1, "microphone stopped once on release");
  CHECK(saw_processing, "state machine passed through PROCESSING");
  CHECK(saw_playing, "state machine reached PLAYING");
  CHECK(led_get_state() == LED_STATE_PLAYBACK, "LED state PLAYBACK");
  CHECK(g_hw_fake.playback_start_calls == 1, "speaker started once entering PLAYING");

  /* Green blink while PLAYING (held open via playback_drained = false). */
  for (uint32_t i = 0; i < 50; i++) {
    hw_fake_set_clock(&g_hw_fake, g_hw_fake.clock_ms + 10);
    app_tick();
    if (g_hw_fake.led_rgb565 == LED_RGB_GREEN) green_seen++;
  }
  CHECK(green_seen > 0, "green LED pulses while PLAYING");
  CHECK(app_state() == STATE_PLAYING, "still PLAYING while hardware has not drained");
  CHECK(g_hw_fake.playback_bytes_written_total == phrase_bytes,
        "every captured byte was handed to the speaker (M1-04 loopback)");

  /* Hardware finally confirms the speaker finished draining -> IDLE. */
  hw_fake_set_playback_drained(&g_hw_fake, true);
  tick_to(g_hw_fake.clock_ms + 100);
  CHECK(app_state() == STATE_IDLE, "IDLE after playback drains");
  CHECK(led_get_state() == LED_STATE_IDLE, "LED state IDLE after playback");
  CHECK(g_hw_fake.playback_stop_calls == 1, "speaker stopped once after draining");
  CHECK(g_hw_fake.led_writes > 0, "LED writes happened");
  CHECK(g_hw_fake.button_reads > 0, "button was polled");

  printf(failures == 0
             ? "OK: BOOT->IDLE->RECORDING->PROCESSING->PLAYING->IDLE + "
               "button/LED (red=record, green=play) + M1-03/M1-04 audio "
               "loopback verified\n"
             : "TEST FAILURES: %d\n",
         failures);
  TEST_ASSERT_EQUAL_INT_MESSAGE(0, failures, "scenario had CHECK() failures, see stdout above");
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_full_state_machine_scenario);
  RUN_TEST(test_ring_buffer_overflow_recovery);
  RUN_TEST(test_max_record_seconds_auto_finish);
  return UNITY_END();
}

/*
 * Spec section 10: simulate the I2S capture task filling the ring buffer
 * past capacity while recording. The app must stop recording immediately
 * (not keep silently dropping data), stop the microphone (M1-03), close
 * the HTTP session gracefully, show ERROR (blinking red), then recover to
 * IDLE without a reboot.
 */
static int overflow_failures = 0;

#define OCHECK(cond, msg)                                              \
  do {                                                                \
    if (!(cond)) {                                                    \
      printf("FAIL: %s (line %d)\n", msg, __LINE__);                  \
      overflow_failures++;                                            \
    }                                                                 \
  } while (0)

static void test_ring_buffer_overflow_recovery(void) {
  hw_fake_reset(&g_hw_fake);
  app_init();

  /* BOOT -> IDLE. */
  tick_to(60);
  OCHECK(app_state() == STATE_IDLE, "IDLE after boot");

  /* IDLE -> RECORDING: session must open, microphone must start. */
  hw_fake_set_button(&g_hw_fake, true);
  tick_to(200);
  OCHECK(app_state() == STATE_RECORDING, "RECORDING after press");
  OCHECK(app_session_state() == HTTP_SESSION_OPEN, "session OPEN while recording");
  OCHECK(g_hw_fake.capture_start_calls == 1, "microphone started");

  /* Capture outruns the network: overflow flag goes up (this uses the
   * app.rb network-drain buffer's test hook directly, independent of the
   * hw mic fake -- see app_test_simulate_ring_overflow()). */
  app_test_simulate_ring_overflow();

  /* One tick later: recording must have STOPPED — ERROR, not RECORDING. */
  app_tick();
  OCHECK(app_state() == STATE_ERROR, "ERROR immediately after overflow");
  OCHECK(led_get_state() == LED_STATE_ERROR, "LED in ERROR state");
  OCHECK(g_hw_fake.capture_stop_calls == 1, "microphone stopped on overflow");
  /* Graceful close: CLOSED and NOT aborted. */
  OCHECK(app_session_state() == HTTP_SESSION_CLOSED, "session CLOSED after overflow");
  OCHECK(!app_session_aborted(), "session closed gracefully, not aborted");
  /*
   * ERROR LED must blink orange (existing LED_STATE_ERROR color, unchanged
   * by this task per its "GPIO, LED logic" non-goal). Stay well inside the
   * APP_ERROR_RECOVER_TICKS budget (10 ticks) while sampling — the ERROR
   * blink period/duty (250 ms / 125 ms, see led_config.h) needs only a
   * handful of ticks to show both phases, and running the loop past the
   * recovery budget would trigger the ERROR->IDLE transition mid-loop and
   * corrupt the "still ERROR" assertion below.
   */
  int orange_on = 0, orange_off = 0;
  for (uint32_t t = 210; t < 290; t += 10) {
    hw_fake_set_clock(&g_hw_fake, t);
    app_tick();
    if (g_hw_fake.led_rgb565 == LED_RGB_ORANGE) orange_on++;
    if (g_hw_fake.led_rgb565 == LED_RGB_OFF) orange_off++;
  }
  OCHECK(orange_on > 0 && orange_off > 0, "ERROR LED blinks orange (on + off phases)");
  OCHECK(app_state() == STATE_ERROR, "still ERROR during recovery window");

  /*
   * The user releases the button once the overflow/ERROR indication shows
   * (recording already stopped when the overflow fired). Without this, the
   * button would still read "pressed" when ERROR recovers to IDLE and
   * button_reset() re-arms it, causing an immediate spurious PRESSED event
   * that reopens RECORDING before this test can observe the IDLE state —
   * a separate, deliberate re-press is exercised further down instead.
   */
  hw_fake_set_button(&g_hw_fake, false);

  /* Recovery: ERROR -> IDLE after APP_ERROR_RECOVER_TICKS, no reboot. */
  tick_to(2000);
  OCHECK(app_state() == STATE_IDLE, "IDLE after recovery");
  OCHECK(led_get_state() == LED_STATE_IDLE, "LED back to IDLE after recovery");
  OCHECK(!app_session_aborted(), "no abort ever happened");

  /* A new recording works after recovery (flag was cleared on start), and
   * the full M1-03/M1-04 loopback round-trip completes back to IDLE. */
  hw_fake_set_button(&g_hw_fake, false);
  tick_to(2200);
  hw_fake_set_button(&g_hw_fake, true);
  tick_to(2400);
  OCHECK(app_state() == STATE_RECORDING, "RECORDING again after recovery");
  OCHECK(app_session_state() == HTTP_SESSION_OPEN, "new session OPEN after recovery");
  OCHECK(g_hw_fake.capture_start_calls == 2, "microphone restarted for new recording");
  hw_fake_set_button(&g_hw_fake, false);
  tick_to(2900);
  OCHECK(app_state() == STATE_IDLE, "full loopback finished back at IDLE after recovery");
  OCHECK(app_session_state() == HTTP_SESSION_CLOSED, "session closed on the recovered recording");

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, overflow_failures,
      "overflow scenario had CHECK() failures, see stdout above");
}

/*
 * Spec section 7: a held button must auto-finish at MAX_RECORD_SECONDS
 * (configurable; 120 s default). The button driver fires
 * MAX_RECORD_TIMEOUT, the RECORDING tick treats it like a release, the
 * microphone stops (M1-03), and the HTTP session is closed gracefully.
 */
static int maxrec_failures = 0;

#define MCHECK(cond, msg)                                              \
  do {                                                                \
    if (!(cond)) {                                                    \
      printf("FAIL: %s (line %d)\n", msg, __LINE__);                  \
      maxrec_failures++;                                              \
    }                                                                 \
  } while (0)

static void test_max_record_seconds_auto_finish(void) {
  hw_fake_reset(&g_hw_fake);
  app_init();
  tick_to(60);
  MCHECK(app_state() == STATE_IDLE, "IDLE after boot");

  /* Press and HOLD past MAX_RECORD_SECONDS (120 s default). */
  hw_fake_set_button(&g_hw_fake, true);
  tick_to(200);
  MCHECK(app_state() == STATE_RECORDING, "RECORDING while held");
  MCHECK(app_session_state() == HTTP_SESSION_OPEN, "session OPEN while held");
  MCHECK(g_hw_fake.capture_start_calls == 1, "microphone started for the held recording");

  /* Hold until just past the limit; tick_to advances in 10 ms steps. Once
   * the auto-finish fires the state machine immediately cascades
   * RECORDING -> PROCESSING -> PLAYING -> IDLE on its own (M1 loopback,
   * no backend to wait on) -- release the button the instant recording
   * stops so a still-held button can't immediately retrigger a new press
   * once IDLE is reached and button_reset() re-arms it. */
  int saw_recording_stop = 0;
  for (uint32_t t = g_hw_fake.clock_ms + 10; t <= 130000; t += 10) {
    hw_fake_set_clock(&g_hw_fake, t);
    app_tick();
    if (g_hw_fake.capture_stop_calls > 0) {
      saw_recording_stop = 1;
      hw_fake_set_button(&g_hw_fake, false);
      break;
    }
  }
  MCHECK(saw_recording_stop, "auto-finished at MAX_RECORD_SECONDS");
  MCHECK(app_session_state() == HTTP_SESSION_CLOSED, "session closed at limit");
  MCHECK(!app_session_aborted(), "session closed gracefully, not aborted");
  MCHECK(g_hw_fake.capture_stop_calls == 1, "microphone stopped exactly once at the auto-finish limit");

  /* The M1 loopback (M1-03/M1-04) runs the rest of the turn through to
   * completion on its own (no backend to wait on, see main.c). */
  for (int i = 0; i < 40 && app_state() != STATE_IDLE; i++) {
    step_tick();
  }
  MCHECK(app_state() == STATE_IDLE, "IDLE after auto-finished turn");
  MCHECK(led_get_state() == LED_STATE_IDLE, "LED IDLE after auto-finished turn");

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, maxrec_failures,
      "max-record scenario had CHECK() failures, see stdout above");
}
