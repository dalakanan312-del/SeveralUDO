"""Harry Potter ancestry: four confirmed spellcaster grandparent positions."""
from sqlalchemy import or_, select, update

from . import harry_potter_rules, occult_rules
from .models import ChronicleSave, Record

STATUSES = ("Pureblood", "Half-Blood", "Muggle-Born", "Muggle", "Squib", "Unknown")
SOURCE = "Four-grandparent ancestry"
PARENT_KEYS = ("mother_id", "father_id")
ANCESTRY_RESULTS = {1: "Muggle", 2: "Muggle-Born", 3: "Squib", 4: "Half-Blood", 5: "Pureblood"}
ANCESTRY_TABLE = "; ".join(f"{face}: {label}" for face, label in ANCESTRY_RESULTS.items())


def rolled(data):
    return data.get("hp_blood_status_mode") == "rolled"


def enabled(save):
    return harry_potter_rules.PACK_ID in set((save.settings or {}).get("selected_rule_packs") or [])


def people(session, save):
    return {r.id: r for r in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim",
        or_(Record.deleted.is_(False), Record.data["infinite_frozen"].as_boolean().is_(True))))}


def spellcaster(person):
    """Tri-state; absent occult data is not evidence that someone is a Muggle."""
    if person is None:
        return None
    data = person.data or {}
    ability = str(data.get("hp_magical_ability") or "").strip().casefold()
    if ability in {"squib", "muggle", "non-magical", "nonmagical"}:
        return False
    if ability in {"witch", "wizard", "magical", "spellcaster", "muggle-born"}:
        return True
    if "Spellcaster" in occult_rules.sim_occult_types(data):
        return True
    # Explicit Human / other occult records are evidence; an unset/default
    # return from sim_occult_types is not. Squibs are non-spellcasters, but do
    # not erase the ancestry recorded for their own parents.
    raw = data.get("species_occult") or data.get("species")
    if raw and str(raw).strip().casefold() not in {"unknown", "unspecified", "none", "?"}:
        return False
    return None


def manual(data):
    if rolled(data):
        return False
    if data.get("hp_blood_status_mode") in {"auto", "manual"}:
        return data["hp_blood_status_mode"] == "manual"
    # Preserve player entries, but correct old birth-roll guesses automatically.
    return bool(data.get("hp_blood_status") and not data.get("hp_birth_roll_id")
                and data.get("hp_blood_status_source") != SOURCE)


def classify(sim, by_id):
    def relative(rid, forbidden=()):
        person = by_id.get(str(rid or ""))
        if not person or person.id in forbidden or person.save_id != sim.save_id or person.kind != "sim":
            return None
        if person.deleted and not (person.data or {}).get("infinite_frozen"):
            return None
        return person

    parents, grandparents = [], []
    for key, side in zip(PARENT_KEYS, ("Maternal", "Paternal")):
        parent = relative((sim.data or {}).get(key), (sim.id,))
        parents.append(parent)
        for gkey, role in zip(PARENT_KEYS, ("grandmother", "grandfather")):
            gp = relative((parent.data or {}).get(gkey), (sim.id, parent.id)) if parent else None
            magic = spellcaster(gp)
            grandparents.append({"role": side + " " + role, "id": gp.id if gp else None,
                "name": gp.label if gp else "Not linked", "spellcaster": magic,
                "evidence": "Spellcaster" if magic is True else "Not a spellcaster" if magic is False else "Magic unknown"})
    # Two parent slots referring to one Sim are invalid ancestry, not four
    # confirmed grandparents. Repeated grandparents across two distinct parents
    # are legitimate pedigree collapse and are evaluated in each position.
    invalid = parents[0] is not None and parents[0] is parents[1]
    known = sum(g["spellcaster"] is not None for g in grandparents)
    magical = sum(g["spellcaster"] is True for g in grandparents)
    nonmagical = sum(g["spellcaster"] is False for g in grandparents)
    self_magic = spellcaster(sim)
    ability = str((sim.data or {}).get("hp_magical_ability") or "").casefold()
    parent_magic = [spellcaster(p) for p in parents]
    muggle_parents = all(p is not None and spellcaster(p) is False and
        str((p.data or {}).get("hp_magical_ability") or "").casefold() != "squib" for p in parents)
    if invalid:
        status, reason = "Unknown", "Both parent links point to the same Sim; correct the parent links."
    elif ability == "muggle":
        status, reason = "Muggle", "Recorded as non-magical by a birth roll or player entry."
    elif magical == 4:
        status, reason = "Pureblood", "All four grandparent positions are confirmed spellcasters."
    elif self_magic is True and muggle_parents:
        status, reason = "Muggle-Born", "A spellcaster with two recorded non-magical parents; Squib parents do not count as Muggles here."
    elif nonmagical and (magical or any(v is True for v in parent_magic) or self_magic is True or ability == "squib"):
        status, reason = "Half-Blood", "Magical ancestry or ability is recorded, and at least one grandparent is confirmed not to be a spellcaster."
    else:
        status, reason = "Unknown", "Not enough confirmed ancestry to establish blood status. Two magical parents alone do not establish Pureblood."
    data = sim.data or {}
    override = manual(data)
    assigned = rolled(data)
    display = data.get("hp_blood_status") if override or assigned else status
    return {"status": status, "reason": reason, "grandparents": grandparents,
        "spellcasters": magical, "known": known, "unknown": 4-known,
        "manual": override, "rolled": assigned, "display": display,
        "roll_actual": data.get("hp_ancestry_roll_actual"),
        "can_roll": status == "Unknown" and display in (None, "", "Unknown") and not invalid
                    and not assigned and not data.get("hp_ancestry_roll_id") and not sim.deleted
                    and not any(data.get(k) for k in ("infinite_frozen", "death_confirmed", "game_was_dead")),
        "evidence": {"parents": [p.id if p else None for p in parents],
                     "grandparents": [{"id": g["id"], "spellcaster": g["spellcaster"]} for g in grandparents]}}


def updates(sim, by_id):
    if manual(sim.data or {}) or rolled(sim.data or {}):
        return {}
    result = classify(sim, by_id)
    return {"hp_blood_status": result["status"], "hp_blood_status_mode": "auto",
        "hp_blood_status_source": SOURCE, "hp_blood_status_reason": result["reason"],
        "hp_blood_status_evidence": result["evidence"]}


def automatic_enabled(session, save):
    from . import domain
    if not enabled(save) or not domain.automation_enabled(save):
        return False
    rule = session.scalar(select(Record).where(Record.save_id == save.id, Record.kind == "addon_rule",
        Record.deleted.is_(False), Record.data["rule_pack_id"].as_string() == harry_potter_rules.PACK_ID,
        Record.data["code"].as_string() == "HP-04"))
    return rule is None or bool((rule.data or {}).get("active"))


def refresh(session, save, by_id=None):
    from . import domain
    if not automatic_enabled(session, save):
        return 0
    by_id = people(session, save) if by_id is None else by_id
    changed = 0
    for sim in by_id.values():
        if sim.save_id != save.id or sim.deleted or (sim.data or {}).get("infinite_frozen"):
            continue
        values = updates(sim, by_id)
        if values and any((sim.data or {}).get(k) != v for k, v in values.items()):
            base = sim.version
            sim.data = {**(sim.data or {}), **values}
            sim.version += 1
            domain.journal(session, sim, "upsert", base)
            changed += 1
    return changed


def form_updates(value, current=None):
    value = str(value or "auto").strip()
    if value == "rolled" and rolled(current or {}):
        return {}
    if value == "auto":
        return {"hp_blood_status_mode": "auto", "hp_blood_status_source": SOURCE}
    if value not in STATUSES:
        raise ValueError("Choose automatic ancestry or a listed blood status.")
    return {"hp_blood_status": value, "hp_blood_status_mode": "manual",
        "hp_blood_status_source": "Player-entered", "hp_blood_status_reason": "Player override",
        "hp_blood_status_evidence": {}}


def validate_unknown_ancestry(session, save, sim):
    from . import infinite_decades
    if not enabled(save):
        raise ValueError("Enable the Harry Potter add-on before rolling unknown ancestry.")
    if (not sim or sim.save_id != save.id or sim.kind != "sim" or sim.deleted
            or infinite_decades.frozen(save) or (sim.data or {}).get("infinite_frozen")):
        raise ValueError("Choose an active Sim in this save, not a frozen branch record.")
    result = classify(sim, people(session, save))
    death = (sim.data or {}).get("death_global_day")
    try:
        deceased = death not in (None, "") and int(death) <= save.global_day
    except (ValueError, TypeError):
        deceased = False
    if not result["can_roll"] or deceased:
        raise ValueError("This d5 is only for living Sims with unresolved ancestry and no confirmed ancestry roll.")
    rule = session.scalar(select(Record).where(Record.save_id == save.id, Record.kind == "addon_rule",
        Record.deleted.is_(False), Record.data["rule_pack_id"].as_string() == harry_potter_rules.PACK_ID,
        Record.data["code"].as_string() == "HP-04"))
    if rule is None or not (rule.data or {}).get("active"):
        raise ValueError("Enable HP-04 Blood Status before rolling unknown ancestry.")
    return rule


def create_unknown_roll(session, save, sim):
    """Player-requested fallback, never an automatic replacement for known ancestry."""
    from . import domain
    # Serialize double-clicks before checking for an existing pending roll.
    session.flush()
    if session.get_bind().dialect.name == "sqlite":
        session.execute(update(ChronicleSave).where(ChronicleSave.id == save.id).values(revision=ChronicleSave.revision))
    session.refresh(save, with_for_update=True)
    if sim is not None:
        session.refresh(sim)
    rule = validate_unknown_ancestry(session, save, sim)
    source = f"harry-potter:HP-04:unknown-ancestry:{sim.id}"
    existing = session.scalar(select(Record).where(Record.save_id == save.id, Record.kind == "roll",
        Record.deleted.is_(False), Record.data["source"].as_string() == source))
    if existing:
        if (existing.data or {}).get("completed"):
            raise ValueError("This Sim already has a confirmed ancestry roll. Edit their magical fields to correct it.")
        return existing, False
    roll = Record(save_id=save.id, kind="roll", label=f"{sim.label} — Unknown ancestry",
        global_day=save.global_day, data={"sim_id": sim.id, "sim_name": sim.label,
        "source": source, "source_rule_id": rule.id, "source_rule_kind": "addon_rule",
        "source_rule_key": "hp_04", "rule_family": "Harry Potter Decades", "rule_generated": True,
        "hp_rule_code": "HP-04", "hp_unknown_ancestry": True, "roll_type": "Unknown ancestry",
        "die": "d5", "bad_results": "", "result_rules": ANCESTRY_TABLE,
        "nonlethal": True, "failure_is_lethal": False, "completed": False,
        "due_global_day": save.global_day,
        "notes": "Player-requested identity for unknown ancestry. Updates tracker magical identity, not the game or relatives. The result is labeled Rolled ancestry, not family-tree evidence."})
    session.add(roll); session.flush(); domain.journal(session, roll, "upsert", 0)
    return roll, True


def apply_unknown_roll(session, save, roll, actual):
    from . import domain
    if roll.save_id != save.id or roll.kind != "roll":
        raise ValueError("Choose an ancestry roll from this save.")
    sim = session.get(Record, str((roll.data or {}).get("sim_id") or ""))
    validate_unknown_ancestry(session, save, sim)
    if actual not in ANCESTRY_RESULTS:
        raise ValueError("Unknown ancestry uses a d5: enter 1 through 5.")
    data = sim.data or {}
    sex = str(data.get("sex") or data.get("game_sex") or "").strip().casefold()
    magical = data.get("hp_magical_ability") if data.get("hp_magical_ability") in {"Witch", "Wizard"} else (
        "Wizard" if sex in {"male", "m", "man", "boy"} else "Witch" if sex in {"female", "f", "woman", "girl"} else "Spellcaster")
    ability = "Muggle" if actual == 1 else "Squib" if actual == 3 else magical
    base = sim.version
    sim.data = {**data, "hp_blood_status": ANCESTRY_RESULTS[actual], "hp_blood_status_mode": "rolled",
        "hp_blood_status_source": "Unknown ancestry d5", "hp_blood_status_evidence": {},
        "hp_blood_status_reason": f"Player-confirmed d5 result {actual}: {ANCESTRY_RESULTS[actual]}. Not confirmed family-tree evidence.",
        "hp_ancestry_roll_id": roll.id, "hp_ancestry_roll_actual": actual,
        "hp_ancestry_roll_global_day": save.global_day, "hp_magical_ability": ability,
        "hp_hidden_squib": False, "hp_public_magical_status": ability}
    sim.version += 1; domain.journal(session, sim, "upsert", base)
    changed = 1
    # The chosen fallback identity supersedes an unresolved birth-identity check,
    # not completed history. Future scheduling must not recreate that check.
    for pending in session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "roll",
            Record.deleted.is_(False), Record.data["sim_id"].as_string() == sim.id)):
        if (pending.data or {}).get("hp_rule_code") in {"HP-05", "HP-06"} and not pending.data.get("completed"):
            base = pending.version; pending.deleted = True
            pending.data = {**pending.data, "retired_reason": "Identity resolved by confirmed unknown-ancestry d5",
                "retired_by_ancestry_roll_id": roll.id, "retired_global_day": save.global_day}
            pending.version += 1; domain.journal(session, pending, "delete", base); changed += 1
    return changed
