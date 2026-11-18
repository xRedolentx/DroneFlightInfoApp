"""Sync BUILD-STATUS, VERIFICATION and README with tonight's verified numbers."""
from pathlib import Path

status = Path("BUILD-STATUS.md")
t = status.read_text(encoding="utf-8")

t = t.replace("- 126 automated tests, all passing (`python -m flightdesk selftest`).",
              "- 130 automated tests, all passing (`python -m flightdesk selftest`).", 1)
t = t.replace("Last updated: 2026-11-17.",
              "Last updated: 2026-11-17 (end of day). Resume from `CHECKPOINT.md`.", 1)

old_verify = """python -m flightdesk selftest        # 120 tests
python tools\\rehearse.py             # end-to-end import → capture → backup → restore
python tools\\recon_check.py          # reconciliation arithmetic
node tools\\ui_check.mjs 9223 <dir>   # scripted browser checks (needs the app running)"""
new_verify = """python -m flightdesk selftest        # 130 tests
python tools\\rehearse.py             # end-to-end import → capture → backup → restore
python tools\\recon_check.py          # reconciliation arithmetic
python tools\\fault_check.py          # interrupted backup / disk-full behaviour
node tools\\ui_check.mjs <port> <outdir> <synthetic-zip>   # 65 browser checks"""
if old_verify in t:
    t = t.replace(old_verify, new_verify, 1)

# Record the durability fix under Stage 7.
old_s7 = """- Backup uses the SQLite backup API, copies every archive object, verifies each
  hash, writes a manifest with schema version and counts, and records the run."""
new_s7 = """- Backup uses the SQLite backup API, copies every archive object, verifies each
  hash, writes a manifest with schema version and counts, and records the run.
- **Interrupted backup is now safe.** The archive is written to `*.part` and
  renamed only after the ZIP closes, so a disk-full or crash cannot leave a
  truncated file that looks like a real backup. A failure returns
  `{"ok": false, "code": "disk_full"}`, records a `failed` run, releases the
  lock, and the next backup validates. Covered by three tests plus
  `tools\\fault_check.py`.
- **Disk full during import** keeps the index clean: one `needs_attention` item,
  `note = disk_full`, no raw file, no flight, no leftover `ingest-*.part`."""
if old_s7 in t:
    t = t.replace(old_s7, new_s7, 1)

old_gaps = """3. **Screenshots 01/03/19/20/21–23 comparison** needs Kevin's reference images.
   The in-app comparison page pairs them without uploading."""
new_gaps = """3. **Screenshots 01/03/19/20/21–23 comparison** needs Kevin's reference images.
   The in-app comparison page pairs them without uploading.
5. **Restore interruption** is only tested at its ends (validation failure,
   live-root refusal, corrupted archive), not injected mid-extraction.
   **Backup during an active USB scan** is covered by a settings-level test, not
   a genuinely concurrent scan. Both are named in `CHECKPOINT.md`."""
if old_gaps in t:
    t = t.replace(old_gaps, new_gaps, 1)

status.write_text(t, encoding="utf-8")

ver = Path("VERIFICATION.md")
v = ver.read_text(encoding="utf-8")
v = v.replace('{"ok": true, "tests": 126, "failures": 0, "errors": 0, "skipped": 0}',
              '{"ok": true, "tests": 130, "failures": 0, "errors": 0, "skipped": 0}')
v = v.replace("- `tests/test_stage1_import.py` — 20 checks",
              "- `tests/test_stage1_import.py` — 23 checks (incl. disk-full paths)")
v = v.replace("- `tests/test_stage7_backup.py` — 12 checks",
              "- `tests/test_stage7_backup.py` — 14 checks (incl. interrupted backup)")
v = v.replace("## 2b. Reconciliation arithmetic",
              """## 2a. Fault injection

```
> python tools\\fault_check.py
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

## 2b. Reconciliation arithmetic""", 1)
ver.write_text(v, encoding="utf-8")

readme = Path("README.md")
r = readme.read_text(encoding="utf-8")
old_readme = """Start with **`OWNER-GUIDE.md`**. Stage-by-stage status is in `BUILD-STATUS.md`;
the raw evidence is in `VERIFICATION.md`."""
new_readme = """Start with **`OWNER-GUIDE.md`**. Stage-by-stage status is in `BUILD-STATUS.md`;
the raw evidence is in `VERIFICATION.md`. If you are resuming work on a later
day, read **`CHECKPOINT.md`** first: it lists what is verified, what is still
waiting on you, and the exact commands to re-check both."""
if old_readme in r:
    r = r.replace(old_readme, new_readme, 1)
readme.write_text(r, encoding="utf-8")
print("docs synced")
