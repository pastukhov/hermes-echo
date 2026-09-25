from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from codex_voice.config import RuntimeConfig
from codex_voice.runtime import CodexRuntime, RuntimeFailure
from codex_voice.smoke import run_smoke


@dataclass
class FakeResult:
    final_response: str | None = "Проверка"
    status: str = "completed"
    error: object | None = None
    items: list[object] | None = None

    def __post_init__(self) -> None:
        if self.items is None:
            self.items = []


class FakeTurn:
    def __init__(self, result: FakeResult, events: list[str]) -> None:
        self.result = result
        self.events = events
        self.started = asyncio.Event()
        self.block_forever = False

    async def run(self) -> FakeResult:
        self.started.set()
        if self.block_forever:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.events.append("local_task_cancelled")
                raise
        return self.result

    async def interrupt(self) -> None:
        self.events.append("remote_interrupt")


class FakeThread:
    def __init__(self, result: FakeResult, events: list[str]) -> None:
        self.id = "thread-voice-1"
        self.result = result
        self.events = events
        self.turn_args: dict[str, object] | None = None
        self.turn_handle: FakeTurn | None = None
        self.block_next_turn = False

    async def turn(self, prompt: str, **kwargs: object) -> FakeTurn:
        self.turn_args = {"prompt": prompt, **kwargs}
        self.turn_handle = FakeTurn(self.result, self.events)
        self.turn_handle.block_forever = self.block_next_turn
        return self.turn_handle


class FakeCodex:
    def __init__(self, result: FakeResult | None = None, *, requires_auth: bool = False,
                 account_type: str | None = None) -> None:
        self.events: list[str] = []
        self.result = result or FakeResult()
        self.requires_auth = requires_auth
        self.account_type = account_type
        self.closed = False
        self.started_args: dict[str, object] | None = None
        self.resumed_ids: list[str] = []
        self.thread = FakeThread(self.result, self.events)

    async def __aenter__(self) -> FakeCodex:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.closed = True

    async def account(self) -> SimpleNamespace:
        account = SimpleNamespace(type=self.account_type) if self.account_type else None
        return SimpleNamespace(requires_openai_auth=self.requires_auth, account=account)

    async def models(self) -> SimpleNamespace:
        return SimpleNamespace(
            data=[SimpleNamespace(id="gpt-6-sol", model="gpt-6-sol", is_default=True)]
        )

    async def thread_start(self, **kwargs: object) -> FakeThread:
        self.started_args = kwargs
        return self.thread

    async def thread_resume(self, thread_id: str, **_: object) -> FakeThread:
        self.resumed_ids.append(thread_id)
        self.thread.id = thread_id
        return self.thread


def _config(*, timeout: float = 10.0, model: str | None = None) -> RuntimeConfig:
    return RuntimeConfig(cwd="/tmp", model=model, turn_timeout_seconds=timeout)


def _factory(client: FakeCodex):
    return lambda: client


def test_run_uses_selected_model_and_read_only_permissions() -> None:
    async def scenario() -> None:
        client = FakeCodex()
        runtime = CodexRuntime(_config(), client_factory=_factory(client))
        await runtime.start()
        thread_id = await runtime.start_thread()
        response = await runtime.run(thread_id, "Ответь одним словом")

        assert response == "Проверка"
        assert client.started_args is not None
        assert client.started_args["model"] == "gpt-6-sol"
        assert client.thread.turn_args is not None
        assert client.thread.turn_args["sandbox"].value == "read-only"
        assert client.thread.turn_args["approval_mode"].value == "deny_all"
        await runtime.close()

    asyncio.run(scenario())


def test_explicit_unavailable_model_fails_without_default_fallback() -> None:
    async def scenario() -> None:
        client = FakeCodex()
        runtime = CodexRuntime(
            _config(model="not-in-catalog"), client_factory=_factory(client)
        )
        with pytest.raises(RuntimeFailure) as error:
            await runtime.start()

        assert error.value.code == "agent_model_unavailable"
        assert client.started_args is None
        assert client.closed

    asyncio.run(scenario())


def test_turn_authentication_failure_is_reported_without_provider_details() -> None:
    async def scenario() -> None:
        info = SimpleNamespace(root=SimpleNamespace(value="unauthorized"))
        result = FakeResult(
            final_response=None,
            status="failed",
            error=SimpleNamespace(codex_error_info=info, message="private auth response"),
        )
        client = FakeCodex(result, requires_auth=True)
        runtime = CodexRuntime(_config(), client_factory=_factory(client))
        await runtime.start()
        thread_id = await runtime.start_thread()
        with pytest.raises(RuntimeFailure) as error:
            await runtime.run(thread_id, "Проверка")

        assert error.value.code == "agent_auth_required"
        assert str(error.value) == "Codex authentication is required"
        await runtime.close()
        assert client.closed

    asyncio.run(scenario())


def test_sdk_turn_validates_auth_instead_of_provider_requirement_flag() -> None:
    async def scenario() -> None:
        client = FakeCodex(requires_auth=True)
        runtime = CodexRuntime(_config(), client_factory=_factory(client))
        await runtime.start()
        assert runtime.model == "gpt-6-sol"
        await runtime.close()

    asyncio.run(scenario())


def test_quota_error_maps_to_retryable_rate_limit() -> None:
    async def scenario() -> None:
        info = SimpleNamespace(root=SimpleNamespace(value="usageLimitExceeded"))
        result = FakeResult(
            final_response=None,
            status="failed",
            error=SimpleNamespace(codex_error_info=info, message="private provider text"),
        )
        client = FakeCodex(result)
        runtime = CodexRuntime(_config(), client_factory=_factory(client))
        await runtime.start()
        thread_id = await runtime.start_thread()

        with pytest.raises(RuntimeFailure) as error:
            await runtime.run(thread_id, "Проверка")

        assert error.value.code == "agent_rate_limited"
        assert error.value.retryable
        assert "private provider text" not in str(error.value)
        await runtime.close()

    asyncio.run(scenario())


def test_permission_request_is_not_auto_approved() -> None:
    async def scenario() -> None:
        result = FakeResult(
            final_response=None,
            items=[
                SimpleNamespace(
                    root=SimpleNamespace(type="commandExecution", status="declined")
                )
            ],
        )
        client = FakeCodex(result)
        runtime = CodexRuntime(_config(), client_factory=_factory(client))
        await runtime.start()
        thread_id = await runtime.start_thread()

        with pytest.raises(RuntimeFailure) as error:
            await runtime.run(thread_id, "Прочитай локальные файлы")

        assert error.value.code == "permission_required"
        assert client.thread.turn_args is not None
        assert client.thread.turn_args["approval_mode"].value == "deny_all"
        await runtime.close()

    asyncio.run(scenario())


def test_timeout_interrupts_remote_turn_before_local_task_cleanup() -> None:
    async def scenario() -> None:
        client = FakeCodex()
        runtime = CodexRuntime(_config(timeout=0.01), client_factory=_factory(client))
        await runtime.start()
        thread_id = await runtime.start_thread()
        client.thread.block_next_turn = True

        with pytest.raises(RuntimeFailure) as error:
            await runtime.run(thread_id, "Долго отвечающий запрос")

        assert error.value.code == "agent_timeout"
        assert client.events == ["remote_interrupt", "local_task_cancelled"]
        await runtime.close()

    asyncio.run(scenario())


def test_resume_uses_persisted_sdk_thread_id() -> None:
    async def scenario() -> None:
        client = FakeCodex()
        runtime = CodexRuntime(_config(), client_factory=_factory(client))
        await runtime.start()
        thread_id = await runtime.resume_thread("persisted-thread-id")
        assert thread_id == "persisted-thread-id"
        assert client.resumed_ids == ["persisted-thread-id"]
        await runtime.close()

    asyncio.run(scenario())


def test_smoke_report_records_versions_model_and_result_without_credentials() -> None:
    class ReportRuntime:
        sdk_version = "0.156.1"
        runtime_version = "0.156.1"
        model = "gpt-6-sol"
        provider = "codex"

        async def start(self) -> None:
            pass

        async def start_thread(self, *, ephemeral: bool = False) -> str:
            assert ephemeral
            return "ephemeral-thread"

        async def run(self, _thread_id: str, _prompt: str) -> str:
            return "Готово"

        async def close(self) -> None:
            pass

    report = asyncio.run(run_smoke(runtime=ReportRuntime()))

    assert report["provider"] == "codex"
    assert report["sdk_version"] == "0.156.1"
    assert report["runtime_version"] == "0.156.1"
    assert report["model"] == "gpt-6-sol"
    assert report["reply"] == "Готово"
    assert report["duration_ms"] >= 0
    assert "token" not in str(report).lower()


def test_writer_conflict_forks_history_and_reuses_owned_thread() -> None:
    from openai_codex.errors import InvalidRequestError

    class LockedCodex(FakeCodex):
        async def thread_resume(self, thread_id, **kwargs):
            self.resumed_ids.append(thread_id)
            raise InvalidRequestError(-32600, f"thread {thread_id} already has an active writer")

        async def thread_fork(self, thread_id, **kwargs):
            self.forked_id = thread_id
            self.fork_args = kwargs
            self.thread.id = "forked-thread"
            return self.thread

    async def scenario():
        client = LockedCodex()
        runtime = CodexRuntime(_config(), client_factory=_factory(client))
        await runtime.start()
        assert await runtime.run("desktop-owned", "Проверка") == "Проверка"
        assert client.forked_id == "desktop-owned"
        assert client.fork_args["sandbox"].value == "read-only"
        assert client.fork_args["approval_mode"].value == "deny_all"
        assert await runtime.resume_thread("forked-thread") == "forked-thread"
        assert client.resumed_ids == ["desktop-owned"]
        assert await runtime.run("forked-thread", "Ещё раз") == "Проверка"
        await runtime.close()

    asyncio.run(scenario())


def test_other_resume_errors_do_not_fork() -> None:
    from openai_codex.errors import InvalidRequestError

    class BrokenCodex(FakeCodex):
        async def thread_resume(self, thread_id, **kwargs):
            raise InvalidRequestError(-32600, "thread does not exist")

        async def thread_fork(self, *args, **kwargs):
            pytest.fail("unrelated failures must not fork")

    async def scenario():
        runtime = CodexRuntime(_config(), client_factory=_factory(BrokenCodex()))
        await runtime.start()
        with pytest.raises(RuntimeFailure):
            await runtime.resume_thread("missing")
        await runtime.close()

    asyncio.run(scenario())
