#include "voice_wifi_profiles.h"
#include <string.h>

int voice_wifi_first_profile(const voice_wifi_profile_t profiles[VOICE_WIFI_PROFILE_COUNT]) {
  for (int i = 0; i < VOICE_WIFI_PROFILE_COUNT; ++i)
    if (profiles[i].ssid[0]) return i;
  return -1;
}

bool voice_wifi_profiles_valid(const voice_wifi_profile_t profiles[VOICE_WIFI_PROFILE_COUNT]) {
  if (!profiles || voice_wifi_first_profile(profiles) < 0) return false;
  for (int i = 0; i < VOICE_WIFI_PROFILE_COUNT; ++i) {
    if (!memchr(profiles[i].ssid, 0, sizeof(profiles[i].ssid)) ||
        !memchr(profiles[i].password, 0, sizeof(profiles[i].password))) return false;
    size_t n = strlen(profiles[i].password);
    if (!profiles[i].ssid[0]) {
      if (n) return false;
      continue;
    }
    if (n && n < 8) return false;
    if (n == 64) {
      for (size_t j = 0; j < n; ++j) {
        char c = profiles[i].password[j];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') ||
              (c >= 'A' && c <= 'F'))) return false;
      }
    }
    for (int j = 0; j < i; ++j)
      if (strcmp(profiles[i].ssid, profiles[j].ssid) == 0) return false;
  }
  return true;
}

void voice_wifi_selector_init(voice_wifi_selector_t *selector) {
  *selector = (voice_wifi_selector_t){.current = -1};
}

int voice_wifi_selector_tick(voice_wifi_selector_t *s,
    const voice_wifi_profile_t profiles[VOICE_WIFI_PROFILE_COUNT],
    bool connected, uint32_t now_ms) {
  if (connected) {
    s->connected = true;
    return -1;
  }
  if (s->connected) {
    s->connected = false;
    s->started_ms = now_ms;
    return s->current; /* One bounded retry of the network just lost. */
  }
  if (s->current >= 0 && (uint32_t)(now_ms - s->started_ms) < VOICE_WIFI_ATTEMPT_MS)
    return -1;
  for (int offset = 1; offset <= VOICE_WIFI_PROFILE_COUNT; ++offset) {
    int candidate = (s->current + offset) % VOICE_WIFI_PROFILE_COUNT;
    if (!profiles[candidate].ssid[0]) continue;
    s->current = candidate;
    s->started_ms = now_ms;
    return candidate;
  }
  return -1;
}

bool voice_wifi_portal_tick(voice_wifi_portal_t *p, bool key_down, bool busy, uint32_t now_ms) {
  if (p->active && (uint32_t)(now_ms - p->opened_ms) >= 300000U) p->active = false;
  if (!key_down || busy) {
    p->held = false;
    p->fired = false;
  } else if (!p->held) {
    p->held = true;
    p->held_since_ms = now_ms;
  } else if (!p->fired && (uint32_t)(now_ms - p->held_since_ms) >= 3000U) {
    p->active = true;
    p->fired = true;
    p->opened_ms = now_ms;
  }
  return p->active;
}

bool voice_wifi_search_grace(bool connected, uint32_t started_ms, uint32_t now_ms) {
  return !connected && (uint32_t)(now_ms - started_ms) < 90000U;
}
