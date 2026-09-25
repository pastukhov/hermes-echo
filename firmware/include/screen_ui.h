#ifndef SCREEN_UI_H
#define SCREEN_UI_H

#include <stdint.h>
#include <stdbool.h>
#include "state_machine.h"

typedef enum {
  SCREEN_ICON_IDLE,
  SCREEN_ICON_LISTENING,
  SCREEN_ICON_THINKING,
  SCREEN_ICON_SPEAKING,
  SCREEN_ICON_ERROR
} screen_icon_t;

typedef enum {
  SCREEN_PROCESSING_TRANSCRIBING,
  SCREEN_PROCESSING_THINKING,
  SCREEN_PROCESSING_SYNTHESIZING,
} screen_processing_phase_t;

typedef struct {
  const char *title;
  const char *hint;
  uint16_t accent;
  screen_icon_t icon;
} screen_ui_view_t;

bool screen_ui_draw_setup(uint16_t *pixels, int width, int height, const char *ssid);

screen_ui_view_t screen_ui_view(state_t state);
screen_ui_view_t screen_ui_view_with_network(state_t state, screen_processing_phase_t phase,
                                             bool wifi_connected, bool vpn_ready);
screen_ui_view_t screen_ui_view_with_phase(state_t state,
                                           screen_processing_phase_t phase);

#endif
