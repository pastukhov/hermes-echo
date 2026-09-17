/*
 * board_atom_echo.c — M5Stack ATOM Echo implementation of the hardware seam
 * declared in include/hardware.h. Built only in env:esp32dev; host tests
 * (env:native) link test/fakes/hw_fakes.c instead.
 *
 * Pin sourcing: see include/board_atom_echo.h. Button polarity and the
 * WS2812/SK6812 bit timing below are the best-corroborated values available
 * without a literal schematic net name (button) or a device-vendor timing
 * spec sheet (LED) — see file-level comments at each site. The physical
 * device was reachable via USB/IP as of 2026-09-17; verify against it
 * before relying on this for anything safety-critical.
 */

#include "hardware.h"
#include "board_atom_echo.h"

#include "driver/gpio.h"
#include "driver/rmt_tx.h"
#include "driver/rmt_encoder.h"
#include "esp_timer.h"
#include "esp_log.h"

#include <string.h>

static const char* TAG = "board_atom_echo";

uint32_t hw_clock_ms(void) {
    return (uint32_t)(esp_timer_get_time() / 1000);
}

/* --- Button --------------------------------------------------------- */

static bool s_button_initialized = false;

bool hw_button_raw(void) {
    if (!s_button_initialized) {
        gpio_config_t cfg = {
            .pin_bit_mask = 1ULL << BOARD_BUTTON_PIN,
            .mode = GPIO_MODE_INPUT,
            /* GPIO39 is an ESP32 input-only pin with no internal pull
             * resistor; the pull-up is external, on the ATOM Echo board
             * itself (see include/board_atom_echo.h). Requesting one here
             * anyway is a harmless no-op on this pin and documents intent. */
            .pull_up_en = GPIO_PULLUP_ENABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type = GPIO_INTR_DISABLE,
        };
        gpio_config(&cfg);
        s_button_initialized = true;
    }
    int level = gpio_get_level(BOARD_BUTTON_PIN);
#if BOARD_BUTTON_ACTIVE_LOW
    return level == 0;
#else
    return level != 0;
#endif
}

/* --- RGB LED (SK6812, WS2812-protocol-compatible, single pixel) ----- */

/*
 * RMT bit timing at 10MHz resolution (100ns/tick), the values Espressif's
 * own RMT LED-strip example (peripherals/rmt/led_strip) uses for
 * WS2812/SK6812: T0H=0.3us T0L=0.9us, T1H=0.9us T1L=0.3us, reset >=50us low.
 */
#define LED_RMT_RESOLUTION_HZ   10000000
#define LED_T0H_TICKS           3
#define LED_T0L_TICKS           9
#define LED_T1H_TICKS           9
#define LED_T1L_TICKS           3
#define LED_RESET_US             80

static rmt_channel_handle_t s_led_channel = NULL;
static rmt_encoder_handle_t s_led_bytes_encoder = NULL;
static rmt_encoder_handle_t s_led_copy_encoder = NULL;

static bool s_led_initialized = false;
static bool s_led_init_failed = false;

static void led_init(void) {
    if (s_led_initialized || s_led_init_failed) {
        return;
    }

    rmt_tx_channel_config_t tx_chan_config = {
        .gpio_num = BOARD_RGB_LED_PIN,
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = LED_RMT_RESOLUTION_HZ,
        .mem_block_symbols = 64,
        .trans_queue_depth = 4,
    };
    if (rmt_new_tx_channel(&tx_chan_config, &s_led_channel) != ESP_OK) {
        ESP_LOGE(TAG, "hw_led_write: rmt_new_tx_channel failed");
        s_led_init_failed = true;
        return;
    }

    rmt_bytes_encoder_config_t bytes_encoder_config = {
        .bit0 = {
            .level0 = 1, .duration0 = LED_T0H_TICKS,
            .level1 = 0, .duration1 = LED_T0L_TICKS,
        },
        .bit1 = {
            .level0 = 1, .duration0 = LED_T1H_TICKS,
            .level1 = 0, .duration1 = LED_T1L_TICKS,
        },
        .flags.msb_first = 1,
    };
    if (rmt_new_bytes_encoder(&bytes_encoder_config, &s_led_bytes_encoder) != ESP_OK) {
        ESP_LOGE(TAG, "hw_led_write: rmt_new_bytes_encoder failed");
        s_led_init_failed = true;
        return;
    }

    rmt_copy_encoder_config_t copy_encoder_config = {0};
    if (rmt_new_copy_encoder(&copy_encoder_config, &s_led_copy_encoder) != ESP_OK) {
        ESP_LOGE(TAG, "hw_led_write: rmt_new_copy_encoder failed");
        s_led_init_failed = true;
        return;
    }

    if (rmt_enable(s_led_channel) != ESP_OK) {
        ESP_LOGE(TAG, "hw_led_write: rmt_enable failed");
        s_led_init_failed = true;
        return;
    }

    s_led_initialized = true;
}

void hw_led_write(uint16_t rgb565) {
    led_init();
    if (!s_led_initialized) {
        return;
    }

    /* RGB565 -> RGB888, then SK6812/WS2812 wire order (GRB). */
    uint8_t r5 = (rgb565 >> 11) & 0x1F;
    uint8_t g6 = (rgb565 >> 5) & 0x3F;
    uint8_t b5 = rgb565 & 0x1F;
    uint8_t r8 = (uint8_t)((r5 * 255 + 15) / 31);
    uint8_t g8 = (uint8_t)((g6 * 255 + 31) / 63);
    uint8_t b8 = (uint8_t)((b5 * 255 + 15) / 31);
    uint8_t grb[3] = {g8, r8, b8};

    rmt_transmit_config_t tx_config = {
        .loop_count = 0,
    };
    if (rmt_transmit(s_led_channel, s_led_bytes_encoder, grb, sizeof(grb), &tx_config) != ESP_OK) {
        ESP_LOGW(TAG, "hw_led_write: rmt_transmit failed");
        return;
    }
    rmt_tx_wait_all_done(s_led_channel, LED_RESET_US * 2 / 1000 + 10);

    /* Latch: hold the line low for >= LED_RESET_US before the next frame.
     * The copy encoder repeats a single "low" symbol for the reset gap. */
    static const rmt_symbol_word_t reset_symbol = {
        .level0 = 0, .duration0 = LED_RESET_US * (LED_RMT_RESOLUTION_HZ / 1000000),
        .level1 = 0, .duration1 = 0,
    };
    rmt_transmit(s_led_channel, s_led_copy_encoder, &reset_symbol, sizeof(reset_symbol), &tx_config);
    rmt_tx_wait_all_done(s_led_channel, 10);
}

bool hw_report_error(const char* what) {
    ESP_LOGE(TAG, "hardware error: %s", what ? what : "(null)");
    return false;
}
