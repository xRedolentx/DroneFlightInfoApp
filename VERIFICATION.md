# Verification record

Everything below was executed on this machine (Windows 11, Python 3.12.10,
Edge, no network needed except the two pinned downloads already installed).
All data is synthetic. Output shown is the aggregate pass/fail form; no
filenames, serials, coordinates or raw stderr were ever printed.

## 1. Test suite

```
> python -m flightdesk selftest
{"ok": true, "tests": 149, "failures": 0, "errors": 0, "skipped": 0}
```

Counts per module as of 2026-11-18 (test methods):
`test_stage1_import` 24, `test_stage2_equipment` 34, `test_stage3_web` 31,
`test_stage4_usb` 13, `test_stage5_key` 14, `test_stage6_analysis` 16,
`test_stage7_backup` 14, `test_diag_privacy` 3.

Groups:

- `tests/test_stage1_import.py` — 23 checks (incl. disk-full paths)
- `tests/test_stage2_equipment.py` — 22 checks
- `tests/test_stage3_web.py` — 26 checks (HTTP security, API, packaged assets,
  a `node --check` parse gate over every browser script, the picker backend, and
  the controller-scan endpoint returning a clean "no device" on a bare machine)
- `tests/test_stage4_usb.py` — 13 checks (simulated adapter)
- `tests/test_stage5_key.py` — 14 checks (exchange shape, DPAPI, offline cache)
- `tests/test_stage6_analysis.py` — 10 checks
- `tests/test_stage7_backup.py` — 14 checks (incl. interrupted backup)
- `tests/test_diag_privacy.py` — 3 checks (fail-closed output redaction)

## 2. End-to-end rehearsal

```
> python tools\rehearse.py
PASS fresh import accepts every file :: archive=4 extras=1
PASS re-import adds nothing :: new=0 dup=4
PASS capture migration is distinguishable :: services=21 grouped=4
PASS repeat capture migration is a no-op
PASS backup created and hashed
PASS backup validates
PASS restore into a new root
PASS restored database passes integrity and references :: fk=0
PASS restored totals equal the live totals
PASS restored maintenance statuses match :: rules=35
PASS exports produced with and without identifiers
PASS reconciliation report is readable
PASS second backup is independent
{"distinct_files": 5, "logical_flights": 5, "rules": 35, "services": 21, "backups": 2}
```

## 2a. Fault injection

```
> python tools\fault_check.py
backup_returned=False
backup_code=disk_full
destination_entries=[]
lock_present=False
staging_dirs=0
followup_ok=True
followup_valid=True
```

With `zipfile.ZipFile.write` forced to raise `ENOSPC`, the backup reports
`disk_full`, leaves the destination **empty** (no truncated ZIP), releases its
lock, and the next backup validates.

## 2b. Reconciliation arithmetic

```
> python tools\recon_check.py
PASS archive files :: 3
PASS archive air minutes :: 30.0
PASS archive miles :: 3.0
PASS video count stays unknown :: None
PASS controller extra counted separately :: 1
PASS combined :: 4
PASS no signed difference on synthetic data :: None
SUMMARY PASS checks=11 failures=0
```

Three archive flights of exactly 10 minutes and 1 mile each, plus one
controller extra: the grouped views are arithmetically honest and a missing
video count is not invented.

## 3. Browser workflow

```
> node tools\ui_check.mjs <debug-port> <outdir>
...
PASS arrow keys move between flights :: 6 -> 12
PASS privacy mode actually hides a serial :: hidden
PASS ZIP upload shows a batch summary
PASS every eligible member is accounted for :: discovered=7 eligible=7 total=9
PASS import summary survives the page refresh
PASS a repeated ZIP import adds no files :: 0
PASS a bad ZIP says why nothing was imported
PASS capture preview reports counts
PASS ZIP upload shows a batch summary
PASS every eligible member is accounted for :: discovered=7 eligible=7 total=9
PASS non-log ZIP members are excluded from the batch :: 7 of 9
PASS import summary survives the page refresh
PASS a repeated ZIP import adds no files :: 0
PASS a bad ZIP says why nothing was imported
PASS capture preview reports counts
PASS no request left loopback
SUMMARY_FINAL PASS checks=65 failures=0
```

Coverage: dashboard geometry, filters applying to list and tiles together,
flight detail, local track grid, gap shading, scrubber ↔ chart sync, all tabs,
keyboard navigation, battery → rule dialog, maintenance grouping, Complete →
Undo, unit conversion, privacy mode, 390 px mobile layout, empty state, and a
Network-domain assertion that every request stayed on `127.0.0.1`.

Screenshots were written under `%LOCALAPPDATA%\FlightDesk\screenshots\`
(synthetic data only, outside the repository).

## 4. Launcher

```
> FlightDesk.cmd --no-browser --port 8791
HTTP 200 from http://127.0.0.1:8791/ (title "Flight Desk")
GET /api/state without the session cookie -> 403 session_required
GET /assets/app.css -> 200
```

The launcher creates the data root under `%LOCALAPPDATA%\FlightDesk\data` on
first run and opens the browser.

## 5. Parser binding

```
> dji-log.exe <synthetic v8>   -> exit 0, real details JSON
> dji-log.exe <synthetic v11>  -> exit 0, real details JSON
> dji-log.exe <synthetic v13>  -> exit 101, stderr "API Key is required..."
   (caught and classified locally as pending_key; never printed)
```

`flightdesk-decode` (local Cargo build against `dji-log-parser =0.5.7`) decodes
a v13 container using a **synthetic** keychain, producing `version=13` and a
valid JSON frame list — proving the keychain path end to end without a real DJI
key.

## 6. Privacy assertions

- `tests/test_diag_privacy.py` builds a folder whose filenames contain a marker
  string and asserts the diagnostic runner's text **and** JSON output contain
  neither the marker, the file extension, nor the parent path.
- `tests/test_stage5_key.py::test_key_is_dpapi_protected_and_not_in_the_database`
  searches the database bytes for the key string; it is absent.
- `tests/test_stage3_web.py::StaticAssetTest` fails if any browser file
  references an absolute external URL.
- The UI check fails if any browser request leaves loopback.

## 7. What this record does not prove

- ~~Any real DJI log.~~ Superseded by section 8: the real archive is imported
  and the real capture is applied. Physical USB controllers, a live DJI keychain
  decode and visual parity against the 23 screenshots are still unproven.
- Physical USB controllers (simulated adapter only).
- A live DJI keychain decode (mechanism verified with a synthetic keychain).
- Visual parity against the 23 original screenshots (not present here).

## 8. Real inputs, aggregate only (2026-11-18)

Real inputs were read by local code only. Everything printed below is a count by
source/version/status, a total, or a figure the brief itself publishes. No
filename, path, serial, coordinate or record row was printed.

### 8.1 Archive inventory — `python tools\diag.py "<handoff.zip>"`

```
distinct_files=264  logical_flights=264  unresolved=0  errors={}
source=archive discovered=264 new=225 awaiting_dji=39
  versions=v8:7,v11:218,v14:39
  statuses=imported:225,pending_key:39
```

With the supplied controller folder in the same run:

```
distinct_files=269  logical_flights=269  unresolved=0
source=archive  versions=v8:7,v11:218,v14:39
source=extras   versions=v11:4,v14:1
```

269 distinct hashes at **7 / 222 / 40** — the brief's table exactly. A second
pass over the extracted folder reported `new=0 already_imported=264
flights_added=0`.

### 8.2 Real equipment capture

Parsed and applied on scratch roots, then on the live root behind a rollback
copy:

```
current_aircraft=3  historical_aircraft=3  batteries=20 (3 placeholders)
rules=43  snapshot_counters=72  services=13 (12 resolved, 1 grouped)
reported_total=21  reporting_gap=8  warnings=4
statuses: due=2 due_soon=5 ok=20 unknown=16
```

Pass criteria, read from the live root:

```
Mavic 2 Pro Extended  flights=188/100 minutes=1018/1000  due
Mavic 2 Pro Basic     flights=19/20   minutes=62/200      due soon
Air 3S Basic          flights=16/20   minutes=111/200     due soon
Mavic 2 battery 3402QS Basic flights=30/20 charges=15/20  due
```

Archive-then-capture and capture-then-archive produced an identical baseline
signature; a repeat migration was a no-op.

### 8.3 Archive totals against the reference

```
records 264 (reference 264)          air 24h21m (reference 24h10m, +11 min)
distance 95.0 mi (reference 108 mi, -13.0 mi)
photos 983 (reference 749)           video count unknown (reference 459)
decoded coverage 225/264 (39 pending key)
```

Unexplained, and one difference points the wrong way, so AirData stays active.

### 8.4 Truth repairs applied to the live root

```
temperatures: 217 records whose every sample was 0.0 -> unknown
              7 records with a genuine non-zero maximum kept
duration:     1 record claiming 191 h -> unknown (all other aggregates unchanged)
photos:       1.17 billion on one record -> unknown (archive total now 983)
video:        29 records exceeding their own flight duration -> unknown
reparse:      equal-weight reparse refreshes metrics; 0 duplicate flights
```
