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

# Names that must never surface in an agent-visible `/skills/` listing:
# packaging artifacts (`__init__.py` makes the directory an importable
# package; `__pycache__/` is a bytecode cache), not skill content.
_HIDDEN_ENTRY_NAMES = {"__init__.py", "__pycache__"}

# The single shared skills source every tooled builder passes. deepagents'
# `SkillsMiddleware` treats each `sources` entry as a CONTAINER directory:
# it lists the source, keeps `is_dir` entries, and reads
# `<entry>/SKILL.md` for each. Our packs
# (novelizer/skills_packs/<pack>/SKILL.md) are subdirectories of one
# container, so the container itself -- `/skills` -- is the only valid
# source shape; passing an individual pack dir (e.g. `/skills/outlining`)
# makes the middleware probe one level too deep
# (`/skills/outlining/references/SKILL.md`) and load zero skills.
#
# Progressive disclosure is what makes "every agent gets the container"
# affordable: each agent's system prompt only pays for each pack's
# name + description line (a handful of tokens), not the full SKILL.md
# body -- that's only read on demand once the agent decides a skill is
# relevant. The middleware's container contract makes per-source-dir
# selectivity (e.g. giving Author only scene-sequel + pacing) impossible
# without duplicating pack data into per-agent container directories, so
# that selectivity has been dropped: every tooled agent now sees the name
# and description of all packs.
CRAFT_SKILLS = ["/skills"]


class ReadOnlyBackend(BackendProtocol):
    """Read-only wrapper around another `BackendProtocol`.

    `FilesystemBackend` is inherently writable -- it will happily create,
    edit, and delete files on disk. Skill packs are shipped, versioned
    reference material bundled with the application; an agent must never be
    able to mutate them (accidentally or otherwise). This wrapper delegates
    all reads -- including bulk reads via `download_files`/`adownload_files`,
    which `SkillsMiddleware` uses to fetch every candidate SKILL.md -- to
    `inner`, and refuses every write/edit/upload path with the same
    canonical message `CanonBackend` uses, so the refusal is indistinguishable
    to callers regardless of which read-only route they hit. Download is a
    bulk READ, not a write, and must not be refused.

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
        raise NotImplementedError("ReadOnlyBackend is async-only; use adownload_files")

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await self._inner.adownload_files(paths)

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
        result = await self._inner.als(path)
        return _filter_hidden_entries(result)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await self._inner.aread(file_path, offset, limit)

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        return await self._inner.agrep(pattern, path, glob)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        result = await self._inner.aglob(pattern, path)
        return _filter_hidden_glob_matches(result)


def _has_hidden_segment(entry_path: str) -> bool:
    """True if ANY path segment (not just the basename) is a hidden name.

    A basename-only check misses nested hits: `aglob("**/*", "/skills")`
    matches files *inside* `__pycache__/` too (e.g.
    `/skills/__pycache__/__init__.cpython-313.pyc`), whose basename is the
    `.pyc` filename, not `__pycache__`. Checking every segment catches
    those regardless of depth.
    """
    return any(seg in _HIDDEN_ENTRY_NAMES for seg in entry_path.strip("/").split("/"))


def _filter_hidden_entries(result: LsResult) -> LsResult:
    """Drop packaging artifacts (`__init__.py`, `__pycache__/`) from an
    `ls`/`als` listing -- they're an implementation detail of shipping
    skill packs as an importable Python package, not skill content, and
    `SkillsMiddleware` will otherwise probe `__pycache__/SKILL.md`."""
    if not result.entries:
        return result
    entries = [e for e in result.entries if not _has_hidden_segment(e["path"])]
    return LsResult(entries=entries, error=result.error)


def _filter_hidden_glob_matches(result: GlobResult) -> GlobResult:
    """Same filtering as `_filter_hidden_entries`, applied to glob matches."""
    if not result.matches:
        return result
    matches = [m for m in result.matches if not _has_hidden_segment(m["path"])]
    return GlobResult(matches=matches, error=result.error)


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
