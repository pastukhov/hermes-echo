#include "screen_ui.h"

screen_ui_view_t screen_ui_view(state_t state) {
  switch (state) {
    case STATE_RECORDING:
      return (screen_ui_view_t){"СЛУШАЮ", "ОТПУСТИТЕ ДЛЯ ОТПРАВКИ", 0xF50A, SCREEN_ICON_LISTENING};
    case STATE_PROCESSING:
      return (screen_ui_view_t){"ДУМАЮ", "ОБРАБАТЫВАЮ", 0xF5A8, SCREEN_ICON_THINKING};
    case STATE_PLAYING:
      return (screen_ui_view_t){"ОТВЕЧАЮ", "ЗАЖМИТЕ ДЛЯ СТОПА", 0x38B8, SCREEN_ICON_SPEAKING};
    case STATE_ERROR:
      return (screen_ui_view_t){"ОШИБКА", "ПОВТОРИТЕ ПОЗЖЕ", 0xF50A, SCREEN_ICON_ERROR};
    case STATE_BOOT:
      return (screen_ui_view_t){"ЗАПУСК", "ПОДКЛЮЧАЮСЬ", 0x74BF, SCREEN_ICON_IDLE};
    case STATE_IDLE:
    default:
      return (screen_ui_view_t){"ГОТОВ", "НАЖМИТЕ ДЛЯ СТАРТА", 0x74BF, SCREEN_ICON_IDLE};
  }
}
