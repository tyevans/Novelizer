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
