#ifndef VOICE_WIFI_PROFILES_H
#define VOICE_WIFI_PROFILES_H

#include <stdbool.h>
#include <stdint.h>

#define VOICE_WIFI_PROFILE_COUNT 5
#define VOICE_WIFI_ATTEMPT_MS 12000U

typedef struct {
  char ssid[33];
  char password[65];
} voice_wifi_profile_t;

typedef struct {
  int current;
  uint32_t started_ms;
  bool connected;
} voice_wifi_selector_t;

typedef struct {
  bool held;
  bool fired;
  bool active;
  uint32_t held_since_ms;
  uint32_t opened_ms;
} voice_wifi_portal_t;

bool voice_wifi_portal_tick(voice_wifi_portal_t *portal, bool key_down, bool busy, uint32_t now_ms);
bool voice_wifi_search_grace(bool connected, uint32_t started_ms, uint32_t now_ms);

bool voice_wifi_profiles_valid(const voice_wifi_profile_t profiles[VOICE_WIFI_PROFILE_COUNT]);
int voice_wifi_first_profile(const voice_wifi_profile_t profiles[VOICE_WIFI_PROFILE_COUNT]);
void voice_wifi_selector_init(voice_wifi_selector_t *selector);
/* Returns a profile to start, or -1 while an attempt/connection is healthy. */
int voice_wifi_selector_tick(voice_wifi_selector_t *selector,
    const voice_wifi_profile_t profiles[VOICE_WIFI_PROFILE_COUNT],
    bool connected, uint32_t now_ms);

#endif
