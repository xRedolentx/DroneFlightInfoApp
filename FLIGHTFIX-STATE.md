# FLIGHTFIX-STATE — durable repair state

Do not delete this file between sessions. It is the resume point for
`FlightDesk_Review_Pack/FLIGHTFIX.md`. Update it at the end of every batch,
before any long operation, and before any host-forced stop.

## Position

- Reviewed commit: `7806e1f554a3959672c4bd15dd4b6e52638d0fc0`
- Actual starting commit: `7806e1f` (identical — no work landed between review and start)
- Starting branch: `main` (tracking `origin/main`, clean)
- Repair branch: `flightfix/repairs` (created from `7806e1f`)
- Remote: `origin` → `git@github.com:xRedolentx/DroneFlightInfoApp.git`; `main` is the GitHub default branch
- Starting dirty paths: `FlightDesk_Review_Pack/` (untracked — the supplied review pack)
- State file created: 2026-11-23, batch P00

## Environment and installed capabilities

Measured 2026-11-23 22:56 UTC on the owner's machine. Aggregate status only;
no private records were read.

- OS: Windows 11 `10.0.26300` (AMD64)
- Python: `3.12.10` at `C:\Users\raven\AppData\Local\Programs\Python\Python312\python.exe`
- SQLite: `3.49.1` (CPython bundled)
- Windows PowerShell: `5.1.26100.9818`
- Pinned parser: `dji-log-cli 0.5.7` present at
  `%LOCALAPPDATA%\FlightDesk\vendor\dji-log\0.5.7\dji-log.exe`
- Parser `MANIFEST.json`: **absent** (`paths.parser_manifest()` does not exist).
  `CHECKPOINT.md` says "with a manifest"; that is stale. Checksum verification
  is therefore unproven — carried into P13.
- Keychain shim: `shim/target/release/flightdesk-decode.exe` built and present;
  `cargo`/`rustc` available at `C:\Users\raven\.cargo\bin`
- Runtime dependencies: standard library only (`requirements.txt`)
- Node/Edge for the browser gate: assumed present (the prior session used them);
  not re-measured in P00

### Private roots — hard boundary

- The owner's **live data root exists**: `paths.default_data_root()` →
  `%LOCALAPPDATA%\FlightDesk\data`. Do not inspect its contents, and never run
  seed / repair / reparse-apply / restore / import / migration experiments
  against it.
- All experiments in this run use temp roots (`tempfile.TemporaryDirectory`, or
  `FLIGHTDESK_DATA_ROOT` / `FLIGHTDESK_HOME` overrides pointing at a throwaway
  directory). The test suite already does this (`tests/support.py:TempRoot`,
  `tests/test_launcher.py` redirects both env vars).
- `%LOCALAPPDATA%\FlightDesk\realcheck\` holds the owner's one real log — private,
  read only by `tools/real_file_check.py`, never by the agent.

## Baseline (measured, not remembered)

- `python -m flightdesk selftest` → **407 tests, 0 failures, 0 errors, 0 skipped**
  in 46.0 s (exit 0), Windows 11 / Python 3.12.10 **with** the pinned parser and
  shim installed. One benign `zipfile` duplicate-name `UserWarning` from a
  synthetic fixture.
- The reviewer's Linux result (407 tests / 61 failures / 19 errors) did **not**
  reproduce here. That difference is the missing parser/DPAPI integration on
  Linux, not a list of app defects — see the "Known baseline failures" section.
- `python FlightDesk_Review_Pack/evidence/review_probes.py .` → **19/19 probes
  reproduced** the reviewed baseline behaviour, matching
  `evidence/review_probe_results.json` on every probe except one cosmetic
  difference: `demo_seed_mutates_existing_history` recorded tags `[]` rather
  than `["mapping"]`, and the pilot was still overwritten to `Demo Pilot`.
  The overwrite defect itself reproduces.
- Probes are **baseline evidence, not regression tests**. Each repaired finding
  gets its own assertion-based test in its batch.

## Current batch / next runnable batch

- Current batch: **P01 — DONE**
- Next runnable batch: **P02 — Non-overwriting exports and archive integrity**
  (independent after P00)

## Batch table

Statuses: `TODO` `IN_PROGRESS` `DONE` `PARTIAL` `BLOCKED` `WAITING_DEPENDENCY` `ALREADY_FIXED`

| ID | Status | Changed files | Tests / result | Commit | Blockers |
|---|---|---|---|---|---|
| P00 | DONE | `FLIGHTFIX-STATE.md`, review pack, `FlightDesk_Review_Pack/FLIGHTFIX-HUMAN.md` (owner-decision merge) | baseline selftest 407/0/0; probes 19/19 reproduced | `ebe0706` | none |
| P01 | DONE | `flightdesk/demo.py`, `importer.py`, `db.py`, `views.py`, `server.py`, `__main__.py`, `web/app.js`, `OWNER-GUIDE.md`, `tests/test_demo_isolation.py`, `tests/test_stage3_web.py` | selftest **418 / 0 failures / 0 errors**; F01 probe now reports `pilot="Owner pilot", tags=["Owner tag"]` unchanged | (this checkpoint) | none |
| P02 | TODO | — | — | — | none |
| P03 | TODO | — | — | — | none |
| P04 | TODO | — | — | — | after P03 |
| P05 | TODO | — | — | — | after P04 |
| P06 | TODO | — | — | — | after P05 |
| P07 | TODO | — | — | — | after P06 |
| P08 | TODO | — | — | — | after P05/P06/P07 |
| P09 | TODO | — | — | — | none |
| P10 | TODO | — | — | — | after P02, P09 |
| P11 | TODO | — | — | — | after P05/P06/P07 |
| P12 | TODO | — | — | — | after P01, P09; identity labels after P11 |
| P13 | TODO | — | — | — | none |
| P14 | TODO | — | — | — | all runnable batches addressed |
| H02 | OPEN | — | — | — | owner runs Windows/parser suite on repaired commit |

## Attempt ledger

One row per problem. An attempt = a distinct hypothesis plus a test/change.
Three attempts without material progress stops that problem. Counts survive
compaction and restarts.

| Problem | Attempts since progress | Hypotheses / results | Progress evidence |
|---|---|---|---|
| F01 demo seeding contaminated owner records | 0 (fixed on first attempt) | Hypothesis: the seed's guard is `demo_seeded` only, so any populated root is eligible; the tag/pilot update selects all flights. Change: atomic empty-root claim inside `BEGIN IMMEDIATE`, synthetic marker set before any write, serials-by-content scoping of pilot/tag edits, and a backend import refusal in a synthetic root. Result: 11 new tests pass; F01 probe shows the owner's pilot and tags unchanged. | Probe output before `["mapping"]`/`Demo Pilot`, after `["Owner tag"]`/`Owner pilot`; 418-test suite green |

## Behaviour changes landed (keep docs in step)

- **P01** — A data root is now `empty`, `unknown`, `real` or `synthetic`
  (`db.data_mode`). Demo seeding is offered only on an empty root and is claimed
  atomically inside `BEGIN IMMEDIATE`; the root is marked `data_mode=synthetic`
  before the first write, so a crashed seed is a resumable demo root, never a
  half-seeded ordinary one. A real log import into a synthetic root returns
  `note=demo_root_requires_separate_root` and writes nothing (HTTP 409 from the
  upload/path/demo routes). Demo pilot/tag edits are scoped to the flights whose
  originals are the exact bytes the seed wrote. `OWNER-GUIDE.md` explains the
  separate-root workflow. Tests moved to `ImportApiTest` (non-demo root) because
  importing into a demo root is now refused by design.

## Pending human IDs

Canonical file: `FlightDesk_Review_Pack/FLIGHTFIX-HUMAN.md`.
Owner decisions already on record (merged from `CHECKPOINT.md`, not to be
re-asked):

- **H01** — no approval for historical-data repair. Do not apply to the live
  root. Preview only, after P08 and an independent restore check.
- **H02** — Windows/parser integration: the P00 baseline above partly answers
  the "can it run at all" half; the repaired-commit run is still owed.
- **H04** — restore: the owner **deferred** the browser restore on 2026-11-22
  (rehearsal passed; the click is theirs). Keep AirData active; do not cancel.
- **H05** — `realcheck/` holds exactly **one** real log (Air 3S, 2026-08-11,
  302.2 s). 5–10 representative logs still needed.
- **H08** — visual parity vs the brief's screenshots: not done; screenshots in
  `Screenshots/` are git-ignored and private.
- **H09** — two unreadable v11 records exist (flights 216 and 283). Flight 283
  was reported to exist **only on a controller**. Confirm independent
  preservation locally; do not upload logs.
- Owner decision on record: the `Bat-Mavic2-3402QS` completed service
  (2026-11-19) is real — never "fix" it by deleting the row.

## Known baseline failures and their environment

- No failures on this machine at `7806e1f`: 407/407 pass with the parser and
  shim installed.
- The reviewer's 61 failures / 19 errors were on Linux, Python 3.12, without the
  Windows parser or DPAPI. They are **integration prerequisites**, not 80 app
  defects. P13 turns this distinction into named skip categories and a failing
  release-readiness result when a mandatory integration is missing.
- Stale documentation found in P00 (to correct in P13/P14, not now):
  `CHECKPOINT.md` claims the parser ships "with a manifest" — no
  `MANIFEST.json` is on disk; `CHECKPOINT.md` also claims "no remote and nothing
  pushed" — a remote now exists and `main` is published.

## Resume command and smallest next action

```powershell
cd "C:\Users\raven\Desktop\Deepseek\Air-Data-Replace-Build"
git switch flightfix/repairs
python -m flightdesk selftest      # expect 418 tests, 0 failures at P01
```

Smallest next action: read P02 in `FlightDesk_Review_Pack/FLIGHTFIX.md` plus
findings F02–F03, then fix `flightdesk/rawexport.py` (collision-safe allocator,
no-clobber publication, atomic `Job.start`) and `flightdesk/archive.py::store_stream`
(hash verification, quarantine on same-size corruption), with tests in
`tests/test_stage18_raw_export.py` and the archive tests.

If the host forces a stop: verify `git status` is clean on `flightfix/repairs`,
push the branch, then resume at P02.
