#include "wav_parser.h"
#include <string.h>

static uint32_t u32(const uint8_t *p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
         ((uint32_t)p[3] << 24);
}
static uint16_t u16(const uint8_t *p) { return (uint16_t)p[0] | ((uint16_t)p[1] << 8); }

void wav_parser_init(wav_parser_t *p, wav_pcm_sink_fn sink, void *ctx) {
  memset(p, 0, sizeof(*p)); p->sink = sink; p->sink_ctx = ctx;
}

size_t wav_parser_feed(wav_parser_t *p, const uint8_t *data, size_t len,
                       wav_result_t *result) {
  size_t off = 0; *result = WAV_OK;
  while (off < len) {
    if (p->in_data) {
      size_t n = len - off;
      if (n > p->chunk_remaining) n = p->chunk_remaining;
      size_t accepted = p->sink ? p->sink(p->sink_ctx, data + off, n) : n;
      if (accepted > n) accepted = n;
      off += accepted; p->chunk_remaining -= (uint32_t)accepted;
      if (accepted < n) { *result = WAV_WOULD_BLOCK; return off; }
      if (p->chunk_remaining == 0) {
        p->in_data = false; p->chunk_pad = (p->format.data_length & 1u) != 0;
      }
      continue;
    }
    if (p->riff_tail_remaining) {
      size_t n = len - off;
      if (n > p->riff_tail_remaining) n = p->riff_tail_remaining;
      off += n;
      p->riff_tail_remaining -= (uint8_t)n;
      continue;
    }
    if (p->chunk_pad) { p->chunk_pad = false; off++; continue; }
    if (p->chunk_remaining) {
      size_t n = len - off;
      if (n > p->chunk_remaining) n = p->chunk_remaining;
      if (p->chunk_remaining == 16 && memcmp(data + off, "", 0) == 0) {}
      /* fmt is parsed when its complete payload is staged below. */
      if (!p->fmt_seen && p->header[0] == 'f' && p->header[1] == 'm') {
        size_t take = n;
        if (take > sizeof(p->fmt_buf) - p->fmt_used) take = sizeof(p->fmt_buf) - p->fmt_used;
        memcpy(p->fmt_buf + p->fmt_used, data + off, take);
        p->fmt_used += take; off += take; p->chunk_remaining -= (uint32_t)take;
        if (p->fmt_used < sizeof(p->fmt_buf)) continue;
        const uint8_t *f = p->fmt_buf;
        if (u16(f) != 1 || u16(f + 2) != 1 || u16(f + 14) != 16 ||
            (u32(f + 4) != 16000 && u32(f + 4) != 24000) ||
            u16(f + 12) != u16(f + 14) / 8 || u32(f + 8) != u32(f + 4) * u16(f + 12)) {
          *result = WAV_ERROR; return off;
        }
        p->format.sample_rate = u32(f + 4); p->format.channels = 1;
        p->format.bits_per_sample = 16; p->fmt_seen = true; continue;
      }
      off += n; p->chunk_remaining -= (uint32_t)n;
      if (p->chunk_remaining == 0 && p->pad_after_chunk) {
        p->chunk_pad = true; p->pad_after_chunk = false;
      }
      continue;
    }
    while (p->header_used < 8 && off < len) p->header[p->header_used++] = data[off++];
    if (p->header_used < 8) continue;
    uint32_t size = u32(p->header + 4);
    if (!p->riff_seen) {
      if (memcmp(p->header, "RIFF", 4) != 0 || size < 4) { *result = WAV_ERROR; return off; }
      p->riff_seen = true; p->riff_tail_remaining = 4; p->header_used = 0;
      continue;
    }
    if (memcmp(p->header, "data", 4) == 0) {
      if (!p->fmt_seen || size == 0 || (size & 1u)) { *result = WAV_ERROR; return off; }
      p->data_seen = true; p->format.data_length = size; p->chunk_remaining = size; p->in_data = true;
    } else if (memcmp(p->header, "fmt ", 4) == 0) {
      if (size != 16) { *result = WAV_ERROR; return off; }
      p->chunk_remaining = size;
    } else {
      p->chunk_remaining = size; p->pad_after_chunk = (size & 1u) != 0;
    }
    p->header_used = 0;
  }
  return off;
}

wav_result_t wav_parser_finish(wav_parser_t *p) {
  if (!p->riff_seen || !p->fmt_seen || !p->data_seen || p->in_data ||
      p->chunk_remaining || p->header_used) return WAV_ERROR;
  return WAV_OK;
}
