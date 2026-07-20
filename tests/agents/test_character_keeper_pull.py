"""Character Keeper reads chapters instead of being handed a slice of them.

Character discovery once missed anyone introduced past prose[:300]; widening
that cap to 6000 moved the cliff without removing it, and a chapter can
introduce someone in its final line. The Keeper has file tools, so in pull mode
it gets the chapter index and reads the prose itself.

See docs/agent-prompting/proposal-character-keeper.md §1, §3.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from novelizer.agents.character_keeper import CharacterKeeper
from novelizer.agents.schemas import KeeperOutput
from novelizer.canon.committer import Committer
from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Chapter, SecretRecord


class FakeRunner:
    def __init__(self, out=None):
        self._out = out or KeeperOutput()
        self.calls = []

    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


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


async def _prompt(read, committer, runner, **kw):
    keeper = CharacterKeeper(runner, read, committer, **kw)
    ctx = await keeper.poll()
    await keeper.work(ctx)
    return runner.calls[-1]["messages"][0]["content"]


class TestPullMode:
    async def test_pull_mode_pushes_the_index_not_the_prose(self, stack):
        events, proj, read, committer = stack
        await events.append(
            EventType.CHAPTER_CREATED, "c1",
            Chapter(id="c1", title="One", prose="A LATE ARRIVAL walked in."),
        )
        await proj.catch_up()
        sent = await _prompt(read, committer, FakeRunner(), pull_mode=True)
        assert "A LATE ARRIVAL" not in sent
        assert "ch001" in sent and "One" in sent

    async def test_push_mode_still_inlines_prose(self, stack):
        """Untooled deployments have no way to read, so they keep the push."""
        events, proj, read, committer = stack
        await events.append(
            EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="INLINE PROSE"),
        )
        await proj.catch_up()
        sent = await _prompt(read, committer, FakeRunner())
        assert "INLINE PROSE" in sent

    async def test_pull_mode_prompt_orders_research_before_emit(self, stack):
        from novelizer.agents.character_keeper import KEEPER_PULL_NOTE

        assert "read_file" in KEEPER_PULL_NOTE
        assert "IN FULL" in KEEPER_PULL_NOTE
        assert "stop searching" in KEEPER_PULL_NOTE


class TestSecretsAreVisible:
    async def test_secret_ids_are_pushed_so_learn_intents_can_cite_them(self, stack):
        """commit() has always handled learn intents, but the prompt never
        mentioned secrets and work() never rendered the ids poll() fetched, so
        the capability could not fire."""
        events, proj, read, committer = stack
        await events.append(
            EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"),
        )
        await events.append(
            EventType.SECRET_CREATED, "the-heir-lives",
            SecretRecord(id="the-heir-lives", title="The Heir Lives"),
        )
        await proj.catch_up()
        sent = await _prompt(read, committer, FakeRunner())
        assert "the-heir-lives" in sent
        assert "The Heir Lives" in sent

    async def test_no_secrets_block_when_none_exist(self, stack):
        events, proj, read, committer = stack
        await events.append(
            EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"),
        )
        await proj.catch_up()
        sent = await _prompt(read, committer, FakeRunner())
        assert "learning a secret" not in sent


class TestAliasesAreVisible:
    async def test_cast_line_lists_aliases_so_dedup_can_work(self, stack):
        """The prompt tells the Keeper not to re-report someone under a
        nickname; that check needs the nicknames in front of it."""
        from novelizer.store.models import Character

        events, proj, read, committer = stack
        await events.append(
            EventType.CHARACTER_CREATED, "reyes",
            Character(id="reyes", name="Dr. Reyes", aliases=["Doc"]),
        )
        await events.append(
            EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"),
        )
        await proj.catch_up()
        sent = await _prompt(read, committer, FakeRunner())
        assert "Doc" in sent
