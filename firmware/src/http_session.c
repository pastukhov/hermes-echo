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
    voice_transport_t *transport = s->transport;
    s->state = HTTP_SESSION_IDLE;
    s->bytes_sent = 0;
    s->aborted = false;
    s->transport = transport;
  }
}

void http_session_bind_transport(http_session_t *s, voice_transport_t *transport) {
  if (s) s->transport = transport;
}

bool http_session_open(http_session_t* s) {
  if (!s || http_session_is_active(s)) {
    return false;
  }
  s->state = HTTP_SESSION_OPEN;
  s->bytes_sent = 0;
  s->aborted = false;
  if (s->transport && voice_transport_begin(s->transport) != VOICE_TRANSPORT_OK) {
    s->state = HTTP_SESSION_CLOSED;
    return false;
  }
  return true;
}

size_t http_session_write(http_session_t* s, const uint8_t* data, size_t len) {
  if (!s || !data || len == 0 || s->state != HTTP_SESSION_OPEN) {
    return 0;
  }
  if (s->transport) {
    voice_transport_write_result_t r = voice_transport_write(s->transport, data, len);
    s->bytes_sent += r.accepted_bytes;
    return r.accepted_bytes;
  }
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
  if (s->transport) {
    voice_transport_result_t r = voice_transport_finish(s->transport);
    if (r != VOICE_TRANSPORT_OK) { s->state = HTTP_SESSION_CLOSING; return false; }
  }
  s->state = HTTP_SESSION_CLOSED;
  return true;
}

voice_transport_result_t http_session_poll(http_session_t *s, uint8_t *data,
                                            size_t capacity, size_t *received) {
  if (!s || !s->transport) { if (received) *received = 0; return VOICE_TRANSPORT_EOF; }
  return voice_transport_poll(s->transport, data, capacity, received);
}

void http_session_abort(http_session_t* s) {
  if (!s) {
    return;
  }
  if (http_session_is_active(s)) {
    if (s->transport) voice_transport_abort(s->transport);
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
