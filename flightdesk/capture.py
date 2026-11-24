"""Parse and apply the AirData equipment capture (markdown headings + tables).

The capture is evidence, not a schema. Unknown columns are ignored, grouped
rows stay grouped, and a repeat migration is a no-op.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from . import db

AIRCRAFT_DEFAULTS = {
    "basic": {"flights": 20, "minutes": 200, "charges": None},
    "extended": {"flights": 100, "minutes": 1000, "charges": None},
    "full": {"flights": 200, "minutes": 2000, "charges": None},
}
BATTERY_DEFAULTS = {
    "basic": {"flights": 20, "minutes": 200, "charges": 20},
    "full": {"flights": 160, "minutes": 1600, "charges": 160},
}
RULE_LABELS = {"basic": "Basic", "extended": "Extended", "full": "Full"}

COUNTER_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(?:/\s*(\d[\d,]*(?:\.\d+)?))?\s*"
    r"(flights?|charges?|minutes?|mins?|hours?|hrs?|cycles?)",
    re.IGNORECASE,
)
DATE_RANGE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s*(?:to|–|—|\s-\s)\s*(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
CAPTURED_RE = re.compile(r"captured\s*:?\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
REPORTED_RE = re.compile(r"(\d+)\s+historical\s+service\s+events", re.IGNORECASE)
# A heading such as "## Completed / skipped service history (21 records)".
HEADING_TOTAL_RE = re.compile(
    r"\(\s*(\d+)\s+(?:historical\s+)?(?:service\s+)?(?:events?|records?|rows?)\s*\)",
    re.IGNORECASE,
)
# Counter cells written without unit words ("188/100, 1,018/1,000").
PAIR_ONLY_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*/\s*(\d[\d,]*(?:\.\d+)?)")
LAST_DATE_RE = re.compile(r"\(\s*(?:last|at)\s*(\d{4}-\d{2}-\d{2})\s*\)", re.IGNORECASE)
BATTERY_LISTED_RE = re.compile(r"(\d+)\s+listed", re.IGNORECASE)
BATTERY_READABLE_RE = re.compile(r"(\d+)\s+readable", re.IGNORECASE)
SERIAL_LIKE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{5,}")

RULE_COLUMNS = ("basic", "extended", "full")
# Counting order observed in the capture itself: an aircraft rule publishes
# flights then minutes; a battery rule publishes charges first, then flights,
# then air minutes.
POSITIONAL_ORDERS = {2: ("flights", "minutes"), 3: ("charges", "flights", "minutes")}


def norm_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _split_cells(line: str) -> list[str]:
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    return [c.strip() for c in raw.split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in cells if c.strip())


def parse_tables(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    tables: list[dict[str, Any]] = []
    heading = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^\s{0,6}#{1,6}\s+(.*)$", line)
        if m:
            heading = m.group(1).strip()
            i += 1
            continue
        if line.strip().startswith("|"):
            block = []
            start = i
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            if len(block) < 2:
                continue
            header = _split_cells(block[0])
            sep = _split_cells(block[1])
            if not _is_separator(sep):
                continue
            headers = []
            seen: dict[str, int] = {}
            for h in header:
                key = norm_header(h) or "column"
                seen[key] = seen.get(key, 0) + 1
                headers.append(key if seen[key] == 1 else f"{key} {seen[key]}")
            rows = []
            for raw in block[2:]:
                cells = _split_cells(raw)
                if not cells or all(not c for c in cells):
                    continue
                cells += [""] * (len(headers) - len(cells))
                rows.append({headers[j]: cells[j] for j in range(len(headers))})
            tables.append({"heading": heading, "headers": headers, "rows": rows,
                           "line": start + 1})
            continue
        i += 1
    return tables


def pick(row: dict[str, str], *aliases: str, default: str = "") -> str:
    keys = [norm_header(a) for a in aliases]
    for key in keys:
        if key in row and row[key] != "":
            return row[key]
    for key, value in row.items():
        if value and any(k and k in key for k in keys):
            return value
    return default


def section_kind(heading: str) -> str:
    h = heading.lower()
    if "service" in h or "history" in h or "event" in h:
        return "service"
    if "maintenance" in h or "snapshot" in h or "counter" in h:
        return "snapshot"
    if "historical" in h or "archived" in h or "retired" in h:
        return "historical_equipment"
    if "batter" in h:
        return "battery_equipment"
    if "aircraft" in h or "drone" in h:
        return "aircraft_equipment"
    return "other"


def table_kind(headers: list[str], heading: str) -> str:
    """Shape beats heading. AirData files a per-item rule grid
    (Item | Basic | Extended | Full) under a heading that mentions service
    status; that grid is a captured baseline, never a list of service events."""
    hset = set(headers)
    rule_cols = [c for c in RULE_COLUMNS if c in hset]
    if rule_cols and "date" not in hset and "rule" not in hset:
        other = hset - set(RULE_COLUMNS)
        if other <= {"item", "name", "aircraft", "battery", "equipment", "serial", "sn"}:
            return "snapshot_grid"
    return section_kind(heading)


def parse_positional_counters(cell: str) -> list[dict[str, Any]]:
    """Read a counter cell that carries no unit words positionally.

    Two pairs are flights then minutes; three pairs are charges, flights then
    minutes. The capture's own neighbouring columns (Chg, Logs, Air and the
    rule limits 20/200 and 160/1600) are what establish that order.
    """
    pairs = PAIR_ONLY_RE.findall(cell or "")
    if not pairs:
        return []
    order = POSITIONAL_ORDERS.get(len(pairs))
    if order is None:
        return parse_counters(cell)
    out: list[dict[str, Any]] = []
    for metric, (value_s, limit_s) in zip(order, pairs):
        try:
            value = float(value_s.replace(",", ""))
        except ValueError:
            continue
        limit = None
        if limit_s:
            try:
                limit = float(limit_s.replace(",", ""))
            except ValueError:
                limit = None
        out.append({
            "metric": metric,
            "value": value,
            "limit": limit,
            "unit": "minutes" if metric == "minutes" else metric,
            "rounded": 0 if "." in value_s else 1,
            "raw": f"{value_s}/{limit_s}",
        })
    return out


def _baseline_text(cell: str) -> str:
    m = LAST_DATE_RE.search(cell or "")
    return m.group(1) if m else ""


def _looks_like_serial(value: str) -> bool:
    v = (value or "").strip()
    return bool(SERIAL_LIKE_RE.fullmatch(v)) and any(c.isdigit() for c in v) \
        and any(c.isalpha() for c in v)


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def match_inventory(name: str, inventory: list[tuple[str, str]]) -> str | None:
    """Resolve one service reference to one inventory entry, only when the
    evidence is unambiguous.

    Evidence accepted: an exact name; a four-character serial tail; or one
    normalised name containing the other ("Mavic 2 Pro" against
    "DJI Mavic 2 Pro"). Several candidates mean the reference stays
    unresolved instead of being guessed.
    """
    low = (name or "").strip().lower()
    if not low:
        return None
    exact = [entry for entry, _ in inventory if entry.strip().lower() == low]
    if len(exact) == 1:
        return exact[0]
    if _looks_like_serial(name):
        tail = low[-4:]
        hits = {
            entry for entry, serial in inventory
            if (len(entry) >= 4 and tail in entry.lower())
            or (len(serial) >= 4 and tail == serial.lower()[-4:])
        }
        return hits.pop() if len(hits) == 1 else None
    target = _norm_name(name)
    if len(target) < 8:
        return None
    hits = {
        entry for entry, _ in inventory
        if len(_norm_name(entry)) >= 8
        and (_norm_name(entry) in target or target in _norm_name(entry))
    }
    return hits.pop() if len(hits) == 1 else None


def normalize_rule(value: str) -> str | None:
    v = (value or "").strip().lower()
    if not v:
        return None
    if v.startswith("basic"):
        return "basic"
    if v.startswith("extended") or v.startswith("ext"):
        return "extended"
    if v.startswith("full"):
        return "full"
    return None


def split_multi(value: str) -> list[str]:
    if not value:
        return []
    parts = re.split(r"\s*(?:,|;|\band\b|\+|&)\s*", value, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def parse_counters(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in COUNTER_RE.finditer(text or ""):
        value_s, limit_s, unit_s = m.groups()
        unit = unit_s.lower()
        metric = "flights"
        if unit.startswith("charge") or unit.startswith("cycle"):
            metric = "charges"
        elif unit.startswith("min") or unit.startswith("hour") or unit.startswith("hr"):
            metric = "minutes"
        try:
            value = float(value_s.replace(",", ""))
            limit = float(limit_s.replace(",", "")) if limit_s else None
        except ValueError:
            continue
        rounded = "." not in value_s
        if unit.startswith("hour") or unit.startswith("hr"):
            value *= 60.0
            if limit is not None:
                limit *= 60.0
        out.append({
            "metric": metric,
            "value": value,
            "limit": limit,
            "unit": "minutes" if metric == "minutes" else metric,
            "rounded": 1 if rounded else 0,
            "raw": m.group(0).strip(),
        })
    # keep the most specific (last) value per metric
    merged: dict[str, dict] = {}
    for item in out:
        merged[item["metric"]] = item
    return list(merged.values())


def _date_only(value: str) -> str | None:
    m = DATE_RE.search(value or "")
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1)).isoformat()
    except ValueError:
        return None


def _temp_c(value: str) -> float | None:
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*°?\s*([CF])", value or "", re.IGNORECASE)
    if not m:
        return None
    try:
        number = float(m.group(1))
    except ValueError:
        return None
    return number if m.group(2).upper() == "C" else (number - 32.0) * 5.0 / 9.0


@dataclass
class EquipmentRow:
    kind: str
    name: str
    serial: str = ""
    parent: str = ""
    status: str = ""
    purchase: str = ""
    firmware: str = ""
    notes: str = ""
    temperature: str = ""
    locked: bool = False
    placeholder: bool = False
    archived: bool = False


@dataclass
class SnapshotRow:
    equipment_name: str
    serial: str
    rule: str | None
    counters: list[dict[str, Any]]
    status: str
    baseline_text: str


@dataclass
class ServiceRow:
    equipment_names: list[str]
    rules: list[str | None]
    date_raw: str
    occurred_at: str | None
    ended_at: str | None
    counter_text: str
    operator: str
    notes: str
    kind: str
    resolved: bool
    raw_wording: str
    group_key: str
    external_key: str


@dataclass
class CaptureDoc:
    raw_text: str
    text_hash: str
    captured_at: str
    covered_through: str
    current_aircraft: list[EquipmentRow] = field(default_factory=list)
    historical_aircraft: list[EquipmentRow] = field(default_factory=list)
    batteries: list[EquipmentRow] = field(default_factory=list)
    snapshots: list[SnapshotRow] = field(default_factory=list)
    services: list[ServiceRow] = field(default_factory=list)
    reported_total: int | None = None
    temperature_placeholder: bool = False
    battery_listed: int | None = None
    battery_readable: int | None = None
    placeholders_added: int = 0
    reference_only_aircraft: int = 0
    references_resolved: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def resolved_services(self) -> int:
        return sum(1 for s in self.services if s.resolved)

    @property
    def reporting_gap(self) -> int | None:
        """Records the capture claims minus rows it actually carries. Never
        closed by inventing the difference."""
        if self.reported_total is None:
            return None
        return self.reported_total - len(self.services)

    @property
    def grouped_services(self) -> int:
        return sum(1 for s in self.services if not s.resolved)


def parse_capture(text: str) -> CaptureDoc:
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    captured = CAPTURED_RE.search(text)
    captured_at = captured.group(1) if captured else datetime.now(timezone.utc).date().isoformat()
    doc = CaptureDoc(raw_text=text, text_hash=text_hash, captured_at=captured_at,
                     covered_through=captured_at)
    reported = REPORTED_RE.search(text)
    if reported:
        doc.reported_total = int(reported.group(1))
    doc.temperature_placeholder = bool(
        re.search(
            r"32\s*°?\s*F[^\n]{0,40}placeholder|placeholder[^\n]{0,40}32\s*°?\s*F",
            text,
            re.IGNORECASE,
        )
    )

    for table in parse_tables(text):
        kind = table_kind(table["headers"], table["heading"])
        rows = table["rows"]
        if kind in ("aircraft_equipment", "historical_equipment", "battery_equipment"):
            eq_kind = "battery" if kind == "battery_equipment" else "aircraft"
            archived = kind == "historical_equipment"
            for row in rows:
                if eq_kind == "battery":
                    name = pick(row, "battery", "battery name", "item", "name")
                    parent = pick(row, "aircraft", "drone", "model")
                else:
                    name = pick(row, "aircraft", "drone", "model", "name", "item")
                    parent = ""
                if not name:
                    continue
                serial = pick(row, "serial", "serial number", "sn")
                notes = pick(row, "notes", "note", "comments", "comment")
                service_ref = pick(row, "service reference", "reference")
                if service_ref:
                    notes = (notes + " " + f"Service ref: {service_ref}").strip()
                status = pick(row, "status", "state", "operational state")
                locked = pick(row, "locked", "locked details").strip().lower() in (
                    "yes", "true", "1", "locked"
                ) or "locked detail" in notes.lower()
                blob = f"{name} {serial} {notes}".lower()
                placeholder = "placeholder" in blob
                archived_row = archived or any(
                    w in (status + " " + notes).lower()
                    for w in ("retired", "archived", "sold", "decommissioned")
                )
                item = EquipmentRow(
                    kind=eq_kind, name=name.strip(), serial=serial.strip(),
                    parent=parent.strip(), status=status.strip(),
                    purchase=pick(row, "purchase date", "purchased", "bought", "acquired"),
                    firmware=pick(row, "firmware", "firmware version", "fw"),
                    notes=notes.strip(), temperature=pick(row, "temperature", "temp").strip(),
                    locked=locked, placeholder=placeholder,
                    archived=archived_row,
                )
                target = (
                    doc.batteries if eq_kind == "battery"
                    else (doc.historical_aircraft if archived_row else doc.current_aircraft)
                )
                target.append(item)
                if eq_kind == "battery":
                    # The battery table publishes this item's own rule counters
                    # in its Basic and Full columns.
                    for code in RULE_COLUMNS:
                        cell = row.get(code, "")
                        counters = parse_positional_counters(cell)
                        if not counters:
                            continue
                        doc.snapshots.append(SnapshotRow(
                            equipment_name=item.name, serial=item.serial, rule=code,
                            counters=counters, status="",
                            baseline_text=_baseline_text(cell),
                        ))
        elif kind == "snapshot_grid":
            for row in rows:
                name = pick(row, "item", "aircraft", "battery", "equipment", "name")
                serial = pick(row, "serial", "sn")
                if not name and not serial:
                    continue
                for code in RULE_COLUMNS:
                    cell = row.get(code, "")
                    counters = parse_positional_counters(cell)
                    if not counters:
                        continue
                    doc.snapshots.append(SnapshotRow(
                        equipment_name=(name or "").strip(), serial=serial.strip(),
                        rule=code, counters=counters, status="",
                        baseline_text=_baseline_text(cell),
                    ))
        elif kind == "snapshot":
            for row in rows:
                rule = normalize_rule(pick(row, "rule", "rule set", "maintenance rule"))
                counter = pick(row, "counter", "counters", "usage", "reading")
                if not rule or not counter:
                    continue
                name = pick(row, "aircraft", "battery", "equipment", "item", "name")
                serial = pick(row, "serial", "sn")
                if not name and not serial:
                    continue
                doc.snapshots.append(SnapshotRow(
                    equipment_name=name.strip(), serial=serial.strip(), rule=rule,
                    counters=parse_counters(counter),
                    status=pick(row, "status", "state"),
                    baseline_text=pick(row, "last baseline", "baseline", "last service"),
                ))
        elif kind == "service":
            for row in rows:
                equipment_cell = pick(row, "equipment", "equipment name", "aircraft",
                                      "battery", "item")
                rule_cell = pick(row, "rule", "rule set", "maintenance rule", "service")
                date_raw = pick(row, "date", "when", "performed", "service date")
                counter_text = pick(row, "counter", "counters", "reading")
                operator = pick(row, "operator", "technician", "by")
                notes = pick(row, "notes", "note", "comments", "comment")
                status_cell = pick(row, "status", "state")
                equipment_names = split_multi(equipment_cell)
                rules = [normalize_rule(r) for r in split_multi(rule_cell)]
                rules = [r for r in rules if r]
                rng = DATE_RANGE_RE.search(date_raw)
                occurred = None
                ended = None
                if rng:
                    occurred, ended = rng.group(1), rng.group(2)
                else:
                    occurred = _date_only(date_raw)
                resolved = (
                    len(equipment_names) == 1
                    and len(rules) <= 1
                    and occurred is not None
                    and not rng
                )
                low = (notes + " " + date_raw).lower()
                status_low = (status_cell or "").lower()
                if "skip" in low or "skip" in status_low:
                    service_kind = "skipped"
                elif "reference only" in low or "pre-purchase" in low or "not yet owned" in low:
                    service_kind = "reference"
                elif "one-off" in low or "oneoff" in low or "one off" in low:
                    service_kind = "oneoff"
                else:
                    service_kind = "completed"
                if "(reset)" in status_low and service_kind == "skipped":
                    notes = (notes + " Skipped with reset.").strip()
                raw_wording = " | ".join(str(v) for v in row.values())
                key_material = "|".join([
                    occurred or "", ended or "", equipment_cell, rule_cell,
                    counter_text, operator, notes,
                ])
                external_key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()
                doc.services.append(ServiceRow(
                    equipment_names=equipment_names,
                    rules=rules,
                    date_raw=date_raw,
                    occurred_at=occurred,
                    ended_at=ended,
                    counter_text=counter_text,
                    operator=operator,
                    notes=notes,
                    kind=service_kind,
                    resolved=resolved,
                    raw_wording=raw_wording,
                    group_key=external_key if not resolved else "",
                    external_key=external_key,
                ))
    # A heading can carry the claimed total when the prose does not:
    # "## Completed / skipped service history (21 records)".
    if doc.reported_total is None:
        for line in text.splitlines():
            if not line.lstrip().startswith("#"):
                continue
            found = HEADING_TOTAL_RE.search(line)
            if found:
                doc.reported_total = int(found.group(1))
                break

    # The inventory can list more batteries than it can read. Keep the
    # unreadable remainder as identified placeholders so the inventory totals
    # what the capture says it has, and keep those placeholders out of
    # reminders. No serial, model or reading is invented for them.
    listed = BATTERY_LISTED_RE.search(text)
    readable = BATTERY_READABLE_RE.search(text)
    if listed:
        doc.battery_listed = int(listed.group(1))
    if readable:
        doc.battery_readable = int(readable.group(1))
    if doc.battery_listed is not None and doc.battery_listed > len(doc.batteries):
        unreadable = doc.battery_listed - len(doc.batteries)
        for index in range(unreadable):
            doc.batteries.append(EquipmentRow(
                kind="battery",
                name=f"Unreadable battery {index + 1} of {unreadable} (locked details)",
                locked=True, placeholder=True, status="unreadable",
                notes="Listed in the capture but not readable; kept out of reminders.",
            ))
        doc.placeholders_added = unreadable
        doc.warnings.append(
            f"{unreadable} of {doc.battery_listed} listed batteries are not readable; "
            f"added as identified placeholders so the inventory totals {doc.battery_listed}"
        )

    known_names = {
        row.name.strip().lower()
        for row in doc.current_aircraft + doc.historical_aircraft + doc.batteries
        if row.name.strip()
    }
    inventory = [
        (row.name.strip(), (row.serial or "").strip())
        for row in doc.current_aircraft + doc.historical_aircraft + doc.batteries
        if row.name.strip()
    ]
    for service in doc.services:
        names: list[str] = []
        for raw_name in service.equipment_names:
            low = raw_name.strip().lower()
            if low in known_names:
                names.append(raw_name)
                continue
            match = match_inventory(raw_name, inventory)
            if match and match.strip().lower() != low:
                names.append(match)
                doc.references_resolved += 1
            else:
                names.append(raw_name)
        service.equipment_names = names

    # Aircraft that appear only in the service history are real history, so
    # keep them as archived equipment instead of dropping or inventing them.
    referenced: dict[str, str] = {}
    for service in doc.services:
        if len(service.equipment_names) != 1:
            # A grouped row names several items at once; turning those names
            # into equipment would be invention, so the row stays grouped.
            continue
        raw_name = service.equipment_names[0]
        name = raw_name.strip()
        low = name.lower()
        if not low or low in known_names or _looks_like_serial(name):
            continue
        referenced.setdefault(low, name)
    for low, name in referenced.items():
        doc.historical_aircraft.append(EquipmentRow(
            kind="aircraft", name=name, archived=True, status="archived",
            notes="Service reference only; not present in the flight-log inventory.",
        ))
        known_names.add(low)
        doc.reference_only_aircraft += 1
    if doc.reference_only_aircraft:
        doc.warnings.append(
            f"{doc.reference_only_aircraft} aircraft appear only in the service history; "
            "kept as archived equipment and excluded from reminders"
        )
    loose = sum(
        1 for service in doc.services
        for name in service.equipment_names if _looks_like_serial(name)
    )
    if loose:
        doc.warnings.append(
            f"{loose} service rows reference a serial that is not in the inventory; "
            "kept unresolved"
        )

    if doc.reported_total is not None and doc.reported_total != len(doc.services):
        doc.warnings.append(
            f"capture reports {doc.reported_total} events; {len(doc.services)} distinguishable rows parsed"
        )
    if not doc.current_aircraft and not doc.batteries:
        doc.warnings.append("no equipment tables recognised")
    return doc


# ------------------------------------------------------------------ equipment

def _edited(conn: sqlite3.Connection, equipment_id: int) -> set[str]:
    row = db.one(conn, "SELECT edited_fields_json FROM equipment WHERE id=?", (equipment_id,))
    if row is None:
        return set()
    try:
        return set(json.loads(row["edited_fields_json"] or "[]"))
    except (TypeError, ValueError):
        return set()


def _upsert_equipment(conn: sqlite3.Connection, item: EquipmentRow) -> tuple[int, bool]:
    existing = None
    if item.serial:
        existing = db.one(
            conn,
            "SELECT * FROM equipment WHERE kind=? AND serial=? COLLATE NOCASE LIMIT 1",
            (item.kind, item.serial),
        )
    if existing is None:
        existing = db.one(
            conn,
            "SELECT * FROM equipment WHERE kind=? AND name=? COLLATE NOCASE LIMIT 1",
            (item.kind, item.name),
        )
    now = db.now_utc()
    if existing is None:
        parent_id = None
        if item.parent:
            prow = db.one(
                conn,
                "SELECT id FROM equipment WHERE kind='aircraft' AND name=? COLLATE NOCASE",
                (item.parent,),
            )
            parent_id = int(prow["id"]) if prow else None
        cur = conn.execute(
            """INSERT INTO equipment(kind,name,serial,archived,placeholder,parent_id,
                purchase_date,firmware,operational_state,notes,source_ref,locked_details,
                edited_fields_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item.kind, item.name, item.serial or None, int(item.archived),
                int(item.placeholder), parent_id, item.purchase or None,
                item.firmware or None, item.status or None, item.notes or None,
                "AirData equipment capture", int(item.locked), "[]", now, now,
            ),
        )
        equipment_id = int(cur.lastrowid)
        created = True
    else:
        equipment_id = int(existing["id"])
        edited = _edited(conn, equipment_id)
        updates: dict[str, Any] = {
            "archived": int(item.archived),
            "placeholder": int(item.placeholder),
            "locked_details": int(item.locked) if item.locked else int(existing["locked_details"]),
            "source_ref": existing["source_ref"] or "AirData equipment capture",
            "updated_at": now,
        }
        for field_name, value in (
            ("serial", item.serial), ("purchase_date", item.purchase),
            ("firmware", item.firmware), ("notes", item.notes),
            ("operational_state", item.status),
        ):
            if field_name in edited:
                continue
            current = existing[field_name]
            if value and not current:
                updates[field_name] = value
        if item.parent and not existing["parent_id"]:
            prow = db.one(
                conn,
                "SELECT id FROM equipment WHERE kind='aircraft' AND name=? COLLATE NOCASE",
                (item.parent,),
            )
            if prow:
                updates["parent_id"] = int(prow["id"])
        sets = ",".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE equipment SET {sets} WHERE id=?", (*updates.values(), equipment_id))
        created = False
    if item.serial:
        link = db.one(conn, "SELECT * FROM serial_links WHERE serial=?", (item.serial,))
        if link is None:
            conn.execute(
                "INSERT INTO serial_links(serial,equipment_id,source,override,updated_at) "
                "VALUES(?,?,?,0,?)",
                (item.serial, equipment_id, "capture", now),
            )
        elif not int(link["override"]):
            conn.execute(
                "UPDATE serial_links SET equipment_id=?,source='capture',updated_at=? WHERE serial=?",
                (equipment_id, now, item.serial),
            )
    return equipment_id, created


def _ensure_rule(
    conn: sqlite3.Connection,
    equipment_id: int,
    code: str,
    defaults: dict[str, Any],
    captured: SnapshotRow | None,
) -> int:
    row = db.one(conn, "SELECT * FROM rules WHERE equipment_id=? AND code=?",
                 (equipment_id, code))
    now = db.now_utc()
    limits = dict(defaults)
    if captured:
        for c in captured.counters:
            if c.get("limit"):
                if c["metric"] == "flights":
                    limits["flights"] = c["limit"]
                elif c["metric"] == "minutes":
                    limits["minutes"] = c["limit"]
                elif c["metric"] == "charges":
                    limits["charges"] = c["limit"]
    if row is None:
        cur = conn.execute(
            "INSERT INTO rules(equipment_id,code,label,enabled,due_soon_pct,"
            "flights_limit,air_minutes_limit,charges_limit,captured_status,"
            "captured_counter_text,captured_baseline_text,captured_baseline_at,"
            "created_at,updated_at) VALUES(?,?,?,1,0.80,?,?,?,?,?,?,?,?,?)",
            (
                equipment_id, code, RULE_LABELS[code],
                limits.get("flights"), limits.get("minutes"), limits.get("charges"),
                captured.status if captured else None,
                None,
                captured.baseline_text if captured else None,
                captured.baseline_text if captured else None,
                now, now,
            ),
        )
        rule_id = int(cur.lastrowid)
    else:
        rule_id = int(row["id"])
        # Apply captured limits to a rule that still holds its defaults. A rule
        # whose limits the owner edited keeps them: a capture restates policy,
        # it does not overrule the owner. The reviewed build never updated the
        # limits on the existing-row path, so a captured limit of 7 stayed 20.
        owner_limits = int(row["owner_limits"] or 0) if "owner_limits" in row.keys() else 0
        limit_sets = ""
        limit_values: list[Any] = []
        if captured and not owner_limits:
            for c in captured.counters:
                if not c.get("limit"):
                    continue
                column = {"flights": "flights_limit",
                          "minutes": "air_minutes_limit",
                          "charges": "charges_limit"}.get(c["metric"])
                if column is None or limits.get(c["metric"]) is None:
                    continue
                limit_sets += f",{column}=?"
                limit_values.append(limits[c["metric"]])
        conn.execute(
            "UPDATE rules SET captured_status=COALESCE(?,captured_status),"
            "captured_baseline_text=COALESCE(?,captured_baseline_text),"
            "captured_baseline_at=COALESCE(?,captured_baseline_at),updated_at=?"
            + limit_sets + " WHERE id=?",
            (
                captured.status if captured else None,
                captured.baseline_text if captured else None,
                captured.baseline_text if captured else None,
                now,
                *limit_values,
                rule_id,
            ),
        )
    return rule_id


def _find_equipment(conn: sqlite3.Connection, name: str, serial: str = "") -> sqlite3.Row | None:
    if serial:
        row = db.one(conn, "SELECT * FROM equipment WHERE serial=? COLLATE NOCASE LIMIT 1",
                     (serial,))
        if row:
            return row
    if name:
        return db.one(
            conn,
            "SELECT * FROM equipment WHERE name=? COLLATE NOCASE "
            "OR alias=? COLLATE NOCASE OR serial=? COLLATE NOCASE ORDER BY archived LIMIT 1",
            (name, name, name),
        )
    return None


def preview_capture(conn: sqlite3.Connection, doc: CaptureDoc) -> dict[str, Any]:
    existing_snapshot = db.one(conn, "SELECT id FROM service_snapshots WHERE source_hash=?",
                               (doc.text_hash,))
    equipment_new = 0
    for item in doc.current_aircraft + doc.historical_aircraft + doc.batteries:
        if _find_equipment(conn, item.name, item.serial) is None:
            equipment_new += 1
    service_rows = []
    for s in doc.services:
        service_rows.append({
            "date": s.occurred_at or s.date_raw,
            "equipment": s.equipment_names,
            "rules": s.rules,
            "kind": s.kind,
            "resolved": s.resolved,
            "counter": s.counter_text,
            "notes": s.notes,
        })
    return {
        "source_hash": doc.text_hash,
        "captured_at": doc.captured_at,
        "covered_through": doc.covered_through,
        "already_applied": existing_snapshot is not None,
        "current_aircraft": len(doc.current_aircraft),
        "historical_aircraft": len(doc.historical_aircraft),
        "reference_only_aircraft": doc.reference_only_aircraft,
        "batteries": len(doc.batteries),
        "battery_listed": doc.battery_listed,
        "battery_readable": doc.battery_readable,
        "placeholders": sum(1 for b in doc.batteries if b.placeholder),
        "placeholders_added": doc.placeholders_added,
        "equipment_new": equipment_new,
        "rules": sum(
            1
            for item in doc.current_aircraft
            for _ in ("basic", "extended", "full")
        ) + sum(1 for item in doc.batteries if not item.archived and not item.placeholder for _ in ("basic", "full")),
        "snapshot_counters": sum(len(row.counters) for row in doc.snapshots),
        "service_events": len(doc.services),
        "resolved_services": doc.resolved_services,
        "grouped_services": doc.grouped_services,
        "references_resolved": doc.references_resolved,
        "reported_total": doc.reported_total,
        "reporting_gap": doc.reporting_gap,
        "warnings": doc.warnings,
        "service_rows": service_rows,
    }


def apply_capture(conn: sqlite3.Connection, doc: CaptureDoc, source_label: str) -> dict[str, Any]:
    existing = db.one(conn, "SELECT id FROM service_snapshots WHERE source_hash=?",
                      (doc.text_hash,))
    if existing is not None:
        return {
            "already_applied": True,
            "snapshot_id": int(existing["id"]),
            "equipment_created": 0,
            "equipment_updated": 0,
            "rules_created": 0,
            "snapshot_counters": 0,
            "services_inserted": 0,
            "resolved_services": doc.resolved_services,
            "grouped_services": doc.grouped_services,
            "warnings": doc.warnings,
        }
    equipment_created = 0
    equipment_updated = 0
    rules_created = 0
    ids: dict[str, int] = {}
    with db.tx(conn):
        for item in doc.current_aircraft + doc.historical_aircraft + doc.batteries:
            eq_id, created = _upsert_equipment(conn, item)
            ids[item.name.lower()] = eq_id
            equipment_created += int(created)
            equipment_updated += int(not created)
        for item in doc.current_aircraft:
            eq_id = ids[item.name.lower()]
            for code in ("basic", "extended", "full"):
                before = db.one(conn, "SELECT id FROM rules WHERE equipment_id=? AND code=?",
                                (eq_id, code))
                _ensure_rule(conn, eq_id, code, AIRCRAFT_DEFAULTS[code], None)
                rules_created += int(before is None)
        for item in doc.batteries:
            if item.archived or item.placeholder:
                continue
            eq_id = ids[item.name.lower()]
            for code in ("basic", "full"):
                before = db.one(conn, "SELECT id FROM rules WHERE equipment_id=? AND code=?",
                                (eq_id, code))
                _ensure_rule(conn, eq_id, code, BATTERY_DEFAULTS[code], None)
                rules_created += int(before is None)

        # A capture applied after an import must associate the same flights
        # that an import-after-capture would have associated.
        conn.execute(
            "UPDATE flights SET aircraft_equipment_id=(SELECT equipment_id FROM "
            "serial_links WHERE serial=flights.aircraft_sn) "
            "WHERE aircraft_equipment_id IS NULL AND aircraft_sn IS NOT NULL "
            "AND EXISTS(SELECT 1 FROM serial_links WHERE serial=flights.aircraft_sn)"
        )
        conn.execute(
            "UPDATE flights SET battery_equipment_id=(SELECT equipment_id FROM "
            "serial_links WHERE serial=flights.battery_sn) "
            "WHERE battery_equipment_id IS NULL AND battery_sn IS NOT NULL "
            "AND EXISTS(SELECT 1 FROM serial_links WHERE serial=flights.battery_sn)"
        )

        # An import that ran before this capture already stored its flights but
        # could not link them to batteries, so their readings are missing.
        # Backfill them here so import-then-capture and capture-then-import
        # leave the same readings behind.
        conn.execute(
            "INSERT OR IGNORE INTO battery_readings(battery_id,flight_id,at,charge_pct,"
            "temp_c,cycles,voltage,source,temp_missing) "
            "SELECT f.battery_equipment_id, f.id, f.start_time_utc, f.battery_start_pct, "
            "f.battery_temp_max_c, NULL, NULL, 'log', 0 FROM flights f "
            "WHERE f.battery_equipment_id IS NOT NULL AND f.battery_start_pct IS NOT NULL "
            "AND NOT EXISTS(SELECT 1 FROM battery_readings b WHERE b.flight_id=f.id)"
        )

        # snapshot header
        cur = conn.execute(
            "INSERT INTO service_snapshots(source_label,source_hash,captured_at,"
            "covered_through,source_kind,notes,created_at) VALUES(?,?,?,?,?,?,?)",
            (source_label, doc.text_hash, doc.captured_at, doc.covered_through,
             "capture", "AirData equipment capture", db.now_utc()),
        )
        snapshot_id = int(cur.lastrowid)
        counters_inserted = 0
        for row in doc.snapshots:
            equipment = _find_equipment(conn, row.equipment_name, row.serial)
            if equipment is None:
                doc.warnings.append(f"snapshot row references unknown equipment: {row.equipment_name}")
                continue
            # attach captured status/baseline to the rule, and its counters
            rule = db.one(conn, "SELECT * FROM rules WHERE equipment_id=? AND code=?",
                          (equipment["id"], row.rule))
            if rule is None:
                defaults = (AIRCRAFT_DEFAULTS if equipment["kind"] == "aircraft"
                            else BATTERY_DEFAULTS).get(row.rule)
                if not defaults:
                    continue
                _ensure_rule(conn, int(equipment["id"]), row.rule, defaults, row)
                rules_created += 1
                created_rule = db.one(
                    conn, "SELECT id FROM rules WHERE equipment_id=? AND code=?",
                    (equipment["id"], row.rule),
                )
                rule_id = int(created_rule["id"]) if created_rule else None
                if rule_id is None:
                    continue
            else:
                _ensure_rule(conn, int(equipment["id"]), row.rule,
                             AIRCRAFT_DEFAULTS.get(row.rule) or BATTERY_DEFAULTS.get(row.rule, {}),
                             row)
                rule_id = int(rule["id"])
            conn.execute(
                "UPDATE rules SET captured_status=?,captured_counter_text=?,"
                "captured_baseline_text=?,captured_baseline_at=?,updated_at=? WHERE id=?",
                (row.status or None, "; ".join(c["raw"] for c in row.counters),
                 row.baseline_text or None, row.baseline_text or None, db.now_utc(), rule_id),
            )
            for c in row.counters:
                conn.execute(
                    "INSERT OR IGNORE INTO snapshot_counters(snapshot_id,equipment_id,"
                    "rule_code,metric,value,unit,rounded,raw_text,covered_through) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (snapshot_id, int(equipment["id"]), row.rule, c["metric"],
                     c["value"], c["unit"], c["rounded"], c["raw"], doc.covered_through),
                )
                counters_inserted += 1

        # Battery temperatures captured with the inventory. A 32°F cell is
        # only a placeholder when this capture says so; a genuine freezing
        # reading in another capture remains a reading.
        for item in doc.batteries:
            if not item.temperature:
                continue
            eq = _find_equipment(conn, item.name, item.serial)
            if eq is None:
                continue
            temp_c = _temp_c(item.temperature)
            missing = bool(
                doc.temperature_placeholder
                and temp_c is not None
                and abs(temp_c - 0.0) < 0.01
            )
            exists = db.one(
                conn,
                "SELECT id FROM battery_readings WHERE battery_id=? AND source='capture' AND at=?",
                (eq["id"], doc.captured_at),
            )
            if exists is None:
                conn.execute(
                    "INSERT INTO battery_readings(battery_id,flight_id,at,charge_pct,temp_c,"
                    "cycles,voltage,source,temp_missing) VALUES(?,NULL,?,NULL,?,NULL,NULL,"
                    "'capture',?)",
                    (eq["id"], doc.captured_at, None if missing else temp_c, int(missing)),
                )

        services_inserted = 0
        for s in doc.services:
            eq_id = None
            if len(s.equipment_names) == 1:
                eq = _find_equipment(conn, s.equipment_names[0])
                eq_id = int(eq["id"]) if eq else None
            rule_id = None
            if s.resolved and eq_id and len(s.rules) == 1:
                rule = db.one(conn, "SELECT id FROM rules WHERE equipment_id=? AND code=?",
                              (eq_id, s.rules[0]))
                rule_id = int(rule["id"]) if rule else None
            linked_rule_id = None
            if s.kind == "oneoff" and eq_id and len(s.rules) == 1:
                rule = db.one(conn, "SELECT id FROM rules WHERE equipment_id=? AND code=?",
                              (eq_id, s.rules[0]))
                linked_rule_id = int(rule["id"]) if rule else None
            cur = conn.execute(
                """INSERT OR IGNORE INTO services(equipment_id,rule_id,kind,occurred_at,
                    occurred_precision,ended_at,notes,source,resolved,external_key,group_key,
                    raw_wording,time_range_text,counter_text,operator,linked_rule_id,
                    provenance_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    eq_id, rule_id, s.kind, s.occurred_at, "date", s.ended_at,
                    s.notes or None, "capture", int(s.resolved), s.external_key,
                    s.group_key or None, s.raw_wording, s.date_raw, s.counter_text,
                    s.operator or None, linked_rule_id,
                    json.dumps({"capture": source_label, "row": s.raw_wording},
                               separators=(",", ":")),
                    db.now_utc(),
                ),
            )
            services_inserted += int(cur.rowcount or 0)
        db.audit(conn, "local", "capture_apply", "service_snapshot", str(snapshot_id),
                 {"services": len(doc.services), "inserted": services_inserted})
    return {
        "already_applied": False,
        "snapshot_id": snapshot_id,
        "equipment_created": equipment_created,
        "equipment_updated": equipment_updated,
        "rules_created": rules_created,
        "snapshot_counters": counters_inserted,
        "services_inserted": services_inserted,
        "resolved_services": doc.resolved_services,
        "grouped_services": doc.grouped_services,
        "warnings": doc.warnings,
    }


def owner_edit_equipment(conn: sqlite3.Connection, equipment_id: int,
                         fields: dict[str, Any]) -> dict[str, Any]:
    allowed = {"alias", "purchase_date", "firmware", "operational_state", "notes", "serial"}
    row = db.one(conn, "SELECT * FROM equipment WHERE id=?", (equipment_id,))
    if row is None:
        return {"ok": False, "code": "not_found"}
    edited = _edited(conn, equipment_id)
    sets = []
    values = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key}=?")
        values.append(value)
        edited.add(key)
    if not sets:
        return {"ok": False, "code": "no_editable_fields"}
    sets.append("edited_fields_json=?")
    values.append(json.dumps(sorted(edited)))
    sets.append("updated_at=?")
    values.append(db.now_utc())
    values.append(equipment_id)
    with db.tx(conn):
        conn.execute(f"UPDATE equipment SET {', '.join(sets)} WHERE id=?", values)
        db.audit(conn, "owner", "equipment_edit", "equipment", str(equipment_id),
                 {"fields": list(fields.keys())})
    return {"ok": True, "edited_fields": sorted(edited)}


def assign_serial(conn: sqlite3.Connection, serial: str, equipment_id: int,
                  *, override: bool = True) -> dict[str, Any]:
    eq = db.one(conn, "SELECT * FROM equipment WHERE id=?", (equipment_id,))
    if eq is None or not serial:
        return {"ok": False, "code": "not_found"}
    now = db.now_utc()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO serial_links(serial,equipment_id,source,override,updated_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(serial) DO UPDATE SET equipment_id=excluded.equipment_id,"
            "source='manual',override=excluded.override,updated_at=excluded.updated_at",
            (serial, equipment_id, "manual", int(override), now),
        )
        if eq["kind"] == "aircraft":
            conn.execute("UPDATE flights SET aircraft_equipment_id=?,updated_at=? "
                         "WHERE aircraft_sn=?", (equipment_id, now, serial))
        elif eq["kind"] == "battery":
            conn.execute("UPDATE flights SET battery_equipment_id=?,updated_at=? "
                         "WHERE battery_sn=?", (equipment_id, now, serial))
        db.audit(conn, "owner", "serial_assign", "equipment", str(equipment_id),
                 {"serial": serial, "override": override})
    return {"ok": True}


def refresh_log_battery_reading(conn: sqlite3.Connection, flight_id: int) -> dict[str, Any]:
    """Rebuild the log-derived battery reading for one flight from its columns.

    Only `source='log'` rows belong to a parse. A manual or capture reading is
    left untouched. Called after an accepted reparse and after a battery
    reassignment, so the equipment history moves with the flight instead of
    pointing at the battery it used to carry.
    """
    flight = db.one(
        conn,
        "SELECT id,battery_equipment_id,start_time_utc,battery_start_pct,"
        "battery_temp_max_c FROM flights WHERE id=?",
        (flight_id,),
    )
    if flight is None:
        return {"ok": False, "code": "not_found"}
    conn.execute(
        "DELETE FROM battery_readings WHERE flight_id=? AND source='log'", (flight_id,)
    )
    battery_id = flight["battery_equipment_id"]
    if not battery_id or flight["battery_start_pct"] is None:
        return {"ok": True, "written": False}
    conn.execute(
        "INSERT OR IGNORE INTO battery_readings"
        "(battery_id,flight_id,at,charge_pct,temp_c,cycles,voltage,source,temp_missing) "
        "VALUES(?,?,?,?,?,NULL,NULL,'log',0)",
        (int(battery_id), flight_id, flight["start_time_utc"],
         flight["battery_start_pct"], flight["battery_temp_max_c"]),
    )
    return {"ok": True, "written": True}


def reassign_flight(conn: sqlite3.Connection, flight_id: int, kind: str,
                    equipment_id: int | None) -> dict[str, Any]:
    if kind not in ("aircraft", "battery"):
        return {"ok": False, "code": "bad_kind"}
    # A Boolean is an int in Python; True must not be read as equipment id 1.
    if equipment_id is not None and (isinstance(equipment_id, bool)
                                     or not isinstance(equipment_id, int)):
        return {"ok": False, "code": "bad_equipment_id"}
    row = db.one(conn, "SELECT id FROM flights WHERE id=?", (flight_id,))
    if row is None:
        return {"ok": False, "code": "not_found"}
    if equipment_id is not None:
        equipment = db.one(conn, "SELECT id,kind FROM equipment WHERE id=?", (equipment_id,))
        if equipment is None:
            return {"ok": False, "code": "equipment_not_found"}
        # A generic foreign key would happily accept a battery as the aircraft.
        if equipment["kind"] != kind:
            return {"ok": False, "code": "wrong_equipment_kind"}
    column = "aircraft_equipment_id" if kind == "aircraft" else "battery_equipment_id"
    with db.tx(conn):
        conn.execute(f"UPDATE flights SET {column}=?,updated_at=? WHERE id=?",
                     (equipment_id, db.now_utc(), flight_id))
        if kind == "battery":
            # Clearing a battery, or moving to another one, must move the
            # log-derived history with it — and only that history.
            refresh_log_battery_reading(conn, flight_id)
        db.audit(conn, "owner", "flight_reassign", "flight", str(flight_id),
                 {"kind": kind, "equipment_id": equipment_id})
    return {"ok": True}
