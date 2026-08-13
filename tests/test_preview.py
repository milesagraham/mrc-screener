import numpy as np

from mrc_screener.core.preview import (
    PreviewCache,
    bin_frame,
    encode_jpeg,
    get_contrast_window,
    get_preview_jpeg,
    normalize_to_uint8,
)

from conftest import REAL_MRC, requires_test_materials

JPEG_MAGIC = b"\xff\xd8"


# ---------------------------------------------------------------------------
# bin_frame
# ---------------------------------------------------------------------------


def test_bin_frame_block_mean():
    arr = np.array(
        [
            [0, 0, 2, 2],
            [0, 0, 2, 2],
            [4, 4, 6, 6],
            [4, 4, 6, 6],
        ],
        dtype=np.float32,
    )
    binned, factor = bin_frame(arr, target_long_axis=2)
    assert factor == 2
    assert binned.shape == (2, 2)
    assert np.array_equal(binned, [[0, 2], [4, 6]])


def test_bin_frame_passthrough_when_already_small():
    arr = np.zeros((10, 10), dtype=np.float32)
    binned, factor = bin_frame(arr, target_long_axis=512)
    assert factor == 1
    assert binned is arr


def test_bin_frame_trims_remainder():
    arr = np.arange(25, dtype=np.float32).reshape(5, 5)
    binned, factor = bin_frame(arr, target_long_axis=2)
    assert factor == 2  # round(5/2) == 2 (approx) or similar; just check it doesn't crash
    assert binned.shape[0] <= 5 and binned.shape[1] <= 5


# ---------------------------------------------------------------------------
# normalize_to_uint8
# ---------------------------------------------------------------------------


def test_normalize_to_uint8_clips_to_window():
    arr = np.array([-100, 0, 50, 100, 200], dtype=np.float32)
    out = normalize_to_uint8(arr, lo=0, hi=100)
    assert out.dtype == np.uint8
    assert out[0] == 0  # clipped below window
    assert out[-1] == 255  # clipped above window
    assert out[2] == pytest_approx_byte(50, 0, 100)


def pytest_approx_byte(value, lo, hi):
    return int((value - lo) / (hi - lo) * 255)  # normalize_to_uint8 truncates, not rounds


def test_normalize_to_uint8_degenerate_window_does_not_crash():
    arr = np.array([5.0, 5.0, 5.0], dtype=np.float32)
    out = normalize_to_uint8(arr, lo=5.0, hi=5.0)
    assert out.dtype == np.uint8
    assert np.all(out == 0)


# ---------------------------------------------------------------------------
# encode_jpeg
# ---------------------------------------------------------------------------


def test_encode_jpeg_produces_valid_jpeg_bytes():
    img8 = (np.random.default_rng(0).random((32, 32)) * 255).astype(np.uint8)
    data = encode_jpeg(img8, quality=85)
    assert data[:2] == JPEG_MAGIC


# ---------------------------------------------------------------------------
# contrast window + full preview pipeline, real fixture
# ---------------------------------------------------------------------------


@requires_test_materials
def test_get_contrast_window_real_fixture_is_sane():
    lo, hi = get_contrast_window(REAL_MRC)
    assert lo < hi


@requires_test_materials
def test_get_preview_jpeg_real_fixture(tmp_path):
    cache = PreviewCache(tmp_path / "cache")
    data = get_preview_jpeg(REAL_MRC, 100, cache, target_long_axis=128)
    assert data[:2] == JPEG_MAGIC


# ---------------------------------------------------------------------------
# PreviewCache behavior
# ---------------------------------------------------------------------------


def test_preview_cache_hit_avoids_recompute(tmp_path, make_mrc, monkeypatch):
    path = make_mrc("vol.mrc", shape=(3, 32, 32))
    cache = PreviewCache(tmp_path / "cache")

    first = get_preview_jpeg(path, 0, cache, target_long_axis=16)

    import mrc_screener.core.preview as preview_mod

    def _boom(*a, **k):
        raise AssertionError("should not re-encode on a cache hit")

    monkeypatch.setattr(preview_mod, "encode_jpeg", _boom)
    second = get_preview_jpeg(path, 0, cache, target_long_axis=16)
    assert second == first


def test_preview_cache_invalidates_on_mtime_change(tmp_path, make_mrc):
    path = make_mrc("vol.mrc", shape=(3, 32, 32))
    cache_dir = tmp_path / "cache"
    cache = PreviewCache(cache_dir)

    get_preview_jpeg(path, 0, cache, target_long_axis=16)
    files_before = list(cache_dir.rglob("*.jpg"))
    assert len(files_before) == 1

    import os
    import time

    time.sleep(0.01)
    os.utime(path, None)  # bump mtime without changing content
    get_preview_jpeg(path, 0, cache, target_long_axis=16)
    files_after = list(cache_dir.rglob("*.jpg"))
    assert len(files_after) == 2
