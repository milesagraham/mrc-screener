from pathlib import Path

import pytest

from mrc_screener.core.matching import (
    MatchConfig,
    MatchTier,
    RowRef,
    load_overrides,
    match_rows,
)
from mrc_screener.core.metadata import parse_txt_list, scan_mrc_dir

from conftest import TEST_MATERIALS, requires_test_materials


def paths(*names: str) -> list[Path]:
    return [Path(n) for n in names]


# ---------------------------------------------------------------------------
# Real fixture: test_materials/input_list.txt vs the one MRC on disk.
# ---------------------------------------------------------------------------


@requires_test_materials
def test_real_input_list_matches_real_mrc_via_normalized_tier():
    rows = parse_txt_list(TEST_MATERIALS / "input_list.txt")
    assert len(rows) == 23  # trailing blank lines must not become rows
    assert rows[0] == RowRef(row_id="1", reference="tomostar/map15_ts_028.tomostar")

    mrc_files = scan_mrc_dir(TEST_MATERIALS)
    assert mrc_files == [TEST_MATERIALS / "map15_ts_028_10.00Apx.mrc"]

    report = match_rows(rows, mrc_files)

    target = next(m for m in report.matches if m.row.reference == "tomostar/map15_ts_028.tomostar")
    assert target.tier is MatchTier.NORMALIZED_STEM
    assert target.matched_path == TEST_MATERIALS / "map15_ts_028_10.00Apx.mrc"

    # every other row has no corresponding file on disk in this fixture
    others = [m for m in report.matches if m.row.reference != "tomostar/map15_ts_028.tomostar"]
    assert all(m.tier is MatchTier.UNMATCHED for m in others)
    assert not report.file_collisions
    assert not report.unclaimed_files  # the one file was claimed


# ---------------------------------------------------------------------------
# Tier 1: exact stem
# ---------------------------------------------------------------------------


def test_exact_stem_match():
    rows = [RowRef("1", "map15_ts_028.tomostar")]
    files = paths("map15_ts_028.mrc")
    report = match_rows(rows, files)
    assert report.matches[0].tier is MatchTier.EXACT_STEM
    assert report.matches[0].matched_path == Path("map15_ts_028.mrc")


# ---------------------------------------------------------------------------
# Tier 2: normalized stem (suffix chains, prefix noise via extra patterns)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mrc_name",
    [
        "map15_ts_028_10.00Apx.mrc",
        "map15_ts_028_bin4.mrc",
        "map15_ts_028_bin4_rec.mrc",
        "map15_ts_028_rec.mrc",
        "map15_ts_028_dose-filt.mrc",
        "map15_ts_028_dosefiltered.mrc",
        "map15_ts_028_DW.mrc",
        "map15_ts_028_flipx.mrc",
        "map15_ts_028_bin2_10.00Apx_rec.mrc",  # stacked suffixes
    ],
)
def test_normalized_stem_handles_default_suffix_conventions(mrc_name):
    rows = [RowRef("1", "tomostar/map15_ts_028.tomostar")]
    files = paths(mrc_name)
    report = match_rows(rows, files)
    m = report.matches[0]
    assert m.tier is MatchTier.NORMALIZED_STEM, f"{mrc_name} -> {m.tier}"
    assert m.matched_path == Path(mrc_name)


def test_extra_suffix_pattern_promotes_loose_match_to_a_precise_one():
    rows = [RowRef("1", "map15_ts_028.tomostar")]
    files = paths("map15_ts_028_myLabSuffix.mrc")
    # substring containment already catches this as a last resort...
    assert match_rows(rows, files).matches[0].tier is MatchTier.SUBSTRING

    # ...but a user-supplied pattern gets a tighter, more confident tier.
    config = MatchConfig(extra_suffix_patterns=(r"[_\-]myLabSuffix$",))
    report = match_rows(rows, files, config)
    assert report.matches[0].tier is MatchTier.NORMALIZED_STEM


def test_extra_prefix_pattern_promotes_loose_match_to_a_precise_one():
    rows = [RowRef("1", "map15_ts_028.tomostar")]
    files = paths("PROJ42_map15_ts_028.mrc")
    assert match_rows(rows, files).matches[0].tier is MatchTier.SUBSTRING

    config = MatchConfig(extra_prefix_patterns=(r"^PROJ\d+[_\-]",))
    report = match_rows(rows, files, config)
    assert report.matches[0].tier is MatchTier.NORMALIZED_STEM


def test_genuinely_ambiguous_case_needs_manual_override_not_more_patterns():
    # Two files both contain "map15_ts_028" as a substring and, once a
    # prefix pattern strips the project tag, both normalize identically --
    # patterns alone can't disambiguate a genuine collision. The report
    # stays honestly AMBIGUOUS; only a manual override picks a winner.
    rows = [RowRef("1", "map15_ts_028.tomostar")]
    files = paths("PROJ42_map15_ts_028.mrc", "PROJ99_map15_ts_028.mrc")
    config = MatchConfig(extra_prefix_patterns=(r"^PROJ\d+[_\-]",))
    report = match_rows(rows, files, config)
    m = report.matches[0]
    assert m.tier is MatchTier.AMBIGUOUS
    assert set(m.candidates) == set(files)

    resolved = match_rows(
        rows,
        files,
        MatchConfig(
            extra_prefix_patterns=(r"^PROJ\d+[_\-]",),
            manual_overrides={"map15_ts_028.tomostar": "PROJ99_map15_ts_028.mrc"},
        ),
    )
    assert resolved.matches[0].tier is MatchTier.OVERRIDE
    assert resolved.matches[0].matched_path == Path("PROJ99_map15_ts_028.mrc")


def test_disable_default_patterns_falls_back_to_a_looser_tier():
    rows = [RowRef("1", "map15_ts_028.tomostar")]
    files = paths("map15_ts_028_bin4.mrc")
    with_defaults = match_rows(rows, files).matches[0].tier
    assert with_defaults is MatchTier.NORMALIZED_STEM

    config = MatchConfig(disable_default_patterns=True)
    report = match_rows(rows, files, config)
    # bin4 suffix no longer stripped, so it falls through to the loose
    # substring tier instead of the precise normalized-stem tier.
    assert report.matches[0].tier is MatchTier.SUBSTRING


# ---------------------------------------------------------------------------
# Tier 3: case-insensitive normalized
# ---------------------------------------------------------------------------


def test_case_insensitive_tier():
    rows = [RowRef("1", "Map15_TS_028.tomostar")]
    files = paths("map15_ts_028_bin4.mrc")
    report = match_rows(rows, files)
    assert report.matches[0].tier is MatchTier.CASE_INSENSITIVE
    assert report.matches[0].matched_path == Path("map15_ts_028_bin4.mrc")


# ---------------------------------------------------------------------------
# Tier 4: unique substring containment
# ---------------------------------------------------------------------------


def test_substring_tier_as_last_resort():
    rows = [RowRef("1", "map15_ts_028.tomostar")]
    files = paths("2024-08-01_session_map15_ts_028_weirdtag_v3.mrc")
    report = match_rows(rows, files)
    assert report.matches[0].tier is MatchTier.SUBSTRING
    assert report.matches[0].matched_path == files[0]


def test_substring_tier_respects_min_length_guard():
    rows = [RowRef("1", "xx_ts5_yy.star")]
    files = paths("aa_ts5_bb.mrc")
    config = MatchConfig(min_substring_len=10)
    report = match_rows(rows, files, config)
    assert report.matches[0].tier is MatchTier.UNMATCHED


# ---------------------------------------------------------------------------
# Ambiguity: row matches multiple files
# ---------------------------------------------------------------------------


def test_row_side_ambiguity_is_reported_not_guessed():
    rows = [RowRef("1", "map15_ts_028.tomostar")]
    files = paths("map15_ts_028_bin2.mrc", "map15_ts_028_bin4.mrc")
    report = match_rows(rows, files)
    m = report.matches[0]
    assert m.tier is MatchTier.AMBIGUOUS
    assert m.matched_path is None
    assert set(m.candidates) == set(files)
    assert not report.is_clean


def test_ambiguity_at_tight_tier_does_not_fall_through_to_looser_tier():
    # Two files share an exact stem after extension stripping; even though
    # a looser tier might disambiguate, tight-tier ambiguity must win as an
    # error rather than being silently resolved by a looser heuristic.
    rows = [RowRef("1", "sample.star")]
    files = paths("sample.mrc", "sample.mrcs")
    report = match_rows(rows, files)
    assert report.matches[0].tier is MatchTier.AMBIGUOUS


# ---------------------------------------------------------------------------
# Ambiguity: file matches multiple rows (the reverse direction)
# ---------------------------------------------------------------------------


def test_file_side_collision_is_reported_for_both_rows():
    rows = [RowRef("1", "sample_a.tomostar"), RowRef("2", "sample_a_bin4.tomostar")]
    # contrived: both rows normalize to the same stem
    files = paths("sample_a.mrc")
    config = MatchConfig(extra_suffix_patterns=(r"[_\-]bin4$",))
    report = match_rows(rows, files, config)
    assert len(report.file_collisions) == 1
    fc = report.file_collisions[0]
    assert fc.path == Path("sample_a.mrc")
    assert {r.row_id for r in fc.rows} == {"1", "2"}
    assert all(m.tier is MatchTier.AMBIGUOUS and m.matched_path is None for m in report.matches)
    assert not report.is_clean


# ---------------------------------------------------------------------------
# No match at all
# ---------------------------------------------------------------------------


def test_completely_unmatched_row():
    rows = [RowRef("1", "totally_unrelated_name.tomostar")]
    files = paths("map15_ts_028.mrc")
    report = match_rows(rows, files)
    assert report.matches[0].tier is MatchTier.UNMATCHED
    assert report.matches[0].matched_path is None
    assert report.unclaimed_files == (Path("map15_ts_028.mrc"),)


# ---------------------------------------------------------------------------
# Manual overrides: the user-control escape hatch
# ---------------------------------------------------------------------------


def test_manual_override_wins_even_against_automatic_ambiguity():
    rows = [RowRef("1", "map15_ts_028.tomostar")]
    files = paths("map15_ts_028_bin2.mrc", "map15_ts_028_bin4.mrc")
    config = MatchConfig(manual_overrides={"map15_ts_028.tomostar": "map15_ts_028_bin4.mrc"})
    report = match_rows(rows, files, config)
    m = report.matches[0]
    assert m.tier is MatchTier.OVERRIDE
    assert m.matched_path == Path("map15_ts_028_bin4.mrc")


def test_manual_override_can_key_on_row_id():
    rows = [RowRef("7", "some/weird/path.tomostar")]
    files = paths("actual_file.mrc")
    config = MatchConfig(manual_overrides={"7": "actual_file.mrc"})
    report = match_rows(rows, files, config)
    assert report.matches[0].tier is MatchTier.OVERRIDE
    assert report.matches[0].matched_path == Path("actual_file.mrc")


def test_manual_override_to_nonexistent_file_is_surfaced_not_silent():
    rows = [RowRef("1", "map15_ts_028.tomostar")]
    files = paths("map15_ts_028.mrc")
    config = MatchConfig(manual_overrides={"map15_ts_028.tomostar": "does_not_exist.mrc"})
    report = match_rows(rows, files, config)
    m = report.matches[0]
    assert m.tier is MatchTier.UNMATCHED
    assert "does_not_exist.mrc" in m.note


def test_load_overrides_from_tsv(tmp_path):
    p = tmp_path / "overrides.tsv"
    p.write_text("# comment\nmap15_ts_028.tomostar\tmap15_ts_028_bin4.mrc\n\n")
    overrides = load_overrides(p)
    assert overrides == {"map15_ts_028.tomostar": "map15_ts_028_bin4.mrc"}


def test_load_overrides_from_csv(tmp_path):
    p = tmp_path / "overrides.csv"
    p.write_text("map15_ts_028.tomostar,map15_ts_028_bin4.mrc\n")
    overrides = load_overrides(p)
    assert overrides == {"map15_ts_028.tomostar": "map15_ts_028_bin4.mrc"}


# ---------------------------------------------------------------------------
# MatchReport.summary() smoke test
# ---------------------------------------------------------------------------


def test_summary_mentions_all_issue_categories():
    rows = [
        RowRef("1", "clean_hit.star"),
        RowRef("2", "ambiguous_row.star"),
        RowRef("3", "no_such_thing_anywhere.star"),
    ]
    files = paths(
        "clean_hit.mrc",
        "ambiguous_row_bin2.mrc",  # both normalize to "ambiguous_row" via
        "ambiguous_row_bin4.mrc",  # the default bin\d+ suffix pattern
        "extra_unclaimed.mrc",
    )
    report = match_rows(rows, files)
    text = report.summary()
    assert "AMBIGUOUS" in text
    assert "UNMATCHED" in text
    assert "matched no row" in text
