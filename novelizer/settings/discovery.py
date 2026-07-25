from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from novelizer.settings.story_dir import StoryDirectory, is_story_dir
from novelizer.settings.toml_io import TOMLFileError, load_toml_file
from novelizer.slug import slugify as _slugify


@dataclass(frozen=True)
class StoryMeta:
    root: Path
    title: str
    mtime: float


def list_stories(stories_dir: Path) -> list[StoryMeta]:
    if not stories_dir.is_dir():
        return []
    stories: list[StoryMeta] = []
    for child in sorted(stories_dir.iterdir()):
        if not child.is_dir() or not is_story_dir(child):
            continue
        sd = StoryDirectory(root=child)
        title = child.name
        if sd.story_toml.exists():
            try:
                title = load_toml_file(sd.story_toml).get("title") or child.name
            except TOMLFileError:
                pass  # unreadable story.toml: fall back to the directory name
        mtime = sd.db_path.stat().st_mtime if sd.db_path.exists() else child.stat().st_mtime
        stories.append(StoryMeta(root=child, title=title, mtime=mtime))
    return stories


def order_stories(stories: list[StoryMeta], last_opened: str | None) -> list[StoryMeta]:
    """Last-opened story first, remainder most-recently-written first."""
    front = [s for s in stories if last_opened and str(s.root) == last_opened]
    rest = sorted(
        (s for s in stories if s not in front),
        key=lambda s: s.mtime,
        reverse=True,
    )
    return front + rest


def slugify(name: str) -> str:
    """A story title as a directory name; never empty ("story" fallback)."""
    return _slugify(name, "story")
