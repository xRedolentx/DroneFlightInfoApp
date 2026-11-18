# CHECKPOINT — resume here

Written 2026-11-17, end of day. Read this first, then `BUILD-STATUS.md`.
Everything below was verified on this machine today; nothing is aspirational.

## State at a glance

- Local Git: a linear history on one branch; **no remote and nothing pushed**.
  Check the tip with `python tools\local_commit.py` (prints the short hash) or by
  reading `.git/refs/heads/master`.
- Working tree: clean.
- `python -m flightdesk selftest` → **130 tests, 0 failures, 0 errors**.
- `node tools\ui_check.mjs <port> <outdir> <zip>` → **65 checks, 0 failures**.
- `python tools\rehearse.py` → **13/13 steps pass** (import → capture → backup →
  restore into a new root → totals and statuses identical).
- `python tools\recon_check.py` → **11/11**.
- `python tools\fault_check.py` → interrupted backup returns `disk_full`, leaves
  **no** partial archive, releases its lock, and the next backup validates.

## What is done

Stages 1–7 are implemented and carry synthetic acceptance evidence. The app
runs from `FlightDesk.cmd`, binds to loopback only, bundles its own CSS/JS, and
makes no external request except the DJI keychain exchange the owner triggers.
Details and per-stage evidence: `BUILD-STATUS.md` and `VERIFICATION.md`.

Notable fix made at the end of today: `create_backup()` used to **raise** on a
disk-full or write error and leave a truncated `FlightDesk-backup-*.zip` beside
real backups — a backup that looks valid and is not. It now writes `*.part`,
renames only on success, records a `failed` row, and returns
`{"ok": false, "code": "disk_full"}`. Three regression tests cover it
(`test_stage7_backup.py`) and the import side is covered too
(`test_stage1_import.py::FailureTest::test_disk_full_*`).

## What is NOT verified — needs you (in this order)

1. **Import the real archive.**
   `python tools\diag.py "<path-to-handoff.zip or folder>"` prints counts by
   source, format version and status only — never a filename, path or serial.
   Expected for the archive: **269 distinct hashes**, versions **7 / 222 / 40**.
   Then do it in the app: Import → Choose ZIP… (the pickers now work; see below).

2. **Apply the real equipment capture.** Settings → Equipment capture →
   Choose file… → **Preview** (check 3 current / 4 historical / 20 batteries /
   1 placeholder / 21 service rows, 17 resolved, 4 grouped) → **Apply**.
   Applying twice is a no-op.

3. **Both controllers, twice each.** Settings → USB controllers → Check
   controllers now. First attach imports eligible logs; second attach must add
   zero. Record the Smart Controller and the RC 2 **separately**. The five
   supplied controller samples cannot certify live hardware.

4. **DJI key, once.** Settings → DJI key → Save → Decode pending. Then close the
   app, disconnect the network, reopen and reparse one decoded v14 file to prove
   the cached keychain works offline.

5. **Restore rehearsal on real data** before the February 2027 renewal, then the
   cutover decision. Keep AirData active while any material difference is
   unexplained.

## Known gaps I did not close

- **Restore interruption** is tested only at the ends (validation failure,
  live-root refusal, corrupted archive). A crash *during* extraction is not
  injected. The code extracts to `<target>.restore-tmp` and only renames after
  hashes pass, so a crash should leave the target untouched and a stale temp
  folder — but that is reasoning, not evidence. `tools/fault_check.py` is the
  place to add it.
- **Backup while the USB watcher is running** is covered only by a settings-level
  test, not by a genuinely concurrent scan. SQLite's backup API plus `BEGIN IMMEDIATE`
  makes this safe, but it is not measured.
- **Real-data numbers** (264 files, 24h10m, 108 mi, 749 photos, 459 videos) are
  unverified here because no real input exists on this machine. The app reports
  `signed_differences: null` until *Mark records as my real inputs* is ticked, so
  it cannot accidentally claim a match.
- **Visual parity** against the 23 original screenshots needs your images; the
  in-app `#/compare` page pairs them without uploading anything.

## Things that bit us today (so they do not bite again)

- The import summary used to be painted and then wiped by the re-render. It now
  persists in `sessionStorage` for the tab and survives navigation.
- `Choose ZIP` used to call a Windows dialog owned by the server process; when
  that dialog did not appear the click silently did nothing. All three pickers
  are now browser-owned `<input type=file>` elements, with the server dialog
  kept only as an optional, self-explaining fallback.
- Duplicate member names inside a ZIP are legal and now accepted (content hash
  dedupes); escaping paths, symlinks, encrypted entries and oversized members
  are still rejected before extraction.
- On a fresh install, a deep link such as `#/import` used to be replaced by the
  welcome screen, leaving that page without working controls.

## How to resume tomorrow

```powershell
cd "C:\Users\raven\Desktop\Deepseek\Air-Data-Replace-Build"
python -m flightdesk selftest          # expect: 130 tests, 0 failures
python tools\fault_check.py            # expect: disk_full, no partial archive
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
  `cargo build --release --manifest-path shim\Cargo.toml` (Rust + MSVC Build
  Tools are now installed) and copy the exe to
  `%LOCALAPPDATA%\FlightDesk\vendor\flightdesk-decode\`.
- `git.exe` is not installed; commits use `python tools\local_commit.py "msg"`
  (dulwich, local only, refuses to commit data/build artifacts).
- Screenshots live in `%LOCALAPPDATA%\FlightDesk\screenshots\`, never in the repo.
- Dry run of the browser gate: start the app on 8787 with a seeded root, start
  Edge with `--remote-debugging-port=9331`, then
  `node tools\ui_check.mjs 9331 <outdir> <synthetic-zip>`; build a ZIP with
  `python tools\make_synthetic_handoff.py out.zip`.
