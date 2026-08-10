from __future__ import annotations

import datetime
import io
import json
import re
import sqlite3
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

import cloud_schema
import storage

ROOT = Path(__file__).resolve().parent
SAVE_PACKAGE_EXTENSION = ".decades-save"
GAMEPLAY_TABLES = [
    "sim_photos", "sims", "households", "pregnancies", "rolls",
    "relationships", "events", "event_results", "raw_import_rows",
]
_SAVE_CACHE = []
_SAVE_CACHE_AT = 0.0
_SAVE_CACHE_SECONDS = 5.0
_SETUP_DONE = False


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _schema_name(save_id):
    return "decades_" + re.sub(r"[^a-z0-9_]", "_", save_id.lower())


def _record(row):
    if not row:
        return None
    return {
        "save_id": row[0], "name": row[1], "schema_name": row[2],
        "created_at": str(row[3]), "updated_at": str(row[4]), "source_note": row[5],
    }


def _invalidate_save_cache():
    global _SAVE_CACHE, _SAVE_CACHE_AT
    _SAVE_CACHE = []
    _SAVE_CACHE_AT = 0.0


def _touch_cached(save_id):
    timestamp = _now()
    for item in _SAVE_CACHE:
        if item["save_id"] == save_id:
            item["updated_at"] = timestamp
            break


def ensure_setup():
    global _SETUP_DONE
    if _SETUP_DONE:
        return list_saves()
    with storage.raw_connect(use_direct=True) as connection:
        cloud_schema.create_registry(connection)
    _SETUP_DONE = True
    _invalidate_save_cache()
    saves = list_saves()
    active = storage.load_config().get("active_save_id")
    if saves and not any(item["save_id"] == active for item in saves):
        storage.update_active_save(saves[0]["save_id"])
    return saves


def list_saves(force_refresh=False):
    global _SAVE_CACHE, _SAVE_CACHE_AT
    now = time.monotonic()
    if not force_refresh and _SAVE_CACHE and now - _SAVE_CACHE_AT < _SAVE_CACHE_SECONDS:
        return [dict(item) for item in _SAVE_CACHE]
    with storage.raw_connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT save_id,name,schema_name,created_at,updated_at,source_note "
                "FROM public.decades_saves ORDER BY created_at,save_id"
            )
            saves = [_record(row) for row in cursor.fetchall()]
    _SAVE_CACHE = saves
    _SAVE_CACHE_AT = now
    return [dict(item) for item in saves]


def active_save_id():
    saves = list_saves()
    if not saves:
        return None
    configured = storage.load_config().get("active_save_id")
    if any(item["save_id"] == configured for item in saves):
        return configured
    storage.update_active_save(saves[0]["save_id"])
    return saves[0]["save_id"]


def get_save(save_id):
    return next((item for item in list_saves() if item["save_id"] == save_id), None)


def active_save():
    save_id = active_save_id()
    return get_save(save_id) if save_id else None


def set_active(save_id):
    if not get_save(save_id):
        raise ValueError("Save not found.")
    storage.update_active_save(save_id)


def _create_record(name, source_note=None):
    save_id = "SAVE-" + uuid.uuid4().hex[:10].upper()
    schema_name = _schema_name(save_id)
    with storage.raw_connect(use_direct=True) as connection:
        cloud_schema.create_registry(connection)
        cloud_schema.create_save_schema(connection, schema_name)
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.decades_saves(save_id,name,schema_name,source_note) VALUES(%s,%s,%s,%s)",
                (save_id, name.strip() or save_id, schema_name, source_note),
            )
        connection.commit()
    _invalidate_save_cache()
    storage.update_active_save(save_id)
    return get_save(save_id)


def _table_columns_sqlite(connection, table):
    return [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]


def _table_columns_postgres(cursor, schema_name, table):
    cursor.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
        (schema_name, table),
    )
    return [row[0] for row in cursor.fetchall()]


def migrate_sqlite_file(path, name=None, make_active=True, source_note=None):
    source_path = Path(path)
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    try:
        check = source.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            raise ValueError("The SQLite source failed its integrity check.")
        tables = {
            row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"settings", "sims"}.issubset(tables):
            raise ValueError("That file does not look like a Decades Tracker database.")

        record = _create_record(name or source_path.stem, source_note or f"Migrated from {source_path.name}")
        try:
            from psycopg import sql

            with storage.raw_connect(use_direct=True) as target:
                with target.cursor() as cursor:
                    for table in cloud_schema.TABLES:
                        if table not in tables:
                            continue
                        source_columns = _table_columns_sqlite(source, table)
                        target_columns = _table_columns_postgres(cursor, record["schema_name"], table)
                        columns = [column for column in source_columns if column in target_columns]
                        if not columns:
                            continue
                        quoted_source = ",".join(f'"{column}"' for column in columns)
                        rows = source.execute(f'SELECT {quoted_source} FROM "{table}"').fetchall()
                        if not rows:
                            continue
                        statement = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
                            sql.Identifier(record["schema_name"]),
                            sql.Identifier(table),
                            sql.SQL(",").join(map(sql.Identifier, columns)),
                            sql.SQL(",").join(sql.Placeholder() for _ in columns),
                        )
                        cursor.executemany(statement, rows)
                target.commit()
        except Exception:
            _drop_save(record["save_id"], allow_only=True)
            raise
    finally:
        source.close()
    if make_active:
        storage.update_active_save(record["save_id"])
    return record


def create_blank(name, calendar_start_year=1200, current_year=None, challenge_day=1, source_save_id=None):
    record = _create_record(name, "Created as a blank Neon save")
    start = int(calendar_start_year)
    year = int(current_year if current_year is not None else start)
    day = max(1, min(4, int(challenge_day)))
    global_day = (year - start) * 4 + day
    from psycopg import sql

    with storage.raw_connect(use_direct=True) as connection:
        with connection.cursor() as cursor:
            if source_save_id:
                source = get_save(source_save_id)
                if source:
                    for table in ["rules", "calendar_rows", "roll_rule_eras", "roll_rule_values"]:
                        cursor.execute(
                            sql.SQL("INSERT INTO {}.{} SELECT * FROM {}.{}").format(
                                sql.Identifier(record["schema_name"]), sql.Identifier(table),
                                sql.Identifier(source["schema_name"]), sql.Identifier(table),
                            )
                        )
            for key, value in {
                "start_year": start, "days_per_year": 4,
                "current_global_day": global_day, "current_heir_id": "", "main_household_id": "",
            }.items():
                cursor.execute(
                    sql.SQL("INSERT INTO {}.settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value").format(sql.Identifier(record["schema_name"])),
                    (key, str(value)),
                )
        connection.commit()
    return record


def duplicate_save(source_save_id, name):
    source = get_save(source_save_id)
    if not source:
        raise ValueError("Save not found.")
    record = _create_record(name, f"Duplicated from {source_save_id}")
    from psycopg import sql

    with storage.raw_connect(use_direct=True) as connection:
        with connection.cursor() as cursor:
            for table in cloud_schema.TABLES:
                cursor.execute(
                    sql.SQL("INSERT INTO {}.{} SELECT * FROM {}.{}").format(
                        sql.Identifier(record["schema_name"]), sql.Identifier(table),
                        sql.Identifier(source["schema_name"]), sql.Identifier(table),
                    )
                )
        connection.commit()
    return record


def rename_save(save_id, new_name):
    with storage.raw_connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE public.decades_saves SET name=%s,updated_at=now() WHERE save_id=%s",
                (new_name.strip(), save_id),
            )
        connection.commit()
    _invalidate_save_cache()
    return get_save(save_id)


def _drop_save(save_id, allow_only=False):
    record = get_save(save_id)
    if not record:
        return
    if not allow_only and len(list_saves()) <= 1:
        raise ValueError("You cannot delete the only save.")
    from psycopg import sql

    with storage.raw_connect(use_direct=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(record["schema_name"])))
            cursor.execute("DELETE FROM public.decades_saves WHERE save_id=%s", (save_id,))
        connection.commit()
    _invalidate_save_cache()


def delete_save(save_id):
    _drop_save(save_id)
    remaining = list_saves()
    if remaining:
        storage.update_active_save(remaining[0]["save_id"])


def touch_active():
    save_id = active_save_id()
    if not save_id:
        return
    with storage.raw_connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE public.decades_saves SET updated_at=now() WHERE save_id=%s", (save_id,))
        connection.commit()
    _touch_cached(save_id)


def _sqlite_bytes(save_id):
    record = get_save(save_id)
    if not record:
        raise ValueError("Save not found.")
    from psycopg import sql

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "save.db"
        sqlite_connection = sqlite3.connect(path)
        try:
            with storage.raw_connect() as postgres:
                with postgres.cursor() as cursor:
                    for table in cloud_schema.TABLES:
                        cursor.execute(
                            "SELECT column_name,data_type FROM information_schema.columns "
                            "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                            (record["schema_name"], table),
                        )
                        definitions = cursor.fetchall()
                        if not definitions:
                            continue
                        columns = [item[0] for item in definitions]
                        types = ["BLOB" if item[1] == "bytea" else "INTEGER" if item[1] in ("integer", "bigint") else "REAL" if item[1] in ("double precision", "numeric", "real") else "TEXT" for item in definitions]
                        sqlite_connection.execute(
                            f'CREATE TABLE "{table}" (' + ",".join(f'"{column}" {kind}' for column, kind in zip(columns, types)) + ")"
                        )
                        cursor.execute(
                            sql.SQL("SELECT {} FROM {}.{}").format(
                                sql.SQL(",").join(map(sql.Identifier, columns)),
                                sql.Identifier(record["schema_name"]), sql.Identifier(table),
                            )
                        )
                        rows = cursor.fetchall()
                        if rows:
                            placeholders = ",".join("?" for _ in columns)
                            sqlite_connection.executemany(
                                f'INSERT INTO "{table}" VALUES({placeholders})', rows
                            )
            sqlite_connection.commit()
        finally:
            sqlite_connection.close()
        return path.read_bytes()


def export_database_bytes(save_id):
    return _sqlite_bytes(save_id)


def export_save_package(save_id):
    record = get_save(save_id)
    database = _sqlite_bytes(save_id)
    manifest = {
        "format": "Decades Tracker Save", "format_version": 1,
        "save_name": record["name"], "exported_at": _now(), "database_file": "save.db",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        archive.writestr("save.db", database)
    return buffer.getvalue()


def import_save_package(data, preferred_name=None, make_active=True):
    name = (preferred_name or "").strip() or "Imported Save"
    database = data
    stream = io.BytesIO(data)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream, "r") as archive:
            if "save.db" not in archive.namelist():
                raise ValueError("This archive does not contain a Decades Tracker save.")
            database = archive.read("save.db")
            if "manifest.json" in archive.namelist() and not preferred_name:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                name = manifest.get("save_name") or name
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "import.db"
        path.write_bytes(database)
        return migrate_sqlite_file(path, name, make_active, "Imported from a shareable save")


def import_database(name, data, make_active=True):
    return import_save_package(data, name, make_active)


def discover_local_saves():
    candidates = []
    paths = [ROOT / "decades.db"]
    if (ROOT / "saves").exists():
        paths.extend(sorted((ROOT / "saves").glob("*.db")))
    for path in paths:
        if not path.exists():
            continue
        try:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            count = connection.execute("SELECT COUNT(*) FROM sims").fetchone()[0]
            connection.close()
            candidates.append({"path": str(path), "name": path.stem, "sims": count})
        except Exception:
            pass
    return candidates
