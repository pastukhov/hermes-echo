#ifndef STATE_MACHINE_H
#define STATE_MACHINE_H

#include <stdbool.h>

/*
 * Voice-terminal finite state machine.
 *
 * Hardware-independent core logic (reused unchanged from the tested core).
 * The machine models the device lifecycle described in the acceptance
 * scenarios: boot, idle, recording, processing a turn, playing the reply,
 * and a recoverable error state.
 *
 * Valid transitions:
 *   BOOT       -> IDLE | ERROR
 *   IDLE       -> RECORDING | ERROR
 *   RECORDING  -> PROCESSING | ERROR
 *   PROCESSING -> PLAYING | ERROR
 *   PLAYING    -> IDLE | ERROR
 *   ERROR      -> IDLE   (recovery without reboot)
 *
 * Every active state may also drop to ERROR. No state transitions to itself.
 */
typedef enum {
  STATE_BOOT = 0,
  STATE_IDLE,
  STATE_RECORDING,
  STATE_PROCESSING,
  STATE_PLAYING,
  STATE_ERROR
} state_t;

typedef struct {
  state_t current_state;
} state_machine_t;

#ifdef __cplusplus
extern "C" {
#endif

void state_machine_init(state_machine_t* sm);
state_t state_machine_get_state(const state_machine_t* sm);

/* True if `next` is a legal transition from the current state. */
bool state_machine_can_transition(const state_machine_t* sm, state_t next);

/*
 * Attempt to move to `next`. Returns true and advances if legal; returns false
 * and leaves the state unchanged if the transition is invalid.
 */
bool state_machine_step(state_machine_t* sm, state_t next);

/* Recovery path: only valid from ERROR, returns to IDLE. */
bool state_machine_recover(state_machine_t* sm);

bool state_machine_is_boot(const state_machine_t* sm);
bool state_machine_is_idle(const state_machine_t* sm);
bool state_machine_is_recording(const state_machine_t* sm);
bool state_machine_is_processing(const state_machine_t* sm);
bool state_machine_is_playing(const state_machine_t* sm);
bool state_machine_is_error(const state_machine_t* sm);

#ifdef __cplusplus
}
#endif

#endif // STATE_MACHINE_H
