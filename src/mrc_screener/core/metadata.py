"""Read the plain-text row-list input format (index<TAB>path per line)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

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


DEFAULT_SCAN_EXTENSIONS: tuple[str, ...] = (".mrc",)


def normalize_extensions(extensions: Iterable[str]) -> tuple[str, ...]:
    """Lower-case, ensure a leading dot, de-duplicate preserving order.

    ``"st"``, ``".ST"`` and ``".st"`` all mean the same thing on the command
    line; an empty string is rejected rather than silently matching every file.
    """
    out: list[str] = []
    for ext in extensions:
        e = ext.strip().lower()
        if not e or e == ".":
            raise ValueError(f"invalid extension {ext!r}")
        if not e.startswith("."):
            e = "." + e
        if e not in out:
            out.append(e)
    if not out:
        raise ValueError("at least one extension is required")
    return tuple(out)


def scan_mrc_dir(
    directory: Path,
    extensions: Iterable[str] = DEFAULT_SCAN_EXTENSIONS,
    recursive: bool = False,
) -> list[Path]:
    """Find candidate image files directly in ``directory``.

    ``extensions`` selects which files count (default ``.mrc`` only). IMOD
    ``.st``/``.ali`` stacks are plain MRC files under another name; they are
    opt-in because a typical IMOD directory holds ``TS_01.st``, ``TS_01.ali``
    and ``TS_01_rec.mrc`` side by side, and scanning all of them would make
    every row ambiguous. Extension matching is case-insensitive.

    Non-recursive by default: reconstruction directories often hold a
    subdirectory of alternate copies of every tomogram under identical
    filenames -- e.g. ``deconv/`` (CTF-deconvolved) alongside the plain
    reconstruction one level up. Scanning only the directory the caller
    actually pointed at avoids treating those as ambiguous duplicates by
    default. Pass ``recursive=True`` for layouts that genuinely need it
    (e.g. one subdirectory per tomogram).
    """
    exts = normalize_extensions(extensions)
    entries = Path(directory).rglob("*") if recursive else Path(directory).glob("*")
    return sorted(p for p in entries if p.name.lower().endswith(exts) and p.is_file())


def rows_to_paths(rows: Sequence[RowRef]) -> list[str]:
    return [r.reference for r in rows]
