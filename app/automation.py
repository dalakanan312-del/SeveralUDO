from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ChronicleSave, Record
from .domain import end_illnesses_for_death, event_is_ignored, journal
from . import game_metadata, telemetry


def _key(action: str, sim_id: str, value: str) -> str:
    return f"{action}:{sim_id}:{value}".casefold()


def _detected_list(value) -> list[str]:
    """Normalize optional game telemetry into stable, readable labels."""
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        value = [{"name": key, "value": item} for key, item in value.items()]
    elif not isinstance(value, (list, tuple, set)):
        value = [value]
    result = []
    for item in value:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("display_name") or item.get("title") or item.get("trait") or item.get("skill") or "").strip()
            level = item.get("level", item.get("value"))
            label = f"{name} (level {level})" if name and level not in (None, "") else name
        else:
            label = str(item).strip()
        if label and label not in result:
            result.append(label)
    return result


def _positive_count(*values) -> int | None:
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def _game_sim(session: Session, save: ChronicleSave, game_sim_id: str) -> Record | None:
    game_sim_id = str(game_sim_id or "").strip()
    if not game_sim_id:
        return None
    return session.scalar(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
        Record.data["game_sim_id"].as_string() == game_sim_id,
    ))


def parent_suggestions(session: Session, save: ChronicleSave, snapshot: dict,
                       exclude_sim_id: str | None = None) -> dict:
    """Resolve game genealogy to tracker Sims without inventing unknown parents."""
    parent_rows = [row for row in (snapshot.get("parents") or []) if isinstance(row, dict)]
    rows_by_id = {
        str(row.get("game_sim_id") or "").strip(): row for row in parent_rows
        if str(row.get("game_sim_id") or "").strip()
    }
    game_ids = []
    for value in list(snapshot.get("parent_game_sim_ids") or []) + list(rows_by_id):
        game_id = str(value or "").strip()
        if game_id and game_id not in game_ids:
            game_ids.append(game_id)
    matched: list[tuple[Record, dict]] = []
    for game_id in game_ids:
        parent = _game_sim(session, save, game_id)
        if parent and parent.id != exclude_sim_id and all(item[0].id != parent.id for item in matched):
            matched.append((parent, rows_by_id.get(game_id, {})))
    result = {
        "parent_ids": [parent.id for parent, _ in matched],
        "inferred_mother_id": None, "inferred_mother_name": "",
        "inferred_father_id": None, "inferred_father_name": "",
    }
    unassigned = []
    for parent, row in matched:
        sex = str(row.get("sex") or (parent.data or {}).get("sex") or "").casefold()
        if "female" in sex and not result["inferred_mother_id"]:
            result.update(inferred_mother_id=parent.id, inferred_mother_name=parent.label)
        elif "male" in sex and "female" not in sex and not result["inferred_father_id"]:
            result.update(inferred_father_id=parent.id, inferred_father_name=parent.label)
        else:
            unassigned.append(parent)
    for field, name_field in (("inferred_mother_id", "inferred_mother_name"),
                              ("inferred_father_id", "inferred_father_name")):
        if not result[field] and unassigned:
            parent = unassigned.pop(0)
            result[field], result[name_field] = parent.id, parent.label
    return result


def resolve_parent_links(session: Session, save: ChronicleSave) -> int:
    """Retry unresolved genealogy after every report as newly detected parents are linked."""
    changed = 0
    sims = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
    )))
    for sim in sims:
        data = dict(sim.data or {})
        if not data.get("parent_game_sim_ids"):
            continue
        hints = parent_suggestions(session, save, {
            "parent_game_sim_ids": data.get("parent_game_sim_ids") or [],
            "parents": data.get("game_parents") or [],
        }, sim.id)
        updates = {"parent_ids": hints["parent_ids"]} if hints["parent_ids"] or data.get("parent_ids") else {}
        if not data.get("mother_id") and hints.get("inferred_mother_id"):
            updates["mother_id"] = hints["inferred_mother_id"]
        if not data.get("father_id") and hints.get("inferred_father_id"):
            updates["father_id"] = hints["inferred_father_id"]
        if any(data.get(key) != value for key, value in updates.items()):
            base = sim.version
            sim.data = {**data, **updates}
            sim.version += 1
            journal(session, sim, "upsert", base)
            changed += 1
    return changed


def _significant_relationship(category: str) -> bool:
    value = str(category or "").casefold()
    return any(marker in value for marker in ("marriage", "married", "spouse", "fianc", "engag"))


def _existing_relationship(session: Session, save: ChronicleSave, first: Record,
                           second: Record | None, category: str) -> bool:
    if not second:
        return False
    for relationship in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "relationship", Record.deleted.is_(False),
    )):
        data = relationship.data or {}
        if {str(data.get("partner1_id") or ""), str(data.get("partner2_id") or "")} != {first.id, second.id}:
            continue
        existing_type = str(data.get("type") or "relationship").casefold()
        if (_significant_relationship(category) and (bool(data.get("legally_married")) or _significant_relationship(existing_type))) or existing_type == str(category or "relationship").casefold():
            return True
    return False


def candidate(session: Session, save: ChronicleSave, action: str, sim: Record | None, label: str, payload: dict, identity: str) -> Record | None:
    source_key = _key(action, sim.id if sim else "new", identity)
    exists = session.scalar(select(Record.id).where(
        Record.save_id == save.id, Record.kind == "game_candidate",
        Record.deleted.is_(False),
        Record.data["source_key"].as_string() == source_key,
    ).limit(1))
    if exists:
        return None
    item = Record(save_id=save.id, kind="game_candidate", label=label, global_day=save.global_day,
                  data={"action": action, "sim_id": sim.id if sim else None, "status": "pending", "source_key": source_key, "payload": payload})
    session.add(item); session.flush(); journal(session, item, "upsert", 0)
    return item


def reconcile_sim(session: Session, save: ChronicleSave, sim: Record, snapshot: dict) -> list[Record]:
    """Turn one guarded game snapshot into safe telemetry and confirmable changes."""
    made = []
    data = dict(sim.data or {})
    history_entries = telemetry.capture_sim_changes(session, save, sim, snapshot, data)
    previously_observed_dead = data.get("game_was_dead")
    incoming_first = str(snapshot.get("first_name") or "").strip()
    incoming_last = str(snapshot.get("last_name") or "").strip()
    incoming_sex = str(snapshot.get("sex") or "").strip()
    old_first = str(data.get("first_name") or "").strip()
    old_last = str(data.get("last_name") or "").strip()
    old_sex = str(data.get("sex") or "").strip()
    name_changed = bool(incoming_first and (incoming_first.casefold(), incoming_last.casefold()) != (old_first.casefold(), old_last.casefold()))
    sex_changed = bool(incoming_sex and old_sex and incoming_sex.casefold() != old_sex.casefold())
    if name_changed or sex_changed:
        identity_payload = {
            **snapshot,
            "previous_first_name": old_first, "previous_last_name": old_last,
            "previous_sex": old_sex,
        }
        item = candidate(
            session, save, "sim_identity_change", sim,
            f"Profile change detected: {sim.label}", identity_payload,
            f"{incoming_first}:{incoming_last}:{incoming_sex}",
        )
        if item:
            made.append(item)
    has_pregnancy_state = "is_pregnant" in snapshot
    is_snapshot_pregnant = bool(snapshot.get("is_pregnant")) if has_pregnancy_state else bool(data.get("game_was_pregnant"))
    partner_game_id = str(snapshot.get("pregnancy_partner_game_sim_id") or snapshot.get("other_parent_game_sim_id") or "").strip()
    partner = session.scalar(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
        Record.data["game_sim_id"].as_string() == partner_game_id,
    )) if partner_game_id else None
    reported_count = _positive_count(
        snapshot.get("babies_expected"), snapshot.get("pregnancy_offspring_count"),
        snapshot.get("offspring_count"), snapshot.get("baby_count"),
    )
    occult = game_metadata.occult_identity(snapshot)
    telemetry_values = {
        "game_age_stage": snapshot.get("age_stage"), "game_sex": incoming_sex or None,
        "game_career": snapshot.get("career"),
        "game_education": snapshot.get("education"), "game_traits": game_metadata.readable_trait_labels(snapshot.get("traits")),
        "game_skills": _detected_list(snapshot.get("skills")),
        "game_milestones": _detected_list(snapshot.get("milestones")), "last_household_funds": snapshot.get("household_funds"),
        "last_game_world": snapshot.get("world_name"), "last_game_lot": snapshot.get("lot_name"),
        "last_game_pregnancy_count": reported_count if is_snapshot_pregnant else None,
        "last_game_pregnancy_partner_game_sim_id": partner_game_id if is_snapshot_pregnant and partner_game_id else None,
        "parent_game_sim_ids": [str(value) for value in (snapshot.get("parent_game_sim_ids") or []) if value],
        "game_parents": [row for row in (snapshot.get("parents") or []) if isinstance(row, dict)],
    }
    if occult["display"]:
        telemetry_values.update({
            "species_occult": occult["display"], "game_occult_types": occult["types"],
            "game_occult_source": occult["source"], "game_occult_scan_supported": occult["authoritative"],
        })
    telemetry_version = int(snapshot.get("telemetry_version") or 0)
    clearable = {"game_traits", "game_career", "game_education"} if telemetry_version >= 2 else set()
    # Clock Sync 2.0.3 reports whether the game actually exposed each optional
    # tracker.  An empty supported scan is authoritative; an unavailable scan
    # must not erase skill or milestone data captured by an earlier report.
    if telemetry_version == 2 or snapshot.get("skills_scan_supported") is True:
        clearable.add("game_skills")
    if telemetry_version == 2 or snapshot.get("milestone_scan_supported") is True:
        clearable.add("game_milestones")
    if occult["authoritative"]:
        clearable.add("game_occult_types")
    updates = {key: value for key, value in telemetry_values.items() if value not in (None, "", []) or key in clearable}
    changed_telemetry = any(data.get(key) != value for key, value in updates.items())
    if changed_telemetry:
        base = sim.version; data.update(updates); sim.data = data; sim.version += 1; journal(session, sim, "upsert", base)
    data = dict(sim.data or {})

    # Genealogy is factual save data, so fill only missing links and retry later
    # when a referenced parent has not yet been accepted into the tracker.
    hints = parent_suggestions(session, save, snapshot, sim.id)
    if hints["parent_ids"]:
        parent_updates = {"parent_ids": hints["parent_ids"]}
        if not data.get("mother_id") and hints.get("inferred_mother_id"):
            parent_updates["mother_id"] = hints["inferred_mother_id"]
        if not data.get("father_id") and hints.get("inferred_father_id"):
            parent_updates["father_id"] = hints["inferred_father_id"]
        if any(data.get(key) != value for key, value in parent_updates.items()):
            base = sim.version; sim.data = {**sim.data, **parent_updates}; sim.version += 1; journal(session, sim, "upsert", base)
            data = dict(sim.data or {})

    has_death_state = "is_dead" in snapshot
    currently_dead = bool(snapshot.get("is_dead")) if has_death_state else bool(previously_observed_dead)
    if has_death_state and currently_dead and previously_observed_dead is not True and not data.get("death_confirmed"):
        item = candidate(session, save, "sim_death", sim, f"Death detected: {sim.label}", snapshot, str(save.global_day))
        if item: made.append(item)
    old_household = str(data.get("game_household_id") or "")
    new_household = str(snapshot.get("household_id") or "")
    if old_household and new_household and old_household != new_household:
        item = candidate(session, save, "household_change", sim, f"Household change: {sim.label}", snapshot, new_household)
        if item: made.append(item)
    elif new_household and not old_household:
        base = sim.version; sim.data = {**sim.data, "game_household_id": new_household, "game_household_name": snapshot.get("household_name")}; sim.version += 1; journal(session, sim, "upsert", base)

    has_relationship_state = "relationships" in snapshot
    relationships = [rel for rel in (snapshot.get("relationships") or []) if isinstance(rel, dict)]
    relationship_keys = sorted({
        f"{str(rel.get('other_game_sim_id') or '')}:{str(rel.get('category') or 'relationship').casefold()}"
        for rel in relationships if rel.get("other_game_sim_id")
    })
    prior_relationship_keys = set(data.get("game_relationship_keys") or [])
    has_relationship_baseline = "game_relationship_keys" in data
    current_game_id = str(data.get("game_sim_id") or snapshot.get("game_sim_id") or "")
    for rel in relationships:
        other = str(rel.get("other_game_sim_id") or "")
        category = str(rel.get("category") or "relationship")
        relationship_key = f"{other}:{category.casefold()}"
        other_sim = _game_sim(session, save, other)
        is_new_transition = has_relationship_baseline and relationship_key not in prior_relationship_keys
        is_initial_significant = not has_relationship_baseline and _significant_relationship(category)
        is_canonical_endpoint = not (other_sim and current_game_id and other and current_game_id > other)
        if (other and (is_new_transition or is_initial_significant) and is_canonical_endpoint
                and not _existing_relationship(session, save, sim, other_sim, category)):
            rel_payload = {
                **rel,
                "other_sim_id": other_sim.id if other_sim else None,
                "other_sim_name": other_sim.label if other_sim else "",
                "detected_game_day": snapshot.get("detected_game_day"),
                "detected_game_hour": snapshot.get("detected_game_hour"),
                "detected_game_minute": snapshot.get("detected_game_minute"),
                "detected_tracker_global_day": snapshot.get("detected_tracker_global_day", save.global_day),
            }
            item = candidate(session, save, "relationship_change", sim, f"{category.title()} detected for {sim.label}", rel_payload, f"{other}:{category}")
            if item: made.append(item)
    # A disappeared relationship is also meaningful.  Use a per-relationship
    # sequence so a later reconciliation of the same pair can be reviewed again,
    # while ordinary repeat reports remain silent.  When both Sims are tracked,
    # only the lower game id creates the shared review item.
    end_sequences = dict(data.get("game_relationship_end_sequences") or {})
    for old_key in sorted(prior_relationship_keys - set(relationship_keys)) if has_relationship_baseline and has_relationship_state else ():
        other, _, category = old_key.partition(":")
        if not other:
            continue
        other_sim = session.scalar(select(Record).where(
            Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
            Record.data["game_sim_id"].as_string() == other,
        ))
        if other_sim and current_game_id and current_game_id > other:
            continue
        sequence = int(end_sequences.get(old_key) or 0) + 1
        end_sequences[old_key] = sequence
        end_payload = {
            "other_game_sim_id": other,
            "other_sim_id": other_sim.id if other_sim else None,
            "other_sim_name": other_sim.label if other_sim else "",
            "category": category or "relationship",
            "detected_game_day": snapshot.get("detected_game_day"),
            "detected_game_hour": snapshot.get("detected_game_hour"),
            "detected_game_minute": snapshot.get("detected_game_minute"),
            "detected_tracker_global_day": snapshot.get("detected_tracker_global_day", save.global_day),
        }
        item = candidate(
            session, save, "relationship_end", sim,
            f"Possible {category or 'relationship'} ending: {sim.label}",
            end_payload, f"{other}:{category}:{sequence}",
        )
        if item:
            made.append(item)
    state_updates = {"game_was_dead": currently_dead} if has_death_state else {}
    if has_relationship_state:
        state_updates.update(game_relationship_keys=relationship_keys, game_relationship_end_sequences=end_sequences)
    if any(data.get(key) != value for key, value in state_updates.items()):
        base = sim.version; sim.data = {**sim.data, **state_updates}; sim.version += 1; journal(session, sim, "upsert", base)
        data = dict(sim.data or {})
    was_pregnant = bool(data.get("game_was_pregnant"))
    is_pregnant = is_snapshot_pregnant
    if has_pregnancy_state and is_pregnant and reported_count:
        data["last_game_pregnancy_count"] = reported_count
    if has_pregnancy_state and is_pregnant and not was_pregnant:
        sequence = int(data.get("game_pregnancy_sequence") or 0) + 1
        pregnancy_payload = {
            **snapshot, "babies_expected": reported_count or 1,
            "inferred_other_parent_id": partner.id if partner else None,
            "inferred_other_parent_name": partner.label if partner else "",
        }
        item = candidate(session, save, "pregnancy_discovered", sim, f"New pregnancy detected: {sim.label}", pregnancy_payload, str(sequence))
        if item: made.append(item)
        data["game_pregnancy_sequence"] = sequence
    if has_pregnancy_state and was_pregnant and not is_pregnant:
        active = next((record for record in session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "pregnancy", Record.deleted.is_(False),
            Record.data["mother_id"].as_string() == sim.id,
        ).order_by(Record.global_day.desc())) if str((record.data or {}).get("status") or "active").casefold() not in {"delivered", "miscarriage", "stillbirth", "cancelled", "canceled", "ended", "closed"}), None)
        active_expected = (active.data or {}).get("babies_expected") if active else None
        delivered = _positive_count(
            snapshot.get("babies_delivered"), snapshot.get("detected_newborn_count"),
            data.get("last_game_pregnancy_count"), active_expected,
        ) or 1
        source = "game report" if snapshot.get("babies_delivered") else (
            "newborn detection" if snapshot.get("detected_newborn_count") else (
                "pregnancy scan" if data.get("last_game_pregnancy_count") else "pregnancy record"
            )
        )
        outcome_payload = {**snapshot, "babies_delivered": delivered, "babies_delivered_source": source,
                           "pregnancy_id": active.id if active else None}
        item = candidate(session, save, "pregnancy_outcome", sim, f"Pregnancy outcome detected: {sim.label}", outcome_payload, str(save.global_day))
        if item: made.append(item)
    if has_pregnancy_state and was_pregnant != is_pregnant:
        base = sim.version; sim.data = {**sim.data, **data, "game_was_pregnant": is_pregnant}; sim.version += 1; journal(session, sim, "upsert", base)
    history_entries.extend(telemetry.capture_pregnancy_progress(session, save, sim, snapshot))
    snapshot["_history_entries"] = history_entries
    return made


def ensure_event_participation(session: Session, save: ChronicleSave) -> int:
    events = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "event", Record.deleted.is_(False), Record.global_day <= save.global_day)))
    sims = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False))))
    made = 0
    for event in events:
        if event_is_ignored(event): continue
        end = event.data.get("end_global_day")
        if end is not None and int(end) < save.global_day: continue
        country = str(event.data.get("country") or event.data.get("location") or "global").casefold()
        for sim in sims:
            death = sim.data.get("death_global_day")
            if death is not None and int(death) <= save.global_day: continue
            location = str(sim.data.get("last_game_world") or sim.data.get("country") or "").casefold()
            if country not in {"", "global", "worldwide", "all"} and country not in location: continue
            source = f"event:{event.id}:{sim.id}"
            exists = session.scalar(select(Record.id).where(Record.save_id == save.id, Record.kind == "event_result", Record.data["source"].as_string() == source).limit(1))
            if exists: continue
            result = Record(save_id=save.id, kind="event_result", label=f"{event.label} — {sim.label}", global_day=save.global_day,
                            data={"event_id": event.id, "sim_id": sim.id, "source": source, "status": "pending", "die": event.data.get("die"), "bad_results": event.data.get("bad_results")})
            session.add(result); session.flush(); journal(session, result, "upsert", 0); made += 1
    return made


def session_journal(session: Session, save: ChronicleSave, changes: list[str], game_day: int,
                    game_hour: int | None = None, game_minute: int | None = None) -> Record | None:
    if not changes: return None
    current_heir = str((save.settings or {}).get("current_heir_id") or "")
    narrator = session.get(Record, current_heir) if current_heir else None
    if not narrator or narrator.kind != "sim" or narrator.deleted:
        narrator = session.scalar(select(Record).where(
            Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
        ).order_by(Record.global_day.asc(), Record.created_at.asc()).limit(1))
    narrator_name = narrator.label if narrator else "the household chronicler"
    narrative = f"I, {narrator_name}, set down what changed: " + " ".join(changes)
    source = f"game-session:{game_day}:{save.global_day}"
    existing = session.scalar(select(Record).where(Record.save_id == save.id, Record.kind == "session_journal", Record.data["source"].as_string() == source))
    if existing:
        merged = list(dict.fromkeys((existing.data.get("entries") or []) + changes)); base = existing.version
        existing.data = {**existing.data, "entries": merged, "notes": " ".join(merged),
                         "narrative": f"I, {narrator_name}, set down what changed: " + " ".join(merged),
                         "narrator_sim_id": narrator.id if narrator else None, "narrator_name": narrator_name,
                         "game_hour": game_hour, "game_minute": game_minute,
                         "game_time": f"{game_hour:02d}:{game_minute:02d}" if game_hour is not None and game_minute is not None else None}; existing.version += 1; journal(session, existing, "upsert", base); return existing
    item = Record(save_id=save.id, kind="session_journal", label=f"Game report — Global Day {save.global_day}", global_day=save.global_day,
                  data={"source": source, "entries": changes, "notes": " ".join(changes), "narrative":narrative,
                        "narrator_sim_id": narrator.id if narrator else None, "narrator_name": narrator_name,
                        "game_day": game_day, "game_hour": game_hour, "game_minute": game_minute,
                        "game_time": f"{game_hour:02d}:{game_minute:02d}" if game_hour is not None and game_minute is not None else None})
    session.add(item); session.flush(); journal(session, item, "upsert", 0); return item
