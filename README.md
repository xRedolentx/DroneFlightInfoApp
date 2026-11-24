# Flight Desk

A personal, Windows-local replacement for the AirData screens Kevin uses. It
answers three questions quickly:

1. Which flights did I just import?
2. Which aircraft or battery needs attention?
3. Can I trust the history enough to leave AirData?

Everything stays on this machine. There is no login, no telemetry, no hosted
database. Flight Desk makes no network request unless you ask it to: the single
DJI keychain exchange you explicitly trigger with your own developer key, and
the optional OpenStreetMap basemap, which stays off until you allow it.

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

**Check the machine first** (changes nothing):

```powershell
python tools\check_env.py          # or --json for machine-readable output
```

It reports Python, SQLite, the pinned parser, the keychain decoder and
PowerShell, and exits non-zero when something mandatory is missing. It never
downloads or installs anything: setup is a separate, explicit action.

The first screen offers two choices:

- **Import my logs** — pick your handoff ZIP, an extracted folder, or a single
  DJI TXT file. Nothing is uploaded; the file is read by local code only.
- **Explore invented demo data** — a synthetic data set, labelled `SYNTHETIC`
  in the header everywhere it is used.

## What each screen is for

- **Flights** — search and filter by date, drone, battery, pilot and tag. The
  four tiles and the record cards use the same filters as the list. Records
  show air time, distance, max altitude, max speed and battery endpoints. The
  distance shown is the path measured from the samples when there is one; a
  record with no sample path shows the log header's own figure and says so.
  **Air time and log time are different figures.** Air time is the time the
  aircraft was off the ground; log time is the whole record, including the time
  it sat on the ground before and after. AirData shows the same two, and the
  dashboard tile carries both so neither has to be guessed at.
- **Selected flight** — Overview has the track (drawn on a local grid by
  default; an OpenStreetMap basemap is available in Settings and stays off
  until you allow it) flanked by metric cards and synchronized charts. Details
  shows provenance: air vs recorder vs airborne time, height above takeoff vs
  altitude, path length vs the log's total distance, sample and gap counts, and
  the source files. Equipment links the flight to its aircraft and battery.
- **Batteries & drones** — usage, flight history, health readings and service
  record for one item.
- **Maintenance** — Upcoming (grouped by batteries and aircraft), Completed,
  and a full rule list. Each card shows which counter fired and the baseline it
  started from.
- **Import** — drop target, file/folder/ZIP choosers, batch results in
  new / already imported / awaiting DJI / needs attention, and import history.
- **Settings** — DJI key, USB controllers, raw log folder, backup and export,
  reconciliation, the equipment-capture migration, and the privacy switch.

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
consistent database snapshot plus every archived original plus a hash manifest,
**and your DJI key** as its Windows-protected file. The protection is per-user and
per-machine: a restore here needs no re-entry, a restore elsewhere must re-enter
the key.

**Rehearse a restore.** Settings → Restore into a new root. It extracts into a
fresh folder, checks every hash and the database's foreign keys, and only then
reports success. The live data root is never used for this.

**Export for a spreadsheet.** Settings → Flights / Equipment / Services CSV.
The fourth button exports without serials or coordinates.

**Copy the original logs somewhere usable.** Settings → Raw log folder. The
archive keeps each original under a name made from its contents, which is right
for integrity and useless in another program. This copies them out under their
real DJI file names. It only ever adds files: nothing in the folder is deleted or
overwritten, including a file you put there yourself, and re-running it copies
only what is new. A manifest in the folder makes the second run fast.

## Where your data lives

`%LOCALAPPDATA%\FlightDesk\data` by default:

- `archive\` — the original bytes, content-addressed and immutable
- `flightdesk.sqlite3` — the index (flights, equipment, rules, services)
- `backups\`, `exports\`, `logs\`, `tmp\`

The DJI key is stored inside the data root at
`%LOCALAPPDATA%\FlightDesk\data\keychains\dji_key.bin` and is encrypted with
Windows DPAPI for your Windows user. It is never written into the database text,
a URL or a process argument. It **is** carried in a backup as that protected
file, so a restore on this machine keeps working; the blob only decrypts for this
Windows account, and anyone who can read the backup as you can use the key.

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
