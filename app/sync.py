from __future__ import annotations

import base64
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Change, ChronicleSave, ClockLink, Conflict, Device, DiceAudit, Portrait, Record


SYNC_KINDS = {
    "sim", "household", "relationship", "pregnancy", "roll", "event",
    "event_result", "illness", "note", "era_rule", "roll_rule", "roll_rule_era", "event_rule", "plant",
    "campaign", "service", "play_rotation", "family_plan", "portrait_meta",
    "task", "source_archive", "death_causes", "planner_rule", "detection_candidate",
    "death", "era_guidance", "game_candidate", "session_journal", "family_plan",
    "save_metadata", "portrait_blob", "dice_audit_record", "clock_state", "story_entry", "name_entry", "multiple_birth_rule", "illness_signature", "occult_rule",
}

SECRET_MARKERS = ("password", "secret", "token", "api_key", "apikey", "database_url", "connection_string", "oauth")


def _public_settings(values: dict | None) -> dict:
    return {str(key): value for key, value in dict(values or {}).items()
            if not any(marker in str(key).casefold() for marker in SECRET_MARKERS)}


def _device_name(device_id: str | None = None) -> str:
    if device_id:
        return device_id
    try:
        from .config import settings
        return "local" if settings.local_mode else "web"
    except Exception:
        return "server"


def upsert_shadow(session: Session, save: ChronicleSave, kind: str, identity: str,
                  label: str, data: dict, device_id: str | None = None) -> Record:
    """Version a non-Record table through the existing conflict-aware sync log."""
    if kind not in SYNC_KINDS:
        raise ValueError("Unsupported shadow record kind")
    current = session.scalar(select(Record).where(
        Record.save_id == save.id, Record.kind == kind, Record.deleted.is_(False),
        Record.data["sync_identity"].as_string() == identity,
    ).limit(1))
    value = {**dict(data or {}), "sync_identity": identity}
    if current and current.label == label and dict(current.data or {}) == value:
        return current
    if current:
        base = current.version; current.label = label; current.data = value; current.version += 1
    else:
        base = 0; current = Record(save_id=save.id, kind=kind, label=label, data=value)
        session.add(current); session.flush()
    current.updated_by_device = _device_name(device_id)
    session.add(Change(
        save_id=save.id, device_id=current.updated_by_device, record_id=current.id,
        kind=kind, operation="upsert", base_version=base, new_version=current.version,
        payload=serialize(current),
    ))
    return current


def ensure_save_metadata(session: Session, save: ChronicleSave, device_id: str | None = None) -> Record:
    return upsert_shadow(session, save, "save_metadata", "save", save.name, {
        "name": save.name, "global_day": int(save.global_day), "start_year": int(save.start_year),
        "days_per_year": int(save.days_per_year), "pregnancy_days": int(save.pregnancy_days),
        "settings": _public_settings(save.settings),
    }, device_id)


def sync_portrait(session: Session, save: ChronicleSave, portrait: Portrait | None,
                  record_id: str, stage: str, *, deleted: bool = False,
                  device_id: str | None = None) -> Record:
    encoded = base64.b64encode(portrait.image).decode("ascii") if portrait and not deleted else ""
    return upsert_shadow(session, save, "portrait_blob", f"{record_id}:{stage}", f"Portrait · {stage}", {
        "record_id": record_id, "stage": stage, "mime_type": portrait.mime_type if portrait else "",
        "source": portrait.source if portrait else "", "image_base64": encoded, "deleted": bool(deleted),
    }, device_id)


def sync_dice_audit(session: Session, save: ChronicleSave, audit: DiceAudit,
                    device_id: str | None = None) -> Record:
    return upsert_shadow(session, save, "dice_audit_record", audit.id, f"Dice · {audit.notation} · {audit.total}", {
        "audit_id": audit.id, "context": audit.context, "context_id": audit.context_id,
        "notation": audit.notation, "faces": list(audit.faces or []), "total": int(audit.total),
        "commitment": audit.commitment, "reveal": audit.reveal,
        "created_at": audit.created_at.isoformat() if audit.created_at else None,
    }, device_id)


def sync_clock_state(session: Session, save: ChronicleSave, link: ClockLink,
                     device_id: str | None = None) -> Record:
    return upsert_shadow(session, save, "clock_state", "clock", "Game Clock alignment", {
        "enabled": bool(link.enabled), "game_anchor_day": link.game_anchor_day,
        "tracker_anchor_day": link.tracker_anchor_day, "last_game_day": link.last_game_day,
        "last_game_hour": link.last_game_hour, "last_game_minute": link.last_game_minute,
        "last_seen_at": link.last_seen_at.isoformat() if link.last_seen_at else None,
    }, device_id)


def materialize_special(session: Session, save: ChronicleSave, record: Record) -> None:
    """Apply synchronized shadows to local tables without moving credentials."""
    data = dict(record.data or {})
    if record.kind == "save_metadata":
        save.name = str(data.get("name") or save.name)[:160]
        for field in ("global_day", "start_year", "days_per_year", "pregnancy_days"):
            try:
                value = int(data.get(field))
            except (TypeError, ValueError):
                continue
            if field in {"days_per_year", "pregnancy_days"}:
                value = max(1, value)
            setattr(save, field, value)
        private_values = {key: value for key, value in dict(save.settings or {}).items()
                          if any(marker in str(key).casefold() for marker in SECRET_MARKERS)}
        save.settings = {**_public_settings(data.get("settings")), **private_values}
    elif record.kind == "portrait_blob":
        record_id, stage = str(data.get("record_id") or ""), str(data.get("stage") or "default")[:40]
        target = session.get(Record, record_id) if record_id else None
        if not target or target.save_id != save.id:
            return
        portrait = session.scalar(select(Portrait).where(Portrait.record_id == record_id, Portrait.stage == stage))
        if bool(data.get("deleted")) or record.deleted:
            if portrait: session.delete(portrait)
            return
        try:
            image = base64.b64decode(str(data.get("image_base64") or ""), validate=True)
        except Exception:
            return
        if not image or len(image) > 8 * 1024 * 1024:
            return
        if portrait:
            portrait.image=image; portrait.mime_type=str(data.get("mime_type") or "image/webp"); portrait.source=str(data.get("source") or "sync")
        else:
            session.add(Portrait(save_id=save.id, record_id=record_id, stage=stage,
                                 image=image, mime_type=str(data.get("mime_type") or "image/webp"),
                                 source=str(data.get("source") or "sync")))
    elif record.kind == "dice_audit_record":
        audit_id = str(data.get("audit_id") or "")
        if not audit_id or session.get(DiceAudit, audit_id):
            return
        try:
            session.add(DiceAudit(id=audit_id, save_id=save.id, context=str(data.get("context") or "practice"),
                                  context_id=str(data.get("context_id") or ""), notation=str(data.get("notation") or "d20"),
                                  faces=list(data.get("faces") or []), total=int(data.get("total") or 0),
                                  commitment=str(data.get("commitment") or ""), reveal=str(data.get("reveal") or "")))
        except (TypeError, ValueError):
            return
    elif record.kind == "clock_state":
        link = session.scalar(select(ClockLink).where(ClockLink.save_id == save.id))
        if not link:
            return
        for field in ("game_anchor_day", "tracker_anchor_day", "last_game_day", "last_game_hour", "last_game_minute"):
            value = data.get(field)
            try: setattr(link, field, int(value) if value is not None else None)
            except (TypeError, ValueError): pass


def unpack_payload(payload: dict) -> tuple[str, int | None, dict, bool]:
    """Accept both native serialized records and older flat sync payloads."""
    value = dict(payload or {})
    if isinstance(value.get("data"), dict):
        return str(value.get("label") or ""), value.get("global_day"), dict(value["data"]), bool(value.get("deleted"))
    label = str(value.pop("label", ""))
    day = value.pop("global_day", None)
    for key in ("id", "kind", "version", "deleted", "updated_at"):
        value.pop(key, None)
    return label, day, value, False


def serialize(record: Record) -> dict:
    return {
        "id": record.id, "kind": record.kind, "label": record.label,
        "global_day": record.global_day, "data": record.data,
        "version": record.version, "deleted": record.deleted,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def conflict_fields(conflict: Conflict) -> list[dict]:
    """Return a stable, field-level comparison for the conflict review UI."""
    incoming = dict(conflict.local_change or {})
    local_label, local_day, local_data, local_deleted = unpack_payload(dict(incoming.get("payload") or {}))
    local_deleted = bool(local_deleted or incoming.get("operation") == "delete")
    server = dict(conflict.server_record or {})
    server_label, server_day, server_data, server_deleted = unpack_payload(server)
    values = [
        ("label", "Name", local_label, server_label),
        ("global_day", "Global Day", local_day, server_day),
        ("deleted", "Archived", local_deleted, server_deleted),
    ]
    for key in sorted(set(local_data) | set(server_data), key=str.casefold):
        values.append((f"data.{key}", str(key).replace("_", " ").title(), local_data.get(key), server_data.get(key)))
    return [{
        "path": path,
        "label": label,
        "desktop": desktop,
        "server": hosted,
        "different": desktop != hosted,
    } for path, label, desktop, hosted in values]


def merged_conflict_payload(conflict: Conflict, desktop_fields: set[str]) -> dict:
    """Merge selected desktop fields into the hosted copy."""
    incoming = dict(conflict.local_change or {})
    local_label, local_day, local_data, local_deleted = unpack_payload(dict(incoming.get("payload") or {}))
    local_deleted = bool(local_deleted or incoming.get("operation") == "delete")
    server = dict(conflict.server_record or {})
    server_label, server_day, server_data, server_deleted = unpack_payload(server)
    data = dict(server_data)
    for key in set(local_data) | set(server_data):
        if f"data.{key}" in desktop_fields:
            if key in local_data:
                data[key] = local_data[key]
            else:
                data.pop(key, None)
    return {
        "id": conflict.record_id,
        "kind": str(incoming.get("kind") or server.get("kind") or "note"),
        "label": local_label if "label" in desktop_fields else server_label,
        "global_day": local_day if "global_day" in desktop_fields else server_day,
        "data": data,
        "deleted": local_deleted if "deleted" in desktop_fields else server_deleted,
    }


def apply_change(session: Session, save: ChronicleSave, device: Device, incoming: dict) -> dict:
    if incoming.get("kind") not in SYNC_KINDS:
        raise ValueError("Unsupported record kind")
    change_id = str(incoming["change_id"])
    prior = session.scalar(select(Change).where(Change.id == change_id))
    if prior:
        return {"status": "duplicate", "record": serialize(session.get(Record, prior.record_id))}
    record_id = str(incoming["record_id"])
    record = session.get(Record, record_id)
    base_version = int(incoming.get("base_version") or 0)
    if record and record.save_id != save.id:
        raise ValueError("Record belongs to a different save")
    if record and record.version != base_version:
        conflict = Conflict(
            save_id=save.id, record_id=record_id,
            local_change=incoming, server_record=serialize(record),
        )
        session.add(conflict)
        session.flush()
        return {"status": "conflict", "conflict_id": conflict.id, "server_record": serialize(record)}
    if record is None:
        record = Record(id=record_id, save_id=save.id, kind=incoming["kind"])
        session.add(record)
    payload = dict(incoming.get("payload") or {})
    label, global_day, record_data, payload_deleted = unpack_payload(payload)
    record.kind = incoming["kind"]
    record.label = label or record.label or ""
    record.global_day = global_day if global_day is not None else record.global_day
    record.data = record_data
    record.deleted = incoming.get("operation") == "delete" or payload_deleted
    record.version = base_version + 1
    record.updated_by_device = device.id
    record.updated_at = datetime.now(timezone.utc)
    materialize_special(session, save, record)
    change = Change(
        id=change_id, save_id=save.id, device_id=device.id, record_id=record.id,
        kind=record.kind, operation=incoming.get("operation", "upsert"),
        base_version=base_version, new_version=record.version, payload=serialize(record),
    )
    session.add(change)
    save.revision += 1
    session.flush()
    return {"status": "applied", "record": serialize(record), "sequence": change.sequence}


def pull(session: Session, save_id: str, after: int, limit: int = 500) -> dict:
    rows = list(session.scalars(
        select(Change).where(Change.save_id == save_id, Change.sequence > after)
        .order_by(Change.sequence).limit(min(max(limit, 1), 1000))
    ))
    return {
        "changes": [{
            "sequence": row.sequence, "change_id": row.id, "device_id": row.device_id,
            "record_id": row.record_id, "kind": row.kind, "operation": row.operation,
            "base_version": row.base_version, "new_version": row.new_version,
            "payload": row.payload, "created_at": row.created_at.isoformat(),
        } for row in rows],
        "cursor": rows[-1].sequence if rows else after,
    }
