# EPUB Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user export a story's chapters as a `.epub` file from the TUI command palette.

**Architecture:** A pure `build_epub()` function in a new `novelizer/export/` module builds the EPUB bytes from a list of `Chapter` records (no store/runtime dependency). A new `ExportScreen` modal (mirrors the existing `ApprovalScreen` pattern) collects title/author/status, calls `ReadStore.list_chapters`, calls `build_epub`, and writes the result to disk. Wired into the TUI via the existing `AppCommand`/`NovelizerCommandProvider` command-palette mechanism — no CLI, no new keybinding.

**Tech Stack:** Python, `ebooklib` (new dependency), Textual (existing TUI framework), pytest + pytest-asyncio (existing test stack).

## Global Constraints

- Never run the test suite against the main checkout — this plan must be executed inside an isolated worktree (project rule, DB-lock incident precedent).
- No CLI command, no dedicated keybinding — command-palette access only (per approved design).
- `stories/` is already fully gitignored — no `.gitignore` change needed for `stories/*/export/`.
- Chapter order for export is whatever `ReadStore.list_chapters` returns (`ORDER BY rowid`) — do not re-sort.
- `build_epub` raises `ValueError` on an empty chapter list; no file is written in that case.

---

## File Structure

- Create `novelizer/export/__init__.py` — empty, marks the package.
- Create `novelizer/export/epub.py` — `build_epub(chapters, *, title, author) -> bytes`.
- Create `tests/export/__init__.py` — empty.
- Create `tests/export/test_epub.py` — unit tests for `build_epub`.
- Create `novelizer/tui/export_screen.py` — `ExportScreen(ModalScreen)`.
- Create `tests/tui/test_export_screen.py` — smoke test for the modal + file write.
- Modify `novelizer/tui/app.py` — add `_app_open_export`, register `AppCommand("export_epub", ...)`.
- Modify `pyproject.toml` — add `ebooklib` to `dependencies`.

---

### Task 1: Add `ebooklib` dependency

**Files:**
- Modify: `pyproject.toml` (dependencies list, currently lines 14-28)

**Interfaces:**
- Produces: `ebooklib` importable as `import ebooklib` and `from ebooklib import epub`, used by Task 2.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, the `dependencies` list currently reads:

```toml
dependencies = [
    "aiosqlite>=0.20.0",
    "chromadb>=0.5.0",
    "click>=8.2.1",
    "deepagents>=0.6.12",
    "httpx>=0.28",
    "langchain>=1.3.14",
    "langchain-openai>=1.3.5",
    "langgraph>=1.2.9",
    "pydantic>=2.11.7",
    "pydantic-settings>=2.10.1",
    "rich>=14.1.0",
    "textual>=5.3.0",
    "tomli-w>=1.0.0",
]
```

Add `"ebooklib>=0.18"` in alphabetical position (between `"deepagents>=0.6.12"` and `"httpx>=0.28"`):

```toml
dependencies = [
    "aiosqlite>=0.20.0",
    "chromadb>=0.5.0",
    "click>=8.2.1",
    "deepagents>=0.6.12",
    "ebooklib>=0.18",
    "httpx>=0.28",
    "langchain>=1.3.14",
    "langchain-openai>=1.3.5",
    "langgraph>=1.2.9",
    "pydantic>=2.11.7",
    "pydantic-settings>=2.10.1",
    "rich>=14.1.0",
    "textual>=5.3.0",
    "tomli-w>=1.0.0",
]
```

- [ ] **Step 2: Install it into the worktree's venv**

Run: `uv sync`
Expected: resolves and installs `ebooklib` (and its `lxml` dependency) with no errors.

- [ ] **Step 3: Verify import**

Run: `uv run python -c "from ebooklib import epub; print(epub.EpubBook)"`
Expected: prints `<class 'ebooklib.epub.EpubBook'>` with no traceback.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add ebooklib dependency for epub export"
```

---

### Task 2: `build_epub()` — pure EPUB builder

**Files:**
- Create: `novelizer/export/__init__.py`
- Create: `novelizer/export/epub.py`
- Create: `tests/export/__init__.py`
- Create: `tests/export/test_epub.py`

**Interfaces:**
- Consumes: `novelizer.store.models.Chapter` (fields used: `id`, `title`, `prose`).
- Produces: `build_epub(chapters: list[Chapter], *, title: str, author: str) -> bytes`, raising `ValueError("no chapters to export")` when `chapters` is empty. This is the function `ExportScreen` (Task 3) calls.

- [ ] **Step 1: Create the empty package files**

```bash
touch novelizer/export/__init__.py tests/export/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `tests/export/test_epub.py`:

```python
import pytest
from ebooklib import epub
from novelizer.export.epub import build_epub
from novelizer.store.models import Chapter


def _chapters():
    return [
        Chapter(title="The Drowned Bell", prose="First line.\n\nSecond paragraph."),
        Chapter(title="Ashes at Dawn", prose="Only one paragraph here."),
    ]


def test_build_epub_raises_on_empty_chapter_list():
    with pytest.raises(ValueError):
        build_epub([], title="Empty Book", author="Nobody")


def test_build_epub_produces_readable_epub_with_all_chapters():
    data = build_epub(_chapters(), title="The Drowned Bell", author="A. Author")
    assert isinstance(data, bytes)
    assert data[:2] == b"PK"  # epub is a zip container


def test_build_epub_toc_matches_chapter_titles_in_order(tmp_path):
    data = build_epub(_chapters(), title="The Drowned Bell", author="A. Author")
    out = tmp_path / "book.epub"
    out.write_bytes(data)

    book = epub.read_epub(str(out))
    titles = [item.title for item in book.toc]
    assert titles == ["The Drowned Bell", "Ashes at Dawn"]

    docs = [
        item for item in book.get_items()
        if item.get_type() == epub.ITEM_DOCUMENT and item.file_name != "nav.xhtml"
    ]
    assert len(docs) == 2


def test_build_epub_splits_prose_into_paragraphs(tmp_path):
    data = build_epub(_chapters(), title="The Drowned Bell", author="A. Author")
    out = tmp_path / "book.epub"
    out.write_bytes(data)

    book = epub.read_epub(str(out))
    first_chapter = next(
        item for item in book.get_items()
        if item.get_type() == epub.ITEM_DOCUMENT and item.file_name == "chap_0.xhtml"
    )
    content = first_chapter.get_content().decode("utf-8")
    assert content.count("<p>") == 2
    assert "First line." in content
    assert "Second paragraph." in content
    assert "<h1>The Drowned Bell</h1>" in content


def test_build_epub_sets_title_and_author(tmp_path):
    data = build_epub(_chapters(), title="The Drowned Bell", author="A. Author")
    out = tmp_path / "book.epub"
    out.write_bytes(data)

    book = epub.read_epub(str(out))
    assert book.get_metadata("DC", "title")[0][0] == "The Drowned Bell"
    assert book.get_metadata("DC", "creator")[0][0] == "A. Author"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/export/test_epub.py -v`
Expected: `ModuleNotFoundError: No module named 'novelizer.export.epub'` (or collection error) on every test.

- [ ] **Step 4: Write `novelizer/export/epub.py`**

```python
from __future__ import annotations
from ebooklib import epub
from novelizer.store.models import Chapter


def _paragraphs(prose: str) -> str:
    blocks = [b.strip() for b in prose.split("\n\n") if b.strip()]
    return "".join(f"<p>{b}</p>" for b in blocks)


def build_epub(chapters: list[Chapter], *, title: str, author: str) -> bytes:
    if not chapters:
        raise ValueError("no chapters to export")

    book = epub.EpubBook()
    book.set_identifier(f"novelizer-{title}")
    book.set_title(title)
    book.set_language("en")
    book.add_author(author)

    epub_chapters = []
    for i, chapter in enumerate(chapters):
        item = epub.EpubHtml(
            title=chapter.title,
            file_name=f"chap_{i}.xhtml",
            lang="en",
        )
        item.content = f"<h1>{chapter.title}</h1>{_paragraphs(chapter.prose)}"
        book.add_item(item)
        epub_chapters.append(item)

    book.toc = tuple(epub_chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav"] + epub_chapters

    import tempfile
    import os

    fd, path = tempfile.mkstemp(suffix=".epub")
    os.close(fd)
    try:
        epub.write_epub(path, book)
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.remove(path)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/export/test_epub.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add novelizer/export tests/export
git commit -m "feat: add build_epub for assembling chapters into an EPUB"
```

---

### Task 3: `ExportScreen` modal

**Files:**
- Create: `novelizer/tui/export_screen.py`
- Create: `tests/tui/test_export_screen.py`

**Interfaces:**
- Consumes: `build_epub(chapters, *, title, author) -> bytes` (Task 2); `runtime.read.list_chapters(status: str | None) -> list[Chapter]` (existing `ReadStore`); `runtime.settings.story_title: str | None` and `runtime.settings.db_path: str` (existing `EffectiveSettings`); `novelizer.settings.discovery.slugify(name: str) -> str` (existing).
- Produces: `ExportScreen(runtime)` — a `ModalScreen` pushed via `app.push_screen(ExportScreen(app.runtime))`, consumed by Task 4. On successful export it writes a `.epub` file under `<story_root>/export/` and appends a result line to the app feed the same way `_run_command` does (`self.query_one("#feed", RichLog).write(...)` + `self.app.messages.append(...)`).

- [ ] **Step 1: Write the failing smoke test**

Create `tests/tui/test_export_screen.py`:

```python
import os
import tempfile
from pathlib import Path

import pytest
from novelizer.settings import EffectiveSettings as Settings
from novelizer.runtime import Runtime
from novelizer.store.models import Chapter, EditorialStatus
from novelizer.canon.events import EventType
from novelizer.tui.app import NovelizerApp
from novelizer.tui.export_screen import ExportScreen


async def _app_with_chapters():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    settings = Settings(db_path=path, projector_interval=0.1, story_title="The Drowned Bell")
    rt = Runtime(settings, runners={})
    await rt.events.init()
    await rt.projector.init()
    await rt.read.init()
    ch = Chapter(title="Ch One", prose="Some prose.", editorial_status=EditorialStatus.published)
    await rt.committer.commit("author", EventType.CHAPTER_CREATED, ch.id, ch)
    await rt.projector.catch_up()
    return NovelizerApp(rt), rt, path


@pytest.mark.asyncio
async def test_export_screen_writes_epub_and_reports_path():
    app, rt, db_path = await _app_with_chapters()
    async with app.run_test() as pilot:
        await app.push_screen(ExportScreen(rt))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ExportScreen)
        screen.title_value = "The Drowned Bell"
        screen.author_value = "A. Author"
        screen.status_value = "published"
        await screen.do_export()
        await pilot.pause()

    story_root = Path(db_path).parent
    export_dir = story_root / "export"
    files = list(export_dir.glob("*.epub"))
    assert len(files) == 1
    assert files[0].stat().st_size > 0
    await rt.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_export_screen.py -v`
Expected: `ModuleNotFoundError: No module named 'novelizer.tui.export_screen'`.

- [ ] **Step 3: Write `novelizer/tui/export_screen.py`**

```python
"""EPUB export as a modal drill-in, reachable only from the command
palette (AppCommand "export_epub" in app.py). Mirrors ApprovalScreen's
shape: a small form, a confirm action, escape to close."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from novelizer.export.epub import build_epub
from novelizer.settings.discovery import slugify


class ExportScreen(ModalScreen):
    """Prompts for title/author/status, then writes an .epub under
    <story_root>/export/. runtime.read must already be initialized
    (true for every screen pushed from the running app)."""

    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self.title_value = runtime.settings.story_title or Path(runtime.settings.db_path).parent.name
        self.author_value = ""
        self.status_value = "published"
        self._error: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="export_box") as box:
            box.border_title = "EXPORT EPUB"
            yield Input(value=self.title_value, placeholder="Title", id="export_title")
            yield Input(value=self.author_value, placeholder="Author", id="export_author")
            yield Select(
                [("Published only", "published"), ("All chapters", "all")],
                id="export_status",
                allow_blank=False,
                value=self.status_value,
            )
            yield Static("", id="export_error")
            yield Button("Export", id="export_confirm")

    def action_close(self) -> None:
        self.dismiss()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "export_title":
            self.title_value = event.value
        elif event.input.id == "export_author":
            self.author_value = event.value

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "export_status":
            self.status_value = event.value

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "export_confirm":
            await self.do_export()

    async def do_export(self) -> None:
        status = None if self.status_value == "all" else self.status_value
        chapters = await self.runtime.read.list_chapters(status=status)
        if not chapters:
            self._set_error(f"No chapters found for status={self.status_value!r}.")
            return

        try:
            data = build_epub(chapters, title=self.title_value, author=self.author_value)
        except ValueError as e:
            self._set_error(str(e))
            return

        story_root = Path(self.runtime.settings.db_path).parent
        export_dir = story_root / "export"
        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        slug = slugify(self.title_value)
        final_path = export_dir / f"{slug}-{self.status_value}-{stamp}.epub"
        tmp_path = final_path.with_suffix(".epub.tmp")
        try:
            tmp_path.write_bytes(data)
            tmp_path.rename(final_path)
        except OSError as e:
            self._set_error(f"write failed: {e}")
            return

        self.dismiss()
        message = f"» exported {len(chapters)} chapters → {final_path}"
        try:
            from textual.widgets import RichLog

            self.app.query_one("#feed", RichLog).write(message)
        except Exception:
            pass
        self.app.messages.append(message)

    def _set_error(self, text: str) -> None:
        self._error = text
        try:
            self.query_one("#export_error", Static).update(text)
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_export_screen.py -v`
Expected: `test_export_screen_writes_epub_and_reports_path` PASSES.

- [ ] **Step 5: Commit**

```bash
git add novelizer/tui/export_screen.py tests/tui/test_export_screen.py
git commit -m "feat: add ExportScreen modal for epub export"
```

---

### Task 4: Wire into the command palette

**Files:**
- Modify: `novelizer/tui/app.py`

**Interfaces:**
- Consumes: `ExportScreen` (Task 3), `AppCommand` (existing dataclass, `app.py` line ~36), `APP_COMMANDS` list (existing, `app.py` line ~500).
- Produces: command-palette entry `"export_epub"` reachable via `Ctrl+K`.

- [ ] **Step 1: Write the failing test**

Look at `tests/tui/test_app_commands.py` first to match its existing style:

```bash
sed -n '1,40p' tests/tui/test_app_commands.py
```

Add a test following that file's pattern — append to `tests/tui/test_app_commands.py`:

```python
def test_export_epub_command_is_registered():
    from novelizer.tui.app import APP_COMMANDS

    names = [c.name for c in APP_COMMANDS]
    assert "export_epub" in names
```

(If the existing file uses a different import/assert style — e.g. a shared `_names()` helper — match that instead of introducing a new pattern.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_app_commands.py::test_export_epub_command_is_registered -v`
Expected: FAIL — `"export_epub" not in names`.

- [ ] **Step 3: Add the import and the command**

In `novelizer/tui/app.py`, add to the imports near the top (alongside the existing `from novelizer.tui.approval_screen import ApprovalScreen` line):

```python
from novelizer.tui.export_screen import ExportScreen
```

Add a guard function next to `_app_open_settings` (same guard pattern as `_app_open_approvals` — never stack modals):

```python
def _app_open_export(app: NovelizerApp) -> None:
    if app.screen is not app.default_screen:
        return
    app.push_screen(ExportScreen(app.runtime))
```

Register it in `APP_COMMANDS` (the list currently ending with the `brain_tab_arcs` entry before its closing `]`):

```python
    AppCommand("export_epub", "Export EPUB", _app_open_export),
```

Insert it right after the `AppCommand("settings", "Open settings", _app_open_settings),` line, so the full relevant section reads:

```python
APP_COMMANDS: list[AppCommand] = [
    AppCommand("approvals", "Open the approvals screen", _app_open_approvals),
    AppCommand("toggle_room", "Toggle Room view", _app_toggle_room),
    AppCommand("toggle_engine", "Toggle Engine Room view", _app_toggle_engine),
    AppCommand("toggle_prompt", "Toggle the Engine Room prompt panel", _app_toggle_prompt),
    AppCommand("toggle_reading", "Toggle Reading view", _app_toggle_reading),
    AppCommand("settings", "Open settings", _app_open_settings),
    AppCommand("export_epub", "Export EPUB", _app_open_export),
    AppCommand("quit", "Quit Novelizer", _app_quit),
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_app_commands.py -v`
Expected: all tests in the file PASS, including the new one.

- [ ] **Step 5: Run the full export-related test slice**

Run: `uv run pytest tests/export tests/tui/test_export_screen.py tests/tui/test_app_commands.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add novelizer/tui/app.py tests/tui/test_app_commands.py
git commit -m "feat: wire epub export into the command palette"
```

---

## Self-Review Notes

- Spec coverage: pure builder (Task 2), modal form with title/author/status (Task 3), output path under `<story_root>/export/` with slug-status-timestamp naming (Task 3), command-palette-only access (Task 4), empty-chapter-list and write-failure error handling (Task 3), no-scene-break-detection / one-EPUB-chapter-per-Chapter (Task 2) — all covered. `.gitignore` change dropped: `stories/` is already fully ignored.
- No placeholders: every step has literal code or an exact command with expected output.
- Type/name consistency checked: `build_epub(chapters, *, title, author) -> bytes` used identically in Task 2 and Task 3; `ExportScreen(runtime)` constructor matches its use in Task 3's test and Task 4's `_app_open_export`.
