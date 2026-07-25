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
from wcmatch import glob as wcglob

from novelizer.brain.irony import build_irony_ledger
from novelizer.canon_fs.generation import GenerationCache
from novelizer.canon_fs.paths import build_path_index
from novelizer.canon_fs.reads import sliced_read
from novelizer.canon_fs.render import (
    render_chapter, render_character, render_irony_ledger, render_secret,
    render_theme, render_thread, render_world_entry,
)

READ_ONLY_ERROR = (
    "Canon is read-only. To change the story record, declare intents "
    "in your structured response."
)

KIND_DIRS = ("chapters", "characters", "world", "threads", "secrets", "themes")

# The dramatic-irony ledger (novelizer/brain/irony.py) is derived wholly from
# secret canon, so it lives in /secrets beside its source rather than claiming a
# seventh top-level directory it would be the only occupant of -- a new root
# would read as a new canon aggregate, which it is not.
#
# The leading underscore is what makes it collision-proof: novelizer.slug's rule
# trims non-alphanumerics from both ends, so no secret title can ever slug to a
# name starting with "_" and take this path first. It also sorts ahead of every
# secret file, so an agent that ls-es /secrets meets the ledger before the rows
# it summarizes.
IRONY_LEDGER_PATH = "/secrets/_dramatic-irony.md"
_IRONY_LEDGER_KIND = "irony_ledger"


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
    irony: list


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
        self._snapshot_cache = GenerationCache(read_store, self._build_snapshot)

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
        """The consistent view every call in this generation shares.

        Cached on the projection generation rather than rebuilt per call: the
        seven queries plus the path index cost 0.8ms on an idle copy of the live
        story but show a 100ms median and 1.3s p90 in its telemetry, where the
        projector is writing and thirteen agents are reading the same file.
        """
        return await self._snapshot_cache.get()

    async def _build_snapshot(self) -> _Snapshot:
        chapters = await self._read.list_chapters()
        characters = await self._read.list_characters()
        world = await self._read.list_world_entries()
        threads = await self._read.list_threads()
        secrets = await self._read.list_secrets()
        themes = await self._read.list_themes()
        matrix = await self._read.knowledge_matrix()
        irony = build_irony_ledger(
            secrets=secrets,
            references=await self._read.list_secret_references(),
            knowledge=await self._read.list_secret_knowledge(),
            chapters=chapters,
            matrix=matrix,
        )
        index = build_path_index(chapters, characters, world, threads, secrets, themes)
        # Added here rather than inside build_path_index: the ledger is a
        # singleton derived file with no record id, and the index is also built
        # by canon_fs/search.py purely to map record ids to paths.
        index[IRONY_LEDGER_PATH] = (_IRONY_LEDGER_KIND, "")
        return _Snapshot(
            index=index,
            chapters={r.id: r for r in chapters},
            characters={r.id: r for r in characters},
            world={r.id: r for r in world},
            threads={r.id: r for r in threads},
            secrets={r.id: r for r in secrets},
            themes={r.id: r for r in themes},
            matrix=matrix,
            irony=irony,
        )

    def _render(self, snap: _Snapshot, kind: str, record_id: str) -> str:
        if kind == _IRONY_LEDGER_KIND:
            return render_irony_ledger(snap.irony, list(snap.chapters.values()))
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
        return sliced_read(
            self._render(snap, kind, record_id),
            offset=offset, limit=limit,
        )

    async def als(self, path: str) -> LsResult:
        snap = await self._snapshot()
        norm = "/" + path.strip("/")
        if norm == "/":
            return LsResult(
                entries=[FileInfo(path=f"/{d}", is_dir=True) for d in KIND_DIRS]
            )
        if norm in snap.index:
            return LsResult(error=f"'{path}' is a file, not a directory. Read it instead.")
        if norm.lstrip("/") not in KIND_DIRS:
            valid = ", ".join(f"/{d}" for d in KIND_DIRS)
            return LsResult(error=f"Directory '{path}' not found. Top-level directories: {valid}")
        prefix = norm + "/"
        return LsResult(entries=[
            FileInfo(path=p, is_dir=False) for p in sorted(snap.index) if p.startswith(prefix)
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
            for p in sorted(snap.index)
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
        norm = "/" + (path or "").strip("/")
        matches: list[GrepMatch] = []

        if norm in snap.index:
            candidates = [norm] if self._glob_ok(norm, glob) else []
        else:
            prefix = "/" if norm == "/" else norm + "/"
            candidates = [
                p for p in sorted(snap.index)
                if p.startswith(prefix) and self._glob_ok(p, glob)
            ]

        for p in candidates:
            kind, record_id = snap.index[p]
            for line_no, text in enumerate(
                self._render(snap, kind, record_id).splitlines(), start=1
            ):
                if pattern in text:
                    matches.append(GrepMatch(path=p, line=line_no, text=text))
        return GrepResult(matches=matches)
