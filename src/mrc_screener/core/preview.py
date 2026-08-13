"""Turn a raw MRC frame into a small, fast-loading JPEG.

Binning uses block-mean reduction on an already-in-memory 2D frame -- cheap,
since a single frame is a few MB at most (see CLAUDE.md hard rule #1; the
"never load the whole volume" rule is about the *volume*, not a frame).

Contrast is windowed once per tomogram from a handful of sampled frames and
reused for every frame of that volume, so brightness doesn't flicker as the
user scrubs through Z (CLAUDE.md, "Preview generation").
"""

from __future__ import annotations

import functools
import hashlib
import io as _stdio
from pathlib import Path

import numpy as np
from PIL import Image

from .io import central_frame_indices, read_frame

DEFAULT_N_PREVIEWS = 20
DEFAULT_CENTRAL_FRACTION = 0.5
DEFAULT_TARGET_LONG_AXIS = 512
DEFAULT_JPEG_QUALITY = 85
DEFAULT_N_CONTRAST_SAMPLES = 5
DEFAULT_LOW_PERCENTILE = 2.0
DEFAULT_HIGH_PERCENTILE = 98.0


def bin_frame(arr: np.ndarray, target_long_axis: int = DEFAULT_TARGET_LONG_AXIS) -> tuple[np.ndarray, int]:
    """Block-mean downsample so the long axis is <= target_long_axis."""
    long_axis = max(arr.shape)
    factor = max(1, round(long_axis / target_long_axis))
    if factor == 1:
        return arr, 1
    h, w = arr.shape
    h2, w2 = h - h % factor, w - w % factor
    trimmed = arr[:h2, :w2]
    binned = trimmed.reshape(h2 // factor, factor, w2 // factor, factor).mean(axis=(1, 3))
    return binned, factor


def normalize_to_uint8(arr: np.ndarray, lo: float, hi: float) -> np.ndarray:
    if hi <= lo:
        hi = lo + 1.0
    scaled = (arr.astype(np.float32) - lo) / (hi - lo)
    np.clip(scaled, 0.0, 1.0, out=scaled)
    return (scaled * 255).astype(np.uint8)


def encode_jpeg(img8: np.ndarray, quality: int = DEFAULT_JPEG_QUALITY) -> bytes:
    buf = _stdio.BytesIO()
    Image.fromarray(img8, mode="L").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


@functools.lru_cache(maxsize=512)
def _contrast_window_cached(
    path_str: str, mtime_ns: int, size: int, n_samples: int, n_previews: int, central_fraction: float
) -> tuple[float, float]:
    path = Path(path_str)
    from .io import get_shape

    n_frames, _, _ = get_shape(path)
    all_indices = central_frame_indices(n_frames, n_previews, central_fraction)
    if not all_indices:
        return (0.0, 1.0)
    step = max(1, len(all_indices) // n_samples)
    sample_indices = all_indices[::step][:n_samples] or all_indices[:1]
    samples = np.stack([read_frame(path, i).astype(np.float32) for i in sample_indices])
    lo = float(np.percentile(samples, DEFAULT_LOW_PERCENTILE))
    hi = float(np.percentile(samples, DEFAULT_HIGH_PERCENTILE))
    return (lo, hi)


def get_contrast_window(
    path: Path,
    n_samples: int = DEFAULT_N_CONTRAST_SAMPLES,
    n_previews: int = DEFAULT_N_PREVIEWS,
    central_fraction: float = DEFAULT_CENTRAL_FRACTION,
) -> tuple[float, float]:
    """2nd/98th percentile window, sampled once per (path, mtime, size)."""
    st = Path(path).stat()
    return _contrast_window_cached(str(path), st.st_mtime_ns, st.st_size, n_samples, n_previews, central_fraction)


class PreviewCache:
    """Disk cache of encoded preview JPEGs, keyed by everything that could
    change the output: file identity (mtime+size), which frame, and the
    render parameters. A second pass over a dataset should cost ~0 I/O."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _key(self, path: str, mtime_ns: int, size: int, index: int, target_long_axis: int, quality: int) -> str:
        raw = f"{path}|{mtime_ns}|{size}|{index}|{target_long_axis}|{quality}"
        return hashlib.sha1(raw.encode()).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.jpg"

    def get(self, *key_parts) -> bytes | None:
        p = self._cache_path(self._key(*key_parts))
        return p.read_bytes() if p.exists() else None

    def put(self, *key_parts, data: bytes) -> None:
        p = self._cache_path(self._key(*key_parts))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def get_preview_jpeg(
    path: Path,
    index: int,
    cache: PreviewCache,
    target_long_axis: int = DEFAULT_TARGET_LONG_AXIS,
    quality: int = DEFAULT_JPEG_QUALITY,
) -> bytes:
    path = Path(path)
    st = path.stat()
    key_parts = (str(path), st.st_mtime_ns, st.st_size, index, target_long_axis, quality)
    cached = cache.get(*key_parts)
    if cached is not None:
        return cached

    lo, hi = get_contrast_window(path)
    frame = read_frame(path, index)
    binned, _ = bin_frame(frame, target_long_axis)
    img8 = normalize_to_uint8(binned, lo, hi)
    data = encode_jpeg(img8, quality)
    cache.put(*key_parts, data=data)
    return data
