from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from novelizer.settings.layers import StoryConfigError
from novelizer.settings.models import STORY_OVERRIDABLE_KEYS
from novelizer.settings.toml_io import write_toml_file


@dataclass(frozen=True)
class StoryDirectory:
    """A self-contained story folder. All storage paths derive from `root`."""

    root: Path

    @property
    def db_path(self) -> Path:
        return self.root / "world.db"

    @property
    def chroma_path(self) -> Path:
        return self.root / "chroma"

    @property
    def story_toml(self) -> Path:
        return self.root / "story.toml"


def is_story_dir(path: Path) -> bool:
    return (path / "story.toml").exists() or (path / "world.db").exists()


def create_story(
    root: Path, title: str, overrides: dict[str, object] | None = None
) -> StoryDirectory:
    """Create a story directory with a story.toml. `overrides` are optional
    story-scoped settings (validated against STORY_OVERRIDABLE_KEYS) written
    alongside the title; validation runs before mkdir so a bad call leaves
    no half-created directory."""
    data: dict[str, object] = {"title": title}
    if overrides:
        unknown = sorted(set(overrides) - STORY_OVERRIDABLE_KEYS)
        if unknown:
            raise StoryConfigError(
                f"{root / 'story.toml'}: {unknown} are not story-overridable settings"
            )
        data.update(overrides)
    sd = StoryDirectory(root=root)
    root.mkdir(parents=True, exist_ok=True)
    write_toml_file(sd.story_toml, data)
    return sd


def migrate_flat_layout(stories_root: Path, story_name: str = "default") -> StoryDirectory:
    """Move a legacy flat layout (stories/world.db, stories/chroma) into a
    proper story directory (stories/<story_name>/)."""
    flat_db = stories_root / "world.db"
    if not flat_db.exists():
        raise FileNotFoundError(f"{flat_db}: no flat-layout story to migrate")
    sd = StoryDirectory(root=stories_root / story_name)
    if sd.db_path.exists():
        raise FileExistsError(
            f"Cannot migrate {flat_db} into {sd.db_path}: a story already exists there."
        )
    flat_chroma = stories_root / "chroma"
    if sd.chroma_path.exists():
        raise FileExistsError(
            f"Cannot migrate {flat_chroma} into {sd.chroma_path}: a story already exists there."
        )
    sd.root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(flat_db), str(sd.db_path))
    if flat_chroma.exists():
        shutil.move(str(flat_chroma), str(sd.chroma_path))
    if not sd.story_toml.exists():
        write_toml_file(sd.story_toml, {"title": story_name})
    return sd
