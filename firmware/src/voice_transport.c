#include "voice_transport.h"

voice_transport_result_t voice_transport_begin(voice_transport_t *t) {
  if (!t || !t->ops || !t->ops->begin || t->begun) return VOICE_TRANSPORT_FATAL;
  voice_transport_result_t r = t->ops->begin(t);
  if (r == VOICE_TRANSPORT_OK) t->begun = 1;
  return r;
}

voice_transport_write_result_t voice_transport_write(voice_transport_t *t,
                                                      const uint8_t *data,
                                                      size_t len) {
  voice_transport_write_result_t bad = {0, VOICE_TRANSPORT_FATAL};
  if (!t || !t->begun || t->finished || !t->ops || !t->ops->write) return bad;
  return t->ops->write(t, data, len);
}

voice_transport_result_t voice_transport_finish(voice_transport_t *t) {
  if (!t || !t->begun || t->finished || !t->ops || !t->ops->finish)
    return VOICE_TRANSPORT_FATAL;
  voice_transport_result_t r = t->ops->finish(t);
  if (r == VOICE_TRANSPORT_OK) t->finished = 1;
  return r;
}

voice_transport_result_t voice_transport_poll(voice_transport_t *t,
                                               uint8_t *data, size_t capacity,
                                               size_t *received) {
  if (received) *received = 0;
  if (!t || !t->begun || !t->finished || !t->ops || !t->ops->poll)
    return VOICE_TRANSPORT_FATAL;
  return t->ops->poll(t, data, capacity, received);
}

void voice_transport_abort(voice_transport_t *t) {
  if (t && t->ops && t->ops->abort) t->ops->abort(t);
  if (t) { t->begun = 0; t->finished = 1; }
}
