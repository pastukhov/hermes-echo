# HTTP-протокол StickS3 ↔ Voice Gateway

Firmware по умолчанию использует `X-Protocol-Version: 1`; v2 включается в setup page. Транспорт сейчас — HTTP. Оба endpoint’а должны быть доступны только в доверенной сети.

## ESP → backend: voice turn

```http
POST /api/v1/voice/turn HTTP/1.1
Host: gateway:8080
Transfer-Encoding: chunked
Content-Type: audio/L16
X-Sample-Rate: 16000
X-Channels: 1
X-Sample-Format: s16le
X-Device-Id: a1b2c3d4e5f6
X-Protocol-Version: 1
Authorization: Bearer <device-token>
```

В текущей реализации v1 gateway не проверяет device bearer token. Firmware может отправить заголовок `Authorization`, но это не включает серверную авторизацию v1. Поэтому v1 допустим только в доверенной LAN. Для v2 gateway проверяет bearer token, сопоставленный с `X-Device-Id`. MAC/device ID — идентификатор, не секрет.

Переменная `VOICE_API_KEY` из шаблона `.env.example` не добавляет авторизацию для `/api/v1/voice/turn`. Не полагайтесь на неё для защиты v1 endpoint.

Body — raw PCM S16LE, 16 000 Hz, 1 channel, little-endian signed 16-bit samples. ESP открывает POST при начале записи и передаёт chunks с минимальной задержкой; отпускание кнопки означает EOF/завершение chunked request. Полный audio body не должен собираться в RAM на ESP или backend.

Обязательные headers:

| Header | Значение |
|---|---|
| `Content-Type` | `audio/L16` |
| `X-Sample-Rate` | `16000` |
| `X-Channels` | `1` |
| `X-Sample-Format` | `s16le` |
| `X-Device-Id` | стабильный device ID StickS3: полный Wi-Fi MAC в hex, например `a1b2c3d4e5f6` |
| `X-Protocol-Version` | `1` |
| `Authorization` | Firmware может отправить заголовок, но текущий v1 gateway его не проверяет |

## Ответ устройства

```http
HTTP/1.1 200 OK
Content-Type: audio/wav
X-Turn-Id: 550e8400-e29b-41d4-a716-446655440000
```

При настроенном TTS ответ содержит WAV PCM signed 16-bit mono; частота дискретизации берётся из WAV header. Firmware проверяет HTTP status, `Content-Type` и WAV metadata, затем передаёт аудио в playback без хранения полной записи в RAM. Если TTS не сконфигурирован, v1 может вернуть успешный ответ без аудиоданных — для голосового ответа TTS обязателен.

## Протокол v2: асинхронный голосовой запрос

v2 добавляет долговечный job API для ответов, которые могут длиться дольше одного HTTP-запроса. V1 остаётся доступной без изменений.

```http
POST /api/v2/voice/turns HTTP/1.1
Content-Type: audio/L16
X-Protocol-Version: 2
X-Request-Id: 9b69da5b-bd5d-44a3-9391-15e8dac36733
X-Device-Id: a1b2c3d4e5f6
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

JSON поля: `error` — машинный код; `turn_id` — UUID turn, если он уже создан. Технические секреты и binary audio не возвращаются. Firmware показывает ошибку на экране до нажатия кнопки.

## Ошибки и рекомендуемые статусы

| Код | HTTP | Значение |
|---|---:|---|
| Код | HTTP | Где возникает |
|---|---:|---|
| `unauthorized` | 401 | v2: token не соответствует `X-Device-Id` |
| `protocol_version_required` | 400 | v2 upload без `X-Protocol-Version: 2` |
| `invalid_request_id` | 400 | v2: request ID не является UUID |
| `audio_too_large` | 413 | v2: upload превысил лимит 3 840 000 байт |
| `audio_invalid` | 400 | пустой/нечётный по размеру PCM upload |
| `idempotency_conflict` | 409 | повторный request ID с другим audio body |
| `upload_in_progress` | 409 | повторный upload этого request ID ещё идёт |
| `agent_busy` | 429 | очередь v2 переполнена |
| `audio_not_ready` | 409 | WAV запрошен до завершения обработки |
| `stt_failed`, `agent_unavailable`, `tts_failed` | terminal status | обработка v2 не завершилась; код находится в `error` status payload |

HTTP status и JSON shape для v1 ошибок зависят от этапа обработки; устройства не следует привязывать к произвольному диагностическому тексту.

## Служебные endpoints

```http
GET /health/live
GET /health/ready
GET /metrics
```

`/health/live` возвращает `200`, когда процесс жив. `/health/ready` возвращает `200`, если config загружен и обязательный archive writable; иначе `503`. `/metrics` возвращает Prometheus exposition format (`text/plain` или совместимый content type) и не использует transcript/title/turn_id/error text как labels.

## Внутренний контракт Hermes

Hermes provider получает STT transcript через OpenAI-compatible endpoint, заданный `HERMES_BASE_URL`, `HERMES_API_KEY`, `HERMES_MODEL` и `HERMES_TIMEOUT`. Ожидаемый ответ агента имеет вид:

```json
{
  "reply": "Короткий голосовой ответ.",
  "note": {"create": true, "title": "...", "content": "...", "tags": ["voice"]}
}
```

Поле `note` архивируется как часть ответа агента. Хотя в репозитории есть Obsidian `NoteStore`, текущий активный pipeline не сохраняет его в vault. JSON агента — внутренний контракт gateway, а не payload ESP.
