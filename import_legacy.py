"""Read-only importer for a 3.x .decades-save into the new v4 database."""
from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import ChronicleSave, Membership, Portrait, Record, User


TABLE_KINDS = {
    "sims": "sim", "households": "household", "pregnancies": "pregnancy",
    "rolls": "roll", "relationships": "relationship", "events": "event",
    "event_results": "event_result", "notebook_entries": "note",
    "illnesses": "illness", "era_guidance": "era_rule",
    "military_campaigns": "campaign", "military_service": "service",
    "event_rule_configs": "event_rule", "action_queue": "task",
    "death_cause_pools": "death_causes", "game_birth_candidates": "detection_candidate",
    "game_pregnancy_candidates": "detection_candidate", "play_rotation": "play_rotation",
    "sim_family_plans": "family_plan", "planner_rules": "planner_rule",
    "roll_rule_eras": "roll_rule_era", "roll_rule_values": "roll_rule",
    "rules": "source_archive", "calendar_rows": "source_archive",
    "raw_import_rows": "source_archive", "maintenance_jobs": "source_archive",
    "game_pregnancy_states": "source_archive",
}

PRIMARY_KEYS = {
    "sims": "sim_id", "households": "household_id", "pregnancies": "pregnancy_id",
    "rolls": "roll_id", "relationships": "relationship_id", "events": "event_id",
    "event_results": "result_id", "notebook_entries": "note_id", "illnesses": "illness_id",
    "era_guidance": "rule_id", "military_campaigns": "campaign_id",
    "military_service": "service_id", "event_rule_configs": "event_id",
    "action_queue": "action_id", "game_birth_candidates": "detection_id",
    "game_pregnancy_candidates": "detection_id", "play_rotation": "rotation_id",
}

LABEL_FIELDS = {
    "sims": ("title", "first_name", "last_name", "suffix"),
    "households": ("household_name",), "pregnancies": ("mother_name", "father_name"),
    "rolls": ("sim_name", "roll_type"), "relationships": ("partner1_name", "partner2_name"),
    "events": ("event_name",), "notebook_entries": ("title",), "illnesses": ("sim_name", "illness_name"),
    "era_guidance": ("title",), "military_campaigns": ("name",), "military_service": ("sim_name", "role"),
    "roll_rule_eras": ("era_name",), "roll_rule_values": ("roll_type",), "death_cause_pools": ("death_group", "cause"),
}

DAY_FIELDS = {
    "sims": "birth_global_day", "pregnancies": "due_global_day", "rolls": "due_global_day",
    "relationships": "start_global_day", "events": "start_global_day",
    "event_results": "global_day", "illnesses": "onset_global_day",
    "military_campaigns": "start_global_day", "military_service": "enlisted_global_day",
    "action_queue": "due_global_day", "play_rotation": "global_day",
}


def read_database(package: Path) -> tuple[Path, tempfile.TemporaryDirectory]:
    temporary = tempfile.TemporaryDirectory()
    target = Path(temporary.name) / "legacy.db"
    if zipfile.is_zipfile(package):
        with zipfile.ZipFile(package, "r") as archive:
            if "save.db" not in archive.namelist(): raise ValueError("Archive has no save.db")
            target.write_bytes(archive.read("save.db"))
    else:
        target.write_bytes(package.read_bytes())
    return target, temporary


def value(row: sqlite3.Row, key: str, default=None):
    return row[key] if key in row.keys() else default


def json_safe(item):
    if isinstance(item, bytes): return {"legacy_blob_bytes": len(item)}
    return item


def label(table: str, row: sqlite3.Row) -> str:
    parts = [str(value(row, field, "") or "").strip() for field in LABEL_FIELDS.get(table, ())]
    result = " — ".join(part for part in parts if part)
    return result or f"Imported {table.replace('_', ' ')}"


def remap(value_, id_map):
    if isinstance(value_, dict):
        result = {}
        for key, item in value_.items():
            if key.endswith("_id") and isinstance(item, str): result[key] = id_map.get(item, item)
            elif key == "spouse_ids" and isinstance(item, str): result[key] = ",".join(id_map.get(part.strip(), part.strip()) for part in item.split(",") if part.strip())
            else: result[key] = remap(item, id_map)
        return result
    if isinstance(value_, list): return [remap(item, id_map) for item in value_]
    return value_


def import_package(package: Path, save_name: str | None = None) -> dict:
    Base.metadata.create_all(engine)
    database, temporary = read_database(package)
    try:
        source = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        if source.execute("PRAGMA quick_check").fetchone()[0].casefold() != "ok": raise ValueError("Legacy database failed integrity check")
        tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        with SessionLocal() as target:
            user = target.scalar(select(User).where(User.email == "local@decades.invalid"))
            if not user: raise RuntimeError("Start v4 once before importing")
            membership = target.scalar(select(Membership).where(Membership.user_id == user.id))
            settings = {row["key"]: row["value"] for row in source.execute("SELECT key,value FROM settings")} if "settings" in tables else {}
            save = ChronicleSave(
                workspace_id=membership.workspace_id,
                name=save_name or f"Elizabethan Start · imported {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                global_day=int(float(settings.get("current_global_day", 1))),
                start_year=int(float(settings.get("start_year", settings.get("challenge_start_year", 1300)))),
                days_per_year=int(float(settings.get("days_per_year", 28))),
                pregnancy_days=int(float(settings.get("pregnancy_length_days", 3))),
                settings={"legacy_settings": settings, "import_source": package.name},
            )
            target.add(save); target.flush()
            id_map, pending, counts = {}, [], {}
            for table, kind in TABLE_KINDS.items():
                if table not in tables: continue
                rows = list(source.execute(f'SELECT * FROM "{table}"'))
                counts[table] = len(rows)
                for index, row in enumerate(rows):
                    legacy_id = str(value(row, PRIMARY_KEYS.get(table, ""), "") or f"{table}:{index}")
                    new_id = uuid4().hex
                    id_map[legacy_id] = new_id
                    payload = {key: json_safe(row[key]) for key in row.keys()}
                    payload.update({"legacy_id": legacy_id, "legacy_table": table})
                    record = Record(id=new_id, save_id=save.id, kind=kind, label=label(table, row), global_day=value(row, DAY_FIELDS.get(table, "")), data=payload)
                    target.add(record); pending.append(record)
            target.flush()
            for record in pending: record.data = remap(record.data, id_map)
            photo_counts = {}
            photo_specs = (("sim_photos", "sim_id", "default"), ("sim_lifestage_photos", "sim_id", None), ("relationship_photos", "relationship_id", "marriage"))
            for table, key, fixed_stage in photo_specs:
                if table not in tables: continue
                imported = 0
                for row in source.execute(f'SELECT * FROM "{table}"'):
                    record_id = id_map.get(str(row[key]))
                    if not record_id: continue
                    stage = fixed_stage or str(value(row, "life_stage", "default"))
                    target.add(Portrait(save_id=save.id, record_id=record_id, stage=stage, mime_type=value(row, "mime_type", "image/jpeg"), image=bytes(row["image_data"]), source="legacy-import"))
                    imported += 1
                photo_counts[table] = imported
            save.settings = {**save.settings, "legacy_id_map": id_map}
            save.revision = len(pending)
            target.commit()
            result = {"save_id": save.id, "name": save.name, "records": len(pending), "tables": counts, "photos": photo_counts}
        source.close()
        return result
    finally:
        temporary.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--name")
    args = parser.parse_args()
    print(json.dumps(import_package(args.package.resolve(), args.name), indent=2))


if __name__ == "__main__": main()
