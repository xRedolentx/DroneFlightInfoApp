# FLIGHTFIX — Human decisions and deferred verification

Created 2026-11-23 for Kevin's Flight Desk repair run.
Companion: `FLIGHTFIX.md`, reviewed commit `7806e1f554a3959672c4bd15dd4b6e52638d0fc0`.

## How the coding agent must use this file

This is a durable handoff, not permission to skip implementable work. Keep ordinary source repairs moving while equipment, access or a genuine owner decision is unavailable. Preserve existing entries and answers across sessions. Do not repeat a question the owner has already answered.

After three attempts at the same problem without material progress, stop that problem and record it here. Continue safe independent batches. Ask the human immediately only if the blocker prevents all remaining safe work, puts data preservation at risk, or requires an unauthorized action. At the end of independent work, **bring this file back to Kevin**, summarize open items in priority order, and give the smallest next actions. Do not silently leave this document buried in the repository.

Use these states: `OPEN`, `WAITING_ON_SOFTWARE`, `READY_FOR_OWNER`, `ANSWERED`, `VERIFIED`, `DEFERRED_BY_OWNER`. Mark evidence from mocks/synthetic fixtures separately from physical or real-data results. No actual serials, locations, private filenames, key material or raw logs belong in this file if the agent will read it.

## Initial handoff queue

| ID | State | What needs a human or target machine | Why it matters | Work that continues first |
|---|---|---|---|---|
| H01 | WAITING_ON_SOFTWARE | Approve a concrete preview before repairing existing history | New identity/air-time rules may change associations and totals; annotations must survive | P01–P14 source work and repair tooling on disposable copies |
| H02 | OPEN | Run the documented setup/check and integration suite on the Windows machine with the pinned parser/shim | This review ran on Linux without those components; the full suite was not certified | All parser-independent repairs and P13 setup preparation |
| H03 | WAITING_ON_SOFTWARE | Plug in each controller separately, then together, and observe watcher behavior | Mocked collector tests cannot prove Explorer/MTP completion or foreground Windows behavior | P09/P10 lifecycle and transfer fixes with fake adapters |
| H04 | WAITING_ON_SOFTWARE | Rehearse a real backup restore into a fresh root using the repaired build | Current validator can miss original-file omissions; sole-archive trust needs independent recovery evidence | P02–P04 preservation repairs and synthetic fault tests |
| H05 | OPEN | Supply local comparison access to a representative small set of real logs and AirData figures | Existing notes describe one real comparison, not broad parser coverage | P07 semantic tests; P13 privacy-preserving comparison recipe |
| H06 | WAITING_ON_SOFTWARE | Review any detected preexisting mixed-demo or ambiguous identity records | Code must not guess which annotations or flight links to discard | Prepare aggregate detection and per-record local preview without applying repairs |
| H07 | OPEN | Resolve annotation conflicts only when a proposed manual merge encounters them | Pilot/tags/notes from two records may both be meaningful | P11 review UI, keep-separate action and explicit conflict choices |
| H08 | OPEN | Optional visual comparison and preferences | Screenshot parity is lower priority than record preservation | P12 functional usability and keyboard checks |
| H09 | OPEN | Confirm independent preservation of the previously documented unreadable controller originals | A damaged source log and an incomplete transfer are different; neither should vanish | P10 retain/retry policy and clear status labels |

H08 is optional and does not block the correctness repairs. Preserve actual prior owner decisions. The prior checkpoint says a browser restore demonstration was deferred; verify Kevin's current preference when the repaired restore is ready.

## H01 — Approval for any historical-data repair

**Blocking:** applying a repair to owner data; not ordinary implementation.

Before asking Kevin, prepare:

1. A verified backup on an independent destination, including hashes and a successful fresh-root restore. Preserve the pre-repair original separately.
2. A dry-run report bound to the exact source revision: counts of records/associations affected; air/log-time deltas; identity conflicts; annotations requiring a choice; missing/corrupt originals; unsupported schema conditions.
3. A local preview Kevin can inspect without exposing records to the agent. The agent receives only sanitized counts and result categories.
4. The exact proposed apply command, expected duration, stop/resume behavior, rollback steps and post-apply checks. A preview that changes beneath the owner must be regenerated.

**Ask when ready:** “The preview would change [counts/categories], with [unresolved choices]. The independent restore check passed. May I apply this reviewed repair to the selected archive?”

**Record:** approval/decline, approved preview revision, result and rollback availability. Do not record private data or assume permission from source-code repair authorization.

## H02 — Windows and parser integration

**Blocking:** target-environment certification.

Once P13 is ready, run setup/check, the pinned parser smoke test, the decoder smoke test, the full applicable test suite and launcher tests on Windows. Check DPAPI with a disposable test secret; never echo the real key. Confirm checksum/version results and compiler prerequisites. Keep install actions distinct from check-only commands.

Record OS/Python/SQLite/parser versions, actual test counts, failures and skipped prerequisites. “407 passed previously” is historical evidence, not a substitute for a run on the repaired commit. Do not turn missing integrations into silent passes.

## H03 — Controller and watcher observation

**Blocking:** claiming unattended controller import is verified.

After P09/P10:

1. Launch normally, enable watching, and verify the UI reports a live worker. Disable/re-enable it without creating duplicate workers.
2. Connect an unlocked Smart Controller in file-transfer mode, then test RC 2 separately. Verify read-only collection, reported progress and originals preserved.
3. Repeat attachment: no duplicate logical flights. Test both devices and known duplicate storage aliases.
4. Exercise a disconnect/reconnect and the explicit verify/re-read mode. Incomplete reads must stay retryable and visible.
5. Observe a sustained attached period and restart the ordinary launcher. Record last attempt/last success and any errors. Install a logon task only if Kevin explicitly opts in.

Record aggregate counts by device class and outcomes; do not list actual flight filenames or serials. Hardware gaps do not block synthetic collector tests or unrelated source work.

## H04 — Real recovery rehearsal

**Blocking:** sole-archive trust and any recommendation to retire the comparison service.

After P02–P04 and before H01 apply:

- Choose a backup destination separate from the active archive, preferably on independent storage.
- Create and validate a new backup. Restore into a new, empty, non-active root; never replace the currently running root.
- Verify the database/manifest/archive inventory bijection, all referenced original hashes, integrity and foreign keys. Inspect representative flight, equipment, annotation and service records locally.
- On the same account/machine, verify the protected key/cache behavior if Kevin chooses. On another account, an intact but undecryptable DPAPI blob is expected to require reauthorization; that is not evidence that logs are missing.
- Reopen the restored copy offline and confirm the appropriate cached workflows. Keep the original untouched. Activation, if wanted, is an explicit action after validation.

Record the repaired commit and aggregate checks. A default-folder backup on the same failed disk is not an independent recovery copy.

## H05 — Representative real-log comparisons

**Blocking:** broad parser/metric confidence; not all source repairs.

Aim for roughly 5–10 locally retained representative logs rather than repeatedly comparing one file. Include the versions/aircraft actually owned, at least one encrypted cached decode, ground idle before/after flight, a gap or poor fix, and a known unreadable case if available.

Compare like-for-like fields: off-ground air time; total log duration; sample path versus header distance; height above takeoff versus absolute altitude; battery endpoints and temperature. Establish acceptable tolerances from sampling precision and the reference rather than tuning code to a single magic total. Do not expect different metric definitions to be identical.

The owner selects logs locally. The agent sees anonymous case IDs, categories and aggregate numeric deltas only. Keep fixtures and reference screenshots private and out of Git. No DJI network exchange without the owner's existing authorization and configured credential flow.

## H06 — Existing contamination or identity conflicts

**Blocking:** automatic historical cleanup.

If code detects mixed demo/ordinary data, duplicate raw ownership, conflicting flight identities or stale derived records, preserve everything. Do not infer “safe to delete” solely from `SYN-` serials, pilot names, tags or a date. Identify record groups in a local preview and ask only the unresolved choices.

For conflicts, show Kevin the source evidence and which annotations/associations each option preserves. Existing raw bytes are immutable evidence. Removing duplicate totals does not justify deleting an original.

## H07 — Merge conflict choices

**Blocking:** only the individual ambiguous merge or an unresolved product rule, not P11a or independent work.

Default: preserve both records until Kevin chooses; preserve all originals in every case. If the two candidate records disagree on pilot, notes, tags, equipment or accepted telemetry source, show those conflicts locally and collect a deliberate choice. Explain how undo works. Never quietly prefer the newest row or the first imported row.

No question is needed for routine implementation decisions such as adding a transaction or validating IDs. Ask about actual conflicting owner data, not hypothetical preferences the software can safely handle.

## H08 — Optional visual review

**Blocking:** none of the data-preservation repairs.

After the functional fixes, Kevin may compare the repaired app with his private reference screenshots on his own machine. Prioritize readable missing-data labels, keyboard operation, fit at ordinary laptop zoom and the main flight → equipment → maintenance workflow. Screenshots and personal records stay local. Do not delay correctness fixes for pixel-level parity or start a framework rewrite.

## H09 — Unreadable originals already noted in the repository

**Blocking:** any claim that all historical originals have an independent copy.

The existing checkpoint describes two unreadable v11 records, with one previously said to exist only on a controller. That statement is historical and was not verified in this review. Ask Kevin to confirm local preservation status using the repaired collector and an independent backup. Do not request uploading the logs to the model. A complete but malformed original should be retained and labelled; it should not be declared decoded or excluded from backups.

## New blocker template

Copy this section for an actual new blocker. Use stable IDs H10 onward.

```text
ID / short title:
State:
First observed / latest update:
Batch and exact invariant affected:
Blocking scope: this item / dependent batches / entire run
Sanitized symptom and reproduction:
Attempt 1 — hypothesis, change/test, result:
Attempt 2 — hypothesis, change/test, result:
Attempt 3 — hypothesis, change/test, result:
Material progress since those attempts: none, or specific measured evidence
Why a fourth attempt is not permitted / why a human is needed:
Work safely completed / checkpoint:
Independent work still runnable:
Smallest owner action or decision:
Recommended option and consequence:
Alternative option and consequence:
Owner response / date:
Verification required after response:
Result / closure evidence:
```

## Owner decisions already recorded (merged from CHECKPOINT.md, 2026-11-23)

These answers exist before the repair run. Do not re-ask them; preserve them.

- **H01** — no approval for any historical-data repair. Nothing may write to the
  live root. A concrete sanitized preview (P08) plus a verified independent
  backup/restore (H04) come first.
- **H04** — the owner **deferred** the browser restore on 2026-11-22. The
  rehearsal passed through the same code the button calls; the cutover click is
  the owner's. **Keep the AirData subscription active** (renewal February 2027)
  until the owner is satisfied; do not cancel it.
- **H05** — `%LOCALAPPDATA%\FlightDesk\realcheck\` holds exactly **one** real
  log, the Air 3S flight of 2026-08-11, matching AirData at 302.2 s. This is
  recorded evidence of one comparison, not broad parser coverage.
- **H08** — visual parity against the brief's 23 screenshots is still open. The
  supplied screenshots are AirData maintenance pages only; `Screenshots/` is
  git-ignored and private.
- **H09** — two unreadable v11 records exist: flight 216 (from the AirData
  export) and flight 283 (from a controller). Flight 283 was reported to exist
  **only on the controller**. Both are kept, flagged `details_unreadable` and
  excluded from totals. Confirm independent preservation locally; never upload
  the logs.
- **Battery `Bat-Mavic2-3402QS`** — the completed service recorded
  2026-11-19 19:55:41 UTC is real and owner-confirmed. Its 30/20 `Due` becoming
  0/20 `ok` is the feature working. Do not "fix" it by deleting the row.
- **DoneDoctor backdating** (2026-11-21) — six rules were given a completed
  service at `2026-11-19 20:00 UTC`, read as 13:00 local (UTC-07:00) by the
  owner's request. If that was meant as UTC, the owner must say so; do not change
  it unasked.
- **Restore target preference** — the owner has never activated a restored
  root. Fresh-root restore only; activation stays an explicit owner action.

## Archive corruption quarantine (added 2026-11-23, P02)

If an import finds an archived original whose bytes no longer match its own
name, the import stops with `archive_object_corrupt`. Nothing in the archive is
replaced. Both the damaged object and the good bytes just received are copied to
`<data root>\quarantine\` as `…-corrupt.bin` and `…-incoming.bin`.

That is a recovery prompt, not a routine event. If it ever appears, the owner
decides whether the archive copy should be restored from the quarantined
incoming file or from an independent backup. The agent must not apply that repair
to the live root without the usual preview and approval (H01).

## Required final handoff

### Final handoff — 2026-11-24, commit `efcc5a0`

**Finished.** P00–P10, P11a, P12 and P13, all verified by
`python -m flightdesk selftest` (537 tests, 0 failures), `tools\ui_gate.ps1`
(109 browser checks, 0 failures across four passes), `tools\rehearse.py` (13/13),
`tools\fault_check.py`, `tools\recon_check.py` (11/11) and `tools\check_env.py`.
Findings F01–F13 are fixed; see `FLIGHTFIX-RESULTS.md`.

**Not finished (software).** P11b (merge two confirmed variants) is not built —
it needs the conflict policy in H07. P13's test-suite tier split is not done.
F14's PowerShell collector does not yet emit transfer-completion evidence, so the
strict USB path is dormant on hardware. Q05's unknown-time label has a static pin
but no browser case, because neither synthetic root produces such a record.

**Needed before trusting the archive.** H02 (run the suite on the target
machine), H04 (a real backup restored into a fresh root with this build), H05
(5–10 representative log comparisons), H06 (review any mixed/ambiguous records),
H09 (confirm the two unreadable originals are preserved).

**Can wait.** H08 visual review; the P13 tier split; the Q05 browser case.

**Next owner action (ordered).**
1. Run `python tools\check_env.py` on the Windows machine, then
   `python -m flightdesk selftest` and confirm 536/0.
2. Create a backup and restore it into a fresh, non-active folder; open it and
   check a few flights, then keep or discard it (H04).
3. Plug in each controller separately and run **Verify / re-read logs** once;
   report what the scan says about incomplete or retried files (H03/H09).
4. If you want a merge of two variants, say which record’s pilot/notes/tags win
   (H07) before any merge code is written.
5. No historical repair has been applied. When you want one, ask for a dry-run
   preview (`python tools\reparse_all.py --root <folder> --json`) and review the
   counts before approving (H01).

AirData stays active. Nothing here has been applied to the live data root.

---

Before ending the repair run, the agent must update this section with:

- **Finished:** completed software batches and current verified commit.
- **Not finished:** blocked/partial software work, distinct from owner-only verification.
- **Needed before trusting the archive:** backup/restore, historical-repair decisions, target-environment and representative-log checks.
- **Can wait:** cosmetic preferences and noncritical conveniences.
- **Next owner action:** one short ordered set of steps or a specific approval question tied to a concrete preview.

Do not mark the run fully verified while these required gates remain open. After Kevin replies, resume the pending item and preserve the earlier attempt ledger.
