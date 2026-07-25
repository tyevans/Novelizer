"""Fleet-shared prompt surfaces (novelizer/agents/prompts.py).

These constants are the pull-tool contract every LLM agent inherits. The
behaviours pinned here come from docs/agent-prompting/proposal-fleet-shared.md
§2.1-§2.2: index-then-read, no-write-from-summary, a stopping rule, and a
three-way act/stand-aside/confirm decision.
"""
from __future__ import annotations

import pytest

from novelizer.agents.prompts import (
    OUTPUT_CONVENTIONS_NOTE,
    PASS_PROMPT_INSTRUCTION,
    RETRIEVAL_NOTE,
    RETRIEVAL_NOTE_BASE,
)


class TestRetrievalNote:
    def test_base_and_map_variants_share_a_prefix_and_suffix(self):
        """The index-mode note is the keeper note plus one map sentence, so
        every agent gets byte-identical tool doctrine either way."""
        assert RETRIEVAL_NOTE.startswith("\n\n## Canon access\n")
        assert RETRIEVAL_NOTE_BASE.startswith("\n\n## Canon access\n")
        assert len(RETRIEVAL_NOTE) > len(RETRIEVAL_NOTE_BASE)

    def test_names_the_index_then_read_loop(self):
        for note in (RETRIEVAL_NOTE, RETRIEVAL_NOTE_BASE):
            assert "grep" in note and "search_canon" in note and "read_file" in note

    def test_divides_grep_from_semantic_search(self):
        """Tool-boundary guidance: exact strings -> grep, meaning -> search_canon."""
        assert "exact" in RETRIEVAL_NOTE_BASE
        assert "search_canon" in RETRIEVAL_NOTE_BASE

    def test_map_variant_forbids_writing_from_the_pushed_summary(self):
        """The starvation-bug fix: pushed context is an index, not the source."""
        assert "INDEX" in RETRIEVAL_NOTE
        assert "do NOT" in RETRIEVAL_NOTE
        assert "in full" in RETRIEVAL_NOTE

    def test_base_variant_omits_the_map_sentence(self):
        """Keepers get no pushed chapter index, so the map sentence would lie."""
        assert "INDEX" not in RETRIEVAL_NOTE_BASE

    def test_names_all_six_root_directories(self):
        """The tree layout must be stated, not guessable: a local model
        inferred a phantom /canon/ root from the surrounding canon language
        and burned a whole pass on not-found reads."""
        for directory in ("/chapters", "/characters", "/world", "/threads",
                          "/secrets", "/themes"):
            for note in (RETRIEVAL_NOTE, RETRIEVAL_NOTE_BASE):
                assert directory in note

    def test_points_at_the_derived_dramatic_irony_ledger(self):
        """The ledger is the one canon file no record's title leads to, so an
        agent that only slugs titles into paths would never find it."""
        from novelizer.canon_fs.backend import IRONY_LEDGER_PATH
        for note in (RETRIEVAL_NOTE, RETRIEVAL_NOTE_BASE):
            assert IRONY_LEDGER_PATH in note

    def test_disowns_the_canon_prefix_hallucination(self):
        for note in (RETRIEVAL_NOTE, RETRIEVAL_NOTE_BASE):
            assert "no /canon" in note

    def test_teaches_the_slug_convention_with_a_worked_example(self):
        """Slugs keep leading articles ('The Silvanthrine' ->
        the-silvanthrine.md); agents guessed slugs with articles dropped."""
        for note in (RETRIEVAL_NOTE, RETRIEVAL_NOTE_BASE):
            assert "lowercase" in note
            assert "the-mourning-courts-of-vael.md" in note

    def test_tells_agents_to_list_rather_than_guess_paths(self):
        for note in (RETRIEVAL_NOTE, RETRIEVAL_NOTE_BASE):
            assert "Never guess a path" in note

    def test_carries_a_stopping_rule_against_turn_burning(self):
        for note in (RETRIEVAL_NOTE, RETRIEVAL_NOTE_BASE):
            assert "stop searching" in note

    def test_requires_grounding_ids_in_files_actually_read(self):
        for note in (RETRIEVAL_NOTE, RETRIEVAL_NOTE_BASE):
            assert "cite ids exactly" in note.lower()


class TestPassInstruction:
    def test_offers_three_outcomes_not_a_binary_skip(self):
        """act / stand-aside / confirm-first — the calibration win is the
        third branch, which the old binary switch had no room for."""
        assert "no_action=true" in PASS_PROMPT_INSTRUCTION
        assert "confirm" in PASS_PROMPT_INSTRUCTION.lower()

    def test_anchors_the_decision_to_a_concrete_delta(self):
        """'Nothing needs your attention' is a vibe; 'what changed since your
        last pass' is checkable."""
        assert "since your last pass" in PASS_PROMPT_INSTRUCTION

    def test_frames_correct_silence_as_success(self):
        assert "SUCCESS" in PASS_PROMPT_INSTRUCTION

    def test_counterweights_laziness(self):
        """Verify-then-abstain overshoots without a don't-miss-real-events clause."""
        assert "failure" in PASS_PROMPT_INSTRUCTION
        assert "Staying silent" in PASS_PROMPT_INSTRUCTION

    def test_keeps_the_no_action_contract_the_agents_commit_on(self):
        assert "leave every list empty" in PASS_PROMPT_INSTRUCTION
        assert "feed_note" in PASS_PROMPT_INSTRUCTION


class TestOutputConventionsNote:
    def test_is_a_self_contained_section(self):
        """Appended after other notes, so it must open its own heading."""
        assert OUTPUT_CONVENTIONS_NOTE.startswith("\n\n## Output contract\n")

    def test_points_at_the_pack_file(self):
        """The pointer must name the exact readable path, not just the pack."""
        assert "/skills/output-conventions/SKILL.md" in OUTPUT_CONVENTIONS_NOTE

    def test_carries_the_inline_summary(self):
        """Useful even when the agent never reads the file."""
        assert "one short line" in OUTPUT_CONVENTIONS_NOTE
        assert "markup" in OUTPUT_CONVENTIONS_NOTE


class TestBackCompatReExports:
    """Seven modules import these through author.py/base.py today. The move
    keeps those paths alive so importers migrate in a separate step."""

    def test_author_still_re_exports_the_retrieval_notes(self):
        from novelizer.agents import author, prompts

        assert author.RETRIEVAL_NOTE is prompts.RETRIEVAL_NOTE
        assert author.RETRIEVAL_NOTE_BASE is prompts.RETRIEVAL_NOTE_BASE

    @pytest.mark.parametrize(
        "module_path",
        [
            "novelizer.agents.character_keeper",
            "novelizer.agents.continuity_checker",
            "novelizer.agents.world_architect",
        ],
    )
    def test_pass_using_agents_still_import_cleanly(self, module_path):
        __import__(module_path)
