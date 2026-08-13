"""Command-line entry point.

    mrc-screener serve --txt input_list.txt --mrc-dir /path/to/tomograms
    mrc-screener export --txt input_list.txt --mrc-dir /path/to/tomograms

``serve`` runs the browser UI (see CLAUDE.md: intended to run on the cluster,
viewed locally over ``ssh -L``). ``export`` regenerates ``_screened.txt`` /
``_kept.txt`` from an existing decision journal without starting a server --
useful after a session, or from a batch script.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.matching import MatchConfig, load_overrides, match_rows
from .core.metadata import parse_txt_list, scan_mrc_dir
from .core.session import Session, write_outputs


def _build_match_config(args: argparse.Namespace) -> MatchConfig:
    overrides = load_overrides(Path(args.overrides)) if args.overrides else {}
    return MatchConfig(
        extra_suffix_patterns=tuple(args.extra_suffix or ()),
        extra_prefix_patterns=tuple(args.extra_prefix or ()),
        disable_default_patterns=args.disable_default_patterns,
        manual_overrides=overrides,
    )


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--txt", required=True, type=Path, help="index<TAB>path row list, e.g. input_list.txt")
    p.add_argument("--mrc-dir", required=True, type=Path, help="directory to scan for MRC files")
    p.add_argument("--out-stem", type=Path, default=None, help="output path stem (default: --txt without extension)")
    p.add_argument("--overrides", type=Path, default=None, help="TSV/CSV of row-reference -> explicit filename")
    p.add_argument("--extra-suffix", action="append", metavar="REGEX", help="additional suffix pattern to strip (repeatable)")
    p.add_argument("--extra-prefix", action="append", metavar="REGEX", help="additional prefix pattern to strip (repeatable)")
    p.add_argument("--disable-default-patterns", action="store_true", help="don't apply the built-in suffix/prefix patterns")
    p.add_argument(
        "--recursive",
        action="store_true",
        help="scan --mrc-dir recursively (default: only the directory itself, not subdirectories -- "
        "avoids treating alternate-copy subdirectories like deconv/ as ambiguous duplicates)",
    )


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server.app import ServerConfig, create_app

    config = ServerConfig(
        txt_path=args.txt,
        mrc_dir=args.mrc_dir,
        out_stem=args.out_stem,
        recursive_scan=args.recursive,
        match_config=_build_match_config(args),
    )
    app = create_app(config)
    print(f"journal: {config.journal_path}")
    print(f"serving on http://127.0.0.1:{args.port} -- tunnel with:")
    print(f"  ssh -L {args.port}:localhost:{args.port} <this host>")
    uvicorn.run(app, host="127.0.0.1", port=args.port)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    rows = parse_txt_list(args.txt)
    mrc_files = scan_mrc_dir(args.mrc_dir, recursive=args.recursive)
    report = match_rows(rows, mrc_files, _build_match_config(args))
    match_by_row = {m.row.row_id: m for m in report.matches}

    out_stem = args.out_stem or args.txt.with_suffix("")
    journal_path = out_stem.parent / f"{out_stem.name}_decisions.jsonl"
    if not journal_path.exists():
        print(f"no journal at {journal_path} -- nothing decided yet", file=sys.stderr)

    session = Session(journal_path=journal_path)
    session.register_rows(report.matches)

    screened_path, kept_path = write_outputs(rows, match_by_row, session, out_stem)
    print(report.summary())
    print(f"wrote {screened_path}")
    print(f"wrote {kept_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mrc-screener")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="run the screening server")
    _add_common_args(serve_p)
    serve_p.add_argument("--port", type=int, default=8000)
    serve_p.set_defaults(func=cmd_serve)

    export_p = sub.add_parser("export", help="regenerate outputs from the decision journal")
    _add_common_args(export_p)
    export_p.set_defaults(func=cmd_export)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
