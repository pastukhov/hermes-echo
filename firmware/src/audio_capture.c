/*
 * audio_capture.c - Streaming PCM S16LE microphone capture through I2S
 * (M1-03: record -> ring buffer).
 *
 * Uses the ESP-IDF v5.x I2S channel API (i2s_common.h / i2s_std.h), same as
 * audio_playback.c (M1-04). Mirrors that module's structure deliberately so
 * the two halves of the audio path stay easy to reason about together.
 *
 * Hardware note: the ATOM Echo's microphone (PDM-capable SPM1423, wired as
 * an I2S source here) and speaker share the SoC's I2S signal group but are
 * driven as two independent I2S peripheral instances (RX on I2S_NUM_1, TX
 * on I2S_NUM_0 in audio_playback.c) so record and playback -- which never
 * run concurrently in this state machine (RECORDING finishes and closes
 * before PLAYING starts) -- each get a plain, independent master clock
 * instead of one shared full-duplex channel. This is a deliberate M1
 * simplification; revisit if real-hardware verification (device currently
 * disconnected, see docs/development.md) shows clock contention on the
 * shared bus.
 */

#include "audio_capture.h"
#include "board_sticks3.h"
#include "driver/i2s_common.h"
#include "driver/i2s_std.h"
#include "esp_log.h"

#include <string.h>

#define TAG "audio_capture"

/* Default configuration values -- match audio_playback.c so a captured
 * chunk can be handed straight to playback without resampling. */
#define DEFAULT_SAMPLE_RATE       16000
#define DEFAULT_BITS_PER_SAMPLE   16
#define DEFAULT_CHANNEL_COUNT     1
#define DEFAULT_BUFFER_FRAME_SIZE 512
#define DEFAULT_QUEUE_SIZE        8

/* Independent I2S peripheral from audio_playback.c's TX (I2S_NUM_0); see
 * file header. Pins from board_atom_echo.h. */
#define I2S_PORT                  1
#define I2S_BCLK_PIN              BOARD_I2S_BCLK_PIN
#define I2S_WS_PIN                BOARD_I2S_WS_PIN
#define I2S_DIN_PIN               BOARD_I2S_DIN_PIN

#define FRAME_SIZE(ch, bits)      (((bits) / 8) * (ch))

static audio_capture_state_t s_state = AUDIO_CAPTURE_STATE_STOPPED;
static audio_capture_config_t s_config = {
    .sample_rate = DEFAULT_SAMPLE_RATE,
    .bits_per_sample = DEFAULT_BITS_PER_SAMPLE,
    .channel_count = DEFAULT_CHANNEL_COUNT,
    .buffer_frame_size = DEFAULT_BUFFER_FRAME_SIZE,
    .queue_size = DEFAULT_QUEUE_SIZE
};

static i2s_chan_handle_t s_rx_chan = NULL;

static i2s_data_bit_width_t bits_to_i2s_width(int bits) {
    switch (bits) {
        case 16: return I2S_DATA_BIT_WIDTH_16BIT;
        case 24: return I2S_DATA_BIT_WIDTH_24BIT;
        case 32: return I2S_DATA_BIT_WIDTH_32BIT;
        default: return I2S_DATA_BIT_WIDTH_16BIT;
    }
}

esp_err_t audio_capture_init(const audio_capture_config_t *config) {
    ESP_LOGI(TAG, "Initializing I2S audio capture");

    if (s_state != AUDIO_CAPTURE_STATE_STOPPED) {
        ESP_LOGW(TAG, "Already started, stopping first");
        audio_capture_stop();
    }
    if (s_rx_chan != NULL) {
        ESP_LOGW(TAG, "Already initialized, deinitializing first");
        audio_capture_deinit();
    }

    if (config) {
        s_config = *config;
    }
    if (s_config.sample_rate <= 0) {
        s_config.sample_rate = DEFAULT_SAMPLE_RATE;
    }
    if (s_config.bits_per_sample != 16 && s_config.bits_per_sample != 24
        && s_config.bits_per_sample != 32) {
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

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_PORT, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = (uint32_t)s_config.queue_size;
    chan_cfg.dma_frame_num = (uint32_t)s_config.buffer_frame_size;
    /* RX-only channel: pass NULL for the TX handle. */
    esp_err_t err = i2s_new_channel(&chan_cfg, NULL, &s_rx_chan);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to allocate I2S channel: %s", esp_err_to_name(err));
        s_rx_chan = NULL;
        return err;
    }

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
            .dout = I2S_GPIO_UNUSED,
            .din = I2S_DIN_PIN,
            .invert_flags = {
                .mclk_inv = 0,
                .bclk_inv = 0,
                .ws_inv = 0,
            },
        },
    };

    err = i2s_channel_init_std_mode(s_rx_chan, &std_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize I2S std mode: %s", esp_err_to_name(err));
        i2s_del_channel(s_rx_chan);
        s_rx_chan = NULL;
        return err;
    }

    s_state = AUDIO_CAPTURE_STATE_STOPPED;
    ESP_LOGI(TAG, "Initialized: rate=%dHz, bits=%d, channels=%d",
             s_config.sample_rate, s_config.bits_per_sample,
             s_config.channel_count);

    return ESP_OK;
}

esp_err_t audio_capture_start(void) {
    if (s_rx_chan == NULL) {
        ESP_LOGE(TAG, "Not initialized");
        return ESP_ERR_INVALID_STATE;
    }
    if (s_state == AUDIO_CAPTURE_STATE_STARTED) {
        ESP_LOGW(TAG, "Already started");
        return ESP_OK;
    }

    esp_err_t err = i2s_channel_enable(s_rx_chan);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to enable I2S RX: %s", esp_err_to_name(err));
        return err;
    }

    s_state = AUDIO_CAPTURE_STATE_STARTED;
    ESP_LOGI(TAG, "Capture started");
    return ESP_OK;
}

esp_err_t audio_capture_stop(void) {
    if (s_rx_chan == NULL) {
        ESP_LOGE(TAG, "Not initialized");
        return ESP_ERR_INVALID_STATE;
    }
    if (s_state != AUDIO_CAPTURE_STATE_STARTED) {
        ESP_LOGW(TAG, "Not running");
        return ESP_OK;
    }

    esp_err_t err = i2s_channel_disable(s_rx_chan);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to disable I2S RX: %s", esp_err_to_name(err));
    }

    s_state = AUDIO_CAPTURE_STATE_STOPPED;
    ESP_LOGI(TAG, "Capture stopped");
    return ESP_OK;
}

size_t audio_capture_read(uint8_t *data, size_t size, uint32_t timeout_ms) {
    if (s_rx_chan == NULL || s_state != AUDIO_CAPTURE_STATE_STARTED) {
        return 0;
    }
    if (data == NULL || size == 0) {
        return 0;
    }

    size_t bytes_read = 0;
    esp_err_t err = i2s_channel_read(s_rx_chan, data, size, &bytes_read, timeout_ms);
    if (err == ESP_ERR_TIMEOUT) {
        /* Nothing ready yet within the (short) poll timeout -- normal when
         * called once per app tick, not an error. */
        return 0;
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "I2S read failed: %s", esp_err_to_name(err));
        return 0;
    }
    return bytes_read;
}

audio_capture_state_t audio_capture_get_state(void) {
    return s_state;
}

esp_err_t audio_capture_deinit(void) {
    ESP_LOGI(TAG, "Deinitializing");

    if (s_state == AUDIO_CAPTURE_STATE_STARTED) {
        i2s_channel_disable(s_rx_chan);
        s_state = AUDIO_CAPTURE_STATE_STOPPED;
    }

    esp_err_t err = ESP_OK;
    if (s_rx_chan != NULL) {
        err = i2s_del_channel(s_rx_chan);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Failed to delete I2S channel: %s", esp_err_to_name(err));
        }
        s_rx_chan = NULL;
    }

    s_state = AUDIO_CAPTURE_STATE_STOPPED;
    return err;
}
