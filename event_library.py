from __future__ import annotations

import base64
import gzip
import json

from event_library_data import EVENT_LIBRARY_GZIP_BASE64

SEED_SETTING = "event_library_seeded_v2"
REFERENCE_START_YEAR = 1200
DAYS_PER_YEAR = 4
EXPECTED_EVENT_COUNT = 655
EVENT_COLUMNS = [
    "event_id", "start_global_day", "end_global_day", "event_name", "scope",
    "location", "roll_required", "affected_class", "active", "source", "notes",
]


def library_rows():
    compressed = base64.b64decode(EVENT_LIBRARY_GZIP_BASE64)
    rows = json.loads(gzip.decompress(compressed).decode("utf-8"))
    if len(rows) != EXPECTED_EVENT_COUNT:
        raise RuntimeError(f"Historical event library is incomplete: expected {EXPECTED_EVENT_COUNT}, found {len(rows)}.")
    return rows


def ensure_event_library(con):
    """Seed the recovered catalog once per save while preserving custom events."""
    seeded = con.execute("SELECT value FROM settings WHERE key=?", (SEED_SETTING,)).fetchone()
    if seeded and str(seeded[0]) == "1":
        return 0

    start_setting = con.execute("SELECT value FROM settings WHERE key=?", ("start_year",)).fetchone()
    save_start = int(float(start_setting[0])) if start_setting and start_setting[0] not in (None, "") else REFERENCE_START_YEAR

    def rebase(global_day):
        if global_day is None:
            return None
        value = int(global_day)
        absolute_year = REFERENCE_START_YEAR + (value - 1) // DAYS_PER_YEAR
        challenge_day = ((value - 1) % DAYS_PER_YEAR) + 1
        return (absolute_year - save_start) * DAYS_PER_YEAR + challenge_day

    placeholders = ",".join("?" for _ in EVENT_COLUMNS)
    added = 0
    for original in library_rows():
        row = dict(original)
        row["start_global_day"] = rebase(row.get("start_global_day"))
        row["end_global_day"] = rebase(row.get("end_global_day"))
        cursor = con.execute(
            f"INSERT OR IGNORE INTO events({','.join(EVENT_COLUMNS)}) VALUES({placeholders})",
            tuple(row.get(column) for column in EVENT_COLUMNS),
        )
        if cursor.rowcount and cursor.rowcount > 0:
            added += cursor.rowcount
    con.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (SEED_SETTING, "1"),
    )
    con.commit()
    return added
