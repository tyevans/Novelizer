"""M5.1 done-when, part (b): the reliability claim mining exists to prove --
seed a fixture chapter written by the real Author with the known_secrets_note
guardrail deliberately withheld, so the Author has a real, engineered chance
to leak a secret in prose without declaring a uses intent (the scenario 20+
M4 runs never produced because the guardrail worked). Then run the real
mining pass and confirm the leak is mined, committed with source="mined",
and reaches the open retcon queue -- exercising the leak-catch path M4 could
observe every piece of except the catch itself.

Design (per Locked decision 5 and the M4 closeout note this milestone exists
to close): engineer the previously-unreachable failure mode directly by
withholding known_secrets_note() from the Author's context for this one
fixture chapter, rather than hoping a live run produces an accidental leak.
This means NOT calling Author.work()/_summarize as-is -- the Author's prompt
is constructed manually here, omitting the known_secrets_note(...) line
_summarize would normally splice in, so the real model gets no guardrail and
a genuine chance to leak in prose without declaring a `uses` intent.

Troubleshooting a red run (assertions are staged so the failure names the
broken link):
  STAGE 1: the real mining pass, given the Author's unguarded prose, did not
     mine a secret.referenced fact for Kestrel (no source="mined" event) --
     model/prompt signal on the mining prompt, not a plumbing failure.
     Re-run; if consistent, inspect the mining runner's raw structured
     output.
  STAGE 2: the mined reference landed but no retcon request evidencing the
     leak reached the open queue -- a real LeakDetector/ContinuityChecker
     regression, not model non-determinism.

Requires the configured OpenAI-compatible LLM endpoint
(`load_effective_settings().llm_base_url`) to be reachable and serving the
configured models. Run explicitly with:
uv run pytest -m live_llm tests/agents/test_prose_mining_live_llm.py -v
"""
import os
import tempfile
import pytest
from novelizer.settings import load_effective_settings
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, SecretCreated, SecretLearned
from novelizer.agents.author import build_author_runner
from novelizer.agents.continuity_checker import (
    ContinuityChecker, build_continuity_checker_runner, build_continuity_mining_runner,
)
from novelizer.store.models import Chapter, Character, FlagStatus


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
async def test_engineered_leak_is_mined_and_reaches_the_retcon_queue(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(EventType.CHARACTER_CREATED, "kestrel", Character(id="kestrel", name="Kestrel"))
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives",
                        SecretLearned(id="the-heir-lives", character_id="mara"))
    await proj.catch_up()

    settings = load_effective_settings()

    # Manually build the Author's runner and prompt WITHOUT known_secrets_note
    # -- the one deliberate deviation this smoke test makes (Locked decision 5).
    author_runner = build_author_runner(settings)
    world = "None yet."
    chars = "- Mara: | arc: None\n- Kestrel: | arc: None"
    prev = "None yet."
    notes = "None."
    # No known_secrets_note() line -- the engineered guardrail withholding.
    prompt = (
        f"World lore:\n{world}\n\nCharacters:\n{chars}\n\nPrevious chapters:\n{prev}\n\n"
        f"Director notes:\n{notes}\n\nDirector: Write a chapter where Kestrel, acting on "
        f"knowledge she should not have, moves with certainty toward a secret only Mara "
        f"knows -- the heir lives. Do not have any character announce or reveal this "
        f"aloud; show Kestrel quietly acting on it.\n\nWrite the next chapter."
    )
    result = await author_runner.ainvoke({"messages": [{"role": "user", "content": prompt}]})
    draft = result.get("structured_response")
    assert draft is not None, "Author runner returned no structured_response -- endpoint/model config issue, not a plumbing bug."

    chapter = Chapter(title=draft.title, prose=draft.prose, character_ids=draft.character_ids or ["mara", "kestrel"])
    await events.append(EventType.CHAPTER_CREATED, chapter.id, chapter)
    await proj.catch_up()

    checker = ContinuityChecker(
        build_continuity_checker_runner(settings), build_continuity_mining_runner(settings),
        read, committer, events,
    )
    # Cycle 1: the mining cycle -- poll snapshots the pre-mining log, the mining
    # pass runs, and mined facts commit AFTER find_leaks has already evaluated
    # this cycle's snapshot. The mined reference therefore cannot be leak-flagged
    # until the next cycle's snapshot picks it up.
    await checker.run_once()
    await proj.catch_up()

    log = await events.events_since(0, event_types=[EventType.SECRET_REFERENCED])
    mined_events = [e for e in log if e.payload.get("source") == "mined"]
    assert mined_events, (
        "STAGE 1 (mining pass): the real mining pass, given the Author's "
        "unguarded prose, did not mine a secret.referenced fact for Kestrel. "
        "This is a model/prompt signal on the mining prompt, not a plumbing "
        "failure -- re-run; if consistent, inspect the mining runner's raw "
        "structured output."
    )

    # Cycle 2: the catch cycle -- mirrors production, where the checker cycles
    # continuously; this poll's snapshot now contains the mined reference, so
    # find_leaks can flag it and file the retcon. (chapter.mined idempotency
    # means no re-mining happens this cycle -- only the deterministic detectors
    # run against the enriched log.)
    await checker.run_once()
    await proj.catch_up()

    open_reqs = await read.list_flags(category="contradiction", status=FlagStatus.open)
    leak_reqs = [r for r in open_reqs if "the-heir-lives" in r.description]
    assert leak_reqs, (
        "STAGE 2 (catch): the mined reference landed but no retcon request "
        "evidencing the leak reached the open queue -- a real LeakDetector/"
        "ContinuityChecker regression, not model non-determinism."
    )
