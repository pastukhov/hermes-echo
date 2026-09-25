#include "voice_mdns.h"

#ifdef ESP_PLATFORM
#include <stdio.h>
#include "esp_log.h"
#include "esp_mac.h"
#include "mdns.h"

void voice_mdns_start(void) {
  static bool started;
  if (started) return;
  uint8_t mac[6];
  esp_err_t err = esp_read_mac(mac, ESP_MAC_WIFI_STA);
  if (err != ESP_OK) return;
  char hostname[32];
  snprintf(hostname, sizeof(hostname), "hermes-%02x%02x%02x%02x%02x%02x",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  err = mdns_init();
  if (err == ESP_OK) {
    err = mdns_hostname_set(hostname);
    if (err == ESP_OK) err = mdns_instance_name_set(hostname);
    mdns_txt_item_t txt[] = {
      {"model", "StickS3"}, {"path", "/"}, {"access", "setup-ap-only"},
    };
    if (err == ESP_OK)
      err = mdns_service_add(NULL, "_hermes", "_tcp", 80, txt, 3);
    if (err == ESP_OK)
      err = mdns_service_add(NULL, "_http", "_tcp", 80, txt, 3);
    if (err != ESP_OK) mdns_free();
  }
  if (err != ESP_OK) {
    ESP_LOGW("voice_mdns", "Discovery unavailable: %s", esp_err_to_name(err));
    return;
  }
  started = true;
  ESP_LOGI("voice_mdns", "Discovery ready: %s.local (_hermes._tcp, _http._tcp)", hostname);
}
#else
void voice_mdns_start(void) {}
#endif
