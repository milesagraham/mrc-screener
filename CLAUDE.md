# mrc_screener

Fast triage of cryo-ET MRC files. The user opens hundreds of tomograms and decides
keep/reject in ~1 second each. Today the *opening* costs more than the decision.
Everything in this project exists to make the decision the slow part again.

## The one-line pitch

Point the tool at a STAR/TXT file and a directory of MRCs. It matches rows to files,
serves cheap binned previews in a browser, and records keep/reject decisions
non-destructively as you press keys.

## Stack

- Python 3.10+ (conda env `mrc-screener`, editable-installed via `pip install -e .`; the cluster is the real target)
- `mrcfile` — MRC I/O, **memory-mapped only** (see Hard Rules)
- `starfile` — RELION STAR read/write
- `numpy` — slice math
- `FastAPI` + `uvicorn` — preview server
- Vanilla JS + `<canvas>` — client. No framework, no build step, no CDN.
- `pytest` — tests

Deliberately **not** used: napari, PyQt, matplotlib, dask, any JS bundler.

## How it runs

The tool runs *on the cluster*, where the data is. The user views it locally:

```bash
# on the cluster
mrc-screener serve --star particles.star --mrc-dir /path/to/tomograms --port 8000

# on the laptop
ssh -L 8000:localhost:8000 cluster
# then open http://localhost:8000
```

Never assume a display, an X server, or a GUI toolkit is available. Never assume the
MRCs can be copied to the client — they are gigabytes each and there are hundreds.

## Hard rules

These are the rules that make or break the project. Violating any of them defeats the
entire point of the tool.

### 1. Never read a whole MRC into memory

Always `mrcfile.mmap(path, permissive=True)` and index into `.data` to pull individual
slices. A tomogram is 2–30 GB; the user needs ~2 MB of it. A single `mrcfile.open()`
on a hot path is a bug, not a style issue.

Bin by strided slicing plus a reduction (`arr[::s, ::s]` for speed, block-mean when
quality matters), never by loading full resolution and downsampling after.

### 2. Never mutate the user's input files

The input STAR/TXT is read-only, always. Outputs go to new paths:

- `<stem>_screened.star` — every input row preserved, plus a selection column
- `<stem>_kept.star` — kept rows only, ready for downstream
- `<stem>_decisions.jsonl` — append-only journal, one line per decision

Write the journal line **before** acknowledging the keystroke in the UI. A crash must
never lose decisions, and relaunching must resume exactly where it stopped.

### 3. Filename matching must never silently guess

STAR rows and MRC filenames routinely disagree (`_rec`, `_bin4`, `_dose-filt`,
`.tomostar` vs `.mrc`, differing directories). Matching is tiered and every tier is
reported:

1. Exact stem match
2. Normalized stem match (strip known extensions and suffixes, see `core/matching.py`)
3. Case-insensitive normalized match
4. Unique substring containment
5. **Unmatched / ambiguous → surfaced to the user, never auto-resolved**

A row that matches two files, or a file that matches two rows, is an error condition.
Print a match report and require confirmation before screening starts. A silent
mismatch corrupts the user's dataset selection in a way they cannot detect later —
this is the highest-severity failure mode in the project.

### 4. Latency budget

Advancing to the next tomogram must feel instant. Target < 100 ms from keypress to
rendered image. Achieve it by prefetching: while the user views file N, a background
worker pool decodes N+1 … N+3. Cache previews on disk keyed by
`(path, mtime, size, bin, n_slices)` so a second pass over a dataset is free.

## Preview generation

Defaults, all overridable:

- 20 evenly-spaced Z slices drawn from the **central 50%** of the volume — the top and
  bottom of a tomogram are reconstruction junk and tell you nothing about quality
- Binned so the long axis is ≤ 512 px
- Encoded as JPEG quality 85

Contrast normalization: compute the 2nd/98th percentile **once per tomogram** from a
sample of slices, then apply that single window to every slice. Per-slice
normalization makes brightness flicker as you scroll, which actively hurts the user's
ability to judge quality.

## Layout

```
src/mrc_screener/
  core/            # UI-agnostic. No FastAPI or HTTP imports may appear here.
    io.py          # mmap access, slice extraction, binning
    preview.py     # slice → normalized 8-bit → JPEG, disk cache
    matching.py    # STAR/TXT row ↔ MRC filename resolution
    metadata.py    # STAR/TXT parsing and writing
    session.py     # decision journal, resume logic
  server/
    app.py         # FastAPI routes
    static/        # index.html, app.js, style.css
  cli.py           # entry point
tests/
```

`core/` must stay importable without a server running and without any web dependency.
It is the seam that lets a second front end exist later — do not leak HTTP concepts
into it.

## Scope

**Tomograms first.** The user also has tilt series, 2D micrographs, and particle
stacks, and will want those later. Design `core/io.py` and `core/preview.py` around a
"give me the k-th 2D frame of this file" abstraction so a new data type is a new
strategy, not a rewrite. Do not build the other types until asked.

## UI conventions

Keyboard-first; the mouse is optional. Bindings:

| Key | Action |
|---|---|
| `→` / `l` | keep, advance |
| `←` / `h` | reject, advance |
| `↑` / `↓` | scrub Z slices |
| `u` | undo last decision |
| `space` | skip without deciding |
| `?` | show bindings |

Always show position (`47 / 312`), running keep/reject counts, and the current
filename. The user needs to know how much is left without asking.

## Commands

```bash
conda activate mrc-screener            # or: conda create -n mrc-screener python=3.10 && conda activate mrc-screener
pip install -e ".[dev]"                # install
pytest                                 # tests
mrc-screener serve --help              # run
```

## Working conventions

- Type hints on all public functions in `core/`.
- Tests for `core/matching.py` are non-negotiable and must cover the real-world messy
  cases: suffix chains, extension mismatches, ambiguity, and no-match.
- Generate synthetic MRCs in tests via `mrcfile`; don't add new binary test fixtures.
  `test_materials/` is the one standing exception — real user-supplied data (a
  mismatched STAR/TXT/MRC triple) used specifically to test filename matching against
  a genuine naming convention, not a fixture we generated. Match tests should use it
  alongside synthetic cases, not instead of them.
- Prefer small pure functions in `core/` that take arrays and paths, so they can be
  tested without a server or a browser.
- When something is ambiguous about the user's data, surface it in the UI rather than
  picking a default. This user is screening for quality — hidden behavior is the enemy.
