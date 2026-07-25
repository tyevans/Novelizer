from novelizer.agents.author import Author
from novelizer.agents.base import ChapterDraft
from novelizer.canon.events import EventType


class _SpyCommitter:
    def __init__(self):
        self.commits = []

    async def commit(self, agent_name, event_type, aggregate_id, payload):
        self.commits.append((agent_name, event_type, payload))
        return None


class _NullRunner:
    async def ainvoke(self, inputs):
        raise AssertionError("not used")


def _empty_ctx() -> dict:
    return {"threads": [], "secrets": [], "chapters": [], "signals": [], "themes": [], "promises": [],
            "characters": []}


async def test_commit_stamps_provenance():
    committer = _SpyCommitter()
    provenance = {
        "model": "m-big",
        "temperature": 0.8,
        "voice_pack": "default",
        "prose_profile": "plain",
    }
    author = Author(_NullRunner(), read_store=None, committer=committer, provenance=provenance)
    await author.commit(ChapterDraft(title="T", prose="P"), _empty_ctx())
    chapter_commits = [c for c in committer.commits if c[1] == EventType.CHAPTER_CREATED]
    assert len(chapter_commits) == 1
    assert chapter_commits[0][2].provenance == provenance


async def test_commit_without_provenance_is_none():
    committer = _SpyCommitter()
    author = Author(_NullRunner(), read_store=None, committer=committer)
    await author.commit(ChapterDraft(title="T", prose="P"), _empty_ctx())
    chapter = [c for c in committer.commits if c[1] == EventType.CHAPTER_CREATED][0][2]
    assert chapter.provenance is None
