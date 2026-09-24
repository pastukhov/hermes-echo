#ifndef AUDIO_CAPTURE_H
#define AUDIO_CAPTURE_H

#include <stddef.h>
#include <stdint.h>
#include <esp_err.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * M1-03: streaming PCM S16LE microphone capture through I2S (I2S RX).
 *
 * Mirrors audio_playback.h's shape deliberately: the state machine (main.c)
 * treats record and playback symmetrically -- start a stream, feed/drain
 * fixed-size chunks once per tick, stop.
 */

/* Audio capture configuration. */
typedef struct {
    int sample_rate;        /* Sample rate in Hz (e.g., 16000) */
    int bits_per_sample;    /* Bits per sample (16 for S16LE) */
    int channel_count;      /* Number of channels (1 = mono) */
    int buffer_frame_size;  /* Frames per DMA descriptor (512-1024 recommended) */
    int queue_size;         /* Number of DMA descriptors */
} audio_capture_config_t;

typedef enum {
    AUDIO_CAPTURE_STATE_STOPPED,
    AUDIO_CAPTURE_STATE_STARTED,
    AUDIO_CAPTURE_STATE_ERROR
} audio_capture_state_t;

/* Initialize I2S audio capture (RX channel). */
esp_err_t audio_capture_init(const audio_capture_config_t *config);

/* Start the microphone capture stream. */
esp_err_t audio_capture_start(void);

/* Stop the microphone capture stream. */
esp_err_t audio_capture_stop(void);

/*
 * Read up to `size` bytes of freshly captured PCM S16LE data into `data`.
 * Non-blocking-ish: pass a small timeout_ms (e.g. 0) so a caller polling
 * once per tick never stalls the state machine waiting on the DMA queue.
 *
 * @return number of bytes actually read (0..size); 0 if nothing was ready
 *         within timeout_ms or capture is not started.
 */
size_t audio_capture_read(uint8_t *data, size_t size, uint32_t timeout_ms);

/* Current capture state. */
audio_capture_state_t audio_capture_get_state(void);

/* Cleanup and deinitialize I2S. */
esp_err_t audio_capture_deinit(void);

#ifdef __cplusplus
}
#endif

#endif /* AUDIO_CAPTURE_H */
