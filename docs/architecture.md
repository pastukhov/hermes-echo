# Архитектура

## Назначение

Система состоит из StickS3 voice terminal, Python Voice Gateway и отдельного host-side Codex Agent Service. Firmware не знает о STT, модели Hermes/Codex, TTS или Obsidian: её контракт — захватить PCM, отправить его HTTP-потоком, дождаться WAV и воспроизвести.

## Схема

```text
[M5Stack StickS3]
 button → ES8311 mic → I²S DMA → ring buffer → HTTP PCM (v1/v2)
                                                    │ Wi‑Fi
                                                    ▼
[Python Voice Gateway]
 v1: receive/archive → STT → selected agent → TTS → WAV response
 v2: durable upload/job → STT → selected agent → TTS → WAV download
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
             Hermes provider        loopback Codex Agent Service
                                    → pinned Codex Python SDK
                                                    │
                                                    ▼
                         StickS3 ES8311 speaker ← WAV
```

Цикл half-duplex: `RECORDING → PROCESSING → PLAYING`. Одновременная запись и playback не выполняются.

## Firmware

Целевая плата — M5Stack StickS3 (ESP32-S3), ES8311 microphone/speaker, KEY1 и Wi‑Fi. Используются ESP-IDF, PlatformIO, C/C++, FreeRTOS, штатные Wi‑Fi/I²S drivers и `esp_http_client`; Arduino framework не используется. Отдельная LED-индикация не используется.

State machine: `BOOT → IDLE → RECORDING → PROCESSING → PLAYING → IDLE`. V1 выполняет синхронный HTTP turn; v2 сохраняет request UUID до upload, затем в отдельной FreeRTOS task опрашивает durable status, принимает cancel и потоково передаёт WAV в parser/audio sink. Экран отображает `СЛЫШУ / РАСПОЗНАЮ РЕЧЬ`, `ДУМАЮ`, `ГОТОВЛЮ / ОТВЕТ`; `ERROR` требует отдельного нажатия. Protocol v2 выбирается явно в настройках и сохраняется в NVS.

Формат входа: signed PCM S16LE, 16 kHz, mono (`audio/L16`), около 32 000 bytes/s. Данные читаются через небольшой ring buffer (ориентир 16–32 KiB) и отправляются сразу. При переполнении запись останавливается, HTTP закрывается, показывается ошибка; полная запись в RAM не накапливается. Push-to-talk ограничен конфигурируемым `MAX_RECORD_SECONDS` (рекомендуется 120 s).

Board-specific инициализация StickS3 находится в `firmware/src/board_sticks3.c`, отдельно от бизнес-логики.

## Backend

Gateway координирует один voice turn и выбирает агента из конфигурации. Hermes остаётся совместимым default; Codex-запросы делегируются host API, а gateway не получает доступ к Codex home или credentials.

```text
HTTP PCM stream
  → UUID/metadata
  → archive input.wav
  → STTProvider.transcribe(wav)
  → AgentClient/Hermes structured response
  → NoteStore (если note.create=true)
  → TTSProvider.synthesize(reply)
  → HTTP audio/wav
```

Рекомендуемое разбиение `voice_gateway/`: `api/`, `archive/`, `audio/`, `stt/`, `hermes/`, `tts/`, `notes/`, `models/`, `config.py`, `main.py`. Провайдеры реализуют интерфейсы `STTProvider`, `HermesClient`, `TTSProvider`, `NoteStore`, `ArchiveStore`, поэтому vendor можно заменить конфигурацией/адаптером.

Во время request stream backend одновременно пишет архив и не загружает весь body в память. После EOF формируется корректный WAV. Архив каждого turn сохраняет input, transcript, запрос/ответ Hermes, reply, metadata и при настройке reply.wav. Metadata сохраняется и при ошибке.

Hermes возвращает `{reply, note:{create,title,content,tags}}`; `reply` обязателен. Невалидный JSON допускает не более одной repair-попытки, затем безопасный fallback и отсутствие note. Ошибка NoteStore логируется, но не отменяет TTS успешного reply. Все внешние вызовы имеют отдельные bounded timeout.

FilesystemObsidianNoteStore пишет обычные `.md` в `OBSIDIAN_INBOX`, атомарно через temporary file → fsync → rename. Исходный transcript по умолчанию остаётся только в archive.

## Codex Agent Service

Host service использует закреплённые `openai-codex` и bundled runtime в отдельном venv, хранит соответствие `device_id → thread_id` и дедуплицирует request IDs в SQLite. HTTP listener привязан к loopback; gateway обращается к нему по bearer token, но токены и домашний каталог Codex не монтируются в контейнер и не передаются устройству. Один device имеет один последовательный разговор; разные devices получают независимые threads. Reset начинает новый thread, cancel прерывает текущий SDK turn, а незавершённый turn после рестарта помечается interrupted и автоматически не повторяется.

V2 job status (`queued`, `transcribing`, `thinking`, `synthesizing`, terminal) доступен устройству только с его bearer token. Устройство проверяет владельца turn через `X-Device-Id`; MAC — routing identity, не секрет.

## Наблюдаемость и готовность

`GET /health/live` означает, что процесс работает. `GET /health/ready` проверяет загруженную конфигурацию и записи в обязательное хранилище; кратковременная недоступность STT/Hermes/TTS не должна вызывать restart loop. `GET /metrics` отдаёт Prometheus counters/histograms/gauges без user text, title, UUID или error text в labels. Structured logs содержат timestamp, level, turn_id, device_id, stage, duration_ms, status и error; ключи и binary audio не логируются.

## Границы и будущие этапы

MVP допускает HTTP внутри доверенной LAN и configurable device token, но backend нельзя публиковать в Internet. WireGuard, deep sleep, wake word, WebSocket, codecs и streaming STT/TTS не входят в реализацию. Их можно добавлять за существующим HTTP application layer без изменения базового voice protocol. Вся изменяемая AI-логика остаётся в gateway.
