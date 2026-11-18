# AirData equipment capture — SYNTHETIC FIXTURE

> Invented aircraft, serials, dates and counters. Shaped like Kevin's local
> capture so the local parser and migration tests exercise real table forms.
> Captured: 2026-11-17
> Source: synthetic fixture, not an AirData account.

### Current aircraft

| Aircraft | Serial | Status | Purchase date | Firmware | Notes |
|---|---|---|---|---|---|
| Mavic 2 Pro | SYN-M2P-0001 | Active | 2019-06-01 | 01.00.0770 | Primary mapping aircraft |
| Air 3S | SYN-A3S-0002 | Active | 2024-12-10 | 10.01.0300 | Filename time runs about two hours off local time |
| Mini 3 Pro | SYN-M3P-0003 | Active | 2022-05-20 | 01.00.0900 | Light travel kit |

### Historical aircraft (archived)

| Aircraft | Serial | Last flown | Service reference | Notes |
|---|---|---|---|---|
| Phantom 4 Pro | SYN-P4P-0101 | 2021-08-14 | SRV-2019-004 | Retired; keep history |
| Mavic Air | SYN-MAV-0102 | 2020-03-02 | SRV-2018-011 | Sold locally |
| Spark | SYN-SPK-0103 | 2019-11-30 | SRV-2017-009 | Retired |
| Inspire 2 | SYN-I2-0104 | 2018-06-21 | SRV-2016-002 | Retired; service reference only |

### Aircraft maintenance snapshot

| Aircraft | Rule | Counter | Status | Last baseline |
|---|---|---|---|---|
| Mavic 2 Pro | Basic | 19/20 flights | Due soon | 2026-09-20 |
| Mavic 2 Pro | Extended | 188/100 flights; 1018/1000 minutes | Due | 2020-06-01 |
| Mavic 2 Pro | Full | 188/200 flights; 1018/2000 minutes | Due soon | 2020-06-01 |
| Air 3S | Basic | 16/20 flights | Due soon | 2026-08-02 |
| Air 3S | Extended | 16/100 flights; 212/1000 minutes | OK | 2024-12-10 |
| Air 3S | Full | 16/200 flights; 212/2000 minutes | OK | 2024-12-10 |
| Mini 3 Pro | Basic | 4/20 flights | OK | 2026-04-11 |
| Mini 3 Pro | Extended | 4/100 flights; 61/1000 minutes | OK | 2022-05-20 |
| Mini 3 Pro | Full | 4/200 flights; 61/2000 minutes | OK | 2022-05-20 |

### Batteries

| Battery | Serial | Aircraft | Purchase date | Firmware | Temperature | Locked | Notes |
|---|---|---|---|---|---|---|---|
| Mavic 2 battery A | SYN-BAT-1001 | Mavic 2 Pro | 2019-06-01 | 02.00.0010 | 35°F | no |  |
| Mavic 2 battery B | SYN-BAT-1002 | Mavic 2 Pro | 2019-06-01 | 02.00.0010 | 36°F | no |  |
| Mavic 2 battery C | SYN-BAT-3402QS | Mavic 2 Pro | 2020-02-11 | 02.00.0010 | 32°F | yes | Locked detail: cell 3 replaced under service SRV-2023-014 |
| Mavic 2 battery D | SYN-BAT-1004 | Mavic 2 Pro | 2020-07-03 | 02.00.0010 | 34°F | no |  |
| Air 3S battery 1 | SYN-BAT-2001 | Air 3S | 2024-12-10 | 03.01.0000 | 41°F | yes | Locked detail: first-flight pack |
| Air 3S battery 2 | SYN-BAT-2002 | Air 3S | 2024-12-10 | 03.01.0000 | 40°F | no |  |
| Air 3S battery 3 | SYN-BAT-2003 | Air 3S | 2024-12-10 | 03.01.0000 | 39°F | no |  |
| Mini 3 battery 1 | SYN-BAT-3001 | Mini 3 Pro | 2022-05-20 | 01.00.0500 | 38°F | no |  |
| Mini 3 battery 2 | SYN-BAT-3002 | Mini 3 Pro | 2022-05-20 | 01.00.0500 | 37°F | no |  |
| Mini 3 battery 3 | SYN-BAT-3003 | Mini 3 Pro | 2023-01-08 | 01.00.0500 | 36°F | yes | Locked detail: replacement pack |
| Phantom battery 1 | SYN-BAT-4001 | Phantom 4 Pro | 2017-04-01 | 01.04.0000 |  | no | Archived equipment |
| Phantom battery 2 | SYN-BAT-4002 | Phantom 4 Pro | 2017-04-01 | 01.04.0000 |  | no | Archived equipment |
| Mavic Air battery 1 | SYN-BAT-5001 | Mavic Air | 2018-02-01 | 01.00.0000 |  | no | Archived equipment |
| Spark battery 1 | SYN-BAT-6001 | Spark | 2017-09-01 | 01.00.0000 |  | no | Archived equipment |
| Inspire battery 1 | SYN-BAT-7001 | Inspire 2 | 2016-11-01 | 01.00.0000 |  | no | Archived equipment |
| Inspire battery 2 | SYN-BAT-7002 | Inspire 2 | 2016-11-01 | 01.00.0000 |  | no | Archived equipment |
| Spare battery unassigned | SYN-BAT-8001 |  | 2021-01-01 |  |  | no | Unassigned |
| Travel battery | SYN-BAT-8002 | Air 3S | 2024-12-10 | 03.01.0000 | 44°F | no |  |
| Backup battery | SYN-BAT-8003 | Mini 3 Pro | 2023-01-08 | 01.00.0500 | 43°F | no |  |
| Placeholder battery |  |  |  |  |  | no | Placeholder — do not schedule service |

### Battery maintenance snapshot

| Battery | Rule | Counter | Status | Last baseline |
|---|---|---|---|---|
| SYN-BAT-3402QS | Basic | 30/20 flights; 30/20 charges; 500/200 minutes | Due | 2026-02-14 |
| SYN-BAT-1001 | Basic | 12/20 flights; 12/20 charges | OK | 2020-01-10 |
| SYN-BAT-1002 | Basic | 8/20 flights; 8/20 charges | OK | 2021-03-04 |
| SYN-BAT-2001 | Basic | 5/20 flights; 5/20 charges | OK | 2024-12-10 |
| SYN-BAT-2002 | Basic | 16/20 flights; 16/20 charges | Due soon | 2025-01-02 |
| SYN-BAT-3001 | Basic | 2/20 flights; 2/20 charges | OK | 2025-06-01 |
| SYN-BAT-8002 | Full | 100/160 charges; 100/160 flights; 900/1600 minutes | OK | 2025-02-02 |

### Historical service events

> Values in the counter column are the capture's wording. Temperature cells
> shown as exactly 32°F are placeholders in this capture, not real readings.
> Do not treat 32°F as missing in any other capture.
> Reported total: 21 historical service events.

| Date | Equipment | Rule | Counter | Operator | Notes |
|---|---|---|---|---|---|
| 2017-02-11 | Inspire 2 | Full | 12 cycles | Kevin | Initial service reference |
| 2017-09-03 | Spark | Basic | reset | Kevin | Propeller set replaced |
| 2018-01-19 | Mavic Air | Basic | 44/20 flights | Kevin | Overdue when retired |
| 2018-06-21 | Inspire 2 | Full | 158/160 charges | Kevin | Last service before retirement |
| 2019-06-01 | Mavic 2 Pro | Basic | reset | Kevin | Commissioning baseline |
| 2019-08-17 | Phantom 4 Pro | Extended | reset | Kevin | Annual service |
| 2020-02-11 | Mavic 2 battery B, Mavic 2 battery C | Basic | reset both | Kevin | Grouped row: two batteries, split unclear |
| 2020-06-01 | Mavic 2 Pro | Extended | reset | Kevin | Extended baseline |
| 2020-09-12 | Mavic 2 Pro | Basic | reset | Kevin | Post-summer service |
| 2021-01-15 | Spark, Mavic Air | Basic | archived | Kevin | Grouped row: retired fleet |
| 2021-03-04 | Mavic 2 battery B | Basic | reset | Kevin | Cell check |
| 2021-08-14 | Phantom 4 Pro | Full | retired | Kevin | Final flight reference |
| 2022-05-20 | Mini 3 Pro | Extended | reset | Kevin | Commissioning baseline |
| 2023-01-08 | Mini 3 battery 3 | Basic | reset | Kevin | New battery baseline |
| 2023-04-02 | Mavic 2 Pro | Basic and Extended | reset | Kevin | Grouped row: two rules |
| 2023-11-19 | Air 3S | Basic | pre-purchase | Kevin | Reference only; aircraft not yet owned |
| 2024-01-02 to 2024-01-05 | Mavic 2 Pro | Full | reset | Kevin | Grouped row: date range |
| 2024-12-10 | Air 3S | Extended | reset | Kevin | Commissioning baseline |
| 2025-01-02 | Air 3S battery 2 | Basic | reset | Kevin | Cell balance check |
| 2026-02-14 | Mavic 2 battery C | Basic | reset | Kevin | Locked detail retained |
| 2026-09-20 | Mavic 2 Pro | Basic | reset | Kevin | Most recent Basic baseline |
