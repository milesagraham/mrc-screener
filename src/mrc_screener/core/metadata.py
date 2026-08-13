"""Read the plain-text row-list input format (index<TAB>path per line)."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .matching import RowRef


def parse_txt_list(path: Path) -> list[RowRef]:
    """Parse an ``index<TAB>path`` list, e.g. ``test_materials/input_list.txt``.

    Blank lines (including trailing ones) are skipped. A line with no tab is
    treated as a bare reference, row-numbered by its position.
    """
    rows: list[RowRef] = []
    for lineno, line in enumerate(Path(path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0].strip():
            row_id, reference = parts[0].strip(), parts[1].strip()
        else:
            row_id, reference = str(lineno), line.strip()
        if reference:
            rows.append(RowRef(row_id=row_id, reference=reference))
    return rows


def scan_mrc_dir(directory: Path, pattern: str = "*.mrc", recursive: bool = False) -> list[Path]:
    """Find MRC files directly in ``directory``.

    Non-recursive by default: reconstruction directories often hold a
    subdirectory of alternate copies of every tomogram under identical
    filenames -- e.g. ``deconv/`` (CTF-deconvolved) alongside the plain
    reconstruction one level up. Scanning only the directory the caller
    actually pointed at avoids treating those as ambiguous duplicates by
    default. Pass ``recursive=True`` for layouts that genuinely need it
    (e.g. one subdirectory per tomogram).
    """
    files = Path(directory).rglob(pattern) if recursive else Path(directory).glob(pattern)
    return sorted(files)


def rows_to_paths(rows: Sequence[RowRef]) -> list[str]:
    return [r.reference for r in rows]
