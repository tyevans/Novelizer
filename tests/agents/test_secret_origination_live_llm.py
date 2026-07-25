"""The observation nothing else in the suite makes: an agent ORIGINATING a
secret from a story that has none.

Every other secret test starts with a secret already in the room --
tests/agents/test_author.py injects a KnowledgeIntent fixture (which proves
only that the commit path is wired) and tests/agents/test_leak_live_llm.py
pre-seeds `the-heir-lives`. That left the origination edge unobserved, and it
was broken in exactly the way an unobserved edge gets broken: over three
chapters of a real story the fleet emitted 5 `promise.made`, 5
`world_entry.created`, 2 `thread.planted`, 2 `theme.introduced` and ZERO
`secret.*`, while telemetry showed `knowledge_intents` offered in 602 prompts
and filled in none. Nothing was being dropped by validation; the model was
never told what a secret is.

The premise here is engineered so a secret is the only honest reading of the
scene -- one character is concealing something from another, on the page,
by the chapter's own setup -- and then the REAL Author runs with its
production prompt and no manual instruction beyond ordinary room state.
There is no secret to cite, so the only intent that can express what the
chapter does is `plant`, which mints its own id.

Requires the configured OpenAI-compatible LLM endpoint
(`load_effective_settings().llm_base_url`) to be reachable and serving the
model named by NOVELIZER_AUTHOR_MODEL. Run explicitly with:
uv run pytest -m live_llm tests/agents/test_secret_origination_live_llm.py -v
"""
import os
import tempfile
import pytest
from novelizer.settings import load_effective_settings
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType
from novelizer.agents.author import Author, build_author_runner
from novelizer.store.models import Character, DirectorSignal, SignalKind


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
async def test_real_author_plants_the_first_secret_in_a_story_that_has_none(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "mara", Character(
        id="mara", name="Mara",
        traits=["watchful", "keeps her own counsel"],
        backstory="Signed the requisition that condemned the north road caravan, and has told no one.",
    ))
    await events.append(EventType.CHARACTER_CREATED, "kestrel", Character(
        id="kestrel", name="Kestrel",
        traits=["direct", "trusting of Mara"],
        backstory="Lost a brother on the north road and still believes it was bandits.",
    ))
    # An ordinary seed signal, the same channel the Director uses in a live
    # run: it sets the scene, not the bookkeeping. It says nothing about
    # secrets, knowledge_intents, or planting -- if the Author declares one,
    # it is reading its own draft, which is the whole point.
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1", DirectorSignal(
        id="s1", kind=SignalKind.seed, target_agent="author",
        body=("Mara and Kestrel share a watch on the wall. Kestrel talks about her "
              "brother and the north road. Mara knows what she signed and says "
              "nothing. Write the scene from that pressure."),
    ))
    await proj.catch_up()

    settings = load_effective_settings()
    author = Author(build_author_runner(settings), read, committer)
    await author.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    created = [e for e in log if e.event_type == EventType.SECRET_CREATED]
    assert created, (
        "The real Author wrote a chapter whose premise IS a concealment -- Mara "
        "withholding from Kestrel what she signed -- and declared no "
        "knowledge_intents `plant`, so no `secret.created` landed. Nothing was "
        "dropped by validation (no drop warning is logged for a plant with a "
        "non-blank title), which means this is a prompt/awareness failure: "
        "check that AUTHOR_SYSTEM_PROMPT still DEFINES what a secret is under "
        "`knowledge_intents`, not merely names the action. That definition is "
        "the fix this test exists to protect."
    )
    secrets = await read.list_secrets()
    assert secrets and secrets[0].title.strip(), (
        "A `secret.created` landed but projected no titled secret row -- that "
        "is a projection regression, not a model one."
    )
