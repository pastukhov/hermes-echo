#include "screen_ui.h"

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
