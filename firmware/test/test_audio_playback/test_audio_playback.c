/*
 * test_audio_playback.c - Smoke test for audio_playback module
 * 
 * This test verifies that the audio_playback module:
 * 1. Initializes I2S correctly
 * 2. Can start/stop playback
 * 3. Accepts chunked audio data
 * 
 * Note: This test is designed for ESP-IDF build environment.
 * Build in ESP-IDF: idf.py build
 * Flash and monitor: idf.py flash monitor
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "audio_playback.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define TEST_SAMPLE_RATE 16000
#define TEST_CHANNELS 1
#define TEST_CHUNK_SIZE 512  /* frames */
#define TEST_CHUNK_BYTES (TEST_CHUNK_SIZE * 2 * TEST_CHANNELS)  /* S16LE = 2 bytes per sample */

/* Generate a simple 1kHz sine wave test pattern */
static void generate_sine_wave(int16_t *buffer, size_t num_frames, float freq_hz) {
    const float sample_rate = (float)TEST_SAMPLE_RATE;
    const float two_pi = 8.0 * atan(1.0);
    
    for (size_t i = 0; i < num_frames; i++) {
        float t = (float)i / sample_rate;
        float value = sinf(two_pi * freq_hz * t);
        /* Scale to 80% of max to avoid clipping */
        buffer[i] = (int16_t)(value * 0.8 * 32767);
    }
}

static void generate_noise(int16_t *buffer, size_t num_frames) {
    for (size_t i = 0; i < num_frames; i++) {
        /* Generate pseudo-random noise */
        buffer[i] = (int16_t)((rand() % 65536) - 32768);
    }
}

static int test_init(void) {
    printf("[TEST] audio_playback_init...\n");
    
    audio_playback_config_t config = {
        .sample_rate = TEST_SAMPLE_RATE,
        .bits_per_sample = 16,
        .channel_count = TEST_CHANNELS,
        .buffer_frame_size = TEST_CHUNK_SIZE,
        .queue_size = 4
    };
    
    esp_err_t err = audio_playback_init(&config);
    if (err != ESP_OK) {
        printf("[FAIL] audio_playback_init returned error: %d\n", err);
        return 1;
    }
    
    printf("[PASS] audio_playback_init succeeded\n");
    return 0;
}

static int test_start_stop(void) {
    printf("[TEST] audio_playback_start/stop...\n");
    
    esp_err_t err = audio_playback_start();
    if (err != ESP_OK) {
        printf("[FAIL] audio_playback_start returned error: %d\n", err);
        return 1;
    }
    
    audio_playback_state_t state = audio_playback_get_state();
    if (state != AUDIO_PLAYBACK_STATE_STARTED) {
        printf("[FAIL] Expected STARTED state, got %d\n", state);
        return 1;
    }
    
    err = audio_playback_stop();
    if (err != ESP_OK) {
        printf("[FAIL] audio_playback_stop returned error: %d\n", err);
        return 1;
    }
    
    state = audio_playback_get_state();
    if (state != AUDIO_PLAYBACK_STATE_STOPPED) {
        printf("[FAIL] Expected STOPPED state, got %d\n", state);
        return 1;
    }
    
    printf("[PASS] Start/stop cycle succeeded\n");
    return 0;
}

static int test_chunked_playback(void) {
    printf("[TEST] Chunked audio playback...\n");
    
    size_t chunk_bytes = TEST_CHUNK_BYTES;
    int16_t *buffer = (int16_t *)malloc(chunk_bytes);
    if (!buffer) {
        printf("[FAIL] Failed to allocate buffer\n");
        return 1;
    }
    
    esp_err_t err = audio_playback_start();
    if (err != ESP_OK) {
        free(buffer);
        printf("[FAIL] audio_playback_start failed\n");
        return 1;
    }
    
    /* Generate and write multiple chunks */
    int num_chunks = 10;
    for (int i = 0; i < num_chunks; i++) {
        generate_sine_wave(buffer, TEST_CHUNK_SIZE, 440.0 + (i * 50.0));  /* Rising tone */
        
        err = audio_playback_write((const uint8_t *)buffer, chunk_bytes, 1000);
        if (err != ESP_OK) {
            free(buffer);
            audio_playback_stop();
            printf("[FAIL] audio_playback_write failed on chunk %d: %d\n", i, err);
            return 1;
        }
        
        int level = audio_playback_get_buffer_level();
        printf("  Chunk %d/%d written, buffer level: %d frames\n", i + 1, num_chunks, level);
    }
    
    /* Wait a bit for playback to complete */
    printf("[INFO] Waiting for playback to complete...\n");
    vTaskDelay(pdMS_TO_TICKS(2000));
    
    err = audio_playback_stop();
    if (err != ESP_OK) {
        free(buffer);
        printf("[FAIL] audio_playback_stop failed: %d\n", err);
        return 1;
    }
    
    free(buffer);
    printf("[PASS] Chunked playback succeeded\n");
    return 0;
}

static int test_deinit(void) {
    printf("[TEST] audio_playback_deinit...\n");
    
    esp_err_t err = audio_playback_deinit();
    if (err != ESP_OK) {
        printf("[FAIL] audio_playback_deinit returned error: %d\n", err);
        return 1;
    }
    
    printf("[PASS] Deinit succeeded\n");
    return 0;
}

/* Main task for ESP-IDF */
void app_main(void) {
    printf("========================================\n");
    printf("Audio Playback Module Smoke Test\n");
    printf("========================================\n");
    printf("Configuration: %d Hz, %d channels, %d frames/chunk\n",
           TEST_SAMPLE_RATE, TEST_CHANNELS, TEST_CHUNK_SIZE);
    printf("\n");
    
    int failures = 0;
    
    /* Test sequence */
    failures += test_init();
    failures += test_start_stop();
    failures += test_chunked_playback();
    failures += test_deinit();
    
    printf("\n========================================\n");
    if (failures == 0) {
        printf("ALL TESTS PASSED\n");
    } else {
        printf("TESTS FAILED: %d\n", failures);
    }
    printf("========================================\n");
}
