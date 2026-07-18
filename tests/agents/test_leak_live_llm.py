"""M4 done-when, part (b): the true observation for M4 -- a planted knowledge
leak ("she never learned this") is auto-caught and routed to the retcon queue,
with real model judgment at every live link in the chain.

Design (and why it changed from an Author-declares-its-own-leak variant): the
leak is PLANTED IN PROSE -- a draft chapter, seeded as room canon, in which
Kestrel plainly asserts the secret 'the-heir-lives' even though the knowledge
matrix says only Mara has learned it. The room then catches it on its own:

  1. The REAL Editor reviews the draft (its normal poll target). The Editor
     deliberately gets no who-knows-what guardrail note (Locked decision #7:
     that is Author-only); it does see the active-secret id list (a citation
     aid, so its knowledge_intents can cite a valid id). Honestly annotating
     the prose, it declares a `uses` intent for Kestrel -> `secret.referenced`.
  2. The deterministic LeakDetector cross-checks that committed reference
     against the knowledge matrix: Kestrel never learned it, not revealed ->
     leak.
  3. The REAL ContinuityChecker files a `retcon_request.created` whose
     description starts with LEAK_SOURCE_TAG, landing in the open queue.

No manual prompting beyond what the room already injects: the seeded draft is
ordinary room state, and both agents run with their production runners and
production prompts.

Why not the Author variant: 12+ live runs showed the Author's injected
known_secrets_note reliably PREVENTS it from declaring a leaking use (raw
model reasoning deliberately attributed the secret to its knower every time).
That is the M4 guardrail working as designed -- and it means the catch half of
the chain must be observed on the annotation path, where a leak already
written into prose is truthfully reported by an agent whose job is to
describe what the prose shows.

Troubleshooting a red run (assertions are staged so the failure names the
broken link):
  STAGE 1: the real Editor did not annotate the planted leak (no
     `secret.referenced` for Kestrel) -- model/prompt signal on the Editor's
     citation aid, not a plumbing failure. Re-run; if consistent, inspect the
     Editor's raw structured output.
  STAGE 2: the reference landed but no LEAK_SOURCE_TAG retcon request reached
     the open queue -- a real LeakDetector/ContinuityChecker regression.

Requires the configured OpenAI-compatible LLM endpoint
(`load_effective_settings().llm_base_url`) to be reachable and serving the
configured models. Run explicitly with:
uv run pytest -m live_llm tests/agents/test_leak_live_llm.py -v
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
from novelizer.agents.editor import Editor, build_editor_runner
from novelizer.agents.continuity_checker import (
    ContinuityChecker, build_continuity_checker_runner, build_continuity_mining_runner,
)
from novelizer.brain.leaks import LEAK_SOURCE_TAG
from novelizer.store.models import Chapter, Character, RetconStatus


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
async def test_planted_prose_leak_is_annotated_by_the_real_editor_and_reaches_the_retcon_queue(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(EventType.CHARACTER_CREATED, "kestrel", Character(id="kestrel", name="Kestrel"))
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives",
                        SecretLearned(id="the-heir-lives", character_id="mara"))
    # The planted leak: a draft chapter whose prose has Kestrel flatly using
    # the secret she never learned. Draft status makes it the Editor's next
    # poll target.
    # The prose must show USAGE, not revelation: an early draft had Kestrel
    # announce the secret to a council, and the live Editor -- correctly --
    # annotated that as a `reveal`, which makes the secret public and
    # un-leakable. Here Kestrel privately ACTS on knowledge she was never
    # given: quiet, unexplained, unannounced use.
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(
        id="c1", title="The Shuttered Window",
        character_ids=["mara", "kestrel"],
        prose=(
            "Kestrel moved through the curfew-dark streets with uncanny "
            "certainty, taking the turning toward the old cooper's yard "
            "without once checking her map. No one had told her the heir "
            "lived; no one had told her anything. Yet she stopped beneath "
            "the one shuttered window where the heir slept, pressed two "
            "fingers to the sill as if in greeting, and slipped away before "
            "the watch came past. Across the city, Mara woke from a dream "
            "of being followed, and could not say why."
        ),
    ))
    await proj.catch_up()

    settings = load_effective_settings()
    editor = Editor(build_editor_runner(settings), read, committer)
    await editor.run_once()
    await proj.catch_up()

    references = await read.list_secret_references(secret_id="the-heir-lives")
    # Casing-tolerant: some models emit the display name ("Kestrel") as the
    # character_id. Either way the matrix has no learned cell for it, so the
    # leak chain fires identically; the filter should not fail on casing.
    kestrel_refs = [r for r in references if r.character_id.lower() == "kestrel"]
    assert kestrel_refs, (
        "STAGE 1 (Editor): the real Editor, reviewing prose in which Kestrel "
        "plainly asserts 'the heir lives', did NOT declare a `uses` knowledge "
        "intent for Kestrel -- the planted leak went unannotated. This is a "
        "model/prompt signal (Editor citation aid), not a plumbing failure: "
        "re-run; if it fails consistently, inspect the Editor's raw "
        "structured output."
    )

    checker = ContinuityChecker(
        build_continuity_checker_runner(settings), build_continuity_mining_runner(settings),
        read, committer, events,
    )
    await checker.run_once()
    await proj.catch_up()

    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    leak_reqs = [r for r in open_reqs if r.description.startswith(LEAK_SOURCE_TAG)
                 and "the-heir-lives" in r.description and "kestrel" in r.description.lower()]
    assert leak_reqs, (
        "STAGE 2 (Checker): Kestrel's leak provably landed as a "
        "`secret.referenced` event, but no LEAK_SOURCE_TAG-prefixed retcon "
        "request reached the open queue -- a real LeakDetector/"
        "ContinuityChecker regression, NOT model non-determinism."
    )
