"""M4 done-when, part (b): the true observation for M4, per
docs/submilestones/M4-knowledge-and-cause.md's own framing -- a
FakeRunner-driven test (see test_m4_3_done_when_mechanical_chain_... in
tests/agents/test_continuity_checker.py) only proves the pipe is connected,
not that a real LLM will act on what flows through it under live conditions.

Design of the fixture (documented here since the spec deliberately left this
open, per the M4.3 dispatch instructions): the real Author must produce the
secret.referenced event itself, via its own knowledge_intents "uses"
declaration -- LeakDetector cannot be fed a synthetic reference here, or the
test would no longer be proving that a real agent's structured output causes
a leak. For that to be *plausible* for a real LLM to produce unforced:

1. A secret ('the-heir-lives') is seeded already known to one character
   (Mara) but NOT to a second character (Kestrel) who is also active in the
   room -- both characters exist so the Author has someone to write about
   who does *not* know the secret.
2. The Author is given no director signal or manual prompt beyond what the
   room already injects: known_secrets_note() (Task 2) tells it, verbatim,
   "'the-heir-lives' (The Heir Lives) — known only to Mara", which is enough
   information for a scene involving Kestrel to plausibly reference the
   secret without the Author having first declared a `learn` intent for
   Kestrel -- exactly the shape of an unforced leak.
3. No fixture data or prompt text tells the Author to *avoid* a leak, or to
   write about Kestrel and the secret together -- the note states the
   knowledge fact only, and the Author is free to write any chapter; if it
   chooses to have Kestrel reference the secret, that's the room's existing
   injected context alone producing the leak, unprompted.

Requires the configured OpenAI-compatible LLM endpoint (`load_effective_settings().llm_base_url`)
to be reachable and serving the model named by NOVELIZER_AUTHOR_MODEL (see
README's Configuration section / docs/examples/config.example.toml). Run explicitly with:
uv run pytest -m live_llm tests/agents/test_author_leak_live_llm.py -v

This test is inherently non-deterministic (it depends on a real model's
narrative choices) -- a single failing run does not necessarily mean the
plumbing is broken; re-run, and if it fails consistently across several
runs, treat that as a real signal the injected note text needs
strengthening (see known_secrets_note() in novelizer/brain/context.py).

Troubleshooting a red run -- the test asserts in two stages so a failure
tells you *which* half broke:

1. Staged assertion (right after the Author's run_once(), before the
   Continuity Checker runs at all): checks the event log directly for a
   secret.referenced row naming character "kestrel" and secret
   "the-heir-lives". If this fails, the real Author simply did not declare
   a leaking `uses` knowledge intent this run -- a model/prompt signal,
   not a plumbing failure. Re-run, or strengthen known_secrets_note().
2. Final assertion (after the Continuity Checker's run_once()): checks
   that an open LEAK_SOURCE_TAG retcon request exists. If stage 1 passed
   but this fails, the leak provably landed in the event log and the
   Continuity Checker failed to catch or file it -- treat this as a real
   regression in LeakDetector or the checker's wiring, not model noise.
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
from novelizer.agents.author import Author, build_author_runner
from novelizer.agents.continuity_checker import ContinuityChecker, build_continuity_checker_runner
from novelizer.brain.leaks import LEAK_SOURCE_TAG
from novelizer.store.models import Character, RetconStatus


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
async def test_real_author_and_continuity_checker_catch_an_unprompted_leak(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(id="mara", name="Mara"))
    await events.append(EventType.CHARACTER_CREATED, "kestrel", Character(id="kestrel", name="Kestrel"))
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives",
                        SecretLearned(id="the-heir-lives", character_id="mara"))
    await proj.catch_up()

    settings = load_effective_settings()
    author = Author(build_author_runner(settings), read, committer)
    await author.run_once()
    await proj.catch_up()

    references = await read.list_secret_references(secret_id="the-heir-lives")
    kestrel_leaked = [r for r in references if r.character_id == "kestrel"]
    assert kestrel_leaked, (
        "STAGE 1 (Author): the real Author, given only the injected "
        "known_secrets_note (Mara knows 'the-heir-lives', no one else does) and "
        "no other prompting, did NOT declare a leaking `uses` knowledge intent "
        "for Kestrel this run -- LIVE AUTHOR DID NOT LEAK. This is a "
        "model/prompt signal, not a plumbing failure: re-run, or strengthen "
        "known_secrets_note() in novelizer/brain/context.py. See this file's "
        "module docstring for the fixture design and troubleshooting note."
    )

    checker = ContinuityChecker(build_continuity_checker_runner(settings), read, committer)
    await checker.run_once()
    await proj.catch_up()

    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    leak_reqs = [r for r in open_reqs if r.description.startswith(LEAK_SOURCE_TAG)]
    assert leak_reqs, (
        "STAGE 2 (Continuity Checker): the Author's leak provably landed as a "
        "secret.referenced event for Kestrel/'the-heir-lives' (stage 1 above "
        "passed), but no LEAK_SOURCE_TAG retcon request reached the open queue "
        "-- THE CHECKER MISSED A LEAK THAT DEFINITELY LANDED. Treat this as a "
        "real regression in LeakDetector or ContinuityChecker wiring, not model "
        "noise. See this file's module docstring for the fixture design and "
        "troubleshooting note."
    )
