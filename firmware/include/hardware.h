#ifndef HARDWARE_H
#define HARDWARE_H

/*
 * Hardware seam (spec section 46, native test environment).
 *
 * The application layer talks to hardware ONLY through these functions.
 * On the ESP target they map to real GPIO/I2C drivers; host tests link a
 * fake implementation (test/fakes/hw_fakes.c) that records calls so the
 * button/LED wiring can be verified without hardware.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Monotonic milliseconds since an arbitrary origin. */
uint32_t hw_clock_ms(void);

/* Raw push-button level: true = physically pressed. */
bool hw_button_raw(void);

/*
 * Drive the single RGB LED. Takes one RGB565 word (0x0000 = off).
 */
void hw_led_write(uint16_t rgb565);

/*
 * Report a fatal device error from the state machine.
 * Returns true if the device should continue (host/test), false to abort.
 */
bool hw_report_error(const char* what);

/*
 * Audio capture (M1-03: record -> ring buffer).
 *
 * main.c never touches I2S directly, same rule as the button/LED seam
 * above. On-target (board_atom_echo.c) these delegate to audio_capture.c
 * (I2S RX, ESP-IDF only); host tests link a fake (test/fakes/hw_fakes.c)
 * that hands back scripted PCM bytes with no hardware involved.
 */

/* Start/stop the microphone capture path for one recording session. */
void hw_audio_capture_start(void);
void hw_audio_capture_stop(void);

/*
 * Pull up to max_len bytes of freshly captured PCM into buf. Returns the
 * number of bytes actually available this tick (0 if none is ready yet or
 * capture is not running) -- never blocks.
 */
size_t hw_audio_capture_read(uint8_t* buf, size_t max_len);

/*
 * Audio playback (M1-04: buffer -> speaker output).
 *
 * Same seam rule: on-target this delegates to audio_playback.c (I2S TX);
 * host tests link a fake that just records what would have been played.
 */

/* Start/stop the speaker output path for one playback session. */
void hw_audio_playback_start(void);
void hw_audio_playback_stop(void);

/*
 * Hand up to len bytes of PCM to the speaker. Returns the number of bytes
 * actually accepted this tick (0 if the sink is full/not ready) -- never
 * blocks.
 */
size_t hw_audio_playback_write(const uint8_t* data, size_t len);

/*
 * True once every byte handed to hw_audio_playback_write() has finished
 * playing (no audio left in flight downstream of the ring buffer).
 */
bool hw_audio_playback_drained(void);

#endif // HARDWARE_H
