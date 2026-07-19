# CPT-M1: canon_fs Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pure path-index and markdown-renderer modules that map canon records to a read-only virtual file tree — the foundation CPT-M2's `CanonBackend` routes through.

**Architecture:** Two new pure modules under `novelizer/canon_fs/`: `paths.py` builds a deterministic `path -> (kind, id)` index from record collections; `render.py` renders each record type to markdown with frontmatter that always carries the exact record id (feeds the cite-ids-exactly discipline). No I/O, no DB — everything unit- and property-testable in isolation.

**Tech Stack:** Python 3.13, pydantic models from `novelizer/store/models.py`, pytest + pytest-asyncio + hypothesis (all already project deps).

## Global Constraints

- Red/green TDD: every task writes the failing test first, watches it fail, then implements.
- Run tests ONLY in this worktree, never the main checkout (standing DB-lock rule).
- Test command prefix: `uv run pytest` from the worktree root.
- Tests mirror package layout: `tests/canon_fs/`, property tests named `*_property.py`.
- Canon is read-only through this subsystem; nothing here writes anything.
- Frontmatter of every rendered file MUST contain the exact record `id`.

---

### Task 1: `slugify`

**Files:**
- Create: `novelizer/canon_fs/__init__.py` (empty)
- Create: `novelizer/canon_fs/paths.py`
- Create: `tests/canon_fs/__init__.py` (empty)
- Test: `tests/canon_fs/test_paths.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `slugify(text: str) -> str` — lowercase, `[a-z0-9-]` only, never empty (falls back to `"untitled"`). Used by Task 2 and CPT-M2.

- [ ] **Step 1: Write the failing test**

```python
# tests/canon_fs/test_paths.py
from novelizer.canon_fs.paths import slugify


def test_slugify_basic():
    assert slugify("The Drowned Bell") == "the-drowned-bell"


def test_slugify_punctuation_collapses():
    assert slugify("Mara's  Scar!!") == "mara-s-scar"


def test_slugify_never_empty():
    assert slugify("") == "untitled"
    assert slugify("???") == "untitled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon_fs/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'novelizer.canon_fs'`

- [ ] **Step 3: Write minimal implementation**

```python
# novelizer/canon_fs/paths.py
from __future__ import annotations
import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lowercase filename-safe slug; never empty ("untitled" fallback)."""
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or "untitled"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon_fs/test_paths.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/ tests/canon_fs/
git commit -m "feat(canon_fs): slugify for virtual canon paths"
```

---

### Task 2: `build_path_index`

**Files:**
- Modify: `novelizer/canon_fs/paths.py`
- Test: `tests/canon_fs/test_paths.py`

**Interfaces:**
- Consumes: `slugify` (Task 1); models `Chapter`, `Character`, `WorldEntry`, `ThreadRecord`, `SecretRecord`, `ThemeRecord` from `novelizer.store.models`.
- Produces: `build_path_index(chapters, characters, world_entries, threads, secrets, themes) -> dict[str, tuple[str, str]]` mapping virtual path → `(kind, id)`, where kind ∈ `{"chapter","character","world","thread","secret","theme"}`. Chapters are `/chapters/NNN-slug.md` with `NNN` = 1-based position in the given list; other kinds are `/{dir}/{slug}.md` with an `-{id[:8]}` suffix on slug collision. CPT-M2's backend routes every `ls`/`read` through this.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/canon_fs/test_paths.py
from novelizer.canon_fs.paths import build_path_index
from novelizer.store.models import (
    Chapter, Character, SecretRecord, ThemeRecord, ThreadRecord, WorldEntry,
)


def _index(**kw):
    empty = dict(chapters=[], characters=[], world_entries=[], threads=[], secrets=[], themes=[])
    empty.update(kw)
    return build_path_index(**empty)


def test_chapter_paths_are_ordinal_prefixed():
    chapters = [Chapter(title="First Light", prose="p"), Chapter(title="The Drowned Bell", prose="p")]
    index = _index(chapters=chapters)
    assert index["/chapters/001-first-light.md"] == ("chapter", chapters[0].id)
    assert index["/chapters/002-the-drowned-bell.md"] == ("chapter", chapters[1].id)


def test_name_collision_gets_id_suffix():
    a, b = Character(name="Mara"), Character(name="Mara")
    index = _index(characters=[a, b])
    assert index["/characters/mara.md"] == ("character", a.id)
    assert index[f"/characters/mara-{b.id[:8]}.md"] == ("character", b.id)


def test_all_kinds_present():
    index = _index(
        chapters=[Chapter(title="C", prose="p")],
        characters=[Character(name="N")],
        world_entries=[WorldEntry(title="W", body="b")],
        threads=[ThreadRecord(id="t1", name="T")],
        secrets=[SecretRecord(id="s1", title="S")],
        themes=[ThemeRecord(id="th1", title="Th")],
    )
    kinds = {kind for kind, _ in index.values()}
    assert kinds == {"chapter", "character", "world", "thread", "secret", "theme"}
    assert len(index) == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon_fs/test_paths.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_path_index'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to novelizer/canon_fs/paths.py
from novelizer.store.models import (
    Chapter, Character, SecretRecord, ThemeRecord, ThreadRecord, WorldEntry,
)


def _claim(directory: str, name: str, record_id: str, taken: set[str]) -> str:
    path = f"/{directory}/{slugify(name)}.md"
    if path in taken:
        path = f"/{directory}/{slugify(name)}-{record_id[:8]}.md"
    taken.add(path)
    return path


def build_path_index(
    chapters: list[Chapter],
    characters: list[Character],
    world_entries: list[WorldEntry],
    threads: list[ThreadRecord],
    secrets: list[SecretRecord],
    themes: list[ThemeRecord],
) -> dict[str, tuple[str, str]]:
    """Deterministic virtual-tree map: path -> (kind, record id).

    Chapter ordinals come from list position (ReadStore.list_chapters is
    creation-ordered), so the tree reads in story order.
    """
    index: dict[str, tuple[str, str]] = {}
    taken: set[str] = set()
    for i, ch in enumerate(chapters, start=1):
        path = _claim("chapters", f"{i:03d}-{slugify(ch.title)}", ch.id, taken)
        index[path] = ("chapter", ch.id)
    for kind, directory, records, label in (
        ("character", "characters", characters, lambda r: r.name),
        ("world", "world", world_entries, lambda r: r.title),
        ("thread", "threads", threads, lambda r: r.name),
        ("secret", "secrets", secrets, lambda r: r.title),
        ("theme", "themes", themes, lambda r: r.title),
    ):
        for record in records:
            index[_claim(directory, label(record), record.id, taken)] = (kind, record.id)
    return index
```

Note: `_claim` slugifies its `name` argument; the chapter branch pre-builds
`NNN-slug` and slugify is idempotent on it (digits and dashes pass through).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon_fs/test_paths.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/paths.py tests/canon_fs/test_paths.py
git commit -m "feat(canon_fs): deterministic path index over canon records"
```

---

### Task 3: frontmatter + `render_chapter` + `render_world_entry`

**Files:**
- Create: `novelizer/canon_fs/render.py`
- Test: `tests/canon_fs/test_render.py`

**Interfaces:**
- Consumes: models from `novelizer.store.models`.
- Produces:
  - `_frontmatter(pairs: list[tuple[str, str]]) -> str` (module-private helper),
  - `render_chapter(chapter: Chapter) -> str`,
  - `render_world_entry(entry: WorldEntry) -> str`.
  Every renderer returns markdown whose frontmatter includes `id: <exact id>` and `kind: <kind>`. CPT-M2 serves these strings as file contents.

- [ ] **Step 1: Write the failing test**

```python
# tests/canon_fs/test_render.py
from novelizer.canon_fs.render import render_chapter, render_world_entry
from novelizer.store.models import Chapter, Domain, WorldEntry


def test_render_chapter_has_id_status_cast_and_full_prose():
    ch = Chapter(title="The Drowned Bell", prose="Long prose here.", character_ids=["c1", "c2"])
    out = render_chapter(ch)
    assert out.startswith("---\n")
    assert f"id: {ch.id}" in out
    assert "kind: chapter" in out
    assert "status: draft" in out
    assert "characters: c1, c2" in out
    assert "# The Drowned Bell" in out
    assert "Long prose here." in out


def test_render_world_entry_has_domain_and_body():
    e = WorldEntry(title="The Bell Cult", body="They ring at dusk.", domain=Domain.social, tags=["cult"])
    out = render_world_entry(e)
    assert f"id: {e.id}" in out
    assert "kind: world" in out
    assert "domain: social" in out
    assert "tags: cult" in out
    assert "They ring at dusk." in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon_fs/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError` on `novelizer.canon_fs.render`

- [ ] **Step 3: Write minimal implementation**

```python
# novelizer/canon_fs/render.py
from __future__ import annotations
from novelizer.store.models import Chapter, WorldEntry


def _frontmatter(pairs: list[tuple[str, str]]) -> str:
    lines = "\n".join(f"{k}: {v}" for k, v in pairs if v != "")
    return f"---\n{lines}\n---\n"


def render_chapter(chapter: Chapter) -> str:
    fm = _frontmatter([
        ("id", chapter.id),
        ("kind", "chapter"),
        ("status", chapter.editorial_status.value),
        ("characters", ", ".join(chapter.character_ids)),
    ])
    return f"{fm}\n# {chapter.title}\n\n{chapter.prose}\n"


def render_world_entry(entry: WorldEntry) -> str:
    fm = _frontmatter([
        ("id", entry.id),
        ("kind", "world"),
        ("domain", entry.domain.value),
        ("canon_status", entry.canon_status.value),
        ("tags", ", ".join(entry.tags)),
    ])
    return f"{fm}\n# {entry.title}\n\n{entry.body}\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon_fs/test_render.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/render.py tests/canon_fs/test_render.py
git commit -m "feat(canon_fs): chapter and world-entry renderers"
```

---

### Task 4: `render_character` with knows-block

**Files:**
- Modify: `novelizer/canon_fs/render.py`
- Test: `tests/canon_fs/test_render.py`

**Interfaces:**
- Consumes: `_frontmatter` (Task 3); `knowledge_cell_state(matrix, secret_id, character_id) -> str` from `novelizer.canon.secrets` (returns `"unknown" | "known" | "revealed"`); `Character`, `SecretRecord` models. The matrix shape is `ReadStore.knowledge_matrix()`'s: `{secret_id: {"revealed": bool, "known_by": set[str]}}`.
- Produces: `render_character(character: Character, matrix: dict[str, dict], secrets: list[SecretRecord]) -> str` — dossier including a `## Knows` section listing each non-revealed secret this character knows as `- {secret.id} ({secret.title})`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/canon_fs/test_render.py
from novelizer.canon_fs.render import render_character
from novelizer.store.models import Character, CharacterRelationship, SecretRecord


def test_render_character_dossier_and_knows():
    c = Character(
        name="Mara", traits="stubborn", motivations="find the bell", arc_status="rising",
        relationships=[CharacterRelationship(target_character_id="c2", description="rival")],
    )
    secrets = [SecretRecord(id="s1", title="The scar's origin"), SecretRecord(id="s2", title="Hidden door")]
    matrix = {"s1": {"revealed": False, "known_by": {c.id}}, "s2": {"revealed": False, "known_by": set()}}
    out = render_character(c, matrix, secrets)
    assert f"id: {c.id}" in out
    assert "kind: character" in out
    assert "# Mara" in out
    assert "traits: stubborn" in out
    assert "- c2: rival" in out
    assert "- s1 (The scar's origin)" in out
    assert "s2" not in out.split("## Knows")[1]


def test_render_character_no_secrets_omits_knows_section():
    c = Character(name="Bo")
    out = render_character(c, {}, [])
    assert "## Knows" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon_fs/test_render.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_character'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to novelizer/canon_fs/render.py  (extend the models import at top)
from novelizer.canon.secrets import knowledge_cell_state
from novelizer.store.models import Character, SecretRecord


def render_character(
    character: Character, matrix: dict[str, dict], secrets: list[SecretRecord]
) -> str:
    fm = _frontmatter([
        ("id", character.id),
        ("kind", "character"),
        ("aliases", ", ".join(character.aliases)),
        ("traits", character.traits),
        ("motivations", character.motivations),
        ("arc_status", character.arc_status),
        ("voice", character.voice),
    ])
    body = [f"\n# {character.name}\n"]
    if character.backstory:
        body.append(f"\n{character.backstory}\n")
    if character.relationships:
        rel = "\n".join(f"- {r.target_character_id}: {r.description}" for r in character.relationships)
        body.append(f"\n## Relationships\n\n{rel}\n")
    known = [
        s for s in secrets
        if knowledge_cell_state(matrix, s.id, character.id) == "known"
    ]
    if known:
        lines = "\n".join(f"- {s.id} ({s.title})" for s in known)
        body.append(f"\n## Knows\n\n{lines}\n")
    return fm + "".join(body)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon_fs/test_render.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/render.py tests/canon_fs/test_render.py
git commit -m "feat(canon_fs): character dossier renderer with knows-block"
```

---

### Task 5: `render_thread`, `render_secret`, `render_theme`

**Files:**
- Modify: `novelizer/canon_fs/render.py`
- Test: `tests/canon_fs/test_render.py`

**Interfaces:**
- Consumes: `_frontmatter`, `knowledge_cell_state`, models `ThreadRecord`, `SecretRecord`, `ThemeRecord`, `Character`.
- Produces:
  - `render_thread(thread: ThreadRecord) -> str`,
  - `render_secret(secret: SecretRecord, matrix: dict[str, dict], characters: list[Character]) -> str` (who-knows names, or `known to no one`),
  - `render_theme(theme: ThemeRecord) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/canon_fs/test_render.py
from novelizer.canon_fs.render import render_secret, render_theme, render_thread
from novelizer.store.models import ThemeRecord, ThreadRecord, ThreadState


def test_render_thread_state_and_touches():
    t = ThreadRecord(id="t1", name="Bell's Curse", state=ThreadState.touched,
                     touch_count=3, last_note="rang again", last_chapter_id="ch9")
    out = render_thread(t)
    assert "id: t1" in out and "kind: thread" in out
    assert "state: touched" in out and "touch_count: 3" in out
    assert "last_chapter_id: ch9" in out and "rang again" in out


def test_render_secret_who_knows():
    mara = Character(name="Mara")
    s = SecretRecord(id="s1", title="The scar's origin")
    matrix = {"s1": {"revealed": False, "known_by": {mara.id}}}
    out = render_secret(s, matrix, [mara])
    assert "id: s1" in out and "kind: secret" in out
    assert "revealed: False" in out
    assert "known to: Mara" in out


def test_render_secret_known_to_no_one():
    s = SecretRecord(id="s2", title="Hidden door")
    out = render_secret(s, {"s2": {"revealed": False, "known_by": set()}}, [])
    assert "known to no one" in out


def test_render_theme():
    th = ThemeRecord(id="th1", title="Drowning as memory", touch_count=2, last_chapter_id="ch4")
    out = render_theme(th)
    assert "id: th1" in out and "kind: theme" in out
    assert "touch_count: 2" in out and "last_chapter_id: ch4" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/canon_fs/test_render.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_secret'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to novelizer/canon_fs/render.py  (extend the models import at top)
from novelizer.store.models import ThemeRecord, ThreadRecord


def render_thread(thread: ThreadRecord) -> str:
    fm = _frontmatter([
        ("id", thread.id),
        ("kind", "thread"),
        ("state", thread.state.value),
        ("touch_count", str(thread.touch_count)),
        ("last_chapter_id", thread.last_chapter_id),
    ])
    note = f"\n{thread.last_note}\n" if thread.last_note else ""
    return f"{fm}\n# {thread.name}\n{note}"


def render_secret(
    secret: SecretRecord, matrix: dict[str, dict], characters: list[Character]
) -> str:
    known = sorted(
        c.name for c in characters
        if knowledge_cell_state(matrix, secret.id, c.id) == "known"
    )
    who = f"known to: {', '.join(known)}" if known else "known to no one"
    fm = _frontmatter([
        ("id", secret.id),
        ("kind", "secret"),
        ("revealed", str(secret.revealed)),
    ])
    return f"{fm}\n# {secret.title}\n\n{who}\n"


def render_theme(theme: ThemeRecord) -> str:
    fm = _frontmatter([
        ("id", theme.id),
        ("kind", "theme"),
        ("touch_count", str(theme.touch_count)),
        ("last_chapter_id", theme.last_chapter_id),
    ])
    note = f"\n{theme.last_note}\n" if theme.last_note else ""
    return f"{fm}\n# {theme.title}\n{note}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/canon_fs/test_render.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/canon_fs/render.py tests/canon_fs/test_render.py
git commit -m "feat(canon_fs): thread, secret, theme renderers"
```

---

### Task 6: Property tests

**Files:**
- Test: `tests/canon_fs/test_canon_fs_property.py`

**Interfaces:**
- Consumes: everything produced in Tasks 1–5.
- Produces: hypothesis coverage of the module invariants (no new runtime code expected; if a property fails, fix the implementation in the same task).

- [ ] **Step 1: Write the property tests**

```python
# tests/canon_fs/test_canon_fs_property.py
import re
from hypothesis import given, strategies as st
from novelizer.canon_fs.paths import build_path_index, slugify
from novelizer.canon_fs.render import render_chapter
from novelizer.store.models import Chapter, Character

SLUG_OK = re.compile(r"^[a-z0-9-]+$")


@given(st.text())
def test_slugify_total_and_filename_safe(text):
    slug = slugify(text)
    assert SLUG_OK.match(slug)
    assert slugify(slug) == slug  # idempotent


@given(st.lists(st.text(min_size=0, max_size=30), max_size=12))
def test_path_index_is_total_and_unique(titles):
    chapters = [Chapter(title=t, prose="p") for t in titles]
    characters = [Character(name=t) for t in titles]
    index = build_path_index(
        chapters=chapters, characters=characters,
        world_entries=[], threads=[], secrets=[], themes=[],
    )
    assert len(index) == len(chapters) + len(characters)  # no silent drops
    ids = {record_id for _, record_id in index.values()}
    assert ids == {c.id for c in chapters} | {c.id for c in characters}


@given(st.text(min_size=0, max_size=50), st.text(min_size=0, max_size=200))
def test_render_chapter_always_carries_exact_id(title, prose):
    ch = Chapter(title=title, prose=prose)
    out = render_chapter(ch)
    assert f"id: {ch.id}" in out
    assert out.startswith("---\n")
```

- [ ] **Step 2: Run and inspect**

Run: `uv run pytest tests/canon_fs/test_canon_fs_property.py -v`
Expected: 3 PASS. If hypothesis finds a counterexample (likely candidates:
identical auto-slugged names colliding beyond the id-suffix guard), fix the
implementation in `paths.py`/`render.py` — do not weaken the property.

- [ ] **Step 3: Run the whole canon_fs suite**

Run: `uv run pytest tests/canon_fs -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add tests/canon_fs/test_canon_fs_property.py novelizer/canon_fs/
git commit -m "test(canon_fs): property coverage for slugs, path index, renderer ids"
```
