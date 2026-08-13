"""Resolve STAR/TXT row references to MRC files on disk.

Row references and MRC filenames routinely diverge: different extensions
(``.tomostar`` vs ``.mrc``), injected pixel-size or processing suffixes
(``_10.00Apx``, ``_bin4``, ``_rec``), different directories. Resolution runs
through five tiers, tightest first. A tier that produces more than one
candidate is an ambiguity, not a tiebreak opportunity for a looser tier —
see :class:`MatchTier`.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Sequence


class MatchTier(str, Enum):
    OVERRIDE = "override"
    EXACT_STEM = "exact_stem"
    NORMALIZED_STEM = "normalized_stem"
    CASE_INSENSITIVE = "case_insensitive"
    SUBSTRING = "substring"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"


RESOLVED_TIERS = frozenset(
    {
        MatchTier.OVERRIDE,
        MatchTier.EXACT_STEM,
        MatchTier.NORMALIZED_STEM,
        MatchTier.CASE_INSENSITIVE,
        MatchTier.SUBSTRING,
    }
)

# Extensions treated as noise and stripped wherever they appear as the
# trailing extension, repeatedly (handles doubled extensions like
# "foo.mrc.gz" -- rare in practice, cheap to support).
DEFAULT_EXTENSIONS = frozenset(
    {".mrc", ".mrcs", ".rec", ".ali", ".st", ".tomostar", ".star", ".map"}
)

# Suffix patterns stripped from the *end* of a normalized name, iteratively,
# case-insensitively. Each is anchored with $. Kept conservative: patterns
# that could eat a meaningful trailing number (e.g. bare "_\d+") are left
# out of the defaults and are the kind of thing a caller supplies via
# extra_suffix_patterns for their own pipeline's conventions.
DEFAULT_SUFFIX_PATTERNS: tuple[str, ...] = (
    r"[_\-]\d+(?:\.\d+)?\s*apx$",  # pixel size: _10.00Apx, -3.72apx
    r"[_\-]bin\d+$",  # bin factor: _bin4
    r"[_\-](?:rec|reconstruction)$",
    r"[_\-](?:ali|aligned)$",
    r"[_\-](?:even|odd|evn)$",
    r"[_\-]dose[-_]?filt(?:ered)?$",
    r"[_\-]dw$",
    r"[_\-]flip[xyz]$",
    r"[_\-](?:ctf|ctfcorr(?:ected)?)$",
)

DEFAULT_PREFIX_PATTERNS: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatchConfig:
    extra_suffix_patterns: tuple[str, ...] = ()
    extra_prefix_patterns: tuple[str, ...] = ()
    extra_extensions: tuple[str, ...] = ()
    disable_default_patterns: bool = False
    disable_default_extensions: bool = False
    min_substring_len: int = 4
    # row reference (exact raw string from the input file) -> explicit
    # filename or path, resolved against the scanned MRC files. The escape
    # hatch for anything the automatic tiers can't or shouldn't guess.
    manual_overrides: dict[str, str] = field(default_factory=dict)

    @property
    def extensions(self) -> frozenset[str]:
        base = frozenset() if self.disable_default_extensions else DEFAULT_EXTENSIONS
        return base | {e.lower() for e in self.extra_extensions}

    @property
    def suffix_patterns(self) -> tuple[str, ...]:
        base = () if self.disable_default_patterns else DEFAULT_SUFFIX_PATTERNS
        return base + self.extra_suffix_patterns

    @property
    def prefix_patterns(self) -> tuple[str, ...]:
        base = () if self.disable_default_patterns else DEFAULT_PREFIX_PATTERNS
        return base + self.extra_prefix_patterns


@dataclass(frozen=True)
class RowRef:
    row_id: str
    reference: str


@dataclass(frozen=True)
class RowMatch:
    row: RowRef
    matched_path: Path | None
    tier: MatchTier
    candidates: tuple[Path, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class FileCollision:
    path: Path
    rows: tuple[RowRef, ...]


@dataclass(frozen=True)
class MatchReport:
    matches: tuple[RowMatch, ...]
    unclaimed_files: tuple[Path, ...]
    file_collisions: tuple[FileCollision, ...]

    @property
    def resolved(self) -> tuple[RowMatch, ...]:
        return tuple(m for m in self.matches if m.tier in RESOLVED_TIERS)

    @property
    def unmatched(self) -> tuple[RowMatch, ...]:
        return tuple(m for m in self.matches if m.tier is MatchTier.UNMATCHED)

    @property
    def ambiguous(self) -> tuple[RowMatch, ...]:
        return tuple(m for m in self.matches if m.tier is MatchTier.AMBIGUOUS)

    @property
    def is_clean(self) -> bool:
        return not self.unmatched and not self.ambiguous and not self.file_collisions

    def summary(self) -> str:
        lines = [f"{len(self.matches)} rows, {len(self.resolved)} resolved:"]
        by_tier: dict[MatchTier, int] = {}
        for m in self.resolved:
            by_tier[m.tier] = by_tier.get(m.tier, 0) + 1
        for tier, count in by_tier.items():
            lines.append(f"  {tier.value}: {count}")
        if self.ambiguous:
            lines.append(f"AMBIGUOUS ({len(self.ambiguous)}):")
            for m in self.ambiguous:
                cand = ", ".join(str(c) for c in m.candidates) or "(see note)"
                lines.append(f"  {m.row.reference!r} -> {cand}" + (f" [{m.note}]" if m.note else ""))
        if self.unmatched:
            lines.append(f"UNMATCHED ({len(self.unmatched)}):")
            for m in self.unmatched:
                lines.append(f"  {m.row.reference!r}" + (f" [{m.note}]" if m.note else ""))
        if self.file_collisions:
            lines.append(f"FILE COLLISIONS ({len(self.file_collisions)}):")
            for fc in self.file_collisions:
                rows = ", ".join(r.reference for r in fc.rows)
                lines.append(f"  {fc.path} claimed by: {rows}")
        if self.unclaimed_files:
            lines.append(f"{len(self.unclaimed_files)} scanned files matched no row (informational).")
        return "\n".join(lines)


class Matcher:
    def __init__(self, config: MatchConfig | None = None):
        self.config = config or MatchConfig()
        self._extensions = self.config.extensions
        self._suffix_res = [re.compile(p, re.IGNORECASE) for p in self.config.suffix_patterns]
        self._prefix_res = [re.compile(p, re.IGNORECASE) for p in self.config.prefix_patterns]

    def _strip_ext(self, name: str) -> str:
        changed = True
        while changed:
            changed = False
            root, ext = _splitext(name)
            if ext.lower() in self._extensions and root:
                name, changed = root, True
        return name

    def stem_only(self, raw: str) -> str:
        """Extension-stripped basename, no suffix/prefix pattern stripping. Tier 1."""
        return self._strip_ext(Path(raw).name)

    def normalize(self, raw: str) -> str:
        """Extension- and pattern-stripped basename, iterated to a fixed point."""
        name = self._strip_ext(Path(raw).name)
        changed = True
        while changed:
            changed = False
            for pat in self._suffix_res:
                new = pat.sub("", name)
                if new != name:
                    name, changed = new, True
            for pat in self._prefix_res:
                new = pat.sub("", name)
                if new != name:
                    name, changed = new, True
            new = self._strip_ext(name)
            if new != name:
                name, changed = new, True
        return name.strip("_-. ")


def _splitext(name: str) -> tuple[str, str]:
    idx = name.rfind(".")
    if idx <= 0:
        return name, ""
    return name[:idx], name[idx:]


def load_overrides(path: Path) -> dict[str, str]:
    """Parse a two-column TSV/CSV of row-reference -> explicit file path.

    Blank lines and lines starting with '#' are skipped. Delimiter is
    auto-detected as tab if present, else comma.
    """
    text = Path(path).read_text()
    overrides: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        delimiter = "\t" if "\t" in line else ","
        row = next(csv.reader([line], delimiter=delimiter))
        if len(row) < 2:
            continue
        overrides[row[0].strip()] = row[1].strip()
    return overrides


def match_rows(
    rows: Sequence[RowRef],
    mrc_files: Sequence[Path],
    config: MatchConfig | None = None,
) -> MatchReport:
    config = config or MatchConfig()
    matcher = Matcher(config)

    file_stem: dict[Path, str] = {p: matcher.stem_only(p.name) for p in mrc_files}
    file_norm: dict[Path, str] = {p: matcher.normalize(p.name) for p in mrc_files}
    by_path_str = {str(p): p for p in mrc_files}
    by_name = {p.name: p for p in mrc_files}

    by_exact: dict[str, list[Path]] = {}
    for p, s in file_stem.items():
        by_exact.setdefault(s, []).append(p)

    by_norm: dict[str, list[Path]] = {}
    by_norm_ci: dict[str, list[Path]] = {}
    for p, n in file_norm.items():
        by_norm.setdefault(n, []).append(p)
        by_norm_ci.setdefault(n.lower(), []).append(p)

    provisional: list[tuple[RowRef, Path | None, MatchTier, tuple[Path, ...], str]] = []

    for row in rows:
        override = config.manual_overrides.get(row.reference) or config.manual_overrides.get(row.row_id)
        if override is not None:
            resolved = by_path_str.get(override) or by_name.get(Path(override).name) or by_name.get(override)
            if resolved is not None:
                provisional.append((row, resolved, MatchTier.OVERRIDE, (), ""))
            else:
                provisional.append(
                    (row, None, MatchTier.UNMATCHED, (), f"override {override!r} not found among scanned files")
                )
            continue

        row_stem = matcher.stem_only(row.reference)
        candidates = by_exact.get(row_stem, [])
        tier = MatchTier.EXACT_STEM

        row_norm = matcher.normalize(row.reference)
        if not candidates:
            candidates = by_norm.get(row_norm, [])
            tier = MatchTier.NORMALIZED_STEM
        if not candidates:
            candidates = by_norm_ci.get(row_norm.lower(), [])
            tier = MatchTier.CASE_INSENSITIVE
        if not candidates:
            candidates = _substring_candidates(row_norm, file_norm, config.min_substring_len)
            tier = MatchTier.SUBSTRING

        if len(candidates) == 1:
            provisional.append((row, candidates[0], tier, (), ""))
        elif len(candidates) > 1:
            provisional.append(
                (row, None, MatchTier.AMBIGUOUS, tuple(candidates), f"{len(candidates)} files matched at {tier.value} tier")
            )
        else:
            provisional.append((row, None, MatchTier.UNMATCHED, (), "no candidate at any tier"))

    # File-side collision pass: two rows independently resolving to the same
    # file is exactly as dangerous as a row resolving to two files, and must
    # not be silently picked by insertion order.
    claim_counts: dict[Path, list[RowRef]] = {}
    for row, path, tier, _, _ in provisional:
        if path is not None:
            claim_counts.setdefault(path, []).append(row)

    collisions = tuple(
        FileCollision(path=path, rows=tuple(claimants))
        for path, claimants in claim_counts.items()
        if len(claimants) > 1
    )
    collided_paths = {fc.path for fc in collisions}

    matches: list[RowMatch] = []
    for row, path, tier, candidates, note in provisional:
        if path is not None and path in collided_paths:
            matches.append(
                RowMatch(
                    row=row,
                    matched_path=None,
                    tier=MatchTier.AMBIGUOUS,
                    candidates=(path,),
                    note="file also claimed by another row; see file_collisions",
                )
            )
        else:
            matches.append(RowMatch(row=row, matched_path=path, tier=tier, candidates=candidates, note=note))

    claimed = {m.matched_path for m in matches if m.matched_path is not None}
    unclaimed_files = tuple(p for p in mrc_files if p not in claimed)

    return MatchReport(matches=tuple(matches), unclaimed_files=unclaimed_files, file_collisions=collisions)


def _substring_candidates(row_norm: str, file_norm: dict[Path, str], min_len: int) -> list[Path]:
    if len(row_norm) < min_len:
        return []
    row_l = row_norm.lower()
    out = []
    for path, norm in file_norm.items():
        norm_l = norm.lower()
        if len(norm_l) < min_len:
            continue
        if row_l in norm_l or norm_l in row_l:
            out.append(path)
    return out
