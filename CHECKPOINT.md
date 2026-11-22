# CHECKPOINT — resume here

Updated 2026-11-22 05:05 UTC, end of the 2026-11-21 session. Read this first,
then `BUILD-STATUS.md`. Everything below was measured on this machine at the time
of writing; nothing is aspirational.

## What the last session did (2026-11-21, three commits)

1. `84f332d` **Fixes from the independent review.** Items 1-8 of the outside
   review: the fingerprint identity bug (two unreadable logs merged into one
   flight), the controller skip-by-name bug, silent watcher and route failures,
   the "path" label that showed the log header's total distance, the
   contradicting on-screen text, the UI polish list, the raw-log export feature,
   and `tools\real_file_check.py`. Full detail in `BUILD-STATUS.md` Stage 16.
   **No live data was changed**; the fingerprint repair is still a dry run.
2. `7f0e164` **A working browser gate, one import batch per scan, and a
   concurrent backup proof.** Three things:
   - The gate never loaded the app. It attached to whatever page the browser
     listed, and with `about:blank` open the derived origin was the string
     `"null"`, so every navigation went to `null/` and the run printed ~70
     failures describing an error page. It now requires a loopback page or an
     explicit app URL, refuses to run otherwise, turns a page-side exception
     into a failed check instead of a crash, and sends the `X-FD-Token` header
     where it reads an API from inside the page. **87 checks, 0 failures.**
   - `import_batches` noise. A controller re-scan wrote one batch per copied
     file: 927 of 1,082 rows held no new flight. A scan now writes one batch,
     and a scan that found nothing new writes none. A file refused before it
     reached the importer is recorded, so a broken copy is not silence.
   - A real concurrent test: one thread imports while another takes six
     backups. Each backup is checked for a consistent archive and manifest, and
     the flight count must move between backups, so the test cannot pass by
     finishing them first.

3. The collector can now bound a single run (`-MaxFilesPerRun`) and say
   `truncated: true`, so a large backlog makes progress in every pass instead of
   losing a whole 200 s window. Default is unlimited: a measured pass over 259
   logs takes 58 s.

## What this session did (2026-11-20 23:00 → 2026-11-21 02:50 UTC)

Nine commits. The five that wrote to the live data root each have a rollback
copy; the other four changed code or documentation only. In order:

1. `f9b3849` **Launcher opens the flight list.** The first-run welcome panel was
   gated on a state field the server never sends, so every cold start on the real
   root opened on "no records have been imported yet". A second click also started
   a second server on a new port; it now reuses the running one.
2. `390cc19` **An unreadable details block is not a flight record.** One v11 log
   stores a details offset of 0, so the file header was read as a details block:
   mojibake identity, height −4.9e31 m, distance 1.96e-33 m, marked
   `authoritative`. Guards added; flight 216 repaired in place.
3. `d901bf8` **The controller path actually copies.** `CopyHere` was called on the
   source folder, so the Smart Controller import staged 0 bytes in 800 s. Fixed,
   plus a skip list so a plugged-in device is not re-copied on every watcher pass
   (59 s → 6 s).
4. `2d42251` Records that import and its open warts.
5. `e6136d1` **Nothing runs longer than 200 s without progress** (owner's rule):
   the parser and collector bounds moved 300 s → 200 s, pinned by tests.
6. `ce4b3b0` **The RC 2 reads, and the scan says what it did.** Seven reporting
   defects in one pass: `imported 0` for a created record, attention with no
   reason, unrecorded scans, devices reported as never seen, a volume counted
   twice.
7. `c411606` **Decode the RC 2 log, and audit the fetch that did it.** The v14 log
   decoded in 1.6 s; a successful keychain fetch was not audited until now.
8. `bb9f898` The physical RC 2 replug adds nothing, as it should.
9. `2d9af30` **Backups carry the DJI key, and DoneDoctor is backdated** — the
   three decisions the owner made at the end of the session.

The archive grew from **264 records / 24.35 h / 108.3 mi** to **284 records /
26.16 h / 116.0 mi** by reading both controllers: **20 new records and 155 new
raw files**, all accounted for.

- **18 decoded flights** from the Smart Controller (ids 265–282), four of them
  on 2026-11-15 which the AirData export never had.
- **1 unreadable record** from the Smart Controller (id 283): details offset 0,
  kept and flagged, excluded from totals.
- **1 decoded flight** from the RC 2 (id 284), decoded with the DJI key.
- The other 135 raw files from the controllers are byte-different variants of
  flights already held; both originals are kept and the flight is counted once.

## State at a glance

- Local Git: a linear history on one branch; **no remote and nothing pushed**.
  Code tip: **`7f0e164`** — "a working browser gate, one import batch per
  scan, and a concurrent backup proof". Every claim below was re-measured at
  05:20 UTC against that tip; the two commits after it are documentation, plus
  the Settings wording for a partial scan and one test that pins it.
- Working tree: **clean**.
- `python -m flightdesk selftest` → **393 tests, 0 failures, 0 errors**.
- `node tools\ui_check.mjs <cdp-port> <outdir> <zip> <app-url>` → **87 checks, 0 failures**.
  The app URL is the fifth argument and is required unless the browser is already
  on a loopback page; see the recipe under *How to resume*.
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
- **63 flights** carry at least one counted fault (44 battery-voltage, 32
  not-enough-force, 11 compass, 5 SD card, 2 gimbal, 1 motor-blocked), plus 21
  more with only a start-up compass transient, which is recorded but not counted.
- **5 batteries** have exceeded the 40 °C limit (peaks 44.0–53.6 °C); **48**
  flights carry a temperature reading at all, the rest report 0 for every sample.
- **`usb_scans = 11`** — both controllers have been read; **both are now proven on
  real hardware** (Smart Controller 259 logs, RC 2 17 logs, second attach adds 0).
- Last backup: **2026-11-21 02:43 UTC, status `ok`** —
  `C:\Users\raven\Desktop\Backups\FlightDesk-backup-20261120-194306.zip`,
  303,410,112 bytes (289.4 MiB), sha256 `79eecc71…f9b8`, 419 originals,
  **key included**, validates with no errors and no warnings. Taken after the
  key-in-backup change and after the DoneDoctor backdating.
  The previous one (`…-20261119-125703.zip`, 264 records, no key) is kept: it is
  a valid snapshot of the archive before the controllers were read.
- Rollback copies of the data root taken before each change live in
  `%LOCALAPPDATA%\FlightDesk\data-before-*` (**16** of them, 335 MB each at the
  latest size — about 5 GB on disk). Not in Git. The 2026-11-21 set covers the
  details fix, the Smart Controller import, the RC 2 import, the DJI decode and
  the backdating.

## One thing that looks like a regression and is not

Battery `Bat-Mavic2-3402QS` Basic read **30/20 Due** at handover — a verified
acceptance case. It now reads **0/20, `ok`**. Cause: the owner recorded a
**completed service** on that battery at 19:55:41 UTC on 2026-11-19 (audit entry
`service_completed`, rule 22, no notes). A completed service resets that rule's
baseline by design — this is the feature working.

**The owner confirmed on 2026-11-21 that this service was real**, so nothing is
to be undone. Do not "fix" it by deleting the row: it is the owner's record and
is audited. (Undo remains available under Maintenance → Completed if a future
entry is ever recorded by mistake.)

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

**One finding, since resolved.** That rehearsal showed the **DJI key was not in
the backup**: `keychains/dji_key.bin` lives in the data root but the archive
carried only the database and originals, so a restored copy reported
`key_configured: false`. **The owner decided on 2026-11-21 that backups must
carry the key**, and they now do (see below). Separately, the restored copy
has `real_inputs_selected: false`, so reconciliation still needs the owner to
tick *Mark records as my real inputs*.

## Backups carry the DJI key (decided 2026-11-21)

The key is `keychains/dji_key.bin`, a DPAPI blob beside the database. Backups
now include it as `keychains/dji_key.bin` with its own hash in the manifest,
`key_included: true`, and a plain-language note about what that means.

- **Restore puts it back.** It extracts with everything else, and a rehearsal
  of the new backup restored a key that decrypts and is byte-identical to the
  live one.
- **Validation checks the hash** and reports `key_checksum_mismatch` as an
  error, `key_missing_from_backup` if the manifest promises one that is absent,
  and `key_not_decryptable_on_this_account` as a **warning** when the blob
  belongs to another Windows account — the data is intact and the key can be
  re-entered, so that must not block a restore.
- **The exposure, stated plainly:** a ZIP that carries the key is only as safe
  as the account that can read it. The blob decrypts only for this Windows user
  on this machine, so a copy taken off the machine is inert elsewhere — but
  anyone who can read the ZIP *as this user* can use the key. The owner accepted
  that on 2026-11-21 and keeps backups on the same disk.
- **Fresh backup taken after the change:**
  `C:\Users\raven\Desktop\Backups\FlightDesk-backup-20261120-194306.zip`,
  303.4 MB, 419 originals, validates clean with no warnings, key included.

## DoneDoctor schedules backdated (owner instruction, 2026-11-21)

The six DoneDoctor rules (three drones x frequent-flights and quarterly-days)
each received a **completed service dated 2026-11-19 20:00 UTC**, which is
**13:00 on 11/19/26 at UTC-07:00** — the owner's local clock, and the reading
that makes the app display the date they asked for. Recorded through the app's
own route, so each one is audited as `service_completed`.

- Every one now reports `basis: service` at that timestamp instead of
  `basis: rule_added`. The clock runs from work actually done, not from the day
  the rule was created.
- Statuses did not change: `27 ok / 16 unknown / 5 due soon / 1 due`, because
  they were already `ok` and are still `ok`. What changed is what the card says
  it is measuring. The 16 `unknown` are the battery rules with no published
  counter, unchanged.
- The 43 AirData rules were not touched (`pre_existing_rules_changed=0`).
- **If 13:00 was meant as UTC**, say so: shifting to `2026-11-19T13:00:00Z`
  moves each due date 7 hours and the display reads 11/19 06:00 local.

## Battery 3402QS service — confirmed intended

The owner confirmed on 2026-11-21 that the completed service on
`Bat-Mavic2-3402QS` at 19:55:41 UTC on 2026-11-19 was real, so its 30/20 `Due`
reading becoming 0/20 `ok` is the feature working. Nothing to undo.

## What is NOT verified — needs the owner

1. ~~Unplug and replug the RC 2~~ — **done 2026-11-21.** The device was
   physically re-attached and the next scan read 17 logs, added nothing, and
   took 1.0 s. Both controllers are now verified on real hardware including the
   physical reconnect.
2. **Watch a restore yourself once** (optional now). The rehearsal above used the
   same code the button calls, on the real backup. The browser route — Settings →
   **Restore into a new root…** → **Keep current data** — has not been clicked
   through on real data. Switching roots is the cutover, which is yours alone.
3. ~~Backdate the real services~~ — **done 2026-11-21** for the six DoneDoctor
   rules (dated 11/19/26 13:00 local). The **AirData** counters need no backdating;
   they come from the capture. If a specific AirData rule was actually serviced on
   a date you know, record that separately — nothing has been invented here.
4. **Read the comparison.** Settings → *Mark records as my real inputs* →
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

- ~~**Backup while a scan is running**~~ — **closed 2026-11-21.** Two real
  threads now: one imports, one takes six backups. Each backup is validated, and
  the flight count must move between them, so the test cannot pass by finishing
  the backups first (`tests/test_stage21_concurrent_backup.py`).
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
- ~~**`import_batches` grows by one row per file re-imported**~~ — **closed
  2026-11-21.** A scan writes one batch, and a scan that found nothing new writes
  none at all (927 of the 1,082 live rows were exactly that kind of noise, which
  stays in the existing history but is not created any more). A file refused
  before it reached the importer is recorded as an item, so a broken copy is not
  silence. `tests/test_stage20_batching.py`.
- **The watcher has not been observed with a controller attached over a long
  period.** One scan costs 6 s with the skip list; the interval default is 45 s.
  That is arithmetic, not an observation.

## Things that bit us (so they do not bite again)
- **The browser gate attached to the wrong page and blamed the app.** It opened
  the browser at `about:blank`, derived the origin `"null"`, navigated to
  `null/`, and printed ~70 failures that all described an error page. It also
  returned a marker object from a failed evaluation, so the first real failure
  crashed the run at `.slice()`. Both were gate defects wearing the costume of
  app defects, and they hid the app's own results entirely. **A harness that
  cannot fail honestly is worse than no harness.**

- **A successful keychain fetch was never audited.** Only
  `keychain_fetch_failed` was recorded, so the one event that actually spends
  the owner's credential — an outbound POST carrying the API key — left no
  trace when it worked, while this file claimed the audit log recorded it.
  `keychain_fetched` now records the endpoint and the keychain count, never the
  key or the material.

- **Every controller defect was silence, not an error.** `CopyHere` on the *source*
  folder returned in 20 ms having done nothing (it copies into the folder it is
  called on); a `FolderItem` passed where a `Folder` was expected threw inside a
  `catch` that recorded nothing, so the scan said `no_files` while 250 logs sat on
  the device; MTP's `.Size = 0` was taken as a real size. Result: 0 bytes staged
  after 800 s. Then, on the RC 2, a created flight was reported as `imported 0`,
  and a scan that found everything already archived wrote no history row at all.
  **A summary that says less than what happened is the same class of bug as one
  that says nothing**, and a rejected path must record why.
- **My own fix shipped a null.** The collector added `$deviceName` to the seen
  list on the line *before* it was assigned, so it reported no devices. Running
  the collector directly and reading its JSON caught it; the app's summary alone
  would have hidden it.

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
python -m flightdesk selftest          # expect: 393 tests, 0 failures
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
# The browser must already be ON the app page. Opening it at about:blank makes
# the gate derive the origin "null" and every check fail against an error page.
start msedge --headless=new --remote-debugging-port=<d> `
  --disable-extensions --disable-sync --no-first-run `
  --user-data-dir=$env:TEMP\edge-fd "http://127.0.0.1:<p>/"
node tools\ui_check.mjs <d> <outdir> out.zip "http://127.0.0.1:<p>/"
```

Pass the app URL as the fifth argument and the gate tolerates a browser that is
not on the page yet; it exits 2 with a clear message when it cannot resolve a
loopback origin, which is the bug that hid ~70 checks behind one crash.

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
