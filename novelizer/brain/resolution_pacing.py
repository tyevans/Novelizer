from novelizer.canon.threads import TERMINAL_STATES
from novelizer.store.models import Chapter, SecretRecord, ThreadRecord


def overdue_resolutions(threads: list[ThreadRecord], chapters: list[Chapter]) -> list[ThreadRecord]:
    now = len(chapters)
    return [
        t for t in threads
        if t.state.value not in TERMINAL_STATES and t.window_hi > 0 and now > t.window_hi
    ]


def overdue_reveals(secrets: list[SecretRecord], chapters: list[Chapter]) -> list[SecretRecord]:
    now = len(chapters)
    return [
        s for s in secrets
        if not s.revealed and s.reveal_window_hi > 0 and now > s.reveal_window_hi
    ]


def congested_windows(
    threads: list[ThreadRecord], secrets: list[SecretRecord], max_per_window: int = 2,
) -> list[tuple[int, int, int]]:
    """Merge every set window (non-terminal threads + unrevealed secrets)
    into overlapping spans; report spans holding more than max_per_window."""
    windows = [
        (t.window_lo, t.window_hi) for t in threads
        if t.state.value not in TERMINAL_STATES and t.window_hi > 0
    ] + [
        (s.reveal_window_lo, s.reveal_window_hi) for s in secrets
        if not s.revealed and s.reveal_window_hi > 0
    ]
    if not windows:
        return []
    windows.sort()
    spans: list[tuple[int, int, int]] = []
    lo, hi, count = *windows[0], 1
    for w_lo, w_hi in windows[1:]:
        if w_lo <= hi:
            hi, count = max(hi, w_hi), count + 1
        else:
            spans.append((lo, hi, count))
            lo, hi, count = w_lo, w_hi, 1
    spans.append((lo, hi, count))
    return [s for s in spans if s[2] > max_per_window]
