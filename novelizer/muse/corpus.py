from __future__ import annotations
import tomllib
from importlib import resources
from pydantic import BaseModel

_DATA_PACKAGE = "novelizer.muse.data"


class CorpusError(RuntimeError):
    """A bundled corpus file is missing, unparsable, or invalid. Raised at
    Muse construction (Runtime.start) so a bad corpus fails fast, never
    mid-novel."""


class Corpora(BaseModel):
    version: str
    given_names: dict[str, list[str]]
    surnames: list[str]
    professions: list[str]
    settings: list[str]
    beats: list[str]


def _load_toml(filename: str) -> dict:
    try:
        raw = resources.files(_DATA_PACKAGE).joinpath(filename).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, NotADirectoryError) as e:
        raise CorpusError(f"corpus file missing: {filename}") from e
    try:
        return tomllib.loads(raw)
    except tomllib.TOMLDecodeError as e:
        raise CorpusError(f"corpus file unparsable: {filename}: {e}") from e


def load_corpora() -> Corpora:
    given = _load_toml("given_names.toml")
    files = {
        "surnames": _load_toml("surnames.toml"),
        "professions": _load_toml("professions.toml"),
        "settings": _load_toml("settings.toml"),
        "beats": _load_toml("beats.toml"),
    }
    version = str(given.get("version", ""))
    if not version:
        raise CorpusError("given_names.toml must declare a version")
    for name, data in files.items():
        if str(data.get("version", "")) != version:
            raise CorpusError(f"{name}.toml version {data.get('version')!r} != given_names version {version!r}")
        if not data.get("entries"):
            raise CorpusError(f"{name}.toml has no entries")
    buckets = {k: v for k, v in given.items() if k != "version"}
    if not buckets or any(not names for names in buckets.values()):
        raise CorpusError("given_names.toml must have non-empty era buckets")
    return Corpora(
        version=version,
        given_names=buckets,
        surnames=files["surnames"]["entries"],
        professions=files["professions"]["entries"],
        settings=files["settings"]["entries"],
        beats=files["beats"]["entries"],
    )
