"""Append-only decision journal, resumable state, and output writing.

The journal is the durable source of truth (CLAUDE.md hard rule #2): every
decision is fsync'd to a JSONL file *before* the caller may acknowledge the
keystroke that produced it. ``_screened``/``_kept`` files are convenience
exports regenerated from the journal on demand -- never the record of truth
themselves, and never the input file, which is never touched.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Sequence

from .matching import RowMatch, RowRef


class Decision(str, Enum):
    KEEP = "keep"
    REJECT = "reject"
    UNDECIDED = "undecided"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Session:
    journal_path: Path
    _rows: dict[str, RowMatch] = field(default_factory=dict)
    state: dict[str, Decision] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.journal_path = Path(self.journal_path)
        if self.journal_path.exists():
            self._replay()

    def register_rows(self, matches: Sequence[RowMatch]) -> None:
        for m in matches:
            self._rows[m.row.row_id] = m

    def _replay(self) -> None:
        for line in self.journal_path.read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            self._apply(entry["row_id"], Decision(entry["decision"]))

    def _apply(self, row_id: str, decision: Decision) -> None:
        self.state[row_id] = decision
        if row_id in self._order:
            self._order.remove(row_id)
        self._order.append(row_id)

    def decision_for(self, row_id: str) -> Decision:
        return self.state.get(row_id, Decision.UNDECIDED)

    def decide(self, row_id: str, decision: Decision) -> None:
        row = self._rows.get(row_id)
        entry = {
            "ts": _now_iso(),
            "row_id": row_id,
            "reference": row.row.reference if row else None,
            "matched_path": str(row.matched_path) if row and row.matched_path else None,
            "decision": decision.value,
        }
        self._append(entry)
        self._apply(row_id, decision)

    def undo_last(self) -> str | None:
        """Revert the most recently decided row to UNDECIDED. Returns its
        row_id, or None if there is nothing to undo."""
        for row_id in reversed(self._order):
            if self.state.get(row_id) != Decision.UNDECIDED:
                self.decide(row_id, Decision.UNDECIDED)
                return row_id
        return None

    def counts(self) -> dict[str, int]:
        keep = sum(1 for d in self.state.values() if d is Decision.KEEP)
        reject = sum(1 for d in self.state.values() if d is Decision.REJECT)
        return {"keep": keep, "reject": reject}

    def _append(self, entry: dict) -> None:
        with self.journal_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())


def write_outputs(
    rows: Sequence[RowRef],
    match_by_row: dict[str, RowMatch],
    session: Session,
    out_stem: Path,
) -> tuple[Path, Path]:
    """Write ``<out_stem>_screened.txt`` (every row, with its status) and
    ``<out_stem>_kept.txt`` (kept rows only). Never touches the input file."""
    out_stem = Path(out_stem)
    screened_path = out_stem.parent / f"{out_stem.name}_screened.txt"
    kept_path = out_stem.parent / f"{out_stem.name}_kept.txt"

    screened_lines = []
    kept_lines = []
    for row in rows:
        m = match_by_row.get(row.row_id)
        if m is None or m.matched_path is None:
            status = m.tier.value if m is not None else "unmatched"
            matched = ""
        else:
            status = session.decision_for(row.row_id).value
            matched = str(m.matched_path)
        screened_lines.append(f"{row.row_id}\t{row.reference}\t{status}\t{matched}")
        if status == Decision.KEEP.value:
            kept_lines.append(f"{row.row_id}\t{row.reference}")

    screened_path.write_text("\n".join(screened_lines) + ("\n" if screened_lines else ""))
    kept_path.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""))
    return screened_path, kept_path
