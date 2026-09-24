#ifndef SCREEN_FONT_H
#define SCREEN_FONT_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
  SCREEN_FONT_SMALL,
  SCREEN_FONT_HINT,
  SCREEN_FONT_TITLE,
} screen_font_size_t;

/* Return -1 when text contains a glyph that the bundled font cannot draw. */
int screen_font_measure(screen_font_size_t size, const char *text);
bool screen_font_draw_centered(uint16_t *pixels, int width, int height,
                               int center_x, int top_y, screen_font_size_t size,
                               const char *text, uint16_t color);

#endif
