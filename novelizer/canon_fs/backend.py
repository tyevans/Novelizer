from __future__ import annotations

from dataclasses import dataclass

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
from deepagents.backends.utils import create_file_data, slice_read_response

from novelizer.canon_fs.paths import build_path_index
from novelizer.canon_fs.render import (
    render_chapter, render_character, render_secret, render_theme,
    render_thread, render_world_entry,
)

READ_ONLY_ERROR = (
    "Canon is read-only. To change the story record, declare intents "
    "in your structured response."
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
