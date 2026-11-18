# Air Data equipment and maintenance capture — SYNTHETIC GRID FIXTURE

> Invented aircraft, serials, dates and readings. This fixture exists to mirror
> the *shape* of a real capture: a per-item rule grid, rule counters inside the
> battery table, an inventory that lists more batteries than it can read, and a
> service history that claims a total the rows do not reach.

> Captured: 2026-02-02

## What does not exist (Enterprise-only, pages showed sample data)

- Nothing to migrate here; sample rows only.

## Drones (flight logs on record)

| Drone | Model | Serial | Purchased | Manufactured | Last flight | Firmware | Notes |
|---|---|---|---|---|---|---|---|
| Ridge - Nova 5 | DJI Nova 5 | SYN-N5-0101 | 2024-03-02 | 2024-01-11 | 2026-01-28 | 01.00.0400 | Primary aircraft |
| Ridge - Nova 5S | DJI Nova 5S | SYN-N5S-0102 | 2025-05-06 | 2025-03-20 | 2026-01-30 | 01.00.0200 | Secondary |
| Ridge - Kite 2 | DJI Kite 2 | SYN-K2-0103 | 2023-09-14 | 2023-07-01 | 2026-01-25 | 01.00.0100 | Third aircraft |

## Current service status (rules)

| Item | Basic | Extended | Full |
|---|---|---|---|
| Ridge - Nova 5 | 14/20, 150/200 (last 2025-11-02) | 88/100, 940/1000 | 88/200, 940/2000 |
| Ridge - Nova 5S | 21/20, 205/200 (last 2025-12-12) | 21/100, 205/1000 | 21/200, 205/2000 |
| Ridge - Kite 2 | 4/20, 40/200 | 4/100, 40/1000 | 4/200, 40/2000 |

## Batteries (3 listed; 2 readable)

| Name | Type | mAh | Op | Purchased | Mfd | Serial | Last flight | Life | Logs | Chg | Air | Avg | MaxF | LowCap | Basic | Full |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Bat-Nova5-A | Nova 5 | 3850 | active | 2024-03-02 | 2024-01-11 | SYN-BAT-1001 | 2026-01-28 | 96% | 14 | 18 | 150 | 3.2 | 88 | 94 | 18/20, 14/20, 150/200 | 18/160, 14/160, 150/1600 |
| Bat-Nova5-B | Nova 5 | 3850 | active | 2024-05-02 | 2024-03-11 | SYN-BAT-1002 | 2026-01-30 | 92% | 22 | 26 | 260 | 3.1 | 91 | 90 | 26/20, 22/20, 260/200 | 26/160, 22/160, 260/1600 |

## Completed / skipped service history (6 records)

| Date | Item | Service | Status | Counters at the time |
|---|---|---|---|---|
| 2025-11-02 | Ridge - Nova 5 | Basic | Completed | 12/20, 140/200 |
| 2025-12-01 | BAT-SYN-BAT-1001 | Basic | Completed | 16/20, 14/20, 168/200 |
| 2026-01-04 | Ridge - Kite 2 | Basic | Skipped | 4/20, 40/200 |
| 2026-01-10 | Ridge - Nova 5 | Basic + Extended | Completed | 13/20, 146/200, 84/100, 900/1000 |
| 2026-01-15 | Bat-Nova5-B, Bat-Nova5-A | Basic | Completed | 20/20, 20/20, 200/200 |
| 2026-01-20 | Old Ridge - Kite Prime | Full | Completed | 150/160, 150/1600 |
