#include <string.h>
#include <stdio.h>
#include <unity.h>
#include "wav_parser.h"

static uint8_t out[64]; static size_t out_len;
static size_t sink(void *ctx, const uint8_t *d, size_t n) { (void)ctx; memcpy(out + out_len, d, n); out_len += n; return n; }

static size_t make_wav(uint8_t *b) {
  uint8_t h[] = {'R','I','F','F',40,0,0,0,'W','A','V','E','J','U','N','K',1,0,0,0,0,0,
                 'f','m','t',' ',16,0,0,0,1,0,1,0,0x80,0x3e,0,0,0x00,0x7d,0,0,2,0,16,0,
                 'd','a','t','a',4,0,0,0,1,2,3,4};
  memcpy(b,h,sizeof(h)); return sizeof(h);
}

void test_wav_parser_accepts_every_split(void) {
  uint8_t wav[64]; size_t n = make_wav(wav);
  for (size_t split = 1; split < n; split++) {
    wav_parser_t p; wav_result_t r; out_len = 0; wav_parser_init(&p, sink, NULL);
    size_t a = wav_parser_feed(&p, wav, split, &r);
    size_t cursor = a;
    while (cursor < split) { size_t q = wav_parser_feed(&p, wav + cursor, split - cursor, &r); TEST_ASSERT_GREATER_THAN_UINT(0, q); cursor += q; }
    cursor = split;
    while (cursor < n) { size_t q = wav_parser_feed(&p, wav + cursor, n - cursor, &r); TEST_ASSERT_GREATER_THAN_UINT(0, q); cursor += q; }
    TEST_ASSERT_EQUAL(WAV_OK, wav_parser_finish(&p));
    TEST_ASSERT_EQUAL_UINT(4, out_len);
  }
}

void test_wav_parser_rejects_truncated(void) {
  uint8_t wav[64]; size_t n = make_wav(wav); wav_parser_t p; wav_result_t r;
  wav_parser_init(&p, sink, NULL); wav_parser_feed(&p, wav, n - 1, &r);
  TEST_ASSERT_EQUAL(WAV_ERROR, wav_parser_finish(&p));
}

void test_wav_parser_rejects_non_pcm(void) {
  uint8_t wav[64]; size_t n = make_wav(wav); wav[29] = 2; wav_parser_t p; wav_result_t r;
  wav_parser_init(&p, sink, NULL); wav_parser_feed(&p, wav, n, &r); TEST_ASSERT_EQUAL(WAV_ERROR, r);
}

int main(void) { UNITY_BEGIN(); RUN_TEST(test_wav_parser_accepts_every_split); RUN_TEST(test_wav_parser_rejects_truncated); RUN_TEST(test_wav_parser_rejects_non_pcm); return UNITY_END(); }
