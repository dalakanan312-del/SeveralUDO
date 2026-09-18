"""The player-supplied SeveralUDO Maternal Mortality second-roll table.

The initial mortality check triggers a complication, not an immediate death.
Morbid's tables use only the Young Adult additional roll (the coin flip).
"""
import re
from copy import deepcopy

from sqlalchemy import select

from . import core_rulesets
from .models import Record

TABLE = {"Preteen": 6, "Teen": 4, "Young Adult": 2, "Adult": 4, "Elder": 10}
SOURCE = "SeveralUDO — Maternal Mortality: additional death-or-infertility roll"
FERTILITY_FIELDS = ("infertile", "fertility_status", "infertility_source")


def primary(roll):
    data = roll.data or {}
    return (roll.kind == "roll" and not data.get("maternal_followup")
            and "maternal" in str(data.get("roll_type") or "").casefold()
            and not data.get("event_id") and not data.get("occult_roll"))


def table_for(session, save, roll):
    if not primary(roll):
        return None
    data = roll.data or {}
    source = str(data.get("source") or "").split(":")
    rule_id = data.get("source_rule_id") or (source[2] if len(source) >= 3 and source[0] == "maternal" else None)
    rule = session.get(Record, rule_id) if rule_id else None
    if rule and (rule.save_id != save.id or rule.kind != "roll_rule"):
        rule = None
    core = data.get("core_ruleset_id") or (rule.data.get("core_ruleset_id") if rule else None) or core_rulesets.selected_core(save)
    if core not in {core_rulesets.SEVERALUDO, core_rulesets.MORBID}:
        return None
    # Morbid's exception selects the coin-flip table, not an age restriction.
    stage = "Young Adult" if core == core_rulesets.MORBID else next((name for name in TABLE
        if re.search(r"(?<![a-z])" + re.escape(name.casefold()) + r"(?![a-z])", str(data.get("roll_type") or "").casefold())), None)
    if stage is None:
        sim = session.get(Record, data.get("sim_id")) if data.get("sim_id") else None
        if not sim or sim.save_id != save.id:
            raise ValueError("Choose the mother before resolving this maternal roll.")
        from .domain import lifecycle_age_days
        try:
            age = int(data.get("delivery_global_day") or roll.global_day or save.global_day) - int(sim.data.get("birth_global_day", sim.global_day))
        except (TypeError, ValueError):
            raise ValueError("The mother's age at delivery is missing. Set her birth date or maternal age table before resolving this roll.")
        stage = next((name for name, limit in (("Preteen", 52), ("Teen", 72), ("Young Adult", 160), ("Adult", 240))
                      if age < lifecycle_age_days(save, limit)), "Elder")
    sides = TABLE[stage]
    return {"stage": stage, "die": f"d{sides}", "bad_results": "1", "core_ruleset_id": core,
            "result_rules": ("1: Heads — dies in labor; 2: Tails — survives a traumatic birth and is now infertile"
                if sides == 2 else f"1: Dies in labor; 2-{sides}: Survives a traumatic birth and is now infertile"),
            "rule_source": SOURCE, "coin_flip": sides == 2}


def schedule(session, save, origin):
    data = origin.data or {}
    spec = data.get("maternal_followup_table")
    if not data.get("completed") or not data.get("maternal_followup_required") or not spec:
        return 0
    from .domain import journal, failed
    if not failed(int(data.get("actual") or 0), str(data.get("bad_results") or "")):
        return 0
    sim = session.get(Record, data.get("sim_id"))
    if not sim or sim.save_id != save.id or sim.deleted or any(sim.data.get(key) for key in ("death_confirmed", "game_was_dead", "infinite_frozen")):
        return 0
    source = f"maternal-followup:{origin.id}"
    existing = session.scalar(select(Record).where(Record.save_id == save.id, Record.kind == "roll",
        Record.data["source"].as_string() == source))
    if existing and (not existing.deleted or existing.data.get("completed")
                     or existing.data.get("retired_reason") != "Origin roll reopened"):
        return 0
    baby = max(1, int(data.get("maternal_baby_index") or 1))
    due = max(int(origin.global_day or save.global_day), int(data.get("completed_global_day") or save.global_day))
    payload = {**spec, "roll_type": f"Maternal complication — {spec['stage']}",
        "source": source, "source_id": origin.id, "origin_roll_id": origin.id,
        "sim_id": sim.id, "sim_name": sim.label, "pregnancy_id": data.get("pregnancy_id") or data.get("source_id"),
        "maternal_baby_index": baby, "maternal_baby_id": data.get("maternal_baby_id"),
        "maternal_followup": True, "automatic_followup": True, "completed": False,
        "nonlethal": False, "failure_is_lethal": True, "due_global_day": due,
        "delivery_global_day": data.get("delivery_global_day") or origin.global_day,
        "notes": "Second roll after a failed maternal check. " + spec["result_rules"] +
            (". Morbid's maternal tables use this coin flip at every age." if spec['core_ruleset_id'] == core_rulesets.MORBID else "")}
    label = f"{sim.label} — Maternal death or infertility · Baby {baby}"
    if existing:
        base = existing.version
        existing.data, existing.label, existing.global_day, existing.deleted = payload, label, due, False
        existing.version += 1
        journal(session, existing, "upsert", base)
    else:
        child = Record(save_id=save.id, kind="roll", label=label, global_day=due, data=payload)
        session.add(child); session.flush(); journal(session, child, "upsert", 0)
    return 1


def resume_pending(session, save):
    """Catch up work recorded while automation was off, not old death history."""
    rows = session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "roll",
        Record.deleted.is_(False), Record.data["maternal_followup_required"].as_boolean().is_(True),
        Record.data["completed"].as_boolean().is_(True)))
    return sum(schedule(session, save, row) for row in list(rows))


def apply_infertility(session, save, roll):
    sim = session.get(Record, roll.data.get("sim_id"))
    if not sim or sim.save_id != save.id or sim.deleted:
        return 0
    from .domain import journal
    data = dict(sim.data or {})
    sources = list(data.get("maternal_infertility_roll_ids") or [])
    if roll.id in sources:
        return 0
    if not sources:
        data["maternal_fertility_before"] = {key: deepcopy(data[key]) for key in FERTILITY_FIELDS if key in data}
    data.update(infertile=True, fertility_status="Infertile after traumatic childbirth",
                infertility_source=SOURCE, maternal_infertility_roll_ids=sources + [roll.id])
    base = sim.version; sim.data = data; sim.version += 1; journal(session, sim, "upsert", base)
    return 1


def reopen_infertility(session, sim, roll):
    """Undo only this confirmed rule consequence when its roll is reopened."""
    if not sim or roll.id not in (sim.data.get("maternal_infertility_roll_ids") or []):
        return
    from .domain import journal
    data = dict(sim.data)
    if data.get("infertility_source") != SOURCE or data.get("fertility_status") != "Infertile after traumatic childbirth":
        raise ValueError("Fertility details changed after this result. Review the Sim profile before reopening it.")
    remaining = [value for value in data["maternal_infertility_roll_ids"] if value != roll.id]
    if remaining:
        data["maternal_infertility_roll_ids"] = remaining
    else:
        previous = data.pop("maternal_fertility_before", {})
        data.pop("maternal_infertility_roll_ids", None)
        for key in FERTILITY_FIELDS:
            data.pop(key, None)
        data.update(previous)
    base = sim.version; sim.data = data; sim.version += 1; journal(session, sim, "upsert", base)
