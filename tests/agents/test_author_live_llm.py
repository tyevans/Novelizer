"""M3 done-when, part (b): the true observation for M3, per the doc's own
framing -- a FakeRunner-driven test only proves the pipe is connected, not
that a real LLM will act on what flows through it. This test seeds the same
stale-thread fixture as the mechanical chain (tests/agents/test_author.py's
test_m3_done_when_mechanical_chain_stale_thread_to_touched_to_not_stale) and
runs the *real* Author -- via build_author_runner against a live
OpenAI-compatible endpoint (novelizer.settings.EffectiveSettings' llm_base_url,
author_model) -- with no director signal and no manual prompt beyond what
the room already injects, and asserts it reacts to the injected stale-thread
note by declaring a matching thread_intents entry, unprompted.

Requires the configured OpenAI-compatible LLM endpoint (`Settings().llm_base_url`)
to be reachable and serving the model named by NOVELIZER_AUTHOR_MODEL (see
.env.example / README's Configuration table). Run explicitly with:
uv run pytest -m live_llm tests/agents/test_author_live_llm.py -v
"""
import os
import tempfile
import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, ThreadPlanted
from novelizer.agents.author import Author, build_author_runner
from novelizer.store.models import Chapter


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path)
    await events.init()
    proj = Projector(events, path)
    await proj.init()
    read = ReadStore(path)
    await read.init()
    yield events, proj, read, Committer(events)
    await read.close()
    await proj.close()
    await events.close()
    os.unlink(path)


@pytest.mark.live_llm
async def test_real_author_reacts_to_a_stale_thread_unprompted(stack):
    events, proj, read, committer = stack
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    for i in range(4):
        await events.append(
            EventType.CHAPTER_CREATED, f"c{i}",
            Chapter(id=f"c{i}", title=f"Chapter {i}", prose=f"Chapter {i} prose, nothing about the locket."),
        )
    await proj.catch_up()

    settings = Settings()
    runner = build_author_runner(settings)
    author = Author(runner, read, committer)
    await author.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    touched = [e for e in log if e.event_type == EventType.THREAD_TOUCHED and e.payload.get("id") == "the-locket"]
    assert touched, (
        "The real Author, given the injected stale-thread note and no other "
        "prompting, did not declare a thread_intents entry touching 'the-locket'."
    )
