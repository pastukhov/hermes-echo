#include "button_driver.h"

void button_init(button_driver_t* b, uint32_t debounce_ms,
                 uint32_t max_record_seconds) {
  if (!b) {
    return;
  }
  b->raw = false;
  b->raw_since_ms = 0;
  b->debounced = false;
  b->is_pressed = false;
  b->press_ms = 0;
  b->debounce_ms = debounce_ms;
  b->max_record_ms = max_record_seconds * 1000u;
  b->timeout_fired = false;
}

button_event_t button_poll(button_driver_t* b, bool raw_pressed,
                           uint32_t now_ms) {
  if (!b) {
    return BUTTON_EVENT_NONE;
  }

  /* Debounce: track how long the raw level has been unchanged. */
  if (raw_pressed != b->raw) {
    b->raw = raw_pressed;
    b->raw_since_ms = now_ms;
  }

  bool debounced = b->debounced;
  if (now_ms - b->raw_since_ms >= b->debounce_ms) {
    debounced = b->raw; /* raw level is stable: accept it */
  }
  b->debounced = debounced;

  if (debounced && !b->is_pressed) {
    /* Debounced press edge. */
    b->is_pressed = true;
    b->press_ms = now_ms;
    b->timeout_fired = false;
    return BUTTON_EVENT_PRESSED;
  }
  if (!debounced && b->is_pressed) {
    /* Debounced release edge. */
    b->is_pressed = false;
    return BUTTON_EVENT_RELEASED;
  }
  if (b->is_pressed && !b->timeout_fired && b->max_record_ms > 0 &&
      now_ms - b->press_ms >= b->max_record_ms) {
    b->timeout_fired = true;
    return BUTTON_EVENT_MAX_RECORD_TIMEOUT;
  }
  return BUTTON_EVENT_NONE;
}

bool button_is_pressed(const button_driver_t* b) {
  return b && b->is_pressed;
}

void button_reset(button_driver_t* b) {
  if (!b) {
    return;
  }
  b->raw = false;
  b->raw_since_ms = 0;
  b->debounced = false;
  b->is_pressed = false;
  b->press_ms = 0;
  b->timeout_fired = false;
}
