#include <unity.h>
#include "screen_ui.h"
#include "../test_main/fakes/hw_fakes.c"

void setUp(void) {}
void tearDown(void) {}

void test_each_voice_state_has_clear_screen_copy_and_accent(void) {
  const screen_ui_view_t boot = screen_ui_view(STATE_BOOT);
  const screen_ui_view_t idle = screen_ui_view(STATE_IDLE);
  const screen_ui_view_t recording = screen_ui_view(STATE_RECORDING);
  const screen_ui_view_t processing = screen_ui_view(STATE_PROCESSING);
  const screen_ui_view_t playing = screen_ui_view(STATE_PLAYING);
  const screen_ui_view_t error = screen_ui_view(STATE_ERROR);

  TEST_ASSERT_EQUAL_STRING("ЗАПУСК", boot.title);
  TEST_ASSERT_EQUAL_STRING("ПОДКЛЮЧАЮСЬ", boot.hint);
  TEST_ASSERT_EQUAL_STRING("ГОТОВ", idle.title);
  TEST_ASSERT_EQUAL_STRING("НАЖМИТЕ ДЛЯ СТАРТА", idle.hint);
  TEST_ASSERT_EQUAL_STRING("СЛУШАЮ", recording.title);
  TEST_ASSERT_EQUAL_STRING("ДУМАЮ", processing.title);
  TEST_ASSERT_EQUAL_STRING("ОБРАБАТЫВАЮ", processing.hint);
  TEST_ASSERT_EQUAL_STRING("ОТВЕЧАЮ", playing.title);
  TEST_ASSERT_EQUAL_STRING("ЗАЖМИТЕ ДЛЯ СТОПА", playing.hint);
  TEST_ASSERT_EQUAL_STRING("ОШИБКА", error.title);
  TEST_ASSERT_EQUAL_STRING("ОТПУСТИТЕ ДЛЯ ОТПРАВКИ", recording.hint);
  TEST_ASSERT_EQUAL_STRING("ПОВТОРИТЕ ПОЗЖЕ", error.hint);
  TEST_ASSERT_NOT_EQUAL(idle.accent, recording.accent);
  TEST_ASSERT_NOT_EQUAL(recording.accent, processing.accent);
  TEST_ASSERT_NOT_EQUAL(processing.accent, playing.accent);
  TEST_ASSERT_NOT_EQUAL(playing.accent, error.accent);
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_each_voice_state_has_clear_screen_copy_and_accent);
  return UNITY_END();
}
