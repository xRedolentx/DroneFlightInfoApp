# BUILD-STATUS

Flight Desk · stage-by-stage status against `FlightDesk-DeepSeek-Build.md`.
Last updated: 2026-11-18. **The real archive is now imported and the real
equipment capture is applied** to the live data root; the real-input checks that
were pending are recorded below. Automated checks still use synthetic fixtures.
Resume notes are in `CHECKPOINT.md`.

## How to reproduce

```powershell
python -m flightdesk selftest        # full suite, prints a JSON summary
python tools\rehearse.py             # end-to-end import → capture → backup → restore
python tools\diag.py "<zip|folder>"  # aggregate-only real-input check
node tools\ui_check.mjs <cdp-port> <outdir> [zip]   # browser checks, app running
```

The browser gate now drives whatever loopback app the debugger is attached to
and ignores browser-internal pages; it used to hardcode port 8787 and could
attach to an Edge sync page, which produced false failures on a fresh profile.

Parser: `dji-log` **0.5.7**, pinned upstream release binary
(`dji-log-x86_64-pc-windows-msvc.zip`, SHA-256
`C036D9314BDFA72A9C13E93B956CCA89747637C4D5C2A0CABED64A7544242DFD`), installed at
`%LOCALAPPDATA%\FlightDesk\vendor\dji-log\0.5.7\dji-log.exe` with a manifest.
Keychain decoding uses `flightdesk-decode`, built locally from `shim/` against
`dji-log-parser =0.5.7` (Cargo.lock copied to the vendor folder).

## Stage results

### Stage 1 · Offline import and archive — **verified on synthetic data**

- 20 automated checks pass (`tests/test_stage1_import.py`).
- v8 and v11 logs each produce real summaries from the pinned parser (duration,
  distance, max height, aircraft, start time with second precision).
- Originals are hashed, stored immutably, and re-read to confirm the hash.
- Re-importing a folder, a renamed exact duplicate, or the same ZIP twice adds
  zero logical flights; the second pass reports every file as already imported.
- A 4-thread manual-vs-watcher race on one file yields one raw file, one flight
  and three duplicate outcomes.
- Same-time different-aircraft files stay two flights; a weak same-time match is
  kept but flagged `unresolved` and excluded from totals; strong corroboration
  merges while keeping both raw variants.
- ZIP handling rejects `..` and absolute members, oversized members and
  symlinks; macOS metadata is ignored. Folder walk never follows symlinks.
- Damaged and parser-failing files are retained with a category, no flight.
- **ZIP import is exercised end to end in the browser** (65-check run): a
  handoff ZIP is attached through the real `Choose ZIP` input, every eligible
  member is reported, the summary survives a page refresh, a repeat import adds
  nothing, and a non-ZIP file produces a stated reason instead of silence.
- Container rules: duplicate member names are accepted (the content hash
  dedupes), escaping or absolute paths, symlinks, encrypted entries and
  oversized members are rejected before extraction, and macOS metadata is
  skipped.
- Container rules: duplicate member names are accepted (the content hash
  dedupes), escaping or absolute paths, symlinks, encrypted entries and
  oversized members are rejected before extraction, and macOS metadata is
  skipped.
- **Verified on the real archive (2026-11-18).** `python tools\diag.py "<handoff.zip>"`
  returned `distinct_files=264`, `logical_flights=264`, `unresolved=0`,
  `errors={}` with versions **v8:7, v11:218, v14:39** and statuses
  `imported:225, pending_key:39`. Adding the supplied controller folder gave
  `distinct_files=269` — archive 264 (7/218/39) plus extras 5 (v11:4, v14:1) —
  i.e. **7 / 222 / 40 across 269 distinct hashes**, exactly the brief's table.
- **Verified on the real archive:** re-importing the extracted folder reported
  `new=0, already_imported=264, flights_added=0`.
- The live data root holds those 264 raw files and 264 authoritative flights.
  The 39 v14 records are `pending_key` and carry metadata only.

### Stage 2 · Equipment and maintenance — **verified on the real capture**

The real capture is a **different document shape** from the synthetic fixture,
which is why it previously migrated as all-unknown counters. It carries an
8-column drones table, a per-item rule grid (`Item | Basic | Extended | Full`),
a 17-column battery table whose `Basic`/`Full` columns hold the battery's own
counters, and a 5-column service history. The parser now classifies tables by
shape as well as heading and reads all three:

- 34 automated checks pass (`tests/test_stage2_equipment.py`), 12 of them
  pinning the real capture's shape on a synthetic fixture built to match it.
- **Synthetic fixture:** the parser reads headings + pipe tables: 3 current
  aircraft, 4 historical (archived), 20 battery entries with 3 locked and 1
  placeholder.
- **Synthetic fixture:** 21 service rows, of which **17 are individually
  resolved and 4 stay grouped** (multi-battery rows, two-rule rows, a date
  range, a retired-fleet row). Grouped rows keep their original wording, range
  text and counter text; nothing is split into invented per-item timestamps.
- **Synthetic fixture:** archive-only baselines match the brief exactly: Mavic 2
  Pro Extended **188/100 flights, 1018/1000 min (Due)**; its Basic **19/20 (Due
  soon)**; Air 3S Basic **16/20 (Due soon)**; Mavic 2 battery C Basic **30/20
  (Due)**.
- Archive→seed, seed→archive, repeat migration and late v14 all produce an
  identical baseline signature (one test compares all four).
- Complete, Skip, one-off (linked and unlinked), Undo (with reversal record),
  reassignment and late discovery behave as specified and recalculate.
- The 32°F placeholder stays missing for that capture only; a variant capture
  with a genuine 32°F reading keeps it.
- Default due-soon threshold is 0.80 per rule and is editable; disabled rules
  drop out of Upcoming.
- **Real capture, verified 2026-11-18** (scratch roots, real file read only by
  local code): 3 current aircraft, **3 archived aircraft named only in the
  service history**, **20 battery entries** (17 readable + 3 identified
  placeholders for the entries the capture lists but cannot read), 43 rules,
  **72 snapshot counters**, 13 service rows (12 individually resolved, 1
grouped).
- Positional counter cells are read as the capture's own columns prove them:
  two pairs are flights then minutes; three pairs are charges, flights then
  minutes (matched against the `Chg`/`Logs`/`Air` columns and the 20/200 and
  160/1600 limits).
- The real pass criteria hold: Mavic 2 Pro Extended **188/100 flights and
  1018/1000 min → Due**; its Basic **19/20 → Due soon**; Air 3S Basic
  **16/20 → Due soon**; the Mavic 2 battery ending 3402QS Basic **30/20 flights
  → Due**. Air time 62 and 111 minutes for the two Basic rules.
- Archive-then-capture and capture-then-archive produce an identical baseline
  signature; a repeat migration is a no-op; applying the capture after an
  import now also backfills the battery readings for already-imported flights.
- **Applied to the live root** after a rollback copy: 26 equipment rows (3
  current, 3 archived reference-only, 20 batteries), 43 rules, 72 snapshot
  counters, 13 service rows. Live statuses: **2 due, 5 due soon, 20 ok, 16
  unknown**.
- **Honest gaps in the real capture:** it states **21 service records but
  supplies 13 rows**, so 8 are unaccounted and are reported as such, never
  invented; **16 battery rules stay `unknown`** because those batteries
  published no counter (a missing counter is not a zero); three aircraft are
  named only in the service history and are kept archived, out of reminders.

### Stage 3 · Screens — **verified in a real browser (Edge, headless via CDP)**

- `node tools/ui_check.mjs` drives the running app: **65/65 checks pass**,
  including an assertion that no request leaves loopback;
  covering list width ratio (312/1158 px), three-column records, filters
  applying to list and tiles together, filter persistence, flight → detail,
  track over a local grid with a "no tiles" caption, gap shading, scrubber
  updating the readout, all six tabs, arrow-key navigation with section
  preserved, battery → rule dialog, maintenance grouping and two-column cards,
  Complete → Completed → Undo, metric/imperial conversion, privacy mode with
  banner, 390 px single-column mobile with a list/detail switcher and no
  horizontal overflow, and the empty state.
- Screenshots (synthetic data only) are written to
  `%LOCALAPPDATA%\FlightDesk\screenshots\` — outside the repo.
- A local comparison page (`#/compare`) pairs a reference image you name with
  the live app; the image is served only to the authenticated loopback page.
- Three largest layout differences fixed after the first captures: missing
  static-asset mapping (`/assets/…`), a CSP that silently blocked inline style
  widths (progress bars, two-column layouts), and 390 px header overflow.
- **Not verified:** side-by-side judgement against the 23 original screenshots
  (they are on Kevin's machine, not here). The comparison page exists for that.

### Stage 4 · USB import — **simulated only; physical pending**

- 13 automated checks pass (`tests/test_stage4_usb.py`) against a simulated
  adapter: hidden extensions, duplicate storage, both devices together,
  interrupted and unstable copies staying retryable, reconnect, watcher restart,
  second plug-in adding zero, manual-import convergence, non-flight files
  ignored.
- The physical collector is `tools/Collect-DjiLogs.ps1` (Shell.Application,
  read-only, `CopyHere` with stability/size waits, unique staging per transfer,
  hash verification, bounded timeout, disconnected reads never marked imported).
- **Pending Kevin:** plug in each powered controller in file-transfer mode;
  first attach should import eligible logs, second should add zero. Results must
  be recorded separately for the Smart Controller and RC 2. The supplied 5
  controller samples cannot certify the live hardware path.

### Stage 5 · DJI key — **verified end to end with a real key**

- 16 automated checks pass (`tests/test_stage5_key.py`).
- The key is sent only in the `Api-Key` header to the documented endpoint,
  never in the body, URL, arguments or logs (asserted with a local test server).
- Storage is DPAPI-protected; a scan of the database bytes for the key string
  finds nothing.
- The KeychainsRequest body is rebuilt from the log's own KeyStorage records and
  matches the pinned interface (`version`, `department`, `keychainsArray`).
- Invalid key, API error, offline and missing-cache cases return categories and
  create no extra flights.
- Without a key, v13/v14 metadata (date, aircraft, battery serial, duration) is
  read from the XOR-encoded auxiliary block and shown; `metrics_source` is
  `metadata_only`.
- **Verified 2026-11-19 with the owner's real developer key.** Save key → Decode
  pending fetched **39 keychains**, cached them, and decoded **all 39 v14 records
  to frames** (median 3,435 samples per flight). The audit log records the
  keychain fetch, the batch decode, and then exactly one further reparse at
  16:15:18 UTC from **Settings → DJI key → Re-decode one record from cache** with
  the owner's network disconnected — the offline keychain proof. No new flight
  was created by any reparse.
- **The first "could not decode" message was a reporting bug, not a decode
  failure.** The endpoint tested `all(results)` and displayed `results[0].code`,
  so an empty target list (clicking Decode pending a second time) read as
  failure, and a real failure could be masked by a successful first result. It
  now returns `nothing_pending`, `decoded`, `failed` and the distinct failure
  codes, and the UI says which.
- **Still unverified by observation:** the Air 3S reference figures (5m02s,
  154 ft, 49 ft, 148 ft, 14.54 mph, 82.9°F, 94%→81%) have not been compared
  against the decoded record. The data is now available to do it.

### Stage 6 · Offline analysis — **verified on synthetic + one real decode path**

- 16 automated checks pass (`tests/test_stage6_analysis.py`) plus the UI checks:
  path length vs max home distance, height vs altitude, recorder vs airborne vs
  details duration, a 115 s hole not drawn as a straight line, implausible jumps
  flagged, missing fix/signal/battery marked missing rather than zero, adaptive
  gap threshold, downsampling that keeps endpoints.
- The track is drawn on a locally computed metre grid with a "no tiles" caption;
  charts share one cursor; gaps are shaded; the readout follows the scrubber.
- External weather/tiles/geocoding are off by default and the UI states what a
  provider would receive before any consent is recorded. No provider is
  configured, so nothing is transmitted.
- **Not verified:** at least one locally decoded real flight with full frames
  (needs Stage 5's key).
- **Truth fixes made from real data (2026-11-18).**
  - A temperature reported as `0.0` for **every** sample is absence, not a
    reading: 217 of the 224 decoded records were affected and are now unknown,
    while a genuinely freezing flight (which varies around zero) is kept.
  - Measurements beyond physical limits — 12 h duration, 500 km distance,
    1500 m height, 9000 m altitude, 100 m/s — become unknown with an
    `implausible_*` flag instead of entering a total. One real record claimed
    **191 hours** and alone inflated the archive's air time from 24h21m to
    215h.
  - Media is bounded too: one record reported **1,176,826,604 photos**, and
    video seconds may not exceed the flight's own duration plus ten minutes.
    The Media tile now says "flights with video" rather than "video clips",
    because that figure counts flights, not videos.
  - **Metadata-only records no longer carry unverified measurements.** Without
    the key, a v13/v14 record keeps its date, aircraft, battery serial and
    duration; distance, height, speed, position and media become unknown. The
    reasoning that led here was right for the wrong reason: those 39 records were
    not corrupt, they were in **kilometres** (see Stage 7). Keeping them out of
    the total was still correct until the unit was understood.
  - **v13/v14 distance unit corrected.** `distance_metres()` converts
    kilometres to metres for v13 and later, measured at a ratio of 1000.006
    against the frame-derived path across all 39 records.
  - **"Could not decode" was a reporting bug, not a decode failure.** The
    endpoint tested `all(results)` and showed `results[0].code`, so an empty
    target list (clicking Decode pending a second time) was reported as a
    failure, and a genuine failure could be masked by a successful first result.
    It now returns `nothing_pending`, `decoded`, `failed` and the set of failure
    codes, and the UI states which. Covered by two tests.
  - Backfill works: a reparse now refreshes the metrics of the flight it
    matches (equal weight refreshes, a poorer parse never replaces a richer
    one), and a reparse can no longer create a duplicate flight when a metric
    correction changes the identity fingerprint.

### Stage 7 · Backup, recovery, cutover — **verified on synthetic data**

- 12 automated checks pass (`tests/test_stage7_backup.py`); `tools/rehearse.py`
  passes all 13 end-to-end steps on a fresh root.
- Backup uses the SQLite backup API, copies every archive object, verifies each
  hash, writes a manifest with schema version and counts, and records the run.
- **Interrupted backup is now safe.** The archive is written to `*.part` and
  renamed only after the ZIP closes, so a disk-full or crash cannot leave a
  truncated file that looks like a real backup. A failure returns
  `{"ok": false, "code": "disk_full"}`, records a `failed` run, releases the
  lock, and the next backup validates. Covered by three tests plus
  `tools\fault_check.py`.
- **Disk full during import** keeps the index clean: one `needs_attention` item,
  `note = disk_full`, no raw file, no flight, no leftover `ingest-*.part`.
- Restore extracts to a new root, re-verifies hashes and `foreign_key_check`,
  refuses the live root, refuses a populated target without confirmation, and
  refuses a corrupted archive.
- A cloud-sync destination is refused unless explicitly confirmed.
- Reconciliation reports archive / supplied controller / later controller /
  combined separately with signed differences, excluded counts, and decoded
  coverage; the first archive report is frozen as a baseline so later imports
  do not move it.
- CSVs are produced for flights, equipment and services, plus a redacted mode.
- **Real reconciliation, measured 2026-11-19 after the DJI key decoded all 39
  encrypted records**: records **264 (match)**; distance **108.3 mi against the
  108 mi reference** — a match at the reference's displayed precision; air time
  **24h21m against 24h10m**; photos **983 against 749**; video count **unknown**
  (reference 459); decoded coverage **100 %**.
- **The distance figure is corroborated twice.** The log's own `totalDistance`
  sums to 174.23 km and the path computed independently from the decoded frame
  coordinates sums to 174.24 km — two unrelated measurement paths agreeing to
  10 m across 264 flights.
- **The v13/v14 distance unit was the cause, and it is fixed.** v6–v12 record
  total distance in **metres**; v13/v14 record it in **kilometres**. Measured
  from the archive: for v8 and v11 the log's figure matches the frame-derived
  distance at a ratio of 1.0000, for v14 at 1000.006 across all 39 records. Read
  unconverted, 39 real flights contributed 0.02 km instead of 21.3 km. An earlier
  note here called those values "corrupt"; they were not — the unit was wrong.
  `distance_metres()` now converts per version, and the synthetic v13/v14 fixture
  writes kilometres so the fixture tells the truth.
- **The remaining air-time gap is explained, and it is two records.** The
  reference's own numbers are not self-consistent: no subset summing to 264
  records produces 24h10m. Dropping exactly the **two newest flights** —
  2026-08-06 21:03 (6.75 min) and 2026-08-12 05:53 (5.07 min) — makes **both**
  reference figures land at once: 1449.3 min (24h10m) and 173.78 km (**108.0 mi
  exactly**). Two independent quantities matching simultaneously is not a
  coincidence; the AirData export the reference was taken from was short by
  those two flights while the file inventory still counted 264. The brief itself
  warns the count "counts files, not distinct flights".
- **Photos: not comparable, and now labeled as such.** Our 983 is the sum of the
  per-flight photo counter in each log. Two properties rule out the obvious
  explanations: the counter is **not** a lifetime total (36 decreases across 262
  consecutive flights, and per-aircraft sequences decrease too, so summing is
  correct), and it is **not** a file count — AirData's own 749 exceeds the
  **180 media files that actually exist** on the owner's copy. Three different
  quantities were being compared as if they were one. The reconciliation no
  longer claims a signed difference for photos and states what the figure is;
  the Flights tile reads "photos from logs".
- **Media "events" were counting states.** `is_video` is true on every frame
  while recording, so summing it reported 159,592 "video events" on the real
  archive. It now counts rising edges — segments that actually started. The
  figure was never displayed, but it was wrong, and would have been misleading
  if it ever were.
- **Pending Kevin:** decide cutover. Keep AirData active until you have read the
  reconciliation with *Mark records as my real inputs* ticked.

## Release gate

| Item | Verdict | Evidence | Next human action |
|---|---|---|---|
| Fresh install + restart | **Verified** | Fresh empty root serves the app; `data_mode=empty`, 0 counts; launcher returns HTTP 200; restart keeps data | none |
| Archive inventory (269 files) | **Verified 2026-11-18** | `tools\diag.py`: 264 archive (7/218/39) + 5 controller (0/4/1) = **269 distinct, 7/222/40**; re-import added 0 | none |
| Decoded totals (264 · 24h10m · 108 mi) | **Explained** | 264 records; 108.3 mi vs 108 (match at displayed precision); 24h21m vs 24h10m is exactly the 2 newest flights the reference export lacked; frames-derived distance agrees independently | Enter the key (done) and read the signed difference with *real inputs* ticked |
| Due statuses at handover | **Verified on the real capture** | 188/100 + 1018/1000 Due; 19/20 and 16/20 Due soon; battery 30/20 Due | Re-check after any service record |
| Both real USB devices | **Pending Kevin** | Simulated adapter covers 13 cases incl. interrupted copy and reconnect | Plug each controller in twice; expect zero new records on the second attach |
| No duplicate imports | **Verified** | Folder, ZIP, renamed duplicate, race, re-scan and reparse all add zero | none |
| Key-cached offline reparse | **Verified 2026-11-19** | Real key decoded all 39 v14 records; one re-decode from cache at 16:15 UTC with the network off, no new flight | none |
| Working backup recovery | **Verified** | 12 tests + 13-step rehearsal, including corrupted archive and live-root refusal | Do one rehearsal on your real data before cancelling |
| Video count comparison | **Discrepancy (by design)** | Logs give video seconds, not a count; `videos_count` is null. Photos are no longer compared: the reference claims 749 while only 180 media files exist | Supply a video count if AirData shows one |

**Keep AirData active while any material difference is unexplained.** A final
fresh export/import rehearsal is offered before the February 2027 renewal;
the cancellation decision is Kevin's.

## Overall counts

- 155 automated tests, all passing (`python -m flightdesk selftest`).
- 68 scripted browser checks, all passing (including the no-egress and privacy
  assertions, a real ZIP import through the picker, and the equipment-capture
  preview gate).
- 13-step backup/restore rehearsal, all passing.
- 11/11 reconciliation checks (`python tools\recon_check.py`) and the
  interrupted-backup fault check (`python tools\fault_check.py`).
- Real-input aggregate diagnostics: 269 distinct files at versions 7/222/40;
  real capture applied with the four due-status criteria met.

Exact commands and outputs are in `VERIFICATION.md`.

## Discrepancies / open items

1. **Real inputs are now in use, aggregate-only.** The archive, the capture and
the controller samples were read by local code only; this record contains counts
by source/version/status and no filename, path, serial or coordinate. The app
still cannot decide on its own that records are real: until *Mark records as my
real inputs* is ticked, the reconciliation screen refuses to show a signed
difference against the 264/24h10m/108mi reference, and the header badge reads
"synthetic". Ticking it is the owner's call.
2. **Decoded totals disagree with the reference** (see Stage 7): air time
   +11 minutes, distance −13.0 miles, photos +234, with 39 of 264 records still
   undecoded. The direction of the air-time difference is unexplained.
3. **The real capture accounts for 13 of the 21 service records it claims.**
   The 8 missing rows are reported, not invented. 16 battery rules are
   `unknown` because those batteries published no counter.
4. **Physical USB and the DJI key remain unexercised.** Both are owner tasks.
   `tools\Collect-DjiLogs.ps1` was executed on this machine with no controller
   attached and returned a clean `no_device` in 0.3 s, so the script parses and
   runs; it has never enumerated a real controller.
5. **Screenshots 01/03/19/20/21–23 comparison** needs Kevin's reference images.
   The in-app comparison page pairs them without uploading.
6. **Restore interruption** is only tested at its ends (validation failure,
   live-root refusal, corrupted archive), not injected mid-extraction.
   **Backup during an active USB scan** is covered by a settings-level test, not
   a genuinely concurrent scan. Both are named in `CHECKPOINT.md`.
7. **Video count and video seconds** are reported as seconds only; the capture
   publishes no video count, so `videos_count` stays `null` instead of being
   derived. Video seconds are now bounded by flight duration, but the field's
   exact semantics are still unconfirmed against AirData.

## Privacy posture (what was never in the transcript)

No model file-read or vision tool touched a private input. Every automated test
uses invented data. Real inputs were processed only by local code: `tools\diag.py`
principles applied throughout — counts by source/version/status, coarse error
categories, and the specific reference figures the brief itself publishes. No
filename, path, serial, coordinate, record row or raw stderr was printed.
Screenshots live outside the repo. Rollback copies of the live data root taken
before each real-data change live under `%LOCALAPPDATA%\FlightDesk\` and never
enter version control.
