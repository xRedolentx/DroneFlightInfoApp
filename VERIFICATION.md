# Verification record

Everything below was executed on this machine (Windows 11, Python 3.12.10,
Edge, no network needed except the two pinned downloads already installed).
All data is synthetic. Output shown is the aggregate pass/fail form; no
filenames, serials, coordinates or raw stderr were ever printed.

## 1. Test suite

```
> python -m flightdesk selftest
{"ok": true, "tests": 120, "failures": 0, "errors": 0, "skipped": 0}
```

Groups:

- `tests/test_stage1_import.py` — 20 checks
- `tests/test_stage2_equipment.py` — 22 checks
- `tests/test_stage3_web.py` — 26 checks (HTTP security, API, packaged assets,
  a `node --check` parse gate over every browser script, the picker backend, and
  the controller-scan endpoint returning a clean "no device" on a bare machine)
- `tests/test_stage4_usb.py` — 13 checks (simulated adapter)
- `tests/test_stage5_key.py` — 14 checks (exchange shape, DPAPI, offline cache)
- `tests/test_stage6_analysis.py` — 10 checks
- `tests/test_stage7_backup.py` — 12 checks
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

## 3. Browser workflow

```
> node tools\ui_check.mjs <debug-port> <outdir>
...
PASS arrow keys move between flights :: 6 -> 12
PASS privacy mode actually hides a serial :: hidden
PASS no request left loopback
SUMMARY_FINAL PASS checks=40 failures=0
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

- Any real DJI log: no archive, ZIP, capture or controller data exists on this
  machine. Stage 1/2/5 reference numbers in the brief remain unverified here.
- Physical USB controllers (simulated adapter only).
- A live DJI keychain decode (mechanism verified with a synthetic keychain).
- Visual parity against the 23 original screenshots (not present here).
