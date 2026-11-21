# CHECKPOINT — resume here

Updated 2026-11-21 01:20 UTC (owner session). Read this first, then
`BUILD-STATUS.md`. Everything below was measured on this machine at the time of
writing; nothing is aspirational.

## State at a glance

- Local Git: a linear history on one branch; **no remote and nothing pushed**.
  Code tip: **`c58b2b7`** — "editable schedules, reversible restore, visible
  capture gap"; the launcher and landing-page work of 2026-11-20 is committed on
  top of the checkpoint commits, so name the tip with `git log -1`.
- Working tree: **clean**.
- `python -m flightdesk selftest` → **326 tests, 0 failures, 0 errors**.
- `node tools\ui_check.mjs <cdp-port> <outdir> <zip>` → **87 checks, 0 failures**.
- `python tools\rehearse.py` → **13/13 steps**.
- `python tools\recon_check.py` → **9/9**.
- `python tools\fault_check.py` → interrupted backup returns `disk_full` with no
  partial archive; interrupted **restore** leaves the target and its marker intact.
- **Restore on the real backup: rehearsed 2026-11-20.** See below.

## Live data (measured, not remembered)

- **284 records · 282 authoritative + 2 unreadable** · 26.16 h · 116.0 mi ·
  **0 awaiting the key** — the RC 2's v14 log from 2026-08-21 was decoded on
  2026-11-21 with the stored key.
- 419 raw originals archived (264 from the AirData export, 155 from the two
  controllers); **40 keychains cached**.
- **Two unreadable flights, both the same defect**: a v11 log whose header stores
  a details offset of 0, so its block was never read. Flight 216 came from the
  export; flight 283 came from the controller on 2026-11-21 and is the same kind
  of file. Both are kept, marked `details_unreadable`, and excluded from totals.
- **49 maintenance rules**: 43 AirData + 6 DoneDoctor. Statuses: 27 ok,
  16 unknown, 5 due soon, **1 due**.
- **57 flights** carry at least one counted fault.
- **5 batteries** have exceeded the 40 °C limit (peaks 44.0–53.6 °C).
- **`usb_scans = 11`** — both controllers have been read; **both are now proven on
  real hardware** (Smart Controller 259 logs, RC 2 17 logs, second attach adds 0).
- Last backup: **2026-11-19 19:57 UTC, status `ok`**
  (`FlightDesk-backup-20261119-125703.zip` on the Desktop, sidecar hash matches).
  It predates the Stage 12/13 commits, but `backup.py` changed in them only in
  `restore_backup`; the writing and validating paths are unchanged, and the file
  validates clean against the current build. Inspected 2026-11-20.
- Rollback copies of the data root taken before each change live in
  `%LOCALAPPDATA%\FlightDesk\data-before-*` (11 of them). Not in Git.

## One thing that looks like a regression and is not

Battery `Bat-Mavic2-3402QS` Basic read **30/20 Due** at handover — a verified
acceptance case. It now reads **0/20, `ok`**. Cause: the owner recorded a
**completed service** on that battery at 19:55:41 UTC on 2026-11-19 (audit entry
`service_completed`, rule 22, no notes). A completed service resets that rule's
baseline by design — this is the feature working.

If that click was unintended: **Maintenance → Completed → Undo** puts it back.
Do not "fix" it by deleting the row: it is the owner's record and is audited.

## Restore rehearsal on the real backup — passed 2026-11-20

Run with the current build against `FlightDesk-backup-20261119-125703.zip`,
restoring into a fresh empty folder. Nothing was activated and the live root was
opened read-only throughout.

- `restore_backup` → ok, **0 warnings**, 4.1 s; counts 264/264/26/49/14/80.
- `integrity_check` ok, **0 foreign-key violations**, all 15 table counts and the
  flight totals identical to the live root (264 · 87,668.8 s · 174,227.8 m).
- **All 264 archive objects hash-identical** to the live originals.
- The app served the restored root: `/api/health` ok, 264 flights listed,
  maintenance at 1 due / 5 due soon / 27 ok / 16 unknown, the most-sampled
  flight drawing 1,262 track points.
- `views.flight_detail` compared for **all 264 flights, live vs restored: 0
  differences** (279,994 telemetry samples, 263 flights with frames).
- Refusals hold: restore at the live root → `refuses_live_root`; into a populated
  folder without confirmation → `target_not_empty`.
- The Stage 13 move-aside path was exercised for real: restore over a populated
  folder with `allow_existing` → ok, old content gone, **no `.previous-*` or
  `.restore-tmp` left anywhere**.

**Two findings.** The **DJI key is not in the backup**: `keychains/dji_key.bin`
lives in the data root but the archive carries only the database and originals,
so a restored copy reports `key_configured: false`. The 39 cached keychains *are*
in the database, so cache decoding works; the key would be re-entered only to
fetch new keychains. Whether a backup should carry a DPAPI blob (it would only
decrypt for this user on this machine) is an open decision, not a bug. Second:
the restored copy has `real_inputs_selected: false`, so reconciliation still
needs the owner to tick *Mark records as my real inputs*.

## What is NOT verified — needs the owner

1. ~~Unplug and replug the RC 2~~ — **done 2026-11-21.** The device was
   physically re-attached and the next scan read 17 logs, added nothing, and
   took 1.0 s. Both controllers are now verified on real hardware including the
   physical reconnect.
2. **Watch a restore yourself once** (optional now). The rehearsal above used the
   same code the button calls, on the real backup. The browser route — Settings →
   **Restore into a new root…** → **Keep current data** — has not been clicked
   through on real data. Switching roots is the cutover, which is yours alone.
3. **Backdate the real services.** Now possible: Maintenance → a rule → **Rule** →
   *Record a service on a past date*. The six DoneDoctor checks started counting
   on 2026-11-19, so without backdating they stay quiet for 60–90 days. Enter the
   real dates for props, deep-clean, IMU/compass calibration and battery cycles.
5. **Read the comparison.** Settings → *Mark records as my real inputs* →
   Reconciliation → Generate report. 108.3 mi against 108; 24h21m against 24h10m,
   explained by the two newest flights the AirData export lacked.

Commercially: **keep AirData active** until the owner is satisfied. Renewal is
February 2027.

## The Air 3S reference figures — checked 2026-11-21, they hold

The record is flight 260, 2026-08-12 05:53:56 UTC ("Antero Media Air 3S") — one
of the two flights the AirData export lacked.

- **154 ft** distance → 46.98 m = 154.1 ft. Match.
- **148 ft** max height → frames 45.10 m = 148.0 ft. Match (the log's own rounded
  field stores 45.0 m).
- **14.54 mph** max speed → 6.50 m/s = 14.54 mph. Exact.
- **94 % → 81 %** battery. Exact.
- **82.9 °F** max battery temperature → 28.3 °C = 82.9 °F. Exact.
- **49 ft** max distance → 14.97 m = 49.1 ft when measured **from the first GPS
  fix**, which is what AirData does. The app measures from the aircraft's
  recorded home point and reports 51.3 ft. Explained convention, 0.66 m.
- **5m02s** duration → 302.1 s airborne (`on_ground` false from 2.1 s); 304.2 s
  for the whole record, which is what the app stores and totals.

**Open question for the owner (not a defect, a choice):** the app's air time is
the record duration; AirData shows airborne time. Measured across all 263
decodable flights the two differ by **8.5 minutes** (24h21m vs 24h11m). Nothing
changes until someone decides which figure the dashboard should show.

## Known gaps that remain (reasoned, not measured)

- **Backup while the USB watcher is running** is covered by a settings-level test,
  not a genuinely concurrent scan.
- **Visual parity** for Flights / Selected Flight / Batteries has not been
  compared against the brief's 23 screenshots. The nine supplied so far are
  AirData *maintenance* pages only; they settled the rule model.
- **A native Windows dialog spawned by the server cannot be proven to surface**
  from here. It now gets a topmost owner and a bounded wait, and every click
  produces a visible message, but whether it appears on the owner's desktop is
  the owner's observation. Typing the path always works.
- **"Missions"** is not modelled; the third tile counts flights. If a mission means
  something else, say so.
- **Two flights have never been checked against anything.** Flight 216 is now
  explicitly unreadable rather than falsely complete, but no attempt has been
  made to recover it from a second source (the DJI Fly app cache, or AirData's
  own copy of that flight). If the owner can re-download that one log, it may
  parse cleanly. Flight 283 is the same defect and exists **only** on the
  controller; there is no second copy to try.
- **`import_batches` grows by one row per file re-imported.** A controller scan
  that copies 24 already-known files writes 24 batches, so the table reached
  1,057 rows after five scans. The Import screen shows the last 40, so it reads
  fine, but the history is noisier than it should be. A batch per scan, or no
  batch for an all-duplicate import, would be the fix.
- **The watcher has not been observed with a controller attached over a long
  period.** One scan costs 6 s with the skip list; the interval default is 45 s.
  That is arithmetic, not an observation.

## Things that bit us (so they do not bite again)
- **A successful keychain fetch was never audited.** Only
  `keychain_fetch_failed` was recorded, so the one event that actually spends
  the owner's credential — an outbound POST carrying the API key — left no
  trace when it worked, while this file claimed the audit log recorded it.
  `keychain_fetched` now records the endpoint and the keychain count, never the
  key or the material.

- **Every controller defect was silence, not an error.** `CopyHere` on the source
  folder returned in 20 ms having done nothing; a `FolderItem` passed where a
  `Folder` was expected threw inside a `catch` that recorded nothing, so the scan
  said `no_files` with 250 logs attached; MTP's `.Size = 0` was taken as a real
  size. Then, on the RC 2, a created flight was reported as `imported 0`, and a
  scan that found everything already archived wrote no history row at all. A
  summary that says less than what happened is the same class of bug as one that
  says nothing.
- **My own fix shipped a null.** The collector added `$deviceName` to the seen
  list on the line *before* it was assigned, so it reported no devices. Running
  the collector directly and reading its JSON caught it; the app's summary alone
  would have hidden it.

- **The controller collector never copied a byte, and said nothing.** Three
  faults stacked: `CopyHere` was invoked on the *source* folder (it copies into
  the folder it is called on, so it returned in 20 ms having done nothing); a
  `FolderItem` was passed where a `Folder` was expected, and the throw was caught
  with no error recorded; and MTP reports `.Size = 0`, which was taken as a real
  size. Result: `no_files` on a device holding 250 logs, and 0 bytes staged after
  800 s. Every one of those was silent. A rejected path must record why.
- **`@($pipeline | ConvertFrom-Json)` nests the whole array in PowerShell 5.1.**
  The JSON array arrives as one object, so `@()` wraps it into a single-element
  array and a `foreach` over it runs once with `.name` being every name joined by
  spaces. The skip list loaded 418 names and matched none. Assign first, then
  iterate.

- **Restore used to be destructive**: it deleted the target root and then renamed
  the staged copy in. A crash between lost both. Fixed by moving the old root
  aside and putting it back on failure. The regression test **fails against the
  old code**, so it bites.
- **The launcher booted on a field that never existed.** The first-run welcome
  panel was gated on `state.app.flights`; `/api/state` sends `counts.flights` and
  has never sent either of the others. Every cold start on the real root — 264
  flights — opened on *"No records have been imported yet"*. The 86-check
  browser gate could not catch it because every gate root is demo-seeded, and
  `demo_seeded` short-circuits the same condition. Both halves are now pinned:
  a static test that the page reads `counts.flights`, and a server test that the
  payload carries it.
- **One v11 log stores a details offset of 0.** `read_details` accepted it and
  read the file *header* as a details block, so a flight carried a mojibake name
  and a height of −4.9e31 m and was still `authoritative`. Three guards now:
  the offset must land after the header, a wholly unprintable identity region
  rejects the block, and `drop_implausible` tests magnitude **and sign**. The
  pinned DJI parser reports the same garbage for that file, so only a guard
  could have caught it.
- **`COALESCE(?,column)` protects a reparse and blocks a repair.** The normal
  backfill never overwrites a stored identity with null — right for backfill,
  fatal for a value now known to be garbage. The repair path nulls those columns
  by name instead.
- **The rule status payload did not expose the limit fields**, so an editing form
  would have opened blank and saving it would have erased every counter it could
  not see. The thresholds are now in the payload, with a test.
- **Downloads fetched the file before clicking a link**, expiring the click
  gesture and making the browser block every download after the first few. Exports
  now carry a per-run token so the download starts inside the click.
- **A capture can claim more service records than it carries** (21 claimed, 13
  present). The gap is now shown in the preview instead of being absorbed.
- **Zero temperatures need care in two different ways**: all-zero means the field
  is absent, and a lone 0.0 among warm readings is a dropout from one older
  Mavic. Both are handled; a genuinely cold flight is still kept.
- `git.exe` works for read-only inspection. Commit with
  `python tools\local_commit.py "msg"` — it normalises line endings on many files,
  so prefer `git diff HEAD~1 HEAD --ignore-all-space --numstat` to see the real
  change set.

## How to resume

```powershell
cd "C:\Users\raven\Desktop\Deepseek\Air-Data-Replace-Build"
python -m flightdesk selftest          # expect: 326 tests, 0 failures
python tools\fault_check.py            # expect: disk_full; restore survives
python tools\recon_check.py            # expect: 9/9
flightdesk.cmd                          # or: python -m flightdesk serve
```

Browser gate recipe that works (fresh root each run — a reused root makes the
ZIP checks fail because the ZIP is already imported):

```powershell
python tools\make_synthetic_handoff.py out.zip
python -m flightdesk seed-demo --root <freshdir>
python -m flightdesk serve --root <freshdir> --port <p> --no-browser
# Edge --headless=new --remote-debugging-port=<d> --disable-extensions --disable-sync
node tools\ui_check.mjs <d> <outdir> out.zip
```

Note: a probe that hangs on one *specific* flight is a probe problem, not an app
problem — the same payload serves in ~0.02 s. Verify through the gate and the API.

A headless render of the landing page is often enough for a UI question, and it
needs no CDP:

```powershell
python -m flightdesk serve --root <root> --port 8765 --no-browser
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --headless=new --disable-gpu `
  --user-data-dir="$env:TEMP\fd-chrome" --virtual-time-budget=15000 --dump-dom `
  "http://127.0.0.1:8765/#/flights" > $env:TEMP\dom.html
```

Both the server and the browser must be started and read **inside one shell
invocation**: the tool kills the process tree when a command returns.

## The launcher

`FlightDesk.cmd` → `python -m flightdesk serve` → browser at
`http://127.0.0.1:<port>/#/flights`.

- The **hash is explicit**, so a double-click always lands on the flight list
  even if the tab's history or the JS default says otherwise.
- A click while the app is **already serving the same data root** opens that page
  instead of starting a second server on a fresh random port. An explicit
  `--port` is never redirected. Detection requires all three: the pid in
  `server.json` is alive, its recorded root matches, and the port answers with a
  Flight Desk page.
- A browser that will not open prints the address instead of failing silently.
- **Caveat:** `server.json` is rewritten by any `serve`, so a scratch run can
  leave it pointing at a dead root. That is harmless — the pid check rejects it
  and the next real click starts a fresh server — but it means the *first* click
  after a scratch session starts a new instance.

## When this session resumes

The owner's 2026-11-19 handover window has been used; this section is kept for
the cost boundary it explains. Peak is **01:00–04:00 and 06:00–10:00 UTC,
Mon–Fri**; the next peak after 2026-11-20 23:59 UTC is **Monday 2026-11-23
01:00 UTC**. Weekends are off-peak all day.

## Environment facts

- Parser: `dji-log` **0.5.7**, pinned Windows binary at
  `%LOCALAPPDATA%\FlightDesk\vendor\dji-log\0.5.7\dji-log.exe` with a manifest.
- Keychain decoder: `flightdesk-decode`, built from `shim/` against
  `dji-log-parser =0.5.7`.
- Screenshots live in `%LOCALAPPDATA%\FlightDesk\screenshots\`. The owner's
  AirData screenshots sit in `Screenshots/`, which is **git-ignored** and should
  eventually move outside the project tree.
- Working hours: peak DeepSeek pricing is **01:00–04:00 and 06:00–10:00 UTC,
  Monday–Friday**; weekends open. `.codewhale/instructions.md` holds the rule.
