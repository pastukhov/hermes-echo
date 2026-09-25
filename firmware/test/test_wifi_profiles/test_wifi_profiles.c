#include <unity.h>
#include <string.h>
#include "voice_wifi_profiles.h"
#include "voice_config_httpd.h"
#include "../test_main/fakes/hw_fakes.c"

static voice_settings_t settings;
static voice_wifi_selector_t selector;
void setUp(void) {
  voice_settings_load(&settings);
  strcpy(settings.device_token, "test-token");
  strcpy(settings.device_id, "test-device");
  strcpy(settings.gateway_url, "http://gateway.test");
  strcpy(settings.wifi[0].ssid, "Home");
  strcpy(settings.wifi[0].password, "home-password");
  strcpy(settings.wifi[2].ssid, "Work");
  strcpy(settings.wifi[2].password, "work-password");
  strcpy(settings.wifi[4].ssid, "Cottage");
  strcpy(settings.wifi[4].password, "cottage-password");
  voice_wifi_selector_init(&selector);
}
void tearDown(void) {}
static bool form(const char *input) {
  char body[2048]; strcpy(body, input);
  return voice_config_parse_form(body, &settings);
}
static int tick(bool connected, uint32_t ms) {
  return voice_wifi_selector_tick(&selector, settings.wifi, connected, ms);
}
static void test_rotation_skips_empty_slots_and_bounds_attempts(void) {
  TEST_ASSERT_EQUAL(0, tick(false, 0));
  TEST_ASSERT_EQUAL(-1, tick(false, 11999));
  TEST_ASSERT_EQUAL(2, tick(false, 12000));
  TEST_ASSERT_EQUAL(4, tick(false, 24000));
  TEST_ASSERT_EQUAL(0, tick(false, 36000));
}
static void test_connected_network_is_sticky_then_retried_before_rotation(void) {
  TEST_ASSERT_EQUAL(0, tick(false, 0));
  TEST_ASSERT_EQUAL(2, tick(false, 12000));
  TEST_ASSERT_EQUAL(-1, tick(true, 12500));
  TEST_ASSERT_EQUAL(-1, tick(true, 999999));
  TEST_ASSERT_EQUAL(2, tick(false, 1000000));
  TEST_ASSERT_EQUAL(-1, tick(false, 1011999));
  TEST_ASSERT_EQUAL(4, tick(false, 1012000));
}
static void test_wraparound_and_no_profiles(void) {
  TEST_ASSERT_EQUAL(0, tick(false, UINT32_MAX - 1000));
  TEST_ASSERT_EQUAL(-1, tick(false, 1000));
  TEST_ASSERT_EQUAL(2, tick(false, 11000));
  memset(settings.wifi, 0, sizeof(settings.wifi));
  voice_wifi_selector_init(&selector);
  TEST_ASSERT_EQUAL(-1, tick(false, 0));
  TEST_ASSERT_FALSE(voice_wifi_profiles_valid(settings.wifi));
}
static void test_blank_password_preserves_only_same_network(void) {
  TEST_ASSERT_TRUE(form("wifi0_ssid=Home&wifi0_password=&wifi2_password="));
  TEST_ASSERT_EQUAL_STRING("home-password", settings.wifi[0].password);
  TEST_ASSERT_EQUAL_STRING("work-password", settings.wifi[2].password);
  TEST_ASSERT_FALSE(form("wifi0_ssid=NewHome&wifi0_password="));
  TEST_ASSERT_TRUE(form("wifi0_password=new-password&wifi0_ssid=OtherHome"));
  TEST_ASSERT_EQUAL_STRING("new-password", settings.wifi[0].password);
}
static void test_delete_network_and_explicit_open(void) {
  TEST_ASSERT_TRUE(form("wifi0_ssid="));
  TEST_ASSERT_EQUAL_STRING("", settings.wifi[0].password);
  TEST_ASSERT_EQUAL(2, voice_wifi_first_profile(settings.wifi));
  TEST_ASSERT_TRUE(form("wifi1_open=1&wifi1_ssid=Guest&wifi1_password="));
  TEST_ASSERT_EQUAL_STRING("", settings.wifi[1].password);
  TEST_ASSERT_TRUE(form("wifi2_open=1&wifi2_ssid=Work"));
  TEST_ASSERT_EQUAL_STRING("", settings.wifi[2].password);
  TEST_ASSERT_FALSE(form("wifi1_ssid=&wifi2_ssid=&wifi4_ssid="));
}
static void test_validation_duplicates_passwords_and_bad_slots(void) {
  TEST_ASSERT_FALSE(form("wifi5_ssid=extra"));
  TEST_ASSERT_FALSE(form("wifi1_open=yes"));
  TEST_ASSERT_FALSE(form("wifi1_ssid=Home&wifi1_password=password1"));
  settings.wifi[1] = (voice_wifi_profile_t){0};
  TEST_ASSERT_FALSE(form("wifi2_password=short"));
  TEST_ASSERT_TRUE(form("wifi2_password=abcdefgh"));
  TEST_ASSERT_FALSE(form("wifi2_ssid=123456789012345678901234567890123"));
}
static void test_manual_setup_hold_timeout_and_voice_activity(void) {
  voice_wifi_portal_t p = {0};
  TEST_ASSERT_FALSE(voice_wifi_portal_tick(&p, true, false, 0));
  TEST_ASSERT_FALSE(voice_wifi_portal_tick(&p, true, false, 2999));
  TEST_ASSERT_TRUE(voice_wifi_portal_tick(&p, true, false, 3000));
  TEST_ASSERT_TRUE(voice_wifi_portal_tick(&p, false, false, 302999));
  TEST_ASSERT_FALSE(voice_wifi_portal_tick(&p, false, false, 303000));
  TEST_ASSERT_FALSE(voice_wifi_portal_tick(&p, true, true, 400000));
  TEST_ASSERT_FALSE(voice_wifi_portal_tick(&p, true, true, 404000));
  TEST_ASSERT_FALSE(voice_wifi_portal_tick(&p, true, false, 405000));
  TEST_ASSERT_TRUE(voice_wifi_portal_tick(&p, true, false, 408000));
}
static void test_search_gets_time_before_battery_sleep(void) {
  TEST_ASSERT_TRUE(voice_wifi_search_grace(false, 100, 60000));
  TEST_ASSERT_FALSE(voice_wifi_search_grace(false, 100, 90100));
  TEST_ASSERT_FALSE(voice_wifi_search_grace(true, 100, 101));
  TEST_ASSERT_TRUE(voice_wifi_search_grace(false, UINT32_MAX - 100, 100));
}
static void test_factory_reset_clears_all_credentials_and_restores_defaults(void) {
  memset(&settings, 'x', sizeof(settings));
  voice_settings_factory_defaults(&settings);
  for (size_t i = 0; i < VOICE_WIFI_PROFILE_COUNT; ++i) {
    TEST_ASSERT_EQUAL_STRING("", settings.wifi[i].ssid);
    TEST_ASSERT_EQUAL_STRING("", settings.wifi[i].password);
  }
  TEST_ASSERT_EQUAL_STRING("", settings.gateway_url);
  TEST_ASSERT_EQUAL_STRING("", settings.device_id);
  TEST_ASSERT_EQUAL_STRING("", settings.device_token);
  TEST_ASSERT_EQUAL_STRING("", settings.request_id);
  TEST_ASSERT_EQUAL_STRING("", settings.turn_id);
  TEST_ASSERT_EQUAL_STRING("", settings.wireguard.private_key);
  TEST_ASSERT_EQUAL_STRING("", settings.wireguard.public_key);
  TEST_ASSERT_EQUAL_STRING("", settings.wireguard.preshared_key);
  TEST_ASSERT_EQUAL_STRING("", settings.wireguard.endpoint);
  TEST_ASSERT_EQUAL_STRING("", settings.wireguard.address);
  TEST_ASSERT_FALSE(settings.wireguard.enabled);
  TEST_ASSERT_EQUAL(51820, settings.wireguard.port);
  TEST_ASSERT_EQUAL(25, settings.wireguard.keepalive);
  TEST_ASSERT_EQUAL(30, settings.sleep_timeout_seconds);
  TEST_ASSERT_EQUAL(-1, voice_wifi_first_profile(settings.wifi));
  TEST_ASSERT_FALSE(voice_settings_valid(&settings));
}
int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_factory_reset_clears_all_credentials_and_restores_defaults);
  RUN_TEST(test_rotation_skips_empty_slots_and_bounds_attempts);
  RUN_TEST(test_connected_network_is_sticky_then_retried_before_rotation);
  RUN_TEST(test_wraparound_and_no_profiles);
  RUN_TEST(test_blank_password_preserves_only_same_network);
  RUN_TEST(test_delete_network_and_explicit_open);
  RUN_TEST(test_validation_duplicates_passwords_and_bad_slots);
  RUN_TEST(test_manual_setup_hold_timeout_and_voice_activity);
  RUN_TEST(test_search_gets_time_before_battery_sleep);
  return UNITY_END();
}
