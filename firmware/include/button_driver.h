#ifndef BUTTON_DRIVER_H
#define BUTTON_DRIVER_H

#include <stdbool.h>
#include <stdint.h>

/*
 * M1-05 push-to-talk button driver.
 *
 * Hardware-agnostic: the caller (application layer) samples the raw pin level
 * through the hw seam and feeds it in via button_poll(). The driver handles
 * debouncing and derives press / hold / release / max-record-timeout events
 * from the raw signal, so the state machine never touches GPIO directly.
 */

#define BUTTON_DEBOUNCE_MS_DEFAULT 30u
#define MAX_RECORD_SECONDS_DEFAULT 600u

typedef enum {
  BUTTON_EVENT_NONE = 0,
  BUTTON_EVENT_PRESSED,             // debounced press edge
  BUTTON_EVENT_RELEASED,            // debounced release edge
  BUTTON_EVENT_MAX_RECORD_TIMEOUT   // held for >= max record time (fires once)
} button_event_t;

typedef struct {
  bool raw;              // last raw pin sample
  uint32_t raw_since_ms; // now_ms when raw last changed
  bool debounced;        // last accepted (debounced) level
  bool is_pressed;       // logical pressed state
  uint32_t press_ms;     // now_ms at debounced press (0 if not pressed)
  uint32_t debounce_ms;
  uint32_t max_record_ms;
  bool timeout_fired;
} button_driver_t;

#ifdef __cplusplus
extern "C" {
#endif

/* Initialize with debounce window (ms) and max record time (seconds). */
void button_init(button_driver_t* b, uint32_t debounce_ms,
                 uint32_t max_record_seconds);

/*
 * Feed one raw pin sample (true = physically pressed) with the current
 * monotonic timestamp. Returns exactly one event per call: PRESSED,
 * RELEASED, MAX_RECORD_TIMEOUT (fires once per hold), or NONE.
 */
button_event_t button_poll(button_driver_t* b, bool raw_pressed,
                           uint32_t now_ms);

bool button_is_pressed(const button_driver_t* b);

/* Reset logical state (e.g. on ERROR recovery). */
void button_reset(button_driver_t* b);

#ifdef __cplusplus
}
#endif

#endif // BUTTON_DRIVER_H
