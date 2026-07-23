from __future__ import annotations

from pathlib import Path

_DOC_SUFFIXES = {".md", ".txt"}


class CorpusReader:
    """Filesystem document corpus: a directory of .md/.txt files. The posix
    relative path of each file is its source_id — stable, human-readable,
    and exactly what claim.proposed payloads carry."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def list_documents(self) -> list[str]:
        docs: list[str] = []
        for path in sorted(self._root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _DOC_SUFFIXES:
                continue
            rel = path.relative_to(self._root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            docs.append(rel.as_posix())
        return docs

    def read_document(self, source_id: str) -> str:
        return (self._root / source_id).read_text(encoding="utf-8")
