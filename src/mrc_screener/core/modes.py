"""Per-data-type preview strategies: which frames to show, in what order,
how to label them, and which frames to sample for the contrast window.

This is the "new data type is a new strategy, not a rewrite" seam from
CLAUDE.md Scope. ``io.py`` knows how to read the k-th 2D frame of any MRC;
this module decides *which* k's are worth looking at for a given data type.

- Tomogram: N evenly spaced Z slices from the central band (top/bottom of a
  reconstruction are junk).
- Tilt series: every tilt, so the full tilt range is visible. When tilt
  angles can be found in a sidecar file (IMOD ``.tlt``/``.rawtlt`` or
  SerialEM ``.mdoc``) frames are ordered by angle and labelled with it;
  otherwise they are shown in file order and the UI says so -- we never
  invent angles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .io import central_frame_indices, get_shape


class DataMode(str, Enum):
    TOMOGRAM = "tomogram"
    TILT_SERIES = "tiltseries"


@dataclass(frozen=True)
class PreviewPlan:
    """Everything a front end needs to page through one file.

    ``frame_indices[pos]`` is the file frame shown at scrub position ``pos``;
    ``labels[pos]`` is its human-readable label. ``start_pos`` is where to
    open (central slice / tilt nearest 0 deg). ``contrast_fraction`` is the
    central fraction of the stack sampled for the contrast window. ``note``
    is anything the user should be told about how this plan was built.
    """

    mode: DataMode
    n_frames: int
    frame_indices: list[int]
    labels: list[str]
    start_pos: int
    contrast_fraction: float
    tilt_angles: list[float] | None = None  # aligned with frame_indices
    angle_source: str | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Tilt-angle sidecars
# ---------------------------------------------------------------------------


def parse_tlt(path: Path) -> list[float]:
    """IMOD ``.tlt``/``.rawtlt``: one angle per line, in stack order."""
    angles: list[float] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            angles.append(float(line.split()[0]))
    return angles


_ZVALUE_RE = re.compile(r"^\s*\[\s*ZValue\s*=\s*(\d+)\s*\]", re.IGNORECASE)
_TILT_RE = re.compile(r"^\s*TiltAngle\s*=\s*(\S+)", re.IGNORECASE)


def parse_mdoc(path: Path) -> list[float]:
    """SerialEM ``.mdoc``: ``TiltAngle`` per ``[ZValue = k]`` section.

    Returned in ZValue (i.e. stack frame) order. Raises ``ValueError`` if a
    section lacks an angle or ZValues are not exactly 0..n-1 -- a partial
    angle list would mislabel frames.
    """
    by_z: dict[int, float] = {}
    current: int | None = None
    for line in Path(path).read_text(errors="replace").splitlines():
        m = _ZVALUE_RE.match(line)
        if m:
            current = int(m.group(1))
            continue
        if line.lstrip().startswith("["):
            current = None  # some other section, e.g. [T = ...]
            continue
        m = _TILT_RE.match(line)
        if m and current is not None and current not in by_z:
            by_z[current] = float(m.group(1))
    if not by_z:
        raise ValueError(f"{path}: no [ZValue] sections with TiltAngle")
    n = max(by_z) + 1
    if sorted(by_z) != list(range(n)):
        raise ValueError(f"{path}: ZValue sections are not contiguous 0..{n - 1}")
    return [by_z[i] for i in range(n)]


def tilt_sidecar_candidates(path: Path) -> list[Path]:
    """Sidecars that may carry angles for ``path``, in preference order.

    ``TS_01.st`` -> ``TS_01.tlt``, ``TS_01.rawtlt``, ``TS_01.st.mdoc``,
    ``TS_01.mdoc``. ``.tlt`` first: refined angles from alignment.
    """
    path = Path(path)
    stem = path.with_suffix("")
    return [
        stem.with_name(stem.name + ".tlt"),
        stem.with_name(stem.name + ".rawtlt"),
        path.with_name(path.name + ".mdoc"),
        stem.with_name(stem.name + ".mdoc"),
    ]


def find_tilt_angles(path: Path, n_frames: int) -> tuple[list[float] | None, str | None, list[str]]:
    """Look for a sidecar whose angle count matches ``n_frames``.

    Returns ``(angles, source_filename, problems)``. ``problems`` lists
    sidecars that exist but were rejected (unparseable, wrong count), so the
    UI can say why angles are missing instead of silently showing none.
    """
    problems: list[str] = []
    for cand in tilt_sidecar_candidates(path):
        if not cand.is_file():
            continue
        try:
            angles = parse_mdoc(cand) if cand.suffix.lower() == ".mdoc" else parse_tlt(cand)
        except (ValueError, OSError) as exc:
            problems.append(f"{cand.name}: unreadable ({exc})")
            continue
        if len(angles) != n_frames:
            problems.append(f"{cand.name}: {len(angles)} angles but stack has {n_frames} frames")
            continue
        return angles, cand.name, problems
    return None, None, problems


def format_angle(angle: float) -> str:
    sign = "−" if angle < 0 else "+" if angle > 0 else "±"
    return f"{sign}{abs(angle):.1f}°"


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


def plan_tomogram(n_frames: int, n_previews: int = 20, central_fraction: float = 0.5) -> PreviewPlan:
    indices = central_frame_indices(n_frames, n_previews, central_fraction)
    return PreviewPlan(
        mode=DataMode.TOMOGRAM,
        n_frames=n_frames,
        frame_indices=indices,
        labels=[f"z {i}" for i in indices],
        start_pos=len(indices) // 2,
        contrast_fraction=central_fraction,
    )


def plan_tilt_series(n_frames: int, angles: list[float] | None = None) -> PreviewPlan:
    """Every frame. Sorted by angle (ascending) when angles are known."""
    if n_frames <= 0:
        return PreviewPlan(DataMode.TILT_SERIES, n_frames, [], [], 0, 1.0)
    if angles is not None:
        if len(angles) != n_frames:
            raise ValueError(f"{len(angles)} angles for {n_frames} frames")
        order = sorted(range(n_frames), key=lambda i: (angles[i], i))
        sorted_angles = [angles[i] for i in order]
        start = min(range(n_frames), key=lambda p: (abs(sorted_angles[p]), p))
        return PreviewPlan(
            mode=DataMode.TILT_SERIES,
            n_frames=n_frames,
            frame_indices=order,
            labels=[f"{format_angle(a)} (frame {i})" for a, i in zip(sorted_angles, order)],
            start_pos=start,
            contrast_fraction=1.0,
            tilt_angles=sorted_angles,
        )
    return PreviewPlan(
        mode=DataMode.TILT_SERIES,
        n_frames=n_frames,
        frame_indices=list(range(n_frames)),
        labels=[f"frame {i}" for i in range(n_frames)],
        start_pos=n_frames // 2,
        contrast_fraction=1.0,
    )


def plan_previews(
    path: Path,
    mode: DataMode,
    n_previews: int = 20,
    central_fraction: float = 0.5,
) -> PreviewPlan:
    """Build the preview plan for one file. Reads only the header (and, for
    tilt series, small sidecar text files)."""
    n_frames, _, _ = get_shape(Path(path))
    if mode is DataMode.TOMOGRAM:
        return plan_tomogram(n_frames, n_previews, central_fraction)
    angles, source, problems = find_tilt_angles(Path(path), n_frames)
    plan = plan_tilt_series(n_frames, angles)
    if angles is not None:
        note = f"angles from {source}"
        if problems:
            note += "; ignored " + "; ".join(problems)
    else:
        note = "no tilt angles found (.tlt/.rawtlt/.mdoc) -- frames in file order"
        if problems:
            note += "; " + "; ".join(problems)
    return PreviewPlan(**{**plan.__dict__, "angle_source": source, "note": note})
