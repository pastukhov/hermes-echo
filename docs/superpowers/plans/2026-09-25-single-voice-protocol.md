# Единый голосовой протокол — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Удалить синхронный голосовой протокол и выбор версии из настройки StickS3, оставив один асинхронный путь с базовым адресом сервера и токеном устройства.

**Architecture:** Сохранить существующие маршруты `/api/v2/voice/...`, очередь задач и клиент polling/recovery. Удалить v1 из gateway и firmware без совместимого режима. Device API использует только пару MAC-based device ID и персональный bearer token; интерфейс настройки не показывает внутреннюю версию API.

**Tech Stack:** Python/FastAPI/pytest, ESP-IDF/C/PlatformIO/Unity, встроенная HTML/JavaScript-страница/Node test runner, Markdown RU/EN.

**Spec:** [Техническое задание](../specs/2026-09-24-single-voice-protocol-design.md).

**Дата:** 2026-09-25. Это план, не реализация. По просьбе пользователя здесь нет реализации функций; вместо неё приведены конкретные изменения, тестовые сценарии и команды проверки.

## Global Constraints

- В проекте один прототип. Не добавлять миграционный слой, совместимость старой NVS-схемы, скрытый fallback или временный релиз с двумя протоколами.
- Существующие `/api/v2/voice/...` остаются техническими URL. Не переименовывать их ради устранения выбора версии в UI.
- Upload: PCM S16LE mono 16 kHz, не более 3 840 000 байт. Playback: WAV PCM16 mono 24 kHz, только после `ready` и валидации.
- Стабильный UUID request ID, дедупликация, ограниченные повторы, отмена и восстановление активного запроса обязательны.
- Единственная авторизация device API — `X-Device-Id` и bearer token из `VOICE_DEVICE_TOKENS`. `VOICE_API_KEY` не добавляет вторую проверку.
- Не менять STT/TTS, выбор Hermes/Codex, host networking, формат архивов, аудиодрайвер и шрифты.
- Не стирать всю NVS и не удалять сохранённый Wi-Fi пароль ради перехода. Старый ключ версии допустимо оставить неиспользуемым.
- Не выводить секреты в команды, журнал, diff, метрики, архивы, конфигурационный GET и ответы API. MAC не является секретом.
- Обновить оба пользовательских комплекта документации. Исторические планы и спецификации не переписывать массовой заменой `v1`: OpenAI `/v1` и agent API не относятся к удаляемому device-протоколу.
- Автоматические проверки, целевая сборка и физический тест — отдельные этапы. Не считать прошивку проверенной по одним native-тестам.
- Коммиты ниже — шаги будущей реализации. Сам этот план не разрешает деплой, прошивку, изменение секретов, merge/rebase или push.

## Review Focus

1. Глобальный ключ отличается от device token: валидный device-запрос всё равно проходит через реальный middleware stack. Проверка в задаче 1.
2. Wi-Fi подключён, но token отсутствует или в NVS остался endpoint: доступны настройки, нет reboot loop и отправки на старый API. Проверки в задачах 3 и 6.
3. Сервер принял upload, но ответ потерян: сохраняется request ID, выполняется lookup без второго агентного запуска. Проверки в задачах 2 и 4.
4. Пустое поле token при сохранённом секрете и ошибка POST: первый сценарий сохраняет секрет, второй не объявляет успех и не перезагружает устройство. Проверка в задаче 3.
5. `ready` сопровождается JSON, усечённым WAV или неправильной частотой: проигрывание не начинается; ошибка остаётся читаемой до подтверждения. Проверки в задачах 4 и 6.

## Карта файлов и ответственности

Все пути ниже относительно `/home/artem/hermes-echo`.

| Область | Существующие файлы | Назначение изменения |
| --- | --- | --- |
| Gateway composition | `backend/src/voice_gateway/app.py`, `config.py`, `middleware.py` | Удалить v1 и конфликт авторизации, сохранить lifespan, middleware и наблюдаемость |
| Jobs/auth | `backend/src/voice_gateway/jobs/api.py`, `auth.py`, `store.py`, `worker.py` | Сохранить контракт задач, owner checks, dedup и recovery; менять store/worker только при найденной регрессии |
| Pipeline/logging | `backend/src/voice_gateway/pipeline.py`, `logging_config.py`, `metrics.py` | Не потерять стадии, архивы, заметки, метрики и structured logging при удалении inline v1 pipeline |
| Backend tests | `test_app_stream.py`, `test_middleware.py`, `test_pipeline.py`, `test_request_metrics.py`, `test_metrics_request_count.py`, `test_structured_logging.py`, `test_logging_config.py`, `jobs/test_api.py`, `jobs/test_auth.py`, `jobs/test_jobs.py`, `jobs/test_worker.py` под `backend/src/voice_gateway/` | Перенести поведенческое покрытие v1 на единственный путь, сохранить интеграционные проверки |
| Settings | `firmware/include/voice_settings.h`, `firmware/src/voice_settings.c` | Удалить protocol setting; обязательный token, базовый URL, сохранение turn IDs |
| Setup | `firmware/src/voice_config_httpd.c`, `firmware/test/web-ui.test.mjs` | Единая форма и полезные ошибки, read-only MAC, сохранённый token без раскрытия |
| Transport | `firmware/include/http_voice_client.h`, `firmware/src/http_voice_client.c`, `firmware/src/voice_turn_http.c`, `firmware/src/voice_turn_client.c`, `firmware/include/voice_turn_client.h` | Только async upload/lookup/poll/download/cancel |
| Firmware orchestration | `firmware/src/main.c`, `firmware/platformio.ini`, `firmware/src/CMakeLists.txt` | Убрать условные ветки; не ломать setup, audio ownership и recovery |
| Firmware tests | `firmware/test/test_main/test_main.c`, `firmware/test/test_main/fakes/hw_fakes.c`, `firmware/test/test_main/fakes/hw_fakes.h`, `firmware/test/test_voice_turn_client/test_voice_turn_client.c`, `firmware/test/test_wav_parser/test_wav_parser.c` | Проверить state machine, ограниченные повторы и отказ от невалидного audio |

Новые файлы: `backend/src/voice_gateway/test_device_api.py` — full-app контракт с fake providers; `firmware/test/test_voice_settings/test_voice_settings.c` — native settings cases. Если ESP-only HTTP transport нельзя протестировать существующим seam, добавить `firmware/test/test_http_voice_client/test_http_voice_client.c` с минимальным fake HTTP adapter, не перенося ESP-IDF в native-сборку.

## Порядок и исходная точка

Задачи выполнять последовательно: 0 → 1 → 2 → 3 → 4 → 5 → 6. Backend и firmware поставляются одним согласованным набором изменений; промежуточные коммиты не деплоить.

На момент составления плана: HEAD `b2c4db0`, рабочее дерево чистое, `main` на 3 коммита впереди и на 8 позади локального `origin/main`. Это снимок, не основание автоматически сливать ветки. В `app.py` на строках 645–681 обнаружены сохранённые маркеры конфликта. Последний коммит добавлял structured logging: эту работу необходимо сохранить.

### Задача 0. Получить воспроизводимую исходную точку

**Файлы:** `backend/src/voice_gateway/app.py`; связанные logging-тесты только при необходимости исправления обнаруженного конфликта.

**Интерфейс:** собираемый текущий checkout; без изменения API и без обновления работающего сервиса.

- [ ] Проверить `rtk git status -sb`, `rtk git log -8 --oneline`, `rtk git log --left-right --oneline main...origin/main`. Перед реализацией согласовать базовую ветку, если расхождение сохраняется; не делать автоматический pull/rebase.
- [ ] Командой `rtk proxy rg -n '^(<<<<<<<|=======|>>>>>>>)' backend/src firmware/src` зафиксировать маркеры. Проверить обе стороны по Git-истории, восстановить корректный Python, сохранив logging/metrics; не выбирать сторону вслепую.
- [ ] Используя проектное Python-окружение с `backend/requirements.txt`, выполнить `rtk proxy python -m pytest -q backend`. Зафиксировать исходные ошибки отдельно от будущих изменений протокола. Не обращаться к платным провайдерам.
- [ ] Запустить из `firmware/` `rtk proxy /home/artem/platformio/.venv/bin/platformio test -e native`, из корня `rtk proxy node --test firmware/test/web-ui.test.mjs`. Сохранить результаты как baseline, не ссылаться на прежние количества тестов.
- [ ] Если потребовалось исправление конфликтных маркеров, выделить его в коммит `fix: restore gateway source after logging merge`. Результат: тесты собираются, причина каждой оставшейся исходной ошибки установлена.

### Задача 1. Зафиксировать единственную авторизацию device API

**Файлы:** `middleware.py`, `config.py`, `app.py`, `jobs/api.py`, `jobs/auth.py`, `test_middleware.py`, новый `test_device_api.py`.

**Интерфейс:** сохранить `install_voice_job_routes(app, store, worker, device_tokens, *, reset_device=None)`; аутентификация per-device остаётся в jobs routes. Общий middleware продолжает корреляцию и прочую защиту, но не проверяет глобальный bearer для точного пространства `/api/v2/voice/`.

- [ ] Добавить full-app тест: с различающимися глобальным ключом и зарегистрированным device token корректный upload возвращает `202`. Проверять именно `create_app`, не только маршруты на пустом FastAPI.
- [ ] Добавить таблицу запросов ко всем device routes: нет ID; нет token; неверный token; неизвестный ID; token другого ID. Ожидать одинаковый `401`, отсутствие секретов/подтверждения существования ID. Правильная пара проходит auth.
- [ ] Выполнить `rtk proxy python -m pytest -q backend/src/voice_gateway/test_device_api.py backend/src/voice_gateway/test_middleware.py`. Новый позитивный тест должен показать существующий конфликт auth до исправления.
- [ ] Убрать глобальную проверку только с device routes; не превращать их в неаутентифицированный public API. Не расширять исключение на похожие пути за пределами точного префикса. Существующую политику остальных routes оставить неизменной.
- [ ] Сохранить correlation ID и rate limiting. Проверить фактический порядок middleware: route auth выполняется после входа middleware, поэтому rate limiter не должен рассчитывать на ещё не установленную owner identity. Не использовать сырой token в логах/labels; сохранить безопасный pre-auth лимит, не вводя новую систему квот.
- [ ] Добавить проверки correlation ID на успешном ответе и `401`, прежних `429`, отсутствия token в captured logs/metrics. Проверить варианты global key задан/не задан и `VOICE_AUTH_ENABLED` — ни один не отключает per-device auth.
- [ ] Запустить focused tests и весь backend; коммит `fix: use one device authentication policy`.

### Задача 2. Удалить синхронный gateway API, сохранив полезное покрытие

**Файлы:** `app.py`, `pipeline.py`, `jobs/test_api.py`, `jobs/test_jobs.py`, `jobs/test_worker.py`, `test_device_api.py`, перечисленные integration/metrics/logging tests.

**Интерфейс:** единственная цепочка upload `202` → status → `ready` → audio. Существующие request lookup, cancel и session reset остаются. Первая принятая загрузка имеет `queued`; идемпотентный повтор возвращает ту же задачу, не откатывая её актуальный статус.

- [ ] Добавить тест отсутствия legacy route в routing/OpenAPI и `404` для `/api/v1/voice/turn` при отключённой глобальной auth. При включённой глобальной auth middleware может вернуть `401` до routing; это не свидетельство сохранённого v1.
- [ ] Составить перечень поведения старых v1-тестов: STT/agent/TTS failures, archive metadata, заметки, metrics и logging. Перенести эти проверки на pipeline/full-app job flow, прежде чем удалять legacy transport tests.
- [ ] Удалить v1 handler, только его imports/helpers/settings и синхронный pipeline из `app.py`; сохранять startup/shutdown worker, provider wiring и health routes.
- [ ] Проверить structured stage logging на реально используемом job pipeline, включая errors/duration/correlation. Если оно было только внутри удалённого handler, перенести в `pipeline.py`, сохраняя ограничение на логирование транскриптов и не дублируя события.
- [ ] В `jobs/test_api.py` закрепить: одинаковые UUID+audio → одна задача и один provider run; тот же UUID с иным audio → conflict; новый upload >3 840 000 байт → rejection; два устройства не видят status/audio/lookup/cancel друг друга, foreign и unknown → `404`.
- [ ] В worker/store тестах закрепить restart queued recovery и явное terminal outcome для прерванной running-задачи по существующему контракту. Не обещать автоматический повтор неопределённо завершённого платного запроса.
- [ ] Full-app fake-provider тесты: длительная обработка не удерживает upload socket; потерянный `202` восстанавливается lookup; cancel не даёт скачать результат как готовый; terminal failure не становится успешным audio.
- [ ] Выполнить `rtk proxy python -m pytest -q backend`. Новые тесты должны сначала обнаружить legacy route/пропущенные гарантии, после изменений пройти. Коммит `refactor: remove synchronous voice endpoint`.

### Задача 3. Упростить настройки и сохранить доступ к portal

**Файлы:** `voice_settings.h`, `voice_settings.c`, `voice_config_httpd.c`, новый `test_voice_settings.c`, `web-ui.test.mjs`; минимальные согласованные изменения потребителей settings в `main.c` и `http_voice_client.h/.c`, необходимые для сборки после удаления поля.

**Интерфейс:** `voice_settings_t` без `protocol_version`; сохраняются Wi-Fi, `gateway_url`, MAC `device_id`, `device_token`, `request_id`, `turn_id`. Сигнатуры `voice_settings_load/save/valid` не меняются. GET `/config` возвращает только признак `device_token_set`, не token. POST не содержит версии; пустой token сохраняет существующий, но не разрешён без сохранённого значения.

- [ ] Добавить native settings cases: базовый URL и token валидны; отсутствующий token/host и endpoint path невалидны; terminal slash допустим; query/fragment/userinfo/пробельный host и переполнение буфера отклоняются. Использовать одинаковые входные примеры в UI-тестах и C URL builder.
- [ ] Добавить web tests: нет selector версии и редактируемого ID; MAC виден; новый token обязателен; сохранённый token не возвращается и не требует повторного ввода; SSID из списка передаётся верно; периодический refresh не затирает поля.
- [ ] Проверить падение новых тестов прежнего интерфейса, затем удалить поле версии, NVS read/write этого поля, selector и условную JS-валидацию. Defaults: только базовый URL; placeholder явно является примером, не обещанием найденного сервера.
- [ ] Подписи: «Адрес Voice Gateway», «Токен устройства»; подсказка о регистрации token для показанного MAC. Различать отсутствующий token, неправильный URL и отказ сервера, не выводя введённый секрет.
- [ ] Серверная валидация POST повторяет ограничения UI. Проверить сохранение пустого token только при существующем секрете, отказ при слишком длинных полях без усечения и сохранение Wi-Fi пароля при пустом поле пароля.
- [ ] Добавить тесты HTTP failure/сетевого исключения при save: видимое сообщение, форма доступна, ложного «saved» и restart нет. Действительно успешный save перезапускает после отправки ответа, а не обрывает его.
- [ ] Разделить готовность Wi-Fi и готовность voice client в `main.c`: отсутствие token/невалидный gateway не останавливает boot, не вызывает reboot loop и позволяет открыть setup даже при успешном Wi-Fi соединении. Не добавлять автоматическую конвертацию старого URL.
- [ ] Запустить native и Node tests, затем целевую сборку по задаче 6. Коммит `feat: simplify device provisioning to gateway and token`.

### Задача 4. Оставить единственный firmware transport и recovery

**Файлы:** `main.c`, `http_voice_client.h/.c`, `voice_turn_client.h/.c`, `voice_turn_http.c`, CMake при добавлении seam, `test_main`, `test_voice_turn_client`, `test_wav_parser`; новый transport test при необходимости.

**Интерфейс:** `http_voice_config_t` без версии; `http_voice_client_init(c, config)` требует базовый URL, ID, token и request ID. Заголовок `X-Protocol-Version`, пока он требуется существующему API, фиксирован как `2`, не является пользовательской настройкой. `voice_turn_client_*` и `voice_turn_build_upload_url` сохраняют существующие сигнатуры.

- [ ] Добавить transport assertions: только `/api/v2/voice/turns`, корректные ID/token/request headers, принятие `202` с валидным turn ID; `200` со старым WAV больше не является успешным upload, malformed JSON/401/413 не переходят в playback.
- [ ] Удалить синхронное чтение audio из upload response и все ветви выбора протокола в `main.c`; оставить отдельное скачивание audio после `ready`. Не переписывать ES8311/I2S и исправления щелчков.
- [ ] Проверить native state-machine cases: UUID сохраняется до upload; неопределённый исход upload ведёт в lookup с тем же UUID; найденный turn ID сохраняется; queued/running/stage statuses ожидаются без преждевременного завершения.
- [ ] Если backend upload завершён — восстанавливать через lookup, а не повторять запись. Повторная загрузка возможна только при наличии тех же сохранённых байтов; не обещать reupload после потери неперсистентного audio и не генерировать новый UUID для прежней попытки.
- [ ] Закрепить существующие ограничения времени fake clock: poll 1 секунда, backoff 1/2/4/5 секунд, общий deadline 180 секунд. Длительный запрос внутри deadline завершается; превышение приводит к видимой ошибке, не бесконечному ожиданию. Сетевые ошибки повторяются ограниченно, `401` не ретраится бесконечно.
- [ ] Проверить boot recovery по сохранённым request/turn IDs, cancel до/после получения turn ID и корректную очистку IDs на terminal outcome. Cancellation не запускает вторую работу и не оставляет активную task навсегда.
- [ ] Проверить ready+JSON, не-200, повреждённый/усечённый WAV, неверные channels/rate/bits: playback не стартует. Успешный WAV проигрывается один раз; после ошибки экран остаётся до подтверждения кнопкой, без LED и мигания.
- [ ] Выполнить native + Node tests и StickS3 build. Поиск `protocol_version` в активном firmware не даёт результатов; упоминание v1 допускается только в тесте его отсутствия. Коммит `refactor: keep only asynchronous device voice transport`.

### Задача 5. Описать один сценарий в RU и EN

**Файлы:** `README.md`, `README.en.md`; `docs/protocol.md`, `architecture.md`, `development.md`, `flash-sticks3.md`, `index.md`, `ru/index.md`; соответствующие `docs/en/` документы и `voice-terminal-context.md`; `deploy/codex-voice-agent.md`, `.ru.md`; `agent_service/README.md`, `README.ru.md`; `.env.example`, `docker-compose.yml`; `docs/assets/system-overview.svg`, `wifi-setup-flow.svg`, `wifi-setup-flow.en.svg` при наличии устаревших подписей.

**Интерфейс:** пользователь находит один набор настроек: Wi-Fi → gateway base URL → device token. Техническая protocol reference сохраняет точные `/api/v2/voice/...` пути.

- [ ] В обеих языковых версиях описать получение MAC из portal, создание случайного индивидуального token, запись соответствия в `VOICE_DEVICE_TOKENS`, применение конфигурации gateway, ввод того же token на устройстве. Только фиктивные значения в примерах; секрет не помещать в shell history/репозиторий.
- [ ] Удалить поддерживаемые сценарии v1/v2 selection, «optional» для нового token и требование полного endpoint. Объяснить, что пустое поле сохраняет уже записанный token и MAC не заменяет секрет.
- [ ] Исправить объяснение глобальной auth в соответствии с задачей 1. Существующие настройки не-device маршрутов документировать отдельно; не удалять `VOICE_API_KEY` из окружения вслепую.
- [ ] Runbook: различить invalid settings, `401`, gateway unreachable, job failed и invalid audio. Для `401` проверять соответствие MAC и наличия token без его вывода. Открытый setup AP — настройка только в доверенной локальной обстановке.
- [ ] Обновить диаграмму до upload → accepted → polling → audio; отразить однократную перенастройку прототипа без migration service. Проверить RU/EN ссылки и SVG подписи.
- [ ] Проверить поиском `rtk proxy rg -n 'protocol_version|api/v1/voice/turn|optional|VOICE_API_KEY' README.md README.en.md docs deploy .env.example docker-compose.yml`: разобрать каждое совпадение, исключая архивные документы и несвязанные optional-поля. Коммит `docs: document single voice setup in Russian and English`.

### Задача 6. Интеграционная и физическая приёмка

**Файлы:** создать после выполнения `docs/superpowers/reports/2026-09-25-single-voice-protocol-verification.md` с commit SHA, командами, результатами и ограничениями без секретов. Продуктовые исправления при найденной регрессии возвращать в соответствующую задачу с тестом.

**Интерфейс результата:** проверенный согласованный backend+firmware, отдельно зафиксированная фактическая проверка звука на прототипе.

- [ ] На финальном checkout выполнить `rtk proxy python -m pytest -q backend`, Node UI tests, PlatformIO native tests; из `agent_service/` выполнить `rtk proxy .venv/bin/python -m pytest -q tests`. Использовать fake providers; если проектное окружение отсутствует, восстановить зависимости из requirements, не заимствовать несовместимую venv.
- [ ] Из `firmware/` выполнить `rtk proxy /home/artem/platformio/.venv/bin/platformio run -e sticks3`. Build environment передавать без секретов в командной строке; runtime provisioning использовать вместо вшивания настоящего Wi-Fi пароля. Исправить устаревший комментарий esp32dev/ATOM в `platformio.ini`, не менять параметры платы без причины.
- [ ] Выполнить `rtk git diff --check`, поиск конфликтных маркеров и legacy transport. Проверить, что тесты не отключены ради зелёного результата, полезное старое покрытие перенесено, credentials не добавлены.
- [ ] После отдельного разрешения на deployment применить gateway и firmware из согласованного набора. Перед upload определить реальный serial port командой `rtk proxy /home/artem/platformio/.venv/bin/platformio device list`; не предполагать `/dev/ttyACM0`. Использовать текущую инструкцию StickS3 download mode; не делать erase_flash.
- [ ] Зарегистрировать MAC/token через локальную защищённую конфигурацию, затем сохранить base URL/token в portal. Проверить persistence перезапуском. Отдельно проверить пустой token и неверный token: понятная ошибка, доступность setup, нет reboot loop; восстановить правильный token.
- [ ] Вместе с пользователем проверить короткую фразу, долгий ответ, реальный звук без регулярных щелчков, возврат в «ГОТОВ», читаемую ошибку до нажатия. Снимок экрана не заменяет подтверждения воспроизведения.
- [ ] Контролируемый сетевой разрыв после приёма upload: восстановление без второй задачи. Перезапуск gateway с queued task и перезапуск устройства с сохранёнными IDs: корректный outcome без дублирования. Использовать fake/контролируемый provider для дорогих fault-injection проверок; не отключать общую сеть компьютера.
- [ ] Проверить cancel и отказ от неверного WAV автоматическими тестами; физически подтвердить, что ошибка не вызывает playback или зависание кнопки. Если пользователь не у устройства, отметить hardware gate как невыполненный, а не объявлять задачу полностью завершённой.
- [ ] Заполнить отчёт, коммит `test: record single protocol verification`. Merge/push — только по отдельному запросу пользователя, после проверки базовой ветки и итогового diff.

## Матрица завершения

| Требование | Проверка |
| --- | --- |
| Нет двух auth для устройства | Задача 1, full-app тест с различными ключами |
| Нет legacy route или firmware fallback | Задачи 2/4, route inspection, negative transport cases и поиск |
| Простая форма, секрет не раскрывается, ошибки сохранения понятны | Задача 3, C + Node tests и portal smoke |
| UUID/dedup/долгие jobs/recovery/cancel/ownership | Задачи 2/4, fake providers/clock, задача 6 на прототипе |
| Audio валиден и действительно слышен | Задачи 4/6, parser/transport tests и пользовательский sound test |
| Нет миграционного слоя или стирания Wi-Fi | Review задач 3/4 и проверка настроек в задаче 6 |
| Наблюдаемость и provider behavior сохранены | Задачи 0/2, logging/metrics/pipeline tests |
| Два полных комплекта актуальной документации | Задача 5, проверка RU/EN и ссылок |

План считается реализованным только после завершения автоматических проверок и отдельного hardware gate. Отсутствие обратной совместимости сокращает код, но не отменяет проверку доступности настройки и восстановления сетевых запросов.
