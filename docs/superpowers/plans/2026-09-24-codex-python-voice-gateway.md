# Codex Python SDK Voice Gateway Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Independent review may be delegated after each complete contract change. Steps use checkbox syntax for tracking. This document is a plan, not authorization to execute a migration immediately.

**Goal:** Заменить Hermes в голосовом тракте StickS3 на локальный Codex через официальный Python SDK, сохранять контекст разговора и доставлять голосовой ответ без зависимости от 15-секундного HTTP-тайм-аута.

**Architecture:** Существующий Docker Voice Gateway принимает PCM, выполняет STT/TTS и хранит аудиоархив. Отдельный пользовательский сервис `codex-voice-agent` на Linux-хосте управляет одним долгоживущим `AsyncCodex`, его app-server и диалогами устройств. Прошивка использует версионированный асинхронный HTTP-протокол; текущий v1 остаётся доступным для отката.

**Tech Stack:** Python 3.12, `openai-codex==0.156.1` с поставляемой им версией runtime, FastAPI/httpx, SQLite, systemd --user, существующие STT/TTS, ESP-IDF 5.5.3 и PlatformIO `sticks3`/`native`.

**Spec:** Раздел «Проектные решения» этого документа — спецификация новой интеграции. Исходная аппаратная спецификация: `docs/superpowers/specs/2026-09-22-sticks3-hermes-terminal-design.md`. Для v2 этот план заменяет требование ответа на том же HTTP-соединении; v1 сохраняет исходный контракт. Поздние пользовательские требования имеют приоритет: Montserrat, отсутствие отдельной LED-индикации, MAC ID, setup AP с автоматическим восстановлением и удержание ошибки до подтверждения кнопкой.

## Global Constraints

- Рабочий проект: `/home/artem/hermes-echo`. `/home/artem/.hermes` — состояние другого агента; не переносить и не переписывать его.
- В дереве уже много пользовательских изменений. Перед реализацией зафиксировать список затрагиваемых файлов; не сбрасывать дерево, не коммитить сторонние изменения.
- В этой итерации меняется исполнитель запросов, но сохраняются провайдеры STT/TTS, аудиоархив и формат `reply`/`note`.
- Не менять `network_mode: host`, не монтировать домашнюю папку или Codex credentials в контейнер.
- Python SDK запускается на хосте от пользователя `artem`. Аутентификацию выполняет штатный Codex runtime; приложение не извлекает и не пересылает OAuth-токены.
- SDK использовать с его закреплённым runtime. Не подставлять системный Codex CLI 0.155.1 в `codex_bin` без отдельной проверки совместимости.
- Модель: `CODEX_VOICE_MODEL`, если задана; иначе текущая настроенная модель Codex. Записывать фактические model/provider в технические метаданные. Скрытого переключения на Hermes или другой провайдер нет.
- Запись: PCM S16LE, mono, 16000 Hz. Ответ устройства: WAV PCM S16LE, mono, 24000 Hz, соответствующий текущему ES8311 playback.
- Сохранять предел записи `MAX_RECORD_SECONDS_DEFAULT = 120`; верхняя граница PCM — 3 840 000 байт. Переполнение отклонять до запуска STT.
- Локальные тесты используют поддельные SDK/STT/TTS. Реальные запросы выполнять только на отдельном smoke-этапе, отмечая расход квоты.
- Компиляция и тесты на ПК не доказывают слышимость. Завершение требует физического теста StickS3.

## Review Focus

1. Ответ `202` потерян после принятия записи: повтор с тем же request ID возвращает тот же turn, без второго вызова агента (задачи 3–4).
2. Две реплики одного устройства и реплики разных устройств: порядок контекста сохраняется, история и ответы не смешиваются (задача 2).
3. Перезапуск в середине агентного действия: неопределённый результат помечается `interrupted`, действие автоматически не повторяется (задачи 2–4).
4. Истёкшая авторизация, лимит аккаунта, запрос разрешения или недоступная модель: понятный конечный статус, отсутствие скрытого fallback и зависшего запроса (задачи 1–3).
5. Wi-Fi пропал во время ожидания или WAV оборвался: опрос можно продолжить, JSON не попадает в динамик, частичное аудио не объявляется успешным (задачи 4–6).

## Проектные решения

### Область первой версии

Один голосовой диалог на устройство; продолжение разговора после перезапуска; короткие ответы на русском; доступ к выбранному локальному проекту для чтения. Публичный сервис, несколько пользователей, перенос текущего открытого чата Codex, Claude adapter, новые STT/TTS и произвольное выполнение голосовых команд вне проекта в эту итерацию не входят.

Начальный профиль Codex — `Sandbox.read_only`. `CODEX_VOICE_CWD` задаётся администратором сервиса, а не содержимым голосового запроса. Рабочая папка сама по себе не является файловой песочницей: проверять фактические ограничения runtime. Любое действие, требующее отдельного разрешения, в первой версии завершается статусом `permission_required`; безусловное разрешение инструментов не включать. Последующее расширение до редактирования проектов требует отдельного сценария подтверждения, а не изменения одного флага на full access.

### Поток данных

```text
StickS3 -- PCM upload --> Voice Gateway (Docker)
                           | STT
                           v
                      codex-voice-agent (host, 127.0.0.1:8765)
                           | Python AsyncCodex -> local app-server
                           | device_id -> persisted thread_id
                           v
                      validated reply + optional note
                           | TTS -> atomic reply.wav
StickS3 <-- status poll / WAV download -- Voice Gateway
```

Шлюз сохраняет публичный LAN-порт 8080. Adapter слушает только loopback 8765 и принимает отдельный bearer token шлюза. Токен adapter не равен Codex credentials и не выдаётся устройству. Существующая host-сеть контейнера уже позволяет обращаться к loopback; новую сеть не создавать.

### Контракты приложения

Новый нейтральный тип `AgentRequest` содержит `request_id`, `device_id`, `transcript`; `AgentReply` — `reply`, `note`, `thread_id`, `model`. `note` сохраняет поля существующего `HermesNote`. Тип `AgentFailure` содержит только ограниченный `code`, пригодный для показа `message` и `retryable`; provider payload и секреты наружу не выходят.

```python
@dataclass(frozen=True)
class AgentRequest:
    request_id: str
    device_id: str
    transcript: str

class AgentClient(Protocol):
    async def complete(self, request: AgentRequest) -> AgentReply: ...
    async def cancel(self, request_id: str) -> None: ...
```

Это проектный интерфейс, а не утверждение о названиях методов SDK. Низкоуровневые вызовы resume/events/interrupt изолируются в `CodexRuntime`; их точные сигнатуры закрепляются проверкой установленного 0.156.1 в задаче 1. Из официальной документации уже подтверждены:

```python
from openai_codex import AsyncCodex

async with AsyncCodex() as codex:
    thread = await codex.thread_start()
    result = await thread.run("Ответь коротко по-русски: проверка связи")
    text = result.final_response
```

В рабочем сервисе контекст SDK живёт весь lifespan процесса. Модель получает схему JSON `reply`/`note` и указание отвечать кратко; используем schema output, если он подтверждён в закреплённой версии SDK. В любом случае выполняется локальная валидация. При невозможности получить корректную структуру — `agent_invalid_response`, без второго незаметного запроса модели. Commentary, tool output и промежуточные рассуждения не озвучиваются.

### Внутренний adapter API

- `POST /v1/agent/turns`: `AgentRequest`, ответ `202 {request_id, status}`; обработка продолжается независимо от HTTP-соединения.
- `GET /v1/agent/turns/{request_id}`: `queued|running|completed|failed|cancelled|interrupted`, результат или `AgentFailure`.
- `POST /v1/agent/turns/{request_id}/cancel`: идемпотентная отмена.
- `POST /v1/agent/devices/{device_id}/reset`: закрыть текущую привязку и создать новую при следующей реплике; при активной работе вернуть `409`.
- `/health/live`: процесс жив. `/health/ready`: локальная БД/SDK готовы. Отдельный диагностический статус сообщает авторизацию/доступность runtime без платной генерации.

SQLite adapter хранит mapping устройств, поколения диалога, request IDs, статусы и результаты. Не более одной незавершённой реплики на устройство и двух одновременно исполняемых реплик всего. При занятости устройства новый request ID получает `409 device_busy`; ограниченная общая очередь содержит максимум 8 запросов, далее `429 agent_busy`. Уникальный request ID повторно не запускается; другой transcript с тем же ID даёт `409 idempotency_conflict`.

### Публичный протокол v2

- `POST /api/v2/voice/turns`: потоковый PCM, существующие аудиозаголовки, `X-Protocol-Version: 2`, `X-Request-Id` (UUID), `X-Device-Id`, device bearer token. После полного сохранения/проверки аудио и надёжной постановки задания — `202 {turn_id, status:"queued"}`.
- `GET /api/v2/voice/turns/{turn_id}`: JSON состояния `queued|transcribing|thinking|synthesizing|ready|failed|cancelled|interrupted`. Для `ready` — относительный `audio_path`; для ошибки — `code`, `message`, `retryable`.
- `GET /api/v2/voice/requests/{request_id}`: найти уже принятую запись своего устройства после потери `202`; вернуть `turn_id` и состояние. Для ещё принимаемого тела — `409 upload_in_progress`, для неизвестного request ID — `404`. Чужие записи также дают `404`.
- `GET /api/v2/voice/turns/{turn_id}/audio`: `200 audio/wav` только после атомарной публикации целого WAV. До готовности `409`, при ошибке JSON; клиент проверяет status и Content-Type до WAV parser.
- `POST /api/v2/voice/turns/{turn_id}/cancel`: отмена задания и активного Codex turn; повтор безопасен.
- `POST /api/v2/voice/conversation/reset`: сброс разговора своего устройства; для MVP вызывается обслуживающей CLI, отдельного экранного меню не требуется.

MAC — идентификатор, не секрет. Для v2 нужен проверяемый device token, связанный с device_id; сервер проверяет владельца на всех status/audio/cancel/reset endpoint. Существующий optional-token v1 не меняет семантику. Токен для v2 вводится через существующий setup portal; его проверка нужна до первого агентного вызова.

Уникальность загрузки: `(device_id, X-Request-Id)`. Первая загрузка резервирует запись, пишет `.part`, считает SHA-256 и атомарно публикует PCM; незавершённая загрузка не запускает worker. При повторе завершённой загрузки сервер ограниченно вычитывает тело, проверяет формат/хеш и возвращает прежний turn. Одновременная загрузка того же ID — `409 upload_in_progress`; другое аудио с тем же ID — `409 idempotency_conflict`. Потеря соединения после `202` не отменяет принятый turn.

StickS3 передаёт звук потоком и не хранит полную запись для повторной загрузки. Поэтому при потере `202` устройство восстанавливает результат через request lookup, а не выдумывает повторную отправку PCM из 32 KiB ring buffer. Если upload действительно оборвался до приёма полного тела, запись завершается ошибкой с предложением повторить фразу; частичное аудио не передавать агенту. Автоматический повтор полного upload допускается только у клиентов, у которых исходное аудио реально сохранено.

SQLite gateway лежит в существующем archive volume. Один worker-процесс gateway; блокирующие STT/TTS выносятся из asyncio event loop в ограниченный executor. Нельзя считать `FastAPI BackgroundTasks` надёжной очередью. На старте `queued` задания можно обработать, а оборванные активные этапы пометить `interrupted`; неизвестные агентные действия автоматически не воспроизводить. На стороне adapter повторный request ID должен позволять получить уже записанный результат без повторного выполнения.

Опрос устройства — раз в 1 секунду; кратковременные сетевые ошибки — повтор с интервалами 1/2/4 секунды, затем максимум 5 секунд. Тайм-аут ожидания отдельной сетевой операции чтения/записи — 5 секунд; полный upload может длиться до 120 секунд, пока продолжается передача. Время ожидания задания — максимум 180 секунд после приёма записи; бюджет STT 30 секунд, Codex 120 секунд, TTS 30 секунд укладывается в общий deadline, а не суммируется поверх него. По истечении deadline — отмена и конечная ошибка. SDK timeout должен останавливать агентный turn: одной отмены Python coroutine недостаточно.

## Карта файлов

| Файлы | Ответственность |
|---|---|
| `agent_service/requirements.txt`, `agent_service/codex_voice/config.py` | Отдельная зависимость SDK и конфигурация host-сервиса |
| `agent_service/codex_voice/runtime.py` | Единственное место зависимости от конкретных SDK методов |
| `agent_service/codex_voice/store.py`, `service.py`, `app.py` | Диалоги, дедупликация, обработка и loopback API |
| `agent_service/tests/` | Поддельный runtime, проверка конкурентности, отмены и перезапуска |
| `backend/src/voice_gateway/agents/{base,models,codex_client}.py` | Нейтральный контракт и HTTP-клиент adapter |
| `backend/src/voice_gateway/pipeline.py` | Существующие STT → agent → TTS и архивирование как отдельный вызов |
| `backend/src/voice_gateway/jobs/{store,worker,api,auth}.py` | Долговечные задания, протокол v2 и владельцы устройств |
| `backend/src/voice_gateway/app.py`, `health.py`, `config.py`, `metrics.py` | Wiring, lifespan, readiness и метрики нового provider |
| `backend/common/error_codes.py` | Новые `agent_*` коды без удаления старых `hermes_*` |
| `firmware/src/voice_turn_client.c`, `include/voice_turn_client.h` | v2 upload, poll, download и cancel |
| `firmware/src/main.c`, `state_machine.c`, `screen_ui.c` и соответствующие headers | Состояния ожидания/отмены и понятные ошибки |
| `firmware/src/voice_settings.c`, `voice_config_httpd.c` и соответствующие headers | Выбор версии протокола и пояснение токена |
| `firmware/test/test_voice_turn_client/`, `test_main/`, `test_screen_ui/` | Контракт HTTP и переходы состояний |
| `deploy/codex-voice-agent.service`, `.env.example`, `docker-compose.yml`, `docs/` | Запуск, конфигурация, эксплуатация и откат |

Существующие `hermes/` не переименовывать массово. V1 и внедрённые в тесты Hermes fakes сохраняются; новый provider явно выбирается `VOICE_AGENT_PROVIDER=codex`.

## Этапы реализации

### Задача 1. Закрепить SDK и доказать текстовый обмен

**Files:** создать `agent_service/requirements.txt`, `agent_service/codex_voice/config.py`, `runtime.py`, `agent_service/tests/test_runtime.py`, `agent_service/smoke.py`.

**Interfaces:** `CodexRuntime.start()`, `start_thread() -> str`, `resume_thread(thread_id)`, `run(thread_id, prompt) -> str`, `interrupt(thread_id)`, `close()`. Это обёртка проекта; остальные компоненты не используют SDK напрямую.

- [x] Создать отдельный venv на Python 3.12; установить `openai-codex==0.156.1`, закрепить остальные зависимости и записать фактическую версию bundled runtime. Не менять глобальную установку Codex.
- [x] Проверить документацию и сигнатуры установленного SDK для resume, event stream, interrupt, account/model discovery и запроса разрешений. Version-specific код оставлен в `runtime.py`; provider auth requirement не используется как доказательство пользовательского login.
- [x] Поддельным SDK воспроизвести: финальный ответ, auth error, quota error, timeout и разрешение инструмента. `test_timeout_interrupts_remote_turn` проверяет вызов interrupt до очистки локального task; `test_permission_request_is_not_auto_approved` — отказ и конечный `permission_required`.
- [x] Реализовать lifespan-обёртку с одним процессом SDK и read-only профилем. Конфигурацию модели разрешать до первого запроса; не начинать fallback при недоступной модели.
- [x] Запустить `pytest -q agent_service/tests/test_runtime.py` в отдельном venv: 9 passed. Реальный текстовый smoke успешен: provider `codex`, SDK/runtime `0.156.1`, model `gpt-6-astra`, 6455 ms, ответ «Работает»; credentials не записывались.

**Готово:** SDK отвечает из отдельного host-процесса с выбранной учётной записью; interruption и ошибки имеют проверенный контракт. Если SDK не способен безопасно остановить turn, дальше не переходить до решения этой несовместимости.

### Задача 2. Контекст, дедупликация и границы параллелизма

**Files:** создать `agent_service/codex_voice/store.py`, `service.py`, `agent_service/tests/test_sessions.py`.

**Interfaces:** `AgentService.submit(AgentRequest) -> JobSnapshot`, `get(request_id) -> JobSnapshot`, `cancel(request_id)`, `reset(device_id)`. `JobSnapshot` содержит `request_id`, `device_id`, `status`, optional `AgentReply`, optional `AgentFailure`.

- [ ] Создать SQLite tables `device_sessions(device_id, generation, thread_id)`, `requests(request_id, device_id, input_hash, status, result_json, error_code, created_at, updated_at)`; транзакционная уникальность request ID обязательна.
- [ ] Написать тесты, которые закрывают реальные пользовательские сценарии:

```python
async def test_same_device_continues_after_restart(service_factory, runtime):
    first = service_factory(runtime)
    await first.complete_for_test("mic-a", "r1", "Запомни слово: кедр")
    thread_id = first.session_for("mic-a")
    await first.close()
    second = service_factory(runtime)
    await second.complete_for_test("mic-a", "r2", "Какое слово?")
    assert second.session_for("mic-a") == thread_id
    assert runtime.calls[-1].thread_id == thread_id
```

Тестовый harness `service_factory` предоставляет `complete_for_test`, `session_for` и временную БД; runtime fake записывает вызовы, но тест проверяет маршрутизацию сервиса, а не реализацию SDK.

- [ ] Добавить сценарии: `mic-a` и `mic-b` имеют разные thread IDs; повтор `r1` не вызывает runtime снова; другой текст с `r1` отвергается; новый turn занятого устройства даёт `device_busy`; третья параллельная работа остаётся в ограниченной очереди.
- [ ] Реализовать транзакционное принятие, последовательную обработку устройства, semaphore на 2 worker и очередь на 8. При рестарте незавершённый `running` переводить в `interrupted`; не повторять run автоматически.
- [ ] Проверить reset: активный turn → `409`; после завершения следующий turn получает новый thread; старая история остаётся доступна для диагностики.
- [ ] Выполнить `pytest -q agent_service/tests/test_sessions.py`.

**Готово:** один MAC соответствует одному устойчивому разговору, повторы и перезапуск не приводят к повторному агентному действию.

### Задача 3. Host API и подключение provider к шлюзу

**Files:** создать `agent_service/codex_voice/app.py`, `agent_service/tests/test_api.py`, `backend/src/voice_gateway/agents/base.py`, `models.py`, `codex_client.py`, `test_codex_client.py`; изменить `app.py`, `config.py`, `health.py`, `backend/common/error_codes.py` и `test_runtime_providers.py`.

**Interfaces:** внутренний API выше; `CodexAgentClient.complete(AgentRequest) -> AgentReply` сначала submit, затем poll до terminal state. `cancel(request_id)` отправляет отдельную команду adapter.

- [ ] Зафиксировать pydantic-модели запросов/ответов и ограниченный набор ошибок: `agent_unavailable`, `agent_auth_required`, `agent_rate_limited`, `agent_timeout`, `agent_invalid_response`, `permission_required`, `interrupted`.
- [ ] Проверить loopback binding и bearer auth на всех рабочих endpoint, отсутствие секрета в access/error logs. Проверить idempotency conflict и cancel после completed.
- [ ] Вынести default provider selection: `hermes` использует существующий клиент; `codex` использует adapter URL/token. При `codex` отсутствие `HERMES_BASE_URL` не делает readiness красным. Отсутствие нужного adapter config возвращает понятный local readiness failure.
- [ ] Тесты HTTP-клиента: `202 → running → completed`, `202 → failed`, потеря ответа submit с повтором того же request ID, deadline с cancel. Число provider submissions должно остаться одним логическим запросом.
- [ ] Выполнить `pytest -q agent_service/tests/test_api.py backend/src/voice_gateway/agents backend/src/voice_gateway/test_runtime_providers.py backend/src/voice_gateway/test_health.py`.

**Готово:** gateway получает валидированный ответ Codex, не зависит от Hermes auth и не копирует Codex credentials в Docker.

### Задача 4. Надёжные голосовые задания и протокол v2

**Files:** создать `backend/src/voice_gateway/pipeline.py`, `jobs/store.py`, `worker.py`, `api.py`, `auth.py`, `jobs/test_jobs.py`, `jobs/test_api.py`; изменить `app.py`, `metrics.py`, `test_app_stream.py`; дополнить `docs/protocol.md`.

**Interfaces:** `VoicePipeline.run(turn_id, device_id, pcm_path, deadline) -> wav_path`; `VoiceJobStore.accept_upload(device_id, request_id)`, `commit_upload(...)`, `get_owned(...)`, `cancel(...)`. `pipeline` вызывается как старым v1 handler, так и новым worker; v1 остаётся синхронным.

- [ ] Вынести общий STT/agent/TTS pipeline без изменения существующих v1 fixtures, note payload и структуры аудиоархива. Для STT/TTS с синхронными клиентами использовать ограниченный executor и явные provider timeouts.
- [ ] Реализовать durable upload/queue протокол, SHA-256, owner checking и token-to-device mapping. В v2 отклонять неподписанный запрос до приёма большого тела.
- [ ] Написать проверку потерянного ответа и повторной доставки:

```python
async def test_duplicate_upload_runs_agent_once(client, audio, agent_spy, device_headers):
    headers = {**device_headers, "X-Request-Id": "9b69da5b-bd5d-44a3-9391-15e8dac36733"}
    first = await client.post("/api/v2/voice/turns", content=audio, headers=headers)
    again = await client.post("/api/v2/voice/turns", content=audio, headers=headers)
    assert first.status_code == again.status_code == 202
    assert first.json()["turn_id"] == again.json()["turn_id"]
    await agent_spy.wait_completed()
    assert agent_spy.calls == 1
```

Fixtures выдают одно корректное PCM, валидные device headers и worker с fake agent. Отдельно проверить другой hash, прерванный upload, превышение 3 840 000 bytes и одновременный submit.

- [ ] Проверить чужой token/device и чужой turn ID на status/audio/cancel/reset; существование чужой записи не раскрывать (404). Request ID и URL пути валидировать до обращения к файловой системе.
- [ ] Проверить request lookup: потерянный `202` восстанавливается без повторного PCM upload; чужой request ID не раскрывает turn; незавершённая загрузка никогда не возвращается как принятая и не вызывает агента.
- [ ] Проверить медленный fake agent (>15 секунд виртуального времени), полную цепочку статусов, responsive health/poll, общий deadline 180 секунд, отмену во время STT/Codex/TTS и восстановление после перезапуска.
- [ ] Публиковать `reply.wav` атомарно и проверять его формат 24 kHz mono S16LE до `ready`. Если TTS вернул другой формат, конечная ошибка формата вместо воспроизведения с неверной скоростью. `.part` никогда не отдавать.
- [ ] Выполнить `pytest -q backend/src/voice_gateway/jobs backend/src/voice_gateway/test_app_stream.py` и полный backend test suite один раз после успешных целевых тестов.

**Готово:** длительная работа агента не удерживает HTTP-запрос устройства, завершённое аудио можно повторно скачать без повторной генерации.

### Задача 5. V2 в прошивке StickS3

**Files:** создать `firmware/include/voice_turn_client.h`, `src/voice_turn_client.c`, `test/test_voice_turn_client/test_voice_turn_client.c`; изменить `src/main.c`, `include/state_machine.h`, `src/state_machine.c`, `src/screen_ui.c`, `include/screen_ui.h`, настройки/portal и относящиеся host fakes/tests.

**Interfaces:** `voice_turn_begin(request_id)`, `voice_turn_write(pcm, len)`, `voice_turn_finish() -> turn_id`, `voice_turn_poll() -> status`, `voice_turn_audio_read(buf, cap)`, `voice_turn_cancel()`. State machine вызывает общий клиент через hardware/transport seam; функции ESP HTTP не входят в native-тесты.

- [x] Добавить явную сохранённую настройку `protocol_version` со значением по умолчанию 1. Для v2 setup принимает gateway base URL и обязательный token; не угадывать версию по HTTP 404 и не обрезать произвольные URL.
- [x] Генерировать UUID request ID один раз на нажатие и сохранять его перед загрузкой. После `202` переходить в PROCESSING и опрашивать status. При потере `202` или перезапуске использовать request lookup; не повторять несохранённую аудиозапись. UUID turn ID сохранять для возобновления ожидания, удалять вместе с request ID после terminal state без записи NVS на каждый poll.
- [x] Реализовать отображение этапов (`СЛЫШУ / РАСПОЗНАЮ РЕЧЬ`, `ДУМАЮ`, `ГОТОВЛЮ / ОТВЕТ`) существующим Montserrat. KEY1 во время ожидания отменяет текущий turn; новая запись начинается отдельным нажатием. ERROR остаётся до отдельного подтверждения.
- [x] Сетевой обмен вынести в worker/task, чтобы пятисекундный timeout не блокировал кнопку и экран. На host fake clock проверить poll 1 sec, retry 1/2/4/5 sec, отсутствие busy loop и общий deadline.
- [x] До запуска playback проверить `200` и audio Content-Type; затем WAV format. JSON ошибки, `202`, `409`, `401` никогда не передавать в audio sink.
- [x] Native сценарии: delayed ready >15 sec, потерянный 202/request lookup, краткий обрыв Wi-Fi через retry, server restart lookup, cancel, невалидный WAV, premature EOF, повторный цикл; ERROR подтверждается отдельным нажатием.
- [x] Выполнить из `firmware`: `rtk proxy /home/artem/platformio/.venv/bin/platformio test -e native`, затем `rtk proxy /home/artem/platformio/.venv/bin/platformio run -e sticks3`.

**Готово:** прошивка собирается, v1 работает прежним образом, v2 проходит host сценарии ожидания и отмены без изменения аудиодрайвера.

### Задача 6. Отдельно проверить физический аудиотракт

**Files:** новый `firmware/src/audio_selftest.c`, при необходимости `include/audio_selftest.h`, отдельный env/flag в `platformio.ini`; отчёт `docs/validation/2026-09-24-codex-voice-smoke.md`.

- [ ] Сделать отдельную диагностическую сборку с коротким локальным 440 Hz тоном (250 ms, S16LE/24 kHz, умеренная амплитуда) через тот же ES8311 playback API. В обычной сборке автоматический тон выключен.
- [ ] Собрать, проверить USB-порт/MAC, прошить диагностическую сборку и снять serial log. Получить подтверждение, что тон слышен. При тишине исправлять аппаратный путь отдельно от SDK/HTTP.
- [ ] Вернуть обычную v2 сборку и проверить, что диагностический тон отсутствует. Не стирать NVS: сеть, token и device ID сохраняются.

**Готово:** звук на физическом динамике подтверждён и в отчёте отделён от результата backend-тестов.

### Задача 7. Запуск сервисов и сквозной тест

**Files:** создать `deploy/codex-voice-agent.service`, `agent_service/README.md`; изменить `.env.example`, `docker-compose.yml`, `docs/development.md`, `docs/flash-sticks3.md`, отчёт проверки.

- [ ] Добавить systemd user unit с ExecStart из отдельного venv, host working directory, restart-on-failure и штатным graceful shutdown/interrupt. Runtime state и SQLite держать в `~/.local/state/hermes-echo/codex-voice/`; credentials остаются в штатном хранилище Codex.
- [ ] В `.env.example` документировать `VOICE_AGENT_PROVIDER`, `CODEX_AGENT_URL`, `CODEX_AGENT_TOKEN`, `CODEX_VOICE_CWD`, optional `CODEX_VOICE_MODEL`. Реальные adapter/device tokens создавать во время развёртывания в отдельных локальных файлах с mode 0600, не добавлять в git и не выводить.
- [ ] Обновить Compose только для provider config, без смены сети и монтирования Codex home. Сохранить используемые STT/TTS параметры и тома. Убедиться, что v2 token можно внести через текущий setup portal.
- [ ] Проверить localhost adapter, account/runtime availability без генерации, provider-aware gateway readiness и отсутствие доступа к 8765 через LAN-IP.
- [ ] На устройстве выполнить: короткий вопрос; «запомни слово кедр»; «какое слово?»; перезапуск host adapter и продолжение; новый разговор; отмена долгого запроса; обрыв Wi-Fi во время ожидания и возвращение в сеть.
- [ ] Подтвердить как слышимый ответ, так и корректные serial/server события: одна генерация на request ID, непустой WAV, возврат в ГОТОВ, отсутствие I²S timeout и перезагрузок. Записать build hash, SDK/runtime/model, время каждого этапа, результат пользователя.
- [ ] Измерить latency на 5 коротких вопросах; сохранить фактические значения. Не обещать SLA до измерения. Если ответ не укладывается в 180 секунд, это явная terminal failure, не бесконечное ожидание.

**Готово:** устройство произносит ответ Codex, следующая реплика использует прежний контекст, сеть/сервисы можно перезапустить без повторного выполнения принятого запроса.

### Задача 8. Откат и завершение

**Files:** `docs/protocol.md`, `docs/architecture.md`, `agent_service/README.md`, отчёт проверки.

- [ ] Документировать новое распределение ответственности и соответствие device → thread; описать reset/cancel и ошибки авторизации/квоты.
- [ ] Перед переключением записать текущую конфигурацию provider без секретов и сохранить известный образ прошивки/его hash.
- [ ] Проверить откат на `VOICE_AGENT_PROVIDER=hermes` и protocol v1 с прежним endpoint, не удаляя историю Codex, архив или NVS. Это восстановление прежнего маршрута, не обещание работоспособности прежнего Hermes provider.
- [ ] Выполнить `git diff --check` и обзор только изменённых файлов. Коммиты — отдельными законченными этапами, если пользователь поручит коммитить; не добавлять чужие изменения.

## Критерии завершения

- [ ] Реальный SDK-запрос успешен; известны runtime, учётная запись без раскрытия credentials и выбранная модель.
- [ ] Диалог устройства сохраняется после перезапуска; новый разговор действительно очищает активный контекст.
- [ ] Медленный ответ не превращается в ошибку через 15 секунд.
- [ ] Повтор upload/status не вызывает второй агентный turn; неопределённые операции после crash не переигрываются автоматически.
- [ ] Устройство может отменить запрос; SDK turn не продолжает незаметно работать после отмены/deadline.
- [ ] Авторизация на LAN и loopback проверена; MAC не используется как пароль; чужие записи недоступны.
- [ ] STT/TTS, v1 и прежние firmware-настройки не регрессировали.
- [ ] Тон и голосовой ответ физически слышны; доказательства записаны в отчёте.
- [ ] Откат задокументирован и проверен без удаления пользовательских данных.

## Источники и проверенная база

- [Официальный Codex Python SDK](https://learn.chatgpt.com/docs/codex-sdk): `AsyncCodex`, сохранение диалогов, закреплённый runtime, Python >=3.10.
- [Codex App Server](https://learn.chatgpt.com/docs/app-server): threads/turns, события, interrupt и permissions. WebSocket transport помечен experimental; здесь используется локальный транспорт SDK.
- [Codex authentication](https://learn.chatgpt.com/docs/auth): штатные способы входа и credential storage; право входа не означает отсутствие квот.
- [PyPI openai-codex](https://pypi.org/project/openai-codex/0.156.1/): версия 0.156.1 проверена 2026-09-24. Перед выполнением проверить доступность именно этой версии; не подменять её latest автоматически.
- Текущий проект: `HermesClient.complete(transcript)` не получает device_id, поэтому нельзя надёжно добавить многотуровый разговор одной заменой URL; новые `AgentRequest` и mapping обязательны.
- Текущая прошивка: 15 000 ms ожидания HTTP в `src/main.c`; максимум записи 120 sec. Текущая аппаратная миграция на общий I²S/ES8311 собрана и прошита, но слышимость пока не подтверждена.

**Рекомендуемый порядок:** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8. Основная работа последовательная из-за общих контрактов; документацию и независимый обзор можно выполнять параллельно. После задачи 3 имеется проверяемый текстовый Codex adapter, после задачи 5 — готовый программный тракт, после задачи 7 — подтверждённый физический голосовой терминал.
