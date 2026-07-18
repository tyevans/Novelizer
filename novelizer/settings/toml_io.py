from __future__ import annotations

import os
import tomllib
from pathlib import Path

import tomli_w


class TOMLFileError(Exception):
    """A config file could not be read or parsed. Message names file and location."""


def load_toml_file(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise TOMLFileError(f"{path}: file not found") from None
    except tomllib.TOMLDecodeError as e:
        # tomllib messages include "at line N, column M"
        raise TOMLFileError(f"{path}: invalid TOML: {e}") from e


def write_toml_file(path: Path, data: dict, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")
    if mode is not None:
        os.chmod(path, mode)
