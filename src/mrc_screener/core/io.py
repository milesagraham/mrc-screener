"""Lazy, memory-mapped access to individual 2D frames within an MRC volume.

Never loads a whole volume into memory (see CLAUDE.md hard rule #1). Every
public function here takes a path and returns either cheap metadata or a
single 2D frame -- the "k-th 2D frame" abstraction that lets tilt series,
particle stacks, and 2D micrographs share this module later (see CLAUDE.md
Scope): only ``central_frame_indices`` encodes tomogram-specific judgment
about which frames are worth previewing.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import mrcfile
import numpy as np


class _MmapCache:
    """Bounded LRU of open mmap handles, so scrubbing through a tomogram's
    frames doesn't reopen and re-parse the header on every request."""

    def __init__(self, max_open: int = 8):
        self._max_open = max_open
        self._handles: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, path: Path) -> Any:
        key = str(path)
        with self._lock:
            mrc = self._handles.get(key)
            if mrc is not None:
                self._handles.move_to_end(key)
                return mrc
            mrc = mrcfile.mmap(key, mode="r", permissive=True)
            self._handles[key] = mrc
            if len(self._handles) > self._max_open:
                _, oldest = self._handles.popitem(last=False)
                oldest.close()
            return mrc

    def close_all(self) -> None:
        with self._lock:
            for mrc in self._handles.values():
                mrc.close()
            self._handles.clear()


_mmap_cache = _MmapCache()


def close_all_handles() -> None:
    """Release every cached mmap handle. Call on server shutdown."""
    _mmap_cache.close_all()


def get_shape(path: Path) -> tuple[int, int, int]:
    """(n_frames, height, width). A 2D file reports n_frames=1."""
    mrc = _mmap_cache.get(path)
    data = mrc.data
    if data.ndim == 2:
        return (1, data.shape[0], data.shape[1])
    return (data.shape[0], data.shape[1], data.shape[2])


def read_frame(path: Path, index: int) -> np.ndarray:
    """Read a single 2D frame by index. Only that frame is paged in from disk."""
    mrc = _mmap_cache.get(path)
    data = mrc.data
    if data.ndim == 2:
        if index != 0:
            raise IndexError(f"single-frame file {path} has no frame {index}")
        return np.asarray(data)
    return np.asarray(data[index])


def central_frame_indices(
    n_frames: int, n_previews: int = 20, central_fraction: float = 0.5
) -> list[int]:
    """Evenly spaced frame indices drawn from the central band of the stack.

    Tomogram top/bottom slices are reconstruction junk and tell you nothing
    about quality (see CLAUDE.md Preview generation).
    """
    if n_frames <= 0:
        return []
    if n_frames == 1:
        return [0]
    n_previews = max(1, min(n_previews, n_frames))
    margin = (1 - central_fraction) / 2
    lo = int(round(n_frames * margin))
    hi = int(round(n_frames * (1 - margin)))
    hi = max(hi, lo + 1)
    hi = min(hi, n_frames - 1)
    lo = min(lo, hi)
    if n_previews == 1:
        return [(lo + hi) // 2]
    step = (hi - lo) / (n_previews - 1)
    indices = sorted({min(n_frames - 1, lo + round(i * step)) for i in range(n_previews)})
    return indices
