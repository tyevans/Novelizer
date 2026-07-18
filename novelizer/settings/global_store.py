from __future__ import annotations

from pathlib import Path

from novelizer.settings.layers import global_config_path
from novelizer.settings.toml_io import load_toml_file, write_toml_file


def write_global_config(data: dict, path: Path | None = None) -> Path:
    """Write the global config file. Always 0600: it may hold llm_api_key."""
    target = path if path is not None else global_config_path()
    write_toml_file(target, data, mode=0o600)
    return target


def update_global_config(path: Path | None = None, **changes) -> dict:
    """Read-modify-write single keys (e.g. last_opened_story). A value of None
    removes the key. Unknown keys already in the file are preserved."""
    target = path if path is not None else global_config_path()
    data = load_toml_file(target) if target.exists() else {}
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    write_global_config(data, path=target)
    return data
