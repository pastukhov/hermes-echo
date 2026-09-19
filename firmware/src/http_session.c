#include "http_session.h"

/*
 * Session state logic only — the transport is injected at the call site
 * (esp_http_client on-target; a no-op on host). In this revision the
 * transport is not wired yet, so body writes are always accepted and a
 * graceful close completes immediately. The lifecycle (OPEN -> CLOSING ->
 * CLOSED, abort fallback) is what the app layer and the host tests rely
 * on for spec section 10.
 */

void http_session_init(http_session_t* s) {
  if (s) {
    s->state = HTTP_SESSION_IDLE;
    s->bytes_sent = 0;
    s->aborted = false;
  }
}

bool http_session_open(http_session_t* s) {
  if (!s || http_session_is_active(s)) {
    return false;
  }
  s->state = HTTP_SESSION_OPEN;
  s->bytes_sent = 0;
  s->aborted = false;
  return true;
}

size_t http_session_write(http_session_t* s, const uint8_t* data, size_t len) {
  if (!s || !data || len == 0 || s->state != HTTP_SESSION_OPEN) {
    return 0;
  }
  /* Transport not wired yet in this revision: accept everything. When the
   * real transport is injected, a failed write returns 0 and the session
   * stays OPEN so the app layer can decide close vs abort. */
  s->bytes_sent += len;
  return len;
}

bool http_session_close(http_session_t* s) {
  if (!s) {
    return false;
  }
  if (s->state != HTTP_SESSION_OPEN && s->state != HTTP_SESSION_CLOSING) {
    return s->state == HTTP_SESSION_CLOSED && !s->aborted;
  }
  /* No transport backing: the close completes on this tick. */
  s->state = HTTP_SESSION_CLOSED;
  return true;
}

void http_session_abort(http_session_t* s) {
  if (!s) {
    return;
  }
  if (http_session_is_active(s)) {
    s->state = HTTP_SESSION_CLOSED;
    s->aborted = true;
  }
}

http_session_state_t http_session_state(const http_session_t* s) {
  return s ? s->state : HTTP_SESSION_IDLE;
}

bool http_session_is_active(const http_session_t* s) {
  return s && (s->state == HTTP_SESSION_OPEN || s->state == HTTP_SESSION_CLOSING);
}
