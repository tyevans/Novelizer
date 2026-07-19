"""Live telemetry smoke (spec: Testing / Live smoke): run one real Author
turn and assert the telemetry log contains a completed run with nonzero
token vitals and a prompt payload that round-trips.

Requires the configured OpenAI-compatible LLM endpoint
(`load_effective_settings().llm_base_url`) to be reachable. Run explicitly:
uv run pytest -m live_llm tests/agents/test_telemetry_live_llm.py -v
"""
import os
import tempfile
import pytest
from novelizer.settings import load_effective_settings
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.agents.author import Author, build_author_runner
from novelizer.telemetry.bus import TelemetryBus
from novelizer.telemetry.recorder import TelemetryRecorder
from novelizer.telemetry.callbacks import TelemetryCallbackHandler
from novelizer.telemetry.events import TelemetryEventType, TokenDelta

pytestmark = pytest.mark.live_llm


async def test_one_real_author_run_lands_full_telemetry_with_round_tripping_prompt():
    live = load_effective_settings()
    fd, dpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
    fd, tpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
    domain = EventStore(dpath); await domain.init()
    proj = Projector(domain, dpath); await proj.init()
    read = ReadStore(dpath); await read.init()
    tel_store = EventStore(tpath); await tel_store.init()
    bus = TelemetryBus()
    recorder = TelemetryRecorder(tel_store, bus)
    tokens_seen = bus.subscribe()
    handler = TelemetryCallbackHandler(recorder)
    author = Author(
        build_author_runner(live, callbacks=[handler]), read, Committer(domain),
        interval=0,
    )
    author.telemetry = recorder
    try:
        await author.run_once()

        tel = await tel_store.events_since(0)
        by_type = {}
        for e in tel:
            by_type.setdefault(e.event_type, []).append(e)

        assert TelemetryEventType.AGENT_RUN_STARTED in by_type
        assert TelemetryEventType.AGENT_RUN_FINISHED in by_type
        run_id = by_type[TelemetryEventType.AGENT_RUN_STARTED][0].payload["run_id"]

        call_started = by_type[TelemetryEventType.LLM_CALL_STARTED][0]
        assert call_started.payload["run_id"] == run_id
        # Prompt payload round-trips: persisted, non-empty, and contains the
        # Author's actual context framing (not a placeholder).
        prompt = call_started.payload["prompt"]
        assert len(prompt) > 100 and "chapter" in prompt.lower()

        finished = by_type[TelemetryEventType.LLM_CALL_FINISHED][0]
        assert finished.payload["output_tokens"] > 0
        assert finished.payload["duration_s"] > 0.0

        # Streaming really streamed: at least one TokenDelta hit the bus.
        deltas = []
        while not tokens_seen.empty():
            item = tokens_seen.get_nowait()
            if isinstance(item, TokenDelta):
                deltas.append(item)
        assert deltas, "expected live token deltas on the bus (streaming enabled)"

        # Correlation: the chapter the run produced carries the run's id.
        dom = await domain.events_since(0)
        assert any(e.run_id == run_id for e in dom)
    finally:
        await read.close(); await proj.close(); await domain.close(); await tel_store.close()
        os.unlink(dpath); os.unlink(tpath)
