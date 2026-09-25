#include <unity.h>
#include <string.h>
#include "voice_config_httpd.h"
#include "../test_main/fakes/hw_fakes.c"

static voice_settings_t settings;
/* Public, synthetic fixture; never used by a device or a real peer. */
static const char key[] = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=";

void setUp(void) {
  voice_settings_load(&settings);
  strcpy(settings.wifi[0].ssid, "test");
  strcpy(settings.device_token, "test-token");
  strcpy(settings.device_id, "test-device");
  strcpy(settings.gateway_url, "http://10.7.0.1:8080");
  settings.wireguard.enabled = true;
  strcpy(settings.wireguard.address, "10.7.0.2");
  strcpy(settings.wireguard.endpoint, "vpn.example.com");
  strcpy(settings.wireguard.private_key, key);
  strcpy(settings.wireguard.public_key, key);
}
void tearDown(void) {}

static bool form(const char *value) {
  char body[2048];
  strcpy(body, value);
  return voice_config_parse_form(body, &settings);
}

static void test_defaults_and_valid_configuration(void) {
  TEST_ASSERT_TRUE(voice_settings_valid(&settings));
  TEST_ASSERT_EQUAL_UINT16(51820, settings.wireguard.port);
  TEST_ASSERT_EQUAL_UINT16(25, settings.wireguard.keepalive);
  TEST_ASSERT_EQUAL_STRING("pool.ntp.org", settings.wireguard.ntp_server);
  voice_settings_load(&settings);
  TEST_ASSERT_FALSE(settings.wireguard.enabled);
  TEST_ASSERT_TRUE(voice_wireguard_valid(&settings.wireguard));
}

static void test_bad_keys_and_addresses_are_rejected(void) {
  TEST_ASSERT_FALSE(form("wg_private_key=bad"));
  strcpy(settings.wireguard.private_key, key);
  TEST_ASSERT_FALSE(form("wg_public_key=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA%3D"));
  strcpy(settings.wireguard.public_key, key);
  TEST_ASSERT_FALSE(form("wg_address=10.7.0.256"));
  TEST_ASSERT_FALSE(form("wg_address=10.7.0.0"));
  TEST_ASSERT_FALSE(form("wg_address=10.7.0.255"));
  TEST_ASSERT_FALSE(form("wg_address=127.0.0.1"));
  strcpy(settings.wireguard.address, "10.7.0.2");
  TEST_ASSERT_FALSE(form("wg_netmask=255.0.255.0"));
  TEST_ASSERT_FALSE(form("wg_netmask=0.0.0.0"));
  strcpy(settings.wireguard.netmask, "255.255.255.0");
  TEST_ASSERT_FALSE(form("wg_endpoint=https%3A%2F%2Fvpn.example.com"));
  TEST_ASSERT_FALSE(form("wg_endpoint=vpn.example.com%3A51820"));
}

static void test_secret_preservation_and_explicit_psk_removal(void) {
  strcpy(settings.wireguard.preshared_key, key);
  TEST_ASSERT_TRUE(form("wg_private_key=&wg_preshared_key=&wg_keepalive=0"));
  TEST_ASSERT_EQUAL_STRING(key, settings.wireguard.private_key);
  TEST_ASSERT_EQUAL_STRING(key, settings.wireguard.preshared_key);
  TEST_ASSERT_EQUAL_UINT16(0, settings.wireguard.keepalive);
  TEST_ASSERT_TRUE(form("wg_clear_psk=1&wg_preshared_key="));
  TEST_ASSERT_EQUAL_STRING("", settings.wireguard.preshared_key);
}

static void test_numeric_limits_and_flags(void) {
  TEST_ASSERT_FALSE(form("wg_port=0"));
  TEST_ASSERT_FALSE(form("wg_port=65536"));
  TEST_ASSERT_FALSE(form("wg_port=-1"));
  TEST_ASSERT_FALSE(form("wg_port=51820xyz"));
  TEST_ASSERT_TRUE(form("wg_port=65535&wg_keepalive=65535&wg_full_tunnel=1"));
  TEST_ASSERT_TRUE(settings.wireguard.full_tunnel);
  TEST_ASSERT_FALSE(form("wg_enabled=true"));
  TEST_ASSERT_FALSE(form("wg_keepalive=99999999999999999999999999"));
  TEST_ASSERT_TRUE(form("wg_enabled=0&wg_private_key=bad"));
  TEST_ASSERT_FALSE(form("wg_enabled=1"));
}

static void test_form_decodes_base64_and_rejects_truncation(void) {
  /* '+' must arrive escaped; a raw '+' is a form-encoded space. */
  TEST_ASSERT_TRUE(form("wg_public_key=%2B%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F8%3D"));
  TEST_ASSERT_EQUAL_CHAR('+', settings.wireguard.public_key[0]);
  TEST_ASSERT_FALSE(form("wg_private_key=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"));
  TEST_ASSERT_FALSE(form("wg_endpoint=host%00evil"));
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_defaults_and_valid_configuration);
  RUN_TEST(test_bad_keys_and_addresses_are_rejected);
  RUN_TEST(test_secret_preservation_and_explicit_psk_removal);
  RUN_TEST(test_numeric_limits_and_flags);
  RUN_TEST(test_form_decodes_base64_and_rejects_truncation);
  return UNITY_END();
}
