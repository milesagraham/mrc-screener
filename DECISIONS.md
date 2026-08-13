# Project decisions

Decided 2026-08-13, before any code was written. Each entry records the choice, the
alternatives considered, and why. Revisit an entry only with a reason that invalidates
its rationale.

---

## D1 — Data type: tomograms first, extensible after

**Decision.** Build for reconstructed 3D tomograms. Structure the code so tilt series,
2D micrographs, and particle stacks can be added as strategies later.

**Why.** The user has all four, but shipping one working tool beats four half-built
ones. The four differ almost entirely in *which 2D frames you want to see* and how you
label the axis — not in I/O, matching, journaling, or UI. Isolating that difference
behind a "k-th 2D frame" abstraction makes the others cheap without speculative work
now.

---

## D2 — Runs on the cluster, viewed in a local browser

**Decision.** A FastAPI server runs where the data lives. The user reaches it with
`ssh -L 8000:localhost:8000 cluster` and views it in Chrome.

**Alternatives.** A native app running locally against sshfs/NFS-mounted paths, which
drags gigabytes over the network per file. X11-forwarded Qt, which is laggy over a WAN
link. Both were rejected on latency.

**Why.** Screening a tomogram needs ~2 MB of preview, not 20 GB of volume. Keeping the
reads next to the disks and shipping only JPEGs is a ~10,000× reduction in bytes over
the wire. It also means zero install on the laptop and it works from any machine the
user can SSH from.

---

## D3 — Browser client, not PyQt

**Decision.** Vanilla JS and `<canvas>`. No framework, no build step, no CDN.

**Context.** The initial answers picked a browser deployment *and* a PyQt stack, which
are mutually exclusive. Resolved in favor of the browser.

**Why.** Once slice extraction happens server-side, the client only ever paints small
JPEGs and captures keystrokes — a job a canvas does as fast as Qt. Qt would have added
a desktop install, a packaging story, and a second process to keep in sync with the
server, buying nothing. No build step because the user should be able to edit
`app.js` on the cluster and hit refresh; no CDN because compute nodes often have no
outbound internet.

**Cost accepted.** A future desktop client would need to be written from scratch. D7
keeps that door open by forbidding web concepts in `core/`.

---

## D4 — Non-destructive output with an append-only journal

**Decision.** Never touch the input. Emit `_screened.star` (all rows plus a selection
column), `_kept.star` (kept rows only), and `_decisions.jsonl` (one line per decision,
appended before the UI acknowledges the keystroke).

**Alternatives.** In-place editing, rejected as destructive and unresumable. A
filtered-copy-only output, rejected because it discards the record of what was thrown
away. A sidecar-only output, rejected because it forces a second command before the
result is usable.

**Why.** Screening sessions get interrupted, and re-screening 300 tomograms because a
process died is exactly the pain this tool exists to remove. Writing the journal before
acknowledging the keypress makes a crash cost at most zero decisions. Keeping rejects
on record means a later "wait, why did I drop that one" is answerable.

---

## D5 — Filename matching is tiered, reported, and never silent

**Decision.** Five tiers from exact stem match down to unique substring containment;
anything unmatched or ambiguous is surfaced to the user and blocks the start of
screening until confirmed.

**Why.** This is the project's highest-severity failure mode. Every other bug is
visible — a slow load, a bad render, a crash. A wrong row-to-file mapping is *invisible*
and silently corrupts the selection the user's downstream processing depends on. The
asymmetry justifies making the tool nag rather than guess.

**Implementation, confirmed against real data.** `test_materials/` has a genuine
mismatch: `input_list.txt` references `tomostar/map15_ts_028.tomostar`, the file on
disk is `map15_ts_028_10.00Apx.mrc` — same basename, different extension, plus an
injected pixel-size tag. `core/matching.py` resolves this at tier 2 (normalized stem)
by iteratively stripping a default set of extensions (`.mrc`, `.tomostar`, `.star`, …)
and known suffix conventions (pixel size, `_binN`, `_rec`, `_ali`, dose-filter tags,
even/odd, flip axes) until the name stops changing.

**User control, three levels, cheapest first.**
1. `MatchConfig.extra_suffix_patterns` / `extra_prefix_patterns` — regexes appended to
   the defaults for a lab's own conventions.
2. `MatchConfig.disable_default_patterns` / `disable_default_extensions` — opt out of
   the built-ins entirely if they misfire on unusual data.
3. `MatchConfig.manual_overrides` — an explicit row-reference → filename map (loadable
   from a two-column TSV/CSV via `load_overrides()`), which bypasses every tier. This
   is the answer for genuine collisions — two files that legitimately normalize to the
   same stem — which no amount of pattern-tuning can disambiguate, only a human
   decision can.

**Two ambiguity directions, both fatal.** A row resolving to two files is the obvious
case. Less obvious: two rows independently resolving to the *same* file (e.g. one
matches at tier 1, another at tier 4 via substring, and they collide). Tested
explicitly (`test_file_side_collision_is_reported_for_both_rows`) because it's the
kind of bug that's easy to miss — each row's resolution looks correct in isolation.

**Deliberately conservative default set.** Bare trailing-number stripping (e.g.
`_\d+$`) was left out of the defaults because it's as likely to eat a meaningful frame
or tilt index as a junk suffix. That kind of pattern belongs in a specific pipeline's
`extra_suffix_patterns`, not in code that has to be safe for everyone's naming scheme
out of the box.

---

## D6 — Memory-mapped reads and aggressive prefetch

**Decision.** `mrcfile.mmap` everywhere; bin by strided reads; prefetch N+1…N+3 in a
background pool; cache previews on disk keyed by path, mtime, size, and preview
parameters.

**Why.** The stated problem is that opening costs more than deciding. A full read of a
20 GB tomogram to show 20 slices is roughly a thousand times more I/O than needed. The
disk cache means the second pass over a dataset — which happens constantly, since
people re-screen after changing criteria — is effectively instant.

**Target.** Under 100 ms from keypress to rendered image.

---

## D7 — `core/` is UI-agnostic

**Decision.** No FastAPI, HTTP, or web imports in `core/`. It is importable and
testable with no server running.

**Why.** It keeps the logic worth trusting — matching, journaling, STAR I/O — testable
in isolation, and it is the seam that makes D3 reversible if the browser client
disappoints.

---

## D8 — Global contrast window per tomogram

**Decision.** Sample slices once, compute a 2nd/98th percentile window, apply it to
every slice of that tomogram.

**Alternative.** Per-slice normalization, which is the more obvious implementation.

**Why.** Per-slice windowing makes brightness pulse as the user scrubs Z, which reads
as a quality artifact and defeats the judgment the tool is meant to support. A fixed
window per volume makes real density variation visible instead of hiding it.

---

## Open questions

Not blocking; resolve when the need is real.

- **Reject reasons.** Currently binary keep/reject. Tagging *why* something was
  rejected (bad ice, poor alignment, contamination) would help later analysis, but adds
  keystrokes to a workflow whose whole point is one keystroke. Defer until asked.
- **Selection column name.** `_screened.star` needs a column; `rlnSelected` is
  conventional but semantics vary by RELION version. Confirm against the user's actual
  downstream tooling before committing.
- **TXT format.** The exact shape of the plain-text inputs (one path per line? columns?
  comments?) is unknown. Inspect a real example before writing the parser.
- **Multi-user.** Assumed single user, single session. Concurrent screeners on one
  dataset would need journal locking.
