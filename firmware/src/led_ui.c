#include "led_ui.h"

#include <stdbool.h>

#include "led_config.h"
#include "hardware.h"

/*
 * State-to-mode mapping: one entry per led_state_t, in enum order.
 *
 * Spec section 9 (ТЗ, "LED UI") table, verbatim:
 *   Wi-Fi connection -> мигающий синий (blinking blue)
 *   Idle             -> слабый синий  (dim blue, steady)
 *   Recording        -> красный       (red, steady)
 *   Processing       -> жёлтый        (yellow, steady)
 *   Playback         -> зелёный       (green, steady)
 *   Error            -> мигающий красный (blinking red)
 * Only the two states the table explicitly calls "мигающий" blink; the
 * others stay steady on their color.
 */
static const led_mode_cfg_t LED_MODE_BY_STATE[6] = {
  [LED_STATE_WIFI] = {
      .color = LED_RGB_BLUE,
      .blink_period_ms = LED_BLINK_PERIOD_MS,
      .blink_duty_ms = LED_BLINK_DUTY_MS,
  },
  [LED_STATE_IDLE] = {
      .color = LED_RGB_BLUE_DIM,
      .blink_period_ms = 0,
      .blink_duty_ms = 0,
  },
  [LED_STATE_RECORDING] = {
      .color = LED_RGB_RED,
      .blink_period_ms = 0,
      .blink_duty_ms = 0,
  },
  [LED_STATE_PROCESSING] = {
      .color = LED_RGB_YELLOW,
      .blink_period_ms = 0,
      .blink_duty_ms = 0,
  },
  [LED_STATE_PLAYBACK] = {
      .color = LED_RGB_GREEN,
      .blink_period_ms = 0,
      .blink_duty_ms = 0,
  },
  [LED_STATE_ERROR] = {
      .color = LED_RGB_RED,
      .blink_period_ms = LED_BLINK_PERIOD_MS / 2,
      .blink_duty_ms = LED_BLINK_DUTY_MS / 2,
  },
};

typedef struct {
  led_state_t state;
  bool flash_until_set;
  uint32_t flash_until_ms;
  bool on;
} led_ctx_t;

static led_ctx_t led_ctx;

void led_init(void) {
  led_ctx.state = LED_STATE_IDLE;
  led_ctx.flash_until_set = false;
  led_ctx.flash_until_ms = 0;
  led_ctx.on = true;
  hw_led_write(LED_RGB_OFF);
}

void led_set_state(led_state_t state) {
  led_ctx.state = state;
}

led_state_t led_get_state(void) {
  return led_ctx.state;
}

void led_update(uint32_t now_ms) {
  if (!led_ctx.on) {
    return;
  }
  const led_mode_cfg_t* cfg = &LED_MODE_BY_STATE[led_ctx.state];

  if (led_ctx.flash_until_set && now_ms < led_ctx.flash_until_ms) {
    /* One-shot attention flash overrides the state pattern. */
    bool phase_on = (now_ms % LED_BLINK_PERIOD_MS) < LED_BLINK_DUTY_MS;
    hw_led_write(phase_on ? LED_RGB_RED : LED_RGB_OFF);
    return;
  }
  led_ctx.flash_until_set = false;

  bool on_now = true;
  if (cfg->blink_period_ms > 0) {
    uint32_t phase = now_ms % cfg->blink_period_ms;
    on_now = phase < cfg->blink_duty_ms;
  }

  hw_led_write(on_now ? cfg->color : LED_RGB_OFF);
}

void led_off(void) {
  led_ctx.on = false;
  led_ctx.flash_until_set = false;
  hw_led_write(LED_RGB_OFF);
}

void led_flash(uint32_t duration_ms, uint32_t now_ms) {
  led_ctx.on = true;
  led_ctx.flash_until_set = true;
  led_ctx.flash_until_ms = now_ms + duration_ms;
}
