# Архитектура

## Назначение

Система состоит из тонкого ESP32 voice terminal и Python Voice Gateway. Firmware не знает о STT, модели Hermes, TTS или Obsidian: её контракт — захватить PCM, отправить его HTTP-потоком, получить WAV и воспроизвести.

## Схема

```text
[M5Stack ATOM Echo]
 button → PDM mic → I²S DMA → ring buffer → HTTP chunked PCM
                                                    │ Wi‑Fi
                                                    ▼
[Python Voice Gateway]
 receive/archive → STT → Hermes (structured JSON) → NoteStore (optional)
                                      │
                                      ▼
                                    TTS → WAV HTTP response
                                                    │
                                                    ▼
                         ATOM Echo I²S speaker → voice reply
```

Цикл half-duplex: `RECORDING → PROCESSING → PLAYBACK`. Одновременная запись и playback в MVP не нужны.

## Firmware

Целевая плата — M5Stack ATOM Echo (ESP32-PICO-D4), встроенные PDM microphone, I²S speaker, button, RGB LED и Wi‑Fi. Используются ESP-IDF, PlatformIO, C/C++, FreeRTOS, штатные Wi‑Fi/I²S drivers и `esp_http_client`; Arduino framework не используется.

State machine: `BOOT → WIFI_CONNECTING → IDLE → RECORDING → WAITING_RESPONSE → PLAYING → IDLE`. Любая сетевая или playback ошибка ведёт в `ERROR`, затем recovery/reconnect и `IDLE`. LED показывает подключение, idle, запись, processing, playback и ошибку.

Формат входа: signed PCM S16LE, 16 kHz, mono (`audio/L16`), около 32 000 bytes/s. Данные читаются через небольшой ring buffer (ориентир 16–32 KiB) и отправляются сразу. При переполнении запись останавливается, HTTP закрывается, показывается ошибка; полная запись в RAM не накапливается. Push-to-talk ограничен конфигурируемым `MAX_RECORD_SECONDS` (рекомендуется 120 s).

Board-specific GPIO инициализация находится в отдельном модуле `board_atom_echo.*`, а не в business logic.

## Backend

Gateway координирует один voice turn:

```text
HTTP PCM stream
  → UUID/metadata
  → archive input.wav
  → STTProvider.transcribe(wav)
  → HermesClient structured response
  → NoteStore (если note.create=true)
  → TTSProvider.synthesize(reply)
  → HTTP audio/wav
```

Рекомендуемое разбиение `voice_gateway/`: `api/`, `archive/`, `audio/`, `stt/`, `hermes/`, `tts/`, `notes/`, `models/`, `config.py`, `main.py`. Провайдеры реализуют интерфейсы `STTProvider`, `HermesClient`, `TTSProvider`, `NoteStore`, `ArchiveStore`, поэтому vendor можно заменить конфигурацией/адаптером.

Во время request stream backend одновременно пишет архив и не загружает весь body в память. После EOF формируется корректный WAV. Архив каждого turn сохраняет input, transcript, запрос/ответ Hermes, reply, metadata и при настройке reply.wav. Metadata сохраняется и при ошибке.

Hermes возвращает `{reply, note:{create,title,content,tags}}`; `reply` обязателен. Невалидный JSON допускает не более одной repair-попытки, затем безопасный fallback и отсутствие note. Ошибка NoteStore логируется, но не отменяет TTS успешного reply. Все внешние вызовы имеют отдельные bounded timeout.

FilesystemObsidianNoteStore пишет обычные `.md` в `OBSIDIAN_INBOX`, атомарно через temporary file → fsync → rename. Исходный transcript по умолчанию остаётся только в archive.

## Наблюдаемость и готовность

`GET /health/live` означает, что процесс работает. `GET /health/ready` проверяет загруженную конфигурацию и записи в обязательное хранилище; кратковременная недоступность STT/Hermes/TTS не должна вызывать restart loop. `GET /metrics` отдаёт Prometheus counters/histograms/gauges без user text, title, UUID или error text в labels. Structured logs содержат timestamp, level, turn_id, device_id, stage, duration_ms, status и error; ключи и binary audio не логируются.

## Границы и будущие этапы

MVP допускает HTTP внутри доверенной LAN и configurable device token, но backend нельзя публиковать в Internet. WireGuard, deep sleep, wake word, WebSocket, codecs и streaming STT/TTS не входят в реализацию. Их можно добавлять за существующим HTTP application layer без изменения базового voice protocol. Вся изменяемая AI-логика остаётся в gateway.
