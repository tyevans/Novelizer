# CPT-M2: CanonBackend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only `CanonBackend` implementing deepagents' `BackendProtocol` over `ReadStore`, so the built-in `ls`/`read_file`/`grep`/`glob` tools serve the whole story canon; plus the small follow-ups deferred from CPT-M1's final review.

**Architecture:** One new module `novelizer/canon_fs/backend.py`. Each async call takes a fresh `_Snapshot` (all six record collections + knowledge matrix + `build_path_index`) and routes through CPT-M1's pure renderers — the backend holds no state and no rendering logic. Async methods (`als`/`aread`/`agrep`/`aglob`) are the implementation (agents run via `ainvoke`, and deepagents' async tool paths call them directly); sync read variants raise `NotImplementedError` naming the async path. Writes refuse with a message pointing at the intent path.

**Tech Stack:** Python 3.13; `deepagents` 0.6.12 (`BackendProtocol`, result dataclasses, `slice_read_response`/`create_file_data` utils); `wcmatch` (already installed as a deepagents dependency) for glob matching; pytest + pytest-asyncio over the EventStore→Projector→ReadStore stack fixture pattern from `tests/canon/test_read_store.py`.

## Global Constraints

- Red/green TDD: every task writes the failing test first, watches it fail, then implements.
- Run tests ONLY in this worktree, never the main checkout. Test command prefix: `uv run pytest`.
- Canon is read-only through this subsystem: `write`/`edit`/`upload_files` NEVER mutate anything; refusal text is exactly `"Canon is read-only. To change the story record, declare intents in your structured response."`
- Use deepagents' exact result types: `LsResult(entries=[FileInfo])`, `ReadResult(file_data=FileData)`, `GrepResult(matches=[GrepMatch])`, `GlobResult(matches=[FileInfo])`, `WriteResult(error=…)`, `EditResult(error=…)`; `GrepMatch.line` is 1-indexed; grep patterns are LITERAL substrings, not regex.
- `aread` returns RAW content sliced by `slice_read_response` — line-number formatting is the middleware's job, not ours.
- All paths in the virtual tree come from `build_path_index`; the backend never invents paths.

---

### Task 1: Backend skeleton + read-only refusal surface

**Files:**
- Create: `novelizer/canon_fs/backend.py`
- Test: `tests/canon_fs/test_backend.py`

**Interfaces:**
- Consumes: `deepagents.backends.protocol` types.
- Produces: `CanonBackend(read_store)` class (subclass of `BackendProtocol`), module constant `READ_ONLY_ERROR`, and the refusal surface later tasks build on. Sync `write`/`edit`/`upload_files`/`download_files` are the implementations (they are pure, so the inherited `asyncio.to_thread` async wrappers `awrite`/`aedit` work for free). Sync `ls`/`read`/`grep`/`glob` raise `NotImplementedError` with a message naming the async variant.

- [ ] **Step 1: Write the failing test**

```python
# tests/canon_fs/test_backend.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon_fs/test_backend.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.canon_fs.backend'`

- [ ] **Step 3: Write minimal implementation**

```python
# novelizer/canon_fs/backend.py
from __future__ import annotations

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

READ_ONLY_ERROR = (
    "Canon is read-only. To change the story record, declare intents "
    "in your structured response."
)


class CanonBackend(BackendProtocol):
    """Read-only virtual filesystem over story canon, routed through
    build_path_index and the canon_fs renderers.

    Path stability caveat: a record's path can change between calls when a
    same-named record lands later (the newer record takes an id-suffixed
    path; ordinals shift if chapters are ever removed). The stable handle
    is the record id in every file's frontmatter, never the path.
    """

    def __init__(self, read_store) -> None:
        self._read = read_store

    # -- writes: refused (sync impls; inherited awrite/aedit wrap them) --

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=READ_ONLY_ERROR)

    def edit(
        self, file_path: str, old_string: str, new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error=READ_ONLY_ERROR)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=p, error="permission_denied") for p, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [FileDownloadResponse(path=p, error="permission_denied") for p in paths]

    # -- reads: async-only (agents run via ainvoke; sync names the way) --

    def ls(self, path: str) -> LsResult:
        raise NotImplementedError("CanonBackend is async-only; use als")

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        raise NotImplementedError("CanonBackend is async-only; use aread")

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        raise NotImplementedError("CanonBackend is async-only; use agrep")

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        raise NotImplementedError("CanonBackend is async-only; use aglob")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon_fs/test_backend.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/backend.py tests/canon_fs/test_backend.py
git commit -m "feat(canon_fs): CanonBackend skeleton with read-only refusal surface"
```

---

### Task 2: `_Snapshot` + `aread`

**Files:**
- Modify: `novelizer/canon_fs/backend.py`
- Test: `tests/canon_fs/test_backend.py`

**Interfaces:**
- Consumes: `ReadStore.list_chapters/list_characters/list_world_entries/list_threads/list_secrets/list_themes/knowledge_matrix` (all async); `build_path_index` (CPT-M1); all six renderers (CPT-M1); `deepagents.backends.utils.slice_read_response` + `create_file_data`.
- Produces: `CanonBackend._snapshot() -> _Snapshot` (frozen view: `index` plus per-kind id-keyed dicts and `matrix`) and `CanonBackend._render(snap, kind, record_id) -> str` — Tasks 3-5 route through both. `aread(file_path, offset=0, limit=2000) -> ReadResult`: missing path → `ReadResult(error="File '<path>' not found. Hint: ls the parent directory.")`.

- [ ] **Step 1: Write the failing test** — append; the stack fixture and seed helper are shared by Tasks 3-5.

```python
# append to tests/canon_fs/test_backend.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon_fs/test_backend.py -v`
Expected: 4 prior PASS; new tests FAIL (default `BackendProtocol.aread` wraps sync `read`, so they raise `NotImplementedError`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend.py — add to imports
from dataclasses import dataclass

from deepagents.backends.utils import create_file_data, slice_read_response

from novelizer.canon_fs.paths import build_path_index
from novelizer.canon_fs.render import (
    render_chapter, render_character, render_secret, render_theme,
    render_thread, render_world_entry,
)


@dataclass
class _Snapshot:
    """One consistent view of canon for a single backend call."""

    index: dict[str, tuple[str, str]]
    chapters: dict
    characters: dict
    world: dict
    threads: dict
    secrets: dict
    themes: dict
    matrix: dict


# methods on CanonBackend:

    async def _snapshot(self) -> _Snapshot:
        chapters = await self._read.list_chapters()
        characters = await self._read.list_characters()
        world = await self._read.list_world_entries()
        threads = await self._read.list_threads()
        secrets = await self._read.list_secrets()
        themes = await self._read.list_themes()
        matrix = await self._read.knowledge_matrix()
        return _Snapshot(
            index=build_path_index(chapters, characters, world, threads, secrets, themes),
            chapters={r.id: r for r in chapters},
            characters={r.id: r for r in characters},
            world={r.id: r for r in world},
            threads={r.id: r for r in threads},
            secrets={r.id: r for r in secrets},
            themes={r.id: r for r in themes},
            matrix=matrix,
        )

    def _render(self, snap: _Snapshot, kind: str, record_id: str) -> str:
        if kind == "chapter":
            return render_chapter(snap.chapters[record_id])
        if kind == "character":
            return render_character(
                snap.characters[record_id], snap.matrix, list(snap.secrets.values())
            )
        if kind == "world":
            return render_world_entry(snap.world[record_id])
        if kind == "thread":
            return render_thread(snap.threads[record_id])
        if kind == "secret":
            return render_secret(
                snap.secrets[record_id], snap.matrix, list(snap.characters.values())
            )
        return render_theme(snap.themes[record_id])

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        snap = await self._snapshot()
        entry = snap.index.get(file_path)
        if entry is None:
            return ReadResult(
                error=f"File '{file_path}' not found. Hint: ls the parent directory."
            )
        kind, record_id = entry
        file_data = create_file_data(self._render(snap, kind, record_id))
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(file_data=create_file_data(sliced))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon_fs/test_backend.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/backend.py tests/canon_fs/test_backend.py
git commit -m "feat(canon_fs): snapshot-routed aread over ReadStore"
```

---

### Task 3: `als`

**Files:**
- Modify: `novelizer/canon_fs/backend.py`
- Test: `tests/canon_fs/test_backend.py`

**Interfaces:**
- Consumes: `_snapshot` (Task 2); `FileInfo` from the protocol module.
- Produces: `als(path) -> LsResult`. `/` lists the six kind directories as `FileInfo(path=..., is_dir=True)`; a kind directory lists its files sorted; anything else errors naming the valid top-level directories. Module constant `KIND_DIRS = ("chapters", "characters", "world", "threads", "secrets", "themes")`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/canon_fs/test_backend.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon_fs/test_backend.py -v`
Expected: new tests FAIL with `NotImplementedError` (default `als` wraps sync `ls`)

- [ ] **Step 3: Write minimal implementation**

```python
# backend.py — add FileInfo to the protocol import; add module constant
KIND_DIRS = ("chapters", "characters", "world", "threads", "secrets", "themes")

# method on CanonBackend:
    async def als(self, path: str) -> LsResult:
        snap = await self._snapshot()
        norm = "/" + path.strip("/")
        if norm == "/":
            return LsResult(
                entries=[FileInfo(path=f"/{d}", is_dir=True) for d in KIND_DIRS]
            )
        if norm.lstrip("/") not in KIND_DIRS:
            valid = ", ".join(f"/{d}" for d in KIND_DIRS)
            return LsResult(error=f"Directory '{path}' not found. Top-level directories: {valid}")
        prefix = norm + "/"
        return LsResult(entries=[
            FileInfo(path=p, is_dir=False) for p in sorted(snap.index) if p.startswith(prefix)
        ])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon_fs/test_backend.py -v`
Expected: 11 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/backend.py tests/canon_fs/test_backend.py
git commit -m "feat(canon_fs): als over the virtual canon tree"
```

---

### Task 4: `aglob`

**Files:**
- Modify: `novelizer/canon_fs/backend.py`
- Test: `tests/canon_fs/test_backend.py`

**Interfaces:**
- Consumes: `_snapshot`; `wcmatch.glob.globmatch` with `GLOBSTAR`.
- Produces: `aglob(pattern, path=None) -> GlobResult`. Pattern may be absolute (`/chapters/*.md`) or relative to `path` (default `/`). Matches sorted by path.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/canon_fs/test_backend.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon_fs/test_backend.py -v`
Expected: new tests FAIL with `NotImplementedError`

- [ ] **Step 3: Write minimal implementation**

```python
# backend.py — add import
from wcmatch import glob as wcglob

# method on CanonBackend:
    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        snap = await self._snapshot()
        if pattern.startswith("/"):
            full = pattern.lstrip("/")
        else:
            base = (path or "/").strip("/")
            full = f"{base}/{pattern}" if base else pattern
        matches = [
            FileInfo(path=p, is_dir=False)
            for p in sorted(snap.index)
            if wcglob.globmatch(p.lstrip("/"), full, flags=wcglob.GLOBSTAR)
        ]
        return GlobResult(matches=matches)
```

Note: `**/*.md` under GLOBSTAR also matches paths with zero intermediate
directories only when the base is empty; canon files always sit exactly one
directory deep, so `**/*.md` matches all six.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon_fs/test_backend.py -v`
Expected: 14 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/backend.py tests/canon_fs/test_backend.py
git commit -m "feat(canon_fs): aglob with globstar over canon paths"
```

---

### Task 5: `agrep`

**Files:**
- Modify: `novelizer/canon_fs/backend.py`
- Test: `tests/canon_fs/test_backend.py`

**Interfaces:**
- Consumes: `_snapshot`, `_render`, `wcglob`, `GrepMatch`.
- Produces: `agrep(pattern, path=None, glob=None) -> GrepResult`. LITERAL substring match over rendered file contents; `path` scopes to a directory subtree; `glob` filters which files are searched; `GrepMatch(path, line (1-indexed), text)`; files scanned in sorted path order.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/canon_fs/test_backend.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon_fs/test_backend.py -v`
Expected: new tests FAIL with `NotImplementedError`

- [ ] **Step 3: Write minimal implementation**

```python
# backend.py — add GrepMatch to the protocol import

# method on CanonBackend:
    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        snap = await self._snapshot()
        base = "/" + (path or "").strip("/")
        prefix = "/" if base == "/" else base + "/"
        matches: list[GrepMatch] = []
        for p in sorted(snap.index):
            if not p.startswith(prefix):
                continue
            if glob and not wcglob.globmatch(p.lstrip("/"), glob, flags=wcglob.GLOBSTAR):
                continue
            kind, record_id = snap.index[p]
            for line_no, text in enumerate(
                self._render(snap, kind, record_id).splitlines(), start=1
            ):
                if pattern in text:
                    matches.append(GrepMatch(path=p, line=line_no, text=text))
        return GrepResult(matches=matches)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon_fs/test_backend.py -v`
Expected: 17 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/backend.py tests/canon_fs/test_backend.py
git commit -m "feat(canon_fs): literal agrep over rendered canon"
```

---

### Task 6: CPT-M1 review follow-ups

**Files:**
- Modify: `tests/canon_fs/test_paths.py`
- Modify: `tests/canon_fs/test_render.py`

**Interfaces:**
- Consumes: `_claim` (via `build_path_index`), `slugify`, `render_character` from CPT-M1.
- Produces: test coverage only — no runtime code is expected to change. If any test fails, fix the implementation, never the test.

- [ ] **Step 1: Hoist the mid-file imports in `tests/canon_fs/test_render.py`**

Move the `from novelizer.canon_fs.render import ...` / `from novelizer.store.models import ...` lines that sit between test functions up into the top-of-file import block (merge duplicates). Run `uv run pytest tests/canon_fs/test_render.py -v` — still all passing.

- [ ] **Step 2: Write the follow-up tests**

```python
# append to tests/canon_fs/test_paths.py
import pytest


def test_duplicate_record_ids_raise_instead_of_silently_dropping():
    dup = Character(name="Mara")
    with pytest.raises(ValueError):
        _index(characters=[dup, dup.model_copy()])


def test_non_ascii_names_fall_back_to_untitled_with_suffix():
    cjk = [Character(name="鈴の呪い"), Character(name="鐘の記憶")]
    index = _index(characters=cjk)
    assert len(index) == 2
    assert all(p.startswith("/characters/untitled") for p in index)
```

```python
# append to tests/canon_fs/test_render.py
def test_render_character_aliases_and_voice_in_frontmatter():
    c = Character(name="Mara", aliases=["The Bell-Ringer", "M"], voice="clipped, dry")
    out = render_character(c, {}, [])
    assert "aliases: The Bell-Ringer, M" in out
    assert "voice: clipped, dry" in out
```

- [ ] **Step 3: Run and inspect**

Run: `uv run pytest tests/canon_fs -v`
Expected: all PASS (21 from M1 + 17 backend + 3 new = 41). The duplicate-id
test exercises `_claim`'s ValueError tier: same name and same id exhausts
base, 8-char, and full-id paths. If it does NOT raise, that is an
implementation bug in `paths.py` — fix there.

- [ ] **Step 4: Commit**

```bash
git add tests/canon_fs/test_paths.py tests/canon_fs/test_render.py
git commit -m "test(canon_fs): M1 review follow-ups — ValueError tier, non-ASCII slugs, aliases/voice"
```
