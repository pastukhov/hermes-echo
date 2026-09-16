#ifndef LED_CONFIG_H
#define LED_CONFIG_H

#include <stdint.h>

/*
 * M1-05 LED configuration: named RGB565 colors and blink timings.
 * No magic numbers live in led_ui.c — everything is configured here.
 */

/* RGB565 color constants. */
#define LED_RGB_RED       0xF800u
#define LED_RGB_GREEN     0x07E0u
#define LED_RGB_BLUE      0x001Fu
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
