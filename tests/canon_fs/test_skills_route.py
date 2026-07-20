import os
import tempfile

import pytest
from deepagents.backends import CompositeBackend

from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon_fs.backend import READ_ONLY_ERROR, CanonBackend
from novelizer.canon_fs.skills_route import ReadOnlyBackend, build_skills_backend


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


def build_composite(read):
    return CompositeBackend(
        default=CanonBackend(read),
        routes={"/skills/": build_skills_backend()},
    )


async def test_skills_read_returns_frontmatter(stack):
    _events, proj, read = stack
    await proj.catch_up()
    composite = build_composite(read)
    result = await composite.aread("/skills/outlining/SKILL.md")
    assert result.error is None
    assert "name: outlining" in result.file_data["content"]


async def test_skills_ls_lists_five_packs(stack):
    _events, proj, read = stack
    await proj.catch_up()
    composite = build_composite(read)
    result = await composite.als("/skills")
    paths = {e["path"] for e in result.entries}
    expected = {
        "/skills/outlining",
        "/skills/promise-payoff",
        "/skills/scene-sequel",
        "/skills/character-arcs",
        "/skills/pacing",
    }
    # entries may have trailing slash for directories
    normalized = {p.rstrip("/") for p in paths}
    assert expected <= normalized


async def test_skills_write_refused(stack):
    _events, proj, read = stack
    await proj.catch_up()
    composite = build_composite(read)
    result = await composite.awrite("/skills/outlining/SKILL.md", "hacked")
    assert result.error == READ_ONLY_ERROR


def test_read_only_backend_sync_write_refused():
    inner = build_skills_backend()  # already a ReadOnlyBackend
    assert isinstance(inner, ReadOnlyBackend)
    result = inner.write("/outlining/SKILL.md", "hacked")
    assert result.error == READ_ONLY_ERROR


def test_read_only_backend_sync_reads_raise_not_implemented():
    backend = build_skills_backend()
    with pytest.raises(NotImplementedError):
        backend.ls("/")
    with pytest.raises(NotImplementedError):
        backend.read("/outlining/SKILL.md")


async def test_skills_ls_hides_packaging_artifacts(stack):
    """__init__.py (makes skills_packs importable) and __pycache__/ (bytecode
    cache) are packaging artifacts, not skill content -- they must never
    surface in an agent-visible /skills/ listing. SkillsMiddleware treats
    every is_dir entry as a candidate skill directory and probes
    <entry>/SKILL.md, so an unfiltered __pycache__ entry produces a spurious
    failed download on every load."""
    _events, proj, read = stack
    await proj.catch_up()
    composite = build_composite(read)
    result = await composite.als("/skills")
    names = {e["path"].rstrip("/").rsplit("/", 1)[-1] for e in result.entries}
    assert "__init__.py" not in names
    assert "__pycache__" not in names


async def test_skills_glob_hides_nested_pycache_files(stack):
    """The hidden-entry filter must match on ANY path segment, not just the
    basename -- otherwise aglob("**/*", ...) still surfaces compiled files
    living inside a nested __pycache__/ directory (e.g.
    /skills/outlining/__pycache__/foo.cpython-313.pyc), whose basename is
    the .pyc filename, not "__pycache__"."""
    _events, proj, read = stack
    await proj.catch_up()
    composite = build_composite(read)
    result = await composite.aglob("**/*.pyc", "/skills")
    assert result.matches == []


async def test_skills_download_files_delegates_to_inner(stack):
    """adownload_files is a bulk READ (SkillsMiddleware uses it to fetch
    every candidate SKILL.md) and must be delegated, not refused like
    writes -- refusing it is exactly what made skills fail to load."""
    _events, proj, read = stack
    await proj.catch_up()
    composite = build_composite(read)
    responses = await composite.adownload_files(["/skills/outlining/SKILL.md"])
    assert len(responses) == 1
    assert responses[0].error is None
    assert b"name: outlining" in responses[0].content
