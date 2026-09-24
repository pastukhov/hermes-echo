#ifndef HTTP_SESSION_H
#define HTTP_SESSION_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "voice_transport.h"

/*
 * Streaming HTTP upload session for the ESP -> Backend record path
 * (spec sections 10-11).
 *
 * This file owns the SESSION STATE LOGIC only: the open / send-body /
 * graceful-close / abort lifecycle. The transport itself (esp_http_client on
 * the ESP target, a fake on host tests) is injected per call site, so the
 * state machine is verifiable without a network.
 *
 * Spec section 10 requires that on ring-buffer overflow the session is
 * closed gracefully "if possible"; spec section 11 defines the body as a
 * streaming PCM upload. The distinction the app layer relies on:
 *
 *   close()  - graceful: finish the body and close the connection. The
 *              caller may retry on a later tick while the network is still
 *              usable; returns false until the close has completed.
 *   abort()  - force close, used when the connection is already dead and a
 *              graceful close cannot complete ("если возможно" fallback).
 *
 * Writes are only accepted in the OPEN state; after close or abort they are
 * rejected so a stale capture task cannot corrupt a finished session.
 */

typedef enum {
  HTTP_SESSION_IDLE = 0,   /* no session */
  HTTP_SESSION_OPEN,       /* body being streamed */
  HTTP_SESSION_CLOSING,    /* graceful close in progress */
  HTTP_SESSION_CLOSED      /* terminated (cleanly or by abort) */
} http_session_state_t;

typedef struct {
  http_session_state_t state;
  size_t bytes_sent;   /* total body bytes handed to the transport */
  bool aborted;        /* true if the session ended via abort() */
  voice_transport_t *transport;
} http_session_t;

void http_session_init(http_session_t* s);
void http_session_bind_transport(http_session_t* s, voice_transport_t *transport);

/* Begin a new session. Fails (returns false) if a session is active. */
bool http_session_open(http_session_t* s);

/*
 * Hand body bytes to the transport. Returns the number of bytes accepted.
 * Returns 0 if the session is not OPEN or if the transport write failed
 * (the caller must then close/abort; the session stays OPEN until it is
 * explicitly closed so the app layer decides the recovery).
 */
size_t http_session_write(http_session_t* s, const uint8_t* data, size_t len);

/*
 * Graceful close. In this state machine the close completes when the
 * transport confirms the body is finished; with no transport backing (host
 * tests, or before the M2 network wiring) it completes immediately.
 * Returns true once the session has reached CLOSED cleanly.
 */
bool http_session_close(http_session_t* s);
voice_transport_result_t http_session_poll(http_session_t *s, uint8_t *data,
                                            size_t capacity, size_t *received);

/* Force close: for when the network is dead and graceful close is
 * impossible (spec section 10: "если возможно" fallback). */
void http_session_abort(http_session_t* s);

http_session_state_t http_session_state(const http_session_t* s);
bool http_session_is_active(const http_session_t* s); /* OPEN or CLOSING */

#endif /* HTTP_SESSION_H */
