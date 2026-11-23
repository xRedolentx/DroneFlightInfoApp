# FLIGHTFIX — Flight Desk review and continuous repair queue

Reviewed 2026-11-23. Repository: <https://github.com/xRedolentx/DroneFlightInfoApp>.
Reviewed commit: `7806e1f554a3959672c4bd15dd4b6e52638d0fc0`.
Audience: Kevin and the DeepSeek V4-Flash coding agent implementing Flight Desk.

## Start here

Place this file and `FLIGHTFIX-HUMAN.md` in the repository root. The optional evidence bundle contains `review_probes.py` and `review_probe_results.json`. Those probes describe **baseline defects**, not a passing regression suite. Keep their output separate from the implementation's test results.

Paste this once into the coding agent:

> Read FLIGHTFIX.md through the execution contract and queue. Create or resume FLIGHTFIX-STATE.md. Execute the first runnable unfinished prompt, starting with P00. After each verified batch, checkpoint your work, read its NEXT instruction, and immediately execute the next runnable batch without asking me to continue. Use the finding appendix only for the current batch. Preserve private data and existing user changes. Never make more than three attempts on the same problem without material progress: stop that line of work, record the evidence and needed human decision in FLIGHTFIX-HUMAN.md, and continue independent work if it is safe. Pause the overall run only for a blocker that prevents all remaining safe work, or when an action needs authorization. At the end, present the outstanding human items and a truthful verification summary. Begin now.

**Resume after interruption:**

> Resume FLIGHTFIX.md using FLIGHTFIX-STATE.md and the current Git diff. Verify the last checkpoint, preserve the attempt ledger, and continue the first runnable unfinished batch. Do not restart completed batches or reset failed-attempt counts. Bring FLIGHTFIX-HUMAN.md back to me when independent work is exhausted.

The Markdown file guides an agent that can read files, edit code and execute commands. It cannot restart a disconnected host or extend an exhausted model session by itself; the durable state and resume prompt provide that continuity.

## Review verdict

Keep the architecture: Python standard library, SQLite WAL, content-addressed original logs, loopback UI, vanilla JavaScript, a pinned parser adapter and a small Windows collector are appropriate for this personal application. A framework rewrite would add cost without fixing the defects below.

**I would not yet use this as the sole trusted archive or cancel the comparison service on the strength of the current code alone.** There are reproducible preservation and accounting defects. The three first implementation priorities are: prevent demo contamination; make exports genuinely non-overwriting; and make backup validation prove that every database-referenced original is present. Restore target protection belongs in the same first release gate.

One feature to remove immediately is **“load invented demo data” inside a populated archive**. Preserve demo exploration in a separate root. One misleading control to replace is **“Keep / merge”**, which currently only opens a details page.

The existing improvements are real: watcher exceptions are recorded, SQLite backup uses the snapshot API, raw files are written before indexing, imports generally parse outside short write transactions, rollback on extraction failure exists, and the UI is loopback protected. Do not reopen the review brief's already-fixed silent-exception issue or serialize a whole USB copy inside a database transaction.

## Execution contract — applies to every prompt

### Scope and authorization

- Implement the repairs in this queue, tests and concise documentation. Preserve offline operation, ordinary-user Windows support, existing UI concepts and original log bytes. No cloud migration, paid services, broad redesign, agent swarm or new production dependency without a concrete need.
- The review snapshot is a starting point, not a command to overwrite newer work. Inspect the current tree and local instructions. Reproduce each finding against current code; mark an already-fixed finding with evidence instead of reimplementing it.
- This new repair instruction authorizes source edits despite the old `REVIEW-BRIEF.md` saying “review only.” Its private-data boundaries still apply. Do not read private logs, screenshots, actual database contents or secrets into model context. All development fixtures must be invented. Do not put private input paths, serials, coordinates, keys or upstream stderr into agent output.
- Develop and test in explicitly named temporary roots. Never run demo seed, repair, reparse-apply, restore, migration experiments or import against the owner's active root. A later live-data repair needs a concrete preview, a verified independent backup and Kevin's approval. Prepare that work; do not silently perform it.
- Local verified commits are appropriate checkpoints under the original build instructions. Stage only the batch's source, tests and safe documentation. Inspect the staged diff. Prefer normal Git; do not invoke a helper that stages the whole tree. Do not push, publish, install a logon task or make credentialed/external calls unless separately authorized. No automatic stash, reset, clean or deletion of someone else's changes.

### Cardinal rule: three attempts without material progress

For **each specific problem**, keep a persistent attempt ledger. An attempt is a distinct hypothesis plus a test/change intended to resolve it. A rerun without a changed hypothesis consumes an attempt too. Material progress means a reproducible cause was isolated, a failing invariant was repaired, or a necessary acceptance check measurably improved; rewritten prose, a new error message or a renamed approach is not progress.

After **three attempts without material progress**, do not attempt a fourth. Stop that problem, checkpoint safely, and create/update a human item with the three hypotheses, sanitized results, affected batches and smallest decision or access needed. The count survives compaction and session restarts. Reset it only when the ledger identifies the material progress that justifies resetting it.

- If the problem can wait, mark its batch `BLOCKED` or `PARTIAL`, mark dependent batches `WAITING_DEPENDENCY`, and continue the next independent runnable batch.
- If it threatens data preservation, requires an unauthorized action, or prevents every remaining safe batch, pause and ask Kevin directly.
- Missing hardware, credentials, real-file comparisons and visual preferences normally wait in `FLIGHTFIX-HUMAN.md`. They are not a reason to stop ordinary source repairs.
- Do not change a failing test to bless the defect, substitute synthetic evidence for hardware evidence, or disable a gate to move the queue along.

### Efficient execution for DeepSeek V4-Flash

These are workflow choices, not claims about an undocumented model API or guaranteed model performance.

1. Keep this contract stable across batches. Read the queue, the current prompt, its finding entries and only the named code/tests first. Use targeted symbol searches for dependencies; avoid dumping the whole repository and historical status documents into context each time.
2. Work one batch at a time. Start with a short invariant and failing case, implement the smallest coherent change, run focused behavioral tests, and review the diff. Add only focused regression coverage that exercises the failure mechanism.
3. Reuse existing adapters and test helpers. Add parser-independent unit fixtures where the parser is irrelevant; retain real parser integration gates. Do not “fix” unavailable Windows APIs with production mocks or add a new fallback decoder just to turn the Linux suite green.
4. Keep output short: batch, changed behavior, tests run, result, pending item and NEXT. Put durable detail in the state file. Do not rewrite the entire plan or run the full suite after every trivial edit.
5. If a batch becomes too large, split it into named substeps in the state file with explicit acceptance boundaries. Complete those sequentially; do not broaden scope into an unrelated refactor.
6. Do not alter temperature, context-window settings, API flags or model aliases based on this file. Use the owner's working runner configuration. Long operations should show progress and have bounded timeouts; do not restart productive work merely because it takes time.

### Durable state and transition procedure

P00 creates `FLIGHTFIX-STATE.md` with:

```text
Reviewed commit / actual starting commit / branch:
Environment and installed capabilities:
Current batch / next runnable batch:
Batch table: ID | status | changed files | tests/result | commit | blockers
Attempt ledger: problem ID | attempts since progress | hypotheses/results | progress evidence
Pending human IDs:
Known baseline failures and their environment:
Resume command and smallest next action:
```

Use `TODO`, `IN_PROGRESS`, `DONE`, `PARTIAL`, `BLOCKED`, `WAITING_DEPENDENCY`, `ALREADY_FIXED`. Mark `DONE` only when the batch's software acceptance checks pass. Hardware-only gates may remain in the human file, explicitly separate from software completion. Record unsupported checks as `NOT RUN` or `BLOCKED`, never `PASS`.

Every batch ends with this procedure: update state and human items; inspect the diff; checkpoint verified changes; select the first unfinished batch whose dependencies are met; **read and execute it immediately**. Do not finish the agent turn with “ready for the next prompt” while runnable work remains. If the host forces a stop, write the exact resume action first.

## Queue and dependency map

P00 is required for every later batch. “Independent” below means independent after P00.

| Batch | Repair unit | Other dependencies | Findings |
|---|---|---|---|
| P00 | Baseline, privacy boundaries and resumable state | None | Environment |
| P01 | Isolate demo data | Independent | F01 |
| P02 | Non-overwriting exports and archive integrity | Independent | F02, F03 |
| P03 | Complete backup validation | Independent | F04 |
| P04 | Safe restore targets and schema opening | P03 | F05, F06 |
| P05 | Flight identity and stable original ownership | P04 | F07 |
| P06 | Atomic derived-data refresh and equipment consistency | P05 | F08, F09 |
| P07 | Air-time semantics and maintenance inputs | P06 | F10, F11 |
| P08 | Honest dry-run repair tooling | P05, P06, P07 | F12 |
| P09 | A functioning watcher lifecycle | Independent | F13 |
| P10 | USB completeness and explicit rescan | P02, P09 | F14 |
| P11 | Real identity-resolution workflow | P05, P06, P07 | F15 |
| P12 | Small UI reliability and usability repairs | P01, P09; identity labels depend on P11 | Q01–Q05 |
| P13 | Reproducible setup and meaningful test tiers | Independent; final integration follows repairs | Environment |
| P14 | Integration, release evidence and human handoff | All runnable batches addressed | All |

Run in listed order when unblocked. If P04 is blocked, for example, P09 and P13 can still proceed. Do not run a dependent migration against a half-repaired schema.

<!-- PROMPT P00 BEGIN -->
## P00 — Establish the baseline and resume mechanism

**Read:** this contract; `README.md`, `requirements.txt`, `flightdesk/paths.py`, `flightdesk/__main__.py`, local agent instructions; relevant test helpers. Read historical status only to distinguish claims from checks you actually run.

**Do:** record current commit, branch, dirty paths, OS, Python/SQLite versions, parser/shim availability and Windows PowerShell availability. Inspect only code/configuration and aggregate capability status. Use a normal repair branch if feasible without disturbing user work. Create `FLIGHTFIX-STATE.md`; preserve an existing state file. Initialize all queue statuses and the persistent attempt ledger. Merge the supplied human checklist with any existing notes without losing owner decisions.

Run the existing selftest once in its documented environment if available. Record the exact command, test totals and blockers. The reviewer observed 407 tests, 61 failures and 19 errors on Linux without the installed Windows parser/DPAPI; this is **not** a list of 80 proven application defects. Identify missing prerequisites before attempting random test repairs. Review test output locally and report sanitized summaries.

If using the optional probes, inspect them first and run `python review_probes.py <repo-path>` on this disposable checkout only. They deliberately exercise broken behavior; use their results as reproduction evidence, then create proper regression tests in the relevant batches.

**Pass when:** starting state is recorded, private roots are excluded, baseline results are honest, and the next runnable batch is explicit. Missing Windows hardware does not block this batch or parser-independent work.

**NEXT:** P01. Execute it now using the transition procedure.
<!-- PROMPT P00 END -->

<!-- PROMPT P01 BEGIN -->
## P01 — Prevent synthetic data from modifying real history

**Read:** F01; `flightdesk/demo.py::seed_demo`, `server.py::api_import_demo`, `__main__.py::seed`, the welcome/import demo controls in `web/app.js`, `views.py::_data_mode`, `tests/test_stage3_web.py`.

**Invariant:** demo exploration cannot mutate any ordinary archive, and subsequent real imports cannot silently mix with a demo archive.

Enforce isolation in the backend, not just by hiding a button. Prefer a dedicated demo root and an explicit demo identity marker. At minimum, reject seeding into a root containing any existing raw files, flights, equipment, services or imported capture; an empty flight table alone is not enough. Handle concurrent demo/import requests with an operation boundary that protects the empty-root decision. Seed a fresh demo root in a staged operation or provide a resumable, explicitly marked failure state. Avoid half-seeded ordinary roots.

Keep demo data useful without requiring the real parser for synthetic UI exploration: use invented normalized outcomes/fixtures through an explicit demo path, clearly labelled. Do not add a production parser bypass. Limit pilot/tag updates to the records just created for the demo. Close connections on already-seeded and error returns.

For a demo root receiving real imports, require a separate ordinary root; do not delete demo rows by guessing which serials look synthetic. Existing possibly mixed roots become a human reconciliation item, not an automatic cleanup migration.

**Tests:** populated archive retains exact pilot/tag/notes and service counts after a rejected seed; capture-only root is protected; clean demo can seed twice idempotently; concurrent seed/import cannot contaminate; real import into demo is refused or routed to an explicitly separate root. Exercise welcome and Import entry points.

**Pass when:** no available demo action overwrites owner metadata or turns an ordinary archive into a synthetic one. Update the owner guide for separate demo exploration.

**NEXT:** P02.
<!-- PROMPT P01 END -->

<!-- PROMPT P02 BEGIN -->
## P02 — Make preservation promises true at the filesystem boundary

**Read:** F02–F03; `rawexport.py` planning/run/Job, `archive.py::store_stream`, raw-export routes, `tests/test_stage18_raw_export.py`, archive tests.

**Invariant:** no existing destination content is overwritten by export; a matching size alone cannot prove archive integrity.

Replace one-shot collision suffixing with a collision-safe allocator. Preserve both the original target and an occupied suffixed target. Detect modified previously exported files; preserve them and create another verified copy under a fresh name or report a conflict. Keep unchanged reruns idempotent. A manifest is a hint, not ownership permission to overwrite. Handle same-size changes explicitly; either verify hashes or expose an honest verification mode while never claiming skipped bytes were verified.

Use unique exclusively-created temporary files, containment checks and a **no-clobber publication operation** appropriate to Windows. A preflight `exists()` followed by `os.replace()` is still a race. Never overwrite an unrelated `.part` file. Validate manifest shape/relative paths; reject symlink/reparse-point escapes from the destination and reserve Windows-invalid names. Serialize starts atomically in `Job.start`; test concurrent start requests and publication races. Do not delete user-created destination files to recover.

When an archive object already exists, verify content against its hash, not just length. A corrupt object must produce a visible integrity failure. Preserve evidence and the newly received known-good bytes in quarantine; do not silently replace the original or discard the only good copy. Record an actionable recovery item. Keep normal duplicate import efficient without weakening this invariant.

**Tests:** existing base and suffix collisions; edited exported file of different and equal length; concurrent exporters; target appears during publication; unrelated temporary file survives; corrupt same-size archive object is detected; healthy duplicates remain one raw row; failure cleanup only removes files owned by that operation.

**Pass when:** all preservation cases pass, including Windows-specific publication checks when available. Platform checks still pending are named explicitly.

**NEXT:** P03.
<!-- PROMPT P02 END -->

<!-- PROMPT P03 BEGIN -->
## P03 — Validate backups against their database, manifest and contents

**Read:** F04; `backup.py::create_backup/validate_backup`, database raw-file schema, `test_stage7_backup.py`, `test_stage21_concurrent_backup.py`.

**Invariant:** “valid” means every original required by the database is present exactly once with the expected length and hash, and the database itself is usable.

Build one strict validator reused by restore. Reconcile the three inventories: `raw_files` rows, manifest entries and normalized ZIP members. Reject missing entries in any required inventory, duplicate normalized names, duplicate manifest identities, contradictory sizes/hashes and unsafe paths. Unknown members must not be silently installed. Define a small allowlist for manifest, database, archived originals and the declared protected key. Missing a declared key must be reported as an incomplete backup, distinct from an intact DPAPI blob that belongs to another Windows user.

Validate manifest types before indexing them. Handle malformed JSON structures, SQLite failures, invalid counts and unsupported schema versions as structured failures. Stream database and archive members to operation-owned temporary storage within configured size limits; do not read a multi-gigabyte database into memory. Close SQLite handles before deleting temporary files on Windows. Check integrity, foreign keys and actual schema/version. Hashes detect consistency, not authenticity; do not imply protection against an attacker who rewrites everything.

Have creation validate the completed artifact using the same rules before reporting success. Preserve the existing snapshot-plus-immutable-objects strategy and concurrent-import test. Do not hold a long database write lock while copying files. Make backup filenames unique beyond the timestamp/PID fallback, and use owned staging/lock cleanup on every exception path.

**Tests:** remove one archive member; remove it from both ZIP and manifest while retaining the DB row; extra/duplicate/path-alias members; malformed manifest; wrong sizes/hashes; corrupt DB; missing protected key; intact foreign-account key; genuine concurrent import and backup; disk-full cleanup. Positive backup/restore fixtures must include at least one original.

**Pass when:** the formerly accepted incomplete backup is rejected before restore, good legacy backups remain supported or get an explicit compatibility result, and no validator exception turns into misleading success.

**NEXT:** P04.
<!-- PROMPT P03 END -->

<!-- PROMPT P04 BEGIN -->
## P04 — Protect restore targets and reject unsupported schemas before mutation

**Read:** F05–F06; `backup.py::restore_backup/activate_data_root`, backup routes, root resolution in `__main__.py`, `db.py::init_db` and migration helpers, fault checks.

**Invariant:** restore creates a verified new root; it never deletes an unrelated directory or replaces a running archive. Opening a newer database must not modify it.

Pass the actual serving root into restore validation. Account for explicit `--root`, active-root pointer and the default root. Reject targets equal to, inside, or containing protected active roots; canonicalize paths and handle Windows junction/symlink aliases conservatively. Remove `allow_existing` from the ordinary web restore operation. Prefer refusing all nonempty targets over inventing an in-place restore system. An offline replacement tool, if retained for existing tests, must have explicit scope and cannot be reachable as a casual UI bypass.

Allocate a unique exclusively-owned staging directory. Never delete a predictable `<target>.restore-tmp` path just because it exists. Guard concurrent restores to the same target. Hold or privately stage a consistent source ZIP so validation and extraction cannot consume different files. Extract only validated members. Revalidate staged contents before publication; publish without clobbering a target another process created. Faults must leave existing data intact and clean only operation-owned artifacts.

Inspect schema version with a read-only connection **before** DDL, WAL changes or migration writes. Refuse a version newer than supported. For supported old versions use explicit ordered migrations inside controlled transactions, advance the version only after success, and surface errors rather than swallowing `ALTER TABLE` failures. Do not bump versions gratuitously. Activation must validate a compatible archive, not merely test whether a filename exists.

**Tests:** custom serving root, active-pointer root, default root, ancestor/descendant targets, occupied staging directory, concurrent same-target restore, interrupted extraction/publication, source replacement, invalid activation target. A DB labelled version 999 is refused and remains byte-for-byte unchanged; a failed migration does not claim success.

**Pass when:** fresh-root restore is dependable and migration gates fail closed. Record Windows alias tests separately if unavailable.

**NEXT:** P05.
<!-- PROMPT P04 END -->

<!-- PROMPT P05 BEGIN -->
## P05 — Make flight identity conservative and original ownership stable

**Read:** F07; `importer.py::_fingerprint/_insert_or_link_flight/reparse_raw`, `fingerprints.py`, `db.py::flight_files`, stage1/stage16 tests.

**Invariant:** absent identity evidence does not prove equality; one original has one current logical-flight owner; reparsing cannot silently move or multiply that ownership.

Resolve an existing raw-file link **before** looking for a fingerprint match. If reparsed identity conflicts with another flight, retain ownership, preserve both records and raise a reviewable conflict. Do not add another link or silently merge away annotations. Expose enough evidence to resolve it in P11.

Treat fingerprints as candidate lookup keys, not unconditional proof. Missing aircraft serial plus same time/duration must not merge different named aircraft. Filename-only identity, unknown duration and missing distance are weak evidence; absence is not a corroborating match. Normalize timestamps for comparison while preserving source precision. Specify and test the exact evidence required for auto-linking a newly encountered variant. Retain genuine same-flight variants without a flood of duplicates.

Add a uniqueness/invariant mechanism for raw ownership consistent with the migration work. Before adding a DB constraint to an existing archive, detect current conflicts. On a disposable fixture prove migration behavior; on real data prepare only an aggregate preview. Do not pick a winner by row ID or delete rows to make a unique index succeed. Add a nonunique fingerprint lookup index if query inspection justifies it; legitimate unresolved candidates can share fingerprints.

**Tests:** no serial with different aircraft names; filename-only collisions; differing metrics with missing evidence; equivalent timestamp representations; a reparse colliding with another flight's fingerprint; existing linked raw keeps its owner; simultaneous import of identical bytes; genuine variants; intentional owner decisions survive reparse. Verify original hashes and annotations unchanged.

**Pass when:** conservative identity prevents the reproduced merge and double-link defects, and legacy-conflict migration has a safe disposition.

**NEXT:** P06.
<!-- PROMPT P05 END -->

<!-- PROMPT P06 BEGIN -->
## P06 — Refresh metrics, tracks and equipment-derived records together

**Read:** F08–F09; importer metric/telemetry/battery-reading writes, capture assignment functions, equipment views, `db.py` constraints, stage2/stage6 tests.

**Invariant:** one accepted parse revision supplies consistent summary, track, gaps, warnings and derived battery readings; reassignment respects equipment kind and provenance.

Choose the accepted source once at the flight-update boundary, then update its derived representations in one transaction. Equal sample count must not suppress a corrected track while summary metrics change. Keep the established protection against poorer variant parses, but distinguish intentional reparse/repair of the accepted source from discovery of a weaker alternative. Store lightweight accepted-source/revision provenance if necessary. Avoid repeated automatic metric oscillation as variants are reparsed.

Refresh log-derived battery readings after accepted reparse and battery reassignment; preserve manually entered/capture readings. Handle clearing an assignment explicitly. Make assignment validation reject nonexistent IDs, wrong equipment kind and Boolean IDs; do not rely on a generic FK to enforce kind. Keep serial mappings and displayed equipment serials coherent, and do not let name fallback override a conflicting known serial. Audit accepted changes.

Define null semantics explicitly: ordinary backfill may preserve unknown identity fields; a correction can invalidate a formerly wrong value. Do not use one blanket `COALESCE` policy for both. Ensure the fingerprint reflects the final stored row, including when duration becomes unknown. If a rejected-details record later reparses successfully, update its status from the new evidence while preserving any explicit manual exclusion decision.

**Tests:** same-length corrected coordinates change both summary and telemetry; richer accepted source is not replaced by a poorer variant; failure mid-refresh rolls back all derived tables; reassignment aircraft→battery is rejected; battery history moves only for log-derived rows; clearing assignment is coherent; null correction does not leave a stale fingerprint. Test source selection across reversed variant import order.

**Pass when:** summary and track no longer disagree after an accepted refresh and equipment views reflect the same associations as flights.

**NEXT:** P07.
<!-- PROMPT P06 END -->

<!-- PROMPT P07 BEGIN -->
## P07 — Make air time and maintenance counters mean what they say

**Read:** F10–F11; `parser_adapter.py::sample_from_frame`, `analysis.py::compute_metrics`, maintenance counter/baseline functions, capture rule creation, stage11/stage22 tests.

**Invariant:** missing telemetry flags remain unknown; air minutes use measured/explicitly-derived off-ground time; captured limits are honored without overwriting owner policy.

Preserve `isOnGround` and motor state as true/false/unknown. Do not convert missing/null values into false. Handle mixed known/unknown intervals deliberately and record provenance/coverage for fallback estimates. Exercise the adapter→analysis path, not only hand-built normalized samples. Ground-state time does not intrinsically require a GPS fix: keep valid non-GPS timing evidence separate from route availability. Guard nonfinite values at normalization/serialization boundaries.

Use `airborne_time_s` for incremental maintenance air minutes. Keep log duration separate. Unknown air time cannot silently become zero or whole log time. Preserve captured historical air-minute baselines as captured; do not retroactively recompute them from log duration. Show partial coverage and avoid an unqualified reassuring OK when an enabled counter is unknown; a known threshold breach still means due. Do not treat a disabled trigger as an unknown enabled counter.

Fix first-capture rule initialization: default rule creation currently happens before captured limits are applied, and the existing-row path never updates those limits. Apply captured limits during first construction. Track owner policy overrides so a later capture cannot reset them. For calendar baselines, verify that service date and snapshot coverage date are not conflated; add a case where an old service is captured recently. Use explicit provenance and defer genuinely ambiguous historical decisions to the human file.

**Tests:** landed samples missing ground flag produce no invented airborne interval; mixed flags; flight with ground states but no GPS; 600 s log/60 s air yields 1 maintenance minute, not 10; unknown air duration stays partial/unknown; initial captured limit 7 remains 7 instead of default 20; later captures preserve owner-edited limits; snapshot versus service calendar baselines.

**Pass when:** dashboard, CSV, equipment usage and maintenance share documented time definitions. Changes to actual historical totals remain a preview pending owner review.

**NEXT:** P08.
<!-- PROMPT P07 END -->

<!-- PROMPT P08 BEGIN -->
## P08 — Build a truthful, resumable dry-run repair tool

**Read:** F12; `tools/reparse_all.py`, `tools/repair_fingerprints.py`, importer update boundaries, checkpoint repair instructions.

**Invariant:** a dry run computes and reports proposed changes without attempting writes to the source database or archive; apply does exactly the reviewed repair with explicit resume state.

Separate analysis from persistence, or use an SQLite snapshot in a disposable root and point a read-only archive provider at original bytes. Do not wrap per-file commits in a pretend outer rollback or open a read-only connection and call the mutating importer. Temporary outputs belong outside the live archive. Preserve cached-key decoding locally without exposing DPAPI material. Report aggregate before/after changes, new conflicts, changed source revisions and failures; never claim every “needs attention” result is a successful repair.

Require an explicit `--root` for mutating repair commands, or resolve and display the active root with a confirmation flow that prevents repairing the default root accidentally after activation elsewhere. Support dry-run reporting in machine-readable sanitized form. Bind a repair preview to a DB/source revision so stale previews cannot silently apply after imports. For owner data, prepare the preview and stop before apply until authorized.

Repair the existing counter that never resets `consecutive` after a success. Use a persistent progress cursor or selected work list so “rerun to continue” does not endlessly restart from raw ID 1. If re-reading all originals is intentional, say so instead. Bound repeated failures consistently with the three-attempt contract; count productive files separately from repeated attempts at one failure.

**Tests:** dry run returns useful proposed deltas and leaves source DB rows, original hashes and settings unchanged; apply to a clone matches preview; injected failure rolls back one file; interruption resumes without skipping unfinished work; success resets a consecutive-failure streak; stale preview is rejected; active/custom roots are handled honestly.

**Pass when:** the default advertised dry run works and can be trusted as the prerequisite to human-approved historical repair.

**NEXT:** P09.
<!-- PROMPT P08 END -->

<!-- PROMPT P09 BEGIN -->
## P09 — Make enabling the USB watcher start useful work

**Read:** F13; `server.py::App.start_watcher/api_usb_watcher`, `__main__.py::serve/live_instance`, `usb.py::scan/set_watcher/status`, launcher tests.

**Invariant:** enabled watcher state corresponds to a live scheduler in the serving process; disabled means no new scans start; one root never runs overlapping controller scans.

A normal launcher does not pass `--watch-usb`, and the settings endpoint currently only stores a Boolean. Reconcile persisted watcher settings when the app starts and whenever they change. Start the worker idempotently; stop/suspend it cooperatively on disable. Handle the case where a logon launcher discovers an already-running ordinary server: do not exit with a false claim that watching started. Prefer a single serving process managing watcher lifecycle over a second competing server.

Add a per-root scan coordinator for manual and watcher scans; busy status should be visible. Keep copy/parse work outside long SQLite write transactions. Bound shutdown and avoid deleting staging still owned by a running collector. Close settings connections in finally blocks. Report configured state separately from running/scanning/error state, with last attempt and last success.

Do not install or modify the Windows logon task during development. Prepare the existing reversible opt-in flow and a human check. Retain recorded watcher errors; do not regress to swallowed exceptions.

**Tests:** ordinary launch + enable starts a fake collector; enabled setting survives normal restart; repeated enable makes one worker; disable prevents new scans; manual scan and scheduled scan cannot overlap; failures leave visible status and a later retry works; logon-start against an existing process reaches the intended state.

**Pass when:** behavioral tests prove the toggle controls work, not just a DB flag. Physical-device observation remains H03.

**NEXT:** P10.
<!-- PROMPT P09 END -->

<!-- PROMPT P10 BEGIN -->
## P10 — Distinguish complete USB copies from plausible prefixes

**Read:** F14; `tools/Collect-DjiLogs.ps1`, `usb.py` validation/staging/salvage, tests for stage4/stage17/stage20.

**Invariant:** a stalled or incomplete read must not be marked as a verified original or added to a permanent skip cache. Structurally damaged complete originals should still be preserved as evidence.

Separate transfer completeness from parser validity. For a trustworthy source length, verify received length and a completed closed read. For unknown-size MTP sources, the current three-second stability plus auxiliary-block length is insufficient. Restore the original build contract's independent evidence: completed repeated reads with matching hashes plus structural checks, or another equally defensible completion signal. If completeness cannot be established, retain recoverable staged bytes as explicitly unverified and retryable; do not promote them to normal “already imported” evidence. Apply the same rule to timeout salvage.

Avoid treating every `bad_details_offset` as a known complete-but-damaged original: distinguish zero/invalid source offsets from an offset beyond a truncated received prefix using transfer evidence. Preserve known genuinely damaged originals without trusting a truncated transfer.

Keep name+size as a fast scan heuristic, **not** a proof of identical bytes. Add an explicit “Verify/re-read controller logs” mode that bypasses that shortcut and content-hashes copied bytes. Avoid per-device filename dedupe across distinct physical storages unless the storage identity is actually equivalent. Label estimated skips honestly; never mutate the controller.

**Tests:** source pauses longer than three seconds after an auxiliary header; prefix passes cheap structural checks but is not complete; mismatching repeated reads; genuine complete damaged-details file; timeout salvage; changed bytes with same name/size can be recovered through force verification; duplicate storage aliases; both devices. Use fake transfer adapters, and separately test PowerShell behavior where available.

**Pass when:** confidence in transfer completeness is explicit and conservative. Record the hardware checks without inventing a physical pass.

**NEXT:** P11.
<!-- PROMPT P10 END -->

<!-- PROMPT P11 BEGIN -->
## P11 — Provide an auditable way to resolve flight identity

**Read:** F15; unresolved-flight views and Import UI, importer identity model from P05, flight-file references, coverage resolutions, battery readings and audit tables.

**Invariant:** “Keep / merge” must either perform a safe, reviewable operation or stop promising one. Resolving identity must preserve original bytes and owner annotations.

Implement in two sequential substeps; checkpoint each.

**P11a: Keep separate.** Rename the current dead-end control immediately to “Review identity.” Show candidate evidence and source provenance. Add a deliberate “These are separate flights” resolution with a note, retaining the decision across reparse. Validate whether a record has enough evidence to become authoritative; a known unreadable log cannot gain fabricated measurements merely because the owner dismisses its warning. Support leaving it excluded with a reason. Make decisions reversible and distinguish them from maintenance snapshot-coverage decisions.

**P11b: Merge confirmed variants.** Preview surviving identity, all original links, annotation conflicts, accepted telemetry source and affected counters. Require an explicit owner action at runtime. Use one transaction with stale-preview/version protection. Move current ownership consistently, preserve all source links/evidence, and retain a reversible resolution record/tombstone rather than deleting evidence. Do not concatenate incompatible pilot names or silently discard notes. Do not implement a merge until policies for conflicts are deterministic and visible; any inherently ambiguous owner choice belongs in H07 while independent work continues.

Keep this bounded to manual resolution of existing candidates. Do not add an automatic fuzzy merge engine. Existing unresolved records remain excluded unless a specific resolution justifies inclusion.

**Tests:** distinct flight decision survives reimport and reparse; merge two variants retains every original and selected annotations; conflicting annotations require a selection; merging same row is rejected; concurrent/stale resolution is rejected; injected failure rolls back; undo restores associations and totals; battery and maintenance views agree afterward. Add an actual browser workflow test that clicks Review identity and reaches a meaningful outcome.

**Pass when:** the misleading workflow is gone and supported resolution actions are auditable. If complex merge remains blocked, ship an honest read-only review/keep-separate workflow and record merge as unfinished; do not call the full batch DONE.

**NEXT:** P12 if dependencies are complete; otherwise continue the next independent runnable batch, including P13.
<!-- PROMPT P11 END -->

<!-- PROMPT P12 BEGIN -->
## P12 — Small UI repairs with direct everyday value

**Read:** Q01–Q05; API helper, download helper, flight filters/rendering, import controls, watcher/export status and modal functions in `web/app.js`; current browser gates.

Work in these small substeps without a framework migration. If P11 is blocked, complete the independent substeps and record the identity-label portion as waiting:

1. **Truthful downloads:** replace immediate “Saved …” with “Download requested …” unless completion was actually observed. Preserve the synchronous click/capability URL that avoids browser download blocking. Surface server generation errors where feasible without weakening authorization.
2. **Prevent stale responses:** add a navigation/request generation guard or AbortController for list/detail/filter loads. A slower old response must not replace a newer route or current selection. Debounce search modestly and preserve focus/caret. Catch request failures and restore enabled controls.
3. **Show actual worker state:** use P09's running/scanning/error distinction. After import, link to affected flights and separately show originals preserved, new flights, duplicates and attention. Do not say “decoded” merely because a bulk request returned top-level OK.
4. **Protect forms and actions:** disable a destructive/save button while its request is pending, preserve entered values after failure, offer an explicit retry and restore focus after dialogs. Add backend idempotency only where duplicated service operations would otherwise be recorded twice.
5. **Honest review labels:** show unresolved identity separately from coverage uncertainty, unknown versus zero readings, and per-field log/air-time labels. Make the existing missing/partial badges readable on a laptop and at narrow width.

Extract only a small shared helper/module if these changes would otherwise duplicate code. The size of `app.js` alone does not justify splitting every screen during a correctness repair.

**Tests:** intentionally reverse two responses; navigate away mid-request; double-click a service action; failed API request retains form content; download label does not claim completion; keyboard focus and basic narrow-width behavior; normal routes still work. Use an explicitly selected synthetic app URL. Do not attach the browser gate to an arbitrary open tab or rely only on substring assertions.

**Pass when:** these small workflows behave correctly in a browser and no product screen exposes developer implementation details.

**NEXT:** P13.
<!-- PROMPT P12 END -->

<!-- PROMPT P13 BEGIN -->
## P13 — Make a fresh checkout installable and tests interpretable

**Read:** README prerequisites, `requirements.txt`, `paths.py`, parser adapter, `shim/Cargo.toml` and lockfile, launcher, selftest, test support and browser gates. See review evidence below.

**Invariant:** a fresh Windows machine has a documented reproducible route to the pinned parser and decoder, and unavailable platform integrations do not masquerade as dozens of unrelated code failures.

Provide a bounded setup/check command and clean-machine instructions. Distinguish check-only from installing/downloading. For parser installation, use the repository's pinned upstream artifact and verify its documented checksum; do not guess or silently update versions. Build the shim with the lockfile (`cargo build --locked --release` in `shim`) or use an explicitly verified release artifact if one is supplied. Stage installation and promote only after version/health checks. Missing Rust/build tools should yield an actionable prerequisite, not endless retries. Do not change the user's global environment unnecessarily.

Keep normal daily operation offline. Dependency downloads are an explicit setup action, not an import side effect. If no network/toolchain is available, produce the complete setup script and leave the machine execution gate in H02.

Split test selection into parser-independent domain tests, real-parser integrations, Windows DPAPI/MTP/launcher tests and browser workflows. Unit tests for identity/backup/maintenance should inject invented normalized parser outcomes where decoding is irrelevant. Parser tests must still run the real pinned binary on independent fixtures in the proper environment. Emit named skipped prerequisites and a failing release-readiness result when mandatory Windows/parser gates are missing. “All unit tests pass” must not imply integration approval.

Replace static UI text checks only where a behavior check is necessary for a repaired defect; do not rewrite the entire test suite. Add a small private real-log acceptance recipe that prints only permitted aggregate differences. Never commit owner logs as fixtures.

**Tests:** clean sandbox setup/check, missing parser/shim diagnostics, corrupted download rejection, offline daily launch, real parser smoke test if supported, truthful skip categories, failing release status when required integrations are unavailable.

**Pass when:** a new developer can reproduce the environment, and test reports explain what actually ran.

**NEXT:** P14 if all independent implementation work has been addressed; otherwise resume the first runnable unfinished batch.
<!-- PROMPT P13 END -->

<!-- PROMPT P14 BEGIN -->
## P14 — Verify the repaired system and return the human checklist

**Read:** current state, all unresolved human items, changed interfaces, final Git diff and current test recipes. Do not reread every historical status claim.

Run focused regressions from every completed batch, then the full applicable suite once. Run the backup fault harness and reconciliation check with the real parser available. Exercise import → flight → equipment → service/undo → export → backup → fresh-root restore using synthetic data. Add a concurrent import/backup case with nonzero originals and changing flight counts. Confirm every restored DB-referenced archive object exists and hashes correctly. Repeat restoration from the same artifact to establish idempotent usability of the backup, using distinct fresh targets.

In a controlled browser, check the empty-root and populated ordinary-root landing pages, isolated demo, navigation/filter race protection, review identity, watcher status, failed requests and downloads. Inspect console errors. Keep browser and hardware evidence separate.

Create a concise final `FLIGHTFIX-RESULTS.md`: base/final commit, findings fixed or already fixed, exact commands and outcomes, migrations and compatibility, changed behaviors, unresolved software blockers, and release gates. Update README/OWNER-GUIDE/CHECKPOINT to the final behavior; do not append another lengthy conflicting history. Clearly label prior Windows results as historical.

Re-read `FLIGHTFIX-HUMAN.md` and present every outstanding item, grouped by what blocks trusting the archive versus what can wait. Provide the smallest ordered set of owner actions. A real-data repair preview must be concrete before requesting authorization. Do not apply it, install a task or cancel a subscription as part of this prompt.

**Final statuses:** `SOFTWARE COMPLETE — OWNER VALIDATION PENDING`, `COMPLETE — VERIFIED`, or `BLOCKED`, with evidence. No blanket “all fixed” if a runnable software batch is still partial, and no “ready to replace AirData” while archive preservation or required comparison checks are outstanding.

**NEXT:** return the self-contained results and human items to Kevin. Stop only after no safe independent work remains. On a later response, continue the exact pending item rather than restarting the queue.
<!-- PROMPT P14 END -->

## Finding appendix — read only entries needed for the current batch

Line references below belong to the reviewed commit; use function names after edits shift them. Severity: **P1** = preservation or materially incorrect records; **P2** = correctness/reliability/workflow. These are not claims of an Internet-facing compromise. “Reproduced” means a disposable synthetic experiment, not a test against Kevin's actual archive.

### F01 · P1 · Demo seeding modifies existing owner records

`flightdesk/demo.py:99,173–179`; `server.py:641–644`; `web/app.js:1788–1790,2066–2071`.
**Certain, bug, reproduced.** `demo_seeded` is the only entry guard; it does not require an empty root. Import exposes the demo button even in populated archives. Seeding selects all flights and overwrites pilot/tags. Probe changed `Owner pilot`/`Owner tag` to `Demo Pilot`/`mapping`. Synthetic captures also enter the same maintenance database. Blast radius: owner annotations and trusted history/maintenance totals. Fix in P01.

### F02 · P1 · Raw export overwrites files it promises to preserve

`flightdesk/rawexport.py:171–194,218–244`.
**Certain, bug, reproduced twice.** A previously exported file edited to a different length is overwritten by `os.replace`; a preexisting basename plus occupied hash-suffixed filename also leads to overwrite. Probe preserved neither edited target nor second collision: each became the original bytes `one`. Same-size modifications are skipped without hash verification. `Job.start` also checks running state outside its lock. Blast radius: independent destination files; concurrent calls worsen the exposure. Fix in P02.

### F03 · P1 · Same-size corruption in the original archive is accepted as a duplicate

`flightdesk/archive.py:148–151`.
**Certain, bug, reproduced.** Change stored `one` bytes to same-length `bad`, then import the intact `one` again. The stored corrupt object passes the size-only check, the fresh intact temp file is discarded, and the importer reports an exact duplicate. A subsequent backup hash check can fail, but the import has not detected or recovered the preservation problem. Blast radius: a damaged original remains the trusted archive object. Fix in P02 with detection/quarantine, not an unreviewed destructive replacement.

### F04 · P1 · Backup validation can approve missing originals

`flightdesk/backup.py:253–266,283–297,337–340`.
**Certain, bug, reproduced.** Validation iterates members actually present rather than proving all required members exist. Removing an archived original from the ZIP returns `ok:true`; restore catches that case only if the manifest still lists it. Removing the entry from both ZIP and manifest while retaining the DB row returns `ok:true` from both validate and restore. Probe restored a DB with one referenced original missing. A manifest count comparison does not establish inventory equivalence. This requires an inconsistent/tampered artifact; the experiment does not show ordinary backup creation spontaneously loses members. Blast radius: false confidence in recoverability. Fix in P03.

### F05 · P1 · Restore owns neither its staging name nor all protected roots

`flightdesk/backup.py:318–326,345–357`; `server.py:1038–1053`.
**Certain, bug, reproduced for staging and nondefault root acceptance.** Restore deletes an existing `<target>.restore-tmp` directory unconditionally; probe lost its unrelated marker file. The live-root guard covers only `default_data_root`, while the API exposes `allow_existing`. A custom/activated serving root is not identified as protected. Probe proved the custom-root bypass on a closed disposable DB; actual Windows in-use file replacement was **not** tested. Predictable staging also makes overlapping restores interfere. Blast radius: unrelated local files or an active archive if platform permissions allow replacement. Fix in P04.

### F06 · P1 · Opening a future schema silently relabels and mutates it

`flightdesk/db.py:358–366,391–428`; `backup.py:245–248,369–376`.
**Certain, bug, reproduced.** `init_db` executes DDL/migration helpers before version checking and sets every differing version to 1. A synthetic DB marked version 999 became version 1. Migration helpers swallow some SQLite failures. Restore warns on version difference; activation only checks the DB file exists. Current real data is not claimed to have a future schema. Blast radius: incompatible restore/downgrade and misleading migration success. Fix in P04.

### F07 · P1 · Fingerprint matches overrule evidence and existing ownership

`flightdesk/importer.py:114–132,402–416,459–470`; `db.py:107–114`.
**Certain, bugs, reproduced twice.** Two same-time/same-duration outcomes with no serial and different aircraft names merged into one flight. Separately, reparsing raw 1 to the fingerprint of flight 2 linked raw 1 to both flights because fingerprint lookup precedes existing-link lookup. The link PK permits this. Probe links became `(1,1),(2,1),(2,2)`. Blast radius: flight counts, provenance, annotations and equipment usage. Fix in P05; never repair historical conflicts by guessing which flight to delete.

### F08 · P2 · Equal-length reparses update summaries but retain stale tracks

`flightdesk/importer.py:186–199,297–376`.
**Certain, bug, reproduced.** `_store_telemetry` refuses parser telemetry when stored downsampled count is greater than or equal to new count. The surrounding code allows equal-weight metric refresh. Probe changed flight home latitude to 2 while stored track still started at 1. The defect is especially broad once tracks hit the 1,400-point cap. Blast radius: contradictory map/chart versus summary, stale gaps. Fix in P06.

### F09 · P2 · Equipment reassignment accepts the wrong equipment kind

`flightdesk/capture.py:1116–1153`; `importer.py:551–568`.
**Certain for kind mismatch, reproduced; related derived-reading drift established by code inspection.** `reassign_flight(...,'aircraft', battery_id)` succeeds because the FK only requires some equipment row. Reassignment updates the flight, while stored log-derived `battery_readings` are not moved/refreshed by that function; accepted reparse likewise lacks the new-flight insert's reading refresh. Blast radius: contradictory equipment histories and attribution. Fix in P06.

### F10 · P1 · Air-time semantics are inconsistent across adapter and maintenance

`flightdesk/parser_adapter.py:450–451`; `maintenance.py:61–75,278–287`; `analysis.py:190–209,270–273`.
**Certain, bugs, reproduced.** Missing `isOnGround` becomes `False`, so two stationary ten-seconds-apart frames with motors off are counted as ten seconds airborne and the documented missing-flag fallback never activates. Maintenance sums `duration_s`, despite dashboard/CSV using `airborne_time_s`: a 600 s log/60 s air flight triggered a five-minute rule as ten air minutes. Ground-state time is also discarded when no GPS fix is present, a code-inspected coupling requiring a regression case. Blast radius: false air time and maintenance-due status. Fix in P07; do not redefine captured historical baselines without evidence.

### F11 · P2 · First capture ignores captured maintenance thresholds

`flightdesk/capture.py:769–819,912–930,978–993`.
**Certain, bug, reproduced.** `apply_capture` creates default rules first. Later `_ensure_rule` computes captured limits but its existing-rule branch only updates baseline/status fields. Probe supplied a flight limit of 7 and stored 20. Owner-edited limits also need explicit precedence, so blindly updating every existing rule would introduce another defect. Blast radius: due dates/counters differ from captured policy. Fix in P07.

### F12 · P2 · The documented reparse dry run cannot work

`tools/reparse_all.py:62,81,87–97`.
**Certain, bug, reproduced.** It opens a read-only connection and calls `Importer.reparse_raw`, which issues updates. Even with a valid injected parse, probe exits 1 with a readonly error rather than calculating a preview. `consecutive` never resets on success; reruns always enumerate from the beginning despite “rerun to continue.” Blast radius: unsafe decision support and repeated wasted work before historical repairs. Fix in P08.

### F13 · P2 · Enabling the watcher does not start a watcher

`flightdesk/server.py:772–777`; `__main__.py:119–139`; `usb.py:560–566`.
**Certain, bug, reproduced at the settings-route boundary.** Normal launch does not start a worker. Toggle stores `enabled:true` but never invokes `start_watcher`; probe confirms enabled config with no worker. The `--watch-usb` path can also short-circuit when an ordinary server is already running. Blast radius: unattended collection silently never happens despite enabled settings. This is distinct from the previously fixed exception logging. Fix in P09.

### F14 · P1 · USB header plausibility is mistaken for transfer completeness

`tools/Collect-DjiLogs.ps1:230–248`; `flightdesk/usb.py:47–71,195–211,250–273`.
**Certain validation gap; real hardware failure scenario is likely, not physically reproduced here.** A fabricated 120-byte v14 header without records passes `validate_flight_bytes`. For unknown-size transfers, three seconds of stable length plus a complete auxiliary header does not prove the encrypted tail finished copying. Timeout salvage uses the same structural check. Name+size skips cannot detect changed bytes of the same length. Blast radius: an incomplete controller copy can look preserved/known. Cheap validation is useful but insufficient as the sole completion evidence. Fix in P10; keep damaged-but-completely-transferred logs distinct from incomplete transfers.

### F15 · P2 · “Keep / merge” is a dead end

`flightdesk/web/app.js:1818–1819,2089–2093`; server route inventory.
**Certain, bug by code inspection; browser interaction not executed here.** Button only navigates to flight Details. There is no identity keep/merge mutation endpoint. `/api/coverage/resolve` answers a separate maintenance snapshot question. README says the owner can resolve these flights on Import. Blast radius: unresolved records remain excluded with no promised resolution workflow. Fix in P11.

## Small usability opportunities (judgment calls unless stated)

| ID | Change | Evidence/value | Scope |
|---|---|---|---|
| Q01 | “Download requested” instead of “Saved” | `web/app.js:98–114` claims success immediately after clicking an anchor | Very small wording/behavior correction |
| Q02 | Ignore obsolete list/detail responses | Async rendering has no consistent route/request revision guard; likelihood depends on response timing | Small shared guard, behavioral browser test |
| Q03 | Show watcher actually running and link failed imports | Configured state is not execution state; owner should see what needs action | Reuse P09 payload and existing import results |
| Q04 | Prevent duplicate pending actions and retain failed forms | Service actions create new records per call; guard double-clicks and recoverable errors | Focus on service, backup and import controls |
| Q05 | Label incomplete readings consistently | Missing air time, unresolved identity and snapshot uncertainty are different states | Copy/badge consistency, no redesigned dashboard |

Optional later work: extract network/status helpers from `app.js`; a force-verification report for archived originals; clearer offline/stale map-cache behavior; timezone-explicit date filtering; spreadsheet-safe CSV text export. These were not all independently reproduced as defects in this review. Do not let them displace preservation repairs or silently grow P12.

## Evidence and review limits

- Cloned the public default branch at the pinned commit. Read the backend archive/import/identity/backup/maintenance/capture/server/parser/security paths, collector, shim, repair tools and relevant frontend/test paths. Original source files were not modified. No private data root, actual flight logs, private screenshots, live controllers or credentials were accessed.
- Ran `python -m flightdesk selftest`: **407 tests; 61 failures; 19 errors; 0 skips**, Linux/Python 3.12, without the owner's installed Windows parser and DPAPI. Many failures cascade from absent integration prerequisites. This does not refute a historical passing Windows run, and does not establish that every failure is environmental. Fresh-checkout reproducibility is the actionable gap.
- Ran `python tools/fault_check.py`: disk-full backup was rejected and cleaned up, follow-up backup validated, interrupted extraction preserved the existing target marker, and a later restore succeeded. This useful harness does not test the missing-inventory or foreign-staging defects above.
- Ran `python tools/recon_check.py`: **6/11 passed**, five decoded-flight/metric checks failed with the parser absent. Do not present that as an independent arithmetic failure.
- Ran **19 disposable probes** in `review_probes.py`. Normalized parser outcomes were injected when testing storage/identity behavior so those cases do not depend on the missing binary. Results cover the concrete behaviors recorded above. The header-only USB case demonstrates cheap validation permissiveness, not actual MTP transfer behavior. Probe results are evidence about the baseline, not tests asserting repaired behavior.
- No browser end-to-end, Windows DPAPI, actual MTP copy, Windows rename/junction, real DJI key exchange or private-log comparison was performed. These remain explicit gates. The review does not certify flightworthiness, manufacturer maintenance intervals or regulatory compliance.
- The old review brief is partly stale: it says 331 tests and flags silent watcher catches; the checkout has 407 tests and watcher error reporting. Existing notes already acknowledge a single real-log comparison, limited long-running controller observation and incomplete visual verification. This review's preservation and accounting findings are additional mechanisms, not a repetition of those acknowledged gaps.

## Completion criteria

Source work is complete when every reproduced defect has a behavioral regression guard, runnable batches are finished, data-preservation invariants hold, and docs describe the actual program. Trusting the real archive additionally requires a verified independent backup/restore, approved historical-repair review, representative private-log comparisons and the Windows/controller checks in `FLIGHTFIX-HUMAN.md`.
