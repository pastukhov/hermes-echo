from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from codex_voice.service import AgentRequest, AgentService, AgentServiceError


@dataclass
class RuntimeCall:
    thread_id: str
    prompt: str


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[RuntimeCall] = []
        self._thread_counter = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.interrupts: list[str] = []

    async def start(self) -> None:
        pass

    async def start_thread(self) -> str:
        self._thread_counter += 1
        return f"thread-{self._thread_counter}"

    async def resume_thread(self, thread_id: str) -> str:
        return thread_id

    async def run(self, thread_id: str, prompt: str) -> str:
        self.calls.append(RuntimeCall(thread_id, prompt))
        self.started.set()
        if self.block:
            await self.release.wait()
        return '{"reply":"reply","note":{"create":false,"title":"",' \
            '"content":"","tags":[]}}'

    async def interrupt(self, thread_id: str) -> None:
        self.interrupts.append(thread_id)
        self.release.set()

    async def close(self) -> None:
        pass


class ServiceFactory:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.counter = 0

    def __call__(
        self, runtime: FakeRuntime, *, max_workers: int = 2, max_queue: int = 8
    ) -> AgentService:
        self.counter += 1
        return AgentService(
            self.tmp_path / f"sessions-{self.counter}.sqlite",
            runtime,
            max_workers=max_workers,
            max_queue=max_queue,
        )

    async def complete_for_test(
        self, service: AgentService, device_id: str, request_id: str, transcript: str
    ) -> object:
        await service.submit(AgentRequest(request_id, device_id, transcript))
        return await service.wait(request_id)

    @staticmethod
    def session_for(service: AgentService, device_id: str) -> str | None:
        return service.session_for(device_id)


@pytest.fixture
def service_factory(tmp_path: Path) -> ServiceFactory:
    return ServiceFactory(tmp_path)


def test_same_device_continues_after_restart(service_factory: ServiceFactory) -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        database = service_factory.tmp_path / "persistent.sqlite"
        first = AgentService(database, runtime)
        await first.start()
        await service_factory.complete_for_test(first, "mic-a", "r1", "Запомни слово: кедр")
        thread_id = service_factory.session_for(first, "mic-a")
        assert thread_id is not None
        await first.close()

        second = AgentService(database, runtime)
        await second.start()
        await service_factory.complete_for_test(second, "mic-a", "r2", "Какое слово?")
        assert service_factory.session_for(second, "mic-a") == thread_id
        assert runtime.calls[-1].thread_id == thread_id
        await second.close()

    asyncio.run(scenario())


def test_different_devices_get_separate_codex_threads(service_factory: ServiceFactory) -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        service = service_factory(runtime)
        await service.start()
        await service_factory.complete_for_test(service, "mic-a", "a1", "Привет")
        await service_factory.complete_for_test(service, "mic-b", "b1", "Привет")

        assert service.session_for("mic-a") != service.session_for("mic-b")
        await service.close()

    asyncio.run(scenario())


def test_duplicate_request_is_idempotent_and_changed_text_conflicts(
    service_factory: ServiceFactory,
) -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        service = service_factory(runtime)
        await service.start()
        original = await service_factory.complete_for_test(service, "mic-a", "r1", "Привет")
        duplicate = await service.submit(AgentRequest("r1", "mic-a", "Привет"))

        assert duplicate.status == "completed"
        assert duplicate.reply == original.reply
        assert len(runtime.calls) == 1
        with pytest.raises(AgentServiceError) as error:
            await service.submit(AgentRequest("r1", "mic-a", "Другой текст"))
        assert error.value.code == "idempotency_conflict"
        await service.close()

    asyncio.run(scenario())


def test_second_request_for_busy_device_is_rejected(service_factory: ServiceFactory) -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.block = True
        service = service_factory(runtime)
        await service.start()
        await service.submit(AgentRequest("r1", "mic-a", "Первый запрос"))
        await runtime.started.wait()

        with pytest.raises(AgentServiceError) as error:
            await service.submit(AgentRequest("r2", "mic-a", "Второй запрос"))
        assert error.value.code == "device_busy"
        runtime.release.set()
        assert (await service.wait("r1")).status == "completed"
        await service.close()

    asyncio.run(scenario())


def test_global_worker_and_queue_limits_are_enforced(service_factory: ServiceFactory) -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.block = True
        service = service_factory(runtime, max_workers=2, max_queue=2)
        await service.start()
        for index in range(2):
            await service.submit(AgentRequest(f"r{index}", f"mic-{index}", "hold"))
        while len(runtime.calls) < 2:
            await asyncio.sleep(0)

        await service.submit(AgentRequest("queued-1", "mic-q1", "wait"))
        await service.submit(AgentRequest("queued-2", "mic-q2", "wait"))
        with pytest.raises(AgentServiceError) as error:
            await service.submit(AgentRequest("overflow", "mic-q3", "reject"))
        assert error.value.code == "agent_busy"

        runtime.release.set()
        for request_id in ("r0", "r1", "queued-1", "queued-2"):
            assert (await service.wait(request_id)).status == "completed"
        await service.close()

    asyncio.run(scenario())


def test_inflight_request_is_interrupted_and_not_replayed_after_restart(
    service_factory: ServiceFactory,
) -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.block = True
        database = service_factory.tmp_path / "restart.sqlite"
        first = AgentService(database, runtime)
        await first.start()
        await first.submit(AgentRequest("r1", "mic-a", "uncertain action"))
        await runtime.started.wait()
        await first.close()

        recovered = AgentService(database, runtime)
        await recovered.start()
        result = await recovered.get("r1")
        assert result.status == "interrupted"
        assert len(runtime.calls) == 1
        await recovered.close()

    asyncio.run(scenario())


def test_queued_request_resumes_after_restart_but_running_request_does_not(
    service_factory: ServiceFactory,
) -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.block = True
        database = service_factory.tmp_path / "queued-restart.sqlite"
        first = AgentService(database, runtime, max_workers=1, max_queue=2)
        await first.start()
        await first.submit(AgentRequest("running", "mic-a", "uncertain"))
        await runtime.started.wait()
        await first.submit(AgentRequest("queued", "mic-b", "resume this"))
        await first.close()

        interrupted = await first.get("running")
        assert interrupted.status == "interrupted"
        runtime.block = False
        recovered = AgentService(database, runtime, max_workers=1, max_queue=2)
        await recovered.start()
        assert (await recovered.wait("queued")).status == "completed"
        assert '"uncertain"' in runtime.calls[0].prompt
        assert '"resume this"' in runtime.calls[1].prompt
        await recovered.close()

    asyncio.run(scenario())


def test_reset_is_rejected_while_active_then_starts_a_new_generation(
    service_factory: ServiceFactory,
) -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        runtime.block = True
        service = service_factory(runtime)
        await service.start()
        await service.submit(AgentRequest("r1", "mic-a", "first"))
        await runtime.started.wait()
        with pytest.raises(AgentServiceError) as error:
            await service.reset("mic-a")
        assert error.value.code == "device_busy"

        runtime.release.set()
        await service.wait("r1")
        first_thread = service.session_for("mic-a")
        await service.reset("mic-a")
        await service_factory.complete_for_test(service, "mic-a", "r2", "new conversation")
        assert service.session_for("mic-a") != first_thread
        assert service.session_history_for("mic-a") == [first_thread]
        await service.close()

    asyncio.run(scenario())
