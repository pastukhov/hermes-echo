#ifndef LED_UI_H
#define LED_UI_H

/*
 * M1-05 status-indicator UI driver.
 *
 * The application sets a device state with led_set_state(); the driver maps
 * each state to a named RGB color and blink pattern (config in led_config.h)
 * and drives it through hw_led_write() (the hw seam) -- an RGB LED, a
 * screen fill, or any other indicator a board provides. Naming here still
 * says "led" for historical reasons (the first target board had one); it
 * has no bearing on what a given board actually renders. No magic numbers
 * in this file: all colors and timings come from led_config.h.
 */

#include <stdint.h>

typedef enum {
  LED_STATE_WIFI = 0,
  LED_STATE_IDLE,
  LED_STATE_RECORDING,
  LED_STATE_PROCESSING,
  LED_STATE_PLAYBACK,
  LED_STATE_ERROR
} led_state_t;

#ifdef __cplusplus
extern "C" {
#endif

void led_init(void);
void led_set_state(led_state_t state);
led_state_t led_get_state(void);
/* Advance blink phase; call once per tick. */
void led_update(uint32_t now_ms);
void led_off(void);
/* One-shot attention flash for a given duration (ms). */
void led_flash(uint32_t duration_ms, uint32_t now_ms);

#ifdef __cplusplus
}
#endif

#endif // LED_UI_H
