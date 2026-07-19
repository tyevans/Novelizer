import pytest
from novelizer.canon_fs.backend import READ_ONLY_ERROR, CanonBackend


def test_write_and_edit_refuse_with_intent_message():
    backend = CanonBackend(read_store=None)
    w = backend.write("/chapters/001-x.md", "prose")
    assert w.error == READ_ONLY_ERROR and w.path is None
    e = backend.edit("/chapters/001-x.md", "old", "new")
    assert e.error == READ_ONLY_ERROR and e.path is None


async def test_async_write_and_edit_refuse_too():
    backend = CanonBackend(read_store=None)
    assert (await backend.awrite("/x.md", "c")).error == READ_ONLY_ERROR
    assert (await backend.aedit("/x.md", "a", "b")).error == READ_ONLY_ERROR


def test_upload_download_refuse_per_file():
    backend = CanonBackend(read_store=None)
    ups = backend.upload_files([("/a.md", b"x"), ("/b.md", b"y")])
    assert [u.path for u in ups] == ["/a.md", "/b.md"]
    assert all(u.error == "permission_denied" for u in ups)
    downs = backend.download_files(["/a.md"])
    assert downs[0].error == "permission_denied" and downs[0].content is None


def test_sync_read_surface_names_async_path():
    backend = CanonBackend(read_store=None)
    for method, args in (("ls", ("/",)), ("read", ("/x.md",)),
                         ("grep", ("q",)), ("glob", ("*.md",))):
        with pytest.raises(NotImplementedError, match="a" + method):
            getattr(backend, method)(*args)


import os
import tempfile

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import (
    EventType, SecretCreated, SecretLearned, ThemeIntroduced, ThreadPlanted,
)
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.store.models import Chapter, Character, WorldEntry


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


async def seed_canon(events, proj):
    """One record of every kind; Mara knows the secret."""
    ch = Chapter(id="ch1", title="The Drowned Bell", prose="Mara heard the bell.\nIt rang twice.")
    await events.append(EventType.CHAPTER_CREATED, ch.id, ch)
    mara = Character(id="mara", name="Mara", traits="stubborn")
    await events.append(EventType.CHARACTER_CREATED, mara.id, mara)
    w = WorldEntry(id="w1", title="Bell Cult", body="They ring at dusk.")
    await events.append(EventType.WORLD_ENTRY_CREATED, w.id, w)
    await events.append(EventType.THREAD_PLANTED, "bells-curse",
                        ThreadPlanted(id="bells-curse", name="Bell's Curse"))
    await events.append(EventType.SECRET_CREATED, "scar",
                        SecretCreated(id="scar", title="The Scar"))
    await events.append(EventType.SECRET_LEARNED, "scar",
                        SecretLearned(id="scar", character_id="mara"))
    await events.append(EventType.THEME_INTRODUCED, "drowning",
                        ThemeIntroduced(id="drowning", title="Drowning as memory"))
    await proj.catch_up()


async def test_aread_serves_every_kind_with_exact_id(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    for path, record_id in [
        ("/chapters/001-the-drowned-bell.md", "ch1"),
        ("/characters/mara.md", "mara"),
        ("/world/bell-cult.md", "w1"),
        ("/threads/bell-s-curse.md", "bells-curse"),
        ("/secrets/the-scar.md", "scar"),
        ("/themes/drowning-as-memory.md", "drowning"),
    ]:
        result = await backend.aread(path)
        assert result.error is None, f"{path}: {result.error}"
        assert f"id: {record_id}" in result.file_data["content"]


async def test_aread_chapter_carries_full_prose_and_knows_block(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    chapter = await backend.aread("/chapters/001-the-drowned-bell.md")
    assert "It rang twice." in chapter.file_data["content"]
    mara = await backend.aread("/characters/mara.md")
    assert "- scar (The Scar)" in mara.file_data["content"]


async def test_aread_missing_path_hints_ls(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    result = await backend.aread("/chapters/999-nope.md")
    assert result.file_data is None
    assert "not found" in result.error and "ls the parent directory" in result.error


async def test_aread_offset_limit_slices_lines(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    full = await backend.aread("/chapters/001-the-drowned-bell.md")
    all_lines = full.file_data["content"].splitlines()
    window = await backend.aread("/chapters/001-the-drowned-bell.md", offset=2, limit=3)
    assert window.file_data["content"].splitlines() == all_lines[2:5]
