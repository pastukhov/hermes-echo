#ifndef SCREEN_BRIGHTNESS_H
#define SCREEN_BRIGHTNESS_H
#include <stdbool.h>
#include <stdint.h>
typedef struct {
  bool initialized, raw, down, armed;
  uint32_t changed_ms, pressed_ms;
  unsigned level;
} screen_brightness_t;
bool screen_brightness_tick(screen_brightness_t *state, bool down, uint32_t now_ms);
unsigned screen_brightness_percent(const screen_brightness_t *state);
#endif
