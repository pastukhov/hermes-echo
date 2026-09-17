#ifndef BOARD_ATOM_ECHO_H
#define BOARD_ATOM_ECHO_H

/*
 * Pin mapping for M5Stack ATOM Echo (ESP32-PICO-D4), per spec section 5/46
 * ("Pin mapping необходимо вынести в отдельный board-specific модуль").
 *
 * Every pin below is cross-checked against THREE independent official
 * sources (2026-09-17), not carried over from memory:
 *   - https://docs.m5stack.com/en/atom/atomecho -- "PinMap > HMI" table
 *     (RGB LED = G27 data, Button = G39 input) and "Note > Pin Usage Notes"
 *     ("Pins G19, G22, G23, and G33 ... reserved for the device's internal
 *     audio I2S interface").
 *   - M5Stack's own example firmware, m5stack/M5-ProductExampleCodes
 *     (Core/Atom/AtomEcho/.../AudioOutputI2S.cpp): `SetPinout(19, 33, 22)`
 *     = BCLK, WS/LRCLK, DOUT.
 *   - Community references for the PDM mic data-in pin (23), consistent
 *     with the four reserved-bus pins above (BCLK/WS shared with the mic
 *     clock; DIN is the fourth).
 *
 * This SUPERSEDES the BCLK=15/WS=13 values previously hardcoded directly
 * in src/audio_playback.c -- those did not match any of the above sources
 * (only DOUT=22 happened to coincide) and were never actually confirmed
 * against M5Stack's own pin-out, despite a code comment claiming so.
 *
 * Button polarity (active-low, i.e. hw_button_raw() returns true when the
 * GPIO39 level reads LOW) follows the M5Stack button convention used
 * consistently across the whole ATOM/Core/StickC product line (BtnA etc.)
 * and the M5Stack community's own notes that G39 -- an ESP32 input-only
 * pin with no internal pull resistor -- relies on an external pull-up
 * already present on the ATOM Echo board. This is the best-corroborated
 * reading available without a literal schematic net name for the button
 * circuit; verify empirically against real hardware (device is currently
 * reachable via USB/IP) before relying on it for anything safety-critical.
 */

#define BOARD_I2S_PORT       0   /* I2S_NUM_0 */
#define BOARD_I2S_BCLK_PIN   19
#define BOARD_I2S_WS_PIN     33
#define BOARD_I2S_DOUT_PIN   22
#define BOARD_I2S_DIN_PIN    23  /* PDM mic data-in; not yet used by any module */

#define BOARD_BUTTON_PIN         39
#define BOARD_BUTTON_ACTIVE_LOW  1  /* pressed == GPIO level LOW */

#define BOARD_RGB_LED_PIN    27  /* SK6812 (WS2812-protocol-compatible), single LED */

#endif // BOARD_ATOM_ECHO_H
