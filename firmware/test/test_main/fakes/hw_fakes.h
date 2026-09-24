#ifndef HW_FAKES_H
#define HW_FAKES_H

#include "hardware.h"

/*
 * Host fake for the hardware seam. Records every call so tests can assert
 * exactly what the state machine did to the button/audio hardware.
 */

/* Bytes the capture fake hands back per hw_audio_capture_read() call, once
 * capture is started (tests script this to simulate the mic producing
 * audio; real content doesn't matter, only counts). 0 = nothing ready. */
#define HW_FAKE_CAPTURE_CHUNK 64u

typedef struct {
  uint32_t clock_ms;
  bool button_raw;
  int button_reads;
  int error_reports;
  const char* last_error;

  /* Audio capture (M1-03). */
  bool capture_started;
  size_t capture_bytes_available; /* test-scripted "mic has this much left" */
  size_t capture_bytes_read_total;
  int capture_start_calls;
  int capture_stop_calls;

  /* Audio playback (M1-04). */
  bool playback_started;
  size_t playback_bytes_written_total;
  int playback_start_calls;
  int playback_stop_calls;
  /* Defaults to true (nothing in flight); tests can force false to model
   * "still playing" and observe that PLAYING waits for drain. */
  bool playback_drained;
  /* Optional per-call cap on how many bytes hw_audio_playback_write()
   * accepts (0 = unlimited, the default): lets tests model the DMA sink
   * being momentarily full so a large loopback buffer needs more than one
   * playback_drain() call to empty, exercising the "regardless of buffer
   * fill level at EOF" acceptance criterion. */
  size_t playback_write_limit;
} hw_fake_t;

extern hw_fake_t g_hw_fake;

void hw_fake_reset(hw_fake_t* f);
void hw_fake_set_clock(hw_fake_t* f, uint32_t ms);
void hw_fake_set_button(hw_fake_t* f, bool pressed);

/* Script the mic to have `len` bytes of audio available to capture. */
void hw_fake_set_capture_available(hw_fake_t* f, size_t len);

/* Script whether the speaker sink still has audio in flight (false) or has
 * finished draining everything handed to it (true, the reset default). */
void hw_fake_set_playback_drained(hw_fake_t* f, bool drained);

/* Cap how many bytes a single hw_audio_playback_write() call accepts (0 =
 * unlimited). Models a momentarily-full DMA sink. */
void hw_fake_set_playback_write_limit(hw_fake_t* f, size_t limit);

void hw_fake_set_playback_drained(hw_fake_t* f, bool drained);

bool hw_audio_playback_drained(void);

#endif //HW_FAKES_H
