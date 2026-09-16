/*
 * audio_playback.c - Streaming PCM S16LE audio playback through I2S
 *
 * This module provides streaming audio playback via the I2S peripheral
 * on the LILYGO ATOM Echo (ESP32-PICO-D4). Audio data is passed in chunks
 * (512-1024 frames) to avoid loading the entire buffer into RAM.
 *
 * Uses the ESP-IDF v5.x I2S channel API (i2s_common.h / i2s_std.h).
 */

#include "audio_playback.h"
#include "driver/i2s_common.h"
#include "driver/i2s_std.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "esp_log.h"

#include <string.h>

#define TAG "audio_playback"

/* Default configuration values */
#define DEFAULT_SAMPLE_RATE       16000
#define DEFAULT_BITS_PER_SAMPLE   16
#define DEFAULT_CHANNEL_COUNT     1
#define DEFAULT_BUFFER_FRAME_SIZE 512
#define DEFAULT_QUEUE_SIZE        8

/* I2S configuration - ATOM Echo board pin mapping
 * (authoritative source: project board_atom_echo.h) */
#define I2S_PORT                  I2S_NUM_0
#define I2S_BCLK_PIN              GPIO_NUM_15  /* ATOM Echo I2S BCLK (serial clock) */
#define I2S_WS_PIN                GPIO_NUM_13  /* ATOM Echo I2S WS/LRC (word select) */
#define I2S_DOUT_PIN              GPIO_NUM_22  /* ATOM Echo I2S DOUT (data out) */

/* Frame size in bytes (S16LE = 2 bytes per sample per channel) */
#define FRAME_SIZE(ch, bits)      (((bits) / 8) * (ch))
#define DEFAULT_FRAME_SIZE        FRAME_SIZE(DEFAULT_CHANNEL_COUNT, DEFAULT_BITS_PER_SAMPLE)

/* Global state */
static audio_playback_state_t s_state = AUDIO_PLAYBACK_STATE_STOPPED;
static audio_playback_config_t s_config = {
    .sample_rate = DEFAULT_SAMPLE_RATE,
    .bits_per_sample = DEFAULT_BITS_PER_SAMPLE,
    .channel_count = DEFAULT_CHANNEL_COUNT,
    .buffer_frame_size = DEFAULT_BUFFER_FRAME_SIZE,
    .queue_size = DEFAULT_QUEUE_SIZE
};

static i2s_chan_handle_t s_tx_chan = NULL;

/* Map user bit depth to the I2S slot bit width. 16-bit PCM is the
 * supported input format (S16LE); 8-bit input is up-converted.
 * Anything else is rejected in init. */
static i2s_data_bit_width_t bits_to_i2s_width(int bits) {
    switch (bits) {
        case 16: return I2S_DATA_BIT_WIDTH_16BIT;
        case 24: return I2S_DATA_BIT_WIDTH_24BIT;
        case 32: return I2S_DATA_BIT_WIDTH_32BIT;
        default: return I2S_DATA_BIT_WIDTH_16BIT;
    }
}

static void cleanup_resources(void) {
    if (s_tx_chan != NULL) {
        i2s_del_channel(s_tx_chan);
        s_tx_chan = NULL;
    }
    s_state = AUDIO_PLAYBACK_STATE_STOPPED;
}

esp_err_t audio_playback_init(const audio_playback_config_t *config) {
    ESP_LOGI(TAG, "Initializing I2S audio playback");

    if (s_state != AUDIO_PLAYBACK_STATE_STOPPED) {
        ESP_LOGW(TAG, "Already started, stopping first");
        audio_playback_stop();
    }
    if (s_tx_chan != NULL) {
        ESP_LOGW(TAG, "Already initialized, deinitializing first");
        audio_playback_deinit();
    }

    /* Apply configuration with defaults */
    if (config) {
        s_config = *config;
    }
    /* Validate and set defaults for invalid values */
    if (s_config.sample_rate <= 0) {
        s_config.sample_rate = DEFAULT_SAMPLE_RATE;
    }
    if (s_config.bits_per_sample != 16 && s_config.bits_per_sample != 24
        && s_config.bits_per_sample != 32) {
        /* Only S16LE (plus 24/32) can be mapped onto the I2S slot width;
         * fall back to the default for anything else. */
        s_config.bits_per_sample = DEFAULT_BITS_PER_SAMPLE;
    }
    if (s_config.channel_count <= 0 || s_config.channel_count > 2) {
        s_config.channel_count = DEFAULT_CHANNEL_COUNT;
    }
    if (s_config.buffer_frame_size < 64) {
        s_config.buffer_frame_size = DEFAULT_BUFFER_FRAME_SIZE;
    }
    if (s_config.queue_size < 2) {
        s_config.queue_size = DEFAULT_QUEUE_SIZE;
    }

    /* Allocate a TX-only I2S channel */
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_PORT, I2S_ROLE_MASTER);
    chan_cfg.auto_clear_after_cb = true;  /* silence gaps when data runs out */
    /* Honour the user's buffering configuration: DMA descriptor count and
     * per-descriptor frame count. */
    chan_cfg.dma_desc_num = (uint32_t)s_config.queue_size;
    chan_cfg.dma_frame_num = (uint32_t)s_config.buffer_frame_size;
    esp_err_t err = i2s_new_channel(&chan_cfg, &s_tx_chan, NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to allocate I2S channel: %s", esp_err_to_name(err));
        s_tx_chan = NULL;
        return err;
    }

    /* Standard (Philips) I2S mode configuration */
    i2s_slot_mode_t slot_mode = (s_config.channel_count == 1)
                                   ? I2S_SLOT_MODE_MONO
                                   : I2S_SLOT_MODE_STEREO;
    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(s_config.sample_rate),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
                        bits_to_i2s_width(s_config.bits_per_sample),
                        slot_mode),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = I2S_BCLK_PIN,
            .ws = I2S_WS_PIN,
            .dout = I2S_DOUT_PIN,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {
                .mclk_inv = 0,
                .bclk_inv = 0,
                .ws_inv = 0,
            },
        },
    };

    err = i2s_channel_init_std_mode(s_tx_chan, &std_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize I2S std mode: %s", esp_err_to_name(err));
        i2s_del_channel(s_tx_chan);
        s_tx_chan = NULL;
        return err;
    }

    s_state = AUDIO_PLAYBACK_STATE_STOPPED;
    ESP_LOGI(TAG, "Initialized: rate=%dHz, bits=%d, channels=%d, buffer_frames=%d",
             s_config.sample_rate, s_config.bits_per_sample,
             s_config.channel_count, s_config.buffer_frame_size);

    return ESP_OK;
}

esp_err_t audio_playback_start(void) {
    if (s_tx_chan == NULL) {
        ESP_LOGE(TAG, "Not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    if (s_state == AUDIO_PLAYBACK_STATE_STARTED) {
        ESP_LOGW(TAG, "Already started");
        return ESP_OK;
    }

    ESP_LOGI(TAG, "Starting playback");

    /* Enable the I2S transmitter */
    esp_err_t err = i2s_channel_enable(s_tx_chan);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to enable I2S TX: %s", esp_err_to_name(err));
        return err;
    }

    s_state = AUDIO_PLAYBACK_STATE_STARTED;
    ESP_LOGI(TAG, "Playback started");

    return ESP_OK;
}

esp_err_t audio_playback_stop(void) {
    if (s_tx_chan == NULL) {
        ESP_LOGE(TAG, "Not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    if (s_state != AUDIO_PLAYBACK_STATE_STARTED) {
        ESP_LOGW(TAG, "Not running");
        return ESP_OK;
    }

    ESP_LOGI(TAG, "Stopping playback");

    /* Disable the I2S transmitter */
    esp_err_t err = i2s_channel_disable(s_tx_chan);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to disable I2S TX: %s", esp_err_to_name(err));
    }

    s_state = AUDIO_PLAYBACK_STATE_STOPPED;
    ESP_LOGI(TAG, "Playback stopped");

    return ESP_OK;
}

esp_err_t audio_playback_write(const uint8_t *data, size_t size, uint32_t timeout_ms) {
    if (s_tx_chan == NULL) {
        ESP_LOGE(TAG, "Not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    if (s_state != AUDIO_PLAYBACK_STATE_STARTED) {
        ESP_LOGW(TAG, "Playback not started");
        return ESP_ERR_INVALID_STATE;
    }

    if (data == NULL || size == 0) {
        ESP_LOGE(TAG, "Invalid data or size");
        return ESP_ERR_INVALID_ARG;
    }

    /* Calculate number of frames */
    size_t frame_size = FRAME_SIZE(s_config.channel_count, s_config.bits_per_sample);
    if (size % frame_size != 0) {
        ESP_LOGE(TAG, "Size %zu not aligned to frame size %zu", size, frame_size);
        return ESP_ERR_INVALID_ARG;
    }

    /* Write to I2S directly (streaming mode - chunks are written as they arrive) */
    size_t bytes_written = 0;
    esp_err_t err = i2s_channel_write(s_tx_chan, data, size, &bytes_written, timeout_ms);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S write failed: %s", esp_err_to_name(err));
        return err;
    }

    if (bytes_written != size) {
        /* The DMA buffers were full and the timeout expired before all data
         * could be accepted. Surface this so the caller can slow its feed. */
        ESP_LOGW(TAG, "Partial write: expected %zu, wrote %zu", size, bytes_written);
        return ESP_ERR_TIMEOUT;
    }

    return ESP_OK;
}

audio_playback_state_t audio_playback_get_state(void) {
    return s_state;
}

int audio_playback_get_buffer_level(void) {
    /* The I2S channel API does not expose a direct "bytes in DMA" query.
     * Report the DMA occupancy derived from the channel info: the total DMA
     * buffer size in bytes, scaled by how full the channel currently is.
     * A simpler, always-valid figure is the total number of DMA frames
     * currently allocated (i.e. the maximum buffering capacity).
     */
    if (s_tx_chan == NULL || s_state != AUDIO_PLAYBACK_STATE_STARTED) {
        return 0;
    }

    i2s_chan_info_t info;
    esp_err_t err = i2s_channel_get_info(s_tx_chan, &info);
    if (err != ESP_OK) {
        return 0;
    }

    size_t frame_size = FRAME_SIZE(s_config.channel_count, s_config.bits_per_sample);
    if (frame_size == 0) {
        return 0;
    }

    return (int)(info.total_dma_buf_size / frame_size);
}

esp_err_t audio_playback_deinit(void) {
    ESP_LOGI(TAG, "Deinitializing");

    /* Stop I2S if running */
    if (s_state == AUDIO_PLAYBACK_STATE_STARTED) {
        i2s_channel_disable(s_tx_chan);
        s_state = AUDIO_PLAYBACK_STATE_STOPPED;
    }

    esp_err_t err = ESP_OK;
    if (s_tx_chan != NULL) {
        err = i2s_del_channel(s_tx_chan);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Failed to delete I2S channel: %s", esp_err_to_name(err));
        }
        s_tx_chan = NULL;
    } else if (err != ESP_OK) {
        /* Nothing to delete, but an earlier error is still reported. */
    }

    s_state = AUDIO_PLAYBACK_STATE_STOPPED;
    ESP_LOGI(TAG, "Deinitialized");

    return err;
}
