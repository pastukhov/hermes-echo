# Codex Voice Agent

Сервис на хосте поддерживает отдельный постоянный диалог Codex для каждого StickS3. Voice Gateway обращается к нему через loopback; прошивка и Docker-контейнер не получают Codex credentials.

## Runtime

- Python 3.12 в локальном `.venv`; зависимости закреплены в `requirements.txt`.
- Версия `openai-codex` и bundled Codex runtime закреплена на `0.156.1`.
- Сервис слушает `127.0.0.1:8765`; не привязывайте его к LAN-интерфейсу.
- Runtime SQLite по умолчанию расположен в `~/.local/state/hermes-echo/codex-voice/agent.sqlite3` и создаётся с ограниченными правами доступа.
- Credentials Codex остаются в стандартном Codex home. Не копируйте `auth.json`, credential tokens или весь Codex home в контейнер либо на устройство.

## Настройка

Пользовательский systemd unit читает env-файл с правами `0600` из `~/.config/hermes-echo/codex-voice-agent.env`. Задайте `CODEX_AGENT_TOKEN` отдельным bearer secret. Необязательные параметры: `CODEX_VOICE_CWD`, `CODEX_VOICE_MODEL` и `CODEX_VOICE_TURN_TIMEOUT` (не более 120 секунд). Gateway получает такой же token adapter через своё локальное окружение; не используйте вместо него device token.

Сервис хранит один активный conversation thread для каждого `device_id`. Request ID идемпотентны; повтор с другим текстом transcript отклоняется. Reset создаёт новый активный thread, сохраняя прежнюю историю в SQLite. Прерванный при рестарте запрос не воспроизводится автоматически.

## Локальные проверки

Fake-runtime тесты не обращаются к аккаунту:

```sh
.venv/bin/python -m pytest -q tests
```

Smoke-тест SDK отправляет реальный запрос Codex и может расходовать квоту. Он сохраняет только диагностику без секретов в игнорируемый Git файл `smoke-result.json`:

```sh
.venv/bin/python -m codex_voice.smoke
```

`GET /health/live` проверяет, что процесс работает; `GET /health/ready` — готовность SDK и модели. Readiness не проверяет STT/TTS или аудио устройства. Оставляйте gateway на Hermes, пока явно не переключите provider и не проведёте end-to-end тест на устройстве.
