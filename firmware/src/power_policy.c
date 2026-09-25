#include "power_policy.h"
bool power_source_is_battery_only(uint8_t source) {
  return (source & 7) == 4;
}
void power_policy_reset(power_policy_t *policy, uint32_t now_ms) {
  policy->idle_since_ms = now_ms;
  policy->tracking = false;
}
bool power_policy_should_sleep(power_policy_t *policy, uint32_t now_ms,
                               bool battery_confirmed, bool busy, uint32_t timeout_ms) {
  if (!battery_confirmed || busy) {
    power_policy_reset(policy, now_ms);
    return false;
  }
  if (!policy->tracking) {
    policy->tracking = true;
    policy->idle_since_ms = now_ms;
  }
  return (uint32_t)(now_ms - policy->idle_since_ms) >= timeout_ms;
}
