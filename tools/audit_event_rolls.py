"""Read-only audit of historical-event rolls in a local Decades Tracker database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def data(value: str) -> dict:
    try:
        return json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--save", default="")
    args = parser.parse_args()
    connection = sqlite3.connect(f"file:{args.database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    if args.save:
        save = connection.execute(
            "SELECT * FROM saves WHERE id = ? OR name = ? ORDER BY updated_at DESC LIMIT 1",
            (args.save, args.save),
        ).fetchone()
    else:
        save = connection.execute("SELECT * FROM saves ORDER BY updated_at DESC LIMIT 1").fetchone()
    if not save:
        raise SystemExit("No matching save was found.")

    sims = []
    for row in connection.execute(
        "SELECT * FROM records WHERE save_id = ? AND kind = 'sim' AND deleted = 0",
        (save["id"],),
    ):
        item = data(row["data"])
        sims.append(
            {
                "id": row["id"],
                "label": row["label"],
                "birth": item.get("birth_global_day", row["global_day"]),
                "death": item.get("death_global_day"),
                "game_dead": bool(item.get("game_was_dead")),
                "country": item.get("country"),
                "location": item.get("location"),
                "world": item.get("world") or item.get("last_game_world"),
            }
        )

    rolls_by_source: dict[str, list[dict]] = {}
    for row in connection.execute(
        "SELECT * FROM records WHERE save_id = ? AND kind = 'roll' AND deleted = 0",
        (save["id"],),
    ):
        item = data(row["data"])
        source = str(item.get("source") or "")
        if source.startswith("event:"):
            rolls_by_source.setdefault(source, []).append(
                {"id": row["id"], "label": row["label"], "day": row["global_day"], "completed": item.get("completed")}
            )

    households = []
    for row in connection.execute(
        "SELECT * FROM records WHERE save_id = ? AND kind = 'household' AND deleted = 0",
        (save["id"],),
    ):
        item = data(row["data"])
        households.append(
            {
                "id": row["id"],
                "label": row["label"],
                "country": item.get("country"),
                "location": item.get("location"),
                "world": item.get("world"),
                "member_ids": item.get("member_ids"),
            }
        )

    events = []
    for row in connection.execute(
        "SELECT * FROM records WHERE save_id = ? AND kind = 'event' AND deleted = 0 ORDER BY global_day, label",
        (save["id"],),
    ):
        item = data(row["data"])
        due = item.get("start_global_day", row["global_day"])
        if not item.get("roll_required") or due is None or int(due) < 1 or int(due) > int(save["global_day"]):
            continue
        sources = {
            source: records
            for source, records in rolls_by_source.items()
            if source.startswith(f"event:{row['id']}:")
        }
        events.append(
            {
                "id": row["id"],
                "label": row["label"],
                "record_day": row["global_day"],
                "due": due,
                "scope": item.get("scope"),
                "location": item.get("location"),
                "active": item.get("active", True),
                "ignored": item.get("ignored", False),
                "roll_count": sum(len(records) for records in sources.values()),
            }
        )

    print(
        json.dumps(
            {
                "save": {
                    "id": save["id"],
                    "name": save["name"],
                    "global_day": save["global_day"],
                    "start_year": save["start_year"],
                    "days_per_year": save["days_per_year"],
                },
                "sim_count": len(sims),
                "living_sims": [sim for sim in sims if not sim["game_dead"] and (sim["death"] is None or int(sim["death"]) > int(save["global_day"]))],
                "households": households,
                "reached_roll_events": events,
                "event_roll_total": sum(len(records) for records in rolls_by_source.values()),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
