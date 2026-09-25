#include <unity.h>
#include <string.h>
#include "screen_font.h"
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
  TEST_ASSERT_EQUAL_STRING("НАЖМИТЕ ДЛЯ\nСТАРТА", idle.hint);
  TEST_ASSERT_EQUAL_STRING("СЛУШАЮ", recording.title);
  TEST_ASSERT_EQUAL_STRING("ДУМАЮ", processing.title);
  TEST_ASSERT_EQUAL_STRING("ОБРАБАТЫВАЮ", processing.hint);
  TEST_ASSERT_EQUAL_STRING("ОТВЕЧАЮ", playing.title);
  TEST_ASSERT_EQUAL_STRING("ЗАЖМИТЕ ДЛЯ\nСТОПА", playing.hint);
  TEST_ASSERT_EQUAL_STRING("ОШИБКА", error.title);
  TEST_ASSERT_EQUAL_STRING("ОТПУСТИТЕ\nДЛЯ ОТПРАВКИ", recording.hint);
  TEST_ASSERT_EQUAL_STRING("НАЖМИТЕ ДЛЯ\nСБРОСА", error.hint);
  TEST_ASSERT_NOT_EQUAL(idle.accent, recording.accent);
  TEST_ASSERT_NOT_EQUAL(recording.accent, processing.accent);
  TEST_ASSERT_NOT_EQUAL(processing.accent, playing.accent);
  TEST_ASSERT_NOT_EQUAL(playing.accent, error.accent);
}

void test_montserrat_draws_cyrillic_letters_distinctly(void) {
  uint16_t o[135 * 40] = {0};
  uint16_t n[135 * 40] = {0};
  TEST_ASSERT_TRUE(screen_font_draw_centered(o, 135, 40, 67, 0,
                                             SCREEN_FONT_TITLE, "О", 0xffff));
  TEST_ASSERT_TRUE(screen_font_draw_centered(n, 135, 40, 67, 0,
                                             SCREEN_FONT_TITLE, "Н", 0xffff));
  TEST_ASSERT_NOT_EQUAL(0, memcmp(o, n, sizeof(o)));
}

void test_montserrat_draws_every_digit_of_device_id(void) {
  const char *digits = "0123456789abcdef";
  for (const char *p = digits; *p; ++p) {
    char one[] = {*p, '\0'};
    uint16_t pixels[24 * 24] = {0};
    TEST_ASSERT_TRUE(screen_font_draw_centered(pixels, 24, 24, 12, 0,
                                               SCREEN_FONT_SMALL, one, 0xffff));
    bool visible = false;
    for (size_t i = 0; i < sizeof(pixels) / sizeof(pixels[0]); ++i)
      if (pixels[i]) visible = true;
    TEST_ASSERT_TRUE(visible);
  }
  TEST_ASSERT_LESS_OR_EQUAL_INT(115,
      screen_font_measure(SCREEN_FONT_SMALL, "ID 7ce8b1e4b780"));
}

void test_montserrat_copy_fits_screen_without_clipping(void) {
  const state_t states[] = {STATE_BOOT, STATE_IDLE, STATE_RECORDING,
                            STATE_PROCESSING, STATE_PLAYING, STATE_ERROR};
  for (size_t i = 0; i < sizeof(states) / sizeof(states[0]); ++i) {
    screen_ui_view_t view = screen_ui_view(states[i]);
    int title_width = screen_font_measure(SCREEN_FONT_TITLE, view.title);
    TEST_ASSERT_GREATER_THAN_INT(0, title_width);
    TEST_ASSERT_LESS_OR_EQUAL_INT(115, title_width);
    const char *line = view.hint;
    int lines = 0;
    while (*line) {
      const char *end = strchr(line, '\n');
      if (!end) end = line + strlen(line);
      char fragment[64];
      size_t length = (size_t)(end - line);
      TEST_ASSERT_LESS_THAN(sizeof(fragment), length);
      memcpy(fragment, line, length);
      fragment[length] = '\0';
      int hint_width = screen_font_measure(SCREEN_FONT_HINT, fragment);
      TEST_ASSERT_GREATER_THAN_INT(0, hint_width);
      TEST_ASSERT_LESS_OR_EQUAL_INT(115, hint_width);
      ++lines;
      line = *end ? end + 1 : end;
    }
    TEST_ASSERT_LESS_OR_EQUAL_INT(2, lines);
  }
}

void test_processing_screen_names_each_backend_stage(void) {
  screen_ui_view_t transcribing = screen_ui_view_with_phase(
      STATE_PROCESSING, SCREEN_PROCESSING_TRANSCRIBING);
  screen_ui_view_t thinking = screen_ui_view_with_phase(
      STATE_PROCESSING, SCREEN_PROCESSING_THINKING);
  screen_ui_view_t synthesizing = screen_ui_view_with_phase(
      STATE_PROCESSING, SCREEN_PROCESSING_SYNTHESIZING);
  TEST_ASSERT_EQUAL_STRING("СЛЫШУ", transcribing.title);
  TEST_ASSERT_EQUAL_STRING("РАСПОЗНАЮ РЕЧЬ", transcribing.hint);
  TEST_ASSERT_EQUAL_STRING("ДУМАЮ", thinking.title);
  TEST_ASSERT_EQUAL_STRING("ГОТОВЛЮ", synthesizing.title);
  TEST_ASSERT_EQUAL_STRING("ОТВЕТ", synthesizing.hint);
  TEST_ASSERT_LESS_OR_EQUAL_INT(115,
      screen_font_measure(SCREEN_FONT_TITLE, transcribing.title));
  TEST_ASSERT_LESS_OR_EQUAL_INT(115,
      screen_font_measure(SCREEN_FONT_TITLE, synthesizing.title));
  TEST_ASSERT_LESS_OR_EQUAL_INT(115,
      screen_font_measure(SCREEN_FONT_HINT, transcribing.hint));
  TEST_ASSERT_LESS_OR_EQUAL_INT(115,
      screen_font_measure(SCREEN_FONT_HINT, synthesizing.hint));
}

void test_idle_waits_for_wifi_and_vpn_before_showing_ready(void) {
  screen_ui_view_t wifi = screen_ui_view_with_network(STATE_IDLE, SCREEN_PROCESSING_THINKING, false, false);
  screen_ui_view_t vpn = screen_ui_view_with_network(STATE_IDLE, SCREEN_PROCESSING_THINKING, true, false);
  screen_ui_view_t ready = screen_ui_view_with_network(STATE_IDLE, SCREEN_PROCESSING_THINKING, true, true);
  TEST_ASSERT_EQUAL_STRING("СЕТЬ", wifi.title);
  TEST_ASSERT_EQUAL_STRING("VPN", vpn.title);
  TEST_ASSERT_EQUAL_STRING("ГОТОВ", ready.title);
  TEST_ASSERT_EQUAL_STRING("СЕТЬ", screen_ui_view_with_network(STATE_IDLE, SCREEN_PROCESSING_THINKING, false, true).title);
  TEST_ASSERT_EQUAL_STRING("СЛУШАЮ", screen_ui_view_with_network(STATE_RECORDING, SCREEN_PROCESSING_THINKING, false, false).title);
  TEST_ASSERT_LESS_OR_EQUAL_INT(115, screen_font_measure(SCREEN_FONT_HINT, wifi.hint));
  TEST_ASSERT_LESS_OR_EQUAL_INT(115, screen_font_measure(SCREEN_FONT_HINT, vpn.hint));
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_idle_waits_for_wifi_and_vpn_before_showing_ready);
  RUN_TEST(test_each_voice_state_has_clear_screen_copy_and_accent);
  RUN_TEST(test_montserrat_draws_cyrillic_letters_distinctly);
  RUN_TEST(test_montserrat_draws_every_digit_of_device_id);
  RUN_TEST(test_montserrat_copy_fits_screen_without_clipping);
  RUN_TEST(test_processing_screen_names_each_backend_stage);
  return UNITY_END();
}
