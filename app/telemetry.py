from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import CLOSED_PREGNANCIES, journal
from .models import ChronicleSave, Record
from . import game_metadata, university


def _text(value) -> str:
    return " ".join(str(value or "").replace("Age.", "").replace("_", " ").split()).strip()


def _items(value) -> list[str]:
    return game_metadata.readable_named_labels(value)


def _clock_data(snapshot: dict) -> dict:
    hour = snapshot.get("detected_game_hour")
    minute = snapshot.get("detected_game_minute")
    second = snapshot.get("detected_game_second")
    exact = None
    if hour is not None and minute is not None:
        exact = (f"{int(hour):02d}:{int(minute):02d}:{int(second):02d}"
                 if second is not None else f"{int(hour):02d}:{int(minute):02d}")
    return {
        "detected_game_day": snapshot.get("detected_game_day"),
        "detected_game_hour": hour,
        "detected_game_minute": minute,
        "detected_game_second": second,
        "detected_game_time": exact,
    }


def history_event(session: Session, save: ChronicleSave, *, category: str, label: str,
                  snapshot: dict, sim: Record | None = None, details: dict | None = None) -> Record:
    data = {
        "category": category,
        "sim_id": sim.id if sim else None,
        "sim_name": sim.label if sim else None,
        "notes": label,
        "source": "clock-sync",
        **_clock_data(snapshot),
        **(details or {}),
    }
    record = Record(save_id=save.id, kind="game_history", label=label, global_day=save.global_day, data=data)
    session.add(record)
    session.flush()
    journal(session, record, "upsert", 0)
    save.revision += 1
    return record


def capture_sim_changes(session: Session, save: ChronicleSave, sim: Record, snapshot: dict,
                        previous: dict) -> list[str]:
    """Create passive life-history entries from fields already sent by Clock Sync."""
    entries: list[str] = []

    occult = game_metadata.occult_identity(snapshot)
    old_occult = _text(previous.get("species_occult") or "Human")
    new_occult = _text(occult.get("display"))
    if new_occult and new_occult.casefold() != old_occult.casefold():
        label = f"{sim.label}'s occult state changed from {old_occult} to {new_occult}."
        history_event(session, save, category="occult", label=label, snapshot=snapshot, sim=sim,
                      details={"from": old_occult, "to": new_occult,
                               "occult_types": occult.get("types") or [], "source": occult.get("source")})
        entries.append(label)

    old_stage = _text(previous.get("game_age_stage"))
    new_stage = _text(snapshot.get("age_stage"))
    if new_stage and new_stage.casefold() != old_stage.casefold():
        label = (f"{sim.label} entered the {new_stage} life stage."
                 if old_stage else f"{sim.label}'s life stage was recorded as {new_stage}.")
        history_event(session, save, category="life_stage", label=label, snapshot=snapshot, sim=sim,
                      details={"from": old_stage or None, "to": new_stage})
        entries.append(label)

    for category, source, stored, noun in (
        ("career", "career", "game_career", "career"),
        ("education", "education", "game_education", "education"),
    ):
        old = _text(previous.get(stored))
        raw_new = snapshot.get(source)
        if raw_new is None:
            continue
        new = _text(raw_new)
        if new.casefold() == old.casefold():
            continue
        if old and new:
            label = f"{sim.label}'s {noun} changed from {old} to {new}."
        elif new:
            label = f"{sim.label}'s {noun} was recorded as {new}."
        else:
            label = f"{sim.label} left {old}."
        history_event(session, save, category=category, label=label, snapshot=snapshot, sim=sim,
                      details={"from": old or None, "to": new or None})
        entries.append(label)

    old_careers = _items(previous.get("game_careers"))
    new_careers = _items(snapshot.get("careers"))
    if old_careers and new_careers and new_careers != old_careers:
        label = f"{sim.label}'s career standing changed to {', '.join(new_careers)}."
        history_event(session, save, category="career_progress", label=label, snapshot=snapshot, sim=sim,
                      details={"from": old_careers, "to": new_careers})
        entries.append(label)

    # University careers use the normal career tracker in The Sims 4. Preserve
    # meaningful performance changes as checkpoints only when the player has
    # linked this Sim to an active university record. Repeated full snapshots
    # with the same score stay idempotent.
    incoming_careers = [row for row in (snapshot.get("careers") or []) if isinstance(row, dict)]
    university_careers = [row for row in incoming_careers if university.is_university_career(row) and row.get("performance") is not None]
    if university_careers:
        enrollment = next((item for item in session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "university_enrollment", Record.deleted.is_(False),
            Record.data["sim_id"].as_string() == sim.id,
        )) if university.enrollment_is_active(item)), None)
        if enrollment:
            latest = session.scalar(select(Record).where(
                Record.save_id == save.id, Record.kind == "university_performance", Record.deleted.is_(False),
                Record.data["enrollment_id"].as_string() == enrollment.id,
                Record.data["source"].as_string() == "clock-sync",
            ).order_by(Record.global_day.desc(), Record.created_at.desc()).limit(1))
            career = university_careers[0]
            try:
                score = max(0.0, min(100.0, float(career.get("performance"))))
            except (TypeError, ValueError):
                score = None
            latest_score = (latest.data or {}).get("performance") if latest else None
            if score is not None and latest_score != score:
                term = session.scalar(select(Record).where(
                    Record.save_id == save.id, Record.kind == "university_term", Record.deleted.is_(False),
                    Record.data["enrollment_id"].as_string() == enrollment.id,
                ).order_by(Record.global_day.desc()).limit(1))
                checkpoint = Record(save_id=save.id, kind="university_performance",
                                    label=f"{sim.label} — Clock Sync performance", global_day=save.global_day,
                                    data={"enrollment_id": enrollment.id, "term_id": term.id if term else None,
                                          "sim_id": sim.id, "sim_name": sim.label,
                                          "checkpoint_type": "Clock Sync performance", "performance": score,
                                          "raw_game_performance": career.get("performance"),
                                          "career": career.get("title") or career.get("name"), "source": "clock-sync",
                                          **_clock_data(snapshot)})
                session.add(checkpoint);session.flush();journal(session,checkpoint,"upsert",0);save.revision+=1
                entries.append(f"{sim.label}'s university performance was recorded as {score:g}.")

    for source, stored, category, sentence in (
        ("degrees", "game_degrees", "education", "completed or reported the degree"),
        ("completed_aspirations", "game_completed_aspirations", "aspiration", "completed the aspiration"),
        ("lifestyles", "game_lifestyles", "lifestyle", "gained the lifestyle"),
        ("fears", "game_fears", "fear", "developed the fear"),
    ):
        old_values = set(_items(previous.get(stored)))
        new_values = _items(snapshot.get(source))
        if stored not in previous:
            continue
        for value in (item for item in new_values if item not in old_values):
            label = f"{sim.label} {sentence} {value}."
            history_event(session, save, category=category, label=label, snapshot=snapshot, sim=sim,
                          details={"value": value})
            entries.append(label)

    old_aspiration = _text(previous.get("game_active_aspiration"))
    new_aspiration = _text(snapshot.get("active_aspiration"))
    if old_aspiration and new_aspiration and old_aspiration.casefold() != new_aspiration.casefold():
        label = f"{sim.label}'s active aspiration changed from {old_aspiration} to {new_aspiration}."
        history_event(session, save, category="aspiration", label=label, snapshot=snapshot, sim=sim,
                      details={"from": old_aspiration, "to": new_aspiration})
        entries.append(label)

    old_occult_progress = previous.get("game_occult_progress") or {}
    new_occult_progress = snapshot.get("occult_progress") or {}
    old_rank = _text(old_occult_progress.get("rank")) if isinstance(old_occult_progress, dict) else ""
    new_rank = _text(new_occult_progress.get("rank")) if isinstance(new_occult_progress, dict) else ""
    if old_rank and new_rank and old_rank.casefold() != new_rank.casefold():
        label = f"{sim.label}'s occult rank changed from {old_rank} to {new_rank}."
        history_event(session, save, category="occult_progress", label=label, snapshot=snapshot, sim=sim,
                      details={"from": old_rank, "to": new_rank, "progress": new_occult_progress})
        entries.append(label)

    if bool(snapshot.get("is_in_labor")) and not bool(previous.get("game_in_labor")):
        label = f"{sim.label} entered labor."
        history_event(session, save, category="pregnancy_labor", label=label, snapshot=snapshot, sim=sim,
                      details={"pregnancy_stage": snapshot.get("pregnancy_stage")})
        entries.append(label)

    if "responsible_pregnancy_states" in snapshot:
        old_states = {
            str(row.get("key") or ""): row
            for row in game_metadata.responsible_pregnancy_states(
                previous.get("game_responsible_pregnancy_states")
            )
            if row.get("key")
        }
        new_states = {
            str(row.get("key") or ""): row
            for row in game_metadata.responsible_pregnancy_states(
                snapshot.get("responsible_pregnancy_states")
            )
            if row.get("key")
        }
        for key in sorted(new_states.keys() - old_states.keys()):
            state = new_states[key]
            label = f"{sim.label}'s Responsible Pregnancy status reported {state['name']}."
            history_event(
                session, save, category="responsible_pregnancy", label=label,
                snapshot=snapshot, sim=sim,
                details={"action": "detected", "state": state, "provider": state.get("provider")},
            )
            entries.append(label)
        for key in sorted(old_states.keys() - new_states.keys()):
            state = old_states[key]
            label = f"{sim.label}'s Responsible Pregnancy status no longer reports {state['name']}."
            history_event(
                session, save, category="responsible_pregnancy", label=label,
                snapshot=snapshot, sim=sim,
                details={"action": "cleared", "state": state, "provider": state.get("provider")},
            )
            entries.append(label)

    old_milestones = set(_items(previous.get("game_milestones")))
    new_milestones = _items(snapshot.get("milestones"))
    for milestone in (item for item in new_milestones if "game_milestones" in previous and item not in old_milestones):
        label = f"{sim.label} completed the milestone {milestone}."
        history_event(session, save, category="milestone", label=label, snapshot=snapshot, sim=sim,
                      details={"milestone": milestone})
        entries.append(label)

    old_skills = set(_items(previous.get("game_skills")))
    new_skills = _items(snapshot.get("skills"))
    for skill in (item for item in new_skills if "game_skills" in previous and item not in old_skills):
        label = f"{sim.label}'s skill progress was recorded: {skill}."
        history_event(session, save, category="skill", label=label, snapshot=snapshot, sim=sim,
                      details={"skill": skill})
        entries.append(label)
    return entries


def capture_responsible_pregnancy(session: Session, save: ChronicleSave, sim: Record,
                                  snapshot: dict) -> list[str]:
    """Attach current optional-mod states to the relevant pregnancy record.

    This is passive telemetry, not an automation-inbox action. Repeated reports
    with the same states do not rewrite the record or create duplicate history.
    Newborn complications follow the newborn's ``pregnancy_id``; prenatal
    states follow the mother's current open pregnancy.
    """
    if "responsible_pregnancy_states" not in snapshot:
        return []
    states = game_metadata.responsible_pregnancy_states(snapshot.get("responsible_pregnancy_states"))
    pregnancy = None
    pregnancy_id = str((sim.data or {}).get("pregnancy_id") or "")
    if pregnancy_id:
        candidate = session.get(Record, pregnancy_id)
        if candidate and candidate.save_id == save.id and candidate.kind == "pregnancy" and not candidate.deleted:
            pregnancy = candidate
    if pregnancy is None:
        pregnancy = next((record for record in session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "pregnancy", Record.deleted.is_(False),
            Record.data["mother_id"].as_string() == sim.id,
        ).order_by(Record.global_day.desc()))
            if str((record.data or {}).get("status") or "active").casefold() not in CLOSED_PREGNANCIES), None)
    if pregnancy is None:
        return []
    data = dict(pregnancy.data or {})
    previous = game_metadata.responsible_pregnancy_states(data.get("responsible_pregnancy_states"))
    if previous == states:
        return []
    clock = _clock_data(snapshot)
    history = list(data.get("responsible_pregnancy_history") or [])
    history.append({
        "global_day": save.global_day,
        "sim_id": sim.id,
        "sim_name": sim.label,
        "states": states,
        **clock,
    })
    base = pregnancy.version
    pregnancy.data = {
        **data,
        "responsible_pregnancy_states": states,
        "responsible_pregnancy_history": history[-50:],
        "responsible_pregnancy_last_global_day": save.global_day,
        "responsible_pregnancy_source": "Clock Sync",
        **clock,
    }
    pregnancy.version += 1
    journal(session, pregnancy, "upsert", base)
    save.revision += 1
    return []


def _progress_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return min(100.0, number * 100.0 if number <= 1 else number)


def pregnancy_band(percent: float) -> str:
    if percent >= 90:
        return "Birth approaching"
    if percent >= 67:
        return "Late pregnancy"
    if percent >= 34:
        return "Middle pregnancy"
    return "Early pregnancy"


def capture_pregnancy_progress(session: Session, save: ChronicleSave, sim: Record,
                               snapshot: dict) -> list[str]:
    if not bool(snapshot.get("is_pregnant")):
        return []
    percent = _progress_number(
        snapshot.get("pregnancy_progress")
        if snapshot.get("pregnancy_progress") is not None
        else snapshot.get("pregnancy_progress_percentage")
    )
    if percent is None:
        return []
    pregnancy = next((record for record in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "pregnancy", Record.deleted.is_(False),
        Record.data["mother_id"].as_string() == sim.id,
    ).order_by(Record.global_day.desc()))
        if str((record.data or {}).get("status") or "active").casefold() not in CLOSED_PREGNANCIES), None)
    if not pregnancy:
        return []
    data = dict(pregnancy.data or {})
    old_band = str(data.get("game_pregnancy_band") or "")
    band = pregnancy_band(percent)
    changed = data.get("game_pregnancy_progress") != round(percent, 1)
    if changed:
        base = pregnancy.version
        pregnancy.data = {
            **data,
            "game_pregnancy_progress": round(percent, 1),
            "game_pregnancy_band": band,
            "last_progress_global_day": save.global_day,
            **_clock_data(snapshot),
        }
        pregnancy.version += 1
        journal(session, pregnancy, "upsert", base)
        save.revision += 1
    if band == old_band:
        return []
    label = f"{sim.label}'s pregnancy entered {band.lower()} ({percent:.0f}%)."
    history_event(session, save, category="pregnancy_progress", label=label, snapshot=snapshot, sim=sim,
                  details={"pregnancy_id": pregnancy.id, "progress": round(percent, 1), "band": band})
    return [label]


def capture_household_finances(session: Session, save: ChronicleSave, members: list[dict],
                               tracked_by_game_id: dict[str, Record], snapshot: dict) -> list[str]:
    """Record one balance change per reported household, not one per Sim."""
    grouped: dict[str, dict] = {}
    for member in members:
        household_id = str(member.get("household_id") or "").strip()
        funds = member.get("household_funds")
        if not household_id or funds is None:
            continue
        grouped.setdefault(household_id, member)
    entries: list[str] = []
    for game_household_id, member in grouped.items():
        try:
            funds = int(member.get("household_funds"))
        except (TypeError, ValueError):
            continue
        linked = [tracked_by_game_id.get(str(item.get("game_sim_id") or "")) for item in members
                  if str(item.get("household_id") or "") == game_household_id]
        linked = [sim for sim in linked if sim]
        previous_values = set()
        for sim in linked:
            try:
                previous_values.add(int(sim.data.get("last_household_funds")))
            except (TypeError, ValueError):
                continue
        previous = next(iter(previous_values)) if len(previous_values) == 1 else None
        if previous == funds:
            continue
        tracker_ids = {str(sim.data.get("current_household_id") or "") for sim in linked
                       if sim.data.get("current_household_id")}
        tracker_household = session.get(Record, next(iter(tracker_ids))) if len(tracker_ids) == 1 else None
        if tracker_household and (tracker_household.kind != "household" or tracker_household.save_id != save.id):
            tracker_household = None
        name = str(member.get("household_name") or (tracker_household.label if tracker_household else "Household"))
        delta = funds - previous if previous is not None else None
        if delta is None:
            label = f"{name}'s household balance was recorded as §{funds:,}."
        elif delta > 0:
            label = f"{name}'s household gained §{delta:,}, reaching §{funds:,}."
        else:
            label = f"{name}'s household spent §{abs(delta):,}, leaving §{funds:,}."
        history_event(session, save, category="finance", label=label, snapshot=snapshot,
                      details={"game_household_id": game_household_id,
                               "tracker_household_id": tracker_household.id if tracker_household else None,
                               "household_name": name, "balance": funds, "previous_balance": previous,
                               "delta": delta})
        if tracker_household:
            base = tracker_household.version
            tracker_household.data = {**tracker_household.data, "last_game_funds": funds,
                                      "game_household_id": game_household_id,
                                      "game_household_name": name}
            tracker_household.version += 1
            journal(session, tracker_household, "upsert", base)
            save.revision += 1
        entries.append(label)
    return entries
