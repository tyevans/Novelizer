import os
import tempfile

import pytest

from novelizer.canon.event_store import EventStore
from novelizer.canon.events import (
    BeatSpec, BlueprintAdopted, ChapterBriefDrafted, EventType,
)
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon_fs.backend import READ_ONLY_ERROR
from novelizer.canon_fs.outline import OutlineBackend


def test_write_and_edit_refuse_with_intent_message():
    backend = OutlineBackend(read_store=None)
    w = backend.write("/blueprint.md", "x")
    assert w.error == READ_ONLY_ERROR and w.path is None
    e = backend.edit("/blueprint.md", "old", "new")
    assert e.error == READ_ONLY_ERROR and e.path is None


async def test_async_write_and_edit_refuse_too():
    backend = OutlineBackend(read_store=None)
    assert (await backend.awrite("/x.md", "c")).error == READ_ONLY_ERROR
    assert (await backend.aedit("/x.md", "a", "b")).error == READ_ONLY_ERROR


def test_upload_download_refuse_per_file():
    backend = OutlineBackend(read_store=None)
    ups = backend.upload_files([("/a.md", b"x")])
    assert ups[0].error == "permission_denied"
    downs = backend.download_files(["/a.md"])
    assert downs[0].error == "permission_denied" and downs[0].content is None


def test_sync_read_surface_names_async_path():
    backend = OutlineBackend(read_store=None)
    for method, args in (("ls", ("/",)), ("read", ("/x.md",)),
                         ("grep", ("q",)), ("glob", ("*.md",))):
        with pytest.raises(NotImplementedError, match="a" + method):
            getattr(backend, method)(*args)


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


async def seed_outline(events, proj):
    adopted = BlueprintAdopted(
        blueprint_id="bp1", framework="six-position", target_chapter_count=10, genre="fantasy",
        beats=[BeatSpec(beat_id="bp1-catalyst", slug="catalyst", name="Catalyst",
                        ideal_pct=0.10, tolerance_pct=0.05)],
    )
    await events.append(EventType.BLUEPRINT_ADOPTED, "bp1", adopted)
    drafted = ChapterBriefDrafted(brief_id="brief1", target_ordinal=4, goal="Mara rings the bell.")
    await events.append(EventType.CHAPTER_BRIEF_DRAFTED, "brief1", drafted)
    await proj.catch_up()


async def test_als_root_lists_outline_files(stack):
    events, proj, read = stack
    await seed_outline(events, proj)
    backend = OutlineBackend(read)
    result = await backend.als("/")
    paths = {e["path"] for e in result.entries}
    assert {"/blueprint.md", "/beats.md", "/threads-plan.md", "/ledger.md", "/briefs"} <= paths


async def test_als_never_errors_with_no_blueprint(stack):
    events, proj, read = stack
    await proj.catch_up()
    backend = OutlineBackend(read)
    result = await backend.als("/")
    assert result.error is None
    blueprint = await backend.aread("/blueprint.md")
    assert blueprint.error is None
    assert "No blueprint adopted." in blueprint.file_data["content"]


async def test_aread_blueprint_has_rendered_content(stack):
    events, proj, read = stack
    await seed_outline(events, proj)
    backend = OutlineBackend(read)
    result = await backend.aread("/blueprint.md")
    assert result.error is None
    assert "id: bp1" in result.file_data["content"]


async def test_aread_brief_via_glob_path(stack):
    events, proj, read = stack
    await seed_outline(events, proj)
    backend = OutlineBackend(read)
    globbed = await backend.aglob("/briefs/004-*.md")
    assert len(globbed.matches) == 1
    path = globbed.matches[0]["path"]
    result = await backend.aread(path)
    assert result.error is None
    assert "Mara rings the bell." in result.file_data["content"]


async def test_agrep_finds_goal_string(stack):
    events, proj, read = stack
    await seed_outline(events, proj)
    backend = OutlineBackend(read)
    result = await backend.agrep("Mara rings the bell.")
    assert any("briefs" in m["path"] for m in result.matches)
