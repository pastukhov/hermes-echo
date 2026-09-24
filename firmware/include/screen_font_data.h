#ifndef SCREEN_FONT_DATA_H
#define SCREEN_FONT_DATA_H

#include <stdint.h>
#include "screen_font.h"

typedef struct {
  uint16_t codepoint;
  uint8_t width;
  uint8_t height;
  int8_t left;
  int8_t top;
  uint8_t advance;
  uint32_t offset;
} screen_font_glyph_t;

typedef struct {
  const screen_font_glyph_t *glyphs;
  const uint8_t *bitmap;
  uint16_t glyph_count;
} screen_font_data_t;

const screen_font_data_t *screen_font_data_get(screen_font_size_t size);

#endif
