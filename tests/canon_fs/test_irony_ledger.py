import os
import tempfile

import pytest

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import EventType, SecretCreated, SecretLearned, SecretReferenced
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon_fs.backend import IRONY_LEDGER_PATH, CanonBackend
from novelizer.canon_fs.render import EMPTY_LEDGER_NOTE, render_irony_ledger
from novelizer.brain.irony import build_irony_ledger
from novelizer.store.models import (
    Chapter, SecretKnowledgeRecord, SecretRecord, SecretReferenceRecord,
)


def ch(chapter_id: str, *character_ids: str) -> Chapter:
    return Chapter(
        id=chapter_id, title=chapter_id, prose="x", character_ids=list(character_ids)
    )


# -- rendering -------------------------------------------------------------

def test_empty_ledger_says_what_is_missing_and_invents_nothing():
    out = render_irony_ledger([], [])
    assert "kind: irony_ledger" in out
    assert "secrets: 0" in out
    assert EMPTY_LEDGER_NOTE in out
    # honesty: no gap language at all when there is nothing to report
    assert "in the dark" not in out


def test_populated_ledger_names_the_reader_onset_and_each_gap():
    chapters = [ch("c1"), ch("c2"), ch("c3", "mara", "tomas"), ch("c4", "mara")]
    entries = build_irony_ledger(
        secrets=[SecretRecord(id="the-heir-lives", title="The heir lives")],
        references=[
            SecretReferenceRecord(
                secret_id="the-heir-lives", character_id="ren", chapter_id="c2"
            )
        ],
        knowledge=[
            SecretKnowledgeRecord(
                secret_id="the-heir-lives", character_id="tomas", chapter_id="c4"
            )
        ],
        chapters=chapters,
        matrix={"the-heir-lives": {"revealed": False, "known_by": {"tomas"}}},
    )
    out = render_irony_ledger(entries, chapters)
    assert "# Dramatic Irony Ledger" in out
    assert "The heir lives" in out and "the-heir-lives" in out
    assert "reader knows from chapter 2" in out
    assert "mara" in out and "tomas" in out
    assert "gaps: 2" in out


def test_a_secret_with_no_reader_knowledge_renders_its_note_not_a_blank():
    entries = build_irony_ledger(
        secrets=[SecretRecord(id="s1", title="Unspoken")], references=[], knowledge=[],
        chapters=[ch("c1", "mara")], matrix={"s1": {"revealed": False, "known_by": set()}},
    )
    out = render_irony_ledger(entries, [ch("c1", "mara")])
    assert "Unspoken" in out
    assert "the reader has not met this secret" in out
    assert "gaps: 0" in out


# -- virtual filesystem ----------------------------------------------------

@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close()
    os.unlink(path)


async def test_ledger_is_listed_under_secrets_and_readable(stack):
    events, proj, read = stack
    backend = CanonBackend(read)

    listing = await backend.als("/secrets")
    assert listing.error is None
    assert IRONY_LEDGER_PATH in [e["path"] for e in listing.entries]

    result = await backend.aread(IRONY_LEDGER_PATH)
    assert result.error is None
    assert "kind: irony_ledger" in result.file_data["content"]


async def test_ledger_is_globbable_and_greppable(stack):
    events, proj, read = stack
    backend = CanonBackend(read)

    matches = await backend.aglob("/secrets/*.md")
    assert IRONY_LEDGER_PATH in [m["path"] for m in matches.matches]

    grep = await backend.agrep("irony_ledger", path="/secrets")
    assert IRONY_LEDGER_PATH in [m["path"] for m in grep.matches]


async def test_ledger_reflects_projected_secret_canon(stack):
    events, proj, read = stack
    await events.append(
        EventType.CHAPTER_CREATED, "author",
        Chapter(id="c1", title="One", prose="p", character_ids=["mara"]),
    )
    await events.append(
        EventType.CHAPTER_CREATED, "author",
        Chapter(id="c2", title="Two", prose="p", character_ids=["mara"]),
    )
    await events.append(
        EventType.SECRET_CREATED, "author",
        SecretCreated(id="the-boundary-fails", title="The boundary fails"),
    )
    await events.append(
        EventType.SECRET_REFERENCED, "author",
        SecretReferenced(
            id="the-boundary-fails", character_id="ren", chapter_id="c1"
        ),
    )
    await proj.catch_up()

    result = await CanonBackend(read).aread(IRONY_LEDGER_PATH)
    assert result.error is None
    assert "The boundary fails" in result.file_data["content"]
    assert "reader knows from chapter 1" in result.file_data["content"]
    # mara is on the page in chapters 1 and 2 and never learns
    assert "mara" in result.file_data["content"]


async def test_ledger_path_does_not_shadow_a_real_secret_file(stack):
    events, proj, read = stack
    await events.append(
        EventType.SECRET_CREATED, "author",
        SecretCreated(id="s1", title="A real secret"),
    )
    await proj.catch_up()
    backend = CanonBackend(read)
    listing = await backend.als("/secrets")
    paths = [e["path"] for e in listing.entries]
    assert IRONY_LEDGER_PATH in paths
    assert "/secrets/a-real-secret.md" in paths
    secret = await backend.aread("/secrets/a-real-secret.md")
    assert "A real secret" in secret.file_data["content"]


async def test_writes_to_the_ledger_are_still_refused(stack):
    events, proj, read = stack
    backend = CanonBackend(read)
    assert (await backend.awrite(IRONY_LEDGER_PATH, "x")).error is not None
