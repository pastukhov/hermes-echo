#ifndef PLAYBACK_OCCUPANCY_H
#define PLAYBACK_OCCUPANCY_H

#include <stdint.h>

/*
 * playback_occupancy - pure arithmetic for tracking how many audio frames
 * are still "in flight" (handed to the I2S driver but not yet actually
 * played out by the hardware).
 *
 * Why this exists (spec section 8): audio_playback_get_buffer_level() /
 * hw_audio_playback_drained() must report TRUE occupancy, not fixed DMA
 * buffer capacity, or the PLAYING -> IDLE EOF transition can never fire on
 * real hardware (a fixed non-zero "buffer level" would make
 * hw_audio_playback_drained() permanently false once playback starts).
 * ESP-IDF's i2s_channel_get_info() only exposes the total *allocated* DMA
 * buffer size, which is a constant, not an occupancy figure -- there is no
 * driver API for "frames still queued".
 *
 * The fallback used here: I2S transmits at a fixed, known rate once a
 * channel is enabled (exactly sample_rate frames/sec), so "frames already
 * played" is exactly the number of sample periods that have elapsed in
 * wall-clock time since playback started, clamped to what has actually
 * been written so far (the hardware can't play frames it was never given,
 * e.g. if the feeder stalls). This is pulled out into its own
 * dependency-free module so it can be exercised by a native/host unit
 * test without any ESP-IDF headers or real I2S hardware -- see
 * test/test_main/test_main.c's playback_occupancy_* cases, which assert
 * the value actually reaches zero once enough time has elapsed (not just
 * that it stays constant, the defect this module replaces).
 */

/*
 * Returns the number of frames still occupying the sink: frames handed to
 * the driver (frames_written) minus frames the hardware must already have
 * played out, given elapsed_us of wall-clock time since playback started
 * at sample_rate frames/sec. Never negative, never more than
 * frames_written. Returns 0 if sample_rate <= 0 or elapsed_us < 0 (callers
 * are expected to treat "unknown/invalid" as "nothing to wait on" rather
 * than risk describing the sink as permanently non-empty).
 */
uint64_t playback_occupancy_frames(uint64_t frames_written, int64_t elapsed_us,
                                    int32_t sample_rate);

#endif /* PLAYBACK_OCCUPANCY_H */
