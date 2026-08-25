from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ChronicleSave, ClockLink, Record
from .domain import AGING_STAGE_OFFSETS, CLOSED_ILLNESSES, journal, schedule_rolls, sync_generations
from . import automation, game_metadata, telemetry, sync, notifications, storyline


CLOSED_PREGNANCIES = {"delivered", "miscarriage", "stillbirth", "cancelled", "canceled", "ended", "closed"}

_GAME_STAGE_ORDER = ("newborn", "infant", "toddler", "child", "teen", "youngadult", "adult", "elder")
_STAGE_LABELS = {
    "baby":"newborn", "newborn":"newborn", "infant":"infant", "toddler":"toddler",
    "child":"child", "preteen":"preteen", "teen":"teen", "youngadult":"youngadult",
    "adult":"adult", "elder":"elder",
}


def _identity_text(value) -> str:
    return " ".join(str(value or "").casefold().split())


def _stage_key(value) -> str:
    text = re.sub(r"[^a-z]", "", str(value or "").casefold())
    if text.startswith("age"):
        text = text[3:]
    return _STAGE_LABELS.get(text, text)


def estimate_new_sim_birth(session: Session, save: ChronicleSave, snapshot: dict, detected_global_day: int | None = None) -> dict:
    """Estimate a preexisting Sim's tracker birth day from game age telemetry."""
    detected = int(snapshot.get("detected_tracker_global_day") or detected_global_day or save.global_day)
    for key in ("birth_global_day", "game_birth_global_day"):
        try:
            exact_birth = int(snapshot.get(key))
        except (TypeError, ValueError):
            continue
        return {
            "estimated_birth_global_day":exact_birth, "estimated_age_days":detected-exact_birth,
            "birth_estimate_source":"Clock Sync reported birth day", "birth_estimate_precision":"reported-birth-day",
            "birth_estimate_stage":_stage_key(snapshot.get("age_stage")), "birth_estimate_detected_global_day":detected,
            "estimated_birth_global_day_range_start":exact_birth, "estimated_birth_global_day_range_end":exact_birth,
        }
    reported_age = None
    for key in ("age_days", "current_age_days", "sim_age_days"):
        try:
            reported_age = max(0, int(float(snapshot.get(key))))
        except (TypeError, ValueError):
            continue
        break
    stage = _stage_key(snapshot.get("age_stage"))
    if reported_age is not None:
        birth = detected - reported_age
        return {
            "estimated_birth_global_day":birth, "estimated_age_days":reported_age,
            "birth_estimate_source":f"Clock Sync reported age: {reported_age} days", "birth_estimate_precision":"reported-age-days",
            "birth_estimate_stage":stage, "birth_estimate_detected_global_day":detected,
            "estimated_birth_global_day_range_start":birth, "estimated_birth_global_day_range_end":birth,
        }
    if stage not in _STAGE_LABELS.values():
        return {}
    starts = {
        "newborn":int(AGING_STAGE_OFFSETS.get("newborn", 0)), "infant":int(AGING_STAGE_OFFSETS.get("infant", 1)),
        "toddler":int(AGING_STAGE_OFFSETS.get("toddler", 4)), "child":int(AGING_STAGE_OFFSETS.get("child", 20)),
        "preteen":int(AGING_STAGE_OFFSETS.get("preteen", 40)), "teen":int(AGING_STAGE_OFFSETS.get("teen", 52)),
        "youngadult":int(AGING_STAGE_OFFSETS.get("young adult", 72)), "adult":int(AGING_STAGE_OFFSETS.get("adult", 160)),
        "elder":int(AGING_STAGE_OFFSETS.get("elder death-age rng", 240)),
    }
    rules = session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll_rule", Record.deleted.is_(False),
    ))
    for rule in rules:
        data = rule.data or {}
        if not bool(data.get("active", True)) or data.get("age_days") in (None, ""):
            continue
        key = _stage_key(rule.label)
        if key in starts or key.startswith("elder"):
            starts["elder" if key.startswith("elder") else key] = max(0, int(data["age_days"]))
    start_age = starts[stage]
    if stage == "preteen":
        end_age = starts["teen"]
    elif stage == "elder":
        end_age = max(start_age + 1, int((save.settings or {}).get("elder_max_age_days", 320)))
    else:
        position = _GAME_STAGE_ORDER.index(stage)
        end_age = starts[_GAME_STAGE_ORDER[position + 1]]
    end_age = max(start_age + 1, end_age)
    progress = None
    for key in ("age_progress_percentage", "age_progress_percent", "life_stage_progress"):
        try:
            value = float(snapshot.get(key))
        except (TypeError, ValueError):
            continue
        progress = max(0.0, min(1.0, value / 100.0 if value > 1 else value)); break
    if progress is None:
        age_days = start_age
        precision = "life-stage-minimum"
        source = f"Clock Sync life stage {stage.replace('youngadult','young adult').title()}; using its editable minimum age"
    else:
        age_days = start_age + min(end_age - start_age - 1, math.floor(progress * (end_age - start_age)))
        precision = "life-stage-progress"
        source = f"Clock Sync life stage {stage.replace('youngadult','young adult').title()} at {round(progress * 100)}%"
    return {
        "estimated_birth_global_day":detected-age_days, "estimated_age_days":age_days,
        "birth_estimate_source":source, "birth_estimate_precision":precision,
        "birth_estimate_stage":stage, "birth_estimate_stage_start_age":start_age, "birth_estimate_stage_end_age":end_age-1,
        "birth_estimate_detected_global_day":detected,
        "estimated_birth_global_day_range_start":detected-(end_age-1),
        "estimated_birth_global_day_range_end":detected-start_age,
    }


def imported_sim_match(session: Session, save: ChronicleSave, snapshot: dict) -> Record | None:
    """Return one unlinked imported Sim only when first and last names agree exactly."""
    first = _identity_text(snapshot.get("first_name"))
    last = _identity_text(snapshot.get("last_name"))
    if not first or not last:
        return None
    candidates = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
    )))
    matches = [sim for sim in candidates if not str(sim.data.get("game_sim_id") or "").strip()
               and _identity_text(sim.data.get("first_name")) == first
               and _identity_text(sim.data.get("last_name")) == last]
    return matches[0] if len(matches) == 1 else None


def attach_game_identity(session: Session, save: ChronicleSave, sim: Record, snapshot: dict) -> int:
    """Attach a stable game ID and close every matching new-Sim candidate."""
    game_id = str(snapshot.get("game_sim_id") or "").strip()
    if not game_id:
        return 0
    base = sim.version
    sim.data = {**sim.data, "game_sim_id": game_id,
                "game_household_id": snapshot.get("household_id"),
                "game_household_name": snapshot.get("household_name") or sim.data.get("game_household_name"),
                "game_age_stage": snapshot.get("age_stage") or sim.data.get("game_age_stage")}
    sim.version += 1; journal(session, sim, "upsert", base)
    linked = 0
    pending = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "game_candidate", Record.deleted.is_(False),
        Record.data["source_key"].as_string() == f"new_sim:{game_id}",
        Record.data["status"].as_string() == "pending",
    )))
    for item in pending:
        item_base = item.version
        item.data = {**item.data, "status": "linked", "linked_sim_id": sim.id}
        item.version += 1; journal(session, item, "upsert", item_base); linked += 1
    save.revision += linked + 1
    return linked


def _game_illnesses(session: Session, save: ChronicleSave, sim: Record, snapshot: dict) -> tuple[int, int]:
    """Reconcile guarded game detections without trusting optional-mod availability."""
    if not snapshot.get("illness_scan_supported", False):
        return 0, 0
    incoming = {}
    for item in snapshot.get("illnesses") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        key = str(item.get("source_key") or name).strip().casefold()
        if name and key:
            incoming[key] = {**item, "name": name, "source_key": key}
    tracked = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "illness", Record.deleted.is_(False),
        Record.data["sim_id"].as_string() == sim.id,
    )))
    active_by_key = {
        str(item.data.get("source_key") or "").casefold(): item for item in tracked
        if item.data.get("source") == "game" and str(item.data.get("status") or "active").casefold() not in CLOSED_ILLNESSES
    }
    created = ended = 0
    for key, item in incoming.items():
        if key in active_by_key:
            record = active_by_key[key]
            old = dict(record.data or {})
            updates = {
                "illness_name": item["name"],
                "severity": item.get("severity") or old.get("severity") or "Unrated",
                "contagious": bool(item.get("contagious", old.get("contagious", False))),
                "provider": item.get("provider") or old.get("provider") or "game",
                "last_detected_global_day": save.global_day,
            }
            if any(old.get(field) != value for field, value in updates.items()):
                base = record.version
                record.label = f"{sim.label} — {item['name']}"
                record.data = {**old, **updates}
                record.version += 1
                journal(session, record, "upsert", base)
                save.revision += 1
            continue
        data = {
            "sim_id": sim.id, "sim_name": sim.label, "illness_name": item["name"],
            "onset_global_day": save.global_day, "end_global_day": None, "status": "Active",
            "severity": item.get("severity") or "Unrated", "contagious": bool(item.get("contagious", False)),
            "treatment": "", "outcome": "", "notes": "Detected automatically in The Sims 4.",
            "source": "game", "source_key": key, "provider": item.get("provider") or "game",
            "last_detected_global_day": save.global_day,
        }
        record = Record(save_id=save.id, kind="illness", label=f"{sim.label} — {item['name']}", global_day=save.global_day, data=data)
        session.add(record); session.flush(); journal(session, record, "upsert", 0); created += 1
        notifications.record(session,save,"illness",f"Illness detected: {item['name']}",
                             f"{sim.label} is now recorded with {item['name']}.","/p/illnesses",f"illness:{record.id}:start")
    for key, record in active_by_key.items():
        if key in incoming:
            continue
        base = record.version; data = dict(record.data or {})
        data.update({"status": "Recovered", "end_global_day": save.global_day, "outcome": "No longer detected in game"})
        record.data = data; record.version += 1; journal(session, record, "upsert", base); ended += 1
        notifications.record(session,save,"illness",f"Recovery detected: {sim.label}",
                             f"{record.data.get('illness_name') or record.label} is no longer detected.","/p/illnesses",f"illness:{record.id}:end:{save.global_day}")
    return created, ended


def _active_pregnancy(session: Session, save: ChronicleSave, mother_id: str) -> Record | None:
    records = session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "pregnancy", Record.deleted.is_(False),
        Record.data["mother_id"].as_string() == mother_id,
    ).order_by(Record.global_day.desc()))
    return next((record for record in records
                 if str((record.data or {}).get("status") or "active").casefold() not in CLOSED_PREGNANCIES), None)


def _newborn_parent_contexts(session: Session, save: ChronicleSave, members: list[dict],
                             tracked_by_game_id: dict[str, Record]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Associate newborns with the correct pregnancy, including simultaneous births."""
    transitions: list[tuple[Record, Record, dict]] = []
    for snapshot in members:
        game_id = str(snapshot.get("game_sim_id") or "").strip()
        mother = tracked_by_game_id.get(game_id)
        if not mother or not bool((mother.data or {}).get("game_was_pregnant")) or bool(snapshot.get("is_pregnant")):
            continue
        pregnancy = _active_pregnancy(session, save, mother.id)
        if pregnancy:
            transitions.append((mother, pregnancy, snapshot))
    contexts: dict[str, dict] = {}
    newborns_by_mother: dict[str, list[dict]] = {}
    for newborn in members:
        newborn_id = str(newborn.get("game_sim_id") or "").strip()
        if not newborn.get("is_baby") or not newborn_id or newborn_id in tracked_by_game_id:
            continue
        parent_game_ids = {str(value or "").strip() for value in (newborn.get("parent_game_sim_ids") or []) if value}
        candidates = [entry for entry in transitions if str((entry[0].data or {}).get("game_sim_id") or "") in parent_game_ids]
        newborn_household = str(newborn.get("household_id") or "")
        if not candidates and newborn_household:
            candidates = [entry for entry in transitions if str(entry[2].get("household_id") or "") == newborn_household]
        if not candidates and len(transitions) == 1:
            candidates = transitions
        if len(candidates) != 1:
            continue
        mother, pregnancy, _ = candidates[0]
        data = pregnancy.data or {}
        father = session.get(Record, str(data.get("father_id") or "")) if data.get("father_id") else None
        if father and (father.save_id != save.id or father.kind != "sim" or father.deleted):
            father = None
        contexts[newborn_id] = {
            "inferred_mother_id": mother.id,
            "inferred_mother_name": mother.label,
            "inferred_father_id": father.id if father else None,
            "inferred_father_name": father.label if father else str(data.get("father_name") or ""),
            "inferred_household_id": (mother.data or {}).get("current_household_id"),
            "pregnancy_id": pregnancy.id,
            "parent_match_source": "pregnancy completed in the same Clock Sync report",
            "parent_match_confidence": "exact",
        }
        newborns_by_mother.setdefault(mother.id, []).append({
            "game_sim_id": newborn_id,
            "name": " ".join(part for part in (
                str(newborn.get("first_name") or "").strip(),
                str(newborn.get("last_name") or "").strip(),
            ) if part),
        })
    return contexts, newborns_by_mother


def _reported_household_name(members: list[dict], default_name: str = "") -> str:
    names = [str(item.get("household_name") or "").strip() for item in members]
    names = [name for name in names if name]
    if names:
        return max(set(names), key=lambda value: (names.count(value), len(value), value))
    if default_name:
        return str(default_name).strip()
    surnames = {str(item.get("last_name") or "").strip() for item in members if item.get("last_name")}
    if len(surnames) == 1:
        return f"{next(iter(surnames))} Household"
    game_id = str((members[0] if members else {}).get("household_id") or "").strip()
    return f"Game Household {game_id[-8:]}" if game_id else "Detected Household"


def connect_sim_to_game_household(
    session: Session, save: ChronicleSave, sim: Record, snapshot: dict,
    household_matches: dict[str, str],
) -> int:
    """Apply an unambiguous game household link without creating a review item."""
    game_household_id = str(snapshot.get("household_id") or "").strip()
    tracker_household_id = household_matches.get(game_household_id)
    if not game_household_id or not tracker_household_id:
        return 0
    household = session.get(Record, tracker_household_id)
    if not household or household.save_id != save.id or household.kind != "household" or household.deleted:
        return 0
    household_name = str(snapshot.get("household_name") or household.label).strip()
    updates = {
        "current_household_id": household.id,
        "game_household_id": game_household_id,
        "game_household_name": household_name,
        "game_household_link_source": "automatic game detection",
    }
    data = dict(sim.data or {})
    if all(data.get(key) == value for key, value in updates.items()):
        return 0
    base = sim.version
    sim.data = {**data, **updates}
    sim.version += 1
    journal(session, sim, "upsert", base)
    save.revision += 1
    return 1


def sync_game_households(
    session: Session, save: ChronicleSave, members: list[dict],
    tracked_by_game_id: dict[str, Record], *, default_name: str = "",
    source: str = "Clock Sync",
) -> tuple[dict[str, str], int, int, int]:
    """Find or create reported households and connect every already-known member.

    The Sims household ID is the durable identity. Existing manual households are
    reused when their members or unique name establish a safe match. Reports are
    intentionally additive: a partial report never removes an absent member.
    """
    grouped: dict[str, list[dict]] = {}
    for member in members:
        game_id = str(member.get("household_id") or "").strip()
        if game_id:
            grouped.setdefault(game_id, []).append(member)
    if not grouped:
        return {}, 0, 0, 0

    households = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "household", Record.deleted.is_(False),
    )))
    by_id = {item.id: item for item in households}
    by_game_id = {
        str((item.data or {}).get("game_household_id") or "").strip(): item
        for item in households if str((item.data or {}).get("game_household_id") or "").strip()
    }
    matches: dict[str, str] = {}
    created = updated = linked = 0
    only_name = default_name if len(grouped) == 1 else ""

    for game_household_id, reported_members in grouped.items():
        name = _reported_household_name(reported_members, only_name)
        household = by_game_id.get(game_household_id)
        was_created = False
        if not household:
            member_choices: dict[str, int] = {}
            for member in reported_members:
                tracked = tracked_by_game_id.get(str(member.get("game_sim_id") or "").strip())
                current_id = str((tracked.data or {}).get("current_household_id") or "") if tracked else ""
                current = by_id.get(current_id)
                current_game_id = str((current.data or {}).get("game_household_id") or "").strip() if current else ""
                if current and current_game_id in {"", game_household_id}:
                    member_choices[current.id] = member_choices.get(current.id, 0) + 1
            if member_choices:
                household = sorted(
                    (by_id[item_id] for item_id in member_choices),
                    key=lambda item: (-member_choices[item.id], _identity_text(item.label), item.id),
                )[0]
            else:
                name_matches = [item for item in households
                                if _identity_text(item.label) == _identity_text(name)
                                and str((item.data or {}).get("game_household_id") or "").strip() in {"", game_household_id}]
                if len(name_matches) == 1:
                    household = name_matches[0]
        if not household:
            household = Record(
                save_id=save.id, kind="household", label=name, global_day=save.global_day,
                data={
                    "household_name": name, "branch_type": "Main", "active": True,
                    "start_global_day": None, "automatically_created": True,
                    "automation_source": source, "first_detected_global_day": save.global_day,
                    "notes": f"Created automatically from {source}.",
                },
            )
            session.add(household)
            session.flush()
            households.append(household)
            by_id[household.id] = household
            created += 1
            was_created = True

        head_game_id = next((str(item.get("game_sim_id") or "").strip() for item in reported_members
                             if item.get("is_household_head")), "")
        head = tracked_by_game_id.get(head_game_id) if head_game_id else None
        world = next((str(item.get("world_name") or "").strip() for item in reported_members if item.get("world_name")), "")
        lot = next((str(item.get("lot_name") or "").strip() for item in reported_members if item.get("lot_name")), "")
        funds = next((item.get("household_funds") for item in reported_members if item.get("household_funds") is not None), None)
        game_member_ids = sorted({str(item.get("game_sim_id") or "").strip() for item in reported_members
                                  if item.get("game_sim_id")})
        data = dict(household.data or {})
        metadata = {
            "game_household_id": game_household_id,
            "game_household_name": name,
            "last_game_world": world or data.get("last_game_world"),
            "last_game_lot": lot or data.get("last_game_lot"),
            "last_game_funds": funds if funds is not None else data.get("last_game_funds"),
            "last_reported_game_member_ids": game_member_ids,
            "last_detected_global_day": save.global_day,
            "active": True,
        }
        if head:
            metadata["head_sim_id"] = head.id
        new_data = {**data, **metadata}
        new_label = name if data.get("automatically_created") and name else household.label
        if was_created:
            household.data = new_data
            household.label = new_label
            journal(session, household, "upsert", 0)
        elif household.data != new_data or household.label != new_label:
            base = household.version
            household.data = new_data
            household.label = new_label
            household.version += 1
            journal(session, household, "upsert", base)
            updated += 1
        matches[game_household_id] = household.id
        by_game_id[game_household_id] = household

    save.revision += created + updated
    for member in members:
        sim = tracked_by_game_id.get(str(member.get("game_sim_id") or "").strip())
        if sim:
            linked += connect_sim_to_game_household(session, save, sim, member, matches)
    return matches, created, updated, linked


def receive(session: Session, link: ClockLink, report: dict) -> dict:
    save = session.get(ChronicleSave, link.save_id)
    game_day = int(report["game_day"])
    hour = max(0, min(23, int(report.get("hour", report.get("game_hour", 0)))))
    minute = max(0, min(59, int(report.get("minute", report.get("game_minute", 0)))))
    if link.game_anchor_day is None:
        link.game_anchor_day = game_day
        link.tracker_anchor_day = save.global_day
    target = int(link.tracker_anchor_day + max(0, game_day - link.game_anchor_day))
    day_advanced = target > save.global_day
    if target > save.global_day:
        save.global_day = target
        save.revision += 1
    link.last_game_day, link.last_game_hour, link.last_game_minute = game_day, hour, minute
    link.last_seen_at = datetime.now(timezone.utc)
    candidates = []; illnesses_created = illnesses_ended = 0; journal_entries = []
    members = list(report.get("household_members", report.get("household_sims", [])) or [])
    incoming_ids = {str(item.get("game_sim_id") or "") for item in members if item.get("game_sim_id")}
    tracked = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
    )))
    tracked_by_game_id = {
        str(record.data.get("game_sim_id") or "").strip(): record for record in tracked
        if str(record.data.get("game_sim_id") or "").strip()
    }
    illness_signatures = [dict(item.data or {}) for item in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "illness_signature", Record.deleted.is_(False),
    ))]
    known_ids = incoming_ids.intersection(tracked_by_game_id)
    household_matches, households_created, households_updated, household_members_linked = sync_game_households(
        session, save, members, tracked_by_game_id,
        default_name=str(report.get("household_name") or ""),
    )
    journal_entries.extend(telemetry.capture_household_finances(
        session, save, members, tracked_by_game_id,
        {"detected_game_day": game_day, "detected_game_hour": hour, "detected_game_minute": minute},
    ))
    detected_newborns = [item for item in members if item.get("is_baby") and str(item.get("game_sim_id") or "") not in known_ids]
    newborn_contexts, newborns_by_mother = _newborn_parent_contexts(
        session, save, members, tracked_by_game_id,
    ) if detected_newborns else ({}, {})
    for sim in members:
        game_id = str(sim.get("game_sim_id") or "")
        if not game_id:
            continue
        sim = game_metadata.enrich_illness_snapshot(dict(sim), illness_signatures)
        existing = tracked_by_game_id.get(game_id)
        if not existing:
            existing = imported_sim_match(session, save, sim)
            if existing:
                attach_game_identity(session, save, existing, {**sim, "household_name": sim.get("household_name") or report.get("household_name", "")})
                tracked_by_game_id[game_id] = existing
                household_members_linked += connect_sim_to_game_household(
                    session, save, existing, sim, household_matches,
                )
        if not existing:
            pending = session.scalar(select(Record).where(
                Record.save_id == save.id, Record.kind == "game_candidate",
                Record.deleted.is_(False),
                Record.data["source_key"].as_string() == f"new_sim:{game_id}",
            ).limit(1))
            if pending:
                household_match = household_matches.get(str(sim.get("household_id") or ""))
                payload = dict((pending.data or {}).get("payload") or {})
                refreshed_payload = {
                    **payload, **sim,
                    "detected_game_day": game_day, "detected_game_hour": hour,
                    "detected_game_minute": minute, "detected_tracker_global_day": save.global_day,
                }
                if household_match:
                    refreshed_payload["inferred_household_id"] = household_match
                if refreshed_payload != payload:
                    base = pending.version
                    pending.data = {**(pending.data or {}), "payload": refreshed_payload}
                    pending.version += 1
                    journal(session, pending, "upsert", base)
                    save.revision += 1
                continue
            detected_payload = {**sim, "detected_game_day": game_day, "detected_game_hour": hour,
                                "detected_game_minute": minute, "detected_tracker_global_day": save.global_day}
            parent_hints = automation.parent_suggestions(session, save, sim)
            if parent_hints.get("parent_ids"):
                detected_payload.update(parent_hints)
            if not sim.get("is_baby"):
                detected_payload.update(estimate_new_sim_birth(session, save, detected_payload, save.global_day))
            if sim.get("is_baby") and newborn_contexts.get(game_id):
                detected_payload.update(newborn_contexts[game_id])
            household_match = household_matches.get(str(sim.get("household_id") or ""))
            if household_match:
                detected_payload["inferred_household_id"] = household_match
            candidate = Record(
                save_id=save.id, kind="game_candidate",
                label=(str(sim.get("first_name", "")) + " " + str(sim.get("last_name", ""))).strip(),
                global_day=save.global_day,
                data={"action": "new_baby" if sim.get("is_baby") else "new_sim", "payload": detected_payload, "source_key": f"new_sim:{game_id}", **sim, "detected_game_day": game_day, "hour": hour, "minute": minute, "status": "pending"},
            )
            session.add(candidate)
            session.flush()
            candidates.append(candidate)
            notifications.candidate_event(session, save, candidate)
            journal_entries.append(f"A new {'baby' if sim.get('is_baby') else 'Sim'} was detected: {candidate.label}.")
        else:
            household_members_linked += connect_sim_to_game_household(
                session, save, existing, sim, household_matches,
            )
            created, ended = _game_illnesses(session, save, existing, sim)
            illnesses_created += created; illnesses_ended += ended
            enriched = {**sim, "household_name": sim.get("household_name") or report.get("household_name", ""),
                        "detected_game_day": game_day, "detected_game_hour": hour,
                        "detected_game_minute": minute, "detected_tracker_global_day": save.global_day}
            household_match = household_matches.get(str(sim.get("household_id") or ""))
            if household_match:
                enriched["inferred_tracker_household_id"] = household_match
            linked_newborns = newborns_by_mother.get(existing.id) or []
            if linked_newborns:
                enriched.update({"detected_newborn_count": len(linked_newborns), "detected_newborns": linked_newborns})
            changes = automation.reconcile_sim(session, save, existing, enriched)
            for item in changes:
                notifications.candidate_event(session, save, item)
            for unknown in enriched.get("unknown_health_traits") or ():
                raw = str(unknown.get("raw") or unknown.get("label") or "").strip()
                label = str(unknown.get("label") or raw).strip()
                source_key = f"unknown_illness:{existing.id}:{raw.casefold()}"
                prior = session.scalar(select(Record.id).where(
                    Record.save_id == save.id, Record.kind == "game_candidate",
                    Record.data["source_key"].as_string() == source_key,
                ).limit(1))
                if prior:
                    continue
                item = Record(save_id=save.id, kind="game_candidate",
                    label=f"Possible health condition for {existing.label}: {label}", global_day=save.global_day,
                    data={"action":"unknown_illness","sim_id":existing.id,"status":"pending","source_key":source_key,
                          "payload":{"sim_id":existing.id,"sim_name":existing.label,"raw_trait":raw,
                                     "suggested_name":label,"detected_game_hour":hour,"detected_game_minute":minute}})
                session.add(item);session.flush();candidates.append(item)
                notifications.candidate_event(session,save,item)
            candidates.extend(changes)
            journal_entries.extend(enriched.get("_history_entries") or [])
            if created: journal_entries.append(f"{existing.label} became ill.")
            if ended: journal_entries.append(f"{existing.label} recovered from an illness.")
            journal_entries.extend(item.label + "." for item in changes)
    if households_created:
        verb = "were" if households_created != 1 else "was"
        journal_entries.append(f"{households_created} household{'s' if households_created != 1 else ''} {verb} created automatically from the game.")
    if household_members_linked:
        verb = "were" if household_members_linked != 1 else "was"
        journal_entries.append(f"{household_members_linked} Sim household assignment{'s' if household_members_linked != 1 else ''} {verb} synchronized.")
    parent_link_updates = automation.resolve_parent_links(session, save)
    generation_updates = sync_generations(session, save)
    # Event and lifecycle obligations are true roll records. Reconcile once when
    # the in-game calendar advances instead of producing parallel event-result
    # rows on every clock report.
    event_results = 0
    rolls_created = schedule_rolls(session, save) if day_advanced else 0
    if rolls_created: journal_entries.append(f"{rolls_created} new roll obligation(s) became due or applicable.")
    if rolls_created:
        notifications.record(session,save,"roll",f"{rolls_created} new roll{'s' if rolls_created != 1 else ''} are ready",
                             "Open Today to complete the newly scheduled obligations.","/p/today",f"clock-rolls:{game_day}")
    journal_record = automation.session_journal(session, save, journal_entries, game_day, hour, minute)
    save.revision += illnesses_created + illnesses_ended + len(candidates) + event_results + parent_link_updates + generation_updates + bool(journal_record)
    if day_advanced and bool((save.settings or {}).get("automatic_storyline")):
        prior_story = session.scalar(select(Record.id).where(
            Record.save_id == save.id, Record.kind == "story_entry", Record.deleted.is_(False),
            Record.data["automatic_clock_day"].as_integer() == save.global_day,
        ).limit(1))
        if not prior_story:
            chapter = storyline.generate_chapter(session, save, tone="intimate", use_ai=False)
            chapter.data = {**chapter.data, "automatic_clock_day": save.global_day}
    sync.sync_clock_state(session,save,link)
    session.flush()
    return {
        "status": "ok", "ok": True, "tracker_global_day": save.global_day,
        "game_time": {"day": game_day, "hour": hour, "minute": minute},
        "new_candidates": len(candidates),
        "illnesses_created": illnesses_created, "illnesses_ended": illnesses_ended,
        "event_results_created": event_results, "rolls_created": rolls_created,
        "households_created": households_created, "households_updated": households_updated,
        "household_members_linked": household_members_linked,
        "parent_links_updated": parent_link_updates, "generations_updated": generation_updates,
        "journal_updated": bool(journal_record),
    }
