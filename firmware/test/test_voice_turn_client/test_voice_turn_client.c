#include <unity.h>

#include "voice_turn_client.h"
#include "../test_main/fakes/hw_fakes.c"
#include <string.h>

void setUp(void) {}
void tearDown(void) {}

void test_poll_retry_uses_bounded_backoff(void) {
  TEST_ASSERT_EQUAL_UINT32(1000, voice_turn_retry_delay_ms(0));
  TEST_ASSERT_EQUAL_UINT32(2000, voice_turn_retry_delay_ms(1));
  TEST_ASSERT_EQUAL_UINT32(4000, voice_turn_retry_delay_ms(2));
  TEST_ASSERT_EQUAL_UINT32(5000, voice_turn_retry_delay_ms(3));
  TEST_ASSERT_EQUAL_UINT32(5000, voice_turn_retry_delay_ms(20));
}

void test_v2_upload_url_uses_gateway_base_without_truncation(void) {
  char url[192];
  TEST_ASSERT_TRUE(voice_turn_build_upload_url("http://10.0.0.4:8080", url, sizeof(url)));
  TEST_ASSERT_EQUAL_STRING("http://10.0.0.4:8080/api/v2/voice/turns", url);
  TEST_ASSERT_FALSE(voice_turn_build_upload_url("http://10.0.0.4:8080/api/v1/voice/turn", url, sizeof(url)));
  TEST_ASSERT_FALSE(voice_turn_build_upload_url("http://user@10.0.0.4:8080", url, sizeof(url)));
  TEST_ASSERT_FALSE(voice_turn_build_upload_url("http://10.0.0.4:8080 bad", url, sizeof(url)));
  TEST_ASSERT_FALSE(voice_turn_build_upload_url("http://10.0.0.4:8080", url, 16));
}

void test_request_uuid_is_stable_rfc4122_v4_text(void) {
  const uint8_t random_bytes[16] = {
    0x9b, 0x69, 0xda, 0x5b, 0x3d, 0x5d, 0x14, 0xa3,
    0x93, 0x91, 0x15, 0xe8, 0xda, 0xc3, 0x67, 0x33
  };
  char uuid[VOICE_TURN_REQUEST_ID_CAPACITY];
  TEST_ASSERT_TRUE(voice_turn_format_uuid(random_bytes, uuid, sizeof(uuid)));
  TEST_ASSERT_EQUAL_STRING("9b69da5b-3d5d-44a3-9391-15e8dac36733", uuid);
}

void test_v2_acceptance_body_must_contain_a_valid_turn_uuid(void) {
  char turn_id[VOICE_TURN_ID_CAPACITY];
  TEST_ASSERT_TRUE(voice_turn_parse_turn_id(
      "{\"request_id\":\"9b69da5b-bd5d-44a3-9391-15e8dac36733\",\"turn_id\":\"9b69da5b-bd5d-44a3-9391-15e8dac36734\",\"status\":\"queued\"}",
      turn_id, sizeof(turn_id)));
  TEST_ASSERT_EQUAL_STRING("9b69da5b-bd5d-44a3-9391-15e8dac36734", turn_id);
  TEST_ASSERT_FALSE(voice_turn_parse_turn_id("{\"status\":\"queued\"}", turn_id, sizeof(turn_id)));
  TEST_ASSERT_FALSE(voice_turn_parse_turn_id("{\"turn_id\":\"not-a-uuid\"}", turn_id, sizeof(turn_id)));
}

void test_status_json_accepts_only_known_server_states(void) {
  voice_turn_status_t status = VOICE_TURN_STATUS_UNKNOWN;
  TEST_ASSERT_TRUE(voice_turn_parse_status("{\"status\":\"uploading\"}", &status));
  TEST_ASSERT_EQUAL(VOICE_TURN_STATUS_QUEUED, status);
  TEST_ASSERT_TRUE(voice_turn_parse_status("{\"status\":\"running\"}", &status));
  TEST_ASSERT_EQUAL(VOICE_TURN_STATUS_RUNNING, status);
  TEST_ASSERT_TRUE(voice_turn_parse_status("{\"status\":\"transcribing\"}", &status));
  TEST_ASSERT_EQUAL(VOICE_TURN_STATUS_TRANSCRIBING, status);
  TEST_ASSERT_TRUE(voice_turn_parse_status("{\"status\":\"thinking\"}", &status));
  TEST_ASSERT_EQUAL(VOICE_TURN_STATUS_THINKING, status);
  TEST_ASSERT_TRUE(voice_turn_parse_status("{\"status\":\"synthesizing\"}", &status));
  TEST_ASSERT_EQUAL(VOICE_TURN_STATUS_SYNTHESIZING, status);
  TEST_ASSERT_TRUE(voice_turn_parse_status("{\"status\":\"ready\"}", &status));
  TEST_ASSERT_EQUAL(VOICE_TURN_STATUS_READY, status);
  TEST_ASSERT_FALSE(voice_turn_parse_status("{\"status\":\"new-state\"}", &status));
}

void test_only_validated_wav_response_can_be_played(void) {
  TEST_ASSERT_TRUE(voice_turn_audio_response_valid(200, "audio/wav", 24000, 1, 16));
  TEST_ASSERT_FALSE(voice_turn_audio_response_valid(202, "audio/wav", 24000, 1, 16));
  TEST_ASSERT_FALSE(voice_turn_audio_response_valid(200, "application/json", 24000, 1, 16));
  TEST_ASSERT_FALSE(voice_turn_audio_response_valid(200, "audio/wav", 16000, 1, 16));
  TEST_ASSERT_FALSE(voice_turn_audio_response_valid(200, "audio/wav", 24000, 2, 16));
  TEST_ASSERT_FALSE(voice_turn_audio_response_valid(200, "audio/wav", 24000, 1, 8));
  TEST_ASSERT_TRUE(voice_turn_audio_response_valid(200, "Audio/Wav; charset=binary", 24000, 1, 16));
  TEST_ASSERT_FALSE(voice_turn_audio_response_valid(200, "audio/wav evil", 24000, 1, 16));
}

typedef struct {
  unsigned lookups;
  unsigned polls;
  unsigned downloads;
  unsigned cancels;
  unsigned saved_ids;
  unsigned cleared_ids;
  bool bad_audio_metadata;
  voice_turn_io_result_t lookup_result;
  voice_turn_io_result_t poll_result;
  voice_turn_io_result_t cancel_result;
  voice_turn_status_t status;
} turn_fake_t;

static voice_turn_io_result_t fake_lookup(void *ctx, const char *request_id,
                                          voice_turn_response_t *response) {
  turn_fake_t *fake = ctx;
  fake->lookups++;
  TEST_ASSERT_EQUAL_STRING("9b69da5b-bd5d-44a3-9391-15e8dac36733", request_id);
  response->status = fake->status;
  strcpy(response->turn_id, "9b69da5b-bd5d-44a3-9391-15e8dac36734");
  return fake->lookup_result;
}

static voice_turn_io_result_t fake_poll(void *ctx, const char *turn_id,
                                        voice_turn_response_t *response) {
  turn_fake_t *fake = ctx;
  fake->polls++;
  TEST_ASSERT_EQUAL_STRING("9b69da5b-bd5d-44a3-9391-15e8dac36734", turn_id);
  response->status = fake->status;
  return fake->poll_result;
}

static voice_turn_io_result_t fake_download(void *ctx, const char *turn_id,
                                            voice_turn_response_t *response) {
  turn_fake_t *fake = ctx;
  fake->downloads++;
  TEST_ASSERT_EQUAL_STRING("9b69da5b-bd5d-44a3-9391-15e8dac36734", turn_id);
  response->http_status = 200;
  strcpy(response->content_type, "audio/wav");
  response->sample_rate = 24000;
  response->channels = 1;
  response->bits_per_sample = 16;
  if (fake->bad_audio_metadata) response->http_status = 202;
  return VOICE_TURN_IO_OK;
}

static voice_turn_io_result_t fake_cancel(void *ctx, const char *turn_id) {
  turn_fake_t *fake = ctx;
  fake->cancels++;
  TEST_ASSERT_EQUAL_STRING("9b69da5b-bd5d-44a3-9391-15e8dac36734", turn_id);
  return fake->cancel_result;
}

static bool fake_save_id(void *ctx, const char *turn_id) {
  turn_fake_t *fake = ctx;
  fake->saved_ids++;
  return strcmp(turn_id, "9b69da5b-bd5d-44a3-9391-15e8dac36734") == 0;
}

static void fake_clear_ids(void *ctx) {
  ((turn_fake_t *)ctx)->cleared_ids++;
}

static const voice_turn_ops_t TURN_OPS = {
  .lookup_request = fake_lookup,
  .poll_turn = fake_poll,
  .download_audio = fake_download,
  .cancel_turn = fake_cancel,
  .save_turn_id = fake_save_id,
  .clear_ids = fake_clear_ids,
};

void test_restart_looks_up_saved_request_before_polling_turn(void) {
  turn_fake_t fake = {.lookup_result = VOICE_TURN_IO_OK,
                      .poll_result = VOICE_TURN_IO_OK,
                      .status = VOICE_TURN_STATUS_QUEUED};
  voice_turn_client_t client;
  voice_turn_client_init(&client, &TURN_OPS, &fake,
      "9b69da5b-bd5d-44a3-9391-15e8dac36733", NULL, 100);
  TEST_ASSERT_EQUAL(VOICE_TURN_WAITING, voice_turn_client_tick(&client, 1099));
  TEST_ASSERT_EQUAL_UINT(0, fake.lookups);
  TEST_ASSERT_EQUAL(VOICE_TURN_WAITING, voice_turn_client_tick(&client, 1100));
  TEST_ASSERT_EQUAL_UINT(1, fake.lookups);
  TEST_ASSERT_EQUAL_UINT(1, fake.saved_ids);
  TEST_ASSERT_EQUAL_UINT(0, fake.polls);
  TEST_ASSERT_EQUAL(VOICE_TURN_WAITING, voice_turn_client_tick(&client, 2100));
  TEST_ASSERT_EQUAL_UINT(1, fake.polls);
}

void test_ready_turn_downloads_audio_once_and_rejects_bad_metadata(void) {
  turn_fake_t fake = {.lookup_result = VOICE_TURN_IO_OK,
                      .poll_result = VOICE_TURN_IO_OK,
                      .status = VOICE_TURN_STATUS_READY};
  voice_turn_client_t client;
  voice_turn_client_init(&client, &TURN_OPS, &fake,
      "9b69da5b-bd5d-44a3-9391-15e8dac36733",
      "9b69da5b-bd5d-44a3-9391-15e8dac36734", 0);
  TEST_ASSERT_EQUAL(VOICE_TURN_READY, voice_turn_client_tick(&client, 1000));
  TEST_ASSERT_EQUAL_UINT(1, fake.downloads);
  TEST_ASSERT_EQUAL_UINT(1, fake.cleared_ids);

  turn_fake_t bad = {.lookup_result = VOICE_TURN_IO_OK,
                     .poll_result = VOICE_TURN_IO_OK,
                     .status = VOICE_TURN_STATUS_READY,
                     .bad_audio_metadata = true};
  voice_turn_client_init(&client, &TURN_OPS, &bad,
      "9b69da5b-bd5d-44a3-9391-15e8dac36733",
      "9b69da5b-bd5d-44a3-9391-15e8dac36734", 0);
  TEST_ASSERT_EQUAL(VOICE_TURN_FAILED, voice_turn_client_tick(&client, 1000));
  TEST_ASSERT_EQUAL_UINT(1, bad.downloads);
  TEST_ASSERT_EQUAL_UINT(1, bad.cleared_ids);
}

void test_poll_retries_back_off_and_stop_at_the_deadline(void) {
  turn_fake_t fake = {.lookup_result = VOICE_TURN_IO_OK,
                      .poll_result = VOICE_TURN_IO_RETRY,
                      .status = VOICE_TURN_STATUS_QUEUED};
  voice_turn_client_t client;
  voice_turn_client_init(&client, &TURN_OPS, &fake,
      "9b69da5b-bd5d-44a3-9391-15e8dac36733",
      "9b69da5b-bd5d-44a3-9391-15e8dac36734", 0);
  TEST_ASSERT_EQUAL(VOICE_TURN_WAITING, voice_turn_client_tick(&client, 1000));
  TEST_ASSERT_EQUAL_UINT(1, fake.polls);
  TEST_ASSERT_EQUAL(VOICE_TURN_WAITING, voice_turn_client_tick(&client, 1999));
  TEST_ASSERT_EQUAL_UINT(1, fake.polls);
  TEST_ASSERT_EQUAL(VOICE_TURN_WAITING, voice_turn_client_tick(&client, 2000));
  TEST_ASSERT_EQUAL_UINT(2, fake.polls);
  TEST_ASSERT_EQUAL(VOICE_TURN_FAILED, voice_turn_client_tick(&client, 180000));
  TEST_ASSERT_EQUAL_UINT(1, fake.cleared_ids);
  TEST_ASSERT_EQUAL(VOICE_TURN_FAILED, voice_turn_client_tick(&client, 181000));
  TEST_ASSERT_EQUAL_UINT(2, fake.polls);
}

void test_turn_may_remain_running_longer_than_fifteen_seconds(void) {
  turn_fake_t fake = {.lookup_result = VOICE_TURN_IO_OK,
                      .poll_result = VOICE_TURN_IO_OK,
                      .status = VOICE_TURN_STATUS_RUNNING};
  voice_turn_client_t client;
  voice_turn_client_init(&client, &TURN_OPS, &fake,
      "9b69da5b-bd5d-44a3-9391-15e8dac36733",
      "9b69da5b-bd5d-44a3-9391-15e8dac36734", 0);
  for (uint64_t now = 1000; now <= 16000; now += 1000)
    TEST_ASSERT_EQUAL(VOICE_TURN_WAITING,
                      voice_turn_client_tick(&client, now));
  TEST_ASSERT_EQUAL_UINT(16, fake.polls);
  fake.status = VOICE_TURN_STATUS_READY;
  TEST_ASSERT_EQUAL(VOICE_TURN_READY,
                    voice_turn_client_tick(&client, 17000));
  TEST_ASSERT_EQUAL_UINT(1, fake.downloads);
}

void test_cancel_stops_the_active_turn(void) {
  turn_fake_t fake = {.lookup_result = VOICE_TURN_IO_OK,
                      .poll_result = VOICE_TURN_IO_OK,
                      .status = VOICE_TURN_STATUS_QUEUED};
  voice_turn_client_t client;
  voice_turn_client_init(&client, &TURN_OPS, &fake,
      "9b69da5b-bd5d-44a3-9391-15e8dac36733",
      "9b69da5b-bd5d-44a3-9391-15e8dac36734", 0);
  TEST_ASSERT_EQUAL(VOICE_TURN_CANCELLED, voice_turn_client_cancel(&client, 0));
  TEST_ASSERT_EQUAL_UINT(1, fake.cancels);
  TEST_ASSERT_EQUAL(VOICE_TURN_CANCELLED,
                    voice_turn_client_tick(&client, 1000));
  TEST_ASSERT_EQUAL_UINT(0, fake.polls);
}

void test_cancel_retries_transient_network_failure_without_polling(void) {
  turn_fake_t fake = {.lookup_result = VOICE_TURN_IO_OK,
                      .poll_result = VOICE_TURN_IO_OK,
                      .cancel_result = VOICE_TURN_IO_RETRY,
                      .status = VOICE_TURN_STATUS_QUEUED};
  voice_turn_client_t client;
  voice_turn_client_init(&client, &TURN_OPS, &fake,
      "9b69da5b-bd5d-44a3-9391-15e8dac36733",
      "9b69da5b-bd5d-44a3-9391-15e8dac36734", 0);
  TEST_ASSERT_EQUAL(VOICE_TURN_WAITING, voice_turn_client_cancel(&client, 100));
  TEST_ASSERT_EQUAL(VOICE_TURN_WAITING, voice_turn_client_tick(&client, 1099));
  TEST_ASSERT_EQUAL_UINT(1, fake.cancels);
  fake.cancel_result = VOICE_TURN_IO_OK;
  TEST_ASSERT_EQUAL(VOICE_TURN_CANCELLED, voice_turn_client_tick(&client, 1100));
  TEST_ASSERT_EQUAL_UINT(2, fake.cancels);
  TEST_ASSERT_EQUAL_UINT(0, fake.polls);
}

void test_invalid_client_configuration_fails_without_crashing(void) {
  voice_turn_client_t client;
  voice_turn_client_init(&client, NULL, NULL,
      "9b69da5b-bd5d-44a3-9391-15e8dac36733", NULL, 0);
  TEST_ASSERT_EQUAL(VOICE_TURN_FAILED, voice_turn_client_tick(&client, 0));
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_poll_retry_uses_bounded_backoff);
  RUN_TEST(test_v2_upload_url_uses_gateway_base_without_truncation);
  RUN_TEST(test_request_uuid_is_stable_rfc4122_v4_text);
  RUN_TEST(test_v2_acceptance_body_must_contain_a_valid_turn_uuid);
  RUN_TEST(test_status_json_accepts_only_known_server_states);
  RUN_TEST(test_only_validated_wav_response_can_be_played);
  RUN_TEST(test_restart_looks_up_saved_request_before_polling_turn);
  RUN_TEST(test_ready_turn_downloads_audio_once_and_rejects_bad_metadata);
  RUN_TEST(test_poll_retries_back_off_and_stop_at_the_deadline);
  RUN_TEST(test_turn_may_remain_running_longer_than_fifteen_seconds);
  RUN_TEST(test_cancel_stops_the_active_turn);
  RUN_TEST(test_cancel_retries_transient_network_failure_without_polling);
  RUN_TEST(test_invalid_client_configuration_fails_without_crashing);
  return UNITY_END();
}
