#ifndef RING_BUFFER_H
#define RING_BUFFER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/*
 * Audio ring buffer for the recording path (spec section 10).
 *
 * ATOM Echo has no PSRAM, so the full recording must never live in RAM.
 * The PDM/I2S capture side pushes into this buffer at the production rate;
 * the HTTP writer side pops at the network rate. Target size is 16-32 KiB
 * (32 KiB by default); audio should leave the device at approximately the
 * rate it is produced.
 *
 * Overflow semantics (spec section 10):
 *   When the buffer is full and more audio arrives, the incoming bytes are
 *   NOT silently dropped while recording keeps running. The push accepts
 *   what fits, returns the number of bytes actually accepted, and raises
 *   a STICKY overflow flag. The application layer is expected to react to
 *   the flag on its next tick: stop recording, close the HTTP session
 *   gracefully when possible, show ERROR, then recover to IDLE.
 *
 * The flag is sticky: it is set exactly once per fill event and is only
 * cleared by ring_buffer_reset() (a new recording session) or
 * ring_buffer_clear_overflow(). This keeps the app-level reaction to a
 * single code path regardless of how many over-presses happened.
 *
 * All operations are single-threaded (one task); no locking.
 */

#define RING_BUFFER_CAPACITY_DEFAULT (32u * 1024u) /* spec: 16-32 KiB */

typedef struct {
  uint8_t* data;
  size_t capacity; /* must be a power of two; 0 = uninitialized */
  size_t head;     /* next read position */
  size_t tail;     /* next write position */
  size_t count;    /* bytes currently stored */
  bool overflow;   /* sticky: set when a push met a full buffer */
} ring_buffer_t;

/*
 * Initialize over caller-provided storage (static on-target; tests can use
 * their own arrays). capacity must be a power of two, >= 2.
 * Returns false (and leaves state untouched) on invalid input.
 */
bool ring_buffer_init(ring_buffer_t* rb, uint8_t* storage, size_t capacity);

/*
 * Push up to len bytes. Returns the number of bytes actually stored
 * (0..len). If the buffer was full or partially filled and there were still
 * bytes left over, the sticky overflow flag is raised and the remainder is
 * lost by design (the caller must stop producing and handle the flag).
 */
size_t ring_buffer_push(ring_buffer_t* rb, const uint8_t* data, size_t len);

/* Pop up to len bytes. Returns the number of bytes actually read. */
size_t ring_buffer_pop(ring_buffer_t* rb, uint8_t* out, size_t len);

/* Number of bytes currently available to the reader. */
size_t ring_buffer_count(const ring_buffer_t* rb);
size_t ring_buffer_capacity(const ring_buffer_t* rb);
bool ring_buffer_empty(const ring_buffer_t* rb);
bool ring_buffer_full(const ring_buffer_t* rb);

/* The overflow flag: true once a push met a full buffer. */
bool ring_buffer_overflow(const ring_buffer_t* rb);

/* Clear just the flag (buffer contents untouched). */
void ring_buffer_clear_overflow(ring_buffer_t* rb);

/*
 * Reset for a new recording session: empties the buffer and clears the
 * overflow flag.
 */
void ring_buffer_reset(ring_buffer_t* rb);

#endif /* RING_BUFFER_H */
