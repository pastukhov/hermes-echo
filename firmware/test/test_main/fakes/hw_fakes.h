#ifndef HW_FAKES_H
#define HW_FAKES_H

#include "hardware.h"

/*
 * Host fake for the hardware seam. Records every call so tests can assert
 * exactly what the state machine did to the button/LED hardware.
 */

typedef struct {
  uint32_t clock_ms;
  bool button_raw;
  uint16_t led_rgb565;
  int led_writes;
  int button_reads;
  int error_reports;
  const char* last_error;
} hw_fake_t;

extern hw_fake_t g_hw_fake;

void hw_fake_reset(hw_fake_t* f);
void hw_fake_set_clock(hw_fake_t* f, uint32_t ms);
void hw_fake_set_button(hw_fake_t* f, bool pressed);

#endif // HW_FAKES_H
