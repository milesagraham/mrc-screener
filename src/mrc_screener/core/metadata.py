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


def scan_mrc_dir(directory: Path, pattern: str = "*.mrc") -> list[Path]:
    return sorted(Path(directory).rglob(pattern))


def rows_to_paths(rows: Sequence[RowRef]) -> list[str]:
    return [r.reference for r in rows]
