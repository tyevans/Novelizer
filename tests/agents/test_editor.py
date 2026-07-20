import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, ThreadPlanted, AnnotationStructureScored, SecretCreated, ThemeIntroduced
from novelizer.agents.editor import Editor
from novelizer.agents.schemas import EditorVerdict, ThreadIntent, KnowledgeIntent, CausalIntent, ThemeIntent, VoiceDriftFlag
from novelizer.agents.editor import VOICE_SOURCE_TAG
from novelizer.store.models import Chapter, EditorialStatus, Character, RetconStatus


class FakeRunner:
    def __init__(self, out): self._out = out; self.calls = []
    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_readiness_scales_with_drafts(stack):
    events, proj, read, committer = stack
    for i in range(3):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await proj.catch_up()
    assert await Editor(FakeRunner(None), read, committer).readiness() == 1.0


async def test_approve_promotes_to_reviewed(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    agent = Editor(FakeRunner(EditorVerdict(verdict="approve", notes="clean")), read, committer)
    await agent.run_once()
    await proj.catch_up()
    ch = await read.get_chapter("c1")
    assert ch.editorial_status == EditorialStatus.reviewed and ch.editor_notes == "clean"


async def test_revise_keeps_draft_and_notes_author(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    agent = Editor(FakeRunner(EditorVerdict(verdict="revise", notes="middle sags")), read, committer)
    await agent.run_once()
    await proj.catch_up()
    assert (await read.get_chapter("c1")).editorial_status == EditorialStatus.draft
    notes = await read.list_unconsumed_signals(target_agent="author")
    assert any("middle sags" in s.body for s in notes)


async def test_editor_revise_verdict_commits_revise_signal_with_target_entity(stack):
    from novelizer.store.models import SignalKind
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    agent = Editor(FakeRunner(EditorVerdict(verdict="revise", notes="fix pacing")), read, committer)
    await agent.run_once()
    await proj.catch_up()
    signals = await read.list_unconsumed_signals(target_agent="author")
    assert len(signals) == 1
    sig = signals[0]
    assert sig.kind == SignalKind.revise
    assert sig.target_agent == "author"
    assert sig.target_entity == "c1"


async def test_editor_prompt_includes_active_prose_profile(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()

    class RecordingRunner:
        def __init__(self, out):
            self._out = out
            self.calls = []

        async def ainvoke(self, inputs):
            self.calls.append(inputs)
            return {"structured_response": self._out}

    runner = RecordingRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer, casting_note="Spare, concrete, unadorned.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Spare, concrete, unadorned." in sent
    assert "Enforce this prose voice:" in sent


async def test_editor_prompt_includes_personality_when_set(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer, personality="A precise, unsentimental line editor.")
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "A precise, unsentimental line editor." in sent
    assert "In character:" in sent


async def test_editor_commit_emits_remark_on_approval(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="approve", notes="clean", feed_note="Finally, a clean draft.")
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["note"] == "Finally, a clean draft."


async def test_editor_commit_emits_remark_on_revision(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="revise", notes="middle sags", feed_note="This needs more tension.")
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    remarks = [e for e in log if e.event_type == EventType.AGENT_REMARKED]
    assert len(remarks) == 1
    assert remarks[0].payload["note"] == "This needs more tension."


async def test_editor_prompt_includes_character_voices_when_present(stack):
    events, proj, read, committer = stack
    await events.append(
        EventType.CHARACTER_CREATED, "ch1",
        Character(id="ch1", name="Mira", voice="Speaks in short, clipped sentences; never says 'I love you' outright."),
    )
    await events.append(
        EventType.CHAPTER_CREATED, "c1",
        Chapter(id="c1", title="One", prose="p", character_ids=["ch1"]),
    )
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Mira" in sent
    assert "Speaks in short, clipped sentences" in sent
    assert "Character voices:" in sent


async def test_editor_prompt_omits_voices_section_when_none_set(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHARACTER_CREATED, "ch1", Character(id="ch1", name="Mira"))
    await events.append(
        EventType.CHAPTER_CREATED, "c1",
        Chapter(id="c1", title="One", prose="p", character_ids=["ch1"]),
    )
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Character voices:" not in sent
    assert sent == f"Chapter title: One\n\nProse:\np"


async def test_editor_commit_touches_a_known_active_thread(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.THREAD_PLANTED, "the-locket", ThreadPlanted(id="the-locket", name="The Locket"))
    await proj.catch_up()
    verdict = EditorVerdict(
        verdict="approve", notes="clean",
        thread_intents=[ThreadIntent(action="touch", id="the-locket", note="resurfaces")],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    thread = await read.get_thread("the-locket")
    assert thread.touch_count == 1
    assert thread.last_chapter_id == "c1"


async def test_editor_commit_drops_pay_off_for_unknown_thread_id(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="approve", notes="clean", thread_intents=[ThreadIntent(action="pay_off", id="ghost")])
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("thread.")] == []


async def test_editor_commits_theme_develop_intent(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.THEME_INTRODUCED, "loss", ThemeIntroduced(id="loss", title="Loss"))
    await proj.catch_up()
    verdict = EditorVerdict(
        verdict="approve", notes="clean",
        theme_intents=[ThemeIntent(action="develop", id="loss")],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    theme = await read.get_theme("loss")
    assert theme.touch_count == 1


async def test_editor_commit_with_no_thread_intents_emits_no_thread_events(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="approve", notes="clean")
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("thread.")] == []


async def test_editor_prompt_includes_pacing_flags_note_when_present(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.9, pacing_label="climax"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c2",
                        AnnotationStructureScored(chapter_id="c2", tension=0.1, pacing_label="flat"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c3",
                        AnnotationStructureScored(chapter_id="c3", tension=0.85, pacing_label="climax"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Pacing flags" in sent
    assert "c2" in sent and "sag" in sent


async def test_editor_prompt_omits_pacing_flags_note_when_none_flagged(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Pacing flags" not in sent
    assert sent == f"Chapter title: One\n\nProse:\np"


async def test_editor_prompt_byte_identical_to_pre_m3_3_shape_when_brain_silent(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert sent == "Chapter title: One\n\nProse:\np"


async def test_editor_commit_uses_a_known_active_secret(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.SECRET_CREATED, "the-heir-lives", SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await proj.catch_up()
    verdict = EditorVerdict(
        verdict="approve", notes="clean",
        knowledge_intents=[KnowledgeIntent(action="uses", id="the-heir-lives", character_id="mara")],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    refs = await read.list_secret_references(secret_id="the-heir-lives")
    assert len(refs) == 1 and refs[0].chapter_id == "c1"


async def test_editor_commit_declares_a_valid_causal_edge(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c0", Chapter(id="c0", title="Zero", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(
        verdict="approve", notes="clean",
        causal_intents=[CausalIntent(cause_chapter_id="c0", effect_chapter_id="c1", note="sets up the reveal")],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    edges = await read.list_causal_edges()
    assert len(edges) == 1 and edges[0].cause_chapter_id == "c0" and edges[0].effect_chapter_id == "c1"


async def test_editor_commit_drops_unknown_secret_id(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(
        verdict="approve", notes="clean",
        knowledge_intents=[KnowledgeIntent(action="reveal", id="ghost-secret")],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith("secret.")] == []


async def test_editor_commit_with_no_knowledge_or_causal_intents_emits_no_new_event_types(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="approve", notes="clean")
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    log = await events.events_since(0)
    assert [e.event_type for e in log if e.event_type.startswith(("secret.", "causal_edge."))] == []


from novelizer.canon.events import CausalEdgeDeclared


async def test_editor_prompt_includes_beat_drift_note_when_present(stack):
    from novelizer.canon.events import BlueprintAdopted

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    for i in range(2, 10):
        await events.append(EventType.CHAPTER_CREATED, f"c{i}", Chapter(id=f"c{i}", title=str(i), prose="p"))
    await events.append(
        EventType.BLUEPRINT_ADOPTED, "bp1",
        BlueprintAdopted(
            blueprint_id="bp1", framework="six-position", target_chapter_count=10,
            beats=[
                {
                    "beat_id": "bp1-midpoint", "slug": "midpoint", "name": "Midpoint",
                    "ideal_pct": 0.5, "tolerance_pct": 0.1, "expected_polarity": "flip",
                },
            ],
        ),
    )
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Beat drift:" in sent
    assert "Midpoint" in sent


async def test_editor_prompt_includes_causal_flags_note_when_present(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.CHAPTER_CREATED, "c2", Chapter(id="c2", title="Two", prose="p"))
    await events.append(EventType.CAUSAL_EDGE_DECLARED, "c1",
                        CausalEdgeDeclared(cause_chapter_id="c2", effect_chapter_id="c1"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Causal flags" in sent
    assert "c2" in sent and "c1" in sent and "ordering" in sent


async def test_editor_prompt_omits_causal_flags_note_when_no_edges(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Causal flags" not in sent
    assert sent == f"Chapter title: One\n\nProse:\np"


async def test_editor_prompt_byte_identical_to_pre_m4_3_shape_when_brain_silent(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert sent == "Chapter title: One\n\nProse:\np"


async def test_editor_prompt_lists_active_secret_ids_for_citation(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.SECRET_CREATED, "the-heir-lives",
                        SecretCreated(id="the-heir-lives", title="The Heir Lives"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Active secrets you may cite by id" in sent
    assert "- the-heir-lives ('The Heir Lives')" in sent


async def test_editor_voice_drift_flag_commits_tagged_retcon(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(
        verdict="approve",
        notes="clean",
        voice_drift_flags=[
            VoiceDriftFlag(
                character_id="mara",
                line="I dunno, whatever.",
                trait_violated="formal, clipped diction",
                note="drops into casual slang",
            )
        ],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    tagged = [r for r in open_reqs if r.description.startswith(VOICE_SOURCE_TAG)]
    assert len(tagged) == 1
    assert "formal, clipped diction" in tagged[0].description


async def test_editor_voice_drift_flag_cites_character_in_conflicting_entry_ids(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(
        verdict="approve",
        notes="clean",
        voice_drift_flags=[
            VoiceDriftFlag(
                character_id="mara",
                line="I dunno, whatever.",
                trait_violated="formal, clipped diction",
                note="drops into casual slang",
            )
        ],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    tagged = [r for r in open_reqs if r.description.startswith(VOICE_SOURCE_TAG)]
    assert tagged[0].conflicting_entry_ids == ["mara"]


async def test_editor_no_voice_drift_flags_commits_no_extra_retcon(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    agent = Editor(FakeRunner(EditorVerdict(verdict="approve", notes="clean")), read, committer)
    await agent.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    tagged = [r for r in open_reqs if r.description.startswith(VOICE_SOURCE_TAG)]
    assert tagged == []


async def test_voice_drift_dedup_survives_reworded_trait(stack):
    # Regression: the live Editor rephrased trait_violated (and note) on every
    # pass over the same unrevised draft, so dedup-by-description never matched
    # and ~6 real complaints stacked into ~20 open retcons. The dedup key must
    # be the stable (character, line) pair, not the LLM's wording.
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    first = EditorVerdict(verdict="revise", notes="drifting", voice_drift_flags=[
        VoiceDriftFlag(character_id="the-boy", line="suspended in amber light",
                       trait_violated="ordinary vocabulary", note="poetic padding"),
    ])
    await Editor(FakeRunner(first), read, committer).run_once()
    await proj.catch_up()
    reworded = EditorVerdict(verdict="revise", notes="still drifting", voice_drift_flags=[
        VoiceDriftFlag(character_id="the-boy", line="suspended in amber light",
                       trait_violated="functional description only", note="atmospheric decoration"),
    ])
    await Editor(FakeRunner(reworded), read, committer).run_once()
    await proj.catch_up()
    tagged = [r for r in await read.list_retcon_requests(status=RetconStatus.open)
              if r.description.startswith(VOICE_SOURCE_TAG)]
    assert len(tagged) == 1


async def test_voice_drift_dedup_within_a_single_verdict(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="approve", notes="clean", voice_drift_flags=[
        VoiceDriftFlag(character_id="the-boy", line="suspended in amber light",
                       trait_violated="ordinary vocabulary"),
        VoiceDriftFlag(character_id="the-boy", line="suspended in amber light",
                       trait_violated="no stylistic thumbprint"),
    ])
    await Editor(FakeRunner(verdict), read, committer).run_once()
    await proj.catch_up()
    tagged = [r for r in await read.list_retcon_requests(status=RetconStatus.open)
              if r.description.startswith(VOICE_SOURCE_TAG)]
    assert len(tagged) == 1


async def test_voice_drift_distinct_lines_and_characters_all_filed(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(verdict="approve", notes="clean", voice_drift_flags=[
        VoiceDriftFlag(character_id="the-boy", line="suspended in amber light", trait_violated="t"),
        VoiceDriftFlag(character_id="the-boy", line="a secret anchor", trait_violated="t"),
        VoiceDriftFlag(character_id="mara", line="suspended in amber light", trait_violated="t"),
    ])
    await Editor(FakeRunner(verdict), read, committer).run_once()
    await proj.catch_up()
    tagged = [r for r in await read.list_retcon_requests(status=RetconStatus.open)
              if r.description.startswith(VOICE_SOURCE_TAG)]
    assert len(tagged) == 3


async def test_m5_2_done_when_mechanical_chain_themes(stack):
    """M5.2 done-when (a), theme half, traced clause by clause -- see
    docs/submilestones/M5-finish.md's M5.2 done-when cell and
    docs/superpowers/plans/2026-07-18-novelizer-m5.2-themes-voice.md Task 9."""
    from novelizer.agents.author import Author, ChapterDraft
    from novelizer.tui.widgets.browser_model import browser_sections

    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()

    # --- Clause 1a: a declared theme_intents entry (action="introduce") via
    # Author commits a theme.introduced event.
    draft = ChapterDraft(
        title="Two", prose="P",
        theme_intents=[ThemeIntent(action="introduce", title="Loss")],
    )
    author = Author(FakeRunner(draft), read, committer)
    await author.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    introduced = [e for e in log if e.event_type == EventType.THEME_INTRODUCED]
    assert len(introduced) == 1 and introduced[0].payload["title"] == "Loss"

    # --- Clause 2: list_themes() after catch_up() reflects it.
    themes = await read.list_themes()
    assert any(t.id == "loss" and t.title == "Loss" for t in themes)

    # --- Clause 3: a subsequent theme_intents entry (action="develop")
    # citing that id commits theme.developed and increments touch_count.
    verdict = EditorVerdict(
        verdict="approve", notes="clean",
        theme_intents=[ThemeIntent(action="develop", id="loss")],
    )
    editor = Editor(FakeRunner(verdict), read, committer)
    await editor.run_once()
    await proj.catch_up()

    log = await events.events_since(0)
    developed = [e for e in log if e.event_type == EventType.THEME_DEVELOPED]
    assert len(developed) == 1 and developed[0].payload["id"] == "loss"
    theme = await read.get_theme("loss")
    assert theme.touch_count == 1

    # --- Clause 4: monotonic-appending -- no event type reset touch_count
    # or removed the record; the theme is still present with its
    # accumulated touch_count, and both prior events remain in the log.
    assert theme is not None
    assert theme.touch_count == 1
    assert len([e for e in log if e.event_type == EventType.THEME_INTRODUCED]) == 1

    # --- browser-visible clause: the theme shows up in browser_sections().
    sections = await browser_sections(read, staleness_threshold=3)
    themes_section = next(s for s in sections if s["key"] == "themes")
    assert any(item["id"] == "loss" and item["label"] == "Loss" for item in themes_section["items"])


async def test_m5_2_done_when_mechanical_chain_voice_drift(stack):
    """M5.2 done-when (a), voice-drift half, traced clause by clause -- see
    docs/submilestones/M5-finish.md's M5.2 done-when cell and
    docs/superpowers/plans/2026-07-18-novelizer-m5.2-themes-voice.md Task 9."""
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()

    # --- Clause 1: a FakeRunner EditorVerdict carrying a voice_drift_flags
    # entry is returned from Editor.work().
    verdict = EditorVerdict(
        verdict="approve",
        notes="clean",
        voice_drift_flags=[
            VoiceDriftFlag(
                character_id="mara",
                line="I dunno, whatever.",
                trait_violated="formal, clipped diction",
                note="drops into casual slang",
            )
        ],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    ctx = await agent.poll()
    result = await agent.work(ctx)
    assert result.voice_drift_flags and result.voice_drift_flags[0].character_id == "mara"

    # --- Clause 2: Editor.commit() produces a retcon_request.created event.
    await agent.commit(result, ctx)
    await proj.catch_up()
    log = await events.events_since(0)
    created = [e for e in log if e.event_type == EventType.RETCON_REQUEST_CREATED]
    assert len(created) == 1

    # --- Clause 3: its description is tagged with VOICE_SOURCE_TAG.
    assert created[0].payload["description"].startswith(VOICE_SOURCE_TAG)

    # --- Clause 4: it lands in the open retcon queue.
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    tagged = [r for r in open_reqs if r.description.startswith(VOICE_SOURCE_TAG)]
    assert len(tagged) == 1


async def test_editor_voice_drift_flag_dedups_against_open_retcons(stack):
    """Fix-wave regression (M5.2 branch review): the Editor re-reviews the same
    draft chapter every cycle until it is revised, so re-flagging the same
    drift must not file a second open retcon -- mirror the Continuity
    Checker's dedup-by-description against the open queue.
    """
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(
        verdict="revise",
        notes="voice drift",
        voice_drift_flags=[
            VoiceDriftFlag(
                character_id="mara",
                line="I dunno, whatever.",
                trait_violated="formal, clipped diction",
                note="drops into casual slang",
            )
        ],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    second = Editor(FakeRunner(verdict), read, committer)
    await second.run_once()
    await proj.catch_up()
    open_reqs = await read.list_retcon_requests(status=RetconStatus.open)
    tagged = [r for r in open_reqs if r.description.startswith(VOICE_SOURCE_TAG)]
    assert len(tagged) == 1, f"expected the duplicate drift flag to dedup, got {len(tagged)} open voice retcons"


async def test_editor_constructor_threads_sag_spike_delta_through(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c1",
                        AnnotationStructureScored(chapter_id="c1", tension=0.5, pacing_label="steady"))
    await events.append(EventType.ANNOTATION_STRUCTURE_SCORED, "c2",
                        AnnotationStructureScored(chapter_id="c2", tension=0.65, pacing_label="steady"))
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer, sag_spike_delta=0.05)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "Pacing flags" in sent


from novelizer.store.models import RetconRequest


async def test_editor_prompt_lists_open_voice_drift_retcons(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    req = RetconRequest(
        description=f"{VOICE_SOURCE_TAG} clipped speech violated by mara: \"Well, I suppose we could.\"",
        conflicting_entry_ids=["mara"], proposed_resolution="")
    await events.append(EventType.RETCON_REQUEST_CREATED, req.id, req)
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert "already filed (do not re-flag these lines)" in sent
    assert 'violated by mara: "Well, I suppose we could."' in sent


async def test_editor_prompt_ignores_non_voice_open_retcons(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    req = RetconRequest(description="two suns vs one", conflicting_entry_ids=["w1"], proposed_resolution="pick one")
    await events.append(EventType.RETCON_REQUEST_CREATED, req.id, req)
    await proj.catch_up()
    runner = FakeRunner(EditorVerdict(verdict="approve", notes="clean"))
    agent = Editor(runner, read, committer)
    ctx = await agent.poll()
    await agent.work(ctx)
    sent = runner.calls[-1]["messages"][0]["content"]
    assert sent == "Chapter title: One\n\nProse:\np"


class _FakeSettings:
    agent_model = "gpt-4o-mini"
    llm_base_url = None
    llm_api_key = "test-key"
    agent_temperature = 0.7
    llm_max_tokens = None


def test_build_editor_runner_without_backend_stays_constructible():
    from novelizer.agents.editor import build_editor_runner

    runner = build_editor_runner(_FakeSettings())
    assert runner is not None


def test_build_editor_runner_with_backend_uses_retrieval_note_base():
    from novelizer.agents.editor import build_editor_runner, SYSTEM_PROMPT
    from novelizer.agents.author import RETRIEVAL_NOTE_BASE
    from novelizer.canon_fs.backend import CanonBackend

    backend = CanonBackend(read_store=None)
    runner = build_editor_runner(_FakeSettings(), backend=backend, tools=[])
    assert runner is not None
    assert "chapter list below" not in RETRIEVAL_NOTE_BASE
    assert (SYSTEM_PROMPT + RETRIEVAL_NOTE_BASE).endswith(RETRIEVAL_NOTE_BASE)


def test_build_editor_runner_with_backend_bounds_recursion():
    from novelizer.agents.editor import build_editor_runner
    from novelizer.canon_fs.backend import CanonBackend

    backend = CanonBackend(read_store=None)
    runner = build_editor_runner(_FakeSettings(), backend=backend, tools=[])
    assert runner.config.get("recursion_limit") == 100


async def test_editor_commits_promise_intents_with_validation(stack):
    events, proj, read, committer = stack
    from novelizer.agents.schemas import PromiseIntent
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="p"))
    await proj.catch_up()
    verdict = EditorVerdict(
        verdict="approve", notes="clean",
        promise_intents=[
            PromiseIntent(action="make", name="The Sealed Letter", kind="plant"),
            PromiseIntent(action="pay", id="never-made"),
        ],
    )
    agent = Editor(FakeRunner(verdict), read, committer)
    await agent.run_once()
    await proj.catch_up()
    promises = await read.list_promises()
    assert [p.id for p in promises] == ["the-sealed-letter"]
    assert promises[0].state.value == "open"
