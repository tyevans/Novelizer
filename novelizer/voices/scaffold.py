from __future__ import annotations
import os
from pathlib import Path
import tomllib
from novelizer.voices.models import VoicePack

DEFAULT_PACK_GUARD_MESSAGE = (
    "Refusing to scaffold into the shipped default voice pack; "
    "pass a separate user pack path instead."
)


def _toml_escape(s: str) -> str:
    """Escape a string for a TOML basic string ("...").

    Handles the two characters that must be escaped inside a TOML basic
    string: backslash and double-quote. Newlines are also escaped so the
    written value stays a single-line basic string regardless of input.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def scaffold_prose_profile(pack_path: str, profile_name: str, description: str) -> str:
    """Write (creating or updating) a `[prose_profiles.<profile_name>]` TOML block
    into the pack at `pack_path`, using stdlib `tomllib` to read the existing pack
    (if any) and hand-written TOML text to write it back — no LLM call, no new
    TOML-writing dependency (see plan Global Constraints for that rationale).

    Refuses to write to a path whose basename is `default.toml` under the
    shipped `novelizer/voices/` package directory, to protect the shipped pack
    from being clobbered by scaffolding aimed at a user pack.
    """
    shipped_default = Path(__file__).parent / "default.toml"
    if Path(pack_path).resolve() == shipped_default.resolve():
        raise ValueError(DEFAULT_PACK_GUARD_MESSAGE)

    p = Path(pack_path)
    if p.is_file():
        with p.open("rb") as f:
            data = tomllib.load(f)
    else:
        data = {"name": p.stem, "prose_profiles": {}, "agent_personalities": {}}

    data.setdefault("prose_profiles", {})
    data["prose_profiles"][profile_name] = {"name": profile_name, "casting_note": description}

    _write_pack_toml(p, data)
    return str(p)


def _write_pack_toml(path: Path, data: dict) -> None:
    """Hand-write the known VoicePack TOML shape: a top-level `name`, a
    `[prose_profiles.<key>]` table per profile (each with `name`/`casting_note`
    string keys), and a `[agent_personalities]` table of string values.

    This is deliberately not a general-purpose TOML serializer — VoicePack's
    shape is small and fixed (see novelizer/voices/models.py), so hand-writing
    it avoids adding a TOML-writing dependency for one call site.
    """
    lines = [f'name = "{_toml_escape(data.get("name", path.stem))}"', ""]
    for key, profile in data.get("prose_profiles", {}).items():
        lines.append(f"[prose_profiles.{key}]")
        lines.append(f'name = "{_toml_escape(profile["name"])}"')
        lines.append(f'casting_note = "{_toml_escape(profile["casting_note"])}"')
        lines.append("")
    personalities = data.get("agent_personalities", {})
    if personalities:
        lines.append("[agent_personalities]")
        for agent, note in personalities.items():
            lines.append(f'{agent} = "{_toml_escape(note)}"')
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
