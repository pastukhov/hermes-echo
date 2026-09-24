/*
 * playback_occupancy.c - see include/playback_occupancy.h for the "why".
 *
 * Pure arithmetic, no hardware/ESP-IDF dependency, so it is included in
 * both the esp32dev build (via audio_playback.c) and the native test
 * build (test/test_main/test_main.c links it directly).
 */

#include "playback_occupancy.h"

uint64_t playback_occupancy_frames(uint64_t frames_written, int64_t elapsed_us,
                                    int32_t sample_rate) {
    if (sample_rate <= 0) {
        return 0;
    }
    if (elapsed_us < 0) {
        elapsed_us = 0;
    }

    uint64_t frames_played =
        (uint64_t)elapsed_us * (uint64_t)sample_rate / 1000000ULL;
    if (frames_played > frames_written) {
        frames_played = frames_written;
    }

    return frames_written - frames_played;
}
