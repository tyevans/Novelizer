import os
import tempfile

import pytest
from novelizer.research.schemas import ResearchAnswer
from novelizer.research.service import ResearchAnswerError, ResearchService
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime


class _R:
    def __init__(self, out):
        self._out = out
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


class _Empty:
    async def ainvoke(self, inputs):
        return {"structured_response": None}


@pytest.mark.asyncio
async def test_ask_returns_runner_text_verbatim():
    runner = _R(ResearchAnswer(answer_text="Three threads are currently stale."))
    service = ResearchService(lambda: runner)

    answer = await service.ask("Anything stale?", history=[])

    assert answer == "Three threads are currently stale."


@pytest.mark.asyncio
async def test_ask_includes_history_and_question_in_the_prompt():
    runner = _R(ResearchAnswer(answer_text="ok"))
    service = ResearchService(lambda: runner)

    await service.ask(
        "and the paradoxes?",
        history=[("you", "any leaks?"), ("project", "No leaks found.")],
    )

    prompt = runner.calls[0]["messages"][0]["content"]
    assert "any leaks?" in prompt
    assert "No leaks found." in prompt
    assert "and the paradoxes?" in prompt


@pytest.mark.asyncio
async def test_ask_raises_when_runner_returns_no_structured_response():
    service = ResearchService(lambda: _Empty())

    with pytest.raises(ResearchAnswerError):
        await service.ask("anything?", history=[])


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.mark.asyncio
async def test_runtime_wires_a_research_service(db_path):
    runner = _R(ResearchAnswer(answer_text="No leaks found."))
    settings = Settings(db_path=db_path, projector_interval=0.05)
    rt = Runtime(settings, runners={"research": runner})
    await rt.start()
    try:
        answer = await rt.research.ask("any leaks?", history=[])
        assert answer == "No leaks found."
    finally:
        await rt.close()


from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.recorder import TelemetryRecorder
from novelizer.canon.event_store import EventStore
from novelizer.telemetry.events import TelemetryEventType


@pytest.mark.asyncio
async def test_ask_tags_telemetry_with_research_identity(tmp_path):
    telemetry_store = EventStore(str(tmp_path / "telemetry.db"))
    await telemetry_store.init()
    bus = TelemetryBus()
    telemetry = TelemetryRecorder(telemetry_store, bus)
    q = bus.subscribe()

    runner = _R(ResearchAnswer(answer_text="answer"))
    service = ResearchService(lambda: runner, telemetry=telemetry)

    await service.ask("q?", history=[])

    started = q.get_nowait()
    assert started.event_type == TelemetryEventType.AGENT_RUN_STARTED
    assert started.payload["agent_name"] == "research"
    finished = q.get_nowait()
    assert finished.event_type == TelemetryEventType.AGENT_RUN_FINISHED
    await telemetry_store.close()


@pytest.mark.asyncio
async def test_runtime_wires_telemetry_into_research_service(db_path):
    settings = Settings(db_path=db_path, projector_interval=0.05)
    rt = Runtime(settings, runners={"research": _R(ResearchAnswer(answer_text="ok"))})
    await rt.start()
    try:
        assert rt.research._telemetry is rt.telemetry
    finally:
        await rt.close()
