#ifndef WAV_PARSER_H
#define WAV_PARSER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum { WAV_OK = 0, WAV_WOULD_BLOCK = 1, WAV_ERROR = -1 } wav_result_t;

typedef struct {
  uint32_t sample_rate;
  uint16_t channels;
  uint16_t bits_per_sample;
  uint32_t data_length;
} wav_audio_format_t;

typedef size_t (*wav_pcm_sink_fn)(void *ctx, const uint8_t *data, size_t len);

typedef struct {
  uint8_t header[8];
  uint8_t fmt_buf[16];
  size_t fmt_used;
  size_t header_used;
  uint32_t chunk_remaining;
  bool chunk_pad;
  bool pad_after_chunk;
  bool riff_seen;
  uint8_t riff_tail_remaining;
  bool fmt_seen;
  bool data_seen;
  bool in_data;
  wav_audio_format_t format;
  wav_pcm_sink_fn sink;
  void *sink_ctx;
} wav_parser_t;

void wav_parser_init(wav_parser_t *p, wav_pcm_sink_fn sink, void *ctx);
size_t wav_parser_feed(wav_parser_t *p, const uint8_t *data, size_t len,
                       wav_result_t *result);
wav_result_t wav_parser_finish(wav_parser_t *p);

#endif
