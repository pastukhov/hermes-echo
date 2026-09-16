#include "state_machine.h"

void state_machine_init(state_machine_t* sm) {
  if (sm) {
    sm->current_state = STATE_BOOT;
  }
}

state_t state_machine_get_state(const state_machine_t* sm) {
  return sm ? sm->current_state : STATE_BOOT;
}

static bool is_valid_transition(state_t from, state_t to) {
  if (from == to) {
    return false;
  }
  /* Any active state may drop to ERROR, except ERROR recovering to itself. */
  if (to == STATE_ERROR && from != STATE_ERROR) {
    return true;
  }
  switch (from) {
    case STATE_BOOT:
      return to == STATE_IDLE;
    case STATE_IDLE:
      return to == STATE_RECORDING;
    case STATE_RECORDING:
      return to == STATE_PROCESSING;
    case STATE_PROCESSING:
      return to == STATE_PLAYING;
    case STATE_PLAYING:
      return to == STATE_IDLE;
    case STATE_ERROR:
      return to == STATE_IDLE;
  }
  return false;
}

bool state_machine_can_transition(const state_machine_t* sm, state_t next) {
  if (!sm) {
    return false;
  }
  return is_valid_transition(sm->current_state, next);
}

bool state_machine_step(state_machine_t* sm, state_t next) {
  if (!sm) {
    return false;
  }
  if (!is_valid_transition(sm->current_state, next)) {
    return false;
  }
  sm->current_state = next;
  return true;
}

bool state_machine_recover(state_machine_t* sm) {
  if (!sm) {
    return false;
  }
  if (sm->current_state != STATE_ERROR) {
    return false;
  }
  sm->current_state = STATE_IDLE;
  return true;
}

bool state_machine_is_boot(const state_machine_t* sm) {
  return sm && sm->current_state == STATE_BOOT;
}
bool state_machine_is_idle(const state_machine_t* sm) {
  return sm && sm->current_state == STATE_IDLE;
}
bool state_machine_is_recording(const state_machine_t* sm) {
  return sm && sm->current_state == STATE_RECORDING;
}
bool state_machine_is_processing(const state_machine_t* sm) {
  return sm && sm->current_state == STATE_PROCESSING;
}
bool state_machine_is_playing(const state_machine_t* sm) {
  return sm && sm->current_state == STATE_PLAYING;
}
bool state_machine_is_error(const state_machine_t* sm) {
  return sm && sm->current_state == STATE_ERROR;
}
