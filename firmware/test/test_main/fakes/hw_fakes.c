#include "hw_fakes.h"

#include <string.h>

hw_fake_t g_hw_fake;

void hw_fake_reset(hw_fake_t* f) {
  memset(f, 0, sizeof(*f));
  f->playback_drained = true; /* nothing in flight until playback starts */
}

void hw_fake_set_clock(hw_fake_t* f, uint32_t ms) {
  f->clock_ms = ms;
}

void hw_fake_set_button(hw_fake_t* f, bool pressed) {
  f->button_raw = pressed;
}

void hw_fake_set_capture_available(hw_fake_t* f, size_t len) {
  f->capture_bytes_available = len;
}

void hw_fake_set_playback_drained(hw_fake_t* f, bool drained) {
  f->playback_drained = drained;
}

/* ---- seam implementations (host/test build) ---- */

uint32_t hw_clock_ms(void) {
  return g_hw_fake.clock_ms;
}

bool hw_button_raw(void) {
  g_hw_fake.button_reads++;
  return g_hw_fake.button_raw;
}

void hw_led_write(uint16_t rgb565) {
  g_hw_fake.led_rgb565 = rgb565;
  g_hw_fake.led_writes++;
}

bool hw_report_error(const char* what) {
  g_hw_fake.error_reports++;
  g_hw_fake.last_error = what;
  return true;
}

void hw_audio_capture_start(void) {
  g_hw_fake.capture_started = true;
  g_hw_fake.capture_start_calls++;
}

void hw_audio_capture_stop(void) {
  g_hw_fake.capture_started = false;
  g_hw_fake.capture_stop_calls++;
}

size_t hw_audio_capture_read(uint8_t* buf, size_t max_len) {
  if (!g_hw_fake.capture_started || buf == NULL || max_len == 0) {
    return 0;
  }
  size_t chunk = HW_FAKE_CAPTURE_CHUNK;
  if (chunk > max_len) {
    chunk = max_len;
  }
  if (chunk > g_hw_fake.capture_bytes_available) {
    chunk = g_hw_fake.capture_bytes_available;
  }
  if (chunk == 0) {
    return 0;
  }
  memset(buf, 0xAA, chunk); /* content is irrelevant to the tests */
  g_hw_fake.capture_bytes_available -= chunk;
  g_hw_fake.capture_bytes_read_total += chunk;
  return chunk;
}

void hw_audio_playback_start(void) {
  g_hw_fake.playback_started = true;
  g_hw_fake.playback_start_calls++;
}

void hw_audio_playback_stop(void) {
  g_hw_fake.playback_started = false;
  g_hw_fake.playback_stop_calls++;
}

size_t hw_audio_playback_write(const uint8_t* data, size_t len) {
  if (!g_hw_fake.playback_started || data == NULL || len == 0) {
    return 0;
  }
  g_hw_fake.playback_bytes_written_total += len;
  return len;
}

bool hw_audio_playback_drained(void) {
  return g_hw_fake.playback_drained;
}
