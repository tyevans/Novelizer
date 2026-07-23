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
    window_lines = window.file_data["content"].splitlines()
    assert window_lines[:3] == all_lines[2:5]
    # A short window ends in the notice naming what it withheld, so the agent
    # cannot read a partial chapter as a whole one.
    assert f"lines 3-5 of {len(all_lines)}" in window_lines[-1]


async def test_chapter_longer_than_the_read_file_default_says_so(stack):
    """Regression: four of the five chapters in the first full novel run were
    longer than read_file's 100-line default, and the Author is told to read
    the previous chapter IN FULL. The window has to announce itself."""
    from deepagents.middleware.filesystem import DEFAULT_READ_LIMIT

    events, proj, read = stack
    await seed_canon(events, proj)
    long_chapter = Chapter(id="ch2", title="The Long Night",
                           prose="\n".join(f"Paragraph {i}." for i in range(1, 301)))
    await events.append(EventType.CHAPTER_CREATED, long_chapter.id, long_chapter)
    await proj.catch_up()
    backend = CanonBackend(read)

    window = await backend.aread("/chapters/002-the-long-night.md",
                                 limit=DEFAULT_READ_LIMIT)
    content = window.file_data["content"]
    assert "TRUNCATED" in content
    assert "offset=100, limit=2000" in content
    # Frontmatter and title eat into the window: the prose stops well short
    # of paragraph 300, which is exactly what the agent cannot otherwise see.
    assert "Paragraph 90." in content and "Paragraph 150." not in content


async def test_als_root_lists_kind_directories(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    result = await backend.als("/")
    assert result.error is None
    assert [e["path"] for e in result.entries] == [
        "/chapters", "/characters", "/world", "/threads", "/secrets", "/themes",
    ]
    assert all(e["is_dir"] for e in result.entries)


async def test_als_kind_directory_lists_files(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    result = await backend.als("/chapters")
    assert [e["path"] for e in result.entries] == ["/chapters/001-the-drowned-bell.md"]
    trailing = await backend.als("/chapters/")
    assert [e["path"] for e in trailing.entries] == ["/chapters/001-the-drowned-bell.md"]


async def test_als_unknown_directory_names_valid_ones(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    result = await backend.als("/nope")
    assert result.entries is None
    assert "/chapters" in result.error and "not found" in result.error


async def test_als_on_file_path_says_read_it(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    result = await backend.als("/chapters/001-the-drowned-bell.md")
    assert result.entries is None
    assert "is a file" in result.error and "Read it" in result.error


async def test_als_empty_string_treated_as_root(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    result = await backend.als("")
    assert [e["path"] for e in result.entries] == [
        "/chapters", "/characters", "/world", "/threads", "/secrets", "/themes",
    ]


async def test_aglob_absolute_and_relative(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    absolute = await backend.aglob("/chapters/*.md")
    assert [m["path"] for m in absolute.matches] == ["/chapters/001-the-drowned-bell.md"]
    relative = await backend.aglob("*.md", path="/secrets")
    assert [m["path"] for m in relative.matches] == ["/secrets/the-scar.md"]


async def test_aglob_globstar_spans_directories(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    result = await backend.aglob("**/*.md")
    assert len(result.matches) == 6  # every canon file
    assert result.matches == sorted(result.matches, key=lambda m: m["path"])


async def test_aglob_no_matches_is_empty_not_error(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    result = await backend.aglob("*.txt")
    assert result.error is None and result.matches == []


async def test_agrep_finds_literal_across_kinds(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    result = await backend.agrep("bell")
    paths = {m["path"] for m in result.matches}
    assert "/chapters/001-the-drowned-bell.md" in paths  # "Mara heard the bell."
    for m in result.matches:
        assert "bell" in m["text"] and m["line"] >= 1


async def test_agrep_path_scopes_and_glob_filters(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    scoped = await backend.agrep("Mara", path="/characters")
    assert {m["path"] for m in scoped.matches} == {"/characters/mara.md"}
    filtered = await backend.agrep("id:", glob="secrets/*.md")
    assert {m["path"] for m in filtered.matches} == {"/secrets/the-scar.md"}


async def test_agrep_is_literal_not_regex(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    result = await backend.agrep("b.ll")
    assert result.matches == []


async def test_agrep_basename_glob_filters_by_filename(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    result = await backend.agrep("id:", glob="*.md")
    assert {m["path"] for m in result.matches} == {
        "/chapters/001-the-drowned-bell.md", "/characters/mara.md", "/world/bell-cult.md",
        "/threads/bell-s-curse.md", "/secrets/the-scar.md", "/themes/drowning-as-memory.md",
    }
    scoped = await backend.agrep("id:", glob="mara.md")
    assert {m["path"] for m in scoped.matches} == {"/characters/mara.md"}


async def test_agrep_exact_file_path_greps_that_file(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    result = await backend.agrep("Mara", path="/characters/mara.md")
    assert {m["path"] for m in result.matches} == {"/characters/mara.md"}


async def test_aglob_brace_expansion(stack):
    events, proj, read = stack
    await seed_canon(events, proj)
    backend = CanonBackend(read)
    result = await backend.aglob("/secrets/*.{md,txt}")
    assert [m["path"] for m in result.matches] == ["/secrets/the-scar.md"]
