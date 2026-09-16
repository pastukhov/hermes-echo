#ifndef BOARD_ATOM_ECHO_H
#define BOARD_ATOM_ECHO_H

/*
 * Pin mapping for M5Stack ATOM Echo (ESP32-PICO-D4), per spec section 5/46
 * ("Pin mapping необходимо вынести в отдельный board-specific модуль").
 *
 * I2S pins below are confirmed by src/audio_playback.c (t_dee198df), which
 * passed an ESP-IDF 5.5.3 header cross-compile check during review.
 *
 * Button and RGB LED pins are NOT confirmed by any source in this repo or
 * the spec (ТЗ line 163 names the requirement but no pin numbers). Do not
 * fill these in from memory — a wrong pin compiles and flashes fine but
 * silently does nothing. Confirm against the M5Stack ATOM Echo datasheet
 * before wiring hw_button_raw()/hw_led_write() in board_atom_echo.c.
 */

#define BOARD_I2S_PORT       0   /* I2S_NUM_0 */
#define BOARD_I2S_BCLK_PIN   15
#define BOARD_I2S_WS_PIN     13
#define BOARD_I2S_DOUT_PIN   22

#endif // BOARD_ATOM_ECHO_H
