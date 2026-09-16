#include "hw_fakes.h"

#include <string.h>

hw_fake_t g_hw_fake;

void hw_fake_reset(hw_fake_t* f) {
  memset(f, 0, sizeof(*f));
}

void hw_fake_set_clock(hw_fake_t* f, uint32_t ms) {
  f->clock_ms = ms;
}

void hw_fake_set_button(hw_fake_t* f, bool pressed) {
  f->button_raw = pressed;
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
