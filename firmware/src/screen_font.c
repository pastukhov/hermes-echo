#include "screen_font.h"
#include "screen_font_data.h"

#include <stddef.h>

static bool next_codepoint(const char **text, uint32_t *codepoint) {
  const unsigned char *p = (const unsigned char *)*text;
  if (!*p) return false;
  if (p[0] < 0x80) {
    *codepoint = p[0];
    *text += 1;
    return true;
  }
  if ((p[0] & 0xe0) == 0xc0 && (p[1] & 0xc0) == 0x80) {
    *codepoint = ((uint32_t)(p[0] & 0x1f) << 6) | (p[1] & 0x3f);
    *text += 2;
    return true;
  }
  if ((p[0] & 0xf0) == 0xe0 && (p[1] & 0xc0) == 0x80 &&
      (p[2] & 0xc0) == 0x80) {
    *codepoint = ((uint32_t)(p[0] & 0x0f) << 12) |
                 ((uint32_t)(p[1] & 0x3f) << 6) | (p[2] & 0x3f);
    *text += 3;
    return true;
  }
  return false;
}

static const screen_font_glyph_t *find_glyph(const screen_font_data_t *font,
                                             uint32_t codepoint) {
  if (!font || codepoint > 0xffff) return NULL;
  size_t lo = 0, hi = font->glyph_count;
  while (lo < hi) {
    size_t mid = lo + (hi - lo) / 2;
    if (font->glyphs[mid].codepoint < codepoint) lo = mid + 1;
    else hi = mid;
  }
  return lo < font->glyph_count && font->glyphs[lo].codepoint == codepoint
             ? &font->glyphs[lo] : NULL;
}

int screen_font_measure(screen_font_size_t size, const char *text) {
  const screen_font_data_t *font = screen_font_data_get(size);
  if (!font || !text) return -1;
  int width = 0;
  while (*text) {
    uint32_t codepoint;
    if (!next_codepoint(&text, &codepoint)) return -1;
    const screen_font_glyph_t *glyph = find_glyph(font, codepoint);
    if (!glyph) return -1;
    width += glyph->advance;
  }
  return width;
}

static uint16_t blend565(uint16_t background, uint16_t foreground,
                         uint8_t alpha) {
  if (alpha == 15) return foreground;
  int inv = 15 - alpha;
  int r = (((background >> 11) & 31) * inv +
           ((foreground >> 11) & 31) * alpha + 7) / 15;
  int g = (((background >> 5) & 63) * inv +
           ((foreground >> 5) & 63) * alpha + 7) / 15;
  int b = ((background & 31) * inv + (foreground & 31) * alpha + 7) / 15;
  return (uint16_t)((r << 11) | (g << 5) | b);
}

bool screen_font_draw_centered(uint16_t *pixels, int width, int height,
                               int center_x, int top_y, screen_font_size_t size,
                               const char *text, uint16_t color) {
  int text_width = screen_font_measure(size, text);
  const screen_font_data_t *font = screen_font_data_get(size);
  if (!pixels || width <= 0 || height <= 0 || !font || text_width < 0)
    return false;
  int pen_x = center_x - text_width / 2;
  while (*text) {
    uint32_t codepoint;
    if (!next_codepoint(&text, &codepoint)) return false;
    const screen_font_glyph_t *glyph = find_glyph(font, codepoint);
    if (!glyph) return false;
    for (int gy = 0; gy < glyph->height; ++gy) {
      int y = top_y + glyph->top + gy;
      if (y < 0 || y >= height) continue;
      for (int gx = 0; gx < glyph->width; ++gx) {
        int x = pen_x + glyph->left + gx;
        if (x < 0 || x >= width) continue;
        size_t index = (size_t)gy * glyph->width + gx;
        uint8_t packed = font->bitmap[glyph->offset + index / 2];
        uint8_t alpha = index & 1 ? packed & 15 : packed >> 4;
        if (alpha) {
          uint16_t *pixel = &pixels[(size_t)y * width + x];
          *pixel = blend565(*pixel, color, alpha);
        }
      }
    }
    pen_x += glyph->advance;
  }
  return true;
}
