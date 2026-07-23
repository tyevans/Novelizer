"""Reads that announce their own truncation.

deepagents' `read_file` tool defaults to `limit=100` lines, and the backend
slice it calls returns that window with nothing appended -- so an agent that
reads a 208-line chapter receives 100 lines that stop mid-scene and no signal
that anything is missing. Four of the five chapters in the first full novel
run were longer than that default, and the Author and Character Keeper prompts
both instruct "read the chapter IN FULL": the agent believes it complied.

This is the failure mode `search.py`'s result cap already guards against -- a
silently-cut result reads as complete, and the agent stops looking for what it
never saw. Every canon backend routes its slicing through `sliced_read` so the
guard holds for chapters, outline files, and skill docs alike.
"""
from __future__ import annotations

from deepagents.backends.protocol import ReadResult
from deepagents.backends.utils import create_file_data, slice_read_response

# What an agent should pass to read a canon file whole. Matches the backends'
# own `aread` default, which is what the tool used before the middleware
# started defaulting the parameter for the model.
FULL_READ_LIMIT = 2000

# Stable substring the notice always contains -- tests and any future
# post-processing match on this rather than on the full sentence.
TRUNCATION_MARKER = "TRUNCATED"


def _notice(start: int, end: int, total: int) -> str:
    """One line, self-labelled as tooling output. The Author agent copies what
    it reads into prose, so the notice has to be unmistakably not-story.

    It deliberately does NOT quote the file's path. `CompositeBackend` strips
    the route prefix before delegating, so a routed backend sees
    `/outlining/SKILL.md` where the agent asked for `/skills/outlining/SKILL.md`
    -- echoing that back would hand the agent a path that does not resolve,
    which is the path-hallucination failure the retrieval note already fights.
    """
    return (
        f"[SYSTEM NOTICE — tool output, not file content] {TRUNCATION_MARKER}: "
        f"you were shown lines {start}-{end} of {total}. "
        f"{total - end} lines were NOT returned and the text above stops "
        f"mid-file — do not treat it as the whole file. read_file returns only "
        f"the first 100 lines unless you pass `limit`. To continue, call "
        f"read_file again on this same path with "
        f"offset={end}, limit={FULL_READ_LIMIT}."
    )


def sliced_read(content: str, *, offset: int, limit: int) -> ReadResult:
    """Slice `content` to the requested window and append a truncation notice
    when lines were withheld. Offset-past-EOF errors pass through from
    deepagents unchanged, so the backends' error surface is unaffected."""
    sliced = slice_read_response(create_file_data(content), offset, limit)
    if isinstance(sliced, ReadResult):  # offset past EOF
        return sliced

    total = len(content.splitlines())
    end = min(offset + limit, total)
    if end < total:
        # `format_content_with_line_numbers` downstream numbers every line it
        # is handed, the notice included; keeping it to a single trailing line
        # keeps that numbering honest for the content above it.
        separator = "" if sliced.endswith("\n") else "\n"
        sliced = f"{sliced}{separator}\n{_notice(offset + 1, end, total)}\n"
    return ReadResult(file_data=create_file_data(sliced))
