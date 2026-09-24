#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
uint32_t hw_clock_ms(void) { return 0; }
bool hw_button_raw(void) { return false; }
bool hw_report_error(const char *x) { (void)x; return false; }
void hw_audio_capture_start(void) {}
void hw_audio_capture_stop(void) {}
size_t hw_audio_capture_read(uint8_t *b, size_t n) { (void)b; (void)n; return 0; }
void hw_audio_playback_start(const void *d, size_t n) { (void)d; (void)n; }
void hw_audio_playback_stop(void) {}
size_t hw_audio_playback_write(const uint8_t *b, size_t n) { (void)b; return n; }
bool hw_audio_playback_drained(void) { return true; }
