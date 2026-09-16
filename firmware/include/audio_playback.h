#ifndef AUDIO_PLAYBACK_H
#define AUDIO_PLAYBACK_H

#include <stdint.h>
#include <esp_err.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Audio playback configuration */
typedef struct {
    int sample_rate;        /* Sample rate in Hz (e.g., 16000, 24000, 44100) */
    int bits_per_sample;    /* Bits per sample (typically 16 for S16LE) */
    int channel_count;      /* Number of channels (1 = mono, 2 = stereo) */
    int buffer_frame_size;  /* Number of frames per buffer chunk (512-1024 recommended) */
    int queue_size;         /* Number of buffers in the queue */
} audio_playback_config_t;

/* Audio playback state */
typedef enum {
    AUDIO_PLAYBACK_STATE_STOPPED,
    AUDIO_PLAYBACK_STATE_STARTED,
    AUDIO_PLAYBACK_STATE_PAUSED,
    AUDIO_PLAYBACK_STATE_ERROR
} audio_playback_state_t;

/* Initialize I2S audio playback
 * 
 * @param config Configuration structure with sample rate and other parameters
 * @return ESP_OK on success, error code otherwise
 */
esp_err_t audio_playback_init(const audio_playback_config_t *config);

/* Start audio playback
 * 
 * @return ESP_OK on success, error code otherwise
 */
esp_err_t audio_playback_start(void);

/* Stop audio playback
 * 
 * @return ESP_OK on success, error code otherwise
 */
esp_err_t audio_playback_stop(void);

/* Write audio data chunk to playback buffer
 * 
 * @param data Pointer to PCM S16LE data
 * @param size Size of data in bytes (must be multiple of frame size)
 * @param timeout_ms Timeout in milliseconds
 * @return ESP_OK on success, error code otherwise (e.g., buffer full)
 */
esp_err_t audio_playback_write(const uint8_t *data, size_t size, uint32_t timeout_ms);

/* Get current playback state
 * 
 * @return Current audio_playback_state_t
 */
audio_playback_state_t audio_playback_get_state(void);

/* Get current buffer level (number of frames in queue)
 * 
 * @return Number of frames waiting in the queue
 */
int audio_playback_get_buffer_level(void);

/* Cleanup and deinitialize I2S
 * 
 * @return ESP_OK on success, error code otherwise
 */
esp_err_t audio_playback_deinit(void);

#ifdef __cplusplus
}
#endif

#endif /* AUDIO_PLAYBACK_H */
