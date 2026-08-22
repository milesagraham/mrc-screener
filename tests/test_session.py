from pathlib import Path

from mrc_screener.core.matching import MatchTier, RowMatch, RowRef
from mrc_screener.core.session import Decision, Session, write_outputs


def make_match(row_id, reference, matched_path=None, tier=MatchTier.EXACT_STEM):
    row = RowRef(row_id=row_id, reference=reference)
    return RowMatch(row=row, matched_path=matched_path, tier=tier)


def test_decide_records_and_reads_back(tmp_path):
    journal = tmp_path / "j.jsonl"
    session = Session(journal_path=journal)
    m = make_match("1", "a.star", Path("a.mrc"))
    session.register_rows([m])

    session.decide("1", Decision.KEEP)
    assert session.decision_for("1") is Decision.KEEP
    assert session.counts() == {"keep": 1, "reject": 0}
    assert journal.exists()
    assert '"decision": "keep"' in journal.read_text()


def test_undo_last_reverts_most_recent_decision(tmp_path):
    session = Session(journal_path=tmp_path / "j.jsonl")
    session.register_rows([make_match("1", "a.star"), make_match("2", "b.star")])

    session.decide("1", Decision.KEEP)
    session.decide("2", Decision.REJECT)

    reverted = session.undo_last()
    assert reverted == "2"
    assert session.decision_for("2") is Decision.UNDECIDED
    assert session.decision_for("1") is Decision.KEEP
    assert session.counts() == {"keep": 1, "reject": 0}

    reverted = session.undo_last()
    assert reverted == "1"
    assert session.decision_for("1") is Decision.UNDECIDED

    assert session.undo_last() is None  # nothing left to undo


def test_journal_replay_resumes_session(tmp_path):
    journal = tmp_path / "j.jsonl"
    s1 = Session(journal_path=journal)
    s1.register_rows([make_match("1", "a.star"), make_match("2", "b.star")])
    s1.decide("1", Decision.KEEP)
    s1.decide("2", Decision.REJECT)

    s2 = Session(journal_path=journal)  # simulates relaunch after a crash
    assert s2.decision_for("1") is Decision.KEEP
    assert s2.decision_for("2") is Decision.REJECT
    assert s2.counts() == {"keep": 1, "reject": 1}


def test_journal_replay_takes_latest_event_per_row(tmp_path):
    journal = tmp_path / "j.jsonl"
    s1 = Session(journal_path=journal)
    s1.register_rows([make_match("1", "a.star")])
    s1.decide("1", Decision.KEEP)
    s1.decide("1", Decision.REJECT)  # changed their mind

    s2 = Session(journal_path=journal)
    assert s2.decision_for("1") is Decision.REJECT
    assert s2.counts() == {"keep": 0, "reject": 1}


def test_write_outputs_includes_unmatched_rows_and_kept_only_file(tmp_path):
    rows = [
        RowRef("1", "kept.star"),
        RowRef("2", "rejected.star"),
        RowRef("3", "pending.star"),
        RowRef("4", "gone.star"),
    ]
    matches = {
        "1": make_match("1", "kept.star", Path("kept.mrc"), MatchTier.EXACT_STEM),
        "2": make_match("2", "rejected.star", Path("rejected.mrc"), MatchTier.EXACT_STEM),
        "3": make_match("3", "pending.star", Path("pending.mrc"), MatchTier.EXACT_STEM),
        "4": make_match("4", "gone.star", None, MatchTier.UNMATCHED),
    }
    session = Session(journal_path=tmp_path / "j.jsonl")
    session.register_rows(matches.values())
    session.decide("1", Decision.KEEP)
    session.decide("2", Decision.REJECT)
    # row 3 stays undecided, row 4 was never matched

    screened_path, kept_path = write_outputs(rows, matches, session, tmp_path / "out")

    screened = screened_path.read_text().splitlines()
    assert screened[0] == "1\tkept.star\tkeep\tkept.mrc"
    assert screened[1] == "2\trejected.star\treject\trejected.mrc"
    assert screened[2] == "3\tpending.star\tundecided\tpending.mrc"
    assert screened[3] == "4\tgone.star\tunmatched\t"

    kept = kept_path.read_text().splitlines()
    assert kept == ["kept.star"]  # exact reference text, no index column
