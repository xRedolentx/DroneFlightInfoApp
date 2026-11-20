# Flight Desk

A personal, Windows-local replacement for the AirData screens Kevin uses. It
answers three questions quickly:

1. Which flights did I just import?
2. Which aircraft or battery needs attention?
3. Can I trust the history enough to leave AirData?

Everything stays on this machine. There is no login, no telemetry, no hosted
database, and no request to any external service except the single DJI
keychain exchange you explicitly trigger with your own developer key.

Start with **`OWNER-GUIDE.md`**. Stage-by-stage status is in `BUILD-STATUS.md`;
the raw evidence is in `VERIFICATION.md`. If you are resuming work on a later
day, read **`CHECKPOINT.md`** first: it lists what is verified, what is still
waiting on you, and the exact commands to re-check both.

## Start it

Double-click **`FlightDesk.cmd`** in this folder. It opens your flight list at
<http://127.0.0.1:8765/#/flights> in your browser.

Double-clicking again while it is already running brings the same page back
instead of starting a second copy. The console window stays open while the app
runs; closing it stops the server.

Requirements: Windows 10/11 and Python 3.11+ on `PATH`. Everything else is
already installed under `%LOCALAPPDATA%\FlightDesk`.

The first screen offers two choices:

- **Import my logs** — pick your handoff ZIP, an extracted folder, or a single
  DJI TXT file. Nothing is uploaded; the file is read by local code only.
- **Explore invented demo data** — a synthetic data set, labelled `SYNTHETIC`
  in the header everywhere it is used.

## What each screen is for

- **Flights** — search and filter by date, drone, battery, pilot and tag. The
  four tiles and the record cards use the same filters as the list. Records
  show air time, path distance, max altitude, max speed and battery endpoints.
- **Selected flight** — Overview has the track (drawn on a local grid, no map
  tiles) flanked by metric cards and synchronized charts. Details shows
  provenance: air vs recorder vs airborne time, height above takeoff vs
  altitude, path distance vs max home distance, sample and gap counts, and the
  source files. Equipment links the flight to its aircraft and battery.
- **Batteries & drones** — usage, flight history, health readings and service
  record for one item.
- **Maintenance** — Upcoming (grouped by batteries and aircraft), Completed,
  and a full rule list. Each card shows which counter fired and the baseline it
  started from.
- **Import** — drop target, file/folder/ZIP choosers, batch results in
  new / already imported / awaiting DJI / needs attention, and import history.
- **Settings** — DJI key, USB controllers, backup and export, reconciliation,
  the equipment-capture migration, and the privacy switch.

## Everyday jobs

**Import today's flights.** Import → Choose folder (or drop the ZIP). A file
seen before is reported as *already imported*; it is never duplicated.

**Check what needs attention.** Maintenance → Upcoming. A card is due because a
specific counter crossed its limit; the card names the counter. Complete or
Skip with a note, and Undo if you change your mind. Completing resets only that
one rule on that one item.

**Copy logs off a controller.** Plug the controller in, unlock it, choose file
transfer, then Settings → Check controllers now. The collector only reads
`DJIFlightRecord_*.txt` / `FlightRecord_*.txt` from the two documented folders.
It never writes to the controller.

**Back up.** Settings → Create backup. Pick a folder that is *not* inside
OneDrive/Dropbox/Google Drive — Flight Desk warns you if it is. A backup is one
consistent database snapshot plus every archived original plus a hash manifest.

**Rehearse a restore.** Settings → Restore into a new root. It extracts into a
fresh folder, checks every hash and the database's foreign keys, and only then
reports success. The live data root is never used for this.

**Export for a spreadsheet.** Settings → Flights / Equipment / Services CSV.
The fourth button exports without serials or coordinates.

## Where your data lives

`%LOCALAPPDATA%\FlightDesk\data` by default:

- `archive\` — the original bytes, content-addressed and immutable
- `flightdesk.sqlite3` — the index (flights, equipment, rules, services)
- `backups\`, `exports\`, `logs\`, `tmp\`

The DJI key is stored separately at `%LOCALAPPDATA%\FlightDesk\keychains\dji_key.bin`
and is encrypted with Windows DPAPI for your Windows user. It is never written
to the database, a backup, a URL or a process argument.

## If something looks wrong

- **A file says "awaiting DJI key".** It is an encrypted v13/v14 log. Its
  metadata (date, aircraft, duration) is already shown. Set your key in
  Settings, then press *Decode pending*. Cached keychains are reused offline.
- **A counter says "unknown".** The capture never published that counter, or
  the flights behind it have no duration. Unknown is shown as unknown; it is
  never treated as zero.
- **A flight says "needs review".** Two files shared a start time but did not
  corroborate. Both are kept; the extra one is excluded from totals until you
  resolve it on the Import page.
- **Run the checks yourself.** `python -m flightdesk selftest` runs the full
  suite. `python tools\rehearse.py` runs an end-to-end backup/restore rehearsal
  on a throwaway data root.

## Removing it

Delete this folder and `%LOCALAPPDATA%\FlightDesk`. If you installed the
logon watcher, remove it first in Settings → USB controllers.
