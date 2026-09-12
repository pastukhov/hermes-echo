# Разработка и проверка

## Локальное окружение

Требуются Python 3.12+, `uv` или venv/pip, PlatformIO Core и ESP-IDF toolchain, совместимый с выбранным PlatformIO environment. Backend и firmware — независимые компоненты.

```bash
cp .env.example .env
python3.12 -m venv backend/.venv
. backend/.venv/bin/activate
python -m pip install -e 'backend[dev]'
```

Запускайте gateway из корня:

```bash
. backend/.venv/bin/activate
uvicorn voice_gateway.main:app --app-dir backend/src --host 127.0.0.1 --port 8080
```

Для LAN используйте `VOICE_BIND_HOST=0.0.0.0` только если понимаете границу доступа.

## Backend tests

```bash
cd backend
. .venv/bin/activate
pytest
pytest -q
```

Unit tests должны покрывать:

- Archive: PCM stream, корректный WAV header, metadata и error metadata.
- Hermes: valid response, invalid JSON, одна успешная repair attempt, repair failure, timeout.
- Notes: `create=false`, Markdown, sanitization имени, atomic write, duplicate title и filesystem error.
- Pipeline: FakeSTT, FakeHermes, FakeTTS и FakeNoteStore без реальных внешних сервисов.

Интеграционный fixture `tests/fixtures/test_voice.wav` проходит HTTP endpoint → FakeSTT → FakeHermes → FakeTTS → WAV response. Проверяются HTTP 200, archive, transcript, metadata, условная `.md` note и валидный WAV.

Для тестов задавайте временные `ARCHIVE_PATH`/vault и mock providers. Не используйте production API keys и реальные пользовательские записи.

## Firmware build и tests

```bash
cd firmware
pio run
pio test -e native
```

Hardware-independent тесты должны покрывать state machine, WAV parser, HTTP response handling, config validation и bounded retry/backoff. Аппаратные audio/I²S проверки выполняются на ATOM Echo отдельно. Прошивка должна собираться на ESP-IDF environment, не на Arduino.

Прошивка платы:

```bash
pio run -t upload
pio device monitor
```

Локальные Wi‑Fi secrets и device token держите в неотслеживаемом файле/секции конфигурации, не в git.

## Docker smoke check

```bash
docker compose up --build -d
curl -f http://127.0.0.1:8080/health/live
curl -f http://127.0.0.1:8080/health/ready
docker compose logs --tail=100

docker compose down
```

Проверьте, что archive и Obsidian volumes действительно смонтированы, а процесс в контейнере работает с ожидаемыми правами.

## Ручные acceptance scenarios

### A. Запись заметки

1. Соберите и прошейте firmware.
2. Дождитесь Wi‑Fi и LED `IDLE`.
3. Удерживайте кнопку и скажите: `Запиши заметку: купить новый USB-C кабель для лаборатории.`
4. Отпустите кнопку; LED должен перейти в processing.
5. Убедитесь, что в archive появился `input.wav`, а STT сохранил transcript.
6. Убедитесь, что Hermes вернул `note.create=true` и в Obsidian inbox появился `.md`.
7. Убедитесь, что TTS вернул WAV и ATOM произнёс короткий ответ.
8. Проверьте возврат LED в `IDLE`.

### B. Обычный вопрос

1. Удерживайте кнопку и скажите: `Сколько будет два плюс два?`
2. После отпускания дождитесь ответа `Четыре.` (или эквивалентного).
3. Убедитесь, что `note.create=false`, новая Obsidian note не появилась.
4. Убедитесь, что archive для voice turn создан всё равно.

## Definition of Done checklist

Перед релизом проверьте: PlatformIO/ESP-IDF build; Wi‑Fi и push-to-talk; потоковую передачу без полной ESP RAM записи; WAV archive; STT transcript; structured Hermes response; conditional Obsidian note; TTS/playback; recovery без reboot; health endpoints; Prometheus metrics; unit/integration tests; отсутствие secrets в git; документацию запуска. WireGuard и deep sleep должны отсутствовать в MVP.

## Безопасность и git

```bash
git status --short
git diff --check
git grep -n -E 'sk-[A-Za-z0-9]|Bearer [A-Za-z0-9._-]{20,}|API_KEY=.+' -- ':!*.md'
```

Последняя команда — только эвристическая проверка; дополнительно используйте secret scanner CI. `.env`, Wi‑Fi passwords, device tokens и provider keys не коммитьте. Не логируйте ключи, binary audio и transcript на INFO. Не отключайте тесты/validation ради прохождения CI.
