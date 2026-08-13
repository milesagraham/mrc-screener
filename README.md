# mrc-screener

Fast, keyboard-driven triage of cryo-ET tomograms. Point it at a STAR/TXT row
list and a directory of MRCs; it resolves filename mismatches between them,
serves cheap preview slices in a browser, and records keep/reject decisions
non-destructively as you press keys.

Built for the case where opening a tomogram takes longer than deciding
whether to keep it. Everything here exists to make the *decision* the slow
part again.

## Why

- **Runs where the data lives.** A FastAPI server runs on the cluster; you
  view it in a local browser over an SSH tunnel. Only small JPEG previews
  cross the network, never the multi-gigabyte volumes.
- **Never touches your input.** Decisions are written to an append-only
  journal; `_screened.txt`/`_kept.txt` are regenerated from it on demand.
- **Never silently mismatches a row to the wrong file.** STAR/TXT row
  references and MRC filenames routinely disagree (different extensions,
  injected pixel-size or processing suffixes, different directories).
  Filename resolution is tiered, and anything ambiguous or unmatched is
  surfaced, not guessed.

See [CLAUDE.md](CLAUDE.md) for the full design rules and
[DECISIONS.md](DECISIONS.md) for the reasoning behind them.

## Install

```bash
conda create -n mrc-screener python=3.10
conda activate mrc-screener
pip install -e .
```

For running the test suite, add the dev extras: `pip install -e ".[dev]"`.

## Run

```bash
# on the machine where the data lives
mrc-screener serve --txt input_list.txt --mrc-dir /path/to/tomograms

# on your laptop
ssh -L 8000:localhost:8000 <that machine>
# then open http://localhost:8000
```

`--txt` is an `index<TAB>path` row list. Full options:

```
--txt PATH                index<TAB>path row list
--mrc-dir PATH             directory to scan for MRC files (not recursive by default)
--recursive                also scan subdirectories of --mrc-dir
--out-stem PATH            output path stem (default: --txt without extension)
--overrides PATH           TSV/CSV of row-reference -> explicit filename
--extra-suffix REGEX       additional suffix pattern to strip (repeatable)
--extra-prefix REGEX       additional prefix pattern to strip (repeatable)
--disable-default-patterns don't apply the built-in suffix/prefix patterns
--port PORT                default 8000
```

`--mrc-dir` scans only the directory you point it at, not subdirectories.
This matters: reconstruction directories often hold a subdirectory of
alternate copies of every tomogram under identical filenames — e.g.
`deconv/` (CTF-deconvolved) alongside the plain reconstruction one level up.
A recursive scan would find both and correctly report every such row as
ambiguous (two real files, same name — not a mismatch a pattern can
resolve). Scanning shallowly by default means you point `--mrc-dir` at
whichever copy you actually want to screen, and the other copy is simply
never seen. Pass `--recursive` for layouts that genuinely need it, e.g. one
subdirectory per tomogram.

Regenerate `_screened.txt`/`_kept.txt` from an existing decision journal
without starting a server:

```bash
mrc-screener export --txt input_list.txt --mrc-dir /path/to/tomograms
```

## Screening

| Key | Action |
|---|---|
| `→` / `l` | keep, advance |
| `←` / `h` | reject, advance |
| `↑` / `↓` | scrub Z slices |
| `u` | undo last decision |
| `space` | skip without deciding |
| `?` | show bindings |

Rows that don't resolve to a file (unmatched or ambiguous) are excluded from
the screening queue and shown in a banner instead — never silently dropped
or guessed.

## Filename matching

Row references and MRC filenames are resolved through five tiers, tightest
first: exact stem, normalized stem (extensions and known suffix/prefix
conventions stripped — pixel size tags, `_binN`, `_rec`, `_ali`, dose-filter,
flip axes, …), case-insensitive normalized, unique substring containment,
then unmatched. A tier producing more than one candidate is reported as
ambiguous, never resolved by falling through to a looser tier.

Three levels of control when the defaults aren't enough:

```bash
# extend the built-in patterns for your own lab's conventions
mrc-screener serve --txt ... --mrc-dir ... \
  --extra-suffix '_myLabTag$' --extra-prefix '^PROJ\d+_'

# opt out of the built-ins entirely
mrc-screener serve --txt ... --mrc-dir ... --disable-default-patterns

# explicit row -> file mapping for genuine collisions no pattern can resolve
mrc-screener serve --txt ... --mrc-dir ... --overrides overrides.tsv
```

`overrides.tsv` is two columns, tab- or comma-separated:

```
tomostar/map15_ts_028.tomostar	map15_ts_028_10.00Apx.mrc
```

## Output

Given `--txt input_list.txt` (and no `--out-stem`), a session produces:

- `input_list_decisions.jsonl` — append-only journal, one line per decision.
  The durable source of truth; everything else is regenerated from it.
- `input_list_screened.txt` — every input row, plus its status
  (`keep`/`reject`/`undecided`/`unmatched`/`ambiguous`) and resolved path.
- `input_list_kept.txt` — kept rows only.

## Layout

```
src/mrc_screener/
  core/          # UI-agnostic: matching, MRC I/O, preview generation, session/journal
  server/        # FastAPI app + static browser client (vanilla JS, no build step)
  cli.py
tests/
```

`core/` has no FastAPI/HTTP imports and is testable standalone.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

Tests use synthetic MRCs generated via `mrcfile`. A handful of tests also
exercise the pipeline against a real tomogram in `test_materials/`, which is
local research data excluded from this repository (see `.gitignore`) — those
tests skip cleanly if the directory isn't present.

## Status

Tomograms only, for now. `core/io.py`'s frame-access abstraction is meant to
extend to tilt series, 2D micrographs, and particle stacks later without a
rewrite — see the Scope section in `CLAUDE.md`.

## License

MIT — see [LICENSE](LICENSE).
