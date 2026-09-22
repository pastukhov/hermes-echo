#ifndef LED_CONFIG_H
#define LED_CONFIG_H

#include <stdint.h>

/*
 * M1-05 LED configuration: named RGB565 colors and blink timings.
 * No magic numbers live in led_ui.c — everything is configured here.
 */

/* RGB565 color constants.
 *
 * Spec section 9 (ТЗ, "LED UI") names these states and colors:
 *   Wi-Fi connection -> blinking blue, Idle -> dim blue ("слабый синий"),
 *   Recording -> red, Processing -> yellow, Playback -> green,
 *   Error -> blinking red. The LED driver (led_ui.c) maps its states to
 *   exactly these; the constants below are the single source of truth.
 */
#define LED_RGB_RED       0xF800u
#define LED_RGB_GREEN     0x07E0u
#define LED_RGB_BLUE      0x001Fu
#define LED_RGB_BLUE_DIM  0x000Fu /* half-intensity blue: IDLE ("слабый синий") */
#define LED_RGB_YELLOW    0xFFE0u /* Processing (spec section 9: "жёлтый") */
#define LED_RGB_CYAN      0x07FFu
#define LED_RGB_ORANGE    0xFD20u
#define LED_RGB_OFF       0x0000u

/* Blink timings (ms). */
#define LED_BLINK_PERIOD_MS   500u
#define LED_BLINK_DUTY_MS     250u

typedef struct {
  uint16_t color;
  uint32_t blink_period_ms; /* 0 = steady on */
  uint32_t blink_duty_ms;
} led_mode_cfg_t;

#endif // LED_CONFIG_H
