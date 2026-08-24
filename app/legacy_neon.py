"""Safe, idempotent bridge from the 3.x Neon schemas to the v4 record model.

Legacy schemas are treated as immutable source material.  Imports create or fill
v4 saves, preserve source identities, and never delete or update legacy rows.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterator

from sqlalchemy import MetaData, Table, create_engine, inspect, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from . import domain, sync
from .models import Change, ChronicleSave, ClockLink, LegacyWorkspaceCode, Membership, Portrait, Record, User, Workspace


TABLE_KINDS = {
    "sims": "sim", "households": "household", "pregnancies": "pregnancy",
    "rolls": "roll", "relationships": "relationship", "events": "event",
    "event_results": "event_result", "notebook_entries": "note",
    "illnesses": "illness", "era_guidance": "era_guidance",
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
    "sims": ("sim_id",), "households": ("household_id",),
    "pregnancies": ("pregnancy_id",), "rolls": ("roll_id",),
    "relationships": ("relationship_id",), "events": ("event_id",),
    "event_results": ("result_id",), "notebook_entries": ("note_id",),
    "illnesses": ("illness_id",), "era_guidance": ("rule_id",),
    "military_campaigns": ("campaign_id",), "military_service": ("service_id",),
    "event_rule_configs": ("event_id",), "action_queue": ("action_id",),
    "game_birth_candidates": ("detection_id",),
    "game_pregnancy_candidates": ("detection_id",),
    "play_rotation": ("rotation_id",), "roll_rule_eras": ("era_id",),
    "roll_rule_values": ("era_id", "roll_type"),
    "death_cause_pools": ("death_group", "cause"),
    "game_pregnancy_states": ("game_sim_id",),
    "sim_family_plans": ("sim_id",),
    "planner_rules": ("rule_key", "start_year", "end_year"),
}

DAY_FIELDS = {
    "sims": "birth_global_day", "pregnancies": "due_global_day",
    "rolls": "due_global_day", "relationships": "start_global_day",
    "events": "start_global_day", "event_results": "global_day",
    "illnesses": "onset_global_day", "military_campaigns": "start_global_day",
    "military_service": "enlisted_global_day", "action_queue": "due_global_day",
    "play_rotation": "global_day", "game_birth_candidates": "birth_global_day",
    "game_pregnancy_candidates": "due_global_day",
}

PHOTO_SPECS = {
    "sim_photos": ("sim_id", "default"),
    "sim_lifestage_photos": ("sim_id", None),
    "relationship_photos": ("relationship_id", "marriage"),
}

# IDs from these source tables are genuine cross-table references.  Config and
# one-per-Sim tables such as event_rule_configs/sim_family_plans deliberately
# receive their own v4 record ID while their foreign key still maps to the
# referenced event or Sim.
REFERENCE_ENTITY_TABLES = {
    "sims", "households", "pregnancies", "rolls", "relationships", "events",
    "event_results", "notebook_entries", "illnesses", "era_guidance",
    "military_campaigns", "military_service", "action_queue",
    "game_birth_candidates", "game_pregnancy_candidates", "play_rotation",
    "roll_rule_eras",
}

BOOL_FIELDS = {
    "active", "completed", "include_in_tree", "legitimate", "roll_required",
    "legally_married", "contagious", "maternal_rolls_required",
    "birth_newborn_rolls_required", "newborn_rolls_required", "pinned",
    "is_baby", "was_pregnant",
}


def stable_id(*parts: object) -> str:
    return hashlib.sha256(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:32]


def _safe(value):
    if isinstance(value, bytes):
        return {"legacy_blob_bytes": len(value)}
    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return value


def _truth(value) -> bool:
    return value is True or str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _integer(value, default: int | None = None) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _json_text(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def legacy_key(table: str, row: dict, index: int = 0) -> str:
    values = [str(row.get(field) if row.get(field) is not None else "") for field in PRIMARY_KEYS.get(table, ())]
    if values and any(values):
        return "|".join(values)
    canonical = json.dumps(_safe(row), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"row-{index}-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def record_label(table: str, row: dict) -> str:
    if table == "sims":
        value = " ".join(str(row.get(key) or "").strip() for key in ("title", "first_name", "last_name", "suffix")).strip()
    elif table == "households": value = str(row.get("household_name") or "").strip()
    elif table == "pregnancies": value = " — ".join(filter(None, (str(row.get("mother_name") or "").strip(), str(row.get("father_name") or "").strip())))
    elif table == "rolls": value = " — ".join(filter(None, (str(row.get("sim_name") or "").strip(), str(row.get("roll_type") or "").strip())))
    elif table == "relationships": value = " & ".join(filter(None, (str(row.get("partner1_name") or "").strip(), str(row.get("partner2_name") or "").strip())))
    elif table == "events": value = str(row.get("event_name") or "").strip()
    elif table == "notebook_entries": value = str(row.get("title") or "").strip()
    elif table == "illnesses": value = " — ".join(filter(None, (str(row.get("sim_name") or "").strip(), str(row.get("illness_name") or "").strip())))
    elif table == "era_guidance": value = str(row.get("title") or "").strip()
    elif table == "military_campaigns": value = str(row.get("name") or "").strip()
    elif table == "military_service": value = " — ".join(filter(None, (str(row.get("sim_name") or "").strip(), str(row.get("role") or "").strip())))
    elif table == "roll_rule_eras": value = str(row.get("era_name") or "").strip()
    elif table == "roll_rule_values": value = str(row.get("roll_type") or "").strip()
    elif table == "death_cause_pools": value = str(row.get("death_group") or "Death causes").strip()
    elif table == "game_birth_candidates": value = " ".join(filter(None, (str(row.get("first_name") or "").strip(), str(row.get("last_name") or "").strip())))
    elif table == "game_pregnancy_candidates": value = " ".join(filter(None, (str(row.get("first_name") or "").strip(), str(row.get("last_name") or "").strip()))) + " pregnancy"
    elif table == "planner_rules": value = str(row.get("rule_key") or "").strip()
    else: value = ""
    return value or f"Imported {table.replace('_', ' ')}"


def normalize_payload(table: str, row: dict, source_key: str) -> dict:
    payload = {str(key): _safe(value) for key, value in row.items() if key != "image_data"}
    for field in BOOL_FIELDS & set(payload):
        payload[field] = _truth(payload[field])
    for field in ("payload_json", "effects_json"):
        parsed = _json_text(payload.get(field))
        if parsed is not None:
            payload[field.removesuffix("_json")] = _safe(parsed)
    if table == "rolls":
        actual = _integer(payload.get("actual_roll"))
        if actual is not None:
            payload["actual"] = actual
        payload["completed"] = _truth(payload.get("completed"))
    elif table == "pregnancies":
        if "birth_newborn_rolls_required" not in payload and "newborn_rolls_required" in payload:
            payload["birth_newborn_rolls_required"] = _truth(payload.get("newborn_rolls_required"))
        status = str(payload.get("status") or "Active")
        if status.casefold() in {"delivered", "complete", "completed"} and payload.get("delivery_global_day") is None:
            payload["delivery_global_day"] = payload.get("due_global_day")
    elif table == "events":
        payload["catalog_id"] = str(payload.get("event_id") or source_key)
    elif table == "event_rule_configs":
        payload["catalog_id"] = str(payload.get("event_id") or source_key)
    elif table in {"game_birth_candidates", "game_pregnancy_candidates"}:
        payload.setdefault("status", "resolved")
        payload["legacy_detection_type"] = "birth" if table == "game_birth_candidates" else "pregnancy"
    payload.update({"legacy_id": source_key, "legacy_table": table, "legacy_neon": True})
    return payload


def remap_payload(value, id_map: dict[str, str]):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"legacy_id", "catalog_id", "game_sim_id", "source_key", "rule_key"}:
                result[key] = remap_payload(item, id_map) if isinstance(item, (dict, list)) else item
            elif key.endswith("_id") and isinstance(item, str):
                result[key] = id_map.get(item, item)
            elif key == "spouse_ids" and isinstance(item, str):
                result[key] = ",".join(id_map.get(part.strip(), part.strip()) for part in item.split(",") if part.strip())
            else:
                result[key] = remap_payload(item, id_map)
        return result
    if isinstance(value, list):
        return [remap_payload(item, id_map) for item in value]
    return value


def _postgres_url(value: str) -> str:
    url = str(value or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("Enter a PostgreSQL Neon connection string.")
    if "sslmode=" not in url.casefold():
        raise ValueError("The Neon connection must require SSL.")
    return url


@contextmanager
def source_connection(target: Session, source_url: str = "") -> Iterator[Connection]:
    temporary: Engine | None = None
    if source_url:
        temporary = create_engine(_postgres_url(source_url), pool_pre_ping=True)
        bind = temporary
    else:
        bind = target.get_bind()
        if bind.dialect.name != "postgresql":
            raise ValueError("The desktop needs a Neon connection string for this one-time import.")
    try:
        with bind.connect() as connection:
            yield connection
    finally:
        if temporary is not None:
            temporary.dispose()


def _table(connection: Connection, name: str, schema: str = "public") -> Table:
    return Table(name, MetaData(), schema=schema, autoload_with=connection)


def _legacy_tables(connection: Connection, schema_name: str) -> set[str]:
    if not re.fullmatch(r"decades_save_[0-9a-f]+", schema_name):
        raise ValueError("The legacy save registry contains an invalid schema name.")
    return set(inspect(connection).get_table_names(schema=schema_name))


def owner_saves(target: Session, owner_hash: str, source_url: str = "") -> list[dict]:
    with source_connection(target, source_url) as connection:
        if "decades_saves" not in inspect(connection).get_table_names(schema="public"):
            return []
        registry = _table(connection, "decades_saves")
        rows = connection.execute(select(registry).where(registry.c.owner_hash == owner_hash).order_by(registry.c.updated_at.desc())).mappings()
        return [{str(key): _safe(value) for key, value in dict(row).items()} for row in rows]


def email_owner_hash(target: Session, email: str, source_url: str = "") -> str | None:
    with source_connection(target, source_url) as connection:
        if "decades_identities" not in inspect(connection).get_table_names(schema="public"):
            return None
        identities = _table(connection, "decades_identities")
        value = connection.execute(select(identities.c.workspace_hash).where(
            identities.c.email == str(email).strip().casefold()
        )).scalar_one_or_none()
        return str(value) if value else None


def _settings(connection: Connection, schema_name: str, tables: set[str]) -> dict:
    if "settings" not in tables:
        return {}
    table = _table(connection, "settings", schema_name)
    return {str(row[0]): _safe(row[1]) for row in connection.execute(select(table.c.key, table.c.value))}


def _prepared_rows(table_name: str, rows: list[dict]) -> list[dict]:
    if table_name != "death_cause_pools":
        return rows
    groups: dict[str, dict] = {}
    for row in rows:
        group = str(row.get("death_group") or "Other").strip() or "Other"
        entry = groups.setdefault(group, {"death_group": group, "causes": [], "active": False})
        cause = str(row.get("cause") or "").strip()
        if cause and cause not in entry["causes"]:
            entry["causes"].append(cause)
        entry["active"] = bool(entry["active"] or _truth(row.get("active", True)))
    return list(groups.values())


def _matching_save(target: Session, workspace: Workspace, legacy: dict) -> ChronicleSave | None:
    legacy_id = str(legacy["save_id"])
    deterministic = stable_id("legacy-save", legacy_id)
    exact = target.get(ChronicleSave, deterministic)
    if exact:
        return exact if exact.workspace_id == workspace.id else None
    for save in target.scalars(select(ChronicleSave).where(ChronicleSave.workspace_id == workspace.id)):
        values = dict(save.settings or {})
        if values.get("legacy_neon_save_id") == legacy_id:
            return save
        if legacy_id.casefold() in str(values.get("import_source") or "").casefold():
            return save
    return None


def _target_workspace(target: Session, user: User, preferred: Workspace | None = None) -> Workspace:
    if preferred:
        return preferred
    for membership in target.scalars(select(Membership).where(Membership.user_id == user.id, Membership.role == "owner")):
        workspace = target.get(Workspace, membership.workspace_id)
        if workspace and target.scalar(select(ChronicleSave.id).where(ChronicleSave.workspace_id == workspace.id).limit(1)) is None:
            return workspace
    workspace = Workspace(name=f"{user.display_name or 'My'} legacy chronicles")
    target.add(workspace); target.flush()
    target.add(Membership(user_id=user.id, workspace_id=workspace.id, role="owner"))
    return workspace


def _clock_state(connection: Connection, legacy: dict) -> dict | None:
    if "decades_clock_sync" not in inspect(connection).get_table_names(schema="public"):
        return None
    table = _table(connection, "decades_clock_sync")
    row = connection.execute(select(table).where(
        table.c.save_id == str(legacy["save_id"]),
        table.c.owner_hash == str(legacy.get("owner_hash") or ""),
        table.c.enabled.is_(True),
    ).order_by(table.c.created_at.desc()).limit(1)).mappings().first()
    return dict(row) if row else None


def import_save(target: Session, connection: Connection, workspace: Workspace, legacy: dict) -> dict:
    schema_name, legacy_save_id = str(legacy["schema_name"]), str(legacy["save_id"])
    tables = _legacy_tables(connection, schema_name)
    source_settings = _settings(connection, schema_name, tables)
    save = _matching_save(target, workspace, legacy)
    created_save = save is None
    if save is None:
        save = ChronicleSave(
            id=stable_id("legacy-save", legacy_save_id), workspace_id=workspace.id,
            name=str(legacy.get("name") or "Imported chronicle")[:160],
            global_day=_integer(source_settings.get("current_global_day"), 1) or 1,
            start_year=_integer(source_settings.get("start_year", source_settings.get("challenge_start_year")), 1300) or 1300,
            days_per_year=max(1, _integer(source_settings.get("days_per_year"), 4) or 4),
            pregnancy_days=max(1, _integer(source_settings.get("pregnancy_length_days"), 4) or 4),
        )
        target.add(save); target.flush()
    save.global_day = max(save.global_day, _integer(source_settings.get("current_global_day"), save.global_day) or save.global_day)
    settings = dict(save.settings or {})
    existing_map = {str(key): str(value) for key, value in dict(settings.get("legacy_id_map") or {}).items()}
    settings.update({
        "legacy_settings": source_settings,
        "legacy_neon_save_id": legacy_save_id,
        "legacy_neon_schema": schema_name,
        "legacy_import_version": 1,
        "legacy_source_read_only": True,
    })
    settings.setdefault("legacy_imported_at", datetime.now(timezone.utc).isoformat())
    save.settings = settings

    rows_by_table: dict[str, list[dict]] = {}
    record_ids: dict[tuple[str, str], str] = {}
    id_map = dict(existing_map)
    for table_name in TABLE_KINDS:
        if table_name not in tables:
            continue
        table = _table(connection, table_name, schema_name)
        rows = _prepared_rows(table_name, [dict(row) for row in connection.execute(select(table)).mappings()])
        rows_by_table[table_name] = rows
        for index, row in enumerate(rows):
            key = legacy_key(table_name, row, index)
            existing_id = id_map.get(key) if table_name in REFERENCE_ENTITY_TABLES else None
            record_id = existing_id or stable_id("legacy-record", legacy_save_id, table_name, key)
            record_ids[(table_name, key)] = record_id
            if table_name in REFERENCE_ENTITY_TABLES:
                id_map.setdefault(key, record_id)

    existing_ids = set(target.scalars(select(Record.id).where(Record.save_id == save.id)))
    new_records: list[Record] = []
    counts: dict[str, int] = {}
    for table_name, rows in rows_by_table.items():
        imported = 0
        for index, row in enumerate(rows):
            key = legacy_key(table_name, row, index)
            record_id = record_ids[(table_name, key)]
            if record_id in existing_ids:
                continue
            payload = remap_payload(normalize_payload(table_name, row, key), id_map)
            day = _integer(row.get(DAY_FIELDS.get(table_name, "")))
            record = Record(
                id=record_id, save_id=save.id, kind=TABLE_KINDS[table_name],
                label=record_label(table_name, row)[:240], global_day=day,
                data=payload, version=1, updated_by_device="legacy-neon",
            )
            target.add(record)
            target.add(Change(
                id=stable_id("legacy-change", record_id), save_id=save.id,
                device_id="legacy-neon", record_id=record.id, kind=record.kind,
                operation="upsert", base_version=0, new_version=1,
                payload=sync.serialize(record),
            ))
            existing_ids.add(record_id); new_records.append(record); imported += 1
        counts[table_name] = imported

    settings["legacy_id_map"] = id_map
    save.settings = settings
    target.flush()
    existing_portraits = {(row.record_id, row.stage) for row in target.scalars(select(Portrait).where(Portrait.save_id == save.id))}
    photo_counts: dict[str, int] = {}
    for table_name, (reference_field, fixed_stage) in PHOTO_SPECS.items():
        if table_name not in tables:
            continue
        table = _table(connection, table_name, schema_name)
        imported = 0
        for row in connection.execute(select(table)).mappings():
            referenced = id_map.get(str(row.get(reference_field) or ""))
            stage = str(fixed_stage or row.get("life_stage") or "default")[:40]
            image = row.get("image_data")
            if not referenced or not image or (referenced, stage) in existing_portraits:
                continue
            portrait = Portrait(
                id=stable_id("legacy-portrait", legacy_save_id, referenced, stage),
                save_id=save.id, record_id=referenced, stage=stage,
                mime_type=str(row.get("mime_type") or "image/jpeg")[:80],
                image=bytes(image), source="legacy-neon",
            )
            target.add(portrait); target.flush()
            sync.sync_portrait(target, save, portrait, referenced, stage, device_id="legacy-neon")
            existing_portraits.add((referenced, stage)); imported += 1
        photo_counts[table_name] = imported

    clock_row = _clock_state(connection, legacy)
    if clock_row and target.scalar(select(ClockLink).where(ClockLink.save_id == save.id)) is None:
        clock_link = ClockLink(
            id=stable_id("legacy-clock", legacy_save_id), save_id=save.id,
            token_hash=str(clock_row.get("token_hash")), enabled=bool(clock_row.get("enabled", True)),
            game_anchor_day=_integer(clock_row.get("game_anchor_day")),
            tracker_anchor_day=_integer(clock_row.get("tracker_anchor_day")),
            last_game_day=_integer(clock_row.get("last_game_day")),
            last_seen_at=clock_row.get("last_seen_at"),
        )
        target.add(clock_link); target.flush()
        sync.sync_clock_state(target, save, clock_link, "legacy-neon")

    save.revision += len(new_records)
    defaults = domain.seed_defaults(target, save)
    save.revision += domain.backfill_married_surnames(target, save)
    save.revision += domain.sync_generations(target, save)
    sync.ensure_save_metadata(target, save, "legacy-neon")
    return {
        "save_id": save.id, "legacy_save_id": legacy_save_id, "name": save.name,
        "created": created_save, "records": len(new_records), "defaults": defaults,
        "tables": counts, "photos": photo_counts,
    }


def import_owner_workspace(target: Session, user: User, owner_hash: str,
                           source_url: str = "", preferred_workspace: Workspace | None = None) -> tuple[Workspace, list[dict]]:
    with source_connection(target, source_url) as connection:
        public_tables = inspect(connection).get_table_names(schema="public")
        if "decades_saves" not in public_tables:
            raise LookupError("No legacy Decades save registry was found in this Neon database.")
        registry = _table(connection, "decades_saves")
        legacy_rows = [dict(row) for row in connection.execute(select(registry).where(
            registry.c.owner_hash == owner_hash
        ).order_by(registry.c.updated_at.desc())).mappings()]
        if not legacy_rows:
            raise LookupError("That workspace code does not match any saves in this Neon database.")
        workspace = _target_workspace(target, user, preferred_workspace)
        results = [import_save(target, connection, workspace, row) for row in legacy_rows]
        workspace.name = (str(legacy_rows[0].get("name") or "Legacy chronicles") + " workspace")[:160]
        return workspace, results
