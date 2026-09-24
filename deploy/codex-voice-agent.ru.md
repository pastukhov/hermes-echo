# Установка Codex Voice Agent на хосте

Python SDK adapter запускается на хосте от пользователя, вошедшего в Codex. Codex credentials не копируются и не монтируются в Docker. Gateway обращается к adapter через host networking по адресу `127.0.0.1:8765`; systemd unit слушает только loopback.

Создайте отдельный случайный token adapter и задайте одно и то же значение в env-файле пользовательского сервиса и локальном `.env` gateway:

```sh
mkdir -p ~/.config/hermes-echo
install -m 600 deploy/codex-voice-agent.env.example \
  ~/.config/hermes-echo/codex-voice-agent.env
```

Замените пример token до запуска. В окружении gateway задайте `VOICE_AGENT_PROVIDER=codex`, `CODEX_AGENT_URL=http://127.0.0.1:8765` и соответствующий `CODEX_AGENT_TOKEN`. Для протокола v2 задайте `VOICE_DEVICE_TOKENS` как JSON-объект, сопоставляющий ID каждого устройства с его отдельным bearer token. Не используйте token adapter в качестве device token.

Войдите в Codex CLI от того же пользователя, затем включите и запустите unit:

```sh
systemctl --user daemon-reload
systemctl --user enable --now codex-voice-agent.service
curl --fail http://127.0.0.1:8765/health/live
curl --fail http://127.0.0.1:8765/health/ready
```

Рабочий каталог в unit предполагает, что репозиторий расположен в `~/hermes-echo`; исправьте путь, если он отличается. Readiness сообщает об ошибках авторизации, модели или запуска runtime, не раскрывая credentials. Пока `VOICE_AGENT_PROVIDER=codex` не задан явно, gateway продолжает использовать Hermes.
