"""Save-a-Sim credits: a small, auditable mercy system for challenge rules.

The tracker deliberately stores every credit as a regular synced record.  That
makes the balance portable between the desktop and hosted editions, and avoids
the easy-to-miss duplicate awards that a bare settings counter would create.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ChronicleSave, Record


CREDIT_KIND = "save_a_sim_credit"
RULE_KIND = "save_a_sim_rule"


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _journal(session: Session, record: Record, operation: str, base_version: int) -> None:
    # Kept local to avoid importing domain while it is being initialized.  This
    # module is also called from ``domain.complete_roll``.
    from .domain import journal
    journal(session, record, operation, base_version)


def _active_records(session: Session, save: ChronicleSave, kind: str) -> list[Record]:
    return list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == kind, Record.deleted.is_(False),
    )))


def credit_entries(session: Session, save: ChronicleSave) -> list[Record]:
    return sorted(_active_records(session, save, CREDIT_KIND), key=lambda item: (
        int(item.global_day or 0), str(item.created_at or ""), item.id,
    ), reverse=True)


def rule_records(session: Session, save: ChronicleSave) -> list[Record]:
    return sorted(_active_records(session, save, RULE_KIND), key=lambda item: (
        not _truthy((item.data or {}).get("active", True)), item.label.casefold(), item.id,
    ))


def _entry_for_source(session: Session, save: ChronicleSave, source_key: str) -> Record | None:
    if not source_key:
        return None
    return session.scalar(select(Record).where(
        Record.save_id == save.id, Record.kind == CREDIT_KIND, Record.deleted.is_(False),
        Record.data["source_key"].as_string() == source_key,
    ).limit(1))


def _record_credit(session: Session, save: ChronicleSave, *, label: str, amount: int,
                   source_key: str, data: dict | None = None) -> tuple[Record, bool]:
    """Write one idempotent ledger entry and return it with a created flag."""
    existing = _entry_for_source(session, save, source_key)
    if existing:
        return existing, False
    payload = {
        "amount": int(amount), "source_key": source_key,
        "entry_type": "earned" if amount > 0 else "spent",
        "recorded_global_day": int(save.global_day), **dict(data or {}),
    }
    entry = Record(save_id=save.id, kind=CREDIT_KIND, label=label, global_day=save.global_day, data=payload)
    session.add(entry)
    session.flush()
    _journal(session, entry, "upsert", 0)
    save.revision += 1
    return entry, True


def _confirmed_death_ids(records: Iterable[Record]) -> set[str]:
    sims = {record.id for record in records if record.kind == "sim"}
    confirmed = {
        record.id for record in records if record.kind == "sim"
        and _truthy((record.data or {}).get("death_confirmed"))
    }
    confirmed.update(
        str((record.data or {}).get("sim_id") or "") for record in records
        if record.kind == "death" and _truthy((record.data or {}).get("completed"))
    )
    return {sim_id for sim_id in confirmed if sim_id in sims}


def _schedulable_sims(records: Iterable[Record]) -> list[Record]:
    result = []
    for record in records:
        if record.kind != "sim":
            continue
        data = record.data or {}
        if _truthy(data.get("death_confirmed")) or _truthy(data.get("game_was_dead")):
            continue
        result.append(record)
    return result


def _scheduled_day(sim: Record) -> int | None:
    value = (sim.data or {}).get("death_global_day")
    try:
        day = int(value)
    except (TypeError, ValueError):
        return None
    return day if day >= 1 else None


def sync_automatic_awards(session: Session, save: ChronicleSave) -> dict:
    """Award death milestones and the all-current-Sims-scheduled condition.

    It is safe to call on every relevant mutation.  Each award has a durable
    source key, so restores, reloads, and browser/desktop synchronization never
    produce a second copy of the same credit.
    """
    records = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind.in_(("sim", "death")),
        Record.deleted.is_(False),
    )))
    confirmed = _confirmed_death_ids(records)
    created: list[Record] = []
    for milestone in range(10, (len(confirmed) // 10) * 10 + 1, 10):
        entry, made = _record_credit(
            session, save,
            label=f"Save-a-Sim earned · {milestone} confirmed deaths",
            amount=1, source_key=f"death-milestone:{milestone}",
            data={"award_reason": "Every 10 confirmed deaths", "death_milestone": milestone},
        )
        if made:
            created.append(entry)

    living = _schedulable_sims(records)
    all_scheduled = bool(living) and all(_scheduled_day(sim) is not None for sim in living)
    if all_scheduled:
        # The roster, rather than its exact death days, defines the condition.
        # Changing an already scheduled date must not farm extra credits; a new
        # Sim joining the challenge gives a genuinely new all-scheduled state.
        roster = ",".join(sorted(sim.id for sim in living))
        signature = hashlib.sha256(roster.encode("utf-8")).hexdigest()[:20]
        entry, made = _record_credit(
            session, save,
            label="Save-a-Sim earned · every current Sim is scheduled",
            amount=1, source_key=f"all-sims-scheduled:{signature}",
            data={"award_reason": "All current Sims have a scheduled death", "roster_signature": signature,
                  "scheduled_sim_ids": [sim.id for sim in sorted(living, key=lambda item: item.label.casefold())]},
        )
        if made:
            created.append(entry)
    return {
        "created": created, "death_count": len(confirmed), "all_scheduled": all_scheduled,
        "scheduled_count": sum(_scheduled_day(sim) is not None for sim in living), "living_count": len(living),
    }


def award_matching_roll_rules(session: Session, save: ChronicleSave, roll: Record) -> list[Record]:
    """Grant custom credits when a completed roll matches a player rule."""
    if roll.kind != "roll" or not _truthy((roll.data or {}).get("completed")):
        return []
    roll_data = roll.data or {}
    roll_text = f"{roll.label} {roll_data.get('roll_type', '')}".casefold()
    outcome = str(roll_data.get("outcome") or "").casefold()
    created: list[Record] = []
    for rule in rule_records(session, save):
        data = rule.data or {}
        if not _truthy(data.get("active", True)) or str(data.get("trigger_type") or "manual") != "roll_result":
            continue
        roll_match = str(data.get("match_roll") or "").strip().casefold()
        outcome_match = str(data.get("match_outcome") or "").strip().casefold()
        # A roll-triggered condition must identify at least one part of the
        # situation.  A blank condition would otherwise grant a credit for
        # every roll in the save.
        if not (roll_match or outcome_match):
            continue
        if roll_match and roll_match not in roll_text:
            continue
        if outcome_match and outcome_match not in outcome:
            continue
        repeatable = _truthy(data.get("repeatable"))
        source_key = f"rule:{rule.id}:roll:{roll.id}" if repeatable else f"rule:{rule.id}"
        amount = max(1, min(9, _int(data.get("amount"), 1)))
        entry, made = _record_credit(
            session, save, label=f"Save-a-Sim earned · {rule.label}", amount=amount, source_key=source_key,
            data={"award_reason": "Completed roll matched a Save-a-Sim rule", "rule_id": rule.id,
                  "rule_label": rule.label, "source_roll_id": roll.id, "source_roll_label": roll.label,
                  "source_outcome": roll_data.get("outcome")},
        )
        if made:
            created.append(entry)
    return created


def award_manual_rule(session: Session, save: ChronicleSave, rule: Record) -> tuple[Record, bool]:
    data = rule.data or {}
    if rule.kind != RULE_KIND or rule.deleted or not _truthy(data.get("active", True)):
        raise ValueError("That Save-a-Sim condition is unavailable.")
    if str(data.get("trigger_type") or "manual") != "manual":
        raise ValueError("This condition is awarded automatically from matching roll results.")
    repeatable = _truthy(data.get("repeatable"))
    source_key = f"rule:{rule.id}:manual:{save.global_day}" if repeatable else f"rule:{rule.id}"
    amount = max(1, min(9, _int(data.get("amount"), 1)))
    return _record_credit(
        session, save, label=f"Save-a-Sim earned · {rule.label}", amount=amount, source_key=source_key,
        data={"award_reason": "Player confirmed a Save-a-Sim rule condition", "rule_id": rule.id,
              "rule_label": rule.label},
    )


def balance(session: Session, save: ChronicleSave) -> int:
    opening = max(0, _int((save.settings or {}).get("free_save_a_sims"), 0))
    entries = credit_entries(session, save)
    return max(0, opening + sum(_int((entry.data or {}).get("amount")) for entry in entries))


def rescuable_sims(session: Session, save: ChronicleSave) -> list[Record]:
    sims = _active_records(session, save, "sim")
    return sorted((sim for sim in sims if not _truthy((sim.data or {}).get("death_confirmed"))
                   and not _truthy((sim.data or {}).get("game_was_dead")) and _scheduled_day(sim) is not None),
                  key=lambda sim: (_scheduled_day(sim) or 10**9, sim.label.casefold()))


def spend_on_sim(session: Session, save: ChronicleSave, sim: Record, reason: str = "") -> dict:
    """Spend one credit to withdraw an unconfirmed scheduled death.

    Confirmed and game-observed deaths are intentionally protected.  The player
    can only save a Sim before confirming the death in Today / Automation.
    """
    sync_automatic_awards(session, save)
    if sim.kind != "sim" or sim.deleted or sim.save_id != save.id:
        raise ValueError("Choose a Sim from this save.")
    data = dict(sim.data or {})
    death_day = _scheduled_day(sim)
    if _truthy(data.get("death_confirmed")) or _truthy(data.get("game_was_dead")):
        raise ValueError("Confirmed game deaths cannot be reversed by a Save-a-Sim credit.")
    if death_day is None:
        raise ValueError("This Sim does not have a scheduled death to prevent.")
    if balance(session, save) < 1:
        raise ValueError("There are no Save-a-Sim credits available.")

    scheduled = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "death", Record.deleted.is_(False),
        Record.data["sim_id"].as_string() == sim.id,
        Record.data["completed"].as_boolean().is_not(True),
    )))
    source_roll_id = str(data.get("death_source_roll_id") or "")
    schedule_key = scheduled[0].id if scheduled else f"gd-{death_day}:{source_roll_id or 'manual'}"
    entry, made = _record_credit(
        session, save, label=f"Save-a-Sim used · {sim.label}", amount=-1,
        source_key=f"rescue:{sim.id}:{schedule_key}",
        data={"spent_reason": reason.strip()[:500] or "Saved before the scheduled death",
              "sim_id": sim.id, "sim_name": sim.label, "withdrawn_death_global_day": death_day,
              "death_source_roll_id": source_roll_id or None},
    )
    if not made:
        raise ValueError("That scheduled death has already been saved with a credit.")

    removed_deaths = 0
    for death in scheduled:
        base = death.version
        death.deleted = True
        death.data = {**(death.data or {}), "saved_by_save_a_sim_credit_id": entry.id,
                      "saved_global_day": save.global_day, "saved_reason": reason.strip()[:500]}
        death.version += 1
        _journal(session, death, "delete", base)
        removed_deaths += 1

    cleared = {
        "death_global_day", "cause_of_death", "death_source_roll_id", "death_date", "death_place",
        "historical_death_date", "historical_death_date_range", "death_date_precision",
        "death_game_hour", "death_game_minute", "death_time", "rescheduled_from_global_day",
        "rescheduled_from_cause",
    }
    sim_data = {key: value for key, value in data.items() if key not in cleared}
    sim_data.update({"death_confirmed": False, "save_a_sim_last_credit_id": entry.id,
                     "save_a_sim_last_saved_global_day": save.global_day,
                     "save_a_sim_saved_count": _int(data.get("save_a_sim_saved_count")) + 1})
    base = sim.version
    sim.data = sim_data
    sim.version += 1
    _journal(session, sim, "upsert", base)

    restored_rolls = 0
    if source_roll_id:
        retired = list(session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(True),
            Record.data["retired_by_death_roll_id"].as_string() == source_roll_id,
        )))
        for roll in retired:
            base = roll.version
            roll.data = {key: value for key, value in dict(roll.data or {}).items()
                         if key not in {"retired_reason", "retired_global_day", "retired_by_death_roll_id"}}
            roll.deleted = False
            roll.version += 1
            _journal(session, roll, "upsert", base)
            restored_rolls += 1
    save.revision += 1 + removed_deaths + restored_rolls
    return {"credit": entry, "removed_deaths": removed_deaths, "restored_rolls": restored_rolls,
            "withdrawn_death_global_day": death_day}


def dashboard(session: Session, save: ChronicleSave) -> dict:
    automatic = sync_automatic_awards(session, save)
    entries = credit_entries(session, save)
    earned = sum(max(0, _int((entry.data or {}).get("amount"))) for entry in entries)
    spent = -sum(min(0, _int((entry.data or {}).get("amount"))) for entry in entries)
    death_count = int(automatic["death_count"])
    next_milestone = ((death_count // 10) + 1) * 10
    return {
        "balance": balance(session, save), "opening_allowance": max(0, _int((save.settings or {}).get("free_save_a_sims"))),
        "earned": earned, "spent": spent, "entries": entries, "rules": rule_records(session, save),
        "rescuable_sims": rescuable_sims(session, save), "death_count": death_count,
        "next_milestone": next_milestone, "deaths_until_next": next_milestone - death_count,
        **automatic,
    }
