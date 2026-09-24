# HTTP protocol ESP ↔ Voice Gateway

Версия контракта: `X-Protocol-Version: 1`. Транспорт MVP — HTTP в доверенной LAN; HTTPS и WireGuard не требуются, но backend не должен публиковаться в Internet.

## ESP → backend: voice turn

```http
POST /api/v1/voice/turn HTTP/1.1
Host: gateway:8080
Transfer-Encoding: chunked
Content-Type: audio/L16
X-Sample-Rate: 16000
X-Channels: 1
X-Sample-Format: s16le
X-Device-Id: atom-echo-01
X-Protocol-Version: 1
Authorization: Bearer <device-token>
```

`Authorization` обязателен, если настроен device token; вместо него допустим `X-Device-Token: <token>` согласно конфигурации. Токен никогда не пишется в logs/archive.

Body — raw PCM S16LE, 16 000 Hz, 1 channel, little-endian signed 16-bit samples. ESP открывает POST при начале записи и передаёт chunks с минимальной задержкой; отпускание кнопки означает EOF/завершение chunked request. Полный audio body не должен собираться в RAM на ESP или backend.

Обязательные headers:

| Header | Значение |
|---|---|
| `Content-Type` | `audio/L16` |
| `X-Sample-Rate` | `16000` |
| `X-Channels` | `1` |
| `X-Sample-Format` | `s16le` |
| `X-Device-Id` | стабильный device ID, например `atom-echo-01` |
| `X-Protocol-Version` | `1` |
| `Authorization` / `X-Device-Token` | configurable device token, если включён |

## Backend → ESP: success

```http
HTTP/1.1 200 OK
Content-Type: audio/wav
X-Turn-Id: 550e8400-e29b-41d4-a716-446655440000
```

Body — WAV с PCM signed 16-bit mono; sample rate читается из WAV header (MVP допускает 16 000 или 24 000 Hz). ESP проверяет status, `Content-Type`, минимально разбирает RIFF/WAV header, настраивает I²S по metadata и потоково воспроизводит body. Полный reply.wav не хранится в RAM.

## Protocol v2: асинхронный голосовой turn

v2 добавляет долговечный job API для ответов, которые могут длиться дольше одного HTTP-запроса. V1 остаётся доступной без изменений.

```http
POST /api/v2/voice/turns HTTP/1.1
Content-Type: audio/L16
X-Protocol-Version: 2
X-Request-Id: 9b69da5b-bd5d-44a3-9391-15e8dac36733
X-Device-Id: sticks3-a1b2c3
X-Sample-Rate: 16000
X-Channels: 1
Authorization: Bearer <device-token>
```

Устройство создаёт UUID один раз на запись. Body — потоковый PCM S16LE mono, 16 kHz; максимум 3 840 000 байт. Успешная загрузка полностью сохраняется и ставится в ограниченную очередь до ответа `202`:

```json
{"turn_id":"<uuid>","request_id":"<uuid>","status":"queued"}
```

После потери `202` клиент не загружает аудио с новым ID: он проверяет `GET /api/v2/voice/requests/{request_id}`. Для принятого turn клиент опрашивает `GET /api/v2/voice/turns/{turn_id}` примерно раз в секунду. Status проходит `queued → transcribing → thinking → synthesizing → ready`; этапы `transcribing`, `thinking` и `synthesizing` отражаются на экране устройства. Terminal status `ready` позволяет скачать `GET /api/v2/voice/turns/{turn_id}/audio`; файл — корректный WAV PCM S16LE, mono, 24 kHz. Только `200` с `Content-Type: audio/wav` можно передавать в аудиовыход.

`POST /api/v2/voice/turns/{turn_id}/cancel` отменяет текущую работу. `POST /api/v2/voice/sessions/reset` создаёт новый разговор устройства. Каждый запрос требует bearer-токен, сопоставленный этому `X-Device-Id`; чужой turn и неизвестный turn одинаково отвечают `404`.

Повторная загрузка с теми же device ID, request ID и SHA-256 возвращает существующий turn без повторного запуска агента. Другой audio body с тем же ID — `409 idempotency_conflict`; параллельная загрузка — `409 upload_in_progress`; переполненная очередь — `429 agent_busy`. При рестарте незавершённая job (`running`, `transcribing`, `thinking` или `synthesizing`) становится `interrupted` и не запускается повторно автоматически.

`X-Turn-Id` связывает ответ с каталогом archive и может отсутствовать только в legacy-compatible обработчике; корректный gateway его возвращает.

## Ошибочный ответ

```http
HTTP/1.1 <4xx или 5xx>
Content-Type: application/json
X-Turn-Id: <uuid>
```

```json
{"error":"stt_failed","turn_id":"..."}
```

JSON поля: `error` — стабильный машинный код; `turn_id` — UUID turn. Технические секреты, binary audio и лишний user text в ошибке не возвращаются. Firmware не озвучивает JSON и показывает ошибку на экране до подтверждения кнопкой.

## Ошибки и рекомендуемые статусы

| Код | HTTP | Значение |
|---|---:|---|
| `unauthorized` | 401 | отсутствует/неверен device token |
| `unsupported_protocol` | 400/426 | неизвестная protocol version |
| `invalid_headers` | 400 | отсутствуют или неверны audio headers |
| `audio_invalid` | 400 | пустой/некорректный PCM или неподдерживаемый формат |
| `audio_receive_failed` | 400/499 | stream оборван или не прочитан |
| `stt_failed` | 502/504 | STT provider error/timeout |
| `hermes_failed` | 502/504 | Hermes request error/timeout |
| `hermes_invalid_response` | 502 | JSON не прошёл validation после одной repair attempt |
| `note_write_failed` | 500* | note storage failure; при успешном reply pipeline по возможности продолжает TTS |
| `tts_failed` | 502/504 | TTS provider error/timeout |
| `internal_error` | 500 | непредвиденная backend ошибка |

`*` Для ошибки note допустим успешный voice response с диагностикой в metadata, если reply/TTS завершились; NoteStore failure не должен уничтожать успешный Hermes reply.

## Служебные endpoints

```http
GET /health/live
GET /health/ready
GET /metrics
```

`/health/live` возвращает `200`, когда процесс жив. `/health/ready` возвращает `200`, если config загружен и обязательный archive writable; иначе `503`. `/metrics` возвращает Prometheus exposition format (`text/plain` или совместимый content type) и не использует transcript/title/turn_id/error text как labels.

## Внутренний Hermes contract

Gateway отправляет STT transcript в OpenAI-compatible Hermes endpoint с отдельными `HERMES_BASE_URL`, `HERMES_API_KEY`, `HERMES_MODEL`, `HERMES_TIMEOUT`. Ожидаемый результат:

```json
{
  "reply": "Короткий голосовой ответ.",
  "note": {"create": true, "title": "...", "content": "...", "tags": ["voice"]}
}
```

При `create:false` title/content пусты, tags — массив; note не создаётся, но voice turn архивируется. Это внутренний контракт gateway, а не payload ESP.
