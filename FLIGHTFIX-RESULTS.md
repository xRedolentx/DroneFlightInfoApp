# FLIGHTFIX — results

Base commit `7806e1f` (the reviewed snapshot). Final commit at the time of
writing: `efcc5a0`. Branch: `main`, pushed to
`git@github.com:xRedolentx/DroneFlightInfoApp.git`. 14 commits, one per batch
plus the baseline.

## Environment used

Windows 11 `10.0.26300`, Python `3.12.10`, SQLite `3.49.1`, Windows PowerShell
`5.1.26100`, pinned `dji-log-cli 0.5.7`, keychain decoder
`flightdesk-decode.exe` present. `python tools\check_env.py` → all mandatory
components present.

## Status

**PARTIAL — software complete for P00–P10, P11a, P12 and P13; P11b (merge) not
implemented.** This is deliberately not one of the three final labels: the
queue says not to call a batch complete while runnable software work remains,
and merging two confirmed variants is still unbuilt. Everything that was built
is verified by the checks below.

Findings:

- **F01** demo contamination — fixed (P01).
- **F02** raw export overwrote destination files — fixed (P02).
- **F03** same-size archive corruption accepted as duplicate — fixed (P02).
- **F04** backup validation approved missing originals — fixed (P03).
- **F05** restore owned or deleted foreign paths — fixed (P04).
- **F06** newer schema silently downgraded — fixed (P04).
- **F07** fingerprint overruled evidence and ownership — fixed (P05).
- **F08** equal-length reparse kept a stale track — fixed (P06).
- **F09** wrong equipment kind and stale battery history — fixed (P06).
- **F10** air-time semantics inconsistent — fixed (P07).
- **F11** first capture ignored captured limits — fixed (P07).
- **F12** reparse dry run could not work — fixed (P08).
- **F13** enabling the watcher started nothing — fixed (P09).
- **F14** USB header plausibility mistaken for completeness — **partial** (P10):
  the Python contract now requires completion evidence, retains unverified bytes
  and offers a verify/re-read mode, but the PowerShell collector does not yet
  emit `size_known`/`sha256_match`, and two-read hashing on hardware is
  unverified.
- **F15** “Keep / merge” was a dead end — **partial** (P11a): a real
  review/keep-separate/exclude workflow with a stored, reversible decision
  replaces it. **P11b (merge confirmed variants) is not implemented.**

Usability notes Q01–Q05 are implemented in `app.js` and pinned by static tests;
the live browser gate was not run this session.

## Commands and outcomes

- `python -m flightdesk selftest` → **536 tests, 0 failures, 0 errors, 0 skipped**.
- `python tools\rehearse.py` → **13/13 steps**, including restore into a fresh
  root, matching totals and maintenance statuses, and two independent backups.
- `python tools\fault_check.py` → disk-full backup rejected and cleaned up;
  interrupted restore keeps the target marker, leaves no half-written target and
  no stale staging; retry succeeds.
- `python tools\recon_check.py` → **11/11**.
- `python tools\check_env.py` → all mandatory components present.
- `python FlightDesk_Review_Pack\evidence\review_probes.py .` → run at baseline:
  all 19 probes reproduced the reviewed behaviour. After the repairs, probes whose
  defect is now refused earlier (missing backup original, future schema) abort at
  that step instead of printing the old result; the export and archive probes
  print the preserved bytes. The probes are baseline evidence, not a suite.

## Migrations and compatibility

- New tables `identity_conflicts`, `identity_decisions`; new columns
  `flight_telemetry.source_raw_id`, `rules.owner_limits`; new indexes
  `idx_flights_fingerprint`, `idx_identity_conflicts_raw`, and a unique
  `idx_flight_files_raw` created only on a database with no double-owned
  original. All are created idempotently on open; no schema version bump.
- A database with `user_version` newer than this app is refused without being
  modified. An older schema is migrated forward; a required migration failure
  surfaces and does not advance the version.
- A legacy backup that never carried a DJI key still validates. A backup whose
  manifest declares a key but does not contain it is now an error.

## Changed behaviour worth telling the owner

1. Demo data is only seeded into an empty root, and a demo root refuses real
   imports. Real logs need their own folder.
2. Raw export never replaces a file in the destination; a modified or colliding
   file is kept and a verified copy is written beside it.
3. A corrupt archived original is not reported as a duplicate; the import fails
   with `archive_object_corrupt` and keeps both copies in `quarantine\`.
4. A backup validates only if every original the database references is present.
5. A restore refuses any protected root (default, activated, serving) and the web
   route no longer replaces data in place.
6. Air time is off-ground time. Maintenance counts air minutes, not log time.
7. An unresolved flight has a **Review identity** action with a real decision.
8. Enabling the watcher starts a real worker; the UI shows whether it is running.
9. The controller scan offers **Verify / re-read logs**, which bypasses the
   name+size skip.

## Unresolved software work

- **P11b** merge of confirmed variants (needs an owner conflict policy, H07).
- **P12** live browser gate (Edge + server) for the UI corrections.
- **P13** test-suite tier split; `check_env.py` covers the setup half only.
- **F14** the collector does not yet emit transfer-completion evidence.

## Release gates still owned by Kevin

- **H01** approve a concrete historical-data repair preview (none has been
  applied).
- **H02** run the repaired build's suite on the target Windows machine.
- **H03** observe both controllers, an incomplete transfer and the watcher over a
  sustained period.
- **H04** rehearse a real backup restore into a fresh root with the repaired
  build.
- **H05** supply 5–10 representative real logs with AirData figures.
- **H06** review any detected mixed-demo or ambiguous identity records.
- **H07** resolve annotation conflicts for any future merge.
- **H08** optional visual review.
- **H09** confirm independent preservation of the two unreadable originals.

AirData stays active. Nothing in this run touched the live data root, and no
historical repair was applied.
