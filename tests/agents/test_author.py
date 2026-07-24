import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, ThreadPlanted, ThemeIntroduced
from novelizer.agents.author import Author, ChapterDraft
from novelizer.agents.schemas import ThreadIntent, ThemeIntent, PromiseIntent
from novelizer.store.models import Chapter, DirectorSignal, SignalKind
from novelizer.brain.ledger import overdue_promises


class FakeRunner:
    def __init__(self, draft): self._draft = draft; self.calls = []
    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._draft}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_readiness_drops_with_draft_backlog(stack):
    events, proj, read, committer = stack
    for i in range(3):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()
    author = Author(FakeRunner(None), read, committer)
    assert await author.readiness() == 0.0


async def test_run_once_appends_and_projects_a_chapter(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="The Salt Road", prose="The road held its salt like a grudge.")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    assert "The Salt Road" in [c.title for c in await read.list_chapters()]


async def test_run_once_consumes_targeted_signals(stack):
    events, proj, read, committer = stack
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.seed, body="a storm is coming"))
    await proj.catch_up()
    author = Author(FakeRunner(ChapterDraft(title="T", prose="P")), read, committer)
    await author.run_once()
    await proj.catch_up()
    assert await read.list_unconsumed_signals(target_agent="author") == []


async def test_author_revise_signal_commits_chapter_revised_not_chapter_created(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="original"))
    await proj.catch_up()
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.revise, body="fix pacing",
                                        target_agent="author", target_entity="c1"))
    await proj.catch_up()
    draft = ChapterDraft(title="One", prose="fixed prose")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    chapters = await read.list_chapters()
    assert len(chapters) == 1  # chapter count unchanged
    revised = await read.get_chapter("c1")
    assert revised.prose == "fixed prose"


async def test_author_revise_signal_still_commits_thread_intents(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="original"))
    await proj.catch_up()
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.revise, body="fix pacing",
                                        target_agent="author", target_entity="c1"))
    await proj.catch_up()
    draft = ChapterDraft(
        title="One", prose="fixed prose",
        thread_intents=[ThreadIntent(action="plant", id="", name="A new thread")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    threads = await read.list_threads()
    assert len(threads) == 1
    assert threads[0].name == "A new thread"


async def test_author_revise_signal_is_consumed(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="original"))
    await proj.catch_up()
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.revise, body="fix pacing",
                                        target_agent="author", target_entity="c1"))
    await proj.catch_up()
    draft = ChapterDraft(title="One", prose="fixed prose")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    assert await read.list_unconsumed_signals(target_agent="author") == []


async def test_work_returns_none_is_noop(stack):
    events, proj, read, committer = stack
    author = Author(FakeRunner(None), read, committer)
    await author.run_once()
    await proj.catch_up()
    assert await read.list_chapters() == []


async def test_work_prompt_includes_casting_note_when_set(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    runner = FakeRunner(draft)
    author = Author(runner, read, committer, casting_note="Spare, concrete, unadorned.")
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Spare, concrete, unadorned." in sent
    assert "Write in this prose voice:" in sent


async def test_work_prompt_omits_casting_note_when_unset(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    runner = FakeRunner(draft)
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Write in this prose voice:" not in sent


async def test_two_profiles_yield_different_prompts(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    sparse_runner = FakeRunner(draft)
    lush_runner = FakeRunner(draft)
    sparse_author = Author(sparse_runner, read, committer, casting_note="Spare, concrete, unadorned.")
    lush_author = Author(lush_runner, read, committer, casting_note="Ornate, sensory, gothic.")
    ctx = await sparse_author.poll()
    await sparse_author.work(ctx)
    await lush_author.work(ctx)
    sparse_prompt = sparse_runner.calls[-1]["messages"][0]["content"]
    lush_prompt = lush_runner.calls[-1]["messages"][0]["content"]
    assert sparse_prompt != lush_prompt
    assert "Spare, concrete, unadorned." in sparse_prompt
    assert "Ornate, sensory, gothic." in lush_prompt


async def test_work_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    runner = FakeRunner(draft)
    author = Author(runner, read, committer, personality="A restless, romantic chronicler.")
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A restless, romantic chronicler." in sent
    assert "In character:" in sent


async def test_work_prompt_omits_personality_line_when_unset(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    runner = FakeRunner(draft)
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "In character:" not in sent


async def test_commit_emits_agent_remarked_when_feed_note_present(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P", feed_note="Another chapter, another heartbreak.")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["agent_name"] == "author"
    assert remarks[0].payload["note"] == "Another chapter, another heartbreak."


async def test_commit_emits_no_remark_when_feed_note_empty(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e for e in log if e.event_type == EventType.AGENT_REMARKED] == []


async def test_author_commit_plants_a_thread_from_structured_output(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(
        title="T", prose="P",
        thread_intents=[ThreadIntent(action="plant", name="The Locket")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    thread = await read.get_thread("the-locket")
    assert thread is not None and thread.name == "The Locket"


async def test_author_commit_touches_a_known_active_thread(stack):
    events, proj, read, committer = stack
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    draft = ChapterDraft(
        title="T", prose="P",
        thread_intents=[ThreadIntent(action="touch", id="the-locket", note="reappears")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    thread = await read.get_thread("the-locket")
    assert thread.touch_count == 1


async def test_author_commit_drops_touch_for_unknown_thread_id(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(
        title="T", prose="P",
        thread_intents=[ThreadIntent(action="touch", id="ghost-thread")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("thread.")] == []


async def test_author_commit_with_no_thread_intents_emits_no_thread_events(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("thread.")] == []


async def test_author_promise_with_window_surfaces_as_overdue_after_window_passes(stack):
    # Pins the M7 acceptance loop's first hop: a PromiseIntent's window
    # survives commit -> projection -> the ledger's overdue faculty.
    events, proj, read, committer = stack
    draft = ChapterDraft(
        title="One", prose="P",
        promise_intents=[
            PromiseIntent(action="make", name="The Sealed Letter", window_lo=1, window_hi=1),
        ],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    # Chapter 1 exists (window_hi=1), still within window: not overdue yet.
    promises = await read.list_promises()
    chapters = await read.list_chapters()
    assert overdue_promises(promises, chapters) == []
    # A second chapter passes; now = 2 > window_hi=1: overdue.
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await proj.catch_up()
    promises = await read.list_promises()
    chapters = await read.list_chapters()
    overdue = overdue_promises(promises, chapters)
    assert len(overdue) == 1
    assert overdue[0].name == "The Sealed Letter"


async def test_author_commits_theme_introduce_intent(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(
        title="T", prose="P",
        theme_intents=[ThemeIntent(action="introduce", title="Loss")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    theme = await read.get_theme("loss")
    assert theme is not None and theme.title == "Loss"


async def test_author_prompt_includes_stale_threads_note_when_present(stack):
    events, proj, read, committer = stack
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    for i in range(4):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Stale threads" in sent
    assert "The Locket" in sent and "the-locket" in sent


async def test_author_prompt_omits_stale_threads_note_when_nothing_stale(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Stale threads" not in sent


async def test_author_prompt_byte_identical_to_pre_m3_3_shape_when_brain_silent(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    expected = (
        "World lore:\nNone yet.\n\nCharacters:\nNone yet.\n\n"
        "Previous chapters:\nNone yet.\n\nDirector notes:\nNone.\n\n"
        "No outline exists yet — you are drafting ahead of the Plotter under a "
        "fallback. Keep this chapter provisional and exploratory; do not invent a "
        "chapter's worth of new threads/promises/secrets, and say so in your feed note.\n\n"
        "Write the next chapter."
    )
    assert sent == expected


async def test_m3_done_when_mechanical_chain_stale_thread_to_touched_to_not_stale(stack):
    """The M3 done-when, part (a): seed a thread stale enough that
    StalenessAnalyzer flags it -> assert the Author's built prompt names it
    (asserted on literal prompt text) -> drive the Author with a FakeRunner
    preset whose structured output declares a thread_intents entry touching
    that exact id -> assert the resulting thread.touched event lands via the
    Committer -> assert the Thread Board's render-time helper (brain_model.thread_line,
    via is_thread_stale) no longer reports the thread stale. No live model call."""
    from novelizer.canon.events import ThreadPlanted
    from novelizer.agents.schemas import ThreadIntent
    from novelizer.tui.widgets.brain_model import thread_line

    events, proj, read, committer = stack
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    for i in range(4):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()

    # Step 1: staleness is real before we touch anything.
    thread_before = await read.get_thread("the-locket")
    chapters = await read.list_chapters()
    assert "stale" in thread_line(thread_before, chapters).plain

    # Step 2: the Author's prompt names the stale thread by name and id.
    probe_runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    probe_author = Author(probe_runner, read, committer)
    ctx = await probe_author.poll()
    await probe_author.work(ctx)
    prompt = probe_runner.calls[-1]["messages"][0]["content"]
    assert "The Locket" in prompt and "the-locket" in prompt

    # Step 3: a scripted Author response declares a matching touch intent.
    draft = ChapterDraft(
        title="Chapter Five", prose="The locket surfaces again.",
        thread_intents=[ThreadIntent(action="touch", id="the-locket", note="resurfaces")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()

    # Step 4: the thread.touched event landed, and the thread is no longer stale.
    log = await events.events_since(0)
    assert any(e.event_type == EventType.THREAD_TOUCHED and e.payload["id"] == "the-locket" for e in log)
    thread_after = await read.get_thread("the-locket")
    assert thread_after.touch_count == 1
    chapters_after = await read.list_chapters()
    assert "stale" not in thread_line(thread_after, chapters_after).plain


from novelizer.agents.schemas import KnowledgeIntent, CausalIntent
from novelizer.canon.events import SecretCreated
from novelizer.store.models import Chapter


async def test_author_commit_plants_a_secret_from_structured_output(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(
        title="T", prose="P",
        knowledge_intents=[KnowledgeIntent(action="plant", title="The Heir Lives")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    secret = await read.get_secret("the-heir-lives")
    assert secret is not None and secret.title == "The Heir Lives"


async def test_author_commit_uses_a_known_active_secret(stack):
    events, proj, read, committer = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await proj.catch_up()
    draft = ChapterDraft(
        title="T", prose="P",
        knowledge_intents=[KnowledgeIntent(action="uses", id="the-heir-lives", character_id="mara")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    refs = await read.list_secret_references(secret_id="the-heir-lives")
    assert len(refs) == 1 and refs[0].character_id == "mara"


async def test_author_commit_drops_causal_edge_citing_unknown_chapter(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    draft = ChapterDraft(
        title="Two", prose="P",
        causal_intents=[CausalIntent(cause_chapter_id="c1", effect_chapter_id="ghost")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    assert await read.list_causal_edges() == []


async def test_author_commit_declares_a_valid_causal_edge_between_prior_chapters(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await proj.catch_up()
    draft = ChapterDraft(
        title="Three", prose="P",
        causal_intents=[CausalIntent(cause_chapter_id="c1", effect_chapter_id="c2", note="sets it up")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    edges = await read.list_causal_edges()
    assert len(edges) == 1
    assert edges[0].cause_chapter_id == "c1" and edges[0].effect_chapter_id == "c2"


async def test_author_commit_with_no_knowledge_or_causal_intents_emits_no_new_event_types(stack):
    events, proj, read, committer = stack
    draft = ChapterDraft(title="T", prose="P")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith(("secret.", "causal_edge."))] == []


from novelizer.canon.events import SecretCreated, SecretLearned


async def test_author_prompt_includes_known_secrets_note_when_present(stack):
    events, proj, read, committer = stack
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await events.append(EventType.CHARACTER_CREATED, "mara", __import__("novelizer.store.models", fromlist=["Character"]).Character(id="mara", name="Mara"))
    await events.append(EventType.SECRET_LEARNED, "the-heir-lives", SecretLearned(id="the-heir-lives", character_id="mara"))
    await proj.catch_up()
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Secrets and who knows them" in sent
    assert "the-heir-lives" in sent and "Mara" in sent


async def test_author_prompt_omits_known_secrets_note_when_no_secrets(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Secrets and who knows them" not in sent


async def test_author_prompt_byte_identical_to_pre_m4_3_shape_when_brain_silent(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    expected = (
        "World lore:\nNone yet.\n\nCharacters:\nNone yet.\n\n"
        "Previous chapters:\nNone yet.\n\nDirector notes:\nNone.\n\n"
        "No outline exists yet — you are drafting ahead of the Plotter under a "
        "fallback. Keep this chapter provisional and exploratory; do not invent a "
        "chapter's worth of new threads/promises/secrets, and say so in your feed note.\n\n"
        "Write the next chapter."
    )
    assert sent == expected


def test_summarize_uses_summary_when_available_over_prose():
    from novelizer.agents.author import _summarize

    # The window governs the chapters BEHIND the newest one; the chapter being
    # continued from is pushed in full so its ending is visible.
    ctx = {
        "world": [], "characters": [],
        "previous": [Chapter(id="c1", title="T", prose="x" * 500), Chapter(id="c2", title="Newest", prose="p")],
        "chapters": [], "signals": [], "threads": [], "secrets": [], "knowledge_matrix": {},
        "themes": [], "causal_edges": [],
    }
    out = _summarize(ctx, summaries={"c1": "A brisk recap of chapter one."})
    assert "A brisk recap of chapter one." in out
    assert "x" * 50 not in out


def test_summarize_falls_back_to_labeled_elision_when_no_summary():
    from novelizer.agents.author import _summarize
    from novelizer.brain.context_assembly import ELISION_MARKER

    ctx = {
        "world": [], "characters": [],
        "previous": [Chapter(id="c1", title="T", prose="x" * 5000), Chapter(id="c2", title="Newest", prose="p")],
        "chapters": [], "signals": [], "threads": [], "secrets": [], "knowledge_matrix": {},
        "themes": [], "causal_edges": [],
    }
    out = _summarize(ctx, advisory_budget=50)
    assert ELISION_MARKER in out


async def test_author_constructor_threads_advisory_token_budget_through(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="T", prose="x" * 5000))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Newest", prose="p"))
    await proj.catch_up()
    from novelizer.brain.context_assembly import ELISION_MARKER

    runner = FakeRunner(ChapterDraft(title="T2", prose="P"))
    author = Author(runner, read, committer, advisory_token_budget=10)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert ELISION_MARKER in sent
    assert "x" * 5000 not in sent


async def test_author_constructor_threads_staleness_threshold_through(stack):
    events, proj, read, committer = stack
    for i in range(3):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer, staleness_threshold_chapters=1)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Stale threads" in sent and "the-locket" in sent


from novelizer.canon.events import CausalEdgeDeclared


def test_summarize_omits_causal_flags_block_when_no_edges():
    from novelizer.agents.author import _summarize

    ctx = {
        "world": [], "characters": [], "previous": [], "chapters": [], "signals": [],
        "threads": [], "secrets": [], "knowledge_matrix": {}, "themes": [], "causal_edges": [],
    }
    out = _summarize(ctx)
    assert "Causal flags:" not in out


async def test_author_prompt_includes_causal_flags_when_edges_flagged(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c1",
                        CausalEdgeDeclared(cause_chapter_id="c2", effect_chapter_id="c1"))
    await proj.catch_up()
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Causal flags:" in sent
    assert "ch002 -> ch001" in sent and "ordering" in sent


async def test_author_prompt_byte_identical_to_pre_causal_shape_when_no_edges(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    expected = (
        "World lore:\nNone yet.\n\nCharacters:\nNone yet.\n\n"
        "Previous chapters:\nNone yet.\n\nDirector notes:\nNone.\n\n"
        "No outline exists yet — you are drafting ahead of the Plotter under a "
        "fallback. Keep this chapter provisional and exploratory; do not invent a "
        "chapter's worth of new threads/promises/secrets, and say so in your feed note.\n\n"
        "Write the next chapter."
    )
    assert sent == expected


async def test_author_pull_mode_false_keeps_previous_chapters_prose_block(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer, pull_mode=False)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Previous chapters:" in sent
    assert "Chapter index:" not in sent


async def test_author_pull_mode_true_replaces_prose_with_chapter_map(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="secret prose text"))
    await proj.catch_up()
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer, pull_mode=True)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Chapter index:" in sent
    assert "Previous chapters:" not in sent
    assert "- ch001 'One' (draft) cast: none [id:c1]" in sent
    assert "secret prose text" not in sent


def test_build_author_runner_without_backend_stays_constructible():
    from novelizer.agents.author import build_author_runner

    class FakeSettings:
        author_model = "gpt-4o-mini"
        llm_base_url = None
        llm_api_key = "test-key"
        author_temperature = 0.7
        llm_max_tokens = None

    runner = build_author_runner(FakeSettings())
    assert runner is not None


def test_build_author_runner_with_canon_backend_builds():
    from novelizer.agents.author import build_author_runner
    from novelizer.canon_fs.backend import CanonBackend

    class FakeSettings:
        author_model = "gpt-4o-mini"
        llm_base_url = None
        llm_api_key = "test-key"
        author_temperature = 0.7
        llm_max_tokens = None

    backend = CanonBackend(read_store=None)
    runner = build_author_runner(FakeSettings(), backend=backend, tools=[])
    assert runner is not None


def test_build_author_runner_with_backend_bounds_recursion(monkeypatch):
    """Fix 3: pull-mode runners must cap the tool loop -- an unbounded graph
    can spin forever chasing tool calls."""
    from novelizer.agents.author import build_author_runner
    from novelizer.canon_fs.backend import CanonBackend

    class FakeSettings:
        author_model = "gpt-4o-mini"
        llm_base_url = None
        llm_api_key = "test-key"
        author_temperature = 0.7
        llm_max_tokens = None

    backend = CanonBackend(read_store=None)
    runner = build_author_runner(FakeSettings(), backend=backend, tools=[])
    assert runner.config.get("recursion_limit") == 100


def test_build_author_runner_binds_callbacks_at_graph_scope_not_model():
    """Fix 1: telemetry callbacks must be bound on the graph (via with_config)
    so ToolNode executions under invoke-time config actually see them --
    constructor callbacks on the chat model never reach on_tool_start/end."""
    from novelizer.agents.author import build_author_runner
    from novelizer.canon_fs.backend import CanonBackend
    from langchain_core.callbacks.base import BaseCallbackHandler

    class FakeSettings:
        author_model = "gpt-4o-mini"
        llm_base_url = None
        llm_api_key = "test-key"
        author_temperature = 0.7
        llm_max_tokens = None

    handler = BaseCallbackHandler()
    backend = CanonBackend(read_store=None)
    runner = build_author_runner(FakeSettings(), callbacks=[handler], backend=backend, tools=[])
    assert handler in (runner.config.get("callbacks") or [])


def test_build_author_runner_tooled_branch_passes_author_skills(monkeypatch):
    from novelizer.agents import author as author_mod
    from novelizer.canon_fs.backend import CanonBackend

    class FakeSettings:
        author_model = "gpt-4o-mini"
        llm_base_url = None
        llm_api_key = "test-key"
        author_temperature = 0.7
        llm_max_tokens = None

    captured = {}

    class FakeGraph:
        def with_config(self, config):
            return self

    def fake_create_deep_agent(*, model, system_prompt, response_format, backend=None, tools=None, skills=None, subagents=None, middleware=None):
        captured["skills"] = skills
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    backend = CanonBackend(read_store=None)
    author_mod.build_author_runner(FakeSettings(), backend=backend, tools=[])
    from novelizer.canon_fs.skills_route import CRAFT_SKILLS
    assert captured["skills"] == CRAFT_SKILLS
    assert captured["skills"] == ["/skills"]


def test_build_author_runner_bare_branch_carries_no_skills_kwarg(monkeypatch):
    from novelizer.agents import author as author_mod

    class FakeSettings:
        author_model = "gpt-4o-mini"
        llm_base_url = None
        llm_api_key = "test-key"
        author_temperature = 0.7
        llm_max_tokens = None

    captured = {}

    class FakeGraph:
        pass

    def fake_create_deep_agent(*, model, system_prompt, response_format):
        captured["called"] = True
        return FakeGraph()

    import deepagents
    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create_deep_agent)

    author_mod.build_author_runner(FakeSettings())
    assert captured["called"]


def test_retrieval_note_base_split():
    """Author re-exports the shared notes; the map variant is the base variant
    plus one index sentence. Wording is pinned in tests/agents/test_prompts.py."""
    from novelizer.agents.author import RETRIEVAL_NOTE, RETRIEVAL_NOTE_BASE
    from novelizer.agents import prompts

    assert RETRIEVAL_NOTE is prompts.RETRIEVAL_NOTE
    assert RETRIEVAL_NOTE_BASE is prompts.RETRIEVAL_NOTE_BASE
    assert "chapter list below" not in RETRIEVAL_NOTE_BASE
    assert "chapter list below" in RETRIEVAL_NOTE
    prefix = prompts._RETRIEVAL_NOTE_PREFIX
    suffix = prompts._RETRIEVAL_NOTE_SUFFIX
    assert RETRIEVAL_NOTE_BASE == prefix + suffix
    assert RETRIEVAL_NOTE == prefix + prompts._RETRIEVAL_NOTE_MAP_SENTENCE + suffix


async def test_author_commits_promise_intents_with_validation(stack):
    events, proj, read, committer = stack
    from novelizer.agents.schemas import PromiseIntent
    draft = ChapterDraft(
        title="T", prose="P",
        promise_intents=[
            PromiseIntent(action="make", name="The Sealed Letter", kind="plant"),
            PromiseIntent(action="pay", id="never-made"),
        ],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    promises = await read.list_promises()
    assert [p.id for p in promises] == ["the-sealed-letter"]
    assert promises[0].state.value == "open"


async def test_author_flag_commits_flag_with_craft_category(stack):
    events, proj, read, committer = stack
    from novelizer.store.models import FlagStatus
    from novelizer.agents.schemas import FlagDraft
    draft = ChapterDraft(
        title="T", prose="P",
        flags=[FlagDraft(category="craft", description="brief conflicts with Mara's voice card",
                          proposed_resolution="Plotter should revise the brief")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    open_flags = await read.list_flags(category="craft", status=FlagStatus.open)
    assert len(open_flags) == 1
    assert open_flags[0].filed_by == "author"
    assert "voice card" in open_flags[0].description


async def test_author_no_flags_commits_no_extra_flag(stack):
    events, proj, read, committer = stack
    from novelizer.store.models import FlagStatus
    draft = ChapterDraft(title="T", prose="P")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    open_flags = await read.list_flags(category="craft", status=FlagStatus.open)
    assert open_flags == []


async def test_author_system_prompt_mentions_promises():
    from novelizer.agents.author import AUTHOR_SYSTEM_PROMPT
    assert "promise" in AUTHOR_SYSTEM_PROMPT.lower()


async def test_author_prompt_includes_ledger_note_when_promise_overdue(stack):
    from novelizer.canon.events import PromiseMade

    events, proj, read, committer = stack
    await events.append(EventType.PROMISE_MADE, "the-sealed-letter",
                        PromiseMade(id="the-sealed-letter", name="The Sealed Letter", window_hi=1))
    for i in range(2):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[0]["messages"][0]["content"]
    assert "Promise ledger" in sent
    assert "The Sealed Letter" in sent and "the-sealed-letter" in sent


async def test_author_prompt_omits_ledger_note_when_no_promises(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[0]["messages"][0]["content"]
    assert "Promise ledger" not in sent


async def test_author_prompt_includes_chapter_brief_when_open_for_next_ordinal(stack):
    from novelizer.canon.events import ChapterBriefDrafted

    events, proj, read, committer = stack
    await events.append(
        EventType.CHAPTER_BRIEF_DRAFTED, "b1",
        ChapterBriefDrafted(brief_id="b1", target_ordinal=1, goal="Introduce the locket"),
    )
    await proj.catch_up()
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[0]["messages"][0]["content"]
    assert "Chapter brief" in sent
    assert "Introduce the locket" in sent


async def test_author_run_once_fulfills_open_brief_on_new_chapter(stack):
    from novelizer.canon.events import ChapterBriefDrafted

    events, proj, read, committer = stack
    await events.append(
        EventType.CHAPTER_BRIEF_DRAFTED, "b1",
        ChapterBriefDrafted(brief_id="b1", target_ordinal=1, goal="Introduce the locket"),
    )
    await proj.catch_up()
    draft = ChapterDraft(title="The Locket", prose="It gleamed.")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    briefs = await read.list_briefs()
    assert len(briefs) == 1
    assert briefs[0].status.value == "fulfilled"
    chapters = await read.list_chapters()
    assert briefs[0].fulfilled_by_chapter_id == chapters[0].id


async def test_author_prompt_omits_chapter_brief_when_none_open(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[0]["messages"][0]["content"]
    assert "Chapter brief" not in sent


async def test_author_revise_signal_with_open_brief_does_not_consume_it(stack):
    from novelizer.canon.events import ChapterBriefDrafted

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="original"))
    await proj.catch_up()
    await events.append(
        EventType.CHAPTER_BRIEF_DRAFTED, "b1",
        ChapterBriefDrafted(brief_id="b1", target_ordinal=2, goal="Reveal the letter"),
    )
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.revise, body="fix pacing",
                                        target_agent="author", target_entity="c1"))
    await proj.catch_up()
    draft = ChapterDraft(title="One", prose="fixed prose")
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()
    briefs = await read.list_briefs()
    assert len(briefs) == 1
    assert briefs[0].status.value == "open"


def test_author_module_does_not_import_arc_note():
    import novelizer.agents.author as author_module
    import inspect

    source = inspect.getsource(author_module)
    assert "arc_note" not in source


def test_spec_carries_subagent_grant():
    from novelizer.agents.author import SPEC
    assert SPEC.subagent_grant.enabled_setting == "author_subagent_enabled"


async def test_push_mode_recap_uses_summary_when_available(stack):
    from novelizer.canon.events import ChapterSummarized

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="x" * 5000))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Newest", prose="latest prose"))
    await events.append(
        EventType.CHAPTER_SUMMARIZED, "c1",
        ChapterSummarized(chapter_id="c1", gist="ch1 gist", summary="A concise recap of what chapter one did."),
    )
    await proj.catch_up()
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer, pull_mode=False)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A concise recap of what chapter one did." in sent
    assert "x" * 200 not in sent


async def test_push_mode_recap_labels_missing_summary(stack):
    from novelizer.brain.context_assembly import ELISION_MARKER

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="x" * 20000))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Newest", prose="latest prose"))
    await proj.catch_up()
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    author = Author(runner, read, committer, pull_mode=False)
    ctx = await author.poll()
    await author.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert ELISION_MARKER in sent
