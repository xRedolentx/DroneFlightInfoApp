# Flight Desk — owner guide

Short, plain English. Everything here runs on this laptop only.

## 1. Install and first start

1. Install **Python 3.11 or newer** once, from
   <https://www.python.org/downloads/windows/>. Tick *Add python.exe to PATH*.
2. Double-click **`FlightDesk.cmd`** in this folder.
3. The browser opens on your flight list at `http://127.0.0.1:8765/#/flights`.
   Your data root is created at `%LOCALAPPDATA%\FlightDesk\data` on first
   start. Click it again later and it reopens the same page rather than starting
   a second copy; the console window must stay open while you use the app.

The first screen has two buttons: **Import my logs** and **Explore invented demo
data**. Demo data is fake and stays labelled `SYNTHETIC` in the header.

## 2. Import your AirData archive (manual)

1. Go to **Import**.
2. Press **Choose ZIP…** and pick the original handoff ZIP (or drag it onto the
   drop area). **Choose folder…** takes an extracted `data` folder; **Choose
   files…** takes individual DJI TXT files.
3. Read the four result counts: **New files / Already imported / Awaiting DJI /
   Needs attention**.
   - *Already imported* is normal on a second pass. The same bytes are never
     stored twice.
   - *Awaiting DJI* means an encrypted (v13/v14) log. Its date, aircraft and
     duration are already shown; only the detailed records need your key.
   - *Needs attention* means the file is kept but could not be decoded. The
     reason is a short category on the Import page.

Nothing is written to your data until the original bytes have been hashed and
saved. If the disk fills up, the import stops with that message and does not
half-add a flight.

## 3. Bring in your maintenance history

1. **Settings → Damage capture → Preview a capture…**
2. Pick your `AirData-Equipment-Capture-20261117.md`.
3. Check the preview: how many aircraft, batteries, placeholders, and how many
   service rows are individually resolved versus kept as groups.
4. Press **Apply…** only when that looks right.

Applying twice is safe — the second attempt reports *already applied*. Grouped
rows (two batteries on one line, a date range, two rules in one cell) are kept
exactly as captured and shown under *Needs clarification*. They are never split
into invented per-item records.

## 4. Set up your DJI key (only for encrypted logs)

1. **Settings → DJI key**.
2. Paste your DJI developer key (developer.dji.com → your app → SDK key) and
   press **Save key**. The badge should change from *no key* to *key stored*.
3. Press **Decode pending**. The *pending files* count should fall to zero; if
   it does not, the message tells you which category failed.
4. **Prove the offline cache.** Close Flight Desk, turn off Wi-Fi, reopen it, and
   in **Settings → DJI key** press **Re-decode one record from cache**. You
   should see *Re-decoded a v14 record from its cached keychain (no network
   needed).* Turn the network back on afterwards.

Until the key is entered, an encrypted record shows its date, aircraft, battery
serial and duration only. Distance, height and speed stay blank rather than
showing a guess — a partial total always says how many records it covers.

What happens: Flight Desk sends exactly one request, to DJI's official
keychain endpoint, with the key in a request header. The key is stored with
Windows DPAPI for your user account — never in the database, a backup, a URL or
a process argument. Each decoded log's keychain is cached against that file's
hash, so later launches work offline.

**After a Windows reinstall or a new machine** the DPAPI protection no longer
applies. Re-enter the key; if the DJI app was deleted, create a new one at
developer.dji.com. Nothing is lost — the raw logs are still in your archive.

## 5. Copy logs off a controller (USB)

1. Power the controller and connect it. On the controller, choose **file
   transfer** mode.
2. **Settings → USB controllers → Check controllers now**.
3. Read the result: scanned / new / duplicates / attention.

Under the hood this reads only `DJIFlightRecord_*.txt` (Smart Controller) and
`FlightRecord_*.txt` (DJI RC 2) from the two documented folders. It never writes
to the controller, and a half-finished copy is discarded and retried rather than
imported.

Optional: tick **Start at logon** to install one per-user task so a plugged-in
controller is noticed while the browser is closed. It is reversible on the same
screen.

## 6. Day-to-day: flights and maintenance

- **Flights** — filter by date, drone, battery, pilot or tag. The tiles and the
  cards always show the same filtered set. Click a record, or use ← / → to walk
  between flights without losing the filter.
- **Maintenance → Upcoming** — grouped into Batteries and Aircraft. Each card
  says which counter fired (e.g. *188 / 100 flights*) and which service set the
  baseline. Press **Complete** or **Skip** with a note; **Undo** reverses it and
  leaves an audit trail. A one-off job does not reset a rule unless you link it.
- **Unknown** on a counter means the capture never published that number. It is
  never treated as zero.

## 7. Show a flight on a real map (optional)

The track always draws on a local grid and needs nothing external. A real map
is one click away and entirely opt-in.

1. Open any flight. In the Overview, under the track, press **Enable map…**.
2. Read the disclosure, then **Allow map tiles**. The button then reads **Local
   grid**, so you can flip between views whenever you like.

What leaves this machine: the **tile numbers** for the area on screen. Tile
numbers describe squares of ground, so together they show roughly where the
flight was. Your serials, notes, names and log files are never sent. Nothing is
fetched for areas you are not looking at, and nothing is saved for offline use.

Tiles come from OpenStreetMap, arrive through Flight Desk on this machine, and
are kept in a local cache for at least seven days so panning does not re-request
them. The map always shows *© OpenStreetMap contributors* in the corner, and
Settings → Online map has **Clear tile cache** and a *Report a map issue* link.

Turn it off at any time with **Settings → Online map → Turn map tiles off**.
That stops all tile requests immediately and the local grid becomes the only
view again.

## 8. Back up, and rehearse recovery

1. **Settings → Backup and export**: type the backup folder in the box (or press
   **Choose folder…**). Use a folder **outside** OneDrive/Dropbox/Google Drive —
   Flight Desk warns you if it is inside one. Typing the path always works, even
   if the Windows folder dialog does not appear.
2. Press **Create backup…**. A backup is one file: the database snapshot plus
   every archived original plus a hash manifest.
3. **Validate** with **Validate a backup…** — type or choose the ZIP; it checks
   hashes without changing anything.
4. **Rehearse:** **Restore into a new root…** — type or choose the backup ZIP and
   a new empty folder. It verifies every hash and the database's references
   before saying success. You can then choose *Use restored copy on restart*;
   your current data root is left untouched.

Do this rehearsal before you cancel AirData.

## 9. Exports

**Settings → Backup and export** gives you flights, equipment and services CSV,
plus a redacted mode that omits serials and coordinates.

## 10. If you want to check the app itself

```powershell
python -m flightdesk selftest      # the full test suite
python tools\make_synthetic_handoff.py out.zip   # an invented ZIP to try
python tools\rehearse.py           # end-to-end import → backup → restore
python tools\diag.py "<folder>"    # counts only, no filenames
```

### If a file choice seems to do nothing

The batch summary appears directly under the three buttons. If it says a ZIP
could not be read, the download is probably not a real ZIP (some are
self-extracting `.exe` files) — extract it first, then use **Choose folder…**.
If a card says *Nothing was imported from that file*, the reason is written
underneath it.

The three buttons read the file in your browser and post it to the local server;
they never depend on a Windows dialog. The *Use the Windows dialog instead* link
is only a fallback for browsers without folder uploads.

`tools\diag.py` is the only command meant for real data: it prints counts by
source, format version and status, and never prints a filename, path or serial.

## 11. Removing Flight Desk

Delete this folder and `%LOCALAPPDATA%\FlightDesk`. If you enabled the logon
watcher, untick it first so the scheduled task is removed too.
