#ifndef BOARD_STICKS3_H
#define BOARD_STICKS3_H

#include <stdbool.h>
#include <stdint.h>
#include "state_machine.h"
#include "screen_ui.h"

/* Pin mapping M5StackStickS3 (ESP32-S3-PICO-1-N8R8)
 Based on:
https://docs.m5stack.com/en/core/StickS3
ES8311 audio codec pins specified task
 */

/* Audio: ES8311 */
/* I2S */
#define BOARD_I2S_MCLK_GPIO 18
#define BOARD_I2S_BCLK_GPIO 17
#define BOARD_I2S_LRCK_GPIO 15
#define BOARD_I2S_DOUT_GPIO 14
#define BOARD_I2S_DIN_GPIO 16
#define BOARD_I2S_PORT 0
#define BOARD_I2S_BCLK_PIN BOARD_I2S_BCLK_GPIO
#define BOARD_I2S_WS_PIN BOARD_I2S_LRCK_GPIO
#define BOARD_I2S_DOUT_PIN BOARD_I2S_DOUT_GPIO
#define BOARD_I2S_DIN_PIN BOARD_I2S_DIN_GPIO

/* I2C ES8311 configuration */
#define BOARD_I2C_SCL_GPIO 48
#define BOARD_I2C_SDA_GPIO 47

/* Buttons */
#define BOARD_KEY1_GPIO 11
#define BOARD_KEY2_GPIO 12
#define BOARD_BUTTON_PIN BOARD_KEY1_GPIO
#define BOARD_BUTTON_ACTIVE_LOW 1

/* LCD status display */
#define BOARD_LCD_SCLK_GPIO 40
#define BOARD_LCD_MOSI_GPIO 39
#define BOARD_LCD_CS_GPIO 41
#define BOARD_LCD_DC_GPIO 45
#define BOARD_LCD_RST_GPIO 21
#define BOARD_LCD_BL_GPIO 38

#ifdef ESP_PLATFORM
#include "driver/i2c_master.h"
i2c_master_bus_handle_t board_sticks3_i2c_bus(void);
#endif
void board_sticks3_power_tick(bool busy, uint32_t now_ms, uint32_t timeout_ms);

/* Credentials are intentionally supplied at build/runtime, never committed. */
bool board_sticks3_wifi_start(const char *ssid, const char *password);
bool board_sticks3_wifi_start_ap(void);
void board_sticks3_log_memory(void);
void board_sticks3_display_set_device_id(const char *device_id);
void board_sticks3_display_update(state_t state, uint32_t now_ms,
                                 screen_processing_phase_t phase);

#endif
