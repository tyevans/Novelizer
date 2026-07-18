from __future__ import annotations
import tomllib
from pathlib import Path
from novelizer.voices.models import ProseProfile, VoicePack


def load_voice_pack(path: str) -> VoicePack:
    """Load a voice pack from a TOML file on disk.

    Raises FileNotFoundError with a clear, pack-specific message if `path`
    does not exist, rather than letting a bare `open()` traceback surface.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Voice pack not found at '{path}'.")
    with p.open("rb") as f:
        data = tomllib.load(f)

    profiles = {
        key: ProseProfile(**profile_data)
        for key, profile_data in data.get("prose_profiles", {}).items()
    }
    return VoicePack(
        name=data["name"],
        prose_profiles=profiles,
        agent_personalities=data.get("agent_personalities", {}),
    )
