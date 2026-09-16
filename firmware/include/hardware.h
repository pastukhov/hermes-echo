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

#endif // HARDWARE_H
