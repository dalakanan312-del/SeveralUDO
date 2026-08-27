from __future__ import annotations

"""Read a small, safe subset of an unmodified Sims 4 save.

The scanner deliberately has no write path.  It reads the DBPF container and
the few protobuf fields needed for a useful comparison with Clock Sync.  Any
unknown or newly added game fields are skipped, so an unsupported game build
produces an empty/partial preview instead of damaging a save.
"""

import base64
import os
import struct
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .game_metadata import _dbpf_entries, _resource_bytes


SAVE_GAME_DATA_RESOURCE = 0x0000000D
SIM_PORTRAIT_RESOURCE = 0x00000015
MAX_SAVE_BYTES = 512 * 1024 * 1024
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
MAX_PORTRAIT_BYTES = 5 * 1024 * 1024


class SaveScanError(RuntimeError):
    pass


@dataclass(frozen=True)
class SaveFile:
    path: Path
    modified_at: datetime
    size: int


def default_save_root() -> Path:
    configured = os.environ.get("SIMS4_SAVES_DIR")
    if configured:
        return Path(configured).expanduser()
    profile = Path(os.environ.get("USERPROFILE") or Path.home())
    return profile / "Documents" / "Electronic Arts" / "The Sims 4" / "saves"


def discover_saves(root: Path | None = None) -> list[SaveFile]:
    """Return primary slot saves newest first; automatic .ver backups are excluded."""
    folder = Path(root) if root else default_save_root()
    if not folder.is_dir():
        return []
    found: list[SaveFile] = []
    try:
        paths = folder.glob("Slot_*.save")
        for path in paths:
            if not path.is_file():
                continue
            stat = path.stat()
            found.append(SaveFile(path.resolve(), datetime.fromtimestamp(stat.st_mtime, timezone.utc), stat.st_size))
    except OSError:
        return []
    return sorted(found, key=lambda item: item.modified_at, reverse=True)


def _varint(data: bytes, cursor: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while cursor < len(data) and shift < 70:
        byte = data[cursor]
        cursor += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, cursor
        shift += 7
    raise SaveScanError("The save contains an invalid protobuf value.")


def protobuf_fields(data: bytes) -> dict[int, list[tuple[int, int | bytes]]]:
    """Decode protobuf wire values without relying on EA's bundled Python runtime."""
    if len(data) > MAX_MESSAGE_BYTES:
        raise SaveScanError("A save section is too large to inspect safely.")
    cursor = 0
    fields: dict[int, list[tuple[int, int | bytes]]] = {}
    while cursor < len(data):
        key, cursor = _varint(data, cursor)
        number, wire = key >> 3, key & 7
        if not number:
            raise SaveScanError("The save contains an invalid field number.")
        if wire == 0:
            value, cursor = _varint(data, cursor)
        elif wire == 1:
            if cursor + 8 > len(data):
                raise SaveScanError("The save ends inside a fixed-width value.")
            value = int.from_bytes(data[cursor:cursor + 8], "little")
            cursor += 8
        elif wire == 2:
            length, cursor = _varint(data, cursor)
            if length > MAX_MESSAGE_BYTES or cursor + length > len(data):
                raise SaveScanError("The save contains an invalid message length.")
            value = data[cursor:cursor + length]
            cursor += length
        elif wire == 5:
            if cursor + 4 > len(data):
                raise SaveScanError("The save ends inside a fixed-width value.")
            value = int.from_bytes(data[cursor:cursor + 4], "little")
            cursor += 4
        else:
            raise SaveScanError(f"Unsupported protobuf wire type {wire}.")
        fields.setdefault(number, []).append((wire, value))
    return fields


def _value(fields, number, default=None):
    values = fields.get(number) or ()
    return values[0][1] if values else default


def _text(fields, number) -> str:
    value = _value(fields, number, b"")
    if not isinstance(value, bytes):
        return ""
    return value.decode("utf-8", errors="replace").strip("\x00 ")


def _float32(fields, number) -> float | None:
    value = _value(fields, number)
    if value is None:
        return None
    try:
        return struct.unpack("<f", int(value).to_bytes(4, "little"))[0]
    except (OverflowError, struct.error, TypeError, ValueError):
        return None


def _id_list(raw: bytes) -> list[int]:
    try:
        fields = protobuf_fields(raw)
    except SaveScanError:
        return []
    values: list[int] = []
    for _, value in fields.get(1, ()):
        if isinstance(value, int):
            values.append(value)
        elif isinstance(value, bytes):
            # IdList.ids is repeated fixed64 and is normally packed.
            values.extend(int.from_bytes(value[cursor:cursor + 8], "little")
                          for cursor in range(0, len(value) - 7, 8))
    return values


def _parse_sim(raw: bytes) -> dict:
    fields = protobuf_fields(raw)
    sim_id = _value(fields, 1)
    if not isinstance(sim_id, int):
        return {}
    first_name, last_name = _text(fields, 5), _text(fields, 6)
    age_value = _value(fields, 8)
    age_labels = {1: "newborn", 2: "infant", 4: "toddler", 8: "child", 16: "teen", 32: "youngadult", 64: "adult", 128: "elder"}
    gender_labels = {4096: "Male", 8192: "Female"}
    age_progress = _float32(fields, 13)
    pregnancy_progress = _float32(fields, 48)
    age_progress_percentage = None
    if age_progress is not None:
        age_progress_percentage = round(age_progress * 100 if age_progress <= 1 else age_progress, 2)
    pregnancy_progress_percentage = None
    if pregnancy_progress is not None:
        pregnancy_progress_percentage = round(
            pregnancy_progress * 100 if pregnancy_progress <= 1 else pregnancy_progress, 2
        )
    result = {
        "game_sim_id": str(sim_id),
        "game_household_id": str(_value(fields, 4) or ""),
        "first_name": first_name,
        "last_name": last_name,
        "name": " ".join(value for value in (first_name, last_name) if value).strip() or f"Game Sim {sim_id}",
        "gender_value": _value(fields, 7),
        "sex": gender_labels.get(_value(fields, 7), "Unknown"),
        "age_value": age_value,
        "age_stage": age_labels.get(age_value, "unknown"),
        "is_baby": age_value == 1,
        "age_progress": age_progress,
        "age_progress_percentage": age_progress_percentage,
        "significant_other_game_id": str(_value(fields, 15) or ""),
        "pregnancy_progress": pregnancy_progress,
        "pregnancy_progress_percentage": pregnancy_progress_percentage,
    }
    # A serialized pregnancy-progress field is the reliable save-file signal
    # that this Sim currently has a pregnancy tracker.  When the field is
    # absent, leave pregnancy state unknown instead of treating the scan as an
    # authoritative pregnancy ending.
    if pregnancy_progress is not None:
        result["is_pregnant"] = True
        result["pregnancy_scan_supported"] = True
    return result


def _parse_household(raw: bytes) -> dict:
    fields = protobuf_fields(raw)
    household_id = _value(fields, 2)
    if not isinstance(household_id, int):
        return {}
    members_raw = _value(fields, 11, b"")
    return {
        "game_household_id": str(household_id),
        "name": _text(fields, 3) or f"Game Household {household_id}",
        "funds": _value(fields, 5),
        "member_game_ids": [str(value) for value in _id_list(members_raw)] if isinstance(members_raw, bytes) else [],
        "last_played_game_sim_id": str(_value(fields, 9) or ""),
        "is_unplayed": bool(_value(fields, 14, 0)),
        "is_player": bool(_value(fields, 31, 0)),
    }


def _parse_save_slot(raw: bytes) -> dict:
    fields = protobuf_fields(raw)
    game_ticks = None
    gameplay = _value(fields, 8)
    if isinstance(gameplay, bytes):
        game_ticks = _value(protobuf_fields(gameplay), 1)
    day = hour = minute = second = None
    if isinstance(game_ticks, int):
        # EA's DateAndTime clock uses 25 ticks per Sim second.
        sim_seconds = game_ticks // 25
        day = sim_seconds // 86_400
        hour = (sim_seconds % 86_400) // 3_600
        minute = (sim_seconds % 3_600) // 60
        second = sim_seconds % 60
    return {
        "slot_name": _text(fields, 9),
        "active_household_game_id": str(_value(fields, 11) or ""),
        "game_ticks": game_ticks,
        "game_day": day,
        "game_hour": hour,
        "game_minute": minute,
        "game_second": second,
    }


def _image_mime(raw: bytes) -> str:
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _embedded_sim_portraits(package: bytes, entries, sim_ids: set[str]) -> dict[str, dict]:
    """Return individual save thumbnails whose DBPF instance is a known Sim ID.

    Sims 4 stores generated Sim thumbnails as resource type 0x15 and uses the
    Sim's stable numeric identity as the resource instance. Requiring both the
    type and exact identity prevents lot or household images from being
    assigned to a person.
    """
    wanted = {int(value) for value in sim_ids if str(value).isdigit()}
    found: dict[str, dict] = {}
    for entry in entries:
        if entry[0] != SIM_PORTRAIT_RESOURCE or entry[2] not in wanted:
            continue
        image = _resource_bytes(package, entry)
        if not image or len(image) > MAX_PORTRAIT_BYTES:
            continue
        mime = _image_mime(image)
        if not mime:
            continue
        sim_id = str(entry[2])
        found[sim_id] = {
            "portrait_image_base64": base64.b64encode(image).decode("ascii"),
            "portrait_mime_type": mime,
            "portrait_source": "save-file-game",
            "portrait_resource_instance": sim_id,
            "has_embedded_portrait": True,
        }
    return found


def inspect_save(path: Path) -> dict:
    """Inspect a save file and return a reconciliation preview.

    This function opens the path read-only and never touches the file's metadata.
    """
    target = Path(path).expanduser().resolve()
    save_root = default_save_root().resolve()
    try:
        target.relative_to(save_root)
    except ValueError as exc:
        raise SaveScanError("Only saves inside the configured Sims 4 saves folder can be scanned.") from exc
    try:
        stat = target.stat()
    except OSError as exc:
        raise SaveScanError("The selected Sims 4 save could not be opened.") from exc
    if not target.is_file() or target.suffix.casefold() != ".save":
        raise SaveScanError("Select a primary .save file, not a backup or unrelated file.")
    if stat.st_size > MAX_SAVE_BYTES:
        raise SaveScanError("The selected Sims 4 save is unexpectedly large.")
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise SaveScanError("The selected Sims 4 save could not be read.") from exc
    if raw[:4] != b"DBPF":
        raise SaveScanError("The selected file is not a Sims 4 DBPF save.")
    entries = list(_dbpf_entries(raw) or ())
    resource = None
    for entry in entries:
        if entry[0] == SAVE_GAME_DATA_RESOURCE:
            resource = _resource_bytes(raw, entry)
            if resource:
                break
    if not resource:
        raise SaveScanError("This game build's save summary could not be located.")
    outer = protobuf_fields(resource)
    slot_raw = _value(outer, 2, b"")
    slot = _parse_save_slot(slot_raw) if isinstance(slot_raw, bytes) else {}
    slot_id = target.stem.removeprefix("Slot_")
    slot["save_slot_id"] = slot_id
    slot["save_identity"] = hashlib.sha256(
        f"{slot_id}|{slot.get('slot_name') or ''}".encode("utf-8")
    ).hexdigest()[:32]
    sims = [_parse_sim(value) for wire, value in outer.get(6, ()) if wire == 2 and isinstance(value, bytes)]
    households = [_parse_household(value) for wire, value in outer.get(5, ()) if wire == 2 and isinstance(value, bytes)]
    sims = [item for item in sims if item]
    households = [item for item in households if item]
    embedded_portraits = _embedded_sim_portraits(raw, entries, {item["game_sim_id"] for item in sims})
    for sim in sims:
        portrait = embedded_portraits.get(sim["game_sim_id"])
        if portrait:
            sim.update(portrait)
            sim["portrait_data_uri"] = (
                f"data:{portrait['portrait_mime_type']};base64,{portrait['portrait_image_base64']}"
            )
    return {
        "path": str(target),
        "file_name": target.name,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "size": stat.st_size,
        "sim_count": len(sims),
        "household_count": len(households),
        "portrait_count": len(embedded_portraits),
        "slot": slot,
        "sims": sims,
        "households": households,
        "limitations": "Read-only save scanning supplements Clock Sync. A portrait is available only when the game has generated and embedded that Sim's thumbnail in this save. Exact live time, illnesses, traits and transient states still come from Clock Sync.",
    }


def relevant_population(scan: dict) -> tuple[list[dict], list[dict]]:
    """Limit review to player-owned households, including the active household."""
    active = str((scan.get("slot") or {}).get("active_household_game_id") or "")
    households = [item for item in scan.get("households") or () if item.get("is_player") or item.get("game_household_id") == active]
    household_ids = {str(item.get("game_household_id") or "") for item in households}
    sims = [item for item in scan.get("sims") or () if str(item.get("game_household_id") or "") in household_ids]
    return households, sims


def compare_scan(session, save, scan: dict) -> dict:
    """Build a read-only, field-level comparison with the open tracker save."""
    from sqlalchemy import select

    from . import portraits
    from .models import Portrait, Record

    households, sims = relevant_population(scan)
    tracked = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
    )))
    by_game_id = {
        str((item.data or {}).get("game_sim_id") or "").strip(): item for item in tracked
        if str((item.data or {}).get("game_sim_id") or "").strip()
    }
    by_name: dict[str, list] = {}
    for item in tracked:
        key = " ".join(str(item.label or "").casefold().split())
        if key:
            by_name.setdefault(key, []).append(item)
    household_by_id = {str(item.get("game_household_id") or ""): item for item in households}
    stored_portraits = {}
    for item in session.scalars(select(Portrait).where(Portrait.save_id == save.id)):
        key = (item.record_id, "".join(character for character in str(item.stage).casefold() if character.isalpha()))
        current = stored_portraits.get(key)
        if not current or (current.source in {"clock-sync-game", "save-file-game"}
                           and item.source not in {"clock-sync-game", "save-file-game"}):
            stored_portraits[key] = item
    rows = []
    present_ids = set()
    for game_sim in sims:
        game_id = str(game_sim.get("game_sim_id") or "").strip()
        if game_id:
            present_ids.add(game_id)
        match = by_game_id.get(game_id)
        match_source = "game identity" if match else ""
        if not match:
            candidates = by_name.get(" ".join(str(game_sim.get("name") or "").casefold().split()), [])
            if len(candidates) == 1:
                match, match_source = candidates[0], "unique exact name"
        differences = []
        home = household_by_id.get(str(game_sim.get("game_household_id") or ""), {})
        if match:
            data = match.data or {}
            checks = (
                ("first name", data.get("first_name"), game_sim.get("first_name")),
                ("last name", data.get("last_name"), game_sim.get("last_name")),
                ("life stage", data.get("game_age_stage"), game_sim.get("age_stage")),
                ("game household", data.get("game_household_id"), game_sim.get("game_household_id")),
                ("significant other", data.get("game_significant_other_game_sim_id"), game_sim.get("significant_other_game_id")),
            )
            for label, tracker_value, game_value in checks:
                if game_value not in (None, "") and str(tracker_value or "").casefold() != str(game_value).casefold():
                    differences.append({"field": label, "tracker": tracker_value, "game": game_value})
            if game_sim.get("pregnancy_scan_supported") and bool(data.get("game_is_pregnant")) != bool(game_sim.get("is_pregnant")):
                differences.append({"field":"pregnancy", "tracker":bool(data.get("game_is_pregnant")), "game":bool(game_sim.get("is_pregnant"))})
            if game_sim.get("portrait_image_base64"):
                stage = "".join(
                    character for character in str(game_sim.get("age_stage") or "default").casefold()
                    if character.isalpha()
                ) or "default"
                current = stored_portraits.get((match.id, stage))
                if current and current.source not in {"clock-sync-game", "save-file-game"}:
                    game_sim["portrait_import_status"] = "manual portrait kept"
                else:
                    try:
                        detected, _ = portraits.normalize_image(
                            base64.b64decode(game_sim["portrait_image_base64"], validate=True),
                            max_pixels=512,
                        )
                    except Exception:
                        detected = b""
                    if not current:
                        differences.append({"field":"portrait", "tracker":"missing", "game":"embedded thumbnail"})
                        game_sim["portrait_import_status"] = "new portrait"
                    elif detected and current.image != detected:
                        differences.append({"field":"portrait", "tracker":"older game thumbnail", "game":"new save thumbnail"})
                        game_sim["portrait_import_status"] = "updated portrait"
                    else:
                        game_sim["portrait_import_status"] = "portrait already current"
        rows.append({
            **game_sim,
            "household_name": home.get("name") or "",
            "tracker_record_id": match.id if match else None,
            "tracker_record_label": match.label if match else None,
            "match_source": match_source,
            "differences": differences,
            "comparison_status": "changed" if differences else "matched" if match else "new",
        })
    missing = [
        {"tracker_record_id": item.id, "tracker_record_label": item.label,
         "game_sim_id": str((item.data or {}).get("game_sim_id") or "")}
        for item in tracked
        if str((item.data or {}).get("game_sim_id") or "").strip()
        and str((item.data or {}).get("game_sim_id") or "").strip() not in present_ids
    ]
    counts = {
        "matched": sum(row["comparison_status"] == "matched" for row in rows),
        "changed": sum(row["comparison_status"] == "changed" for row in rows),
        "new": sum(row["comparison_status"] == "new" for row in rows),
        "missing": len(missing),
    }
    return {"rows": rows, "missing": missing, "counts": counts,
            "safe_review_count": counts["changed"] + counts["new"]}


def import_portraits(session, save, scan: dict, target_record_id: str | None = None) -> dict:
    """Import only embedded Sim thumbnails from a read-only save scan.

    This deliberately leaves the tracker clock and every non-portrait Sim field
    untouched. Stable game IDs are preferred; a unique exact display name is a
    safe fallback for older imported Sims that have not been linked yet.
    """
    from sqlalchemy import func, select

    from . import clock
    from .models import Portrait, Record

    automatic_sources = {"clock-sync-game", "save-file-game"}
    tracked = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
    )))
    target = next((item for item in tracked if item.id == target_record_id), None) if target_record_id else None
    if target_record_id and not target:
        return {"identity_matches":0, "available":0, "matched":0, "updated":0,
                "protected":0, "unchanged":0, "unmatched":0}

    by_game_id = {
        str((item.data or {}).get("game_sim_id") or "").strip(): item for item in tracked
        if str((item.data or {}).get("game_sim_id") or "").strip()
    }
    by_name: dict[str, list] = {}
    for item in tracked:
        key = " ".join(str(item.label or "").casefold().split())
        if key:
            by_name.setdefault(key, []).append(item)

    result = {"identity_matches":0, "available":0, "matched":0, "updated":0,
              "protected":0, "unchanged":0, "unmatched":0}
    seen_records: set[str] = set()
    for snapshot in scan.get("sims") or ():
        game_id = str(snapshot.get("game_sim_id") or "").strip()
        match = by_game_id.get(game_id)
        if not match:
            candidates = by_name.get(" ".join(str(snapshot.get("name") or "").casefold().split()), [])
            if len(candidates) == 1:
                match = candidates[0]
        if target and (not match or match.id != target.id):
            continue
        if match and match.id not in seen_records:
            result["identity_matches"] += 1
            seen_records.add(match.id)
        if not snapshot.get("portrait_image_base64"):
            continue
        result["available"] += 1
        if not match:
            result["unmatched"] += 1
            continue
        result["matched"] += 1
        stage = clock._stage_key(snapshot.get("age_stage")) or "default"
        existing = list(session.scalars(select(Portrait).where(
            Portrait.record_id == match.id,
            func.lower(func.replace(Portrait.stage, " ", "")) == stage.casefold(),
        )))
        if any(item.source not in automatic_sources for item in existing):
            result["protected"] += 1
            continue
        if clock._store_game_portrait(session, save, match, snapshot):
            result["updated"] += 1
        else:
            result["unchanged"] += 1
    return result


def reconcile_scan(session, save, scan: dict, selected_game_ids: set[str], advance_clock: bool = True) -> dict:
    """Apply a user-approved read-only scan to tracker records.

    The source file remains untouched.  New people become review items; exact
    name matches attach to imported people, and linked people receive only the
    fields the save parser can determine reliably.
    """
    from sqlalchemy import select

    from . import automation, clock, domain
    from .models import Record

    households, sims = relevant_population(scan)
    household_by_id = {str(item["game_household_id"]): item for item in households}
    slot = scan.get("slot") or {}
    game_day = slot.get("game_day")
    hour, minute, second = slot.get("game_hour"), slot.get("game_minute"), slot.get("game_second")
    advanced = 0
    settings = dict(save.settings or {})
    if advance_clock and game_day is not None:
        anchor_game = settings.get("save_scan_anchor_game_day")
        anchor_tracker = settings.get("save_scan_anchor_tracker_day")
        if anchor_game is None or anchor_tracker is None:
            settings.update(save_scan_anchor_game_day=int(game_day), save_scan_anchor_tracker_day=int(save.global_day))
        else:
            target = int(anchor_tracker) + max(0, int(game_day) - int(anchor_game))
            if target > save.global_day:
                advanced = target - save.global_day
                save.global_day = target
        settings.update(
            save_scan_last_file=scan.get("file_name"), save_scan_last_game_day=game_day,
            save_scan_last_game_hour=hour, save_scan_last_game_minute=minute,
            save_scan_last_game_second=second,
            save_scan_last_modified_at=scan.get("modified_at"),
        )
        save.settings = settings
    tracked = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
    )))
    by_game_id = {str(item.data.get("game_sim_id") or ""): item for item in tracked if item.data.get("game_sim_id")}
    candidates = linked = updated = portrait_updates = 0
    journal_entries: list[str] = []
    selected_snapshots: list[tuple[dict, dict]] = []
    for item in sims:
        game_id = str(item.get("game_sim_id") or "")
        if not game_id or game_id not in selected_game_ids:
            continue
        home = household_by_id.get(str(item.get("game_household_id") or ""), {})
        snapshot = {
            **item,
            "household_id": item.get("game_household_id"),
            "household_name": home.get("name") or "",
            "household_funds": home.get("funds"),
            "household_member_game_ids": list(home.get("member_game_ids") or []),
            "household_last_played_game_sim_id": home.get("last_played_game_sim_id") or "",
            "household_is_unplayed": bool(home.get("is_unplayed", False)),
            "household_is_player": bool(home.get("is_player", False)),
            "detected_game_day": game_day,
            "detected_game_hour": hour,
            "detected_game_minute": minute,
            "detected_game_second": second,
            "detected_tracker_global_day": save.global_day,
            "telemetry_version": 0,
            "source": "read-only Sims 4 save scan",
        }
        # The data URI is only for the browser preview. Keep one base64 copy in
        # the review payload instead of duplicating the image in stored JSON.
        snapshot.pop("portrait_data_uri", None)
        selected_snapshots.append((item, snapshot))
    household_matches, households_created, households_updated, household_members_linked = clock.sync_game_households(
        session, save, [snapshot for _, snapshot in selected_snapshots], by_game_id,
        source="the read-only Sims 4 save scan",
    )
    for item, snapshot in selected_snapshots:
        game_id = str(item.get("game_sim_id") or "")
        existing = by_game_id.get(game_id)
        if not existing:
            existing = clock.imported_sim_match(session, save, snapshot)
            if existing:
                clock.attach_game_identity(session, save, existing, snapshot)
                by_game_id[game_id] = existing
                household_members_linked += clock.connect_sim_to_game_household(
                    session, save, existing, snapshot, household_matches,
                )
                linked += 1
        if not existing:
            pending = session.scalar(select(Record).where(
                Record.save_id == save.id, Record.kind == "game_candidate", Record.deleted.is_(False),
                Record.data["source_key"].as_string() == f"new_sim:{game_id}",
            ).limit(1))
            if pending:
                tracker_household_id = household_matches.get(str(snapshot.get("household_id") or ""))
                payload = dict((pending.data or {}).get("payload") or {})
                refreshed_payload = {**payload, **snapshot}
                if tracker_household_id:
                    refreshed_payload["inferred_household_id"] = tracker_household_id
                if refreshed_payload != payload:
                    base = pending.version
                    pending.data = {**(pending.data or {}), "payload": refreshed_payload}
                    pending.version += 1
                    domain.journal(session, pending, "upsert", base)
                    save.revision += 1
                continue
            payload = {**snapshot, **clock.estimate_new_sim_birth(session, save, snapshot, save.global_day)}
            tracker_household_id = household_matches.get(str(snapshot.get("household_id") or ""))
            if tracker_household_id:
                payload["inferred_household_id"] = tracker_household_id
            candidate = Record(
                save_id=save.id, kind="game_candidate", label=item.get("name") or f"Game Sim {game_id}",
                global_day=save.global_day,
                data={"action":"new_baby" if item.get("is_baby") else "new_sim", "payload":payload,
                      "source_key":f"new_sim:{game_id}", "status":"pending", **snapshot},
            )
            session.add(candidate); session.flush(); domain.journal(session, candidate, "upsert", 0)
            candidates += 1
            continue
        household_members_linked += clock.connect_sim_to_game_household(
            session, save, existing, snapshot, household_matches,
        )
        changes = automation.reconcile_sim(session, save, existing, snapshot)
        if clock._store_game_portrait(session, save, existing, snapshot):
            portrait_updates += 1
        candidates += len(changes)
        updated += 1
        journal_entries.extend(snapshot.get("_history_entries") or [])
    if advanced:
        made = domain.schedule_rolls(session, save)
        journal_entries.append(f"The save-file clock advanced the tracker by {advanced} day(s); {made} obligation(s) were scheduled.")
    if linked:
        journal_entries.append(f"{linked} imported Sim(s) were matched to their game identities.")
    if households_created:
        journal_entries.append(f"{households_created} household(s) were created automatically from the game save.")
    if household_members_linked:
        journal_entries.append(f"{household_members_linked} Sim household assignment(s) were synchronized.")
    if candidates:
        journal_entries.append(f"{candidates} change(s) are ready for review in Automation Inbox.")
    if portrait_updates:
        journal_entries.append(f"{portrait_updates} embedded save-file portrait(s) were added or refreshed.")
    automation.session_journal(session, save, journal_entries, int(game_day or 0), hour, minute)
    save.revision += linked + candidates + updated + bool(advanced)
    return {"advanced":advanced, "linked":linked, "updated":updated, "candidates":candidates,
            "households_created":households_created, "households_updated":households_updated,
            "household_members_linked":household_members_linked,
            "portrait_updates":portrait_updates,
            "selected":len(selected_game_ids), "global_day":save.global_day}
