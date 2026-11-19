# CHECKPOINT — resume here

Written 2026-11-19 23:30 UTC. Read this first, then `BUILD-STATUS.md`.
Everything below was measured on this machine at the time of writing; nothing is
aspirational.

## State at a glance

- Local Git: a linear history on one branch; **no remote and nothing pushed**.
  Code tip: **`c58b2b7`** — "editable schedules, reversible restore, visible
  capture gap". 
  (This checkpoint file is committed immediately after it, so `git log -1` will
  show a checkpoint commit whose parent is that code tip. That is expected and is
  why the tip is named rather than derived.)
- Working tree: **clean**.
- `python -m flightdesk selftest` → **275 tests, 0 failures, 0 errors**.
- `node tools\ui_check.mjs <cdp-port> <outdir> <zip>` → **86 checks, 0 failures**.
- `python tools\rehearse.py` → **13/13 steps**.
- `python tools\recon_check.py` → **9/9**.
- `python tools\fault_check.py` → interrupted backup returns `disk_full` with no
  partial archive; interrupted **restore** leaves the target and its marker intact.

## Live data (measured, not remembered)

- **264 flights · 24.35 h · 108.3 mi** · 100% decoded · **0 awaiting the key**.
- 264 raw originals archived; **39 keychains cached**.
- **49 maintenance rules**: 43 AirData + 6 DoneDoctor. Statuses: 27 ok,
  16 unknown, 5 due soon, **1 due**.
- **57 flights** carry at least one counted fault.
- **5 batteries** have exceeded the 40 °C limit (peaks 44.0–53.6 °C).
- **`usb_scans = 0`** — no controller has ever been attached.
- Last backup: **2026-11-19 19:57 UTC, status `ok`** — this predates the Stage 12
  and 13 code changes, so take a fresh one.
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

## What is NOT verified — needs the owner

1. **Both controllers, twice each.** Still `usb_scans = 0`. Power one, choose
   file transfer, **Settings → USB controllers → Check controllers now**, record
   the result, unplug, replug, check again (expect **0 new**). Repeat for the
   second. Record the Smart Controller and the RC 2 **separately**.
2. **A fresh backup**, then **Validate a backup…** on it. The existing one
   predates the last two code changes.
3. **Restore rehearsal on real data.** Restore into a *new empty folder* and
   choose **Keep current data**. Switching roots is the cutover, which is the
   owner's decision.
4. **Backdate the real services.** Now possible: Maintenance → a rule → **Rule** →
   *Record a service on a past date*. The six DoneDoctor checks started counting
   on 2026-11-19, so without backdating they stay quiet for 60–90 days. Enter the
   real dates for props, deep-clean, IMU/compass calibration and battery cycles.
5. **Read the comparison.** Settings → *Mark records as my real inputs* →
   Reconciliation → Generate report. 108.3 mi against 108; 24h21m against 24h10m,
   explained by the two newest flights the AirData export lacked.

Commercially: **keep AirData active** until the owner is satisfied. Renewal is
February 2027.

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

## Things that bit us (so they do not bite again)

- **Restore used to be destructive**: it deleted the target root and then renamed
  the staged copy in. A crash between lost both. Fixed by moving the old root
  aside and putting it back on failure. The regression test **fails against the
  old code**, so it bites.
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
python -m flightdesk selftest          # expect: 275 tests, 0 failures
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
