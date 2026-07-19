from novelizer.muse.report import muse_status_report, uptake_summary
from novelizer.store.models import HandStatus, InspirationHandRecord, InspirationUptakeRecord


def _hand(hand_id, status=HandStatus.consumed):
    return InspirationHandRecord(
        id=hand_id, seed=1, corpus_version="2026.07", era="modern", status=status,
        names=["Doris Kimbrough"], professions=["glazier"], settings=["salvage yard"],
        beats=["a debt is called in early"],
    )


def test_uptake_summary_counts_landed_items():
    hands = [_hand("h1"), _hand("h2")]
    uptake = [
        InspirationUptakeRecord(hand_id="h1", kind="names", item="Doris Kimbrough"),
        InspirationUptakeRecord(hand_id="h2", kind="professions", item="glazier"),
    ]
    summary = uptake_summary(hands, uptake)
    assert "2/8" in summary and "25%" in summary  # 4 items per hand x 2 consumed hands


def test_uptake_summary_without_consumed_hands():
    assert "No consumed hands" in uptake_summary([_hand("h1", HandStatus.active)], [])


def test_status_report_shows_active_hand_and_uptake():
    active = _hand("h9", HandStatus.active)
    report = muse_status_report(active, [active, _hand("h1")], [])
    assert "Doris Kimbrough" in report and "glazier" in report
    assert "0/4" in report


def test_status_report_without_active_hand():
    assert "No active hand" in muse_status_report(None, [], [])
