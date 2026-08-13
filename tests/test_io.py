import numpy as np
import pytest

from mrc_screener.core.io import central_frame_indices, close_all_handles, get_shape, read_frame

from conftest import REAL_MRC, requires_test_materials


# ---------------------------------------------------------------------------
# central_frame_indices geometry
# ---------------------------------------------------------------------------


def test_central_frame_indices_stay_within_central_band():
    indices = central_frame_indices(100, n_previews=20, central_fraction=0.5)
    assert len(indices) == len(set(indices))  # unique
    assert indices == sorted(indices)
    assert min(indices) >= 24  # central band is [25, 75], allow rounding slack
    assert max(indices) <= 76
    assert all(0 <= i < 100 for i in indices)


def test_central_frame_indices_caps_at_available_frames():
    indices = central_frame_indices(3, n_previews=20, central_fraction=0.5)
    assert len(indices) <= 3
    assert all(0 <= i < 3 for i in indices)


def test_central_frame_indices_single_frame():
    assert central_frame_indices(1, n_previews=20) == [0]


def test_central_frame_indices_zero_frames():
    assert central_frame_indices(0, n_previews=20) == []


def test_central_frame_indices_full_fraction_covers_whole_stack():
    indices = central_frame_indices(100, n_previews=10, central_fraction=1.0)
    assert min(indices) <= 5
    assert max(indices) >= 94


# ---------------------------------------------------------------------------
# mmap frame access, real fixture
# ---------------------------------------------------------------------------


@requires_test_materials
def test_get_shape_real_fixture():
    assert get_shape(REAL_MRC) == (224, 382, 382)


@requires_test_materials
def test_read_frame_real_fixture_shape_and_no_full_load():
    frame = read_frame(REAL_MRC, 100)
    assert frame.shape == (382, 382)
    close_all_handles()  # exercise cleanup path; must not raise


# ---------------------------------------------------------------------------
# synthetic fixtures
# ---------------------------------------------------------------------------


def test_get_shape_synthetic(make_mrc):
    path = make_mrc("vol.mrc", shape=(7, 32, 40))
    assert get_shape(path) == (7, 32, 40)


def test_read_frame_matches_source_data(make_mrc):
    path = make_mrc("vol.mrc", shape=(4, 16, 16), seed=42)
    frame = read_frame(path, 2)
    assert frame.shape == (16, 16)
    assert frame.dtype == np.float32


def test_read_frame_out_of_range_raises(make_mrc):
    path = make_mrc("vol.mrc", shape=(3, 8, 8))
    with pytest.raises(IndexError):
        read_frame(path, 99)


def test_2d_file_reports_single_frame(make_mrc):
    path = make_mrc("img.mrc", shape=(32, 32))
    assert get_shape(path) == (1, 32, 32)
    frame = read_frame(path, 0)
    assert frame.shape == (32, 32)
    with pytest.raises(IndexError):
        read_frame(path, 1)
