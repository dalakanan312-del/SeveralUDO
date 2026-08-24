from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import BackupSnapshot, ChronicleSave, Portrait, Record
from . import sync


MAX_PACKAGE_BYTES = 150_000_000
_started = False


def public_settings(values: dict | None) -> dict:
    blocked = ("token", "secret", "password", "database_url", "api_key", "connection", "webhook")
    return {key: value for key, value in dict(values or {}).items()
            if not any(part in str(key).casefold() for part in blocked)}


def build_package(session: Session, save: ChronicleSave) -> bytes:
    records = list(session.scalars(select(Record).where(Record.save_id == save.id)))
    portraits = list(session.scalars(select(Portrait).where(Portrait.save_id == save.id)))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("manifest.json", json.dumps({
            "format": "decades-save-v4",
            "name": save.name,
            "global_day": save.global_day,
            "start_year": save.start_year,
            "days_per_year": save.days_per_year,
            "pregnancy_days": save.pregnancy_days,
            "revision": save.revision,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "settings": public_settings(save.settings),
        }, indent=2))
        archive.writestr("records.json", json.dumps([sync.serialize(item) for item in records], indent=2))
        portrait_manifest = []
        for item in portraits:
            extension = "webp" if item.mime_type == "image/webp" else "png" if item.mime_type == "image/png" else "jpg"
            filename = f"portraits/{item.record_id}-{item.stage}.{extension}"
            archive.writestr(filename, item.image)
            portrait_manifest.append({
                "record_id": item.record_id,
                "stage": item.stage,
                "mime_type": item.mime_type,
                "file": filename,
            })
        archive.writestr("portraits.json", json.dumps(portrait_manifest, indent=2))
    return stream.getvalue()


def inspect_package(raw: bytes) -> tuple[dict, list[dict], list[dict], zipfile.ZipFile, io.BytesIO]:
    if len(raw) > MAX_PACKAGE_BYTES:
        raise ValueError("Save package is too large")
    stream = io.BytesIO(raw)
    archive = zipfile.ZipFile(stream)
    manifest = json.loads(archive.read("manifest.json"))
    rows = json.loads(archive.read("records.json"))
    portraits = json.loads(archive.read("portraits.json")) if "portraits.json" in archive.namelist() else []
    if manifest.get("format") != "decades-save-v4":
        archive.close()
        raise ValueError("Unsupported save format")
    return manifest, rows, portraits, archive, stream


def _remap(value, mapping: dict[str, str]):
    if isinstance(value, dict):
        return {key: _remap(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap(item, mapping) for item in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def restore_as_copy(session: Session, workspace_id: str, raw: bytes,
                    suffix: str = "Restored") -> ChronicleSave:
    manifest, rows, portrait_manifest, archive, _stream = inspect_package(raw)
    try:
        save = ChronicleSave(
            workspace_id=workspace_id,
            name=f"{manifest.get('name') or 'Chronicle'} — {suffix}",
            global_day=int(manifest.get("global_day") or 1),
            start_year=int(manifest.get("start_year") or 1300),
            days_per_year=max(1, int(manifest.get("days_per_year") or 4)),
            pregnancy_days=max(1, int(manifest.get("pregnancy_days") or 4)),
            settings=dict(manifest.get("settings") or {}),
        )
        session.add(save)
        session.flush()
        mapping = {str(row["id"]): uuid4().hex for row in rows}
        for row in rows:
            item = Record(
                id=mapping[str(row["id"])],
                save_id=save.id,
                kind=row["kind"],
                label=row.get("label") or "",
                global_day=row.get("global_day"),
                data=_remap(row.get("data") or {}, mapping),
                version=1,
                deleted=bool(row.get("deleted")),
            )
            session.add(item)
            session.flush()
            from .domain import journal
            journal(session, item, "upsert", 0)
        for meta in portrait_manifest:
            record_id = mapping.get(str(meta.get("record_id")))
            filename = str(meta.get("file") or "")
            if record_id and filename in archive.namelist():
                session.add(Portrait(
                    save_id=save.id,
                    record_id=record_id,
                    stage=str(meta.get("stage") or "default"),
                    mime_type=str(meta.get("mime_type") or "image/jpeg"),
                    image=archive.read(filename),
                    source="backup-restore",
                ))
        save.revision = len(rows)
        session.flush()
        return save
    finally:
        archive.close()


def create_snapshot(session: Session, save: ChronicleSave, reason: str = "automatic",
                    force: bool = False) -> BackupSnapshot | None:
    latest = session.scalar(select(BackupSnapshot).where(
        BackupSnapshot.save_id == save.id,
    ).order_by(BackupSnapshot.created_at.desc()).limit(1))
    if latest and latest.revision == save.revision and not force:
        return None
    package = build_package(session, save)
    digest = hashlib.sha256(package).hexdigest()
    if latest and latest.sha256 == digest and not force:
        return None
    row = BackupSnapshot(
        save_id=save.id,
        revision=save.revision,
        reason=reason[:80],
        sha256=digest,
        size_bytes=len(package),
        package=package,
    )
    session.add(row)
    session.flush()
    history = list(session.scalars(select(BackupSnapshot).where(
        BackupSnapshot.save_id == save.id,
    ).order_by(BackupSnapshot.created_at.desc())))
    for old in history[14:]:
        session.delete(old)
    return row


def maybe_create_daily_snapshots() -> int:
    made = 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    with SessionLocal() as session:
        for save in session.scalars(select(ChronicleSave)):
            latest = session.scalar(select(BackupSnapshot).where(
                BackupSnapshot.save_id == save.id,
            ).order_by(BackupSnapshot.created_at.desc()).limit(1))
            latest_at = latest.created_at if latest else None
            if latest_at and latest_at.tzinfo is None:
                latest_at = latest_at.replace(tzinfo=timezone.utc)
            if latest_at is None or latest_at < cutoff:
                made += bool(create_snapshot(session, save, "automatic daily"))
        session.commit()
    return int(made)


def _loop() -> None:
    # Give startup and tests time to finish their first transaction.  Backups
    # are intentionally background maintenance, never part of page loading.
    time.sleep(60)
    while True:
        try:
            maybe_create_daily_snapshots()
        except Exception:
            pass
        time.sleep(60 * 60)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="decades-backups", daemon=True).start()
