# Hermes Voice Terminal

## 1. Цель проекта

Разработать голосовой терминал на базе **M5Stack ATOM Echo**, позволяющий:

1. Нажать кнопку на устройстве.
2. Продиктовать фразу.
3. Потоково передать аудио на Python backend.
4. Сохранить исходную запись в архив.
5. Расшифровать речь посредством STT.
6. Передать текст в Hermes через OpenAI-compatible API.
7. Получить от Hermes:
   - текст голосового ответа;
   - при необходимости структурированную заметку.
8. Сохранить заметку в Obsidian-compatible Markdown storage.
9. Синтезировать ответ через TTS.
10. Вернуть аудио на ATOM Echo.
11. Воспроизвести ответ через встроенный динамик.

Первая версия работает в локальной Wi-Fi сети.

**WireGuard, deep sleep и wake word не входят в MVP и реализуются отдельным вторым этапом.**

---

# 2. Общая архитектура

```text
┌───────────────────────────┐
│ M5Stack ATOM Echo         │
│                           │
│ Button                    │
│   ↓                       │
│ PDM microphone            │
│   ↓                       │
│ PCM 16 kHz / 16 bit mono  │
│   ↓                       │
│ HTTP chunked stream       │
└─────────────┬─────────────┘
              │ Wi-Fi
              ▼
┌────────────────────────────────┐
│ Python Voice Gateway           │
│                                │
│  ┌────────────┐                │
│  │ Archive    │◄── input.wav   │
│  └────────────┘                │
│         │                      │
│         ▼                      │
│       STT                      │
│         │ transcript           │
│         ▼                      │
│      Hermes                    │
│         │                      │
│         ├──── reply text       │
│         │                      │
│         └──── optional note ─────► Obsidian
│                                │
│         │                      │
│         ▼                      │
│        TTS                     │
│         │                      │
│         ▼                      │
│     reply.wav                  │
└─────────────┬──────────────────┘
              │ HTTP response
              ▼
┌───────────────────────────┐
│ ATOM Echo                 │
│                           │
│ I²S speaker               │
│        ↓                  │
│ голосовой ответ Hermes    │
└───────────────────────────┘
```

Система должна быть **half-duplex**:

```text
RECORDING → PROCESSING → PLAYBACK
```

Одновременная запись и воспроизведение в MVP не требуются.

---

# 3. Структура репозитория

Предпочтительная структура:

```text
hermes-voice-terminal/
├── README.md
├── docs/
│   ├── architecture.md
│   ├── protocol.md
│   └── development.md
│
├── firmware/
│   ├── platformio.ini
│   ├── sdkconfig.defaults
│   ├── CMakeLists.txt
│   ├── partitions.csv
│   ├── include/
│   ├── src/
│   └── test/
│
├── backend/
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── src/
│   │   └── voice_gateway/
│   ├── tests/
│   └── prompts/
│
├── docker-compose.yml
├── .env.example
└── .gitignore
```

Firmware и backend должны быть независимыми компонентами.

---

# 4. Firmware

## 4.1. Технологии

Использовать:

- ESP-IDF;
- PlatformIO;
- C/C++;
- FreeRTOS;
- штатный ESP-IDF Wi-Fi stack;
- штатный I²S driver;
- `esp_http_client`.

Не использовать Arduino framework.

Допустимо использовать небольшие внешние ESP-IDF-compatible библиотеки, если это существенно упрощает работу, но основной код должен оставаться ESP-IDF native.

---

# 5. Hardware target

Целевая плата:

```text
M5Stack ATOM Echo
ESP32-PICO-D4
```

Используются:

- встроенный PDM microphone;
- встроенный I²S speaker;
- встроенная кнопка;
- встроенный RGB LED;
- Wi-Fi.

Pin mapping необходимо вынести в отдельный board-specific модуль:

```text
board_atom_echo.*
```

Никакие hardware GPIO не должны быть разбросаны по business logic.

---

# 6. Формат входного аудио

Базовый формат:

```text
Sample rate:     16000 Hz
Channels:        1
Sample format:   signed PCM
Bits/sample:     16
Byte order:      little endian
```

То есть:

```text
audio/L16
16000 Hz
mono
PCM S16LE
```

Примерный поток:

```text
16000 × 2 = 32000 bytes/sec
```

Не использовать MP3/Opus в MVP.

Компрессию оставить для будущего улучшения.

---

# 7. Работа кнопки

В MVP использовать **push-to-talk**.

Поведение:

```text
button press
    ↓
start recording

button held
    ↓
continue recording + HTTP streaming

button release
    ↓
finish request
    ↓
wait for response
```

Максимальная длительность одной записи:

```text
120 seconds
```

Значение должно быть configurable.

При превышении лимита запись автоматически завершается.

---

# 8. State machine firmware

Необходимо реализовать явную конечную state machine.

Состояния:

```text
BOOT
WIFI_CONNECTING
IDLE
RECORDING
WAITING_RESPONSE
PLAYING
ERROR
```

Основные переходы:

```text
BOOT
 ↓
WIFI_CONNECTING
 ↓
IDLE
 ↓ button down
RECORDING
 ↓ button up
WAITING_RESPONSE
 ↓ response audio
PLAYING
 ↓ EOF
IDLE
```

При ошибке:

```text
ANY_STATE
 ↓
ERROR
 ↓ recover/reconnect
IDLE
```

State machine не должна быть размазана по callback-функциям.

---

# 9. LED UI

RGB LED используется как основной UI.

Предлагаемые состояния:

| Состояние | LED |
|---|---|
| Wi-Fi connection | мигающий синий |
| Idle | слабый синий |
| Recording | красный |
| Processing | жёлтый |
| Playback | зелёный |
| Error | мигающий красный |

Конкретные RGB значения вынести в config/constants.

---

# 10. Audio buffering

ATOM Echo не имеет PSRAM, поэтому запрещено хранить полную запись в памяти.

Использовать:

```text
PDM microphone
      ↓
I²S DMA
      ↓
small ring buffer
      ↓
HTTP writer
```

Целевой ring buffer:

```text
16–32 KiB
```

Допустимо изменить после измерений.

Основной принцип:

> Аудио должно покидать устройство приблизительно с той же скоростью, с которой производится.

При длительной блокировке сети firmware не должна бесконечно накапливать данные.

При заполнении ring buffer:

1. прекратить запись;
2. корректно закрыть HTTP session, если возможно;
3. показать ERROR;
4. вернуться в IDLE после recovery.

---

# 11. HTTP-протокол ESP → Backend

Основной endpoint:

```http
POST /api/v1/voice/turn
```

Тело запроса:

```text
raw PCM S16LE
```

Передавать через:

```http
Transfer-Encoding: chunked
```

Headers:

```http
Content-Type: audio/L16
X-Sample-Rate: 16000
X-Channels: 1
X-Sample-Format: s16le
X-Device-Id: atom-echo-01
X-Protocol-Version: 1
```

Firmware должна начинать передачу сразу после начала записи.

После отпускания кнопки отправить terminal HTTP chunk.

---

# 12. Ответ backend

Успешный ответ:

```http
HTTP/1.1 200 OK
Content-Type: audio/wav
X-Turn-Id: <uuid>
```

Body:

```text
WAV audio
```

ATOM Echo должен:

1. проверить HTTP status;
2. определить `Content-Type`;
3. разобрать минимально необходимый WAV header;
4. переключить audio subsystem в playback mode;
5. потоково воспроизводить audio body.

Полный WAV не должен сохраняться в RAM.

---

# 13. Ошибки backend

При ошибке backend возвращает JSON:

```http
HTTP/1.1 5xx
Content-Type: application/json
```

Пример:

```json
{
  "error": "stt_failed",
  "turn_id": "..."
}
```

Firmware не обязана отображать текст ошибки.

Достаточно:

```text
LED error
→ cleanup
→ IDLE
```

---

# 14. Python Voice Gateway

## Технологии

Использовать:

- Python 3.12+;
- FastAPI;
- Uvicorn;
- Pydantic;
- httpx;
- asyncio.

Dependency management:

```text
pyproject.toml
```

Предпочтительно `uv`.

---

# 15. Backend responsibilities

Backend является coordinator одного voice turn.

Он отвечает за:

```text
receive audio
     ↓
archive
     ↓
STT
     ↓
Hermes
     ↓
note processing
     ↓
TTS
     ↓
audio response
```

Backend НЕ должен содержать бизнес-логику самого Hermes.

---

# 16. Внутренняя архитектура backend

Разделить компоненты:

```text
voice_gateway/
├── api/
├── archive/
├── audio/
├── stt/
├── hermes/
├── tts/
├── notes/
├── models/
├── config.py
└── main.py
```

Абстракции:

```text
STTProvider
HermesClient
TTSProvider
NoteStore
ArchiveStore
```

Каждый компонент должен иметь чёткий интерфейс.

Backend не должен быть привязан к конкретному STT/TTS implementation.

---

# 17. Voice turn lifecycle

При поступлении запроса:

## 17.1 Создать turn

Сгенерировать:

```text
turn_id = UUID
timestamp
device_id
```

## 17.2 Принимать PCM поток

Не загружать весь request body в RAM.

Использовать streaming request reader.

Одновременно:

```text
incoming HTTP
     ↓
write archive
```

## 17.3 Завершить запись

После EOF:

```text
raw PCM
 ↓
WAV
```

Сформировать корректный WAV-файл.

---

# 18. Архив записей

Все обращения должны сохраняться независимо от создания Obsidian note.

Структура:

```text
archive/
└── 2026/
    └── 09/
        └── 07/
            └── <turn-id>/
                ├── input.wav
                ├── transcript.txt
                ├── hermes-request.json
                ├── hermes-response.json
                ├── reply.txt
                ├── reply.wav
                └── metadata.json
```

`reply.wav` можно сделать configurable:

```text
ARCHIVE_TTS_AUDIO=true
```

---

# 19. metadata.json

Пример:

```json
{
  "turn_id": "uuid",
  "device_id": "atom-echo-01",
  "started_at": "ISO-8601",
  "finished_at": "ISO-8601",
  "audio_duration_ms": 12340,
  "input_bytes": 394880,
  "transcript": "...",
  "reply": "...",
  "note_created": true,
  "note_path": "Voice Inbox/...",
  "status": "success"
}
```

При ошибках metadata всё равно должна сохраняться.

Например:

```json
{
  "status": "stt_failed",
  "error": "..."
}
```

---

# 20. STT abstraction

Интерфейс логически должен выглядеть как:

```text
transcribe(wav) -> Transcript
```

Результат:

```json
{
  "text": "...",
  "language": "ru"
}
```

Первый implementation сделать через configurable OpenAI-compatible STT API.

Конфигурация:

```text
STT_BASE_URL=
STT_API_KEY=
STT_MODEL=
STT_TIMEOUT=
```

Backend не должен предполагать конкретный hostname/model.

В дальнейшем должна быть возможность подключить:

- Whisper;
- faster-whisper;
- OpenAI;
- локальный OpenAI-compatible endpoint.

---

# 21. Hermes integration

Hermes вызывается через его OpenAI-compatible endpoint.

Конфигурация:

```text
HERMES_BASE_URL=
HERMES_API_KEY=
HERMES_MODEL=
HERMES_TIMEOUT=
```

В Hermes передаётся полученная STT расшифровка.

---

# 22. Контракт Hermes

Hermes должен возвращать **структурированный результат**, а не произвольный текст.

Логическая JSON schema:

```json
{
  "reply": "string",
  "note": {
    "create": true,
    "title": "string",
    "content": "markdown",
    "tags": ["tag1", "tag2"]
  }
}
```

Если заметка не требуется:

```json
{
  "reply": "string",
  "note": {
    "create": false,
    "title": "",
    "content": "",
    "tags": []
  }
}
```

`reply` обязателен всегда.

---

# 23. Правила Hermes

System prompt должен находиться отдельным файлом:

```text
backend/prompts/hermes_voice.md
```

Hermes должен:

1. отвечать коротко и естественно для голосового интерфейса;
2. не использовать Markdown в `reply`;
3. не проговаривать URL, UUID и технические детали без необходимости;
4. создавать заметку, если пользователь явно просит:
   - запомнить;
   - записать;
   - сохранить мысль;
   - сделать заметку;
   - добавить идею;
5. не создавать заметку для обычного вопроса без необходимости;
6. превращать диктовку в читаемый Markdown;
7. исправлять очевидные ошибки STT по контексту;
8. не изменять смысл пользовательской заметки.

История всех voice turns всё равно остаётся в archive независимо от `note.create`.

---

# 24. Structured output validation

Ответ Hermes валидировать через Pydantic.

При невалидном JSON:

1. выполнить максимум **одну** попытку repair;
2. если repair неуспешен:
   - сохранить исходный ответ в archive;
   - не создавать note;
   - использовать безопасный fallback voice reply.

Например:

```text
Не удалось обработать ответ.
```

Не допускать бесконечных retry.

---

# 25. Obsidian-compatible notes

Реализовать интерфейс:

```text
NoteStore
```

MVP implementation:

```text
FilesystemObsidianNoteStore
```

Backend получает путь к Obsidian vault:

```text
OBSIDIAN_VAULT_PATH=/data/obsidian
OBSIDIAN_INBOX=Voice Inbox
```

Заметки сохраняются обычными `.md` файлами.

---

# 26. Формат заметки

Пример:

```markdown
---
created: 2026-09-07T21:42:00+03:00
source: atom-echo
turn_id: 42a...
tags:
  - voice
  - idea
---

# Идея голосового терминала

Содержимое, подготовленное Hermes.
```

Filename:

```text
YYYY-MM-DD HH-MM-SS - <sanitized-title>.md
```

Запись файла должна быть atomic:

```text
temporary file
→ fsync
→ rename
```

Не допускать частично записанных заметок.

---

# 27. Transcript и Obsidian

Исходную транскрипцию **не добавлять в заметку по умолчанию**.

Она хранится в:

```text
archive/.../transcript.txt
```

Добавить настройку:

```text
NOTE_INCLUDE_TRANSCRIPT=false
```

Если включена — добавлять в конец note отдельный раздел:

```markdown
## Исходная расшифровка

...
```

---

# 28. TTS abstraction

Интерфейс:

```text
synthesize(text) -> wav
```

Конфигурация:

```text
TTS_BASE_URL=
TTS_API_KEY=
TTS_MODEL=
TTS_VOICE=
TTS_TIMEOUT=
```

Первый implementation — configurable HTTP/OpenAI-compatible provider.

Не привязывать backend к конкретному TTS engine.

Допустимые будущие providers:

- OpenAI-compatible TTS;
- Piper;
- Kokoro;
- Coqui;
- другой локальный сервис.

---

# 29. Формат TTS для ESP

Предпочтительный результат:

```text
WAV
PCM signed 16 bit
mono
```

Sample rate:

```text
16000 или 24000 Hz
```

Backend должен знать фактический sample rate из WAV header.

ESP должна корректно настроить I²S playback по WAV metadata.

Для MVP не использовать MP3/AAC/Opus в ответе.

---

# 30. Обработка одного запроса

Полная последовательность:

```text
ATOM button down
      ↓
HTTP POST open
      ↓
audio PCM chunks
      ↓
Python writes archive
      ↓
ATOM button up
      ↓
HTTP request EOF
      ↓
finalize input.wav
      ↓
STT
      ↓
transcript.txt
      ↓
Hermes
      ↓
reply + note
      ↓
optional Obsidian write
      ↓
TTS(reply)
      ↓
reply.wav
      ↓
HTTP 200 audio/wav
      ↓
ATOM speaker
      ↓
IDLE
```

---

# 31. Timeout policy

Backend должен иметь отдельные timeouts:

```text
STT_TIMEOUT
HERMES_TIMEOUT
TTS_TIMEOUT
```

Рекомендуемые defaults:

```text
STT:     60 s
Hermes:  120 s
TTS:     60 s
```

Firmware должна иметь общий server response timeout, configurable.

---

# 32. Ошибки

Разделить ошибки:

```text
audio_receive_failed
audio_invalid
stt_failed
hermes_failed
hermes_invalid_response
note_write_failed
tts_failed
internal_error
```

Ошибка сохранения note не должна уничтожать успешный Hermes reply.

То есть:

```text
Hermes reply OK
NoteStore failed
        ↓
log error
        ↓
всё равно выполнить TTS
        ↓
ответить пользователю
```

---

# 33. Logging

Backend должен использовать structured logging.

Минимальные поля:

```text
timestamp
level
turn_id
device_id
stage
duration_ms
status
error
```

Не логировать API keys.

Не логировать binary audio.

Transcript можно логировать только на DEBUG и по отдельной настройке.

---

# 34. Metrics

Для MVP реализовать Prometheus endpoint:

```http
GET /metrics
```

Минимальные метрики:

```text
voice_turns_total{status}
voice_turn_duration_seconds
voice_audio_duration_seconds
voice_stt_duration_seconds
voice_hermes_duration_seconds
voice_tts_duration_seconds
voice_notes_total{status}
voice_active_turns
```

Не использовать transcript, title, turn_id, error message и произвольный текст в labels.

---

# 35. Health endpoints

Реализовать:

```http
GET /health/live
GET /health/ready
```

`live`:

```text
процесс работает
```

`ready`:

```text
config загружен
archive writable
note storage writable/optional
```

Не делать readiness зависимым от кратковременной недоступности Hermes/STT/TTS, если это приводит к restart loop.

---

# 36. Configuration

Все environment-specific значения вынести из кода.

Backend:

```text
VOICE_BIND_HOST
VOICE_BIND_PORT

ARCHIVE_PATH

STT_BASE_URL
STT_API_KEY
STT_MODEL

HERMES_BASE_URL
HERMES_API_KEY
HERMES_MODEL

TTS_BASE_URL
TTS_API_KEY
TTS_MODEL
TTS_VOICE

OBSIDIAN_VAULT_PATH
OBSIDIAN_INBOX
NOTE_INCLUDE_TRANSCRIPT
```

Firmware:

```text
WIFI_SSID
WIFI_PASSWORD
VOICE_GATEWAY_URL
DEVICE_ID
MAX_RECORD_SECONDS
```

Secrets не коммитить.

Добавить:

```text
.env.example
```

и пример firmware secrets configuration.

---

# 37. Backend deployment

Backend должен запускаться:

1. локально для разработки;
2. через Docker.

Предоставить:

```text
Dockerfile
docker-compose.yml
```

Volumes:

```text
/data/archive
/data/obsidian
```

Пример:

```text
host archive → /data/archive
Obsidian vault → /data/obsidian
```

Container должен запускаться non-root, если это не создаёт искусственных сложностей.

---

# 38. Firmware recovery

Firmware должна самостоятельно восстанавливаться после:

- потери Wi-Fi;
- timeout backend;
- HTTP connection reset;
- некорректного ответа;
- ошибки playback.

После recover:

```text
IDLE
```

Устройство не должно требовать reboot после одиночной сетевой ошибки.

---

# 39. Network reconnect

Если Wi-Fi отсутствует:

```text
WIFI_CONNECTING
```

Использовать reconnect с bounded exponential backoff.

Кнопка во время отсутствия сети не начинает запись в MVP.

LED должен показывать отсутствие готовности.

---

# 40. Security MVP

На первом этапе допускается:

```text
HTTP внутри доверенной LAN
```

Не реализовывать HTTPS исключительно ради MVP.

Backend не должен быть опубликован напрямую в Internet.

Добавить простой device token:

```http
Authorization: Bearer <device-token>
```

или:

```http
X-Device-Token: ...
```

Token configurable.

Это не заменяет WireGuard и является только защитой от случайного доступа внутри LAN.

---

# 41. Этап 2: WireGuard

WireGuard **не реализовывать в рамках первого этапа**.

Архитектура должна позволять добавить его без изменения voice protocol:

```text
Wi-Fi
 ↓
SNTP
 ↓
WireGuard
 ↓
existing HTTP Voice Gateway
```

После включения WireGuard:

```text
VOICE_GATEWAY_URL=http://192.168.10.x:PORT
```

Весь application layer остаётся неизменным.

ESP получает отдельный WireGuard key и отдельный VPN address.

---

# 42. Этап 2: Deep Sleep

Deep sleep также **не реализовывать в MVP**.

Будущая схема:

```text
deep sleep
    ↓
button wake
    ↓
Wi-Fi
    ↓
SNTP
    ↓
WireGuard
    ↓
ready
```

Wake source:

```text
ATOM Echo button
```

Не реализовывать wake word в рамках этапа 2 без отдельного исследования энергопотребления.

---

# 43. Возможный этап 3

Не реализовывать сейчас, но не создавать архитектурных препятствий для:

- VAD;
- single-click вместо hold-to-talk;
- Opus;
- WebSocket;
- streaming STT;
- streaming TTS;
- interruption/barge-in;
- conversational context;
- несколько Hermes profiles;
- выбор агента голосом;
- OTA firmware update;
- battery-powered hardware;
- wake word.

---

# 44. Tests — backend

Обязательные unit tests:

### Archive

- корректная запись PCM;
- корректный WAV header;
- metadata;
- error metadata.

### Hermes

- valid response;
- invalid JSON;
- repair success;
- repair failure;
- timeout.

### Notes

- note create=false;
- создание Markdown;
- filename sanitization;
- atomic write;
- duplicate title;
- filesystem error.

### Voice pipeline

Использовать mock:

```text
FakeSTT
FakeHermes
FakeTTS
FakeNoteStore
```

Тестировать pipeline без реальных внешних сервисов.

---

# 45. Integration test backend

Создать fixture:

```text
tests/fixtures/test_voice.wav
```

Integration flow:

```text
test_voice.wav
 ↓
HTTP endpoint
 ↓
Fake STT
 ↓
Fake Hermes
 ↓
Fake TTS
 ↓
HTTP WAV response
```

Проверить:

- HTTP 200;
- создан archive;
- создан transcript;
- создан metadata;
- при `note.create=true` создан `.md`;
- response является валидным WAV.

---

# 46. Firmware tests

Hardware-independent код максимально отделить от ESP drivers.

Unit test 가능한 части:

- state machine;
- WAV parser;
- HTTP response handling;
- config validation;
- retry/backoff logic.

Hardware audio тестируется отдельно на устройстве.

---

# 47. Hardware smoke test

Автоматизированным полностью быть не обязан.

Acceptance scenario:

1. Flash firmware.
2. ATOM подключается к Wi-Fi.
3. LED показывает IDLE.
4. Удержать кнопку.
5. Сказать:

```text
Запиши заметку: купить новый USB-C кабель для лаборатории.
```

6. Отпустить кнопку.
7. LED показывает processing.
8. В archive появляется `input.wav`.
9. STT создаёт корректную транскрипцию.
10. Hermes возвращает note.
11. В Obsidian появляется `.md`.
12. Backend делает TTS.
13. ATOM Echo голосом отвечает, например:

```text
Записал.
```

14. LED возвращается в IDLE.

---

# 48. Второй acceptance scenario

Обычный вопрос:

```text
Сколько будет два плюс два?
```

Ожидается:

```text
Hermes reply → "Четыре."
note.create = false
```

ATOM произносит:

```text
Четыре.
```

Obsidian note не создаётся.

Archive voice turn создаётся в любом случае.

---

# 49. Definition of Done MVP

MVP считается завершённым, когда выполняются все условия:

- firmware собирается PlatformIO;
- используется ESP-IDF framework;
- ATOM Echo стабильно подключается к Wi-Fi;
- push-to-talk работает;
- звук передаётся потоково, без накопления полной записи;
- backend сохраняет WAV;
- STT возвращает transcript;
- transcript передаётся Hermes;
- Hermes выдаёт structured response;
- заметка при необходимости создаётся в Obsidian-compatible directory;
- текст ответа проходит TTS;
- ATOM Echo воспроизводит ответ;
- после ошибки устройство восстанавливается без reboot;
- backend имеет health endpoints;
- backend имеет Prometheus metrics;
- backend имеет unit/integration tests;
- secrets отсутствуют в git;
- README описывает запуск всей системы;
- WireGuard и deep sleep отсутствуют в реализации MVP.

---

# 50. Порядок разработки

Реализовывать строго инкрементально.

## Milestone 1 — Audio hardware

```text
button
→ microphone
→ local capture
→ speaker playback
```

Цель: проверить hardware/I²S без сети.

## Milestone 2 — Streaming

```text
ATOM
→ HTTP PCM
→ Python
→ input.wav
```

Без STT/Hermes/TTS.

## Milestone 3 — STT

```text
audio
→ STT
→ transcript
```

## Milestone 4 — Hermes

```text
transcript
→ Hermes
→ structured result
```

## Milestone 5 — Notes

```text
Hermes
→ Markdown
→ Obsidian
```

## Milestone 6 — TTS

```text
Hermes reply
→ TTS
→ WAV HTTP response
→ ATOM speaker
```

## Milestone 7 — Hardening

- reconnect;
- timeouts;
- metrics;
- archive metadata;
- tests;
- Docker;
- documentation.

Только после успешного MVP переходить к WireGuard/deep sleep.

---

# 51. Ограничения для coding agent

При реализации соблюдать следующие правила:

1. Не добавлять WireGuard.
2. Не добавлять deep sleep.
3. Не добавлять wake word.
4. Не добавлять WebSocket без доказанной необходимости.
5. Не добавлять audio codecs в MVP.
6. Не хранить полную запись в ESP RAM.
7. Не связывать backend напрямую с конкретным STT/TTS vendor.
8. Не позволять Hermes самостоятельно писать произвольные файлы.
9. Запись заметок должна идти через `NoteStore`.
10. Не хранить secrets в repository.
11. Не выключать тесты, linting или validation для прохождения CI.
12. Не заменять исправление ошибки отключением соответствующей проверки.
13. Все внешние зависимости должны иметь timeout.
14. Все retries должны быть ограничены.
15. Не использовать текст пользователя как Prometheus label.
16. Перед завершением каждого milestone запускать соответствующие тесты и фиксировать результат.

---

# 52. Основной архитектурный принцип

Firmware должна оставаться максимально «тупым» voice terminal:

```text
capture audio
send audio
receive audio
play audio
```

Она не должна знать:

- какой используется STT;
- какая модель работает в Hermes;
- где и как хранятся заметки;
- какой используется TTS;
- как Hermes интерпретирует команды.

Вся изменяемая AI-логика находится за Python Voice Gateway.

Это позволит в дальнейшем добавить:

```text
WireGuard
deep sleep
другой STT
другой TTS
другой Hermes profile
другой note backend
```

без изменения базового voice protocol устройства.