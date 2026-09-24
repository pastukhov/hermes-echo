# Hermes Voice Terminal · StickS3

Голосовой терминал на M5Stack StickS3: удерживайте кнопку, скажите фразу и отпустите её — устройство отправит запись в Voice Gateway и воспроизведёт ответ.

Документация: [Русский](docs/ru/index.md) · [English](docs/en/index.md).

Проект состоит из прошивки устройства, Python-шлюза и отдельного host-сервиса для Codex. По умолчанию шлюз использует Hermes; Codex можно включить настройкой. Ни прошивка, ни Docker-контейнер не получают доступ к Codex credentials.

![Схема компонентов и границ безопасности](docs/assets/system-overview.svg)

## С чего начать

1. [Настройте и запустите Voice Gateway](docs/development.md#запуск-шлюза).
2. Если хотите отвечать через Codex, настройте [host-side Codex Agent](deploy/codex-voice-agent.md). Для Hermes этот шаг не нужен.
3. Подключите StickS3 к Wi-Fi через [setup portal и прошейте устройство](docs/flash-sticks3.md).
4. Посмотрите [архитектуру и путь голосового запроса](docs/architecture.md), [HTTP-протокол](docs/protocol.md) или полный [указатель документации](docs/index.md).

## Быстрый запуск шлюза

Нужны Docker Compose и доступные endpoint’ы STT, Hermes (либо локальный Codex Agent) и TTS.

```sh
cp .env.example .env
# Откройте .env и настройте endpoint’ы и модели; секреты оставьте только в этом локальном файле.
docker compose up --build -d backend
curl --fail http://127.0.0.1:8080/health/live
curl --fail http://127.0.0.1:8080/health/ready
```

Compose запускает gateway с `network_mode: host`, поэтому он может обращаться к локальным сервисам на хосте. Проверьте сетевую изоляцию перед запуском в общей или недоверенной сети; не публикуйте gateway в Internet. Остальные проверки и запуск без Docker описаны в [руководстве разработки](docs/development.md).

## Подключение StickS3

Без сохранённых Wi-Fi настроек устройство поднимает открытую сеть `Hermes-StickS3-Setup-XX`, где `XX` — последние два hex-символа Wi-Fi MAC. Телефон обычно предлагает открыть setup portal автоматически; если нет, перейдите на `http://192.168.4.1/`.

В веб-форме выберите сеть и укажите Gateway endpoint:

- для **v1** — полный адрес, например `http://192.168.1.10:8080/api/v1/voice/turn`;
- для **v2** — только базовый адрес, например `http://192.168.1.10:8080`, и отдельный device token.

Сохранённая сеть получает минуту на подключение. Если подключиться не удалось, setup AP включится, а устройство продолжит попытки соединения. При успешном подключении AP выключится. В v2 gateway должен знать пару device ID → token; подробности — в [настройке устройства](docs/flash-sticks3.md) и [протоколе](docs/protocol.md).

## Голосовой запрос

Устройство использует half-duplex цикл: `ГОТОВ → СЛУШАЮ → ДУМАЮ → ОТВЕЧАЮ → ГОТОВ`. Состояние ошибки остаётся на экране до нажатия кнопки. Отдельная LED-индикация не используется.

В v1 запрос обрабатывается одним синхронным HTTP-вызовом. В v2 gateway сначала сохраняет аудио как задачу, затем устройство проверяет её состояние и скачивает WAV. v2 нужен отдельный bearer token на устройство.

## Содержимое репозитория

| Путь | Назначение |
| --- | --- |
| `firmware/` | ESP-IDF/PlatformIO прошивка M5Stack StickS3 |
| `backend/` | FastAPI Voice Gateway, Hermes/Codex orchestration, STT/TTS, archive и v2 jobs |
| `agent_service/` | локальный host-side адаптер Codex Python SDK |
| `deploy/` | systemd unit и установка Codex adapter |
| `docs/` | архитектура, протокол, разработка, прошивка и схемы |
| `docker-compose.yml` | запуск gateway в контейнере |
| `.env.example` | шаблон переменных без рабочих секретов |

## Важно

- Backend сохраняет голосовые запросы и результаты в archive. Проверьте путь и политику хранения перед использованием с личными записями.
- Codex provider и протокол v2 включаются явно. По умолчанию firmware использует v1, а gateway — Hermes.
- Setup AP открыт и предназначен только для локальной настройки.
- `.env`, Wi-Fi пароли, device tokens и Codex credentials нельзя коммитить.
