# Hermes Voice Terminal

Голосовой терминал на M5Stack ATOM Echo: кнопка запускает запись PCM, ESP32 потоково отправляет её в Python Voice Gateway, а gateway выполняет STT → Hermes → опциональную заметку → TTS и возвращает WAV для воспроизведения.

MVP работает в доверенной локальной Wi‑Fi сети и использует half-duplex цикл `RECORDING → PROCESSING → PLAYBACK`. Firmware остаётся «тупым» терминалом: она захватывает, отправляет, принимает и воспроизводит аудио; AI-логика, архив, Hermes и Obsidian находятся в backend.

## Состав

- `firmware/` — ESP-IDF/PlatformIO firmware для M5Stack ATOM Echo.
- `backend/` — Python 3.12+, FastAPI voice gateway.
- `docs/architecture.md` — компоненты и поток данных.
- `docs/protocol.md` — HTTP-контракт ESP ↔ gateway.
- `docs/development.md` — разработка и тесты.
- `docker-compose.yml` — запуск gateway в контейнере.
- `.env.example` — полный шаблон конфигурации без секретов.

## Быстрый старт

### 1. Клонирование

```bash
git clone <repository-url> hermes-voice-terminal
cd hermes-voice-terminal
```

Подставьте URL своего репозитория вместо `<repository-url>`.

### 2. Конфигурация

```bash
cp .env.example .env
$EDITOR .env
```

Заполните URL, модели и ключи STT/Hermes/TTS, пути архива и Obsidian. Файл `.env` не коммитьте. Backend должен слушать LAN только осознанно; не публикуйте его напрямую в Internet.

Минимально нужны `STT_BASE_URL`, `STT_API_KEY`, `STT_MODEL`, `HERMES_BASE_URL`, `HERMES_API_KEY`, `HERMES_MODEL`, `TTS_BASE_URL`, `TTS_API_KEY`, `TTS_MODEL`, `ARCHIVE_PATH`, `OBSIDIAN_VAULT_PATH` и `VOICE_DEVICE_TOKEN` (если включена проверка устройства).

### 3. Локальный backend

```bash
cd backend
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
cd ..
. backend/.venv/bin/activate
uvicorn voice_gateway.main:app --host 0.0.0.0 --port "${VOICE_BIND_PORT:-8080}"
```

Проверьте:

```bash
curl http://127.0.0.1:8080/health/live
curl http://127.0.0.1:8080/health/ready
curl http://127.0.0.1:8080/metrics
```

Ожидается `200` для live; ready дополнительно проверяет загруженную конфигурацию и доступность записи в archive (и note storage, если он включён).

### 4. Backend через Docker

Из корня проекта:

```bash
cp .env.example .env
# заполните .env
docker compose up --build
```

Compose монтирует архив в `/data/archive`, а vault в `/data/obsidian`. Для остановки:

```bash
docker compose down
```

Не добавляйте ключи в `Dockerfile`, compose-файл или git. Контейнер запускайте non-root, если это поддерживает выбранная конфигурация.

### 5. Настройка и прошивка ATOM Echo

Установите PlatformIO (CLI или IDE), USB-драйверы платы и подключите ATOM Echo. В firmware создайте локальную secrets-конфигурацию по примеру проекта и задайте Wi‑Fi, URL gateway, device ID и лимит записи. Секреты firmware не коммитьте.

```bash
cd firmware
pio run
pio run -t upload
pio device monitor
```

Проверьте в `platformio.ini`, что выбран ESP-IDF environment для ATOM Echo, а не Arduino framework. Gateway URL должен указывать на IP компьютера в LAN, например `http://192.168.1.20:8080`.

### 6. Первый голосовой запрос

1. Дождитесь мигающего синего LED во время подключения и слабого синего LED в `IDLE`.
2. Удерживайте кнопку и произнесите: `Запиши заметку: купить новый USB-C кабель для лаборатории.`
3. Отпустите кнопку.
4. Дождитесь обработки и ответа `Записал.` (или эквивалентного короткого ответа).
5. Проверьте `archive/YYYY/MM/DD/<turn-id>/input.wav`, transcript, metadata и Markdown-файл в `OBSIDIAN_INBOX`.

Обычный запрос `Сколько будет два плюс два?` должен вернуть `Четыре.`, не создавать заметку, но всё равно создать архив voice turn.

## Конфигурация

Полный список переменных и их назначение находится в `.env.example` и [docs/development.md](docs/development.md). Ключевые настройки: bind host/port, archive, STT/Hermes/TTS URL, key/model/timeout, Obsidian vault/inbox, включение transcript в заметке, device token, Wi‑Fi, `VOICE_GATEWAY_URL`, `DEVICE_ID`, `MAX_RECORD_SECONDS`.

## Тесты

```bash
# backend
cd backend
. .venv/bin/activate
pytest

# firmware hardware-independent/native tests
cd ../firmware
pio test -e native
```

Подробности, mock-провайдеры и acceptance-проверки: [docs/development.md](docs/development.md).

## Ограничения MVP

В MVP намеренно отсутствуют WireGuard, deep sleep, wake word, WebSocket и MP3/Opus/AAC. Аудио — PCM S16LE, 16 kHz, mono; передача ESP потоковая, без хранения полной записи в RAM. WireGuard и deep sleep — отдельный этап и не должны менять application protocol.

## Документация

- [Архитектура](docs/architecture.md)
- [HTTP protocol](docs/protocol.md)
- [Разработка, тесты и smoke tests](docs/development.md)
