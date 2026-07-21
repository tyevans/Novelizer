"""Property proof: CharacterKeeper.commit mints exactly one character per
distinct non-blank slug, regardless of how the LLM words, cases, or repeats
the names it reports — and a from-zero projector rebuild agrees with the
incremental projection."""
import asyncio
import os
import tempfile
from hypothesis import given, settings, strategies as st
from novelizer.agents.character_keeper import CharacterKeeper
from novelizer.agents.schemas import CharacterUpdate, KeeperOutput, KnowledgeIntent, NewCharacter, FlagDraft
from novelizer.canon.characters import slugify_character_name
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Chapter, FlagStatus


class FakeRunner:
    def __init__(self, out):
        self._out = out

    async def ainvoke(self, inputs):
        return {"structured_response": self._out}


async def _run_names(names: list[str]) -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path)
        await events.init()
        proj = Projector(events, path)
        await proj.init()
        read = ReadStore(path)
        await read.init()

        await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="..."))
        await proj.catch_up()

        out = KeeperOutput(new_characters=[NewCharacter(name=n) for n in names])
        keeper = CharacterKeeper(FakeRunner(out), read, committer=Committer(events))
        await keeper.run_once()
        await proj.catch_up()

        expected_slugs = {slugify_character_name(n) for n in names if n.strip()}
        log = await events.events_since(0, event_types=[EventType.CHARACTER_CREATED])
        assert sorted(e.aggregate_id for e in log) == sorted(expected_slugs)
        incremental = {c.id for c in await read.list_characters()}
        assert incremental == expected_slugs

        proj2 = Projector(events, path)
        await proj2.init()
        await proj2._reset_state()
        await proj2.catch_up()
        rebuilt = {c.id for c in await read.list_characters()}
        assert rebuilt == expected_slugs
        await proj2.close()

        await read.close()
        await proj.close()
        await events.close()
    finally:
        os.unlink(path)


@given(st.lists(st.text(max_size=12), max_size=8))
@settings(max_examples=30, deadline=None)
def test_character_creation_is_slug_deduped_and_replay_stable(names: list[str]):
    asyncio.run(_run_names(names))


async def _run_pass(out: KeeperOutput) -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        events = EventStore(path)
        await events.init()
        proj = Projector(events, path)
        await proj.init()
        read = ReadStore(path)
        await read.init()

        await events.append(EventType.CHAPTER_CREATED, "ch1", Chapter(id="ch1", title="One", prose="..."))
        await proj.catch_up()

        keeper = CharacterKeeper(FakeRunner(out), read, committer=Committer(events))
        await keeper.run_once()
        await proj.catch_up()

        # However populated the lists, a pass never mutates canon:
        # the only event beyond the seeded chapter may be one agent.remarked.
        assert await read.list_characters() == []
        assert await read.list_flags(category="contradiction", status=FlagStatus.open) == []
        log = await events.events_since(0)
        assert {e.event_type for e in log} <= {EventType.CHAPTER_CREATED, EventType.AGENT_REMARKED}
        assert sum(1 for e in log if e.event_type == EventType.AGENT_REMARKED) <= 1

        await read.close()
        await proj.close()
        await events.close()
    finally:
        os.unlink(path)


_texts = st.text(max_size=12)


@given(
    st.builds(
        KeeperOutput,
        no_action=st.just(True),
        feed_note=_texts,
        new_characters=st.lists(st.builds(NewCharacter, name=st.text(min_size=1, max_size=12)), max_size=4),
        updated_characters=st.lists(st.builds(CharacterUpdate, id=_texts), max_size=4),
        flags=st.lists(st.builds(FlagDraft, category=st.just("contradiction"), description=st.text(min_size=1, max_size=12)), max_size=4),
        knowledge_intents=st.lists(
            st.builds(KnowledgeIntent, action=st.just("learn"), id=_texts, character_id=_texts), max_size=4
        ),
    )
)
@settings(max_examples=25, deadline=None)
def test_no_action_pass_never_mutates_canon(out: KeeperOutput):
    asyncio.run(_run_pass(out))
