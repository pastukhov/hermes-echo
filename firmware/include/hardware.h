#ifndef HARDWARE_H
#define HARDWARE_H

/*
 Hardware seam (spec section 46, native test environment).
 *
 * application layer talks hardware ONLY through functions.
 * ESP target map real GPIO/I2C drivers; host tests link a
 * fake implementation (test/fakes/hw_fakes.c) records calls so the
 * button and audio paths can be verified without hardware.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Monotonic milliseconds since arbitrary origin. */
uint32_t hw_clock_ms(void);

/* Raw push-button level: true physically pressed. */
bool hw_button_raw(void);

/*
 * Report fatal device error state machine.
 * Returns true device continue (host/test) false abort.
 */
bool hw_report_error(const char* what);

/*
 * Audio capture (M1-03: record -> ring buffer).
 * 
 * main.c does not touch I2S directly, as with the button seam
 * above. On-target (board_atom_echo.c) delegates audio_capture.c
 * (I2S RX, ESP-IDF only); host tests link fake (test/fakes/hw_fakes.c)
 * hands back scripted PCM bytes hardware involved.
 */

/* Start/stop microphone capture path one recording session. */
void hw_audio_capture_start(void);
void hw_audio_capture_stop(void);

/*
 * Pull max_len bytes freshly captured PCM buf. Returns the
 * number bytes actually available tick (0 if none is ready yet or
 * capture is not running) blocks.
 */
size_t hw_audio_capture_read(uint8_t* buf, size_t max_len);

/*
 * Audio playback (M1-04: buffer ... 
 */

/* Start/stop audio playback path one playback session. */
void hw_audio_playback_start(const void* data, size_t size);
void hw_audio_playback_stop(void);

/*
 Push max_len bytes played back. Returns the
 number bytes actually acceptedtick (0 if none is accepted yet or
 * playback is not running).
 */
size_t hw_audio_playback_write(const uint8_t* buf, size_t size);

/*
 Return true when the playback hardware has finished playing
 all bytes that have been given to it (i.e. the DMA queue is empty).
 */
bool hw_audio_playback_drained(void);

#endif /*HARDWARE_H*/
