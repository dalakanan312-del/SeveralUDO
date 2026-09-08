"""One dynasty save, stable identities, and isolated branch checkpoints.

Inactive people remain frozen references. Hidden branch records preserve each
timeline and travel with the ordinary save export. No Sims game files change.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import event, inspect, select, update
from sqlalchemy.orm import Session, object_session

from .models import ChronicleSave, ClockLink, Record, Portrait

KEY = "infinite_decades"
KIND = "dynasty_branch"
FINISHED = {"extinct", "modern"}
SHADOWS = {KIND, "save_metadata", "clock_state", "clock_protocol_state", "clock_diagnostic", "game_candidate", "portrait_blob"}
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MARKERS = {"infinite_frozen", "infinite_frozen_global_day", "infinite_branch_id"}


class BranchFrozenError(ValueError):
    pass


def state(save):
    value = (getattr(save, "settings", None) or {}).get(KEY)
    return value if isinstance(value, dict) and value.get("schema_version") == 2 else {}


def enabled(save):
    """Existing dynasties predate the switch and remain enabled by default."""
    return bool(state(save)) and state(save).get("enabled", True) is not False


def frozen(save):
    return bool(state(save)) and (not enabled(save) or state(save).get("status") != "active")


def year(save, day=None):
    return int(save.start_year) + (int(save.global_day if day is None else day) - 1) // max(1, int(save.days_per_year))


def household_id(sim):
    return str((sim.data or {}).get("current_household_id") or (sim.data or {}).get("household_id") or "")


def alive(sim, save):
    data = sim.data or {}
    if sim.deleted or data.get("death_confirmed") or data.get("game_was_dead"): return False
    birth, death = data.get("birth_global_day"), data.get("death_global_day")
    return (birth is None or int(birth) <= save.global_day) and (death is None or int(death) > save.global_day)


def remap(value, mapping):
    if isinstance(value, dict): return {mapping.get(k, k): remap(v, mapping) for k, v in value.items()}
    if isinstance(value, list): return [remap(v, mapping) for v in value]
    if isinstance(value, str):
        return re.sub(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", lambda m: mapping.get(m.group(), m.group()), value)
    return value


def _clean_settings(values):
    from .backup_service import public_settings
    return {k: copy.deepcopy(v) for k, v in public_settings(values).items()
            if k != KEY and not k.startswith(("clock_", "sims3_save", "sims3_reader", "save_scan_", "game_save_"))}


def detached_settings(values, mapping=None):
    """A duplicate is an independent whole dynasty, not a second active branch."""
    result = remap(copy.deepcopy(values or {}), mapping or {})
    if (result.get(KEY) or {}).get("schema_version") == 2:
        result = {**_clean_settings(result), KEY: {**result[KEY], "game_ready": False, "epoch": uuid4().hex}}
    elif KEY in result:
        result["infinite_copy_origin"] = result.pop(KEY)
    return result


def pack_snapshot(payload):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_SNAPSHOT_BYTES: raise ValueError("This checkpoint is too large. No partial branch change was saved.")
    return {"encoding": "zlib-json-v1", "sha256": hashlib.sha256(raw).hexdigest(),
            "body": base64.b64encode(zlib.compress(raw, 6)).decode("ascii")}


def unpack_snapshot(value):
    try:
        if value.get("encoding") != "zlib-json-v1": raise ValueError()
        decoder = zlib.decompressobj()
        raw = decoder.decompress(base64.b64decode(value["body"], validate=True), MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES or not decoder.eof or decoder.unused_data: raise ValueError()
        if hashlib.sha256(raw).hexdigest() != value["sha256"]: raise ValueError()
        payload = json.loads(raw)
        if not isinstance(payload.get("records"), list) or not isinstance(payload.get("member_sim_ids"), list): raise ValueError()
        return payload
    except (KeyError, TypeError, ValueError, zlib.error) as exc:
        raise ValueError("The branch checkpoint is invalid. The current branch was left unchanged.") from exc


def copy_record_data(kind, data, mapping):
    result = remap(copy.deepcopy(data or {}), mapping)
    if kind == KIND:
        result["snapshot"] = pack_snapshot(remap(unpack_snapshot(data["snapshot"]), mapping))
    return result


def branches(session, save):
    return list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == KIND))) if save else []


def metadata(branch):
    return (branch.data or {}).get("meta", {}) if branch else {}


def active_branch(session, save):
    row = session.get(Record, state(save).get("active_branch_id")) if state(save) else None
    return row if row and row.save_id == save.id and row.kind == KIND else None


def _lock(session, save):
    session.flush()
    if session.get_bind().dialect.name == "sqlite":
        session.execute(update(ChronicleSave).where(ChronicleSave.id == save.id).values(revision=ChronicleSave.revision))
    session.scalar(select(ChronicleSave).where(ChronicleSave.id == save.id).with_for_update()
                   .execution_options(populate_existing=True))


@contextmanager
def branch_operation(session, save):
    previous = session.info.get("infinite_branch_operation")
    session.info["infinite_branch_operation"] = True
    try:
        yield
        from .sync import ensure_save_metadata
        ensure_save_metadata(session, save)
        session.flush()
    finally:
        session.info["infinite_branch_operation"] = previous


def _touch(session, row, data=None, *, deleted=None):
    from .domain import journal
    base = row.version or 0
    if data is not None: row.data = data
    if deleted is not None: row.deleted = deleted
    row.version = base + 1
    journal(session, row, "upsert", base)


def _set_state(save, **values):
    save.settings = {**(save.settings or {}), KEY: {**state(save), **values}}
    save.revision += 1


def _references(value, ids):
    if isinstance(value, dict): return set().union(*(_references(v, ids) for k, v in value.items() if k not in MARKERS), set())
    if isinstance(value, list): return set().union(*(_references(v, ids) for v in value), set())
    return {value} if isinstance(value, str) and value in ids else set()


def _belongs(row, chosen, sims, homes, home_ids):
    if row.kind in SHADOWS: return False
    if row.kind == "sim": return row.id in chosen
    if row.kind == "household": return row.id in homes
    data = row.data or {}
    owner = data.get("mother_id") if row.kind == "pregnancy" else data.get("sim_id")
    if owner in sims: return owner in chosen
    refs = _references(data, sims)
    if refs: return bool(refs & chosen)
    refs = _references(data, home_ids)
    return not refs or bool(refs & homes)


def snapshot(session, save, chosen=None, *, include_all=False):
    rows = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind.not_in(SHADOWS))))
    sims = {r.id for r in rows if r.kind == "sim"}
    chosen = set(chosen) if chosen is not None else {r.id for r in rows if r.kind == "sim" and not r.deleted}
    homes = {household_id(r) for r in rows if r.id in chosen}
    home_ids = {r.id for r in rows if r.kind == "household"}
    selected = [r for r in rows if (include_all or not (r.data or {}).get("infinite_frozen"))
                and (include_all or _belongs(r, chosen, sims, homes, home_ids))]
    preferences = _clean_settings(save.settings)
    if preferences.get("current_heir_id") not in chosen: preferences["current_heir_id"] = None
    if preferences.get("main_household_id") not in homes:
        preferences["main_household_id"] = next(iter(sorted(homes - {""})), None)
    return {"global_day": save.global_day, "start_year": save.start_year, "days_per_year": save.days_per_year,
            "pregnancy_days": save.pregnancy_days, "settings": preferences,
            "member_sim_ids": sorted(chosen), "records": [
                {"id": r.id, "kind": r.kind, "label": r.label, "global_day": r.global_day,
                 "data": {k: copy.deepcopy(v) for k, v in (r.data or {}).items() if k not in MARKERS},
                 "deleted": bool(r.deleted) and not (r.data or {}).get("infinite_frozen")} for r in selected],
            "portraits": [{"record_id": p.record_id, "stage": p.stage, "mime_type": p.mime_type,
                "image": base64.b64encode(p.image).decode("ascii")} for p in session.scalars(select(Portrait).where(
                    Portrait.save_id == save.id, Portrait.record_id.in_(chosen)))]}


def _new_branch(session, save, label, status, payload, game_save_name, parent=None):
    row = Record(id=uuid4().hex, save_id=save.id, kind=KIND, label=label.strip()[:160],
        global_day=payload["global_day"], deleted=True, version=0, data={})
    session.add(row)
    names = [r["label"] for r in payload["records"] if r["id"] in payload["member_sim_ids"]]
    _touch(session, row, {"meta": {"status": status, "parent_branch_id": parent,
        "split_global_day": payload["global_day"], "split_year": year(save, payload["global_day"]),
        "current_global_day": payload["global_day"], "seed_sim_ids": payload["member_sim_ids"],
        "seed_names": names, "game_save_name": game_save_name.strip()[:240],
        "created_at": datetime.now(timezone.utc).isoformat()}, "snapshot": pack_snapshot(payload)})
    session.flush()
    return row


def _update_branch(session, row, payload=None, **values):
    _touch(session, row, {**row.data, "meta": {**metadata(row), **values},
                         **({"snapshot": pack_snapshot(payload)} if payload is not None else {})})


def _freeze(session, row, day, owner):
    if (row.data or {}).get("infinite_frozen"): return
    _touch(session, row, {**(row.data or {}), "infinite_frozen": True,
        "infinite_frozen_global_day": day, "infinite_branch_id": owner}, deleted=True)


def _reset_game(session, save, *, preserve_candidates=False):
    link = session.scalar(select(ClockLink).where(ClockLink.save_id == save.id))
    if link:
        link.enabled = False
        link.game_anchor_day = link.tracker_anchor_day = None
        link.last_game_day = link.last_game_hour = link.last_game_minute = None
        link.last_seen_at = None
    reset_kinds = SHADOWS - {KIND, "save_metadata", "portrait_blob"}
    if preserve_candidates: reset_kinds.discard("game_candidate")
    for row in session.scalars(select(Record).where(Record.save_id == save.id,
            Record.kind.in_(reset_kinds))):
        if not row.deleted: _touch(session, row, deleted=True)


def _restore_working_view(session, save, target, payload):
    from .backup_service import public_settings
    current = {r.id: r for r in session.scalars(select(Record).where(Record.save_id == save.id, Record.kind.not_in(SHADOWS)))}
    restored_ids = {r["id"] for r in payload["records"]}
    old_branch = state(save).get("active_branch_id")
    for row in current.values():
        if row.id not in restored_ids: _freeze(session, row, save.global_day, (row.data or {}).get("infinite_branch_id") or old_branch)
    for entry in payload["records"]:
        row = current.get(entry["id"])
        if row is None:
            row = Record(id=entry["id"], save_id=save.id, kind=entry["kind"], version=0, data={})
            session.add(row)
        elif row.kind != entry["kind"]: raise ValueError("A checkpoint record no longer matches its original type.")
        row.label, row.global_day = entry["label"], entry.get("global_day")
        _touch(session, row, {**entry["data"], "infinite_branch_id": target.id}, deleted=bool(entry.get("deleted")))
    for photo in payload.get("portraits", []):
        row = session.scalar(select(Portrait).where(Portrait.save_id == save.id,
            Portrait.record_id == photo["record_id"], Portrait.stage == photo["stage"]))
        raw = base64.b64decode(photo["image"], validate=True)
        if row: row.image, row.mime_type = raw, photo["mime_type"]
        else: session.add(Portrait(save_id=save.id, record_id=photo["record_id"], stage=photo["stage"],
            mime_type=photo["mime_type"], image=raw, source="dynasty-checkpoint"))
    private = {k: v for k, v in (save.settings or {}).items() if k not in public_settings(save.settings)}
    meta = state(save)
    save.settings = {**payload["settings"], **private, KEY: meta}
    save.global_day, save.pregnancy_days = payload["global_day"], payload["pregnancy_days"]
    _set_state(save, active_branch_id=target.id, branch_name=target.label, status="active",
        split_global_day=metadata(target)["split_global_day"], game_ready=False, epoch=uuid4().hex)
    _reset_game(session, save)


def _selected(session, save, ids):
    unique = set(ids)
    sims = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False))))
    selected = [s for s in sims if s.id in unique]
    if not selected or len(selected) != len(unique) or not any(alive(s, save) for s in selected):
        raise ValueError("Choose at least one living Sim; every selected Sim must belong to the active line.")
    return sims, selected


def enable(session, save, selected_ids, label, modern_year, game_save_name):
    _lock(session, save)
    if state(save): raise ValueError("Infinite Decades is already enabled for this dynasty.")
    sims, selected = _selected(session, save, selected_ids)
    if not label.strip() or not game_save_name.strip(): raise ValueError("Name the branch and its matching in-game Save As checkpoint.")
    if modern_year <= year(save) or modern_year > 9999: raise ValueError("Choose a modern-day target later than the current historical year.")
    with branch_operation(session, save):
        from .backup_service import create_snapshot
        create_snapshot(session, save, "infinite:before-enable", force=True)
        initial = snapshot(session, save, {s.id for s in sims}, include_all=True)
        root = _new_branch(session, save, "Starting world", "archive", initial, game_save_name)
        payload = snapshot(session, save, {s.id for s in selected})
        first = _new_branch(session, save, label, "active", payload, game_save_name, root.id)
        _set_state(save, schema_version=2, enabled=True, modern_year=modern_year, starting_branch_id=root.id)
        _restore_working_view(session, save, first, payload)
        for row in session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "sim")):
            if (row.data or {}).get("infinite_frozen"): _touch(session, row, {**row.data, "infinite_branch_id": root.id})
    return first


def set_enabled(session, save, value):
    """Pause/resume this dynasty without unfreezing or merging its timelines."""
    if not isinstance(value, bool): raise ValueError("Choose On or Off for Infinite Decades.")
    _lock(session, save)
    if not state(save):
        if value: raise ValueError("Choose a starting family before enabling Infinite Decades.")
        return
    if enabled(save) == value: return
    with branch_operation(session, save):
        if not value and state(save).get("status") == "active":
            _update_branch(session, active_branch(session, save), snapshot(session, save), current_global_day=save.global_day)
        _set_state(save, enabled=value, game_ready=False, epoch=uuid4().hex)
        # A pause is not a branch change: keep pending inbox decisions.
        _reset_game(session, save, preserve_candidates=True)


def capture(session, save, selected_ids, label, game_save_name):
    _lock(session, save)
    if frozen(save) or not state(save): raise ValueError("Capture a split while playing an active branch.")
    sims, selected = _selected(session, save, selected_ids)
    selected_ids = {s.id for s in selected}
    if not label.strip() or not game_save_name.strip(): raise ValueError("Name the branch and record its matching in-game checkpoint.")
    if not any(alive(s, save) for s in sims if s.id not in selected_ids): raise ValueError("Leave a living Sim in the current branch. Select only departing family members.")
    with branch_operation(session, save):
        parent = active_branch(session, save)
        payload = snapshot(session, save, selected_ids)
        child = _new_branch(session, save, label, "waiting", payload, game_save_name, parent.id)
        child_ids = {r["id"] for r in payload["records"]}
        remaining = snapshot(session, save, {s.id for s in sims if s.id not in selected_ids})
        remaining_ids = {r["id"] for r in remaining["records"]}
        for row in session.scalars(select(Record).where(Record.save_id == save.id, Record.id.in_(child_ids - remaining_ids))):
            _freeze(session, row, save.global_day, child.id)
        save.settings = {**save.settings, **{k: remaining["settings"].get(k) for k in ("current_heir_id", "main_household_id")}}
        _update_branch(session, parent, current_global_day=save.global_day)
        save.revision += 1
    return child


def capture_starting(session, save, selected_ids, label, game_save_name):
    """Register an initially paused household without moving the active clock."""
    _lock(session, save)
    if not enabled(save): raise ValueError("Turn Infinite Decades on before registering another branch.")
    root = session.get(Record, state(save).get("starting_branch_id"))
    if not root or root.save_id != save.id: raise ValueError("The starting-world checkpoint is unavailable.")
    initial = unpack_snapshot(root.data["snapshot"])
    chosen = set(selected_ids)
    eligible = {r.id for r in session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "sim"))
                if r.id in initial["member_sim_ids"] and (r.data or {}).get("infinite_frozen") and (r.data or {}).get("infinite_branch_id") == root.id}
    if not chosen or not chosen <= eligible: raise ValueError("Choose unplayed starting Sims; already assigned family members cannot be reused.")
    if not label.strip() or not game_save_name.strip(): raise ValueError("Name the branch and its matching in-game checkpoint.")
    from types import SimpleNamespace
    rows = [SimpleNamespace(**entry) for entry in initial["records"]]
    selected = [r for r in rows if r.id in chosen]
    if not any(alive(r, SimpleNamespace(global_day=initial["global_day"])) for r in selected): raise ValueError("The starting branch needs a living Sim.")
    homes = {household_id(r) for r in selected}
    sims = {r.id for r in rows if r.kind == "sim"}
    home_ids = {r.id for r in rows if r.kind == "household"}
    payload = {**initial, "member_sim_ids": sorted(chosen),
        "records": [entry for entry, row in zip(initial["records"], rows) if _belongs(row, chosen, sims, homes, home_ids)],
        "portraits": [p for p in initial["portraits"] if p["record_id"] in chosen]}
    with branch_operation(session, save):
        child = _new_branch(session, save, label, "waiting", payload, game_save_name, root.id)
        for sim_id in chosen:
            row = session.get(Record, sim_id)
            _touch(session, row, {**row.data, "infinite_branch_id": child.id})
        save.revision += 1
    return child


def finish_reason(session, save):
    if year(save) >= int(state(save).get("modern_year", 9999)): return "modern"
    sims = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False))))
    return "extinct" if sims and not any(alive(s, save) for s in sims) else None


def finish(session, save):
    _lock(session, save)
    if not state(save) or frozen(save): raise ValueError("Only the active branch can be completed.")
    reason = finish_reason(session, save)
    if not reason: raise ValueError("This branch still has living Sims and has not reached modern day.")
    with branch_operation(session, save):
        row = active_branch(session, save)
        _update_branch(session, row, snapshot(session, save), status=reason, current_global_day=save.global_day,
            finished_global_day=save.global_day, finished_year=year(save))
        _set_state(save, status=reason, game_ready=False, epoch=uuid4().hex)
        _reset_game(session, save)
    return reason


def next_branch(family):
    waiting = [r for r in family if metadata(r).get("status") == "waiting"]
    return max(waiting, key=lambda r: (metadata(r)["split_year"], metadata(r)["split_global_day"],
               metadata(r)["created_at"], r.id), default=None)


def activate_next(session, save):
    _lock(session, save)
    if not enabled(save): raise ValueError("Turn Infinite Decades on before playing another branch.")
    if not state(save) or not frozen(save): raise ValueError("Finish the active branch before returning to another split.")
    target = next_branch(branches(session, save))
    if not target: raise ValueError("No unplayed branches remain.")
    payload = unpack_snapshot(target.data["snapshot"])
    with branch_operation(session, save):
        _restore_working_view(session, save, target, payload)
        _update_branch(session, target, status="active")
    return target


def confirm_game(session, save):
    _lock(session, save)
    if not state(save) or frozen(save): raise ValueError("Select the active branch first.")
    with branch_operation(session, save): _set_state(save, game_ready=True)


def import_allowed(save):
    return not state(save) or (not frozen(save) and state(save).get("game_ready") is True)


def lock_current_branch(session, save):
    if state(save): _lock(session, save)


def filter_members(session, save, members):
    if not state(save): return members
    sims = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "sim")))
    active = [s for s in sims if not s.deleted]
    ids = {str(s.data.get("game_sim_id")) for s in active if s.data.get("game_sim_id")}
    blocked = {str(s.data.get("game_sim_id")) for s in sims if s.deleted and s.data.get("game_sim_id")}
    names = {s.label.casefold().strip() for s in active if not s.data.get("game_sim_id")}
    houses = {str(s.data.get("game_household_id") or "") for s in active}
    homes = {household_id(s) for s in active}
    for home in session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "household")):
        if home.id in homes: houses.add(str(home.data.get("game_household_id") or ""))
    houses.discard("")
    accepted = []
    for item in members:
        if not isinstance(item, dict): continue
        gid = str(item.get("game_sim_id") or "")
        if gid in blocked: continue
        name = str(item.get("name") or item.get("sim_name") or " ".join(str(item.get(k) or "") for k in ("first_name", "last_name"))).casefold().strip()
        home = str(item.get("household_id") or item.get("game_household_id") or "")
        parents = {str(p) for p in item.get("parent_game_sim_ids") or []}
        if gid in ids or name in names or home in houses or parents.intersection(ids): accepted.append(copy.deepcopy(item))
    allowed = {str(i.get("game_sim_id") or "") for i in accepted}
    for item in accepted:
        item["household_member_game_ids"] = [gid for gid in item.get("household_member_game_ids") or [] if str(gid) in allowed]
    return accepted


def filter_scan(session, save, scan):
    if not state(save): return scan
    _lock(session, save)
    if not import_allowed(save): raise ValueError("Infinite Decades is paused or awaiting confirmation of the matching in-game branch save.")
    result = copy.deepcopy(scan)
    result["sims"] = filter_members(session, save, result.get("sims") or [])
    allowed = {str(s.get("game_sim_id")) for s in result["sims"]}
    homes = {str(s.get("game_household_id") or "") for s in result["sims"]}
    result["households"] = [h for h in result.get("households") or [] if str(h.get("game_household_id") or "") in homes]
    for home in result["households"]:
        home["member_game_ids"] = [gid for gid in home.get("member_game_ids") or [] if str(gid) in allowed]
    result["population_complete"] = False
    return result


def guard_request(request, save):
    if not state(save) or request.method in {"GET", "HEAD", "OPTIONS"}: return
    owner = object_session(save)
    if owner: _lock(owner, save)
    from fastapi import HTTPException
    path = request.url.path
    if path == "/saves/select" or path in {f"/saves/{save.id}/snapshot", f"/saves/{save.id}/duplicate", f"/saves/{save.id}/rename", f"/saves/{save.id}/delete"}: return
    epoch = request.query_params.get("_dynasty_epoch") or request.headers.get("X-Dynasty-Epoch")
    if epoch != state(save).get("epoch"): raise HTTPException(409, "The dynasty branch changed, or this page is out of date. Refresh before making changes.")
    if not frozen(save) or path.startswith("/infinite/"): return
    raise HTTPException(409, "This dynasty is paused or its branch is complete. Open Infinite Decades to continue.")


@event.listens_for(Session, "before_flush")
def _protect_frozen(session, _context, _instances):
    if session.info.get("infinite_branch_operation"): return
    for item in list(session.new) + list(session.dirty) + list(session.deleted):
        if not isinstance(item, (ChronicleSave, Record, Portrait, ClockLink)): continue
        save = item if isinstance(item, ChronicleSave) else session.get(ChronicleSave, item.save_id)
        if not save: continue
        if isinstance(item, ChronicleSave) and item not in session.new:
            changed = inspect(item).attrs.settings.history
            previous = (changed.deleted[0] or {}).get(KEY) if changed.deleted else None
            if previous and previous != state(save): raise BranchFrozenError("Use Infinite Decades controls to change the active branch.")
        if not state(save) or (isinstance(item, ChronicleSave) and item in session.new): continue
        if isinstance(item, ChronicleSave):
            if item in session.deleted: continue
            if any(inspect(item).attrs[field].history.has_changes() for field in ("start_year", "days_per_year")):
                raise BranchFrozenError("Dynasty calendars are fixed at their split points. Set the year length before enabling Infinite Decades.")
            if item.global_day < int(state(save).get("split_global_day", 1)): raise BranchFrozenError("Use Play next branch to go back to a recorded split.")
        if isinstance(item, Record) and item.kind == "save_metadata": continue
        if isinstance(item, Record) and (item.kind == KIND or item in session.deleted): raise BranchFrozenError("Branch history is protected. Use Infinite Decades controls or archive ordinary records instead.")
        if frozen(save):
            if isinstance(item, ChronicleSave) and not any(inspect(item).attrs[field].history.has_changes() for field in ("global_day", "start_year", "days_per_year", "pregnancy_days", "settings")): continue
            raise BranchFrozenError("Paused or completed dynasty history cannot be changed.")
        if isinstance(item, Portrait):
            sim = session.get(Record, item.record_id)
            if sim and (sim.data or {}).get("infinite_frozen"): raise BranchFrozenError("This portrait belongs to a paused family branch.")
        if isinstance(item, Record):
            previous = inspect(item).attrs.data.history.deleted
            if (item.data or {}).get("infinite_frozen") or any((d or {}).get("infinite_frozen") for d in previous): raise BranchFrozenError("This record belongs to a paused family branch.")
            if item.kind not in SHADOWS: item.data = {**(item.data or {}), "infinite_branch_id": state(save)["active_branch_id"]}
