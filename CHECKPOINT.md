# CHECKPOINT — resume here

Written 2026-11-18. Read this first, then `BUILD-STATUS.md`.
Everything below was verified on this machine; nothing is aspirational.

## State at a glance

- Local Git: a linear history on one branch; **no remote and nothing pushed**.
  Check the tip with `python tools\local_commit.py` or by reading
  `.git/refs/heads/master`.
- Working tree: clean after the commit noted at the end of this file.
- `python -m flightdesk selftest` → **153 tests, 0 failures, 0 errors**.
- `node tools\ui_check.mjs <cdp-port> <outdir> <zip>` → **68 checks, 0 failures**.
- `python tools\rehearse.py` → **13/13 steps** (import → capture → backup →
  restore into a new root → totals and statuses identical).
- `python tools\recon_check.py` → **11/11**.
- `python tools\fault_check.py` → interrupted backup returns `disk_full`, leaves
  **no** partial archive, releases its lock, and the next backup validates.

## Real data: imported and applied

- **The real archive is imported.** `tools\diag.py` reported 264 archive files
  (v8:7, v11:218, v14:39) plus 5 controller samples = **269 distinct hashes at
  7/222/40**, matching the brief's table. The live data root holds 264 raw files
  and 264 authoritative flights; 39 v14 records are `pending_key` and carry
  metadata only.
- **The real equipment capture is applied.** 26 equipment rows (3 current
  aircraft, 3 archived aircraft that appear only in the service history,
  20 batteries including 3 identified placeholders for entries the capture
  lists but cannot read), 43 rules, 72 snapshot counters, 13 service rows
  (12 resolved, 1 grouped).
- Live due statuses match the capture: **2 due, 5 due soon, 20 ok, 16 unknown**.
  The unknown 16 are batteries whose capture row published no counter; a missing
  counter is not a zero.
- Rollback copies of the data root, taken before each real-data change, live
  under `%LOCALAPPDATA%\FlightDesk\` as `data-before-*`. They are not in Git.

## Real-data fixes made on 2026-11-18

1. **The capture parser could not read the real capture.** It assumed the
   fixture's layout; the real file files counters in a per-item rule grid and in
   the battery table's own `Basic`/`Full` columns. Shape-based table
   classification, positional counter cells, the `Status` column, a heading-based
   reported total, inventory reconciliation and service-reference resolution are
   now implemented and pinned by 12 tests on a synthetic fixture of that shape.
2. **A temperature of exactly 0.0 for every sample is now unknown**, not a
   reading (217 of 224 decoded records were affected).
3. **Values beyond physical limits become unknown with a flag** — one real
   record claimed 191 hours; another 1,176,826,604 photos. Video seconds are
   bounded by the flight's own duration.
4. **Reparse now backfills.** An equal-weight reparse refreshes the matched
   flight (a poorer parse still never replaces a richer one), and a reparse can
   no longer duplicate a flight when a metric correction changes the identity
   fingerprint.
5. **The browser gate is portable.** It no longer hardcodes port 8787 and no
   longer attaches to an Edge internal page, both of which produced false
   failures on a fresh profile.

## What is NOT verified — needs you (in this order)

1. **Both controllers, twice each.** Settings → USB controllers → Check
   controllers now. First attach imports eligible logs; the second must add
   zero. Record the Smart Controller and the RC 2 **separately**. The five
   supplied controller samples cannot certify live hardware.
2. **DJI key, once.** Settings → DJI key → Save → Decode pending (39 records).
   Then close the app, disconnect the network, reopen and reparse one decoded
   v14 file to prove the cached keychain works offline.
3. **Compare the decoded totals against AirData.** Records match (264), but air
   time reads 24h21m against 24h10m and distance 95.0 mi against 108 mi, with 39
   records still undecoded. Tick *Mark records as my real inputs* to see the
   signed difference. **Keep AirData active while this is unexplained.**
4. **Restore rehearsal on real data**, then the cutover decision (the renewal
   noted in the handoff is February 2027).

## Known gaps I did not close

- **Restore interruption** is tested only at the ends (validation failure,
  live-root refusal, corrupted archive). A crash *during* extraction is not
  injected; the code extracts to `<target>.restore-tmp` and renames only after
  hashes pass, but that is reasoning, not evidence. `tools/fault_check.py` is
  the place to add it.
- **Backup while the USB watcher is running** is covered only by a
  settings-level test, not a genuinely concurrent scan.
- **The capture claims 21 service records and supplies 13 rows.** The 8 missing
  are reported as unaccounted, never invented.
- **Video semantics** are still unconfirmed: logs give video seconds, not a
  count, so `videos_count` stays `null`.
- **Visual parity** against the 23 original screenshots needs your images; the
  in-app `#/compare` page pairs them without uploading anything.

## Things that bit us (so they do not bite again)

- The import summary used to be painted and then wiped by the re-render. It now
  persists in `sessionStorage` for the tab and survives navigation.
- `Choose ZIP` used to call a Windows dialog owned by the server process; all
  three pickers are now browser-owned `<input type=file>` elements.
- Duplicate member names inside a ZIP are legal and accepted (the content hash
  dedupes); escaping paths, symlinks, encrypted entries and oversized members
  are still rejected before extraction.
- On a fresh install, a deep link such as `#/import` used to be replaced by the
  welcome screen, leaving that page without working controls.
- Running two verification passes against one scratch root makes the second one
  fail (the ZIP is already imported); use a fresh root per run.

## How to resume

```powershell
cd "C:\Users\raven\Desktop\Deepseek\Air-Data-Replace-Build"
python -m flightdesk selftest          # expect: 153 tests, 0 failures
python tools\fault_check.py            # expect: disk_full, no partial archive
python tools\diag.py "<handoff zip>"   # expect: 269 distinct, 7/222/40
flightdesk.cmd                          # or: python -m flightdesk serve
```

Then pick the first unticked item from *What is NOT verified* above. If you plug
in a controller or paste a DJI key, do that in the app; do not paste either into
the chat.

## Environment facts worth remembering

- Parser: `dji-log` **0.5.7**, pinned upstream Windows binary, installed at
  `%LOCALAPPDATA%\FlightDesk\vendor\dji-log\0.5.7\dji-log.exe` with a manifest.
- Keychain decoder: `flightdesk-decode`, built locally from `shim/` against
  `dji-log-parser =0.5.7`; rebuild with
  `cargo build --release --manifest-path shim\Cargo.toml` and copy the exe to
  `%LOCALAPPDATA%\FlightDesk\vendor\flightdesk-decode\`.
- Commit with `python tools\local_commit.py "msg"` (dulwich, local only, refuses
  to commit data/build artifacts). `git.exe` did work on this machine today for
  read-only inspection.
- Screenshots live in `%LOCALAPPDATA%\FlightDesk\screenshots\`, never in the repo.
- Browser gate recipe that works:
  `python tools\make_synthetic_handoff.py out.zip`,
  `python -m flightdesk seed-demo --root <freshdir>`,
  `python -m flightdesk serve --root <freshdir> --port <p> --no-browser`,
  Edge `--headless=new --remote-debugging-port=<d> --disable-extensions
  --disable-sync`, then `node tools\ui_check.mjs <d> <outdir> out.zip`.
