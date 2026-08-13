from pathlib import Path

from mrc_screener.core.matching import MatchTier, RowRef, match_rows
from mrc_screener.core.metadata import parse_txt_list, scan_mrc_dir


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_parse_txt_list_skips_blank_lines(tmp_path):
    p = tmp_path / "list.txt"
    p.write_text("1\ttomostar/a.tomostar\n2\ttomostar/b.tomostar\n\n\n")
    rows = parse_txt_list(p)
    assert [r.reference for r in rows] == ["tomostar/a.tomostar", "tomostar/b.tomostar"]
    assert [r.row_id for r in rows] == ["1", "2"]


def test_parse_txt_list_tolerates_bare_reference_lines(tmp_path):
    p = tmp_path / "list.txt"
    p.write_text("just_a_path.tomostar\n")
    rows = parse_txt_list(p)
    assert rows[0].reference == "just_a_path.tomostar"
    assert rows[0].row_id == "1"  # row-numbered by position, no tab column


def test_scan_mrc_dir_default_is_not_recursive(tmp_path):
    touch(tmp_path / "a.mrc")
    touch(tmp_path / "sub" / "b.mrc")
    assert scan_mrc_dir(tmp_path) == [tmp_path / "a.mrc"]


def test_scan_mrc_dir_recursive_finds_nested_files(tmp_path):
    touch(tmp_path / "a.mrc")
    touch(tmp_path / "sub" / "b.mrc")
    found = scan_mrc_dir(tmp_path, recursive=True)
    assert found == sorted([tmp_path / "a.mrc", tmp_path / "sub" / "b.mrc"])


def test_scan_mrc_dir_ignores_wrong_extension(tmp_path):
    touch(tmp_path / "a.mrc")
    touch(tmp_path / "a.png")
    assert scan_mrc_dir(tmp_path) == [tmp_path / "a.mrc"]


def test_deconv_subdirectory_is_invisible_by_default_and_ambiguous_if_opted_into(tmp_path):
    # Reproduces the real-world case: a reconstruction directory holding a
    # deconvolved copy of every tomogram under an identical filename one
    # level down in deconv/. The non-recursive default matches only the
    # plain reconstruction, so this resolves cleanly with zero
    # configuration -- the whole point of the shallow default.
    touch(tmp_path / "map67_ts_006_10.00Apx.mrc")
    touch(tmp_path / "deconv" / "map67_ts_006_10.00Apx.mrc")
    touch(tmp_path / "map67_ts_006_10.00Apx.png")  # never a candidate: wrong extension

    rows = [RowRef("1", "tomostar/map67_ts_006.tomostar")]

    default_report = match_rows(rows, scan_mrc_dir(tmp_path))
    assert default_report.matches[0].tier is MatchTier.NORMALIZED_STEM
    assert default_report.matches[0].matched_path == tmp_path / "map67_ts_006_10.00Apx.mrc"

    # opting into recursion surfaces the genuine ambiguity rather than guessing
    recursive_report = match_rows(rows, scan_mrc_dir(tmp_path, recursive=True))
    assert recursive_report.matches[0].tier is MatchTier.AMBIGUOUS
    assert len(recursive_report.matches[0].candidates) == 2
