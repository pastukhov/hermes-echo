#include "screen_ui.h"
#include "screen_font.h"
#include <string.h>

screen_ui_view_t screen_ui_view_with_phase(state_t state,
                                           screen_processing_phase_t phase) {
  switch (state) {
    case STATE_RECORDING:
      return (screen_ui_view_t){"СЛУШАЮ", "ОТПУСТИТЕ\nДЛЯ ОТПРАВКИ", 0xF50A, SCREEN_ICON_LISTENING};
    case STATE_PROCESSING:
      if (phase == SCREEN_PROCESSING_TRANSCRIBING)
        return (screen_ui_view_t){"СЛЫШУ", "РАСПОЗНАЮ РЕЧЬ", 0xF5A8, SCREEN_ICON_THINKING};
      if (phase == SCREEN_PROCESSING_SYNTHESIZING)
        return (screen_ui_view_t){"ГОТОВЛЮ", "ОТВЕТ", 0xF5A8, SCREEN_ICON_THINKING};
      return (screen_ui_view_t){"ДУМАЮ", "ОБРАБАТЫВАЮ", 0xF5A8, SCREEN_ICON_THINKING};
    case STATE_PLAYING:
      return (screen_ui_view_t){"ОТВЕЧАЮ", "ЗАЖМИТЕ ДЛЯ\nСТОПА", 0x38B8, SCREEN_ICON_SPEAKING};
    case STATE_ERROR:
      return (screen_ui_view_t){"ОШИБКА", "НАЖМИТЕ ДЛЯ\nСБРОСА", 0xF50A, SCREEN_ICON_ERROR};
    case STATE_BOOT:
      return (screen_ui_view_t){"ЗАПУСК", "ПОДКЛЮЧАЮСЬ", 0x74BF, SCREEN_ICON_IDLE};
    case STATE_IDLE:
    default:
      return (screen_ui_view_t){"ГОТОВ", "НАЖМИТЕ ДЛЯ\nСТАРТА", 0x74BF, SCREEN_ICON_IDLE};
  }
}

screen_ui_view_t screen_ui_view(state_t state) {
  return screen_ui_view_with_phase(state, SCREEN_PROCESSING_THINKING);
}


screen_ui_view_t screen_ui_view_with_network(state_t state, screen_processing_phase_t phase,
                                             bool wifi_connected, bool vpn_ready) {
  if (state == STATE_IDLE) {
    if (!wifi_connected)
      return (screen_ui_view_t){"СЕТЬ", "ОЖИДАЮ WI-FI", 0xF5A8, SCREEN_ICON_THINKING};
    if (!vpn_ready)
      return (screen_ui_view_t){"VPN", "ОЖИДАЮ VPN", 0xF5A8, SCREEN_ICON_THINKING};
  }
  return screen_ui_view_with_phase(state, phase);
}


// The generated AP name is ASCII and split at a word boundary to fit the LCD.
bool screen_ui_draw_setup(uint16_t *pixels, int width, int height, const char *ssid) {
  if (!pixels || width < 135 || height < 240 || !ssid || strlen(ssid) > 32)
    return false;
  char first[33];
  const char *second = strstr(ssid, "Setup-");
  size_t length = second ? (size_t)(second - ssid) : strlen(ssid);
  memcpy(first, ssid, length);
  first[length] = '\0';
  if (!second) second = "";
  const struct {
    const char *text;
    int y;
    screen_font_size_t font;
    uint16_t color;
  } lines[] = {
    {"НАСТРОЙКА", 51, SCREEN_FONT_HINT, 0xF5A8},
    {"ТОЧКА ДОСТУПА", 76, SCREEN_FONT_SMALL, 0xEF9F},
    {"Сеть Wi-Fi:", 108, SCREEN_FONT_SMALL, 0x74B3},
    {first, 126, SCREEN_FONT_SMALL, 0xEF9F},
    {second, 143, SCREEN_FONT_SMALL, 0xEF9F},
    {"Адрес в браузере:", 178, SCREEN_FONT_SMALL, 0x74B3},
    {"192.168.4.1", 195, SCREEN_FONT_HINT, 0xEF9F},
  };
  for (unsigned i = 0; i < sizeof(lines) / sizeof(lines[0]); ++i) {
    int measured = screen_font_measure(lines[i].font, lines[i].text);
    if (measured < 0 || measured > width - 20 ||
        !screen_font_draw_centered(pixels, width, height, width / 2, lines[i].y,
                                   lines[i].font, lines[i].text, lines[i].color))
      return false;
  }
  return true;
}
