/* StickS3 ES8311 audio path, adapted from esp-claw's Rover S3 board. */
#include "audio_capture.h"
#include "audio_playback.h"
#include "board_sticks3.h"

#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "es8311_codec.h"

#include <limits.h>

static const char *TAG = "audio_sticks3";
static i2c_master_bus_handle_t s_bus;
static i2s_chan_handle_t s_tx;
static i2s_chan_handle_t s_rx;
static esp_codec_dev_handle_t s_mic;
static esp_codec_dev_handle_t s_spk;
static audio_capture_state_t s_capture_state = AUDIO_CAPTURE_STATE_STOPPED;
static audio_playback_state_t s_playback_state = AUDIO_PLAYBACK_STATE_STOPPED;
static int s_play_rate = 24000;
static int64_t s_play_until_us;

static esp_codec_dev_sample_info_t mic_format(void) {
    return (esp_codec_dev_sample_info_t){
        .sample_rate = 16000, .channel = 1, .bits_per_sample = 16
    };
}

static esp_codec_dev_sample_info_t speaker_format(void) {
    return (esp_codec_dev_sample_info_t){
        .sample_rate = s_play_rate, .channel = 1, .bits_per_sample = 16
    };
}

esp_err_t audio_capture_init(const audio_capture_config_t *config) {
    if (s_mic) return ESP_OK;
    if (config && (config->sample_rate != 16000 ||
                   config->bits_per_sample != 16 || config->channel_count != 1)) {
        return ESP_ERR_NOT_SUPPORTED;
    }

    /* Board owns the bus shared with the PMIC for the entire boot. */
    s_bus = board_sticks3_i2c_bus();
    esp_err_t err = s_bus ? ESP_OK : ESP_ERR_INVALID_STATE;
    if (err != ESP_OK) goto fail;

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(BOARD_I2S_PORT, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 8;
    chan_cfg.dma_frame_num = 512;
    chan_cfg.auto_clear_after_cb = true;
    err = i2s_new_channel(&chan_cfg, &s_tx, &s_rx);
    if (err != ESP_OK) goto fail;
    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                      I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = BOARD_I2S_MCLK_GPIO,
            .bclk = BOARD_I2S_BCLK_GPIO,
            .ws = BOARD_I2S_LRCK_GPIO,
            .dout = BOARD_I2S_DOUT_GPIO,
            .din = BOARD_I2S_DIN_GPIO,
        },
    };
    err = i2s_channel_init_std_mode(s_tx, &std_cfg);
    if (err != ESP_OK) goto fail;
    err = i2s_channel_init_std_mode(s_rx, &std_cfg);
    if (err != ESP_OK) goto fail;
    err = i2s_channel_enable(s_rx);
    if (err != ESP_OK) goto fail;
    err = i2s_channel_enable(s_tx);
    if (err != ESP_OK) goto fail;

    audio_codec_i2c_cfg_t i2c_cfg = {
        .port = I2C_NUM_0, .addr = ES8311_CODEC_DEFAULT_ADDR, .bus_handle = s_bus
    };
    const audio_codec_ctrl_if_t *ctrl = audio_codec_new_i2c_ctrl(&i2c_cfg);
    audio_codec_i2s_cfg_t i2s_cfg = {
        .port = BOARD_I2S_PORT, .rx_handle = s_rx, .tx_handle = s_tx
    };
    const audio_codec_data_if_t *data = audio_codec_new_i2s_data(&i2s_cfg);
    if (!ctrl || !data) { err = ESP_FAIL; goto fail; }
    es8311_codec_cfg_t codec_cfg = {
        .ctrl_if = ctrl, .codec_mode = ESP_CODEC_DEV_WORK_MODE_BOTH,
        .pa_pin = -1, .master_mode = false, .use_mclk = true,
    };
    const audio_codec_if_t *codec = es8311_codec_new(&codec_cfg);
    if (!codec) { err = ESP_FAIL; goto fail; }
    s_mic = esp_codec_dev_new(&(esp_codec_dev_cfg_t){
        .dev_type = ESP_CODEC_DEV_TYPE_IN, .codec_if = codec, .data_if = data
    });
    s_spk = esp_codec_dev_new(&(esp_codec_dev_cfg_t){
        .dev_type = ESP_CODEC_DEV_TYPE_OUT, .codec_if = codec, .data_if = data
    });
    if (!s_mic || !s_spk) { err = ESP_FAIL; goto fail; }
    esp_codec_dev_sample_info_t fs = mic_format();
    if (esp_codec_dev_open(s_mic, &fs) != ESP_CODEC_DEV_OK) {
        err = ESP_FAIL; goto fail;
    }
    esp_codec_dev_set_in_gain(s_mic, 30.0f);
    ESP_LOGI(TAG, "ES8311 ready: shared I2S0, MCLK18, mic 16 kHz");
    return ESP_OK;

fail:
    ESP_LOGE(TAG, "ES8311 initialization failed: %s", esp_err_to_name(err));
    audio_capture_deinit();
    return err;
}

esp_err_t audio_capture_start(void) {
    if (!s_mic || s_playback_state == AUDIO_PLAYBACK_STATE_STARTED) return ESP_ERR_INVALID_STATE;
    s_capture_state = AUDIO_CAPTURE_STATE_STARTED;
    return ESP_OK;
}

esp_err_t audio_capture_stop(void) {
    s_capture_state = AUDIO_CAPTURE_STATE_STOPPED;
    return ESP_OK;
}

size_t audio_capture_read(uint8_t *data, size_t size, uint32_t timeout_ms) {
    if (s_capture_state != AUDIO_CAPTURE_STATE_STARTED || !data || !size) return 0;
    size_t got = 0;
    esp_err_t err = i2s_channel_read(s_rx, data, size, &got, timeout_ms);
    if (err != ESP_OK && err != ESP_ERR_TIMEOUT) {
        ESP_LOGW(TAG, "microphone read: %s", esp_err_to_name(err));
    }
    return got;
}

audio_capture_state_t audio_capture_get_state(void) { return s_capture_state; }

esp_err_t audio_capture_deinit(void) {
    s_capture_state = AUDIO_CAPTURE_STATE_STOPPED;
    if (s_mic) { esp_codec_dev_close(s_mic); esp_codec_dev_delete(s_mic); s_mic = NULL; }
    if (s_spk) { esp_codec_dev_close(s_spk); esp_codec_dev_delete(s_spk); s_spk = NULL; }
    if (s_rx) { i2s_channel_disable(s_rx); i2s_del_channel(s_rx); s_rx = NULL; }
    if (s_tx) { i2s_channel_disable(s_tx); i2s_del_channel(s_tx); s_tx = NULL; }
    s_bus = NULL; /* Borrowed; PMIC still uses this bus. */
    return ESP_OK;
}

esp_err_t audio_playback_init(const audio_playback_config_t *config) {
    if (!s_mic || !s_spk) return ESP_ERR_INVALID_STATE;
    if (config) {
        if (config->sample_rate != 24000 || config->bits_per_sample != 16 ||
            config->channel_count != 1) return ESP_ERR_NOT_SUPPORTED;
        s_play_rate = config->sample_rate;
    }
    return ESP_OK;
}

esp_err_t audio_playback_start(void) {
    if (!s_mic || !s_spk) return ESP_ERR_INVALID_STATE;
    if (s_playback_state == AUDIO_PLAYBACK_STATE_STARTED) return ESP_OK;
    s_capture_state = AUDIO_CAPTURE_STATE_STOPPED;
    esp_codec_dev_close(s_mic);
    esp_codec_dev_sample_info_t fs = speaker_format();
    int ret = esp_codec_dev_open(s_spk, &fs);
    if (ret != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "speaker open at %d Hz failed: %d", s_play_rate, ret);
        fs = mic_format();
        esp_codec_dev_open(s_mic, &fs);
        return ESP_FAIL;
    }
    ret = esp_codec_dev_set_out_vol(s_spk, 80);
    if (ret != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "speaker volume failed: %d", ret);
        esp_codec_dev_close(s_spk);
        fs = mic_format();
        esp_codec_dev_open(s_mic, &fs);
        return ESP_FAIL;
    }
    s_play_until_us = esp_timer_get_time();
    s_playback_state = AUDIO_PLAYBACK_STATE_STARTED;
    ESP_LOGI(TAG, "speaker ready: %d Hz, volume 80", s_play_rate);
    return ESP_OK;
}

esp_err_t audio_playback_write(const uint8_t *data, size_t size, uint32_t timeout_ms) {
    (void)timeout_ms; /* codec driver uses a bounded 1000 ms DMA wait */
    if (s_playback_state != AUDIO_PLAYBACK_STATE_STARTED) return ESP_ERR_INVALID_STATE;
    if (!data || !size || (size & 1) || size > INT_MAX) return ESP_ERR_INVALID_ARG;
    int64_t before = esp_timer_get_time();
    int ret = esp_codec_dev_write(s_spk, (void *)data, (int)size);
    if (ret != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "speaker write failed: %d", ret);
        return ESP_FAIL;
    }
    if (s_play_until_us < before) s_play_until_us = before;
    s_play_until_us += (int64_t)(size / sizeof(int16_t)) * 1000000 / s_play_rate;
    return ESP_OK;
}

int audio_playback_get_buffer_level(void) {
    if (s_playback_state != AUDIO_PLAYBACK_STATE_STARTED) return 0;
    int64_t remaining_us = s_play_until_us - esp_timer_get_time();
    return remaining_us > 0 ? (int)((remaining_us * s_play_rate + 999999) / 1000000) : 0;
}

audio_playback_state_t audio_playback_get_state(void) { return s_playback_state; }

esp_err_t audio_playback_stop(void) {
    if (s_playback_state != AUDIO_PLAYBACK_STATE_STARTED) return ESP_OK;
    esp_codec_dev_close(s_spk);
    esp_codec_dev_sample_info_t fs = mic_format();
    int ret = esp_codec_dev_open(s_mic, &fs);
    s_playback_state = AUDIO_PLAYBACK_STATE_STOPPED;
    s_play_until_us = 0;
    if (ret != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "microphone reopen failed: %d", ret);
        return ESP_FAIL;
    }
    return ESP_OK;
}

esp_err_t audio_playback_deinit(void) { return audio_capture_deinit(); }
