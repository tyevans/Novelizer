from __future__ import annotations
import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_secret_name(title: str) -> str:
    """Turn a freeform secret title into a stable, id-safe slug.

    Lowercases, collapses runs of non-alphanumeric characters into single
    hyphens, and strips leading/trailing hyphens. Called exactly once, at
    secret.created time, to mint a secret's aggregate_id — mirrors
    novelizer.canon.threads.slugify_thread_name exactly (see M4.1's Locked
    decision #1: same rule as threads, reused rather than reinvented).
    """
    slug = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return slug or "secret"


def knowledge_cell_state(matrix: dict[str, dict], secret_id: str, character_id: str) -> str:
    """Derive one (secret, character) cell's state from the matrix returned
    by ReadStore.knowledge_matrix(): "unknown" | "known" | "revealed".

    `revealed` is secret-level state (Locked decision #2) — it is derived
    here for *any* character_id, including one created after the secret was
    revealed, rather than being looked up from a per-cell write. This keeps
    the matrix a simple two-flag structure (`revealed: bool`,
    `known_by: set[str]`) instead of fanning `revealed` out to every known
    character at event-application time, which would silently miss
    characters that didn't exist yet when the reveal happened.
    """
    entry = matrix.get(secret_id)
    if entry is None:
        return "unknown"
    if entry["revealed"]:
        return "revealed"
    return "known" if character_id in entry["known_by"] else "unknown"
