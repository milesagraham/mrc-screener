import pytest

from mrc_screener.core.modes import (
    DataMode,
    find_tilt_angles,
    format_angle,
    parse_mdoc,
    parse_tlt,
    plan_previews,
    plan_tilt_series,
    plan_tomogram,
)


def write(path, text):
    path.write_text(text)
    return path


MDOC = """PixelSpacing = 1.35
ImageFile = TS_01.st

[T = SerialEM: Digitized on EMBL Krios]

[ZValue = 0]
TiltAngle = 0.0
ExposureDose = 3

[ZValue = 1]
TiltAngle = 3.0

[ZValue = 2]
TiltAngle = -3.0

[ZValue = 3]
TiltAngle = -6.0
"""


# ---------------------------------------------------------------------------
# sidecar parsing
# ---------------------------------------------------------------------------


def test_parse_tlt(tmp_path):
    p = write(tmp_path / "a.tlt", " -60.0\n-57.0\n\n  0.01\n")
    assert parse_tlt(p) == [-60.0, -57.0, 0.01]


def test_parse_mdoc_returns_zvalue_order(tmp_path):
    p = write(tmp_path / "TS_01.st.mdoc", MDOC)
    assert parse_mdoc(p) == [0.0, 3.0, -3.0, -6.0]


def test_parse_mdoc_rejects_gaps(tmp_path):
    p = write(tmp_path / "x.mdoc", "[ZValue = 0]\nTiltAngle = 0\n[ZValue = 2]\nTiltAngle = 3\n")
    with pytest.raises(ValueError):
        parse_mdoc(p)


def test_parse_mdoc_ignores_tiltangle_outside_zvalue(tmp_path):
    p = write(tmp_path / "x.mdoc", "TiltAngle = 99\n[ZValue = 0]\nTiltAngle = 1.5\n")
    assert parse_mdoc(p) == [1.5]


# ---------------------------------------------------------------------------
# sidecar discovery
# ---------------------------------------------------------------------------


def test_find_tilt_angles_prefers_tlt(tmp_path):
    st = tmp_path / "TS_01.st"
    write(tmp_path / "TS_01.tlt", "-3\n0\n3\n")
    write(tmp_path / "TS_01.rawtlt", "-4\n0\n4\n")
    angles, source, problems = find_tilt_angles(st, 3)
    assert angles == [-3.0, 0.0, 3.0]
    assert source == "TS_01.tlt"
    assert problems == []


def test_find_tilt_angles_falls_back_and_reports_count_mismatch(tmp_path):
    st = tmp_path / "TS_01.st"
    write(tmp_path / "TS_01.tlt", "-3\n0\n")  # 2 angles, stack has 4
    write(tmp_path / "TS_01.st.mdoc", MDOC)
    angles, source, problems = find_tilt_angles(st, 4)
    assert source == "TS_01.st.mdoc"
    assert angles == [0.0, 3.0, -3.0, -6.0]
    assert problems == ["TS_01.tlt: 2 angles but stack has 4 frames"]


def test_find_tilt_angles_none_found(tmp_path):
    assert find_tilt_angles(tmp_path / "TS_01.st", 4) == (None, None, [])


def test_find_tilt_angles_for_mrc_extension_uses_mrc_mdoc(tmp_path):
    write(tmp_path / "TS_01.mrc.mdoc", MDOC)
    angles, source, _ = find_tilt_angles(tmp_path / "TS_01.mrc", 4)
    assert source == "TS_01.mrc.mdoc"


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------


def test_plan_tilt_series_shows_every_frame_sorted_by_angle():
    # dose-symmetric acquisition order: 0, +3, -3, -6
    plan = plan_tilt_series(4, [0.0, 3.0, -3.0, -6.0])
    assert plan.frame_indices == [3, 2, 0, 1]
    assert plan.tilt_angles == [-6.0, -3.0, 0.0, 3.0]
    assert plan.start_pos == 2  # opens on the 0 deg tilt
    assert plan.contrast_fraction == 1.0
    assert plan.labels[0] == "−6.0° (frame 3)"


def test_plan_tilt_series_without_angles_is_file_order():
    plan = plan_tilt_series(41)
    assert plan.frame_indices == list(range(41))
    assert plan.tilt_angles is None
    assert plan.start_pos == 20


def test_plan_tilt_series_rejects_wrong_angle_count():
    with pytest.raises(ValueError):
        plan_tilt_series(3, [0.0, 1.0])


def test_plan_tomogram_matches_central_band():
    plan = plan_tomogram(200, n_previews=20, central_fraction=0.5)
    assert len(plan.frame_indices) == 20
    assert min(plan.frame_indices) >= 50 and max(plan.frame_indices) <= 150
    assert plan.contrast_fraction == 0.5


def test_format_angle():
    assert format_angle(-60) == "−60.0°"
    assert format_angle(12.34) == "+12.3°"
    assert format_angle(0) == "±0.0°"


def test_plan_previews_tilt_series_covers_full_range(make_mrc, tmp_path):
    path = make_mrc("TS_01.st", shape=(41, 32, 32))
    angles = [-60 + 3 * i for i in range(41)]
    write(tmp_path / "TS_01.rawtlt", "\n".join(str(a) for a in angles))
    plan = plan_previews(path, DataMode.TILT_SERIES)
    assert len(plan.frame_indices) == 41
    assert plan.tilt_angles[0] == -60 and plan.tilt_angles[-1] == 60
    assert plan.angle_source == "TS_01.rawtlt"
    assert "TS_01.rawtlt" in plan.note


def test_plan_previews_tilt_series_without_sidecar_says_so(make_mrc):
    path = make_mrc("TS_02.st", shape=(5, 16, 16))
    plan = plan_previews(path, DataMode.TILT_SERIES)
    assert plan.frame_indices == [0, 1, 2, 3, 4]
    assert "no tilt angles" in plan.note


def test_plan_previews_tomogram_mode_unchanged(make_mrc):
    path = make_mrc("vol.mrc", shape=(40, 16, 16))
    plan = plan_previews(path, DataMode.TOMOGRAM)
    assert plan.mode is DataMode.TOMOGRAM
    assert plan.note is None
