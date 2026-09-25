#include "voice_wireguard.h"

#ifdef ESP_PLATFORM
#include <stdatomic.h>
#include <stdio.h>
#include <time.h>
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_wifi.h"
#include "esp_wireguard.h"
#include "wireguard-platform.h"
_Static_assert(WIREGUARD_MAX_SRC_IPS >= 2, "WireGuard needs own-address and VPN-route slots");
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/tcpip.h"

#if !LWIP_ESP_NETIF_DATA
#error "WireGuard requires separate ESP-NETIF client data; enable CONFIG_LWIP_PPP_SUPPORT"
#endif

typedef enum { WG_DISABLED, WG_WIFI, WG_TIME, WG_CONNECTING, WG_UP,
               WG_ERROR, WG_CONFLICT } wg_state_t;
static atomic_int state = WG_DISABLED;
static voice_wireguard_settings_t settings;
static wireguard_config_t config = ESP_WIREGUARD_CONFIG_DEFAULT();
static wireguard_ctx_t context = ESP_WIREGUARD_CONTEXT_DEFAULT();
static struct ifreq interface;
static bool initialized, connecting;
static uint32_t previous_ip;
static const char *TAG = "voice_wireguard";

const char *voice_wireguard_status(void) {
  static const char *names[] = {"disabled", "waiting_wifi", "waiting_time",
    "connecting", "connected", "error", "subnet_conflict"};
  return names[atomic_load(&state)];
}

bool voice_wireguard_ready(void) {
  int s = atomic_load(&state);
  return s == WG_DISABLED || s == WG_UP;
}

struct ifreq *voice_wireguard_interface(void) {
  return settings.enabled ? &interface : NULL;
}

typedef struct { bool online; esp_netif_ip_info_t ip; } network_t;

/* Every raw lwIP operation, including status and teardown, runs on tcpip_thread. */
static void tick(void *arg) {
  network_t *network = arg;
  uint32_t address, mask;
  voice_wireguard_ipv4(settings.address, &address);
  voice_wireguard_ipv4(settings.netmask, &mask);
  uint32_t sta_ip = ntohl(network->ip.ip.addr);
  uint32_t sta_mask = ntohl(network->ip.netmask.addr);
  uint32_t common = sta_mask & mask;
  bool conflict = network->online && (address & common) == (sta_ip & common);
  /* Setup AP must also remain reachable when the VPN is active. */
  common = mask & 0xffffff00U;
  conflict |= (address & common) == (0xc0a80401U & common);
  if (!network->online || conflict || previous_ip != network->ip.ip.addr) {
    atomic_store(&state, conflict ? WG_CONFLICT : WG_WIFI);
    if (context.netif) esp_wireguard_disconnect(&context);
    connecting = false;
    previous_ip = network->ip.ip.addr;
    if (!network->online || conflict) return;
  }
  if (time(NULL) < 1704067200) {
    atomic_store(&state, WG_TIME);
    return;
  }
  esp_err_t err = ESP_OK;
  if (!initialized) {
    err = esp_wireguard_init(&config, &context);
    initialized = err == ESP_OK;
  }
  if (err == ESP_OK && !connecting) {
    atomic_store(&state, WG_CONNECTING);
    err = esp_wireguard_connect(&context);
    if (err == ESP_ERR_RETRY) return;
    if (err == ESP_OK) {
      // All IPv4 destinations are allowed, including the gateway's LAN address.
      // WireGuard's encrypted UDP transport remains bound to the Wi-Fi netif.
      err = esp_wireguard_add_allowed_ip(&context, "0.0.0.0", "0.0.0.0");
      if (err == ESP_OK)
        err = esp_wireguard_set_default(&context);
      if (err == ESP_OK) {
        netif_index_to_name(netif_get_index(context.netif), interface.ifr_name);
        connecting = true;
      }
    }
  }
  if (err != ESP_OK) {
    atomic_store(&state, WG_ERROR);
    if (context.netif) esp_wireguard_disconnect(&context);
    connecting = false;
    ESP_LOGW(TAG, "connection setup failed: %s", esp_err_to_name(err));
    return;
  }
  atomic_store(&state, esp_wireguard_peer_is_up(&context) == ESP_OK ? WG_UP : WG_CONNECTING);
}

static void worker(void *arg) {
  (void)arg;
  esp_sntp_config_t ntp = ESP_NETIF_SNTP_DEFAULT_CONFIG(settings.ntp_server);
  ntp.start = false;
  if (esp_netif_sntp_init(&ntp) != ESP_OK) {
    atomic_store(&state, WG_ERROR);
    vTaskDelete(NULL);
    return;
  }
  bool was_online = false;
  int previous_state = -1;
  for (;;) {
    network_t network = {0};
    wifi_ap_record_t ap;
    esp_netif_t *sta = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    network.online = sta && esp_wifi_sta_get_ap_info(&ap) == ESP_OK &&
      esp_netif_get_ip_info(sta, &network.ip) == ESP_OK && network.ip.ip.addr;
    if (network.online && !was_online) esp_netif_sntp_start();
    was_online = network.online;
    if (tcpip_callback_wait(tick, &network) != ERR_OK) atomic_store(&state, WG_ERROR);
    int current = atomic_load(&state);
    if (current != previous_state) {
      ESP_LOGI(TAG, "%s", voice_wireguard_status());
      previous_state = current;
    }
    vTaskDelay(pdMS_TO_TICKS(current == WG_ERROR ? 10000 : 1000));
  }
}

void voice_wireguard_start(const voice_wireguard_settings_t *s) {
  static bool started;
  if (started || !s) return;
  started = true;
  settings = *s;
  if (!settings.enabled) return;
  atomic_store(&state, WG_ERROR);
  if (!voice_wireguard_valid(&settings)) return;
  config.private_key = settings.private_key;
  config.public_key = settings.public_key;
  config.preshared_key = settings.preshared_key[0] ? settings.preshared_key : NULL;
  config.address = settings.address;
  config.netmask = settings.netmask;
  config.endpoint = settings.endpoint;
  config.port = settings.port;
  config.persistent_keepalive = settings.keepalive;
  atomic_store(&state, WG_WIFI);
  if (xTaskCreate(worker, "voice_wg", 4096, NULL, 3, NULL) != pdPASS)
    atomic_store(&state, WG_ERROR);
}
#else
void voice_wireguard_start(const voice_wireguard_settings_t *s) { (void)s; }
const char *voice_wireguard_status(void) { return "disabled"; }
bool voice_wireguard_ready(void) { return true; }
#endif
