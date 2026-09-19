/*
 * Voice Terminal — main application state machine (Milestone 1 loopback).
 *
 * States: BOOT -> IDLE -> RECORDING -> PROCESSING -> PLAYING -> IDLE,
 * with any state able to drop to ERROR and recover back to IDLE.
 *
 * This revision (M1-03/M1-04 integration) wires the previously stubbed
 * audio capture and playback actions to the real M1-03/M1-04 modules:
 *
 *   Capture (M1-03) : audio_capture (include/audio_capture.h) via the hw
 *                     seam (hw_audio_capture_start/stop/read). RECORDING
 *                     pulls freshly captured PCM once per tick and fans it
 *                     out to both the network-drain buffer (spec section
 *                     10/11, M2 upload — unchanged from the previous
 *                     revision) and a second buffer that holds the audio
 *                     for local playback, since Milestone 1 has no backend
 *                     to fetch a reply from (see "Loopback" below).
 *
 *   Playback (M1-04): audio_playback (include/audio_playback.h) via the hw
 *                     seam (hw_audio_playback_start/stop/write/drained).
 *                     PLAYING drains the loopback buffer to the speaker
 *                     once per tick and waits for the hardware to confirm
 *                     it has actually finished playing (not just queued)
 *                     before returning to IDLE.
 *
 *   Button read   : button_driver (include/button_driver.h), unchanged.
 *   LED control   : led_ui (include/led_ui.h) + led_config.h, unchanged.
 *
 *   Spec mapping (Milestone 1):
 *     RECORDING   -> red, blinking   (led_state RECORDING)
 *     PLAYBACK    -> green, blinking (led_state PLAYBACK)
 *     IDLE        -> green, steady
 *     PROCESSING  -> cyan, blinking
 *     ERROR       -> red, fast blink (spec section 9: мигающий красный)
 *
 * Loopback (Milestone 1 hardware smoke test): there is no backend turn to
 * fetch a reply from yet (networking is Milestone 2, out of scope here),
 * so M1's "reply" is simply the audio that was just recorded, played back
 * through the speaker. STATE_PROCESSING is therefore a single, immediate
 * tick that hands off to STATE_PLAYING — the real "wait for the backend"
 * logic lands with the M2 network wiring; the state exists now so the
 * transition table doesn't need to change again then.
 *
 * Buffering note: ATOM Echo has no PSRAM, so neither buffer below may hold
 * more than a short recording (RING_BUFFER_CAPACITY_DEFAULT each, spec
 * section 10). The M1 loopback scenario (a short spoken phrase) fits well
 * within that; a long recording will legitimately hit ring_buffer overflow
 * on one or both buffers exactly like the M2 upload path already handles.
 *
 * Non-goals in this revision: networking (Milestone 2, http_session stays
 * a pure state-tracking seam with no real transport), GPIO/LED module
 * changes, and any refactor of the existing modules — this task only
 * wires main.c to the audio hw seam that board_atom_echo.c already backs
 * with the real M1-03/M1-04 modules.
 *
 * Recording path (spec sections 7 + 10):
 *   - Capture pushes into app.rb (ring_buffer, 32 KiB); the writer drains it
 *     into the HTTP session once per tick (M2 upload path, kept as-is).
 *   - Capture also pushes the same bytes into app.playback_rb, the M1
 *     loopback buffer M1-04 plays back once the turn finishes.
 *   - Sticky overflow flag (on app.rb) => stop recording immediately, stop
 *     the capture hardware, close the HTTP session gracefully when
 *     possible (abort fallback), show ERROR (red fast blink), recover to
 *     IDLE after APP_ERROR_RECOVER_TICKS — no reboot.
 *   - MAX_RECORD_SECONDS (configurable, spec section 7): button_driver fires
 *     MAX_RECORD_TIMEOUT at the limit, which the RECORDING tick treats like a
 *     release, so the recording auto-finishes and the session closes.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "button_driver.h"
#include "hardware.h"
#include "http_session.h"
#include "led_ui.h"
#include "ring_buffer.h"
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
  /* Recording path (spec section 10): network-drain buffer (M2 upload). */
  uint8_t rb_storage[RING_BUFFER_CAPACITY_DEFAULT];
  ring_buffer_t rb;
  http_session_t session;
  /* M1 loopback: holds what was just captured until M1-04 plays it back
   * (see file header "Loopback"). Filled in parallel with app.rb during
   * RECORDING, drained to the speaker during PLAYING. */
  uint8_t playback_storage[RING_BUFFER_CAPACITY_DEFAULT];
  ring_buffer_t playback_rb;
  /* One chunk the speaker hasn't accepted yet (hw_audio_playback_write is
   * all-or-nothing, see playback_drain()); kept here instead of losing or
   * reordering audio when the sink is momentarily full. */
  uint8_t playback_pending[256];
  size_t playback_pending_len;
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

/*
 * Start of a recording session: clean both buffers, fresh HTTP session,
 * start the microphone (M1-03). The buffer reset also clears the sticky
 * overflow flag, so a previous overflow can never leak into a new
 * recording.
 */
static void recording_start(void) {
  ring_buffer_reset(&app.rb);
  ring_buffer_reset(&app.playback_rb);
  http_session_init(&app.session);
  (void)http_session_open(&app.session);
  hw_audio_capture_start();
}

/*
 * Spec section 10 step 2: close the HTTP session gracefully when the
 * connection still allows it; fall back to abort when a graceful close
 * cannot complete (network already dead).
 */
static void session_close_or_abort(void) {
  if (http_session_is_active(&app.session)) {
    if (!http_session_close(&app.session)) {
      http_session_abort(&app.session);
    }
  }
}

/*
 * Capture side of the audio path (M1-03). Pulls whatever PCM the
 * microphone has ready this tick and fans it out to both the
 * network-drain buffer (app.rb, M2 upload — unchanged) and the loopback
 * buffer (app.playback_rb) that M1-04 plays back once the turn finishes.
 * "One tick = everything ready" mirrors recording_drain()'s shape below.
 */
static void recording_capture(void) {
  uint8_t chunk[256];
  size_t n;
  while ((n = hw_audio_capture_read(chunk, sizeof(chunk))) > 0) {
    ring_buffer_push(&app.rb, chunk, n);
    ring_buffer_push(&app.playback_rb, chunk, n);
  }
}

/*
 * Writer side of the audio path: drain the ring buffer into the HTTP
 * session. "One tick = everything that fits" models the network rate;
 * the real writer paces itself with the transport. Stops as soon as the
 * overflow flag is up so no data is handed over after the failure.
 */
static void recording_drain(void) {
  uint8_t chunk[256];
  while (!ring_buffer_overflow(&app.rb) && !ring_buffer_empty(&app.rb)) {
    size_t n = ring_buffer_pop(&app.rb, chunk, sizeof(chunk));
    if (n == 0) {
      break;
    }
    (void)http_session_write(&app.session, chunk, n);
  }
}

/*
 * Spec section 10 reaction, called when the sticky overflow flag is set:
 *   1. recording stops immediately (capture task sees the flag + this
 *      transition; the drain loop stops writing),
 *   2. microphone stopped (M1-03),
 *   3. HTTP session closed gracefully if possible,
 *   4. ERROR shown,
 *   5. recovery back to IDLE happens in the ERROR state (no reboot).
 */
static void on_ring_buffer_overflow(void) {
  hw_audio_capture_stop();
  session_close_or_abort();
  enter_state(STATE_ERROR, "ring buffer overflow");
}

/*
 * Start of local playback (M1-04): prime the speaker so playback_drain()
 * can begin writing to it next tick.
 */
static void playback_start(void) {
  app.playback_pending_len = 0;
  hw_audio_playback_start();
}

/*
 * Drain side of the audio path (M1-04): hand the loopback buffer to the
 * speaker. hw_audio_playback_write() is all-or-nothing (see
 * board_atom_echo.c) — a chunk it doesn't accept in full is kept in
 * app.playback_pending and retried next tick instead of being lost or, if
 * we instead popped the next chunk in its place, played back out of
 * order.
 */
static void playback_drain(void) {
  if (app.playback_pending_len > 0) {
    size_t written =
        hw_audio_playback_write(app.playback_pending, app.playback_pending_len);
    if (written < app.playback_pending_len) {
      return; /* sink still full; try the same chunk again next tick */
    }
    app.playback_pending_len = 0;
  }
  while (!ring_buffer_empty(&app.playback_rb)) {
    size_t n = ring_buffer_pop(&app.playback_rb, app.playback_pending,
                                sizeof(app.playback_pending));
    if (n == 0) {
      break;
    }
    size_t written = hw_audio_playback_write(app.playback_pending, n);
    if (written < n) {
      app.playback_pending_len = n; /* retry this whole chunk next tick */
      break;
    }
  }
}

/* True once every captured byte has been handed to the speaker AND the
 * hardware confirms it actually finished playing them (not just queued). */
static bool playback_finished(void) {
  return ring_buffer_empty(&app.playback_rb) && app.playback_pending_len == 0 &&
         hw_audio_playback_drained();
}

void app_init(void) {
  state_machine_init(&app.sm);
  /* MAX_RECORD_SECONDS is the configurable cap from spec section 7;
   * button_driver auto-fires MAX_RECORD_TIMEOUT, which the RECORDING tick
   * treats like a release, so a held button never records past the limit. */
  button_init(&app.btn, BUTTON_DEBOUNCE_MS_DEFAULT, MAX_RECORD_SECONDS_DEFAULT);
  led_init();
  app.boot_ticks = 0;
  app.error_ticks = 0;
  ring_buffer_init(&app.rb, app.rb_storage, RING_BUFFER_CAPACITY_DEFAULT);
  ring_buffer_init(&app.playback_rb, app.playback_storage,
                    RING_BUFFER_CAPACITY_DEFAULT);
  app.playback_pending_len = 0;
  http_session_init(&app.session);
}

/* Current app state (test hook). */
state_t app_state(void) {
  return state_machine_get_state(&app.sm);
}

/* Current HTTP session state (test hook). */
http_session_state_t app_session_state(void) {
  return http_session_state(&app.session);
}

/* True if the last session ended via abort() (test hook). */
bool app_session_aborted(void) {
  return app.session.aborted;
}

/*
 * Test hook: simulate the I2S capture task overflowing the ring buffer —
 * pushes through the public API until the sticky flag is raised, exactly
 * like a real capture task that outran the network would do.
 */
void app_test_simulate_ring_overflow(void) {
  static const uint8_t filler[RING_BUFFER_CAPACITY_DEFAULT] = {0};
  while (!ring_buffer_overflow(&app.rb)) {
    if (ring_buffer_push(&app.rb, filler, sizeof(filler)) == 0) {
      break;
    }
  }
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
        recording_start();
        enter_state(STATE_RECORDING, NULL);
      }
      break;
    }

    case STATE_RECORDING: {
      /*
       * Spec section 10: the capture task pushes into app.rb at the
       * production rate; if the network falls behind, the sticky overflow
       * flag goes up and we must stop recording IMMEDIATELY — not keep
       * silently dropping data.
       */
      if (ring_buffer_overflow(&app.rb)) {
        on_ring_buffer_overflow();
        break;
      }
      recording_capture();
      recording_drain();
      button_event_t ev = button_poll(&app.btn, hw_button_raw(), now);
      if (ev == BUTTON_EVENT_RELEASED ||
          ev == BUTTON_EVENT_MAX_RECORD_TIMEOUT) {
        /*
         * Normal finish (release, or the MAX_RECORD_SECONDS auto-finish
         * from spec section 7: the button driver fires
         * MAX_RECORD_TIMEOUT at the configurable limit). Stop the
         * microphone and send the terminal body, closing the session
         * gracefully.
         */
        hw_audio_capture_stop();
        session_close_or_abort();
        enter_state(STATE_PROCESSING, NULL);
      }
      break;
    }

    case STATE_PROCESSING:
      /*
       * M1 loopback (see file header): there is no backend turn to wait
       * on yet, so the audio that was just recorded IS the reply. This
       * step is a single, immediate tick — the real "wait for the
       * backend" condition lands with the M2 network wiring.
       */
      playback_start();
      enter_state(STATE_PLAYING, NULL);
      break;

    case STATE_PLAYING:
      /* M1-04: drain the loopback buffer to the speaker; wait for the
       * hardware to confirm playback actually finished before IDLE. */
      playback_drain();
      if (playback_finished()) {
        hw_audio_playback_stop();
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
