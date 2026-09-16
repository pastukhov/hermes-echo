#ifndef LED_UI_H
#define LED_UI_H

/*
 * M1-05 RGB LED UI driver.
 *
 * The application sets a device state with led_set_state(); the driver maps
 * each state to a named RGB color and blink pattern (config in led_config.h)
 * and drives the RGB565 channel through the hw seam. No magic numbers in
 * this file: all colors and timings come from led_config.h.
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
