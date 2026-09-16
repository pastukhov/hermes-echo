/*
 * board_atom_echo.c — M5Stack ATOM Echo implementation of the hardware seam
 * declared in include/hardware.h. Built only in env:esp32dev; host tests
 * (env:native) link test/fakes/hw_fakes.c instead.
 *
 * See include/board_atom_echo.h for which pins are confirmed vs. open.
 */

#include "hardware.h"
#include "board_atom_echo.h"

#include "esp_timer.h"
#include "esp_log.h"

static const char* TAG = "board_atom_echo";

uint32_t hw_clock_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000);
}

bool hw_button_raw(void) {
#error "hw_button_raw: ATOM Echo button GPIO not confirmed by any source " \
       "(ТЗ line 163 names the requirement, no pin number). Confirm against " \
       "the M5Stack ATOM Echo datasheet, then replace this with a real " \
       "gpio_get_level() read. Do not guess the pin."
}

void hw_led_write(uint16_t rgb565) {
    (void)rgb565;
#error "hw_led_write: ATOM Echo RGB LED (WS2812) GPIO not confirmed by any " \
       "source. Confirm against the M5Stack ATOM Echo datasheet, then wire " \
       "this to the RMT/WS2812 driver. Do not guess the pin."
}

bool hw_report_error(const char* what) {
    ESP_LOGE(TAG, "hardware error: %s", what ? what : "(null)");
    return false;
}
