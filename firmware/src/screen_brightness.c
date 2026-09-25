#include "screen_brightness.h"
void screen_brightness_restore(screen_brightness_t *state, unsigned level) {
  state->level = level < SCREEN_BRIGHTNESS_LEVEL_COUNT ? level : 0;
}
unsigned screen_brightness_percent(const screen_brightness_t *state) {
  static const unsigned levels[] = {100, 60, 30, 10, 5, 2};
  return levels[state->level % SCREEN_BRIGHTNESS_LEVEL_COUNT];
}
bool screen_brightness_tick(screen_brightness_t *s, bool down, uint32_t now) {
  if (!s->initialized) {
    s->initialized = true;
    s->raw = s->down = down;
    s->changed_ms = now;
    return false; // Releasing a wake-up button is not a brightness click.
  }
  if (s->raw != down) { s->raw = down; s->changed_ms = now; }
  if (s->down == down || (uint32_t)(now - s->changed_ms) < 30) return false;
  s->down = down;
  if (down) {
    s->pressed_ms = s->changed_ms;
    s->armed = true;
  } else {
    bool short_press = s->armed && (uint32_t)(s->changed_ms - s->pressed_ms) < 3000;
    s->armed = false;
    if (short_press) { s->level = (s->level + 1) % SCREEN_BRIGHTNESS_LEVEL_COUNT; return true; }
  }
  return false;
}
