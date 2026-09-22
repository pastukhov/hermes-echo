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
#include "playback_occupancy.h"
#include "voice_transport.h"
#include "fakes/hw_fakes.h"

/* app API (declared here; test build has no VOICE_WITH_MAIN, so main() is
 * not compiled into main.c). */
void app_init(void);
void app_tick(void);
state_t app_state(void);
http_session_state_t app_session_state(void);
bool app_session_aborted(void);
void app_test_simulate_ring_overflow(void);
void app_test_simulate_playback_conn_drop(void);
void app_test_simulate_playback_bad_wav_header(void);
size_t app_playback_position(void);
size_t app_playback_pending_len(void);
size_t app_playback_rb_count(void);

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
static void test_playback_conn_drop_recovery(void);
static void test_playback_bad_wav_header_recovery(void);
static void test_led_section9_all_states(void);
static void test_playback_eof_idle_transition_full_buffer(void);
static void test_playback_eof_idle_transition_near_empty_buffer(void);
static void test_playback_eof_idempotent_no_double_cleanup(void);
static void test_playback_repeated_cycles_no_resource_accumulation(void);
static void test_playback_occupancy_reaches_zero_after_drain_time(void);
static void test_playback_occupancy_edge_cases(void);
static void test_transport_finish_keeps_response_socket(void);

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

  /* After boot the LED state must be IDLE (dim blue, steady — spec
   * section 9 "слабый синий"). */
  CHECK(led_get_state() == LED_STATE_IDLE, "LED state IDLE after boot");
  CHECK(g_hw_fake.led_rgb565 == LED_RGB_BLUE_DIM, "IDLE LED is dim blue");

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
  /* Spec section 9: RECORDING is steady red ("красный", no blink) for the
   * whole window. */
  uint32_t red_seen = 0, green_seen = 0;
  for (uint32_t t = 200; t < 750; t += 10) {
    hw_fake_set_clock(&g_hw_fake, t);
    app_tick();
    if (g_hw_fake.led_rgb565 == LED_RGB_RED) red_seen++;
  }
  CHECK(red_seen == 55, "LED steady red for the entire RECORDING window");
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

  /* Spec section 9: PLAYBACK is steady green ("зелёный", no blink). */
  for (uint32_t i = 0; i < 50; i++) {
    hw_fake_set_clock(&g_hw_fake, g_hw_fake.clock_ms + 10);
    app_tick();
    if (g_hw_fake.led_rgb565 == LED_RGB_GREEN) green_seen++;
  }
  CHECK(green_seen == 50, "LED steady green for the entire PLAYING window");
  CHECK(app_state() == STATE_PLAYING, "still PLAYING while hardware has not drained");
  CHECK(g_hw_fake.playback_bytes_written_total == phrase_bytes,
        "every captured byte was handed to the speaker (M1-04 loopback)");

  /* Hardware finally confirms the speaker finished draining -> IDLE. */
  hw_fake_set_playback_drained(&g_hw_fake, true);
  tick_to(g_hw_fake.clock_ms + 100);
  CHECK(app_state() == STATE_IDLE, "IDLE after playback drains");
  CHECK(led_get_state() == LED_STATE_IDLE, "LED state IDLE after playback");
  CHECK(g_hw_fake.led_rgb565 == LED_RGB_BLUE_DIM,
        "IDLE LED back to steady dim blue after playback");
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
  RUN_TEST(test_playback_conn_drop_recovery);
  RUN_TEST(test_playback_bad_wav_header_recovery);
  RUN_TEST(test_playback_eof_idle_transition_full_buffer);
  RUN_TEST(test_playback_eof_idle_transition_near_empty_buffer);
  RUN_TEST(test_playback_eof_idempotent_no_double_cleanup);
  RUN_TEST(test_playback_repeated_cycles_no_resource_accumulation);
  RUN_TEST(test_playback_occupancy_reaches_zero_after_drain_time);
  RUN_TEST(test_playback_occupancy_edge_cases);
  RUN_TEST(test_led_section9_all_states);
  RUN_TEST(test_transport_finish_keeps_response_socket);
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
   * Spec section 9: ERROR is blinking red (мигающий красный). Stay well
   * inside the APP_ERROR_RECOVER_TICKS budget (10 ticks) while sampling —
   * the ERROR blink period/duty (250 ms / 125 ms, see led_config.h) needs
   * only a handful of ticks to show both phases, and running the loop past
   * the recovery budget would trigger the ERROR->IDLE transition mid-loop
   * and corrupt the "still ERROR" assertion below.
   */
  int red_on = 0, red_off = 0;
  for (uint32_t t = 210; t < 290; t += 10) {
    hw_fake_set_clock(&g_hw_fake, t);
    app_tick();
    if (g_hw_fake.led_rgb565 == LED_RGB_RED) red_on++;
    if (g_hw_fake.led_rgb565 == LED_RGB_OFF) red_off++;
  }
  OCHECK(red_on > 0 && red_off > 0, "ERROR LED blinks red (on + off phases)");
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

/*
 * Spec sections 8/13/38: a backend connection drop mid-stream during
 * PLAYING must drive the device PLAYING -> ERROR -> (after
 * APP_ERROR_RECOVER_TICKS) IDLE automatically, without reboot, and without
 * hanging. Structured as a close mirror of
 * test_ring_buffer_overflow_recovery() (the analogous RECORDING-side
 * failure), since both go through the same ERROR/recovery/LED machinery in
 * main.c's enter_state()/APP_ERROR_RECOVER_TICKS.
 */
static int conn_drop_failures = 0;

#define CDCHECK(cond, msg)                                             \
  do {                                                                 \
    if (!(cond)) {                                                     \
      printf("FAIL: %s (line %d)\n", msg, __LINE__);                   \
      conn_drop_failures++;                                            \
    }                                                                  \
  } while (0)

static void test_playback_conn_drop_recovery(void) {
  hw_fake_reset(&g_hw_fake);
  app_init();

  /* BOOT -> IDLE. */
  tick_to(60);
  CDCHECK(app_state() == STATE_IDLE, "IDLE after boot");

  /* Drive a full turn into PLAYING (M1 loopback): press, script a short
   * phrase, release -> RECORDING -> PROCESSING -> PLAYING. Keep the sink
   * "still playing" so PLAYING is observable for more than a single tick,
   * mirroring test_full_state_machine_scenario's setup. */
  const size_t phrase_bytes = 128;
  hw_fake_set_capture_available(&g_hw_fake, phrase_bytes);
  hw_fake_set_playback_drained(&g_hw_fake, false);
  hw_fake_set_button(&g_hw_fake, true);
  tick_to(200);
  CDCHECK(app_state() == STATE_RECORDING, "RECORDING after press");
  hw_fake_set_button(&g_hw_fake, false);
  int saw_playing = 0;
  for (int i = 0; i < 40 && !saw_playing; i++) {
    if (step_tick() == STATE_PLAYING) saw_playing = 1;
  }
  CDCHECK(saw_playing, "state machine reached PLAYING");
  CDCHECK(g_hw_fake.playback_start_calls == 1, "speaker started once entering PLAYING");

  /* Snap the clock forward (monotonically) to a phase matching the known
   * blink boundary used by test_ring_buffer_overflow_recovery (entry ends
   * in "...200" mod the 250ms ERROR blink period): the LED sampling window
   * below then straddles the on/off boundary the same deterministic way,
   * regardless of how many ticks it took to reach PLAYING above. */
  uint32_t entry_clock = (g_hw_fake.clock_ms / 250 + 1) * 250 + 200;
  hw_fake_set_clock(&g_hw_fake, entry_clock);

  /* Backend connection drops mid-stream while the reply is being played
   * (the scenario this task's acceptance criteria calls out by name:
   * "обрыв соединения во время стрима ответа"). This must not hang the
   * device -- the very next tick must observe ERROR, not a stuck PLAYING. */
  app_test_simulate_playback_conn_drop();
  app_tick();
  CDCHECK(app_state() == STATE_ERROR, "ERROR immediately after connection drop");
  CDCHECK(led_get_state() == LED_STATE_ERROR, "LED in ERROR state");
  CDCHECK(g_hw_fake.playback_stop_calls == 1, "speaker stopped on connection drop");

  /* Stay inside the APP_ERROR_RECOVER_TICKS budget while sampling the LED
   * (same rationale as test_ring_buffer_overflow_recovery: running past the
   * recovery budget would fire ERROR->IDLE mid-loop and corrupt the "still
   * ERROR" assertion below). */
  int red_on = 0, red_off = 0;
  for (uint32_t t = entry_clock + 10; t < entry_clock + 90; t += 10) {
    hw_fake_set_clock(&g_hw_fake, t);
    app_tick();
    if (g_hw_fake.led_rgb565 == LED_RGB_RED) red_on++;
    if (g_hw_fake.led_rgb565 == LED_RGB_OFF) red_off++;
  }
  CDCHECK(red_on > 0 && red_off > 0, "ERROR LED blinks red (on + off phases, spec section 9)");
  CDCHECK(app_state() == STATE_ERROR, "still ERROR during recovery window");

  /* Recovery: ERROR -> IDLE after APP_ERROR_RECOVER_TICKS, no reboot. */
  tick_to(g_hw_fake.clock_ms + 2000);
  CDCHECK(app_state() == STATE_IDLE, "IDLE after recovery, no reboot required");
  CDCHECK(led_get_state() == LED_STATE_IDLE, "LED back to IDLE after recovery");

  /* A new turn works after recovery (flags were cleared on the next
   * playback_start()), proving the device is not wedged. */
  hw_fake_set_capture_available(&g_hw_fake, phrase_bytes);
  hw_fake_set_playback_drained(&g_hw_fake, true);
  hw_fake_set_button(&g_hw_fake, true);
  tick_to(g_hw_fake.clock_ms + 200);
  CDCHECK(app_state() == STATE_RECORDING, "RECORDING again after recovery");
  hw_fake_set_button(&g_hw_fake, false);
  int i;
  for (i = 0; i < 60 && app_state() != STATE_IDLE; i++) {
    step_tick();
  }
  CDCHECK(app_state() == STATE_IDLE, "full turn completes back to IDLE after recovery");

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, conn_drop_failures,
      "playback connection-drop scenario had CHECK() failures, see stdout above");
}

/*
 * Spec sections 8/13/38: a malformed/incorrect WAV header on the reply must
 * also drive PLAYING -> ERROR -> IDLE automatically, without reboot. Same
 * shape as test_playback_conn_drop_recovery() above, just the other
 * sticky flag.
 */
static int bad_wav_failures = 0;

#define WVCHECK(cond, msg)                                             \
  do {                                                                 \
    if (!(cond)) {                                                     \
      printf("FAIL: %s (line %d)\n", msg, __LINE__);                   \
      bad_wav_failures++;                                              \
    }                                                                  \
  } while (0)

static void test_playback_bad_wav_header_recovery(void) {
  hw_fake_reset(&g_hw_fake);
  app_init();

  tick_to(60);
  WVCHECK(app_state() == STATE_IDLE, "IDLE after boot");

  const size_t phrase_bytes = 128;
  hw_fake_set_capture_available(&g_hw_fake, phrase_bytes);
  hw_fake_set_playback_drained(&g_hw_fake, false);
  hw_fake_set_button(&g_hw_fake, true);
  tick_to(200);
  hw_fake_set_button(&g_hw_fake, false);
  int saw_playing = 0;
  for (int i = 0; i < 40 && !saw_playing; i++) {
    if (step_tick() == STATE_PLAYING) saw_playing = 1;
  }
  WVCHECK(saw_playing, "state machine reached PLAYING");

  /* Reply arrives with a malformed WAV header. */
  app_test_simulate_playback_bad_wav_header();
  app_tick();
  WVCHECK(app_state() == STATE_ERROR, "ERROR immediately after bad WAV header");
  WVCHECK(led_get_state() == LED_STATE_ERROR, "LED in ERROR state");
  WVCHECK(g_hw_fake.playback_stop_calls == 1, "speaker stopped on bad WAV header");

  tick_to(g_hw_fake.clock_ms + 2000);
  WVCHECK(app_state() == STATE_IDLE, "IDLE after recovery, no reboot required");
  WVCHECK(led_get_state() == LED_STATE_IDLE, "LED back to IDLE after recovery");

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, bad_wav_failures,
      "playback bad-WAV-header scenario had CHECK() failures, see stdout above");
}

/*
 * Spec section 9 acceptance (this task's direct criterion): every state in
 * the section-9 LED table must show exactly its specified color/pattern,
 * across every transition path the state machine can take -- IDLE (dim
 * blue, steady), RECORDING (steady red), PROCESSING (steady yellow),
 * PLAYBACK (steady green) and ERROR (blinking red) -- and the LED must
 * follow the state through a full turn plus an error and recovery back to
 * IDLE. The other tests sample colors incidentally along their own
 * scenarios; this one pins the section-9 table state-by-state, including
 * the single-tick PROCESSING state no other test observes.
 */
static int led9_failures = 0;

#define L9CHECK(cond, msg)                                             \
  do {                                                                 \
    if (!(cond)) {                                                     \
      printf("FAIL: %s (line %d)\n", msg, __LINE__);                   \
      led9_failures++;                                                 \
    }                                                                  \
  } while (0)

static void test_led_section9_all_states(void) {
  hw_fake_reset(&g_hw_fake);
  app_init();

  /* BOOT -> IDLE: section 9 "слабый синий", steady. */
  tick_to(60);
  L9CHECK(app_state() == STATE_IDLE, "IDLE after boot");
  L9CHECK(led_get_state() == LED_STATE_IDLE, "LED state IDLE");
  L9CHECK(g_hw_fake.led_rgb565 == LED_RGB_BLUE_DIM, "IDLE shows dim blue");

  /* Full turn with a short phrase: RECORDING -> PROCESSING -> PLAYING. */
  const size_t phrase_bytes = 96;
  hw_fake_set_capture_available(&g_hw_fake, phrase_bytes);
  hw_fake_set_playback_drained(&g_hw_fake, false);
  hw_fake_set_button(&g_hw_fake, true);
  tick_to(200);
  L9CHECK(app_state() == STATE_RECORDING, "RECORDING after press");
  L9CHECK(led_get_state() == LED_STATE_RECORDING, "LED state RECORDING");
  /* Steady red for the entire recording window: every single tick of it
   * shows red, none of them off or any other color. */
  int red_ticks = 0;
  for (int i = 0; i < 10; i++) {
    step_tick();
    if (g_hw_fake.led_rgb565 == LED_RGB_RED) red_ticks++;
  }
  L9CHECK(red_ticks == 10, "RECORDING shows steady red every tick");

  hw_fake_set_button(&g_hw_fake, false);
  int saw_processing = 0, saw_playback = 0;
  for (int i = 0; i < 40 && !saw_playback; i++) {
    state_t s = step_tick();
    if (s == STATE_PROCESSING) saw_processing = 1;
    if (s == STATE_PLAYING) saw_playback = 1;
  }
  L9CHECK(saw_processing, "passed through PROCESSING");
  L9CHECK(saw_playback, "reached PLAYING");

  /* PLAYBACK: section 9 "зелёный", steady, for every tick the state lasts
   * (held open with playback_drained = false). */
  int green_ticks = 0;
  for (int i = 0; i < 20; i++) {
    step_tick();
    if (g_hw_fake.led_rgb565 == LED_RGB_GREEN) green_ticks++;
  }
  L9CHECK(green_ticks == 20, "PLAYBACK shows steady green every tick");
  L9CHECK(led_get_state() == LED_STATE_PLAYBACK, "LED state PLAYBACK");

  /* EOF -> IDLE: dim blue again, steady. */
  hw_fake_set_playback_drained(&g_hw_fake, true);
  step_tick();
  L9CHECK(app_state() == STATE_IDLE, "IDLE after playback drains");
  L9CHECK(led_get_state() == LED_STATE_IDLE, "LED state IDLE after playback");
  /* led_update() runs at the top of app_tick(), so the pixel that shows
   * the NEW state is written on the tick AFTER the transition; sample one
   * tick later. */
  step_tick();
  L9CHECK(g_hw_fake.led_rgb565 == LED_RGB_BLUE_DIM, "IDLE back to dim blue");

  /* Error path (section 9: "мигающий красный"): drive RECORDING -> ERROR
   * with the ring-overflow hook (the overflow flag is only checked in the
   * RECORDING tick, so the app must actually be recording when it fires).
   * The ERROR blink is ON for the first 125 ms of each 250 ms cycle
   * (led_config.h), and led_update() runs at the top of app_tick(), so
   * the first ERROR pixel is drawn on the tick that detects the
   * overflow. We hold the button and advance in RECORDING until that
   * detection tick's clock phase is exactly 100 ms, so the following
   * 9-tick sampling window covers phases 100..180 -- ON through 120,
   * OFF from 130 -- observing both blink phases while still inside the
   * 10-tick APP_ERROR_RECOVER_TICKS budget (the state cannot leave
   * ERROR mid-sampling). */
  hw_fake_set_button(&g_hw_fake, true);
  tick_to(g_hw_fake.clock_ms + 100);
  L9CHECK(app_state() == STATE_RECORDING, "RECORDING for error-path setup");
  while ((g_hw_fake.clock_ms + 10) % 250 != 100) {
    step_tick(); /* stay in RECORDING, aligning the blink phase */
  }
  app_test_simulate_ring_overflow();
  step_tick();
  L9CHECK(app_state() == STATE_ERROR, "ERROR after overflow");
  L9CHECK(led_get_state() == LED_STATE_ERROR, "LED state ERROR");
  hw_fake_set_button(&g_hw_fake, false); /* release before recovery */
  int red_on = 0, red_off = 0;
  for (int i = 0; i < 9; i++) {
    step_tick();
    if (g_hw_fake.led_rgb565 == LED_RGB_RED) red_on++;
    if (g_hw_fake.led_rgb565 == LED_RGB_OFF) red_off++;
  }
  L9CHECK(red_on > 0 && red_off > 0, "ERROR blinks red (on + off phases)");
  L9CHECK(app_state() == STATE_ERROR, "still ERROR inside recovery budget");

  /* Recovery: ERROR -> IDLE, and the LED returns to steady dim blue. */
  tick_to(g_hw_fake.clock_ms + 2000);
  L9CHECK(app_state() == STATE_IDLE, "IDLE after ERROR recovery");
  L9CHECK(led_get_state() == LED_STATE_IDLE, "LED state IDLE after recovery");
  L9CHECK(g_hw_fake.led_rgb565 == LED_RGB_BLUE_DIM, "dim blue after recovery");

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, led9_failures,
      "section-9 LED mapping scenario had CHECK() failures, see stdout above");
}

/*
 * Spec section 8 acceptance criteria: PLAYING -> IDLE on EOF must be
 * deterministic (fires within one tick of the last sample being
 * consumed) and must fully release playback resources -- ring buffer,
 * pending chunk, position counter, and the DMA/I2S peripheral -- exactly
 * once, regardless of whether the loopback buffer was still full or
 * nearly empty when EOF was confirmed. These two tests are a close pair:
 * same shape, opposite buffer-fill extremes at the moment of EOF.
 */
static int eof_full_failures = 0;

#define EOFFCHECK(cond, msg)                                           \
  do {                                                                 \
    if (!(cond)) {                                                     \
      printf("FAIL: %s (line %d)\n", msg, __LINE__);                   \
      eof_full_failures++;                                             \
    }                                                                  \
  } while (0)

static void test_playback_eof_idle_transition_full_buffer(void) {
  hw_fake_reset(&g_hw_fake);
  app_init();

  tick_to(60);
  EOFFCHECK(app_state() == STATE_IDLE, "IDLE after boot");

  /* A phrase large enough that it will not entirely drain in a single
   * playback_drain() call while the sink is capped (see write limit
   * below): the loopback buffer is still FULL/near-full the tick EOF
   * ultimately fires, exercising the "buffer full at EOF" case. */
  const size_t phrase_bytes = 4096;
  hw_fake_set_capture_available(&g_hw_fake, phrase_bytes);
  /* Cap what the sink accepts per tick well below the phrase size, so the
   * loopback buffer stays substantially full across multiple PLAYING
   * ticks instead of draining in one shot -- the EOF check must still be
   * exactly one tick after the true last sample is consumed regardless
   * of how many ticks the drain itself took. */
  hw_fake_set_playback_write_limit(&g_hw_fake, 256);
  hw_fake_set_playback_drained(&g_hw_fake, false);
  hw_fake_set_button(&g_hw_fake, true);
  tick_to(200);
  EOFFCHECK(app_state() == STATE_RECORDING, "RECORDING after press");
  hw_fake_set_button(&g_hw_fake, false);
  int saw_playing = 0;
  for (int i = 0; i < 40 && !saw_playing; i++) {
    if (step_tick() == STATE_PLAYING) saw_playing = 1;
  }
  EOFFCHECK(saw_playing, "state machine reached PLAYING");
  EOFFCHECK(g_hw_fake.playback_start_calls == 1, "speaker started once entering PLAYING");

  /* Drive ticks until every byte has been handed to the speaker (ring
   * buffer + pending chunk both empty) but keep hw_audio_playback_drained
   * reporting false a little longer, so the state machine must still be
   * PLAYING -- confirming it does NOT transition early just because the
   * buffer emptied (drain != EOF; EOF also needs the hardware-confirmed
   * drain). */
  int drained_all_bytes = 0;
  for (int i = 0; i < 200 && !drained_all_bytes; i++) {
    app_tick();
    if (app_playback_rb_count() == 0 && app_playback_pending_len() == 0) {
      drained_all_bytes = 1;
    }
  }
  EOFFCHECK(drained_all_bytes, "every byte eventually handed to the speaker");
  EOFFCHECK(app_playback_position() == phrase_bytes,
            "position counter tracked every byte written to the sink");
  EOFFCHECK(app_state() == STATE_PLAYING,
            "still PLAYING: buffer empty but hardware has not confirmed drain yet");

  /* Now the hardware confirms EOF. The very next tick must observe IDLE
   * -- deterministic, one-tick transition, no dependence on how the
   * buffer got drained (it was capped/near-full for most of this test). */
  hw_fake_set_playback_drained(&g_hw_fake, true);
  app_tick();
  EOFFCHECK(app_state() == STATE_IDLE, "IDLE within one tick of confirmed EOF");
  EOFFCHECK(led_get_state() == LED_STATE_IDLE, "LED IDLE after EOF transition");

  /* Resource cleanup on IDLE entry (spec section 8): ring buffer freed,
   * pending chunk cleared, position counter cleared, DMA/I2S stopped. */
  EOFFCHECK(app_playback_rb_count() == 0, "loopback ring buffer freed/empty after EOF");
  EOFFCHECK(app_playback_pending_len() == 0, "no pending unwritten chunk after EOF");
  EOFFCHECK(app_playback_position() == 0, "playback position counter cleared after EOF");
  EOFFCHECK(g_hw_fake.playback_stop_calls == 1, "DMA/I2S peripheral stopped exactly once after EOF");

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, eof_full_failures,
      "EOF full-buffer scenario had CHECK() failures, see stdout above");
}

static int eof_empty_failures = 0;

#define EOFECHECK(cond, msg)                                           \
  do {                                                                 \
    if (!(cond)) {                                                     \
      printf("FAIL: %s (line %d)\n", msg, __LINE__);                   \
      eof_empty_failures++;                                            \
    }                                                                  \
  } while (0)

static void test_playback_eof_idle_transition_near_empty_buffer(void) {
  hw_fake_reset(&g_hw_fake);
  app_init();

  tick_to(60);
  EOFECHECK(app_state() == STATE_IDLE, "IDLE after boot");

  /* A short phrase that fully drains to the sink in a single
   * playback_drain() call (no write limit) -- the loopback buffer is
   * already EMPTY well before EOF is confirmed, exercising the opposite
   * extreme from the full-buffer test above. */
  const size_t phrase_bytes = 64;
  hw_fake_set_capture_available(&g_hw_fake, phrase_bytes);
  hw_fake_set_playback_drained(&g_hw_fake, false);
  hw_fake_set_button(&g_hw_fake, true);
  tick_to(200);
  hw_fake_set_button(&g_hw_fake, false);
  int saw_playing = 0;
  for (int i = 0; i < 40 && !saw_playing; i++) {
    if (step_tick() == STATE_PLAYING) saw_playing = 1;
  }
  EOFECHECK(saw_playing, "state machine reached PLAYING");

  /* One more tick: with no write limit, the whole short phrase drains to
   * the sink immediately -- buffer empty well ahead of EOF confirmation. */
  app_tick();
  EOFECHECK(app_playback_rb_count() == 0, "loopback buffer already empty ahead of EOF");
  EOFECHECK(app_playback_pending_len() == 0, "no pending chunk ahead of EOF");
  EOFECHECK(app_state() == STATE_PLAYING,
            "still PLAYING while hardware has not confirmed drain, even though buffer is empty");

  /* EOF confirmed with an already-empty buffer: must still transition
   * within exactly one tick, same as the full-buffer case. */
  hw_fake_set_playback_drained(&g_hw_fake, true);
  app_tick();
  EOFECHECK(app_state() == STATE_IDLE, "IDLE within one tick of confirmed EOF (near-empty buffer)");
  EOFECHECK(g_hw_fake.playback_stop_calls == 1, "DMA/I2S peripheral stopped after EOF");
  EOFECHECK(app_playback_position() == 0, "playback position counter cleared after EOF");

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, eof_empty_failures,
      "EOF near-empty-buffer scenario had CHECK() failures, see stdout above");
}

/*
 * Spec section 8 idempotency requirement: a spurious re-entry of EOF
 * detection while already IDLE must be a no-op -- no double cleanup, no
 * extra hardware stop call, no illegal state transition attempt.
 */
static int eof_idempotent_failures = 0;

#define EOFICHECK(cond, msg)                                           \
  do {                                                                 \
    if (!(cond)) {                                                     \
      printf("FAIL: %s (line %d)\n", msg, __LINE__);                   \
      eof_idempotent_failures++;                                       \
    }                                                                  \
  } while (0)

static void test_playback_eof_idempotent_no_double_cleanup(void) {
  hw_fake_reset(&g_hw_fake);
  app_init();

  tick_to(60);
  const size_t phrase_bytes = 64;
  hw_fake_set_capture_available(&g_hw_fake, phrase_bytes);
  hw_fake_set_playback_drained(&g_hw_fake, true); /* drains immediately */
  hw_fake_set_button(&g_hw_fake, true);
  tick_to(200);
  hw_fake_set_button(&g_hw_fake, false);
  int saw_idle_again = 0;
  for (int i = 0; i < 40 && !saw_idle_again; i++) {
    state_t s = step_tick();
    if (s == STATE_PLAYING) {
      /* Immediately confirmed drained -> transitions back to IDLE next
       * tick or two. */
    }
    if (s == STATE_IDLE && i > 0) {
      saw_idle_again = 1;
    }
  }
  EOFICHECK(saw_idle_again, "full turn completed back to IDLE");
  EOFICHECK(app_state() == STATE_IDLE, "settled in IDLE");
  int stop_calls_at_idle = g_hw_fake.playback_stop_calls;
  EOFICHECK(stop_calls_at_idle >= 1, "playback stopped at least once reaching IDLE");

  /* Spurious re-entry: hw_audio_playback_drained() still reads true (as
   * it naturally would, nothing playing) and IDLE is driven by the
   * IDLE-state button-poll branch, not the PLAYING EOF branch -- ticking
   * further in IDLE must never re-run playback cleanup or re-invoke
   * hw_audio_playback_stop(). This proves entering IDLE from anywhere
   * other than a genuine PLAYING EOF cannot re-trigger the cleanup path. */
  for (int i = 0; i < 5; i++) {
    app_tick();
  }
  EOFICHECK(app_state() == STATE_IDLE, "still IDLE, no-op");
  EOFICHECK(g_hw_fake.playback_stop_calls == stop_calls_at_idle,
            "no additional playback_stop calls from idle-state re-ticking (idempotent)");
  EOFICHECK(app_playback_rb_count() == 0, "ring buffer still empty (idempotent)");
  EOFICHECK(app_playback_pending_len() == 0, "pending chunk still empty (idempotent)");
  EOFICHECK(app_playback_position() == 0, "position counter still cleared (idempotent)");

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, eof_idempotent_failures,
      "EOF idempotency scenario had CHECK() failures, see stdout above");
}

/*
 * Spec section 8 acceptance criterion: repeated play/stop cycles must not
 * accumulate resources. Runs several full record/play turns back to back
 * and asserts every one ends with buffers freed and the position counter
 * cleared, and that hw start/stop call counts stay in lockstep (one stop
 * per start -- no leaked "still started" playback sessions).
 */
static int repeat_cycles_failures = 0;

#define RCCHECK(cond, msg)                                             \
  do {                                                                 \
    if (!(cond)) {                                                     \
      printf("FAIL: %s (line %d)\n", msg, __LINE__);                   \
      repeat_cycles_failures++;                                        \
    }                                                                  \
  } while (0)

static void test_playback_repeated_cycles_no_resource_accumulation(void) {
  hw_fake_reset(&g_hw_fake);
  app_init();
  tick_to(60);

  const int num_cycles = 5;
  const size_t phrase_bytes = 96;
  for (int cycle = 0; cycle < num_cycles; cycle++) {
    hw_fake_set_capture_available(&g_hw_fake, phrase_bytes);
    hw_fake_set_playback_drained(&g_hw_fake, false);
    hw_fake_set_button(&g_hw_fake, true);
    tick_to(g_hw_fake.clock_ms + 200);
    RCCHECK(app_state() == STATE_RECORDING, "reached RECORDING this cycle");
    hw_fake_set_button(&g_hw_fake, false);
    int saw_playing = 0;
    for (int i = 0; i < 60 && !saw_playing; i++) {
      if (step_tick() == STATE_PLAYING) saw_playing = 1;
    }
    RCCHECK(saw_playing, "reached PLAYING this cycle");

    /* Let it fully drain, then confirm EOF. */
    for (int i = 0; i < 40 && app_playback_rb_count() > 0; i++) {
      app_tick();
    }
    hw_fake_set_playback_drained(&g_hw_fake, true);
    app_tick();
    RCCHECK(app_state() == STATE_IDLE, "back to IDLE at end of cycle");

    /* No accumulation: resources are exactly as clean after this cycle
     * as after any other -- same zeroed buffer/position, and start/stop
     * counts equal (each cycle starts and stops the speaker exactly
     * once, in lockstep with `cycle + 1`). */
    RCCHECK(app_playback_rb_count() == 0, "ring buffer empty at end of cycle");
    RCCHECK(app_playback_pending_len() == 0, "pending chunk empty at end of cycle");
    RCCHECK(app_playback_position() == 0, "position counter cleared at end of cycle");
    RCCHECK(g_hw_fake.playback_start_calls == cycle + 1,
            "playback started exactly once per cycle, no leak");
    RCCHECK(g_hw_fake.playback_stop_calls == cycle + 1,
            "playback stopped exactly once per cycle, in lockstep with start (no leaked session)");
  }

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, repeat_cycles_failures,
      "repeated play/stop cycles had CHECK() failures, see stdout above");
}

/*
 * Reviewer-round-2 defect (see task comment thread): the real-hardware
 * "drained" signal (hw_audio_playback_drained() -> audio_playback_get_
 * buffer_level() == 0) must actually be able to REACH zero once enough
 * wall-clock time has elapsed for the hardware to have played everything
 * that was written, not stay pinned at a fixed non-zero DMA-capacity
 * figure forever. That real-hardware wiring itself (audio_playback.c) is
 * ESP-IDF-only and excluded from the native/host test build (see
 * platformio.ini's build_src_filter), so it cannot be exercised directly
 * here -- but the pure-arithmetic occupancy model it is built on
 * (playback_occupancy.c) has none of those dependencies and IS linked
 * into native. This test is the host-level proof that the model
 * underlying audio_playback_get_buffer_level() is not the old "always
 * constant" defect: occupancy must start at what was written and
 * monotonically fall to exactly zero as simulated time advances at the
 * configured sample rate, matching the real function's behavior 1:1.
 */
static int occupancy_time_failures = 0;

#define OTCHECK(cond, msg)                                             \
  do {                                                                 \
    if (!(cond)) {                                                     \
      printf("FAIL: %s (line %d)\n", msg, __LINE__);                   \
      occupancy_time_failures++;                                       \
    }                                                                  \
  } while (0)

static void test_playback_occupancy_reaches_zero_after_drain_time(void) {
  const int32_t sample_rate = 16000; /* frames/sec, matches DEFAULT_SAMPLE_RATE */
  const uint64_t frames_written = 800; /* 50ms worth at 16kHz */

  /* At t=0 (no time elapsed since playback started): nothing has played
   * yet, so every written frame is still occupying the sink. This is the
   * exact condition that made the old (buggy) implementation permanently
   * report non-zero -- the difference is what happens as time advances
   * below. */
  uint64_t occ_t0 = playback_occupancy_frames(frames_written, 0, sample_rate);
  OTCHECK(occ_t0 == frames_written, "at t=0 occupancy equals everything written");

  /* Halfway through the 50ms of audio (25ms elapsed): half the frames
   * must have been consumed by the hardware. */
  uint64_t occ_half =
      playback_occupancy_frames(frames_written, 25000, sample_rate);
  OTCHECK(occ_half == frames_written / 2,
          "occupancy halves after half the playout duration elapses");

  /* Exactly the full playout duration elapsed: occupancy must reach
   * EXACTLY zero -- this is the critical assertion the old
   * total_dma_buf_size-based implementation could never satisfy (it was a
   * fixed non-zero constant for the life of the channel, so
   * hw_audio_playback_drained() was always false and PLAYING could never
   * transition to IDLE on real hardware). */
  uint64_t occ_full =
      playback_occupancy_frames(frames_written, 50000, sample_rate);
  OTCHECK(occ_full == 0,
          "occupancy reaches exactly zero once the full playout duration has elapsed");

  /* Well past the full duration: still zero, not negative/wrapped (the
   * function takes unsigned frame counts, so this also guards against an
   * underflow regression). */
  uint64_t occ_past =
      playback_occupancy_frames(frames_written, 500000, sample_rate);
  OTCHECK(occ_past == 0, "occupancy stays at zero long after playout finishes");

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, occupancy_time_failures,
      "playback occupancy time-based scenario had CHECK() failures, see stdout above");
}

/*
 * Edge cases for playback_occupancy_frames(): invalid sample rate, no
 * time elapsed / negative elapsed (clock not yet advanced this tick),
 * and nothing written yet -- all must return a safe, non-blocking value
 * (0) rather than something that could wedge hw_audio_playback_drained()
 * into permanently reporting "not drained".
 */
static int occupancy_edge_failures = 0;

#define OECHECK(cond, msg)                                             \
  do {                                                                 \
    if (!(cond)) {                                                     \
      printf("FAIL: %s (line %d)\n", msg, __LINE__);                   \
      occupancy_edge_failures++;                                       \
    }                                                                  \
  } while (0)

static void test_playback_occupancy_edge_cases(void) {
  OECHECK(playback_occupancy_frames(1000, 1000000, 0) == 0,
          "sample_rate == 0 returns 0, not a div-by-zero/garbage value");
  OECHECK(playback_occupancy_frames(1000, 1000000, -1) == 0,
          "negative sample_rate returns 0");
  OECHECK(playback_occupancy_frames(0, 0, 16000) == 0,
          "nothing written yet -> zero occupancy");
  OECHECK(playback_occupancy_frames(500, -1000, 16000) == 500,
          "negative elapsed_us (clock hasn't advanced) is clamped to zero elapsed, "
          "not treated as future time / negative occupancy");

  TEST_ASSERT_EQUAL_INT_MESSAGE(
      0, occupancy_edge_failures,
      "playback occupancy edge-case scenario had CHECK() failures, see stdout above");
}

typedef struct { int begins, finishes, polls, aborted; } transport_probe_t;
static voice_transport_result_t probe_begin(voice_transport_t *t) { ((transport_probe_t *)t->ctx)->begins++; return VOICE_TRANSPORT_OK; }
static voice_transport_write_result_t probe_write(voice_transport_t *t, const uint8_t *d, size_t n) { (void)d; return (voice_transport_write_result_t){n, VOICE_TRANSPORT_OK}; }
static voice_transport_result_t probe_finish(voice_transport_t *t) { ((transport_probe_t *)t->ctx)->finishes++; return VOICE_TRANSPORT_OK; }
static voice_transport_result_t probe_poll(voice_transport_t *t, uint8_t *d, size_t n, size_t *got) { (void)d; (void)n; ((transport_probe_t *)t->ctx)->polls++; *got = 0; return VOICE_TRANSPORT_EOF; }
static void probe_abort(voice_transport_t *t) { ((transport_probe_t *)t->ctx)->aborted++; }

static void test_transport_finish_keeps_response_socket(void) {
  transport_probe_t probe = {0};
  const voice_transport_ops_t ops = {probe_begin, probe_write, probe_finish, probe_poll, probe_abort};
  voice_transport_t t = {.ops = &ops, .ctx = &probe};
  uint8_t pcm = 0, response = 0; size_t got = 0;
  TEST_ASSERT_EQUAL(VOICE_TRANSPORT_OK, voice_transport_begin(&t));
  TEST_ASSERT_EQUAL_UINT(1, voice_transport_write(&t, &pcm, 1).accepted_bytes);
  TEST_ASSERT_EQUAL(VOICE_TRANSPORT_OK, voice_transport_finish(&t));
  TEST_ASSERT_EQUAL(VOICE_TRANSPORT_EOF, voice_transport_poll(&t, &response, 1, &got));
  TEST_ASSERT_EQUAL_INT(1, probe.begins);
  TEST_ASSERT_EQUAL_INT(1, probe.finishes);
  TEST_ASSERT_EQUAL_INT(1, probe.polls);
  TEST_ASSERT_EQUAL_INT(0, probe.aborted);
}
