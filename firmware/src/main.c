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
 *   State display : board_sticks3_display_update() renders the LCD.
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
 * a pure state-tracking seam with no real transport), GPIO module
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
 *     possible (abort fallback), show ERROR on the LCD, recover to
 *     IDLE only after a fresh button tap — no reboot.
 *   - MAX_RECORD_SECONDS (configurable, spec section 7): button_driver fires
 *     MAX_RECORD_TIMEOUT at the limit, which the RECORDING tick treats like a
 *     release, so the recording auto-finishes and the session closes.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#ifdef ESP_PLATFORM
#include "esp_mac.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"
#include "esp_random.h"
#include "esp_timer.h"
#endif

#include "button_driver.h"
#include "hardware.h"
#include "http_session.h"
#include "http_voice_client.h"
#include "board_sticks3.h"
#include "voice_config_httpd.h"
#include "voice_settings.h"
#include "voice_turn_http.h"
#include "voice_turn_client.h"
#include "wav_parser.h"

#ifndef VOICE_WIFI_SSID
#define VOICE_WIFI_SSID ""
#endif
#ifndef VOICE_WIFI_PASSWORD
#define VOICE_WIFI_PASSWORD ""
#endif
#ifndef VOICE_GATEWAY_URL
#define VOICE_GATEWAY_URL "http://192.168.1.10:8000"
#endif
#ifndef VOICE_DEVICE_TOKEN
#define VOICE_DEVICE_TOKEN NULL
#endif
#include "ring_buffer.h"
#include "state_machine.h"

/* Ticks the app waits in BOOT before entering IDLE. */
#define APP_BOOT_TICKS 5u

typedef struct {
  state_machine_t sm;
  button_driver_t btn;
  uint32_t boot_ticks;
  bool error_ack_ready;
  bool error_ack_pressed;
  bool ignore_button_until_release;
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
  /*
   * Playback-side error flags (spec sections 8/13/38): sticky, set by the
   * test-simulation hooks below exactly like app.rb's ring-buffer-overflow
   * flag models a RECORDING-side failure. Real backend wiring lands with
   * M2 (see http_session.c file header); until then these flags are how
   * "backend connection dropped mid-stream" and "malformed WAV header on
   * the reply" are driven deterministically for host tests and, later, by
   * the real M2 download/parsing paths.
   */
  bool playback_conn_dropped;
  bool playback_bad_wav_header;
  /*
   * Playback position counter (spec section 8): total bytes handed to the
   * speaker so far this playback session. Reset to 0 by playback_start()
   * and, deterministically, by playback_release_resources() on every
   * PLAYING -> IDLE/ERROR exit, so a stale count from a finished turn can
   * never leak into the next one.
   */
  size_t playback_position;
  wav_parser_t wav;
#ifdef ESP_PLATFORM
  StreamBufferHandle_t turn_audio_stream;
  StreamBufferHandle_t turn_upload_stream;
  voice_turn_http_t turn_http;
  voice_turn_client_t turn_client;
  volatile voice_turn_result_t turn_result;
  volatile bool turn_task_active;
  volatile bool turn_cancel_requested;
  volatile bool turn_audio_started;
  volatile bool turn_upload_active;
  volatile bool turn_upload_finished;
  volatile bool turn_upload_release_requested;
  volatile bool turn_upload_failed;
  bool turn_http_ready;
  volatile bool turn_storage_failed;
  bool turn_pending_byte;
  uint8_t turn_pending_value;
#endif
} app_t;

static app_t app;
static http_voice_client_t voice_client;
static voice_settings_t voice_settings;

#ifdef ESP_PLATFORM
static voice_turn_io_result_t turn_lookup(void *ctx, const char *request_id,
                                           voice_turn_response_t *response) {
  app_t *a = ctx;
  return voice_turn_http_ops()->lookup_request(&a->turn_http, request_id, response);
}
static voice_turn_io_result_t turn_poll(void *ctx, const char *turn_id,
                                         voice_turn_response_t *response) {
  app_t *a = ctx;
  return voice_turn_http_ops()->poll_turn(&a->turn_http, turn_id, response);
}
static voice_turn_io_result_t turn_download(void *ctx, const char *turn_id,
                                             voice_turn_response_t *response) {
  app_t *a = ctx;
  return voice_turn_http_ops()->download_audio(&a->turn_http, turn_id, response);
}
static voice_turn_io_result_t turn_cancel(void *ctx, const char *turn_id) {
  app_t *a = ctx;
  return voice_turn_http_ops()->cancel_turn(&a->turn_http, turn_id);
}
static bool turn_save_id(void *ctx, const char *turn_id) {
  app_t *a = ctx;
  if (strlcpy(voice_settings.turn_id, turn_id,
              sizeof(voice_settings.turn_id)) >= sizeof(voice_settings.turn_id))
    return false;
  if (voice_settings_save(&voice_settings) != ESP_OK) {
    a->turn_storage_failed = true;
    return false;
  }
  return true;
}
static void turn_clear_ids(void *ctx) {
  app_t *a = ctx;
  voice_settings.request_id[0] = '\0';
  voice_settings.turn_id[0] = '\0';
  if (voice_settings_save(&voice_settings) != ESP_OK) a->turn_storage_failed = true;
}
static const voice_turn_ops_t TURN_OPS = {
  .lookup_request = turn_lookup,
  .poll_turn = turn_poll,
  .download_audio = turn_download,
  .cancel_turn = turn_cancel,
  .save_turn_id = turn_save_id,
  .clear_ids = turn_clear_ids,
};

static void upload_recording(app_t *a) {
  uint8_t chunk[1024];
  http_session_init(&a->session);
  bool open = http_session_open(&a->session);
  if (!open) a->turn_upload_failed = true;
  while (open) {
    if (a->turn_upload_failed) break;
    size_t count = xStreamBufferReceive(a->turn_upload_stream, chunk,
                                        sizeof(chunk), pdMS_TO_TICKS(100));
    if (count) {
      if (http_session_write(&a->session, chunk, count) != count) {
        a->turn_upload_failed = true;
        break;
      }
    }
    if (a->turn_upload_finished &&
        xStreamBufferBytesAvailable(a->turn_upload_stream) == 0) break;
  }
  if (open && !a->turn_upload_failed) {
    if (!http_session_close(&a->session)) {
      http_session_abort(&a->session);
      a->turn_upload_failed = true;
    } else if (http_voice_client_status(&voice_client) == 202) {
      const char *turn_id = http_voice_client_turn_id(&voice_client);
      if (turn_id && turn_id[0] && !turn_save_id(a, turn_id))
        a->turn_storage_failed = true;
    }
  } else if (open) {
    http_session_abort(&a->session);
  }
  a->turn_upload_active = false;
}

static void turn_worker(void *arg) {
  app_t *a = arg;
  if (a->turn_upload_active) {
    upload_recording(a);
    if (a->turn_upload_failed) {
      a->turn_result = VOICE_TURN_FAILED;
      a->turn_task_active = false;
      vTaskDelete(NULL);
      return;
    }
  }
  voice_turn_client_init(&a->turn_client, &TURN_OPS, a,
      voice_settings.request_id,
      voice_settings.turn_id[0] ? voice_settings.turn_id : NULL,
      (uint64_t)(esp_timer_get_time() / 1000ULL));
  if (a->turn_client.result == VOICE_TURN_FAILED) {
    a->turn_result = VOICE_TURN_FAILED;
    a->turn_task_active = false;
    vTaskDelete(NULL);
    return;
  }
  for (;;) {
    if (a->turn_cancel_requested && a->turn_client.turn_id[0]) {
      uint64_t now = (uint64_t)(esp_timer_get_time() / 1000ULL);
      a->turn_result = voice_turn_client_cancel(&a->turn_client, now);
      if (a->turn_result != VOICE_TURN_WAITING) break;
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    }
    uint64_t now = (uint64_t)(esp_timer_get_time() / 1000ULL);
    a->turn_result = voice_turn_client_tick(&a->turn_client, now);
    if (a->turn_result != VOICE_TURN_WAITING) break;
    vTaskDelay(pdMS_TO_TICKS(50));
  }
  a->turn_task_active = false;
  vTaskDelete(NULL);
}

static bool start_turn_worker(bool upload_pending) {
  if (!app.turn_http_ready || !app.turn_audio_stream || !app.turn_upload_stream ||
      !voice_settings.request_id[0]) return false;
  if (app.turn_task_active) return true;
  xStreamBufferReset(app.turn_audio_stream);
  xStreamBufferReset(app.turn_upload_stream);
  app.turn_cancel_requested = false;
  app.turn_audio_started = false;
  app.turn_pending_byte = false;
  app.turn_storage_failed = false;
  app.turn_upload_failed = false;
  app.turn_upload_finished = !upload_pending;
  app.turn_upload_release_requested = false;
  app.turn_upload_active = upload_pending;
  app.turn_result = VOICE_TURN_WAITING;
  app.turn_task_active = true;
  if (xTaskCreate(turn_worker, "voice_turn", 8192, &app, 5, NULL) != pdPASS) {
    app.turn_task_active = false;
    return false;
  }
  return true;
}

static bool begin_turn_request(void) {
  uint8_t random_bytes[16];
  char request_id[VOICE_TURN_REQUEST_ID_CAPACITY];
  esp_fill_random(random_bytes, sizeof(random_bytes));
  if (!voice_turn_format_uuid(random_bytes, request_id, sizeof(request_id))) return false;
  strlcpy(voice_settings.request_id, request_id, sizeof(voice_settings.request_id));
  voice_settings.turn_id[0] = '\0';
  return voice_settings_save(&voice_settings) == ESP_OK;
}
#endif

static void enter_state(state_t next, const char* error_what) {
  if (!state_machine_step(&app.sm, next)) {
    return; /* illegal transition: leave state, report if we can */
  }
  if (next == STATE_ERROR) {
    app.error_ack_ready = false;
    app.error_ack_pressed = false;
    if (error_what) {
      hw_report_error(error_what);
    }
  }
  if (next == STATE_IDLE) {
    button_reset(&app.btn); /* don't let a stale hold re-trigger */
  }
}

/*
 * Start of a recording session: clean both buffers, fresh HTTP session,
 * start the microphone (M1-03). The buffer reset also clears the sticky
 * overflow flag, so a previous overflow can never leak into a new
 * recording.
 */
static bool recording_start(void) {
#ifdef ESP_PLATFORM
  if (!voice_wireguard_ready() || !app.turn_http_ready) return false;
  if (app.turn_task_active) return false;
  if (!begin_turn_request()) return false;
#endif
  ring_buffer_reset(&app.rb);
  ring_buffer_reset(&app.playback_rb);
#ifdef ESP_PLATFORM
  {
    if (!start_turn_worker(true)) return false;
    hw_audio_capture_start();
    return true;
  }
#else
  http_session_init(&app.session);
  if (!http_session_open(&app.session)) return false;
  hw_audio_capture_start();
  return true;
#endif
}

/*
 * Spec section 10 step 2: close the HTTP session gracefully when the
 * connection still allows it; fall back to abort when a graceful close
 * cannot complete (network already dead).
 */
#ifndef ESP_PLATFORM
static void session_close_or_abort(void) {
  if (http_session_is_active(&app.session)) {
    if (!http_session_close(&app.session)) {
      http_session_abort(&app.session);
    }
  }
}

#endif

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
#ifndef ESP_PLATFORM
    ring_buffer_push(&app.playback_rb, chunk, n);
#endif
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
    size_t limit = sizeof(chunk);
#ifdef ESP_PLATFORM
    {
      size_t available = xStreamBufferSpacesAvailable(app.turn_upload_stream);
      if (!available) return;
      if (limit > available) limit = available;
    }
#endif
    size_t n = ring_buffer_pop(&app.rb, chunk, limit);
    if (n == 0) {
      break;
    }
#ifdef ESP_PLATFORM
    {
      if (xStreamBufferSend(app.turn_upload_stream, chunk, n, 0) != n) {
        app.turn_upload_failed = true;
        return;
      }
    }
#else
    (void)http_session_write(&app.session, chunk, n);
#endif
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
#ifdef ESP_PLATFORM
  {
    app.turn_upload_failed = true;
    app.turn_upload_finished = true;
    app.turn_cancel_requested = true;
  }
#else
  session_close_or_abort();
#endif
  enter_state(STATE_ERROR, "ring buffer overflow");
}

/*
 * Spec section 8: release every playback resource, deterministically and
 * idempotently, as part of entering IDLE (EOF path) or ERROR (playback
 * error path) out of PLAYING. Called exactly once per PLAYING exit by
 * both on_playback_error() and the EOF handler in app_tick()'s
 * STATE_PLAYING case, so the two paths can never diverge in what they
 * clean up:
 *   - stop the DMA/I2S peripheral (hw_audio_playback_stop()) so no more
 *     bytes reach the speaker regardless of what was still queued,
 *   - free/zero the loopback ring buffer (ring_buffer_reset(), which also
 *     clears its internal head/tail/count -- no stale audio or dangling
 *     read/write position survives into the next turn),
 *   - drop the one pending unwritten chunk (app.playback_pending_len = 0;
 *     the bytes themselves are stale scratch space, not a resource that
 *     needs freeing, but the length must be zeroed so a later playback
 *     session never mistakes them for real pending data),
 *   - clear the playback position counter (app.playback_position = 0).
 * Safe to call from a state that has already released these resources
 * (e.g. a spurious repeated call): every step here is itself idempotent
 * (hw_audio_playback_stop() / ring_buffer_reset() on an already-stopped/
 * already-empty target is a no-op), so calling this twice in a row is
 * harmless -- the idempotency guarantee in the app_tick() EOF check below
 * only needs to ensure it isn't reached at all while already IDLE.
 */
static void playback_release_resources(void) {
  hw_audio_playback_stop();
  ring_buffer_reset(&app.playback_rb);
#ifdef ESP_PLATFORM
  app.turn_pending_byte = false;
#endif
  app.playback_pending_len = 0;
  app.playback_position = 0;
}

/*
 * Spec sections 8/13/38 reaction, called when a playback-side error flag is
 * set (backend connection dropped mid-stream, or the reply's WAV header is
 * malformed):
 *   1. playback stops immediately (no more bytes handed to the speaker),
 *   2. the speaker hardware is stopped (mirrors on_ring_buffer_overflow's
 *      hw_audio_capture_stop() on the RECORDING side),
 *   3. the loopback buffer is drained/reset so no stale audio survives into
 *      the next turn,
 *   4. ERROR is shown,
 *   5. recovery back to IDLE happens in the ERROR state (no reboot),
 *      exactly like the ring-buffer-overflow path already does.
 */
static void on_playback_error(const char* what) {
#ifdef ESP_PLATFORM
  app.turn_cancel_requested = true;
#endif
  playback_release_resources();
  enter_state(STATE_ERROR, what);
}

/*
 * Start of local playback (M1-04): prime the speaker so playback_drain()
 * can begin writing to it next tick.
 */
#ifndef ESP_PLATFORM
static void playback_start(void) {
  app.playback_pending_len = 0;
  app.playback_position = 0;
  app.playback_conn_dropped = false;
  app.playback_bad_wav_header = false;
  hw_audio_playback_start(NULL, 0);
}
#endif

/*
 * Drain side of the audio path (M1-04): hand the loopback buffer to the
 * speaker. hw_audio_playback_write() is all-or-nothing (see
 * board_atom_echo.c) — a chunk it doesn't accept in full is kept in
 * app.playback_pending and retried next tick instead of being lost or, if
 * we instead popped the next chunk in its place, played back out of
 * order.
 */
#ifndef ESP_PLATFORM
static void playback_drain(void) {
  if (app.playback_pending_len > 0) {
    size_t written =
        hw_audio_playback_write(app.playback_pending, app.playback_pending_len);
    if (written < app.playback_pending_len) {
      return; /* sink still full; try the same chunk again next tick */
    }
    app.playback_position += written;
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
    app.playback_position += written;
  }
}

#endif

#ifdef ESP_PLATFORM
static bool turn_playback_drain(void) {
  uint8_t audio[1025];
  size_t prefix = 0;
  if (app.turn_pending_byte) {
    audio[0] = app.turn_pending_value;
    prefix = 1;
    app.turn_pending_byte = false;
  }
  size_t received = xStreamBufferReceive(app.turn_audio_stream,
      audio + prefix, sizeof(audio) - prefix, 0);
  size_t total = prefix + received;
  if (total & 1u) {
    app.turn_pending_byte = true;
    app.turn_pending_value = audio[total - 1];
    total--;
  }
  if (!total) return true;
  size_t written = hw_audio_playback_write(audio, total);
  if (written != total) return false;
  app.playback_position += written;
  return true;
}
#endif

/* True once every captured byte has been handed to the speaker AND the
 * hardware confirms it actually finished playing them (not just queued). */
#ifndef ESP_PLATFORM
static bool playback_finished(void) {
  return ring_buffer_empty(&app.playback_rb) && app.playback_pending_len == 0 &&
         hw_audio_playback_drained()
      ;
}

#endif

void app_init(void) {
  state_machine_init(&app.sm);
  /* MAX_RECORD_SECONDS is the configurable cap from spec section 7;
   * button_driver auto-fires MAX_RECORD_TIMEOUT, which the RECORDING tick
   * treats like a release, so a held button never records past the limit. */
  button_init(&app.btn, BUTTON_DEBOUNCE_MS_DEFAULT, MAX_RECORD_SECONDS_DEFAULT);
  app.boot_ticks = 0;
  app.error_ack_ready = false;
  app.error_ack_pressed = false;
  app.ignore_button_until_release = false;
  ring_buffer_init(&app.rb, app.rb_storage, RING_BUFFER_CAPACITY_DEFAULT);
  ring_buffer_init(&app.playback_rb, app.playback_storage,
                    RING_BUFFER_CAPACITY_DEFAULT);
  app.playback_pending_len = 0;
  app.playback_position = 0;
  app.playback_conn_dropped = false;
  app.playback_bad_wav_header = false;
  http_session_init(&app.session);
#ifdef ESP_PLATFORM
  (void)voice_settings_load(&voice_settings);
  uint8_t sta_mac[6];
  ESP_ERROR_CHECK(esp_read_mac(sta_mac, ESP_MAC_WIFI_STA));
  voice_settings_set_device_id_from_mac(&voice_settings, sta_mac);
  board_sticks3_display_set_device_id(voice_settings.device_id);
  ESP_LOGI("power", "Battery idle sleep timeout: %lu seconds",
           (unsigned long)voice_settings.sleep_timeout_seconds);
  app.turn_task_active = false;
  app.turn_cancel_requested = false;
  app.turn_audio_started = false;
  app.turn_upload_active = false;
  app.turn_upload_finished = false;
  app.turn_upload_failed = false;
  app.turn_result = VOICE_TURN_FAILED;
  app.turn_http_ready = false;
  app.turn_storage_failed = false;
  app.turn_pending_byte = false;
  {
    app.turn_audio_stream = xStreamBufferCreate(8192, 1);
    app.turn_upload_stream = xStreamBufferCreate(8192, 1);
    if (app.turn_audio_stream && app.turn_upload_stream &&
        voice_settings_valid(&voice_settings)) {
      app.turn_http_ready = voice_turn_http_init(&app.turn_http,
          voice_settings.gateway_url, voice_settings.device_id,
          voice_settings.device_token, app.turn_audio_stream,
          &app.turn_cancel_requested, &app.turn_audio_started);
    }
  }
  if (voice_wifi_first_profile(voice_settings.wifi) >= 0) {
    if (!board_sticks3_wifi_start(voice_settings.wifi)) {
      (void)board_sticks3_wifi_start_ap();
    }
  } else {
    (void)board_sticks3_wifi_start_ap();
  }
  voice_wireguard_start(&voice_settings.wireguard);
  voice_config_httpd_start(&voice_settings);
  const http_voice_config_t cfg = {
      .url = voice_settings.gateway_url,
      .device_id = voice_settings.device_id,
      .token = voice_settings.device_token[0] ? voice_settings.device_token : NULL,
      .timeout_ms = 5000,
      .request_id = voice_settings.request_id,
  };
  if (http_voice_client_init(&voice_client, &cfg) == 0)
    http_session_bind_transport(&app.session, http_voice_client_transport(&voice_client));
#endif
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

/* Test hooks (spec section 8 acceptance criteria): expose the playback
 * resources directly so tests can verify they are actually released on
 * PLAYING -> IDLE/ERROR, not just that the state getter reads IDLE. */
size_t app_playback_position(void) {
  return app.playback_position;
}
size_t app_playback_pending_len(void) {
  return app.playback_pending_len;
}
size_t app_playback_rb_count(void) {
  return ring_buffer_count(&app.playback_rb);
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
 * Test hook: simulate the backend connection dropping mid-stream while a
 * reply is being downloaded/played (spec sections 8/13/38). Sets the sticky
 * flag the STATE_PLAYING tick checks; the next tick drives PLAYING -> ERROR
 * -> (after acknowledgement) IDLE, mirroring
 * app_test_simulate_ring_overflow() on the RECORDING side.
 */
void app_test_simulate_playback_conn_drop(void) {
  app.playback_conn_dropped = true;
}

/*
 * Test hook: simulate the backend's reply arriving with a malformed/
 * incorrect WAV header (spec sections 8/13/38). Same sticky-flag shape as
 * app_test_simulate_playback_conn_drop() above.
 */
void app_test_simulate_playback_bad_wav_header(void) {
  app.playback_bad_wav_header = true;
}

/*
 * One application tick. Non-blocking: each per-state action performs at most
 * one hardware poll and returns.
 */
void app_tick(void) {
  uint32_t now = hw_clock_ms();

  state_t s = state_machine_get_state(&app.sm);

  switch (s) {
    case STATE_BOOT:
      if (++app.boot_ticks >= APP_BOOT_TICKS) {
#ifdef ESP_PLATFORM
        if (voice_settings.request_id[0]) {
          if (start_turn_worker(false)) enter_state(STATE_PROCESSING, NULL);
          else enter_state(STATE_ERROR, "could not resume saved voice turn");
        } else {
          enter_state(STATE_IDLE, NULL);
        }
#else
        enter_state(STATE_IDLE, NULL);
#endif
      }
      break;

    case STATE_IDLE: {
      if (app.ignore_button_until_release) {
        if (!hw_button_raw()) {
          app.ignore_button_until_release = false;
          button_reset(&app.btn);
        }
        break;
      }
      button_event_t ev = button_poll(&app.btn, hw_button_raw(), now);
      if (ev == BUTTON_EVENT_PRESSED) {
        if (recording_start()) enter_state(STATE_RECORDING, NULL);
        else enter_state(STATE_ERROR, "voice recording start failed");
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
#ifdef ESP_PLATFORM
      if (app.turn_result == VOICE_TURN_FAILED && !app.turn_task_active) {
        hw_audio_capture_stop();
        enter_state(STATE_ERROR, "voice upload failed");
        break;
      }
#endif
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
#ifndef ESP_PLATFORM
        session_close_or_abort();
#endif
#ifdef ESP_PLATFORM
        {
          app.turn_upload_release_requested = true;
          if (!voice_settings.request_id[0] || !app.turn_task_active) {
            enter_state(STATE_ERROR, "could not resume voice turn");
            break;
          }
        }
#endif
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
#ifdef ESP_PLATFORM
      {
        if (app.turn_upload_release_requested && !app.turn_upload_finished) {
          recording_drain();
          if (ring_buffer_empty(&app.rb)) app.turn_upload_finished = true;
        }
        button_event_t event = button_poll(&app.btn, hw_button_raw(), now);
        if (event == BUTTON_EVENT_PRESSED) app.turn_cancel_requested = true;
        if (app.turn_storage_failed || app.turn_result == VOICE_TURN_FAILED) {
          if (app.turn_task_active) app.turn_cancel_requested = true;
          enter_state(STATE_ERROR, "voice turn failed");
        } else if (app.turn_result == VOICE_TURN_CANCELLED) {
          app.ignore_button_until_release = true;
          playback_release_resources();
          enter_state(STATE_IDLE, NULL);
        } else if (app.turn_audio_started) {
          app.playback_position = 0;
          hw_audio_playback_start(NULL, 0);
          enter_state(STATE_PLAYING, NULL);
        }
        break;
      }
#else
      playback_start();
#endif
      enter_state(STATE_PLAYING, NULL);
      break;

    case STATE_PLAYING:
      /*
       * Spec sections 8/13/38: a backend connection drop mid-stream or a
       * malformed WAV header on the reply must be detected during PLAYING
       * and drive the device to ERROR immediately -- not hang waiting for
       * bytes that are never coming, and not silently keep playing
       * corrupted/incomplete audio. Checked before draining so a failure
       * flagged this tick takes effect before any more bytes reach the
       * speaker. Real backend wiring lands with M2 (see http_session.c
       * file header); the flags are set here by
       * app_test_simulate_playback_conn_drop() /
       * app_test_simulate_playback_bad_wav_header() today and, later, by
       * the real M2 download/parsing path signaling the same failures.
       */
      if (app.playback_conn_dropped) {
        on_playback_error("playback backend connection dropped");
        break;
      }
      if (app.playback_bad_wav_header) {
        on_playback_error("playback malformed WAV header");
        break;
      }
#ifdef ESP_PLATFORM
      {
        if (app.turn_result == VOICE_TURN_FAILED || app.turn_storage_failed) {
          on_playback_error("voice turn failed during audio");
          break;
        }
        if (app.turn_result == VOICE_TURN_CANCELLED) {
          app.ignore_button_until_release = true;
          playback_release_resources();
          enter_state(STATE_IDLE, NULL);
          break;
        }
        if (!turn_playback_drain()) {
          on_playback_error("speaker rejected voice audio");
          break;
        }
        if (app.turn_result == VOICE_TURN_READY &&
            xStreamBufferBytesAvailable(app.turn_audio_stream) == 0) {
          if (app.turn_pending_byte) {
            on_playback_error("odd-length voice audio");
            break;
          }
          if (hw_audio_playback_drained()) {
            playback_release_resources();
            enter_state(STATE_IDLE, NULL);
          }
        }
        break;
      }
#endif
#ifndef ESP_PLATFORM

      /* M1-04: drain the loopback/response buffer to the speaker; wait for the
       * hardware to confirm playback actually finished before IDLE.
       *
       * EOF detection (spec section 8): playback_finished() is robust to
       * whatever the buffer fill level was at EOF -- it doesn't care
       * whether the loopback buffer was full, partially drained, or
       * empty when the last sample was consumed, only that (a) nothing
       * is left queued on this side (ring buffer empty, no pending
       * unwritten chunk) and (b) the hardware confirms it has actually
       * finished playing what was handed to it (hw_audio_playback_drained,
       * not just "queued"). Checked every tick, so the PLAYING -> IDLE
       * transition below fires on the very same tick EOF is confirmed --
       * no multi-tick delay and no dependence on a specific drain
       * sequence (full-buffer and near-empty-buffer EOF both resolve
       * through this one check). */
      playback_drain();
      if (playback_finished()) {
        /* Resource cleanup on IDLE entry (spec section 8): release the
         * ring buffer, pending chunk, position counter, and DMA/I2S
         * peripheral atomically with the state transition -- the same
         * playback_release_resources() the error path above uses, so
         * both PLAYING exits leave identical, fully-cleaned-up state and
         * can never leak a buffer, handle, or dangling position counter
         * into the next playback session. */
        playback_release_resources();
        enter_state(STATE_IDLE, NULL);
      }
      break;
#endif

    case STATE_ERROR: {
      bool raw_pressed = hw_button_raw();
      button_event_t ev = button_poll(&app.btn, raw_pressed, now);
      if (!app.error_ack_ready) {
        if (!raw_pressed && !button_is_pressed(&app.btn))
          app.error_ack_ready = true;
      } else if (ev == BUTTON_EVENT_PRESSED) {
        app.error_ack_pressed = true;
      } else if (ev == BUTTON_EVENT_RELEASED && app.error_ack_pressed) {
#ifdef ESP_PLATFORM
        if (!app.turn_task_active)
          enter_state(STATE_IDLE, NULL);
#else
        enter_state(STATE_IDLE, NULL);
#endif
      }
      break;
    }

    default:
      break;
  }
}

#ifdef VOICE_WITH_MAIN
static void voice_main_loop(void) {
  board_sticks3_log_memory();
  app_init();
  for (;;) {
    app_tick();
    screen_processing_phase_t phase = SCREEN_PROCESSING_THINKING;
#ifdef ESP_PLATFORM
    if (app.turn_client.status == VOICE_TURN_STATUS_TRANSCRIBING)
      phase = SCREEN_PROCESSING_TRANSCRIBING;
    else if (app.turn_client.status == VOICE_TURN_STATUS_SYNTHESIZING)
      phase = SCREEN_PROCESSING_SYNTHESIZING;
#endif
    board_sticks3_display_update(app_state(), hw_clock_ms(), phase);
    board_sticks3_power_tick(
      (app_state() != STATE_IDLE && app_state() != STATE_ERROR) || app.turn_task_active,
      hw_clock_ms(), voice_settings.sleep_timeout_seconds * 1000U);
    /* Keep the USB/Wi-Fi/I2S system tasks and watchdog serviced. */
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}
#ifdef ESP_PLATFORM
void app_main(void) { voice_main_loop(); }
#else
int main(void) { voice_main_loop(); return 0; }
#endif
#endif
