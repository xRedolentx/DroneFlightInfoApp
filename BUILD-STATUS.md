# BUILD-STATUS

Flight Desk · stage-by-stage status against `FlightDesk-DeepSeek-Build.md`.
Last updated: 2026-11-21. **The real archive is imported, the real equipment
capture is applied, and both DJI controllers have been read from real hardware.**
Live totals are **284 records · 419 raw originals · 26.16 h · 116.0 mi**, of which
two records are unreadable and excluded; the AirData reference stays scoped to the
264-record archive so the comparison keeps its meaning. The stage records below
state the counts as they stood when each stage was verified. Automated checks still
use synthetic fixtures. Resume notes are in `CHECKPOINT.md`.

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

### Stage 4 · USB import — **both controllers verified on real hardware, 2026-11-21**

The Smart Controller was attached and scanned, then the **DJI RC 2** was attached
and scanned through the app's own path (Settings → *Check controllers now*, driven
over HTTP against the live root). Each first attempt exposed real defects, all
silent.

- **`CopyHere` was called on the source folder.** It copies INTO the folder it is
  invoked on, so calling it on the source was a no-op: it returned in ~20 ms and
  wrote nothing. Measured: **0 bytes staged after 800 s** with 250 logs attached.
  The destination is now a `NameSpace` folder object for each staging directory;
  the same copy then took **0.3 s** for a 968,945-byte log.
- **A `FolderItem` was passed where a `Folder` was expected.** `$storage.Items()`
  throws on a FolderItem, the throw was caught, no error was recorded, and the
  scan reported `no_files` while 250 logs sat on the device. `Get-Folder` now
  normalises either shape, and lookup failures are recorded as errors.
- **MTP reports `.Size = 0`, not null.** Taking that as a real size made every
  copy look like a size mismatch. The size now comes from
  `ExtendedProperty('System.Size')` (which correctly said 968,945), and a zero is
  treated as unknown.
- **Found while fixing:** the per-file wait was 60 s each, so a broken transfer
  would have ground for four hours. Three consecutive failures now abort the
  scan with `copy_not_progressing`.

**Smart Controller: 259 logs read, 153 raw files imported.** 18 are genuinely new
flights — including four on 2026-11-15 that exist only on the controller and were
never in the AirData export; the other 135 raw files are variants of flights
already held (different bytes, same flight by fingerprint), and both originals
are kept.

**RC 2: 17 logs read, 1 new record.** It is `FlightRecord_2026-08-21`, a **v14
encrypted log** — stored as `pending_key` with its metadata (date, aircraft,
1090.4 s duration) and no measurements, which is the documented rule for an
encrypted record without its keychain.

Both pass the acceptance criterion — **the second attach adds zero**: Smart
Controller 0 imported / 259 duplicates in 6 s; RC 2 0 imported / 17 duplicates
in 1 s. Live totals: **284 records · 26.16 h · 115.6 mi**.

Reporting defects found by reading what the scan actually said, not what it was
meant to say:

- **"imported 0" while a flight had been created.** A v14 record lands in
  `awaiting_dji`, which was not counted as imported. `imported` now counts stored
  records (`max(new, new_flight)`, so a decoded log reporting both is not
  double-counted), and `awaiting_dji_key` is named in the notes.
- **`attention: 1` with an empty reason list.** Every count that raises attention
  now names its cause.
- **`all_known` scans left no trace.** The early return skipped the scan row, so
  the second attach — the exact evidence the owner is asked to produce — was not
  recorded at all. Every exit path now writes the row, including a check that
  found no device ("looked, nothing was there" is not "nobody looked").
- **A read device reported as never seen.** The collector listed devices from
  copied files only, so a scan that found everything already archived reported no
  device. It now reports the devices it read, and `last_success` means "this
  controller was read", not "this controller supplied something new".
- **One volume counted twice.** The RC 2 exposes the same volume as two storage
  handles, so `scanned` read 34 for 17 logs. Files are deduped by name per device
  before anything is counted.
- **A defect in the fix itself:** the collector added `$deviceName` to the seen
  list *before* assigning it, so it reported no devices at all. Caught by running
  the collector directly rather than trusting the app's summary.

- **A plugged-in controller must not be re-copied on every watcher pass.** The
  watcher rescans every 45 s; the first version copied all 182 MB each time. The
  scan now sends the collector a list of names the archive already holds, and the
  collector skips them before copying. Measured: **59 s → 6 s** on the Smart
  Controller and **0.7 s** on the RC 2, with 0 bytes copied.
- **Two logs on the Smart Controller cannot be read at all** (details offset 0 —
  the same defect as flight 216, and one is byte-identical to that flight's log,
  so the defect is in the file the aircraft wrote). They are now **kept** and
  marked `details_unreadable` rather than discarded at the door: size and hash
  have already proved the bytes are what the device holds, and an unreadable
  flight on the review screen is more honest than a silent absence.
- 29 automated checks pass (`tests/test_stage4_usb.py`): the simulated adapter's
  13 original cases, the unreadable-log rules, the skip list, the 200-second
  bounds, and the reporting contract above.
- **Unplug/replug verified 2026-11-21.** The RC 2 was physically unplugged and
  re-attached; the device re-enumerated and the next scan read 17 logs, found
  all of them already archived, and added nothing: **0 imported, 17 duplicates,
  1.0 s, attention 0**. That is the physical half of the acceptance criterion,
  not just a repeat scan against an unchanged handle.

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
- **The RC 2's v14 log was decoded 2026-11-21** with the stored key: one
  keychain fetched from `dev.dji.com` and cached, then the container decoded.
  Fetch plus decode took **1.6 s**. The record is 18m10s, 718 m, 36 m max
  height, **10,905 frames**, linked to the Air 3S and to `Bat-Air3S-G41V24`, no
  fault flags, peak battery 39.4 °C (inside the 40 °C limit). The track draws
  1,364 points with no gaps. Nothing is pending any more.
- **A successful fetch was never audited.** Only `keychain_fetch_failed` was
  recorded, so the one event that actually spends the owner's credential — an
  outbound POST carrying the API key — left no trace when it worked, while
  this file claimed the audit log recorded the fetch. `keychain_fetched` now
  records the endpoint and the keychain count, never the key or the keychain
  material, with tests for both a fetch and a cache hit that must not claim
  one.
- **Air 3S reference figures — checked 2026-11-21 against the decoded record.**
  The record is flight 260, 2026-08-12 05:53:56 UTC — one of the two flights the
  AirData export lacked. Five of the seven figures match exactly, and the other
  two are conventions, not errors:

  | Reference | Record | Verdict |
  |---|---|---|
  | 154 ft distance | 46.98 m = 154.1 ft | match |
  | 148 ft max height | frames 45.10 m = 148.0 ft | match (the log's rounded field stores 45.0) |
  | 14.54 mph max speed | 6.50 m/s = 14.54 mph | exact |
  | 94% → 81% battery | 94 → 81 | exact |
  | 82.9 °F max temp | 28.3 °C = 82.9 °F | exact |
  | 49 ft max distance | 14.97 m = 49.1 ft **from the first GPS fix**; 15.63 m = 51.3 ft from the recorded home point | explained |
  | 5m02s duration | 302.1 s airborne (`on_ground` false from 2.1 s); 304.2 s whole record | explained |

  The two conventions are now written down rather than assumed. **Max distance:**
  the app measures from the aircraft's recorded home point, which is the
  aircraft's own datum and 0.66 m further out here; AirData measures from the
  first fix. **Duration:** the app stores and totals the record duration; AirData
  appears to show airborne time. Measured across all 263 decodable flights, the
  airborne rule (`on_ground` false) totals **8.5 minutes less** — 24.19 h against
  24.33 h. The archive total uses the record duration, so nothing in the totals
  moves today; switching the headline to airborne time is a one-line change with
  a 24h21m → 24h11m effect, and it is the owner's call, not a silent fix.

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

### Stage 6b · Online basemap — **verified against live OpenStreetMap tiles**

The brief's privacy-first default was a locally drawn grid, and that is still the
default. A real basemap was added later at the owner's request, behind explicit
consent. OSM's own tile policy was read in full before implementing
(`operations.osmfoundation.org/policies/tiles/`), and the design follows it.

- **Tiles are proxied through the local server, not fetched by the browser.** The
  page keeps `img-src 'self'` and `Referrer-Policy: no-referrer`; the server holds
  the consent gate, the identifiable User-Agent, and the on-disk cache. This keeps
  the existing privacy assertions meaningful: the browser still never leaves
  loopback.
- **Policy compliance, each point tested:** a distinct `User-Agent` naming the app
  (never `python-urllib`/`curl`/library defaults), visible `© OpenStreetMap
  contributors` attribution linked to the copyright page, cache TTL of at least 7
  days, `Cache-Control` never suppressed on tiles, only viewport tiles requested,
  and a *Report a map issue* link.
- **Consent is required and reversible.** Off by default. Turning it on shows what
  is sent (tile numbers, which reveal roughly where the flight was) before any
  request. Turning it off stops requests immediately.
- **The proxy cannot be aimed elsewhere.** The provider host is fixed in code, the
  client supplies no URL, and z/x/y are pinned to non-negative integers inside the
  pyramid. `True` is rejected as a coordinate even though Python treats it as `1`.
- **Verified end to end against the real service** on a seeded scratch root: 8
  tile requests, all HTTP 200, the track drawn over real street imagery, the
  attribution legible, and a second view served **entirely from cache**
  (`X-Tile-Cache: hit` × 8, no upstream request).
- **The map pans and zooms.** Drag to move, wheel or trackpad to zoom about the
  pointer, `+`/`−` buttons, and the track takes keyboard focus: arrows pan, `+`
  and `-` zoom, `0` re-fits. Fit track resets the view as well as redrawing.
  Zoom is integer and clamped to 0–19; the centre is clamped inside the mercator
  world so a drag cannot leave the map. Verified in a browser: zoom 18 -> 19 ->
  17, a drag changes the visible tiles, and *Fit track* restores the original
  tile set exactly. Ten tests pin the maths, including that the point under the
  cursor stays still while zooming.
- **One bug worth remembering.** The bounds cache was declared with an
  initializer *after* the first draw, so it wiped the value the draw had just
  stored and every zoom click returned early — the buttons did nothing while
  looking correct. Caught by driving the real browser, not by reading the code.
- **Image requests cannot carry the `X-FD-Token` header**, which the session
  check requires, so tiles would always have 403'd. Rather than weaken that check,
  tiles carry a separate per-run capability token in the path; the tile route is
  the only path exempt from the header check and validates the token itself.

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
- **The DJI key is included (owner decision, 2026-11-21).** The key lives at
  `keychains/dji_key.bin` beside the database rather than inside it, so it is
  carried as `keychains/dji_key.bin` with its own hash in the manifest and
  `key_included: true`. Restore extracts it like any other member. Validation
  treats a hash mismatch as an error, a promised-but-absent key as a warning
  (`key_missing_from_backup`), and a blob belonging to another Windows account
  as a warning (`key_not_decryptable_on_this_account`) rather than a failure —
  the data is intact and the key can simply be re-entered. Rehearsed on the
  real root: a 303.4 MB backup whose restored key decrypts and is identical to
  the live one. **Exposure, stated in the manifest and in Settings:** a ZIP
  carrying the blob is only as safe as the Windows account that can read it.
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
- **Photo and video counts are removed from the app (2026-11-19, owner request).**
  The owner's priority is a durable flight log with tracks, totals and
  maintenance — not media counts — so the feature is gone rather than
  half-trusted. Removed end to end: the dashboard's Media tile, the flight
  detail note, the reconciliation column, the CSV columns, the totals, and the
  parser's reads of the fixed-layout `photoNum`/`videoTime` fields and the
  per-frame `is_photo`/`is_video` flags. The `photo_count`, `video_time_s` and
  `media_source` columns are dropped from the schema, and an idempotent
  migration drops them from existing databases.
- **Why removal was the right call, not just the owner's preference.** Three
  different quantities were being compared as though they were one: our 983
  (sum of per-flight log counters), AirData's 749, and the **180 media files
  that actually exist**. The counter is not cumulative (36 decreases across 262
  consecutive flights, so summing was correct) and AirData's figure exceeds the
  files it holds, so no honest reconciliation was possible.
- **Migration is non-destructive and reversible.** `SCHEMA_VERSION` stays at 1 on
  purpose: bumping it would make the owner's existing backups fail restore
  validation. The migration is keyed on column presence, so old backups restore
  and are migrated on open. Live check: 264 records before and after, air
  seconds and distance identical to the millisecond, `foreign_key_check` clean,
  `integrity_check` ok, and a second open is a no-op.
- **Pending Kevin:** decide cutover. Keep AirData active until you have read the
  reconciliation with *Mark records as my real inputs* ticked.

### Stage 9 · Flight warnings — **verified on the real archive**

The brief asks each flight record to track location, speed, battery and
*warnings*. Location, speed and battery were already stored; warnings were not,
even though the decoded frames have carried them all along.

- **Fourteen fault conditions are recognised**: compass error, motor blocked,
  not enough force, propeller catapult, out of limit, vibrating, barometer dead
  in air, accelerometer out of range, vision/obstacle sensor error, battery
  voltage warning (with its level), gimbal stuck, SD card full/problem, and motor
  start failure with its cause.
- **Benign constants are deliberately excluded.** Scanning the real archive
  showed several fields are always present with a harmless value:
  `isImuPreheated` is `True` on every frame, `imuInitFailReason` reads
  `MonitorError` throughout, `nonGpsCause` reads `Already`, and the gimbal
  at-limit flags are normal at the ends of travel. Treating "present and
  truthy" as a fault would have raised an alarm on every flight forever, so each
  exclusion is written down in `FAULT_FLAGS`' comment.
- **A sustained flag is one event, not hundreds.** Frames are aggregated into
  runs reporting start, end and duration, so the panel reads "Not enough force,
  04:19, 9 s" rather than "91 frames". A run ends at the last sample that
  actually raised it, not at the first clean one.
- **Three provenance states, never collapsed.** A flight that was inspected and
  found clean shows "no fault flag was raised"; a record that was never decoded
  says faults are pending on the DJI key; a record with no stored frames says
  they could not be read. None of those may display as "0 warnings".
- **Real backfill:** all 264 records re-decoded; **76 flights carry at least one
  fault** — voltage warning 38, compass error 33, not enough force 32, SD card 5,
  gimbal stuck 2, motor blocked 1. Verified in the browser: the panel lists them
  with times and durations, and the flight list shows a warning count on the 76
  affected records.
- 19 new tests cover extraction, aggregation, provenance and the JSON round trip.

### Stage 10 · Battery temperature limits — **verified on the real archive**

Temperature was stored and displayed but never compared to anything: a reading
on its own cannot tell you a pack ran hot. It is now judged against an explicit,
editable policy.

- **Where the numbers come from.** No temperature limit is published in the DJI
  log — the frames carry a reading and nothing more — so the limits are local
  policy. Defaults are **0 °C cold / 40 °C hot**, with a **10 °C margin** that
  turns an excursion into a warning rather than a caution. Settings says plainly
  that these are the app's own defaults, not the manufacturer's figures, and all
  three are editable. A pair where hot is not above cold is refused.
- **Three answers, not two.** In limits → *ok*. Beyond them → *caution*, and far
  beyond (past the margin) → *warning*. A record with no temperature reading
  reports *no reading*, never *ok*: absent is not safe.
- **Real result:** of 264 records, 46 carry a temperature (the rest report 0 for
  every sample, which is already treated as absent). **Five batteries have run
  above the 40 °C limit**, peaks 44.0–53.6 °C, worst `Bat-Mavic3-A01WUJ` with 8
  flights and a 52.3 °C peak. None has been below the cold limit.
- **A false cold reading was caught and fixed.** Five flights — all one older
  Mavic — recorded a 0.00 °C minimum inside flights whose real readings were
  35–53 °C. Zero readings far from the flight's own median are now treated as
  dropouts and dropped, with a `battery_temp_dropouts` flag; a genuinely cold
  flight (median near zero) is still kept. That removed all five false "freezing"
  reports without hiding a real one.
- 20 new tests cover the policy, the classifier, the battery and fleet summaries,
  and the dropout rule.

### Stage 10b · The compass start-up quirk

The owner reported the compass errors as the known DJI behaviour at motor start.
The archive supports it: **22 of 33 compass flags fire at t=0**, every one lasts
a single frame, and they are spread across four different aircraft — not one
faulty unit. Those are now classified `startup` and **do not count** as faults;
they are still recorded and shown, labelled "Compass flagged at start-up
(transient, single frame)", so nothing is hidden.

**11 compass flags are not explained that way** — they occur 32–425 s into the
flight. Those keep full `warning` severity. Meanwhile the genuinely useful faults
cluster mid-to-late flight (not-enough-force at 56 % through, battery voltage at
94 %, SD card at 70 %), which is what you would expect from real events.

After the change: **89 counted faults across 57 flights**, down from 113 across
76, with the 22 start-up transients recorded separately.

### Stage 11 · Maintenance schedules and their provenance — **applied to the real data**

The rule engine could count flights, airtime and charges, and nothing else. The
owner's AirData screenshots showed that AirData itself models a third trigger —
**"Time-Based Days"** — and a per-rule **"Recommended Service Items"** checklist.
Neither was implemented here, which is why the schedules could only ever be
counter-based.

- **Two sources, each labelled in the app.** AirData supplies the counters the
  archive was verified against. DoneDoctor supplies the calendar intervals. No
  manufacturer service interval is applied: none has been verified for the
  aircraft in this archive, and an interval written for a different class of
  aircraft would either never fire or fire constantly.
- **AirData's counters are untouched.** Verified on the live data: 43 rules
  before, `pre_existing_rules_changed=0` after. The four acceptance cases still
  read 188/100 due, 19/20 due soon, 16/20 due soon and battery 30/20 due.
- **DoneDoctor added as two rules per drone**, published ranges mapped onto the
  existing model — the upper bound becomes the limit, the lower bound the
  due-soon point:
  - *every 10–15 flights* → due at 15, due soon at 10 (67%)
  - *every 2–3 months* → due at 90 days, due soon at 60 days (67%)
  Live: **6 rules added across 3 drones**, all starting at 0 and correctly not due.
- **Backdated 2026-11-21 at the owner's instruction:** each of the six carries a
  completed service dated **2026-11-19 20:00 UTC** (13:00 on 11/19/26 local), so
  every one reports `basis: service` rather than `basis: rule_added`. Statuses were
  already `ok` and remain `ok` — what changed is what the card says it measures.
- **A new rule's clock starts when it was added.** Counting a drone's whole
  history against a check nobody has ever performed would report "overdue since
  records began" on day one. The baseline is recorded as `rule_added` and shown,
  so the card is honest about what it is measuring. Provenance decides this, not
  timestamps — the capture writes its rules and its snapshot in one transaction,
  so both carry "now".
- **Task checklists carried from the sources.** AirData's own wording from the
  owner's screenshots (aircraft Basic/Extended/Full, battery Basic/Full) plus
  DoneDoctor's two lists, shown in a collapsible list on each card. Every rule now
  names its schedule source.
- 20 new tests cover the calendar trigger, the range-to-threshold mapping,
  idempotent seeding, provenance, that AirData counters survive seeding, and a
  guard that no third schedule source is introduced.

### Stage 12 · Downloads and destination pickers — **fixed from a real report**

Two failures the owner hit while making the first real backup. Both were
reporting and mechanism faults, not data faults.

- **The redacted export did nothing.** The download fetched the file first and
  only then clicked a link to it. Fetching expires the click gesture, so the
  browser classifies every download after the first few as an *automatic
  download* and blocks it silently. Three CSVs saved, the fourth and fifth did
  nothing. Exports now go through a per-run capability token in the query string
  (`App.export_token`), so a plain `<a download>` starts inside the click and no
  gesture is lost. The token is accepted only on `/api/export/`; the header path
  still works and is still required everywhere else.
- **"Choose folder" and "Choose file" appeared dead.** The dialog is spawned by
  the server, so Windows is free to place it behind the browser — and the request
  then blocks until it is answered, which looks exactly like a broken button. The
  picker now gives the dialog a topmost owner so it comes to the front, the wait
  is bounded at 120 s instead of 300 s, and — the part that matters — **the click
  always produces a visible message**: while waiting, on cancel, and on failure,
  each saying to type the path instead. A silent no-op is the one outcome that is
  never acceptable.
- **A destination no longer needs a dialog at all.** Settings prefills the backup
  folder with `<app home>\backups`, so Create backup works without touching the
  picker.
- Verified in a browser: all five exports download; the redacted one reports
  "Saved FlightDesk-flights-redacted.csv"; serials and coordinates are absent from
  it while the row count is identical; the backup folder is prefilled; and the
  chooser shows its message immediately.
- 6 new tests pin the export capability: token accepted without a header, bad
  token refused, no token and no header refused, header still works, and the
  token rejected outside the export routes.

### Stage 13 · Schedule editing, restore durability, capture honesty

Three gaps closed without needing the owner.

- **A rule's thresholds can be edited from the screen, and backdated.** The Rule
  dialog previously showed counters, baseline and history but could not change
  anything, so the 20/200 counters and the DoneDoctor 15/90 limits were frozen.
  It now takes flights, air minutes, charges, days and the due-soon percentage,
  plus an enable switch, and offers *Record a service on a past date* so a
  calendar check reflects work that was already done instead of starting today.
- **Editing cannot wreck a rule.** Validation is in one place: a nonsense value
  is refused rather than stored, at least one trigger must remain, the due-soon
  percentage is bounded, and empty means "switch this trigger off". Editing never
  touches service history, and every change is audited.
- **A hazard was found while building it.** The rule status payload did not expose
  `flights_limit`/`air_minutes_limit`/`charges_limit`, so an editing form would
  have opened blank and **saving it would have erased every counter it could not
  see**. The thresholds are now part of the payload, and a test asserts it.
- **Restore is now reversible, not destructive.** `restore_backup` deleted the
  existing target root and then renamed the staged copy into place — a crash in
  between lost both. The old root is now moved aside, the switch is attempted, and
  the old root is put back if the switch fails. Proven by fault injection:
  `tools/fault_check.py` interrupts extraction and asserts the target and its
  marker file survive, that nothing half-restored is left where it would be
  trusted, and that a later restore still works. The regression test was checked
  against the old implementation and **fails there**, so it genuinely bites.
- **The capture's unaccounted records are now visible.** The real capture states
  21 historical service records and carries 13. The difference was computed but
  never shown, so the app silently looked complete. It now appears in the preview
  counts and in a plain-language notice: unaccounted, never invented.

### Stage 14 · The launcher lands on the home page — **fixed from a real click**

The owner double-clicked `FlightDesk.cmd` on 2026-11-20 and did not get the home
page. Both halves of that were real and independent.

- **The app opened on the first-run welcome panel, on a root holding 264
  flights.** The boot gate read `state.app.flights`; `/api/state` sends
  `counts.flights` and has never sent a top-level `flights` key. So the condition
  was permanently true on any root where `demo_seeded` was false — that is, on
  every real root. The 86-check browser gate could not see it, because gate roots
  are demo-seeded and `demo_seeded` short-circuits the same expression. A cold
  headless render of `/` reproduced it; after the fix the same render lists 264
  flights and the three tiles.
- **The address changed on every launch.** Each click started a fresh server on
  whatever free port came up, and opened the bare origin. The URL now carries
  `#/flights` explicitly, so a click lands on the flight list regardless of the
  tab's history or the JS default.
- **A second click started a second copy.** Now, when the app is already serving
  the same data root, the launcher opens that page and exits instead of starting
  another server on another port. Three conditions must all hold: the recorded pid
  is alive, its recorded root matches the root being launched, and the port
  answers with a Flight Desk page (a live socket alone is not enough — ports get
  reused). An explicit `--port` is never redirected.
- **A browser that will not open now says so**, with the address to paste. The
  Stage 12 lesson — a click that appears to do nothing is never acceptable —
  applies to the launcher too.
- 13 new tests (`tests/test_launcher.py`) cover the URL, each rejection path
  (no state file, unreadable state, dead pid, other root, nothing listening,
  an explicit port not being redirected), reuse of a real running server, the
  `--no-browser` path, and the visible failure. Two more pin the landing-page
  contract from both sides: the page reads `counts.flights`, and the API sends it.
  The browser gate gains a check that a root with records does not open on the
  welcome panel — 87 checks, 0 failures.

### Stage 15 · A details block that is not where it says it is

One real record — flight 216, a v11 log from 2025-05-16 — carried a mojibake
aircraft name and serials, a max height of **−4.9e31 m** and a total distance of
**1.96e-33 m**, and was marked `authoritative`. The pinned DJI parser reports the
same garbage for that file, so nothing downstream could tell.

- **Cause.** A v6–v12 header stores the offset of the details block in its first
  eight bytes. That file stores **0**. `read_details` accepted it, so the file
  header was read as if it were a 380-byte details block: every field came from
  the framing bytes. All 224 other v6–v12 files put the block at 86–99.98 % of
  the file.
- **Three guards, each with a test.** The offset must land after the header and
  leave room for the block. A block whose whole identity region decodes to
  unprintable bytes is rejected wholesale — identity is the alignment check, so
  misalignment invalidates the numbers in it too; a single unreadable field is
  dropped alone and the readable serials are kept. Distance values between 0 and
  1e-6 m are floating-point noise, not a measurement.
- **`drop_implausible` now tests magnitude and sign.** `value > limit` never
  caught a *negative* absurdity, which is exactly the shape a misaligned block
  produces. Fields that cannot be negative are now refused when negative;
  altitude above sea level is deliberately exempt, because −80 m in Death Valley
  is a reading.
- **A flight nobody can read is still a flight.** It is kept with its file-name
  date, marked `unresolved` with `excluded_reason='details_unreadable'`, and
  `metrics_source='unavailable'` — visible on Import → **Needs review** in plain
  English, and excluded from totals. Re-parsing repairs an existing record: the
  identity columns are nulled **by name**, because the ordinary backfill uses
  `COALESCE(?,column)` and a null can never overwrite the garbage.
- **Applied to the live root** after a rollback copy
  (`data-before-details-fix-20261121`): flight 216 re-parsed, audited,
  264 records still, air time and distance identical to the second
  (87,668.8 s · 174,227.79 m), 263 authoritative + 1 unresolved, rule statuses
  untouched (27 ok / 16 unknown / 5 due soon / 1 due), reconciliation still
  108.26 mi at 100 % decoded coverage.
- 18 new tests (`tests/test_stage15_bad_details.py`): offset 0, an offset inside
  the header, an offset past the end, a wholly misaligned identity region, one
  bad field among good ones, denormal distance, negative height / distance /
  speed, a negative altitude that must survive, an import with no invented
  facts, a reparse that replaces a garbage record, and a reparse that never
  degrades a good one.

### Stage 16 · Fixes from the independent review — **items 1–4 fixed with tests, 5–6 done, 7 built, 8 added**

An outside review read the code and used the app on a throwaway demo root. Its
findings were turned into changes here; each bug fix has a test that fails on the
code the review read. **No live data was changed.** The fingerprint repair runs as
a dry run and waits for the owner (see below).

- **1 · Two unreadable logs merged into one flight.** `_fingerprint` hashed
  `"||0.0"` when a log had no details, so every unreadable log shared one
  identity and the second was linked as a "variant" of the first. A fingerprint
  is now produced only when there is a start time (decoded, or from the file
  name), the two sources are tagged apart, and the metrics update rewrites the
  fingerprint instead of leaving it stale. `tools\repair_fingerprints.py`
  reports; `--apply` writes.
- **2 · The collector skipped by name alone.** An incomplete copy on the
  controller could never be replaced. Skipping is now name **and** size (one size
  read, only for names already archived). When the device reports no size, a copy
  must be stable for three seconds **and** long enough to hold the block its own
  header points at, or it is reported as `copy_unverified`. The check that hashed
  the staged file and compared it to the hash computed from that same file is
  gone — it could not fail. `validate_flight_bytes` also now rejects a copy that
  stops inside its declared details block, which the old check accepted.
- **3 · Silent failures.** `except Exception: pass` in the watcher now logs and
  records the category in a setting, which Settings → USB controllers shows as
  "Last watcher problem". Route failures log the route and traceback to
  `<root>\logs\flightdesk.log` (1 MB × 3, paths outside the data root and
  key-like tokens scrubbed). `usb.scan` writes a `usb_scans` row on every exit,
  with `collector_timeout` told apart from `collector_unavailable`; files already
  copied before a timeout are imported instead of deleted.
- **4 · "Path" that was not the path.** Cards, the Overview tile and the
  flights-page tile now show `path_length_m` labelled *path* when it exists, and
  `total_distance_m` labelled *log distance* otherwise. The tile says which one
  it sums, and only claims the path when every counted flight has one.
- **5–6 · Text and layout.** Key-in-backup wording, the key's real path in
  the README, the opt-in basemap, the tile cache, no mention of an assistant, the
  synthetic badge only when demo data is loaded, demo `warnings_json`, a compact
  navigation rail, one tab bar instead of two, "1 archived flight", provenance
  behind a Details toggle, and plain owner wording in Settings.
- **7 · Raw log export.** Settings → Raw log folder copies the archived
  originals out under their real names, additively and incrementally, with a
  manifest, optional variants and year grouping, a background run with progress,
  and an Open folder button. Ten tests.
- **8 · `tools\real_file_check.py`.** An opt-in, git-ignored check against a
  handful of the owner's real logs and figures recorded once by hand. Skips
  cleanly when the folder is absent.

**Measured:** 378 tests, 0 failures (was 331). `tools\recon_check.py` 9/9;
`tools\fault_check.py` exit 0 with the expected interrupted-restore outcome.
Headless Chrome renders of Settings, Flights, Maintenance and a flight detail
confirm the new text and layout; the removed wording is gone.

**The browser gate did not run for that change, and the reason was the gate.**
`tools\ui_check.mjs` failed against untouched `HEAD` too, with the same ~70
failures and a crash at `firstSummary.slice()`. Stage 17 found why and fixed it;
the gate now passes 87/87 with this code in place.

**Fingerprint repair, dry run on the live archive:** 284 flights checked, 284
fingerprints differ (the scheme gained a source tag, so every value changes),
0 flights share a fingerprint, and 94 multi-file flights have file names that
disagree about the date — **77 by a second and 17 by exactly two hours**. None
contradicts: the link was made on the decoded start times, which agree within
three seconds. Nothing was written.


### Stage 17 · The browser gate, batch noise, and a concurrent proof — **all three measured**

- **The gate was broken, not the app.** It attached to whatever page the browser
  listed; opened at `about:blank`, the derived origin was the string `"null"`, so
  `Page.navigate` went to `null/` and every check described an error page. It also
  returned a marker object from a failed `Runtime.evaluate`, so the first real
  failure crashed the run at `.slice()`. Fixed: a loopback page or an explicit
  app URL is required (exit 2 otherwise), a page-side exception is a failed check
  rather than a crash, and the one check that read an API from inside the page
  now sends the `X-FD-Token` header the server requires. **87 checks, 0
  failures**, on a full run that exercises the Stage 16 UI changes.
- **One import batch per scan.** A controller re-scan wrote one batch per copied
  file — 927 of the 1,082 live batches held no new flight. A scan now writes a
  single deferred batch, and a scan that found nothing new writes none; the scan
  itself is still recorded in `usb_scans`. A file refused before it reached the
  importer is recorded as an item, so a truncated copy is not silence.
- **A bounded collector run.** `-MaxFilesPerRun` lets a pass stop deliberately and
  report `truncated: true` (`status: partial`), so a large backlog makes progress
  in every pass. Unlimited by default: a measured pass over 259 logs takes 58 s.
- **Backup during a real scan.** Two threads: one imports 40 files, one takes six
  backups. Every backup is checked for a consistent archive and manifest, and the
  flight count must move between backups, so the test cannot pass by finishing
  the backups first.

**Measured:** 407 tests, 0 failures (was 393). `tools\recon_check.py` 11/11;
`tools\fault_check.py` exit 0; browser gate 87/87.


### Stage 18 · Air time means off the ground, and the repaired archive — **measured on the live data**

Three owner decisions from 2026-11-22, applied and verified.

- **Fingerprint repair applied.** All 284 rows rewritten from a rollback copy
  (`data-before-fingerprint-repair-20261122`). Verified after: flights 284,
  authoritative 282, log duration 94,183.4 s, distance 186,689.94 m,
  `flight_files` 419 and identical row for row, 0 foreign-key violations,
  0 shared fingerprints. A second run reports 0 differences: it is idempotent.
- **Air time is now time off the ground.** The app counted "motors on", which
  came to 24h19m — nearly the log duration, so the two figures were the same
  number wearing two names. Counting samples where `on_ground` is false gives
  24h10m, which is what AirData displays. Both are shown and named:

  | View | Air time | Log time |
  |---|---|---|
  | Our archive scope (264 files) | 24h10m | 24h21m |
  | AirData dashboard | 24h10m | 24h20m |

  The "unexplained 8.5 minutes" in the earlier checkpoint was two quantities
  being compared with each other. It is now 0.1 minutes on air time and about
  1 minute on log time, both from rounding to the displayed minute.
- **Every original re-read.** `tools\reparse_all.py --apply`: 419 files,
  0 failures, 95 s, after a rollback copy (`data-before-airtime-reparse-20261122`).
  Log duration and distance were unchanged to the byte; only air time moved
  (1,567.1 → 1,556.6 min across all 282 authoritative flights), and no flight
  reports more air time than log time.
- **Per-flight confirmation.** The Air 3S flight of 2026-08-11 measures 302.2 s
  off the ground against AirData's displayed 5m02s (302 s).
- **Real-file check stood up.** `%LOCALAPPDATA%\FlightDesk\realcheck\` holds one
  real log with the AirData figures recorded for it, a README and a template.
  `tools\real_file_check.py` compares like with like — air time with air time,
  log time with log time — after a first version compared air time with the
  record duration and reported a correct decode as a failure. It passes:
  302.2 s measured against 302 s.
- **Photo and video fields stay gone.** They were removed earlier; four tests now
  refuse to let them return to the schema, the API, the CSV or the owner docs.
- **Maintenance counters were deliberately not changed.** They still count log
  minutes: moving them would reshuffle 49 rule statuses for a difference of about
  0.5 %. Recorded here so the choice is visible rather than accidental.


## Release gate

| Item | Verdict | Evidence | Next human action |
|---|---|---|---|
| Fresh install + restart | **Verified** | Fresh empty root serves the app; `data_mode=empty`, 0 counts; launcher returns HTTP 200; restart keeps data | none |
| Archive inventory (269 files) | **Verified 2026-11-18** | `tools\diag.py`: 264 archive (7/218/39) + 5 controller (0/4/1) = **269 distinct, 7/222/40**; re-import added 0 | none |
| Decoded totals (264 · 24h10m · 108 mi) | **Explained** | 264 records; 108.3 mi vs 108 (match at displayed precision); 24h21m vs 24h10m is exactly the 2 newest flights the reference export lacked; frames-derived distance agrees independently | Enter the key (done) and read the signed difference with *real inputs* ticked |
| Due statuses at handover | **Verified on the real capture, since superseded by one owner action** | 188/100 + 1018/1000 Due; 19/20 and 16/20 Due soon; battery 3402QS was 30/20 Due at handover. On 2026-11-19 19:55 the owner recorded a **completed service** on that battery, which resets its baseline to zero (now 0/20, `ok`). That is the feature working, not a regression; Maintenance → Completed → Undo reverses it if it was a misclick | Undo the service if unintended, or leave it if the battery really was serviced |
| Both real USB devices | **Verified on both, 2026-11-21** | Smart Controller: 259 logs, 153 raw files, 18 new flights, second attach 0 in 6 s. RC 2: 17 logs, 1 new v14 record (since decoded), and after a **physical unplug/replug** a scan found all 17 already archived — 0 imported, 1.0 s. Seven collector and reporting defects found and fixed | none |
| No duplicate imports | **Verified** | Folder, ZIP, renamed duplicate, race, re-scan and reparse all add zero | none |
| Key-cached offline reparse | **Verified 2026-11-19** | Real key decoded all 39 v14 records; one re-decode from cache at 16:15 UTC with the network off, no new flight | none |
| Working backup recovery | **Verified on the real backup 2026-11-20** | 12 tests + 13-step rehearsal + `fault_check.py` (interrupted backup and interrupted restore). Then a real rehearsal: `FlightDesk-backup-20261119-125703.zip` restored with the current code into a fresh root — hashes, integrity and foreign keys clean, all 264 archive objects identical, `views.flight_detail` identical for **all 264 flights**, and the copy served over HTTP. Refusals held at the live root and on a populated folder | none for the backup itself; the cutover decision stays with Kevin |
| The DJI key travels with a backup | **Verified 2026-11-21** | `key_included: true` with its own hash; a restored copy decrypts a key identical to the live one; a tampered blob fails validation, a foreign-account blob warns instead of failing | none |
| Launcher lands on the home page | **Verified 2026-11-20** | A cold render of `/` on the real root opened on *"No records have been imported yet"*: the welcome panel was gated on a state field the server never sends. Fixed, and after the fix the same render shows the flight list with 264 rows. The URL now carries `#/flights` and a second click reuses the running instance | Click it and confirm the page looks right |
| Unreadable details block | **Fixed and applied 2026-11-21** | Flight 216's v11 header stores a details offset of 0, so the file header was read as a details block: mojibake identity, −4.9e31 m height. Guards added, the record repaired in place after a rollback copy, totals identical | none |
| Video count comparison | **Removed** | Media counts are gone from the app at the owner's request; the reference claimed 749 photos while only 180 files exist | none |

**Keep AirData active while any material difference is unexplained.** A final
fresh export/import rehearsal is offered before the February 2027 renewal;
the cancellation decision is Kevin's.

## Overall counts

- 407 automated tests, all passing (`python -m flightdesk selftest`).
- 87 scripted browser checks, all passing (including the no-egress and privacy
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
2. **The decoded-total gap is explained and closed.** Distance was the v13/v14
   unit error (fixed); the air-time difference is exactly the two newest flights
   the AirData export lacked; media counts were removed. All 39 v14 records are
   decoded. What remains is the owner ticking *Mark records as my real inputs* so
   the signed difference is shown against the reference.
3. **The real capture accounts for 13 of the 21 service records it claims.**
   The 8 missing rows are reported, not invented. 16 battery rules are
   `unknown` because those batteries published no counter.
4. **Physical USB — verified 2026-11-21.** This item said `usb_scans` was 0
   and that the collector had never enumerated a real controller. That stopped
   being true the same day: both controllers were read, `usb_scans` is 11, and a
   physical replug added nothing. See the release-gate row "Both real USB
   devices" and Stage 4. What remains unobserved is a controller left attached
   for a long period under the watcher — arithmetic, not an observation.
5. **Screenshots 01/03/19/20/21–23 comparison** needs Kevin's reference images.
   The in-app comparison page pairs them without uploading.
6. **Backup during an active USB scan** is covered by a settings-level test, not
   a genuinely concurrent scan. Restore interruption *is* injected mid-extraction
   (`tools\fault_check.py`, Stage 13): the target and its marker survive and a
   later restore still works. A restore on the real backup has now been rehearsed
   end to end with the current code.
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
