# EPUB Export — Design

## Purpose

Let a user export a story's chapters as a `.epub` file directly from the TUI, so they can read the manuscript on an e-reader without hand-assembling one.

## Scope

- Export chapters filtered by editorial status (`published` or `all`), in existing insertion order (`ReadStore.list_chapters`, `ORDER BY rowid`).
- Triggered from the TUI command palette (`Ctrl+K` → "Export EPUB"). No CLI command, no dedicated keybinding.
- Title/author prompted at export time via a modal form, title pre-filled from `story.toml`'s `title`.
- Output written to `stories/<story>/export/<slug>-<status>-<timestamp>.epub`.

Out of scope: scene-break marker detection/rendering, cover images, custom CSS/themes, CLI access, chapter reordering UI.

## Components

### 1. `novelizer/export/epub.py` (new module)

Pure builder, no store/runtime dependency:

```python
def build_epub(chapters: list[Chapter], *, title: str, author: str) -> bytes:
```

- Uses `ebooklib` (new dependency in `pyproject.toml`).
- One EPUB chapter (`EpubHtml`) per `Chapter` record, in list order (caller has already filtered/ordered).
- Chapter heading: `<h1>{chapter.title}</h1>`.
- Body: `chapter.prose` split on blank lines into `<p>` tags (no scene-break marker handling).
- Spine and TOC (NCX + nav) built from the same chapter list/order.
- Raises `ValueError` if `chapters` is empty — caller surfaces this as a user-facing error, no file is written.

### 2. `novelizer/tui/export_screen.py` (new file)

`ExportScreen(ModalScreen)`, following the existing `ApprovalScreen` pattern:

- Form: `Input` for title (pre-filled with `runtime.settings.story_title` or story folder name if unset), `Input` for author (blank default), `Select` for status (`published` / `all`, default `published`).
- Confirm button (and `escape` to cancel, matching `ApprovalScreen`'s binding style).
- On confirm:
  1. `chapters = await runtime.read.list_chapters(status="published" if ... else None)`
  2. If empty, show inline error in the modal (no dismiss, no file write).
  3. `data = build_epub(chapters, title=title, author=author)`
  4. Write to `<story_root>/export/<slug>-<status>-<timestamp>.epub` (create `export/` dir if missing; slug via existing `settings.discovery.slugify`; timestamp `YYYYMMDD-HHMMSS`).
  5. Dismiss the modal; push a result line ("Exported N chapters → <path>") into the app's feed via the existing `messages` mechanism, same channel `commands.dispatch` results use.

### 3. Command palette wiring (`novelizer/tui/app.py`)

- Add `AppCommand("export_epub", "Export EPUB", _app_export_epub)` to the existing `AppCommand` list feeding `NovelizerCommandProvider`.
- `_app_export_epub` pushes `ExportScreen(self.runtime)` via `self.push_screen`.

### 4. `.gitignore`

Add `stories/*/export/` so exported files aren't accidentally committed.

## Data flow

```
Ctrl+K → "Export EPUB" → ExportScreen (title/author/status form)
   → confirm → ReadStore.list_chapters(status)
   → build_epub(chapters, title, author) → bytes
   → write to stories/<story>/export/<slug>-<status>-<timestamp>.epub
   → feed message with resulting path
```

## Error handling

- Empty chapter list for the chosen status → inline modal error, no file written, modal stays open for the user to change status or cancel.
- Filesystem write failure (e.g. permissions) → inline modal error with the exception message; no partial file left behind (write to a temp path in the same dir, then rename on success).

## Testing

- Unit tests for `build_epub`: correct chapter count, TOC entries match chapter titles, paragraph splitting produces one `<p>` per blank-line-delimited block, raises on empty input.
- No test coverage for the TUI modal itself beyond a smoke-level check if the existing TUI test setup supports it (`docs/TESTING-TUI.md` conventions apply if so).
- Per project rule: **never run the test suite against the main checkout** — tests for this feature must run inside an isolated worktree (DB-lock incident precedent).

## Dependencies

- Add `ebooklib` to `pyproject.toml` `dependencies`.
