# BUILD-STATUS

Flight Desk · stage-by-stage status against `FlightDesk-DeepSeek-Build.md`.
Last updated: 2026-11-17. Data used for every automated check below is
**synthetic** (invented aircraft, serials, dates, routes).

## How to reproduce

```powershell
python -m flightdesk selftest        # full suite, prints a JSON summary
python tools\rehearse.py             # end-to-end import → capture → backup → restore
node tools\ui_check.mjs 9223 <dir>   # scripted browser checks (needs the app running)
```

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
- **Not verified:** the supplied 264/5-file archive and the table totals
  (269 files, 7/222/40 by version) — those inputs are not on this machine. Run
  `python tools\diag.py "<folder-or-zip>"` locally; it prints only counts by
  source/version/status and coarse error categories.

### Stage 2 · Equipment and maintenance — **verified on the synthetic capture**

- 22 automated checks pass (`tests/test_stage2_equipment.py`).
- The capture parser reads headings + pipe tables: 3 current aircraft,
  4 historical (archived), 20 battery entries with 3 locked and 1 placeholder.
- 21 service rows, of which **17 are individually resolved and 4 stay grouped**
  (multi-battery rows, two-rule rows, a date range, a retired-fleet row). Grouped
  rows keep their original wording, range text and counter text; nothing is
  split into invented per-item timestamps.
- Archive-only baselines match the brief exactly: Mavic 2 Pro Extended
  **188/100 flights, 1018/1000 min (Due)**; its Basic **19/20 (Due soon)**;
  Air 3S Basic **16/20 (Due soon)**; Mavic 2 battery C Basic **30/20 (Due)**.
- Archive→seed, seed→archive, repeat migration and late v14 all produce an
  identical baseline signature (one test compares all four).
- Complete, Skip, one-off (linked and unlinked), Undo (with reversal record),
  reassignment and late discovery behave as specified and recalculate.
- The 32°F placeholder stays missing for that capture only; a variant capture
  with a genuine 32°F reading keeps it.
- Default due-soon threshold is 0.80 per rule and is editable; disabled rules
  drop out of Upcoming.
- **Not verified:** the real `data/AirData-Equipment-Capture-20261117.md`. The
  parser is written for its shape (headings + pipe tables) and tested on a
  fixture in that shape; point Settings → Damage capture → Preview at the real
  file to compare before applying.

### Stage 3 · Screens — **verified in a real browser (Edge, headless via CDP)**

- `node tools/ui_check.mjs` drives the running app: **45/45 checks pass**,
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

### Stage 5 · DJI key — **mechanism verified; live decode pending a real key**

- 14 automated checks pass (`tests/test_stage5_key.py`).
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
- **Pending Kevin:** enter a valid key and decode one real v14 file, then close,
  disconnect the network and re-open to confirm the cached keychain works
  offline. The Air 3S reference flight figures (5m02s, 154 ft, 49 ft, 148 ft,
  14.54 mph, 82.9°F, 94%→81%) can only be compared against a real decode.

### Stage 6 · Offline analysis — **verified on synthetic + one real decode path**

- 10 automated checks pass (`tests/test_stage6_analysis.py`) plus the UI checks:
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

### Stage 7 · Backup, recovery, cutover — **verified on synthetic data**

- 12 automated checks pass (`tests/test_stage7_backup.py`); `tools/rehearse.py`
  passes all 13 end-to-end steps on a fresh root.
- Backup uses the SQLite backup API, copies every archive object, verifies each
  hash, writes a manifest with schema version and counts, and records the run.
- Restore extracts to a new root, re-verifies hashes and `foreign_key_check`,
  refuses the live root, refuses a populated target without confirmation, and
  refuses a corrupted archive.
- A cloud-sync destination is refused unless explicitly confirmed.
- Reconciliation reports archive / supplied controller / later controller /
  combined separately with signed differences, excluded counts, and decoded
  coverage; the first archive report is frozen as a baseline so later imports
  do not move it.
- CSVs are produced for flights, equipment and services, plus a redacted mode.
- **Pending Kevin:** decide cutover. Keep AirData active while any material
  difference is unexplained.

## Release gate

| Item | Verdict | Evidence | Next human action |
|---|---|---|---|
| Fresh install + restart | **Verified** | Fresh empty root serves the app; `data_mode=empty`, 0 counts; launcher returns HTTP 200; restart keeps data | none |
| Archive inventory (269 files) | **Pending Kevin** | Synthetic ZIP/table shapes verified; real archive absent here | Run `python tools\diag.py "<handoff zip>"`; expect 7/222/40 versions and 269 distinct hashes |
| Decoded totals (264 · 24h10m · 108 mi) | **Pending Kevin** | Reconciliation groups + signed differences implemented and tested on synthetic data | After import, tick *Mark records as my real inputs* and read the signed difference |
| Due statuses at handover | **Verified on synthetic capture** | 188/100 + 1018/1000 Due; 19/20 and 16/20 Due soon; battery 30/20 Due | Re-check after applying the real capture |
| Both real USB devices | **Pending Kevin** | Simulated adapter covers 13 cases incl. interrupted copy and reconnect | Plug each controller in twice; expect zero new records on the second attach |
| No duplicate imports | **Verified** | Folder, ZIP, renamed duplicate, race and re-scan all add zero | none |
| Key-cached offline reparse | **Mechanism verified** | DPAPI cache + local decoder produce frames without network | Enter a real key, decode one v14, disconnect, restart, reparse |
| Working backup recovery | **Verified** | 12 tests + 13-step rehearsal, including corrupted archive and live-root refusal | Do one rehearsal on your real data before cancelling |
| Video count comparison | **Discrepancy (by design)** | Logs give video seconds, not a count; `videos_count` is null | Supply a video count if AirData shows one |

**Keep AirData active while any material difference is unexplained.** A final
fresh export/import rehearsal is offered before the February 2027 renewal;
the cancellation decision is Kevin's.

## Overall counts

- 120 automated tests, all passing (`python -m flightdesk selftest`).
- 45 scripted browser checks, all passing (including the no-egress and privacy
  assertions).
- 13-step backup/restore rehearsal, all passing.

Exact commands and outputs are in `VERIFICATION.md`.

## Discrepancies / open items

1. **Real inputs never touched.** No archive, ZIP, capture or controller data
   exists on this machine. Every real-data number in the brief is therefore
   unverified here, and the app says so: with `real_inputs_selected = false`
   the reconciliation screen refuses to show a signed difference against the
   264/24h10m/108mi reference. The reference itself is now stored as data with
   its source, scope, precision and date.
2. **Physical USB and the DJI key are unexercised.** Both are owner tasks.
   `tools\Collect-DjiLogs.ps1` was executed on this machine with no controller
   attached and returned a clean `no_device` in 0.3 s, so the script parses and
   runs; it has never enumerated a real controller.
3. **Screenshots 01/03/19/20/21–23 comparison** needs Kevin's reference images.
   The in-app comparison page pairs them without uploading.
4. **Video count** is reported as seconds only. The capture publishes no video
   count, so `videos_count` stays `null` instead of being derived.

## Privacy posture (what was never in the transcript)

No model file-read or vision tool touched a private input — there were none to
touch. All test data is invented. `tools/diag.py` is the only path intended for
real inputs and a test asserts its output contains no filenames, paths or
serials. Screenshots live outside the repo. No raw stderr is surfaced: parser
stderr is captured, classified into a category, and discarded.
