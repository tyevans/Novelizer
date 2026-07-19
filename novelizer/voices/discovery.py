from __future__ import annotations

from pathlib import Path

from novelizer.voices.loader import load_voice_pack

_SHIPPED_DEFAULT = Path(__file__).parent / "default.toml"


def discover_voice_packs(stories_root: Path) -> list[tuple[str, str]]:
    """(label, path) pairs for the voice-pack picker: the shipped default pack
    first, then any *.toml files directly under `stories_root` (the
    `voice-scaffold` convention — its default output is stories/user_pack.toml),
    sorted by filename. Files that don't parse as voice packs are skipped;
    story directories' own story.toml files live in subdirectories and are
    never scanned."""
    shipped = load_voice_pack(str(_SHIPPED_DEFAULT))
    packs: list[tuple[str, str]] = [(shipped.name, str(_SHIPPED_DEFAULT))]
    if stories_root.is_dir():
        for p in sorted(stories_root.glob("*.toml")):
            try:
                pack = load_voice_pack(str(p))
            except Exception:
                continue
            packs.append((f"{pack.name} ({p.name})", str(p)))
    return packs
