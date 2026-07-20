from __future__ import annotations

import importlib.resources

from deepagents.backends.filesystem import FilesystemBackend
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

from novelizer.canon_fs.backend import READ_ONLY_ERROR


class ReadOnlyBackend(BackendProtocol):
    """Read-only wrapper around another `BackendProtocol`.

    `FilesystemBackend` is inherently writable -- it will happily create,
    edit, and delete files on disk. Skill packs are shipped, versioned
    reference material bundled with the application; an agent must never be
    able to mutate them (accidentally or otherwise). This wrapper delegates
    all reads to `inner` and refuses every write/edit/upload/download path
    with the same canonical message `CanonBackend` uses, so the refusal is
    indistinguishable to callers regardless of which read-only route they
    hit.

    Sync mirrors raise `NotImplementedError`, matching `CanonBackend`'s
    convention: agents run via `ainvoke`, so only the async methods are
    supported paths.
    """

    def __init__(self, inner: BackendProtocol) -> None:
        self._inner = inner

    # -- writes: refused --

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=READ_ONLY_ERROR)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=READ_ONLY_ERROR)

    def edit(
        self, file_path: str, old_string: str, new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error=READ_ONLY_ERROR)

    async def aedit(
        self, file_path: str, old_string: str, new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error=READ_ONLY_ERROR)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=p, error="permission_denied") for p, _ in files]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self.upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [FileDownloadResponse(path=p, error="permission_denied") for p in paths]

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self.download_files(paths)

    # -- reads: async-only (agents run via ainvoke; sync names the way) --

    def ls(self, path: str) -> LsResult:
        raise NotImplementedError("ReadOnlyBackend is async-only; use als")

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        raise NotImplementedError("ReadOnlyBackend is async-only; use aread")

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        raise NotImplementedError("ReadOnlyBackend is async-only; use agrep")

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        raise NotImplementedError("ReadOnlyBackend is async-only; use aglob")

    async def als(self, path: str) -> LsResult:
        return await self._inner.als(path)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await self._inner.aread(file_path, offset, limit)

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        return await self._inner.agrep(pattern, path, glob)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await self._inner.aglob(pattern, path)


def build_skills_backend() -> ReadOnlyBackend:
    """Build the read-only backend for the `/skills/` composite route.

    Wraps a `FilesystemBackend` rooted at the packaged `novelizer.skills_packs`
    directory in `virtual_mode=True` (the safest read configuration: it
    anchors all paths to the skills-pack root, blocks `..`/`~` traversal, and
    rejects absolute paths that would otherwise escape the root) and in
    `ReadOnlyBackend` so writes are refused even though `FilesystemBackend`
    itself is writable.
    """
    root_dir = str(importlib.resources.files("novelizer.skills_packs"))
    inner = FilesystemBackend(root_dir=root_dir, virtual_mode=True)
    return ReadOnlyBackend(inner)
