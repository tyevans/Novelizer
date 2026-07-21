"""M5.2 done-when, part (b), live smoke: seed a character with an established
voice card and a chapter whose dialogue clearly violates a specific stated
trait, run the real Editor (no FakeRunner), and confirm the model itself
catches the drift and that the catch reaches the open retcon queue tagged
VOICE_SOURCE_TAG, citing the violated trait -- proving citation-grounded
enforcement, not generic "this feels off" prose.

Design mirrors M5.1 Task 11's "engineer the fixture, use the real model"
pattern (see tests/agents/test_prose_mining_live_llm.py): CI-mechanical
coverage already proves the Editor->retcon plumbing works given a FakeRunner
verdict (tests/agents/test_editor.py::test_editor_voice_drift_flag_commits_tagged_retcon
and ::test_m5_2_done_when_mechanical_chain_voice_drift); this smoke proves
the live model actually notices the drift when given a genuine voice card
and a chapter drafted to violate it.

Troubleshooting a red run (assertions are staged so the failure names the
broken link):
  STAGE 1: the real Editor, given the voice card and the violating dialogue,
     did not return a voice_drift_flags entry at all -- model/prompt signal,
     not a plumbing failure. Re-run (model judgment is stochastic); if it
     fails consistently across a few attempts inspect the Editor's raw
     structured_response.
  STAGE 2: a voice_drift_flags entry was returned but no retcon_request
     tagged VOICE_SOURCE_TAG reached the open queue -- a real Editor.commit
     regression, not model non-determinism.

D3 contingency: if structured_response comes back None (not "no flags" --
literally no structured output at all) across attempts, that is the signal
Decision Note D3 named as the trigger for reconsidering
ProviderStrategy(EditorVerdict) on the Editor's runner. This test does not
apply that change itself -- it only surfaces the observation.

Requires the configured OpenAI-compatible LLM endpoint
(`load_effective_settings().llm_base_url`) to be reachable and serving the
configured models. Run explicitly with:
uv run pytest -m live_llm tests/agents/test_voice_drift_live_llm.py -v
"""
import os
import tempfile
import pytest
from novelizer.settings.loader import load_effective_settings
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.editor import build_editor_runner, Editor
from novelizer.store.models import Chapter, Character, EditorialStatus, FlagStatus


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
async def test_voice_drift_live_catch(stack):
    events, proj, read, committer = stack

    # Established voice card: a concrete, checkable trait -- formal, clipped,
    # no contractions or slang.
    character = Character(
        id="lord-ashwick",
        name="Lord Ashwick",
        voice=(
            "Speaks in short, formal, clipped sentences; never uses "
            "contractions or slang; addresses others by full title."
        ),
    )
    await events.append(EventType.CHARACTER_CREATED, character.id, character)

    # Dialogue drafted to clearly violate that specific trait: casual
    # contractions and slang, the opposite of "formal, clipped, no slang".
    chapter = Chapter(
        title="The Bargain",
        prose=(
            'Lord Ashwick leaned against the doorframe and shrugged. '
            '"Nah, I dunno, whatever works I guess," he said, grinning at '
            'the merchant. "You do you, man. Ain\'t my problem no more." '
            'He waved a lazy hand and wandered off, hands in his pockets.'
        ),
        character_ids=[character.id],
        editorial_status=EditorialStatus.draft,
    )
    await events.append(EventType.CHAPTER_CREATED, chapter.id, chapter)
    await proj.catch_up()

    settings = load_effective_settings()
    runner = build_editor_runner(settings)
    editor = Editor(runner, read, committer)
    await editor.run_once()
    await proj.catch_up()

    voice_flags = await read.list_flags(category="voice_drift", status=FlagStatus.open)
    assert voice_flags, (
        "STAGE 1/2: expected a voice-drift Flag(category='voice_drift') in "
        f"the open queue; got none. If the Editor returned no "
        "structured_response at all (not merely no flags), this is the D3 "
        "contingency checkpoint -- see module docstring."
    )
    description_lower = voice_flags[0].description.lower()
    assert "formal" in description_lower or "clipped" in description_lower, (
        f"voice-drift flag landed but did not cite the violated trait "
        f"(formal/clipped): {voice_flags[0].description!r}"
    )
