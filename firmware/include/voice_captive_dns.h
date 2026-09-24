#ifndef VOICE_CAPTIVE_DNS_H
#define VOICE_CAPTIVE_DNS_H

#ifdef ESP_PLATFORM
#include "esp_err.h"
#include "esp_netif.h"

esp_err_t voice_captive_dns_start(esp_netif_t *ap);
#endif

#endif
