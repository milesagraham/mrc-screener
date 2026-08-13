"""FastAPI server: resolves rows to files once at startup, then serves
preview JPEGs and records keep/reject decisions.

This is the only module in the project allowed to import FastAPI/Starlette
(CLAUDE.md: ``core/`` stays UI-agnostic). Route handlers that touch mmap'd
files or PIL are plain ``def``, not ``async def`` -- Starlette dispatches
synchronous handlers to its threadpool automatically, which is what keeps a
slow decode from blocking every other client's requests.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..core import io as frames
from ..core.matching import MatchConfig, MatchReport, RowMatch, match_rows
from ..core.metadata import parse_txt_list, scan_mrc_dir
from ..core.preview import PreviewCache, get_preview_jpeg
from ..core.session import Decision, Session, write_outputs

STATIC_DIR = Path(__file__).parent / "static"


@dataclass
class ServerConfig:
    txt_path: Path
    mrc_dir: Path
    out_stem: Path | None = None
    preview_cache_dir: Path | None = None
    match_config: MatchConfig = field(default_factory=MatchConfig)
    n_previews: int = 20
    central_fraction: float = 0.5
    target_long_axis: int = 512
    jpeg_quality: int = 85

    def __post_init__(self) -> None:
        self.txt_path = Path(self.txt_path)
        self.mrc_dir = Path(self.mrc_dir)
        if self.out_stem is None:
            self.out_stem = self.txt_path.with_suffix("")
        else:
            self.out_stem = Path(self.out_stem)
        if self.preview_cache_dir is None:
            self.preview_cache_dir = self.out_stem.parent / f".{self.out_stem.name}_preview_cache"
        else:
            self.preview_cache_dir = Path(self.preview_cache_dir)

    @property
    def journal_path(self) -> Path:
        return self.out_stem.parent / f"{self.out_stem.name}_decisions.jsonl"


@dataclass
class RowInfo:
    n_frames: int
    preview_indices: list[int]


class DecisionBody(BaseModel):
    decision: Literal["keep", "reject", "undecided"]


def _row_summary(m: RowMatch) -> dict:
    return {
        "row_id": m.row.row_id,
        "reference": m.row.reference,
        "filename": m.matched_path.name if m.matched_path else None,
        "tier": m.tier.value,
    }


def create_app(config: ServerConfig) -> FastAPI:
    rows = parse_txt_list(config.txt_path)
    mrc_files = scan_mrc_dir(config.mrc_dir)
    report: MatchReport = match_rows(rows, mrc_files, config.match_config)

    session = Session(journal_path=config.journal_path)
    session.register_rows(report.matches)

    match_by_row = {m.row.row_id: m for m in report.matches}
    queue: list[RowMatch] = [m for m in report.matches if m.matched_path is not None]
    queue_by_row = {m.row.row_id: m for m in queue}

    preview_cache = PreviewCache(config.preview_cache_dir)
    row_info_cache: dict[str, RowInfo] = {}
    row_info_lock = threading.Lock()

    def get_row_info(m: RowMatch) -> RowInfo:
        row_id = m.row.row_id
        with row_info_lock:
            info = row_info_cache.get(row_id)
        if info is not None:
            return info
        n_frames, _, _ = frames.get_shape(m.matched_path)
        indices = frames.central_frame_indices(n_frames, config.n_previews, config.central_fraction)
        info = RowInfo(n_frames=n_frames, preview_indices=indices)
        with row_info_lock:
            row_info_cache[row_id] = info
        return info

    def require_queued(row_id: str) -> RowMatch:
        m = queue_by_row.get(row_id)
        if m is None:
            raise HTTPException(404, f"row {row_id!r} is not a screenable row")
        return m

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        frames.close_all_handles()

    app = FastAPI(title="mrc-screener", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/state")
    def get_state() -> dict:
        return {
            "queue": [
                {**_row_summary(m), "decision": session.decision_for(m.row.row_id).value} for m in queue
            ],
            "counts": session.counts(),
            "total": len(queue),
            "issues": {
                "unmatched": [_row_summary(m) for m in report.unmatched],
                "ambiguous": [
                    {**_row_summary(m), "candidates": [str(c) for c in m.candidates]} for m in report.ambiguous
                ],
                "file_collisions": [
                    {"path": str(fc.path), "rows": [r.reference for r in fc.rows]} for fc in report.file_collisions
                ],
                "unclaimed_files": [str(p) for p in report.unclaimed_files],
            },
        }

    @app.get("/api/rows/{row_id}")
    def get_row(row_id: str) -> dict:
        m = require_queued(row_id)
        info = get_row_info(m)
        return {
            **_row_summary(m),
            "decision": session.decision_for(row_id).value,
            "n_frames": info.n_frames,
            "n_previews": len(info.preview_indices),
        }

    @app.get("/api/rows/{row_id}/preview/{pos}")
    def get_preview(row_id: str, pos: int) -> Response:
        m = require_queued(row_id)
        info = get_row_info(m)
        if not (0 <= pos < len(info.preview_indices)):
            raise HTTPException(404, f"preview position {pos} out of range (0..{len(info.preview_indices) - 1})")
        frame_index = info.preview_indices[pos]
        data = get_preview_jpeg(
            m.matched_path,
            frame_index,
            preview_cache,
            target_long_axis=config.target_long_axis,
            quality=config.jpeg_quality,
        )
        return Response(content=data, media_type="image/jpeg")

    @app.post("/api/rows/{row_id}/decision")
    def post_decision(row_id: str, body: DecisionBody) -> dict:
        require_queued(row_id)
        session.decide(row_id, Decision(body.decision))
        return {"row_id": row_id, "decision": body.decision, "counts": session.counts()}

    @app.post("/api/undo")
    def post_undo() -> dict:
        reverted = session.undo_last()
        return {"row_id": reverted, "counts": session.counts()}

    @app.post("/api/export")
    def post_export() -> dict:
        screened_path, kept_path = write_outputs(rows, match_by_row, session, config.out_stem)
        return {"screened_path": str(screened_path), "kept_path": str(kept_path)}

    return app
