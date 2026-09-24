#include "voice_captive_dns.h"

#ifdef ESP_PLATFORM
#include <string.h>
#include <lwip/sockets.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define DNS_PACKET_MAX 512
#define DNS_RESPONSE_MAX (DNS_PACKET_MAX + 16)

static const char *TAG = "voice_dns";
static TaskHandle_t s_task;
static esp_netif_ip_info_t s_ap_ip;
static uint8_t s_query[DNS_PACKET_MAX];
static uint8_t s_response[DNS_RESPONSE_MAX];

/* Answer only single-question IN/A lookups. An empty NOERROR answer for
 * other record types lets clients continue their IPv4 captive check. */
static size_t dns_reply(const uint8_t *query, size_t length, uint8_t *response,
                        size_t capacity, const uint8_t *address) {
  if (length < 17 || (query[2] & 0x80) || (query[2] & 0x78) ||
      query[4] != 0 || query[5] != 1) return 0;
  size_t cursor = 12;
  while (cursor < length && query[cursor]) {
    uint8_t label = query[cursor++];
    if (label > 63 || cursor + label >= length) return 0;
    cursor += label;
  }
  if (cursor >= length || cursor + 5 > length) return 0;
  cursor++; /* root label */
  bool answer_a = query[cursor] == 0 && query[cursor + 1] == 1 &&
                  query[cursor + 2] == 0 && query[cursor + 3] == 1;
  size_t question_end = cursor + 4;
  size_t response_length = question_end + (answer_a ? 16 : 0);
  if (response_length > capacity) return 0;
  memcpy(response, query, question_end);
  response[2] = 0x81; response[3] = 0x80;
  response[6] = 0; response[7] = answer_a ? 1 : 0;
  response[8] = response[9] = response[10] = response[11] = 0;
  if (answer_a) {
    uint8_t answer[16] = {0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 60, 0, 4,
                          address[0], address[1], address[2], address[3]};
    memcpy(response + question_end, answer, sizeof(answer));
  }
  return response_length;
}

static void dns_task(void *arg) {
  (void)arg;
  int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (fd < 0) goto done;
  struct sockaddr_in bind_address = {
      .sin_family = AF_INET, .sin_port = htons(53),
      .sin_addr.s_addr = s_ap_ip.ip.addr};
  if (bind(fd, (struct sockaddr *)&bind_address, sizeof(bind_address)) < 0) {
    ESP_LOGE(TAG, "DNS bind to setup AP failed");
    close(fd);
    goto done;
  }
  ESP_LOGI(TAG, "captive DNS listening on setup AP");
  const uint8_t *address = (const uint8_t *)&s_ap_ip.ip.addr;
  for (;;) {
    struct sockaddr_in client;
    socklen_t client_len = sizeof(client);
    int received = recvfrom(fd, s_query, sizeof(s_query), 0,
                            (struct sockaddr *)&client, &client_len);
    if (received <= 0) continue;
    size_t length = dns_reply(s_query, (size_t)received, s_response,
                              sizeof(s_response), address);
    if (length) sendto(fd, s_response, length, 0,
                       (struct sockaddr *)&client, client_len);
  }
done:
  s_task = NULL;
  vTaskDelete(NULL);
}

esp_err_t voice_captive_dns_start(esp_netif_t *ap) {
  if (!ap) return ESP_ERR_INVALID_ARG;
  if (s_task) return ESP_OK;
  esp_err_t err = esp_netif_get_ip_info(ap, &s_ap_ip);
  if (err != ESP_OK || !s_ap_ip.ip.addr) return ESP_ERR_INVALID_STATE;

  esp_netif_dhcp_status_t status = ESP_NETIF_DHCP_INIT;
  err = esp_netif_dhcps_get_status(ap, &status);
  if (err != ESP_OK) return err;
  if (status == ESP_NETIF_DHCP_STARTED) {
    err = esp_netif_dhcps_stop(ap);
    if (err != ESP_OK) return err;
  }
  uint8_t offer_dns = 1;
  esp_netif_dns_info_t dns = {.ip.type = ESP_IPADDR_TYPE_V4};
  dns.ip.u_addr.ip4.addr = s_ap_ip.ip.addr;
  const char *portal_url = "http://192.168.4.1/";
  err = esp_netif_dhcps_option(ap, ESP_NETIF_OP_SET,
                               ESP_NETIF_DOMAIN_NAME_SERVER,
                               &offer_dns, sizeof(offer_dns));
  if (err == ESP_OK) err = esp_netif_set_dns_info(ap, ESP_NETIF_DNS_MAIN, &dns);
  if (err == ESP_OK) err = esp_netif_dhcps_option(ap, ESP_NETIF_OP_SET,
      ESP_NETIF_CAPTIVEPORTAL_URI, (void *)portal_url, strlen(portal_url));
  esp_err_t restart_err = ESP_OK;
  if (status == ESP_NETIF_DHCP_STARTED) restart_err = esp_netif_dhcps_start(ap);
  if (err != ESP_OK) return err;
  if (restart_err != ESP_OK) return restart_err;

  if (xTaskCreate(dns_task, "voice_dns", 3072, NULL, 4, &s_task) != pdPASS)
    return ESP_ERR_NO_MEM;
  return ESP_OK;
}
#endif
