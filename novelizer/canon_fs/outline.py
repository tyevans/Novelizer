from __future__ import annotations

from dataclasses import dataclass

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import create_file_data, slice_read_response
from wcmatch import glob as wcglob

from novelizer.canon_fs.backend import READ_ONLY_ERROR
from novelizer.canon_fs.outline_render import (
    render_beats, render_blueprint, render_brief, render_ledger,
    render_threads_plan,
)
from novelizer.canon_fs.paths import slugify

TOP_LEVEL_FILES = ("/blueprint.md", "/beats.md", "/threads-plan.md", "/ledger.md")


@dataclass
class _Snapshot:
    """One consistent view of the outline for a single backend call."""

    blueprint: object | None
    beats: list
    chapters: list
    threads: list
    promises: list
    briefs: dict[str, tuple]  # path -> ChapterBriefRecord


def _brief_path(brief) -> str:
    return f"/briefs/{brief.target_ordinal:03d}-{slugify(brief.goal)}.md"


class OutlineBackend(BackendProtocol):
    """Read-only virtual filesystem over the story outline (blueprint, beats,
    threads plan, ledger, open chapter briefs). Root-relative: the composite
    routes /outline/* here with the prefix stripped.

    Never errors on ls even with no blueprint adopted -- the fixed top-level
    files always exist, rendering a "No blueprint adopted." body.
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
        raise NotImplementedError("OutlineBackend is async-only; use als")

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        raise NotImplementedError("OutlineBackend is async-only; use aread")

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        raise NotImplementedError("OutlineBackend is async-only; use agrep")

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        raise NotImplementedError("OutlineBackend is async-only; use aglob")

    async def _snapshot(self) -> _Snapshot:
        blueprint = await self._read.get_active_blueprint()
        beats = await self._read.list_beats()
        chapters = await self._read.list_chapters()
        threads = await self._read.list_threads()
        promises = await self._read.list_promises()
        open_briefs = await self._read.list_briefs(status="open")
        briefs = {_brief_path(b): b for b in open_briefs}
        return _Snapshot(
            blueprint=blueprint, beats=beats, chapters=chapters,
            threads=threads, promises=promises, briefs=briefs,
        )

    def _render(self, snap: _Snapshot, path: str) -> str | None:
        if path == "/blueprint.md":
            return render_blueprint(snap.blueprint, snap.beats)
        if path == "/beats.md":
            return render_beats(snap.blueprint, snap.beats, snap.chapters)
        if path == "/threads-plan.md":
            return render_threads_plan(snap.threads, snap.chapters)
        if path == "/ledger.md":
            return render_ledger(snap.promises, snap.chapters)
        if path in snap.briefs:
            return render_brief(snap.briefs[path])
        return None

    def _all_paths(self, snap: _Snapshot) -> list[str]:
        return list(TOP_LEVEL_FILES) + sorted(snap.briefs)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        snap = await self._snapshot()
        content = self._render(snap, file_path)
        if content is None:
            return ReadResult(
                error=f"File '{file_path}' not found. Hint: ls the parent directory."
            )
        file_data = create_file_data(content)
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(file_data=create_file_data(sliced))

    async def als(self, path: str) -> LsResult:
        snap = await self._snapshot()
        norm = "/" + path.strip("/")
        if norm == "/":
            entries = [FileInfo(path=p, is_dir=False) for p in TOP_LEVEL_FILES]
            entries.append(FileInfo(path="/briefs", is_dir=True))
            return LsResult(entries=entries)
        if norm in TOP_LEVEL_FILES or norm in snap.briefs:
            return LsResult(error=f"'{path}' is a file, not a directory. Read it instead.")
        if norm != "/briefs":
            return LsResult(
                error=f"Directory '{path}' not found. Top-level directories: /briefs"
            )
        return LsResult(entries=[
            FileInfo(path=p, is_dir=False) for p in sorted(snap.briefs)
        ])

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        snap = await self._snapshot()
        if pattern.startswith("/"):
            full = pattern.lstrip("/")
        else:
            base = (path or "/").strip("/")
            full = f"{base}/{pattern}" if base else pattern
        matches = [
            FileInfo(path=p, is_dir=False)
            for p in sorted(self._all_paths(snap))
            if wcglob.globmatch(
                p.lstrip("/"), full, flags=wcglob.BRACE | wcglob.GLOBSTAR
            )
        ]
        return GlobResult(matches=matches)

    def _glob_ok(self, p: str, glob: str | None) -> bool:
        if not glob:
            return True
        target = p.rsplit("/", 1)[-1] if "/" not in glob else p.lstrip("/")
        return wcglob.globmatch(target, glob, flags=wcglob.BRACE | wcglob.GLOBSTAR)

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        snap = await self._snapshot()
        all_paths = self._all_paths(snap)
        norm = "/" + (path or "").strip("/")
        matches: list[GrepMatch] = []

        if norm in all_paths:
            candidates = [norm] if self._glob_ok(norm, glob) else []
        else:
            prefix = "/" if norm == "/" else norm + "/"
            candidates = [
                p for p in sorted(all_paths)
                if p.startswith(prefix) and self._glob_ok(p, glob)
            ]

        for p in candidates:
            content = self._render(snap, p)
            if content is None:
                continue
            for line_no, text in enumerate(content.splitlines(), start=1):
                if pattern in text:
                    matches.append(GrepMatch(path=p, line=line_no, text=text))
        return GrepResult(matches=matches)
