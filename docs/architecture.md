# Архитектура

Проект разделяет устройство, Voice Gateway и (опционально) локальный Codex Agent. StickS3 отвечает за захват и воспроизведение звука; STT, выбор агента, TTS и архив находятся на стороне сервера.

![Компоненты и потоки данных](assets/system-overview.svg)

## Компоненты и границы

```mermaid
flowchart LR
    D["M5Stack StickS3<br/>ESP32-S3 · микрофон · экран · динамик"]
    G["Voice Gateway<br/>FastAPI · v1 sync / v2 jobs"]
    S["STT provider<br/>OpenAI-compatible"]
    H["Hermes provider<br/>по умолчанию"]
    C["Codex Agent<br/>опциональный host service"]
    SDK["Codex Python SDK<br/>credentials остаются на хосте"]
    T["TTS provider<br/>OpenAI-compatible"]
    A[("Archive<br/>PCM/WAV · transcript · metadata")]
    J[("SQLite job store<br/>v2 ownership · deduplication")]

    D -->|"PCM S16LE · Wi-Fi/HTTP"| G
    G --> S
    S -->|"transcript"| G
    G --> H
    G --> C
    C --> SDK
    H -->|"reply"| G
    SDK -->|"reply"| C
    C --> G
    G --> T
    T -->|"WAV"| G
    G -->|"WAV response / download"| D
    G --> A
    G --> J
```

Gateway выбирает ровно одного conversational provider: `VOICE_AGENT_PROVIDER=hermes` (значение по умолчанию) либо `codex`. STT и TTS конфигурируются отдельно. Сервис Codex слушает только loopback; его bearer token — внутренний ключ между gateway и adapter, а не пароль устройства или Codex credential.

## Путь голосового запроса

### V1 · синхронный

```mermaid
sequenceDiagram
    participant U as Пользователь
    participant D as StickS3
    participant G as Voice Gateway
    participant S as STT
    participant A as Hermes или Codex Agent
    participant T as TTS
    U->>D: удерживает кнопку и говорит
    D->>G: POST /api/v1/voice/turn · поток PCM
    U->>D: отпускает кнопку
    G->>G: сохраняет запись и формирует WAV для STT
    G->>S: распознавание речи
    S-->>G: transcript
    G->>A: текст запроса
    A-->>G: короткий reply
    G->>T: синтез речи
    T-->>G: WAV
    G-->>D: HTTP response · audio/wav
    D->>U: воспроизводит ответ
```

Gateway архивирует turn по UUID: исходное аудио, transcript, ответ и metadata. Наличие STT, agent и TTS зависит от конфигурации; для полного голосового цикла должны быть настроены все три стадии.

### V2 · задача с сохранением состояния

```mermaid
sequenceDiagram
    participant D as StickS3
    participant G as Voice Gateway
    participant Q as Job store / worker
    participant S as STT → agent → TTS
    D->>G: POST /api/v2/voice/turns · UUID + PCM + device token
    G->>Q: сохраняет upload и создаёт queued job
    G-->>D: 202 · turn_id + request_id
    Q->>S: обрабатывает задачу по очереди
    D->>G: GET status (poll)
    G-->>D: queued / transcribing / thinking / synthesizing
    S-->>Q: готовый WAV
    D->>G: GET /turns/{id}/audio
    G-->>D: audio/wav
    D->>D: потоковое воспроизведение
```

Перед первой отправкой устройство создаёт UUID запроса. При потере ответа на upload оно проверяет этот же UUID, а не загружает запись с новым идентификатором. Gateway сохраняет v2 job и переживает рестарт процесса: queued задачи восстанавливаются, прерванные во время обработки автоматически не повторяются. Для одного устройства действует отдельное право доступа по паре `X-Device-Id` + bearer token.

## Прошивка

Цель — M5Stack StickS3 на ESP32-S3 с ESP-IDF и PlatformIO. Board-specific код отделён от voice state machine. Кнопка запускает push-to-talk; PCM S16LE, mono, 16 kHz передаётся частями, а ответ WAV проверяется и воспроизводится через ES8311.

Состояния: `BOOT → IDLE → RECORDING → PROCESSING → PLAYING → IDLE`; ошибка восстанавливается нажатием кнопки без перезагрузки. В v2 request ID и turn ID хранятся в NVS, а HTTP polling выполняется отдельно от основного UI loop.

Wi-Fi setup AP имеет SSID `Hermes-StickS3-Setup-XX`, где `XX` — последний байт Wi-Fi MAC. Если сохранённая сеть не выдала IP за минуту, устройство включает setup AP и продолжает повторные подключения. После получения IP AP останавливается. См. [иллюстрацию Wi-Fi fallback](assets/wifi-setup-flow.svg) и [инструкцию прошивки](flash-sticks3.md).

## Codex Agent Service

Это отдельное приложение на host-компьютере. Оно обращается к закреплённому `openai-codex` SDK и стандартной авторизации Codex локального пользователя. Сервис сохраняет SQLite-соответствие `device_id → thread_id`, принимает запросы от gateway по loopback и не монтирует домашний каталог Codex в контейнер. Один device продолжает собственный разговор; reset создаёт новую сессию. Детали — в [инструкции deployment](../deploy/codex-voice-agent.md).

## Archive, notes и метрики

Audio turn и служебные результаты сохраняются под `ARCHIVE_ROOT/YYYY/MM/DD/<turn-id>/`. Для v2 используется SQLite job database (по умолчанию внутри archive). `/health/live` проверяет только доступность процесса; `/health/ready` проверяет обязательную конфигурацию и возможность записи в archive. Временная недоступность удалённого STT или TTS сама по себе не делает процесс unhealthy. `/metrics` отдаёт Prometheus metrics без transcript и произвольного текста в labels.

В репозитории есть реализация `NoteStore` для Obsidian, но текущий активный voice pipeline не записывает заметки в vault. Не считайте наличие переменных `OBSIDIAN_*` подтверждением работающей записи заметок.

## Безопасность

- Gateway рассчитан на доверенную локальную сеть; текущая конфигурация использует HTTP, не TLS.
- Не публикуйте gateway или открытую setup AP напрямую в Internet.
- Для v2 настройте уникальный token для каждого устройства. MAC/device ID — идентификатор, но не секрет.
- Codex adapter должен слушать `127.0.0.1`; держите его token отдельно от device token.
- `.env`, Wi-Fi пароль, tokens и Codex credentials не сохраняйте в репозитории или логах.
