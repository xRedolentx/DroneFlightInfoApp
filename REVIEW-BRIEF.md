# Review brief — Flight Desk

You are reviewing a finished personal application for a critical, independent
read. You are not here to confirm it works. It has 331 passing tests and a
55 KB status document saying so, both written by the same process that wrote
the code. Your job is to find what that process could not see about its own work.

Be direct. If something is sound, say so once and move on. Spend your effort on
what is wrong, fragile, or over-engineered.

## What this is

A Windows-local replacement for a drone-flight-log SaaS (AirData). One user, one
machine, no login, no cloud. Python 3.11 standard library + SQLite, a vanilla-JS
single-page front end served from the same process, and one Rust helper binary
that wraps a pinned upstream parser (`dji-log` 0.5.7). Roughly 69 tracked files,
1 MB of source.

Read `README.md` for the user-facing shape. Do not read the other documents
first — see **Read order** below.

## Scope limits — these are hard

- **Do not read the live data root** at `%LOCALAPPDATA%\FlightDesk\` or any
  `data-before-*` sibling. It holds the owner's real flight history, serial
  numbers and coordinates. It is out of scope for this review.
- **Do not read `Screenshots/`.** Personal, and irrelevant to the code.
- **Do not read `shim/target/`, `fixtures/` binary blobs, or any `.sqlite3`.**
  Build output and fixtures; not reviewable content.
- **Review only. Do not edit, write, commit, or push.** There is no remote and
  nothing should be pushed anywhere. Do not run `tools/local_commit.py`.
- Safe to run, if you want evidence: `python -m flightdesk selftest`,
  `python tools/recon_check.py`, `python tools/fault_check.py`. All create their
  own throwaway roots. `selftest` takes ~35 s.

## Read order — this matters

1. **Read this brief.**
2. **Read code first.** Start from the joints listed under *Where we are least
   confident* and form your own view before you read any claim about them.
3. **Only then** skim `CHECKPOINT.md`, `BUILD-STATUS.md`, `VERIFICATION.md`.
   They are evidence, not truth — written by the author defending his own work.
   If a finding of yours already appears there, say so and say whether you
   reached it independently. If a claim there looks wrong, say that too.

If you read our status documents first, you will spend your review agreeing with
them, and the review will be worthless.

## What has been claimed

Compressed, so you know what is being asserted — not so you can verify it by
reading the assertion again.

- 331 tests, 0 failures. **All fixtures are synthetic.** Every real-file path is
  exercised, but only against files this process generated.
- 284 flights in the archive, 419 raw originals, two flights permanently
  unreadable (a v11 log format whose details offset is 0), excluded from totals.
- Backup/restore rehearsed end-to-end on a real backup, including a real
  interrupted-extract fault injection.
- Both DJI controllers read from real hardware; a second attach adds nothing.
- Reconciliation against the AirData export: distance matches, air time differs
  by 8.5 min across 263 flights, explained by two flights the export lacked.

Where each claim was checked is in `VERIFICATION.md`. That is the one document
worth reading closely, because it is where an overclaim would show.

## Where we are least confident — investigate these

These are leads, not accusations. Several are places where the author already
knows something is imperfect. An answer of "this is fine, here is why" is
useful; so is "this is not fine".

**1. Silent failure in the USB watcher.**
`flightdesk/server.py` ~lines 115–116 and 125–126: the watcher loop wraps device
scanning and setting-reads in `except Exception: pass`. This project's own
checkpoint says *"every controller defect was silence, not an error"* and treats
that as the central lesson. Argue whether this loop contradicts the lesson, and
what a failure here would look like to the owner (who sees nothing).

**2. Transaction discipline under `isolation_level=None`.**
`flightdesk/db.py` opens connections in autocommit mode and provides `tx()` as a
context manager issuing `BEGIN IMMEDIATE` (db.py ~431–441). That means a
multi-statement write that forgets `with db.tx(...)` commits each statement
immediately and is never atomic — and nothing in the type system catches it.
Find every write path. Which ones are outside `tx()`? Are any of those
multi-statement? Is there a way to make that a mechanism rather than a
convention?

**3. Writer serialization vs. long operations.**
`ThreadingHTTPServer` with one SQLite connection per request, WAL, and
`BEGIN IMMEDIATE` for writes. A USB scan copies hundreds of files and can run for
seconds. Does a long copy hold the write lock, and if so what does the UI see?
Two threads also mean the browser is polling while the watcher writes — check
what `/api/*` returns during a scan.

**4. The dedupe model.**
`flightdesk/importer.py`: content hash plus a fingerprint of
`(aircraft_sn, start_time, duration)` (~line 89), with same-time weak matches
retained as `unresolved` and excluded from totals (~355, 426–450). What input
merges two genuinely different flights? What input splits one flight into two?
The fixtures are generated by this codebase, so think about what they cannot
contain.

**5. `COALESCE(?,column)` as a backfill guard.**
`importer.py` ~266–278 updates with `COALESCE(?,existing)`, so a reparse never
overwrites stored values with null. This is right for backfill and wrong for
repair, and there is a separate repair path that nulls columns by name. Is the
one-statement difference between "backfill" and "repair" actually safe, or is it
a trap that has simply not been sprung again?

**6. Test adequacy, not test count.**
331 tests, 941 assertions, distributed across 16 files — the count is healthy.
Ask instead: which behaviours are asserted only through the HTTP layer, where a
change in the handler would silently pass? What class of bug would leave all 331
green? Is there a real-file regression suite that does not depend on the
generator agreeing with the parser?

**7. A 162 KB single file.**
`flightdesk/web/app.js`, one file, no framework, no build step. Judge whether
this is a defensible choice for a single-user local app or a maintenance cliff —
and be specific about which part is which.

## What we do not need

- Style, naming, formatting, docstring coverage.
- "Consider adding type hints" / "add logging" / "add CI" boilerplate.
- Suggestions to rewrite in a framework, add async, or move to a hosted
  database. The constraints (offline, single user, no dependencies) are
  deliberate and the owner chose them.
- Praise of the test count or the documentation depth. We wrote those.

## What we do need

Findings ranked by severity, each with:

- **`file:line`** and a one-line title.
- **What breaks**, concretely — the input or sequence that triggers it.
- **Confidence** (`certain` / `likely` / `speculative`) and whether you ran
  something to check.
- **Bug or judgement call.** Say which. A taste disagreement dressed as a defect
  wastes our time, and so does a defect softened into "might be worth
  considering".
- **Blast radius.** This is a personal archive with no second copy of some
  originals. Data corruption outranks a crash, which outranks a slow page.

Then, separately:

- **The three things you would change first**, and why those three.
- **One thing you would remove.** Every codebase of this age has something that
  was built and turned out not to matter.
- **What you would leave alone**, so we know where the criticism stops.

## The question behind the question

The owner wants to cancel an ongoing subscription and trust this archive instead.
Judge the work by that: would you trust your own flight history to this code, and
if not, what precisely would stop you?
