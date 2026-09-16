/*
 * Voice Terminal — main application state machine (Milestone 1 loopback).
 *
 * States: BOOT -> IDLE -> RECORDING -> PROCESSING -> PLAYING -> IDLE,
 * with any state able to drop to ERROR and recover back to IDLE.
 *
 * This revision (M1-05 integration) wires the previously stubbed hardware
 * actions to the real M1-05 modules:
 *
 *   Button read   : button_driver (include/button_driver.h)
 *                   The raw pin is sampled once per tick through the hw seam
 *                   (hw_button_raw) and fed to button_poll(), which performs
 *                   debouncing and derives PRESSED / RELEASED /
 *                   MAX_RECORD_TIMEOUT events. main.c never touches GPIO.
 *
 *   LED control   : led_ui (include/led_ui.h) + led_config.h
 *                   Each state entry sets the LED device state via
 *                   led_set_state(); led_update(now) advances the blink
 *                   pattern once per tick and drives hw_led_write().
 *
 *   Spec mapping (Milestone 1):
 *     RECORDING   -> red, blinking   (led_state RECORDING)
 *     PLAYBACK    -> green, blinking (led_state PLAYBACK)
 *     IDLE        -> green, steady
 *     PROCESSING  -> cyan, blinking
 *     ERROR       -> orange, fast blink
 *
 * Non-goals in this revision: audio capture/playback (M1-03/M1-04, marked
 * TODO) and networking (Milestone 2).
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "button_driver.h"
#include "hardware.h"
#include "led_ui.h"
#include "state_machine.h"

/* Ticks the app runs in ERROR before recovering to IDLE. */
#define APP_ERROR_RECOVER_TICKS 10u
/* Ticks the app waits in BOOT before entering IDLE. */
#define APP_BOOT_TICKS 5u

typedef struct {
  state_machine_t sm;
  button_driver_t btn;
  uint32_t boot_ticks;
  uint32_t error_ticks;
  /* Playback completion arrives from M1-04 (playback module) in a later
   * revision; until then the loopback harness sets this flag. */
  bool playback_done;
} app_t;

static app_t app;

/* Map app state -> LED device state. */
static led_state_t led_state_for(state_t s) {
  switch (s) {
    case STATE_IDLE:
      return LED_STATE_IDLE;
    case STATE_RECORDING:
      return LED_STATE_RECORDING;
    case STATE_PROCESSING:
      return LED_STATE_PROCESSING;
    case STATE_PLAYING:
      return LED_STATE_PLAYBACK;
    case STATE_ERROR:
      return LED_STATE_ERROR;
    case STATE_BOOT:
    default:
      return LED_STATE_IDLE;
  }
}

static void enter_state(state_t next, const char* error_what) {
  state_t prev = state_machine_get_state(&app.sm);
  if (!state_machine_step(&app.sm, next)) {
    return; /* illegal transition: leave state, report if we can */
  }
  if (next == STATE_ERROR) {
    app.error_ticks = 0;
    if (error_what) {
      hw_report_error(error_what);
    }
  }
  if (next == STATE_IDLE) {
    button_reset(&app.btn); /* don't let a stale hold re-trigger */
  }
  led_set_state(led_state_for(next));
  (void)prev;
}

void app_init(void) {
  state_machine_init(&app.sm);
  button_init(&app.btn, BUTTON_DEBOUNCE_MS_DEFAULT, MAX_RECORD_SECONDS_DEFAULT);
  led_init();
  app.boot_ticks = 0;
  app.error_ticks = 0;
  app.playback_done = false;
}

/* Test hook: harness drives playback completion from here. */
void app_test_set_playback_done(bool done) {
  app.playback_done = done;
}

/*
 * One application tick. Non-blocking: each per-state action performs at most
 * one hardware poll and returns.
 */
void app_tick(void) {
  uint32_t now = hw_clock_ms();

  /* LED blink phase advances in every state. */
  led_update(now);

  state_t s = state_machine_get_state(&app.sm);

  switch (s) {
    case STATE_BOOT:
      if (++app.boot_ticks >= APP_BOOT_TICKS) {
        enter_state(STATE_IDLE, NULL);
      }
      break;

    case STATE_IDLE: {
      button_event_t ev = button_poll(&app.btn, hw_button_raw(), now);
      if (ev == BUTTON_EVENT_PRESSED) {
        enter_state(STATE_RECORDING, NULL);
      }
      break;
    }

    case STATE_RECORDING: {
      button_event_t ev = button_poll(&app.btn, hw_button_raw(), now);
      if (ev == BUTTON_EVENT_RELEASED ||
          ev == BUTTON_EVENT_MAX_RECORD_TIMEOUT) {
        /*
         * TODO(M1-03): hand the captured buffer to the record module;
         * loopback test skips capture for now.
         */
        enter_state(STATE_PROCESSING, NULL);
      }
      break;
    }

    case STATE_PROCESSING:
      /*
       * TODO(M1-03/M1-04): real turn processing + reply fetch lands with the
       * record/playback integration task. Loopback test: advance as soon as
       * the harness marks playback ready/done.
       */
      if (app.playback_done) {
        app.playback_done = false;
        enter_state(STATE_PLAYING, NULL);
      }
      break;

    case STATE_PLAYING:
      /*
       * TODO(M1-04): query the playback module for completion; the loopback
       * harness raises playback_done instead.
       */
      if (app.playback_done) {
        app.playback_done = false;
        enter_state(STATE_IDLE, NULL);
      }
      break;

    case STATE_ERROR:
      if (++app.error_ticks >= APP_ERROR_RECOVER_TICKS) {
        enter_state(STATE_IDLE, NULL);
      }
      break;

    default:
      break;
  }
}

#ifdef VOICE_WITH_MAIN
int main(void) {
  app_init();
  for (;;) {
    app_tick();
    /* On-target: vTaskDelay / idle wait here. */
  }
  return 0;
}
#endif
