from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

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


def create_story(root: Path, title: str) -> StoryDirectory:
    sd = StoryDirectory(root=root)
    root.mkdir(parents=True, exist_ok=True)
    write_toml_file(sd.story_toml, {"title": title})
    return sd


def migrate_flat_layout(stories_root: Path, story_name: str = "default") -> StoryDirectory:
    """Move a legacy flat layout (stories/world.db, stories/chroma) into a
    proper story directory (stories/<story_name>/)."""
    flat_db = stories_root / "world.db"
    if not flat_db.exists():
        raise FileNotFoundError(f"{flat_db}: no flat-layout story to migrate")
    sd = StoryDirectory(root=stories_root / story_name)
    sd.root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(flat_db), str(sd.db_path))
    flat_chroma = stories_root / "chroma"
    if flat_chroma.exists():
        shutil.move(str(flat_chroma), str(sd.chroma_path))
    write_toml_file(sd.story_toml, {"title": story_name})
    return sd
