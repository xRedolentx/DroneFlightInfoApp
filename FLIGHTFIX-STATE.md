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
- Repair branch: `flightfix/repairs` (merged into `main` by fast-forward at P02; work now continues on `main` directly, per the owner's instruction to push repairs to `main`)
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

- Current batch: **P05 — DONE**
- Next runnable batch: **P06 — Atomic derived-data refresh and equipment consistency**
  (depends on P05, now met)

## Batch table

Statuses: `TODO` `IN_PROGRESS` `DONE` `PARTIAL` `BLOCKED` `WAITING_DEPENDENCY` `ALREADY_FIXED`

| ID | Status | Changed files | Tests / result | Commit | Blockers |
|---|---|---|---|---|---|
| P00 | DONE | `FLIGHTFIX-STATE.md`, review pack, `FlightDesk_Review_Pack/FLIGHTFIX-HUMAN.md` (owner-decision merge) | baseline selftest 407/0/0; probes 19/19 reproduced | `ebe0706` | none |
| P01 | DONE | `flightdesk/demo.py`, `importer.py`, `db.py`, `views.py`, `server.py`, `__main__.py`, `web/app.js`, `OWNER-GUIDE.md`, `tests/test_demo_isolation.py`, `tests/test_stage3_web.py` | selftest **418 / 0 failures / 0 errors**; F01 probe now reports `pilot="Owner pilot", tags=["Owner tag"]` unchanged | (this checkpoint) | none |
| P02 | DONE | `flightdesk/rawexport.py`, `flightdesk/archive.py`, `tests/test_stage18_raw_export.py`, `tests/test_archive_integrity.py` | selftest **430 / 0 failures**; F02 probes now return the owner's bytes in both collision cases; F03 probe `duplicate_count=0` with the corrupt object left in place | (this checkpoint) | none |
| P03 | DONE | `flightdesk/backup.py`, `tests/test_backup_completeness.py`, `tests/test_stage7_backup.py` | selftest **444 / 0 failures**; F04 probes: the missing-member probe now aborts at the restore step because restore refuses the artifact (validation_failed), which is the fix | (this checkpoint) | none |
| P04 | DONE | `flightdesk/db.py`, `flightdesk/backup.py`, `flightdesk/server.py`, `flightdesk/__main__.py`, `tools/fault_check.py`, `tests/test_restore_protection.py`, `tests/test_stage7_backup.py` | selftest **458 / 0 failures**; fault harness clean (marker kept, no half-written target, no stale staging); F05/F06 probes: the missing-original probe aborts at restore because restore now refuses the artifact | (this checkpoint) | none |
| P05 | DONE | `flightdesk/importer.py`, `flightdesk/db.py`, `flightdesk/fingerprints.py`, `tests/test_identity_conservative.py` | selftest **473 / 0 failures**; F07 probes: the missing-serial merge and the double-link reparse are both covered by tests that now expect 2 flights / one owner | (this checkpoint) | none |
| P06 | TODO | — | — | — | after P05 (met) |
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
| F01 demo seeding contaminated owner records | 0 (fixed on first attempt) | Hypothesis: the seed's guard is `demo_seeded` only; the tag/pilot update selects all flights. Change: atomic empty-root claim inside `BEGIN IMMEDIATE`, synthetic marker before any write, content-scoped pilot/tag edits, backend import refusal in a synthetic root. Result: 11 new tests pass. | Probe before `["mapping"]`/`Demo Pilot`, after `["Owner tag"]`/`Owner pilot`; 418-test suite green |
| F02 raw export overwrote destination files | 0 (fixed on first attempt) | Hypotheses: (a) one-shot suffixing collides with an occupied suffixed name; (b) `os.replace` publishes over whatever appeared; (c) skip decided by size only. Change: collision loop, validated manifest claims, hash-verified skips, unique `mkstemp` temp, no-clobber publish, containment checks, atomic `Job.start`. 8 new tests including a target planted mid-publication and concurrent exporters. | Probe bytes before `one`/`one`, after `owner changed this file`/`owner second` |
| F03 same-size archive corruption accepted | 0 (fixed on first attempt) | Hypothesis: `store_stream` compares only `st_size`. Change: hash the existing object; on mismatch quarantine both copies and raise `ArchiveCorrupt` without touching the archive. 4 new tests. | Probe `duplicate_count` 1 -> 0; corrupt bytes unchanged |
| F04 backup validation approved missing originals | 0 (fixed on first attempt) | Hypotheses: (a) the validator iterated present members instead of proving required ones exist; (b) a manifest count stood in for inventory equivalence; (c) restore re-read the source independently of validation. Change: one strict `validate_backup` reconciling DB rows, manifest entries and canonicalised ZIP members; allowlisted members; duplicate/alias detection; streamed DB inspection; creation self-validates the artifact; restore validates then extracts only approved members from a private copy of the source. 14 new tests. | Probe `manifest_omits_db_referenced_object` now refuses at restore; 444-test suite green |
| F05 restore owned unprotected paths | 0 (fixed on first attempt) | Hypotheses: (a) staging name was predictable and deleted unconditionally; (b) protection covered only the default root; (c) the web route exposed `allow_existing`. Change: unique `mkdtemp` staging, per-target exclusive lock, protection of default + active-pointer + serving root with canonical resolution and ancestor/descendant checks, `allow_existing` forced false on the web route, activation validates the DB and every referenced original. 14 new tests. | `test_an_unrelated_staging_directory_survives`, `test_a_custom_serving_root_is_protected` |
| F06 newer schema silently downgraded | 0 (fixed on first attempt) | Hypothesis: `init_db` ran DDL/migrations then set any differing version to 1. Change: probe `user_version` read-only (header fallback) before any write; raise `SchemaTooNew`; advance the version only after migrations succeed; migration failures surface; close the connection on failure. 4 new tests, including byte-for-byte immutability. | `test_a_newer_schema_is_refused_and_left_untouched` compares file hashes |
| F07 fingerprint overruled evidence and ownership | 0 (fixed on first attempt) | Hypotheses: (a) fingerprint lookup ran before the existing link; (b) the fingerprint omitted the aircraft identity, so serial-less records with different names collided; (c) absence of duration/distance counted as corroboration; (d) nothing enforced one owner per original. Change: existing link first, fingerprint as corroborated candidate only, aircraft identity in the hash, `_corroborates` requires complete evidence, normalised instants, `identity_conflicts` table + unique raw-ownership index on clean DBs (conflicts recorded, never deleted), nonunique fingerprint index. 15 new tests. | `test_missing_serial_different_names_do_not_merge`, `test_reparse_does_not_give_one_original_two_flights` |

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

- **P02** — Raw export never replaces a destination file. `skipped` now means the
  destination was hashed and matched (the report also carries `verified`); a
  modified destination is kept and a verified copy is written beside it
  (`preserved`, `conflicts`). Manifest entries are validated and used only as a
  hint. An existing archive object is verified by hash, not length; a mismatch
  raises `archive_object_corrupt`, quarantines both the corrupt object and the
  bytes just received under `<root>/quarantine/`, and leaves the archive
  untouched. `Job.start` serialises its running check and thread start under one
  lock.

- **P03** — `backup.validate_backup` is now one strict validator: it reconciles
  `raw_files` rows, manifest entries and canonicalised ZIP members, refuses
  missing/duplicate/aliased/unknown members, streams the DB to a temp file for
  `integrity_check`/`foreign_key_check`/`user_version`, and reports a newer
  schema as a refusal. A declared-but-absent DJI key is now an **error**
  (`key_missing_from_backup`), not a warning; a legacy backup that never
  declared one is clean, and an intact blob this account cannot decrypt stays a
  warning. `create_backup` validates its own artifact before reporting success
  and uses a random-suffixed filename when the timestamp name is taken.
  `restore_backup` copies the source, validates that copy, then extracts only the
  approved members, so validation and extraction never read different bytes.

- **P04** — `restore_backup` protects the default root, the activated root (read
  from `active_data_root.txt`) and the serving root passed by the caller; it
  refuses a target equal to, inside, or containing any of them, after canonical
  resolution. Staging is a unique `mkdtemp` directory beside the target, never a
  predictable `<target>.restore-tmp`, and a per-target lock refuses overlapping
  restores. The web route no longer accepts `allow_existing`. `activate_data_root`
  verifies the target's schema is supported, the database passes
  `integrity_check`, and every referenced original exists. `db.init_db` raises
  `db.SchemaTooNew` for a newer schema **without writing anything**, advances the
  version only after migrations succeed, surfaces required-migration failures,
  and closes its connection on failure. The launcher reports a newer-schema root
  plainly instead of opening it.

- **P05** — Flight identity is conservative. An original's existing link is
  resolved before any fingerprint lookup, so a reparse cannot move it or give it
  a second owner; a conflict is recorded in `identity_conflicts` instead. The
  fingerprint includes the aircraft identity (serial, else name) and a
  UTC-normalised instant, and requires both, so serial-less records with different
  names never collide and name-derived instants stay distinct from decoded ones.
  A match is accepted only when `_corroborates` finds the same aircraft, a start
  within 3 s, and **known** durations within 1 s and distances within tolerance;
  missing evidence yields a weak match, which is kept but excluded. `init_db`
  creates a unique `flight_files(raw_file_id)` index on a clean database; a
  legacy database with a double-owned original records each conflict and skips
  the index rather than deleting a row. A nonunique fingerprint index was added.
  `fingerprints.recompute` passes the aircraft name too, so repair and import
  agree.

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
python -m flightdesk selftest      # expect 473 tests, 0 failures at P05
```

Smallest next action: read P06 in `FlightDesk_Review_Pack/FLIGHTFIX.md` plus
findings F08–F09, then make the accepted-parse refresh atomic in
`importer.py` (`_store_telemetry`/`_update_flight_metrics`) and enforce equipment
kind on reassignment in `capture.py`.

If the host forces a stop: verify `git status` is clean, push, then resume at P06.
