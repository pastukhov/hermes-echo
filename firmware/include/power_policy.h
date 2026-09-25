#ifndef POWER_POLICY_H
#define POWER_POLICY_H
#include <stdbool.h>
#include <stdint.h>
typedef struct {
  uint32_t idle_since_ms;
  bool tracking;
} power_policy_t;
/* PM1 PWR_SRC bitmap: VIN=bit0, VINOUT=bit1, VBAT=bit2. */
bool power_source_is_battery_only(uint8_t source);
void power_policy_reset(power_policy_t *policy, uint32_t now_ms);
/* battery_confirmed must be false for failed/unknown power reads. */
bool power_policy_should_sleep(power_policy_t *policy, uint32_t now_ms,
                               bool battery_confirmed, bool busy, uint32_t timeout_ms);
#endif
