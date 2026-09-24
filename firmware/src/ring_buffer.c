#include "ring_buffer.h"

#include <string.h>

/*
 * Power-of-two capacity is required so index arithmetic can use a mask
 * instead of a modulus (hot path: called for every captured chunk).
 */
static bool is_power_of_two(size_t n) {
  return n != 0 && (n & (n - 1)) == 0;
}

bool ring_buffer_init(ring_buffer_t* rb, uint8_t* storage, size_t capacity) {
  if (!rb || !storage || !is_power_of_two(capacity)) {
    return false;
  }
  memset(rb, 0, sizeof(*rb));
  rb->data = storage;
  rb->capacity = capacity;
  return true;
}

size_t ring_buffer_push(ring_buffer_t* rb, const uint8_t* data, size_t len) {
  if (!rb || !data || len == 0) {
    return 0;
  }
  size_t accepted = 0;
  while (accepted < len) {
    size_t free_bytes = rb->capacity - rb->count;
    if (free_bytes == 0) {
      /* Buffer full: sticky flag, remainder is lost by design. The app
       * layer must stop producing and react to ring_buffer_overflow(). */
      rb->overflow = true;
      break;
    }
    size_t room_before_wrap = rb->capacity - rb->tail;
    size_t chunk = len - accepted;
    if (chunk > free_bytes) {
      chunk = free_bytes;
    }
    if (chunk > room_before_wrap) {
      chunk = room_before_wrap;
    }
    memcpy(&rb->data[rb->tail], &data[accepted], chunk);
    rb->tail = (rb->tail + chunk) & (rb->capacity - 1);
    rb->count += chunk;
    accepted += chunk;
  }
  return accepted;
}

size_t ring_buffer_pop(ring_buffer_t* rb, uint8_t* out, size_t len) {
  if (!rb || !out || len == 0) {
    return 0;
  }
  size_t chunk = len;
  if (chunk > rb->count) {
    chunk = rb->count;
  }
  size_t before_wrap = rb->capacity - rb->head;
  size_t first = chunk < before_wrap ? chunk : before_wrap;
  memcpy(out, &rb->data[rb->head], first);
  if (chunk > first) memcpy(out + first, rb->data, chunk - first);
  rb->head = (rb->head + chunk) & (rb->capacity - 1);
  rb->count -= chunk;
  return chunk;
}

size_t ring_buffer_count(const ring_buffer_t* rb) {
  return rb ? rb->count : 0;
}

size_t ring_buffer_capacity(const ring_buffer_t* rb) {
  return rb ? rb->capacity : 0;
}

bool ring_buffer_empty(const ring_buffer_t* rb) {
  return rb && rb->count == 0;
}

bool ring_buffer_full(const ring_buffer_t* rb) {
  return rb && rb->count == rb->capacity;
}

bool ring_buffer_overflow(const ring_buffer_t* rb) {
  return rb && rb->overflow;
}

void ring_buffer_clear_overflow(ring_buffer_t* rb) {
  if (rb) {
    rb->overflow = false;
  }
}

void ring_buffer_reset(ring_buffer_t* rb) {
  if (!rb) {
    return;
  }
  rb->head = 0;
  rb->tail = 0;
  rb->count = 0;
  rb->overflow = false;
}
