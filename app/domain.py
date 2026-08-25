from __future__ import annotations

import random
import re
import base64
import gzip
import json
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Change, ChronicleSave, Portrait, Record
from . import calendar_utils, occult_rules, sync
from .event_catalog_data import EVENT_LIBRARY_GZIP_BASE64


DEFAULT_STAGES = [
    ("Being Born", 0, "d20", "1-3"), ("Newborn", 0, "d20", "1"),
    ("Infant", 1, "d20", "1"), ("Toddler", 4, "d20", "1"),
    ("Child", 20, "d20", "1"), ("Preteen", 40, "d20", "1"),
    ("Teen", 52, "d20", "1"), ("Young Adult", 72, "d20", "1"),
    ("Adult", 160, "d20", "1"), ("Elder Death-Age RNG", 240, "d100", "1-20"),
]

AGING_STAGE_OFFSETS = {stage.casefold(): age for stage, age, _die, _bad in DEFAULT_STAGES}

DEFAULT_DEATH_CAUSES = {
    "birth": ["Complications of childbirth", "Childbed fever", "Hemorrhage"],
    "infant": ["Infant fever", "Respiratory infection", "Unknown childhood illness"],
    "child": ["Fever", "Accident", "Infectious disease"],
    "adult": ["Pneumonia", "Infectious disease", "Accident", "Sudden illness"],
    "elder": ["Old age", "Pneumonia", "Stroke", "Heart failure"],
}

DEFAULT_MATERNAL_RULES = [
    ("Maternal — Preteen", "d20", "1-8"),
    ("Maternal — Teen", "d20", "1-6"),
    ("Maternal — Young Adult", "d20", "1-4"),
    ("Maternal — Adult", "d20", "1-5"),
    ("Maternal — Elder", "d20", "1-8"),
]

DEFAULT_MULTIPLE_BIRTH_ERAS = (
    (1200, 1749), (1750, 1865), (1866, 1895), (1896, 9999),
)

# Location-neutral starter guidance. Every entry remains editable per save.
DEFAULT_ERA_GUIDANCE = [
    ("Household-based livelihoods", "Careers & education", -9999, 1499, "Most work is tied to household, land, craft, trade, or local service. Formal schooling depends on class, faith, sex, and location."),
    ("Marriage as a household alliance", "Marriage & family", -9999, 1499, "Treat marriage as a family, property, labor, or political alliance as well as a personal relationship."),
    ("Customary inheritance", "Inheritance", -9999, 1499, "Use the selected succession system, legitimacy, birth order, and local custom."),
    ("Limited medical care", "Health", -9999, 1499, "Care is domestic or local. Illness, childbirth, infection, and injury carry serious consequences."),
    ("Early modern households", "Marriage & family", 1500, 1699, "Household formation, inheritance, religion, trade, migration, and reputation remain central."),
    ("Print, trade, and expanding literacy", "Careers & education", 1500, 1699, "Literacy and specialized work expand unevenly through schools, apprenticeships, clerical work, and trade."),
    ("Agrarian and commercial life", "Economy", 1700, 1799, "Land and household production remain important while commerce and wage work grow."),
    ("Industrial transition", "Economy", 1800, 1849, "Introduce factories, wage labor, urban crowding, and faster transport only where industrialization has reached the location."),
    ("Rail, steam, and telegraph transition", "Building & technology", 1850, 1913, "Introduce rail, steam, telegraphy, photography, sanitation, and electricity by location and class."),
    ("Total-war disruption", "Military", 1914, 1945, "Major wars may affect soldiers and civilians through service, evacuation, rationing, displacement, injury, and bereavement."),
    ("Postwar household change", "Marriage & family", 1946, 1969, "Let local law and culture govern marriage, divorce, adoption, legitimacy, gender roles, and reproductive choices."),
    ("Rights and opportunities in transition", "Careers & education", 1970, 1999, "Education, employment, family law, and civil rights broaden at different rates by location."),
    ("Contemporary local rules", "Other", 2000, 9999, "Use current-year rules for the location while retaining differences in law, culture, cost, healthcare, and opportunity."),
]

DEFAULT_PLANNER_RULES = [
    ("Side Household Pregnancy", -9999, 1299, "d20", "1-14: Schedule that many pregnancies; 15-20: No pregnancy", "Annual pregnancy-count roll"),
    ("Side Household Pregnancy", 1300, 1399, "d20", "1-13: Schedule that many pregnancies; 14-20: No pregnancy", "Annual pregnancy-count roll"),
    ("Side Household Pregnancy", 1400, 1499, "d20", "1-11: Schedule that many pregnancies; 12-15: One pregnancy; 16-20: No pregnancy", "Annual pregnancy-count roll"),
    ("Side Household Pregnancy", 1500, 1699, "d12", "1-10: Schedule that many pregnancies; 11-12: No pregnancy", "Annual pregnancy-count roll"),
    ("Side Household Pregnancy", 1700, 1799, "d10", "1-8: Schedule that many pregnancies; 9-10: No pregnancy", "Annual pregnancy-count roll"),
    ("Side Household Pregnancy", 1800, 1899, "d10", "1-8: Schedule that many pregnancies; 9-10: No pregnancy", "Annual pregnancy-count roll"),
    ("Side Household Pregnancy", 1900, 9999, "d6", "1-5: Schedule that many pregnancies; 6: No pregnancy", "Annual pregnancy-count roll"),
    ("Non-Heir Marriage Eligibility", -9999, 1299, "d12", "1", "Marriage eligibility for a non-heir"),
    ("Non-Heir Marriage Eligibility", 1300, 1499, "d10", "1", "Marriage eligibility for a non-heir"),
    ("Non-Heir Marriage Eligibility", 1500, 1799, "d8", "1", "Marriage eligibility for a non-heir"),
    ("Non-Heir Marriage Eligibility", 1800, 9999, "d6", "1", "Marriage eligibility for a non-heir"),
]

CLOSED_PREGNANCIES = {"delivered", "complete", "completed", "miscarriage", "stillbirth", "cancelled", "canceled", "closed"}
CLOSED_ILLNESSES = {"recovered", "resolved", "deceased", "ended", "closed"}


def due_on_today(record: Record, global_day: int) -> bool:
    """Return whether a record belongs in Today's actionable queue."""
    if record.deleted or record.global_day is None or int(record.global_day) > global_day:
        return False
    data = record.data or {}
    if record.kind == "roll":
        due_day = record.global_day if record.global_day is not None else data.get("due_global_day")
        try:
            return int(due_day) >= 1 and not bool(data.get("completed"))
        except (TypeError, ValueError):
            return False
    if record.kind == "pregnancy":
        return str(data.get("status") or "active").strip().casefold() not in CLOSED_PREGNANCIES
    if record.kind == "event":
        if event_is_ignored(record) or not bool(data.get("active", True)) or bool(data.get("completed")):
            return False
        end_day = data.get("end_global_day")
        return end_day is None or int(end_day) >= global_day
    if record.kind == "illness":
        if str(data.get("status") or "active").strip().casefold() in CLOSED_ILLNESSES:
            return False
        end_day = data.get("end_global_day")
        return end_day in (None, "") or int(end_day) >= global_day
    if record.kind == "death":
        return not bool(data.get("completed"))
    return False


def event_is_ignored(record: Record) -> bool:
    """Return whether an event has been intentionally hidden for this save."""
    return record.kind == "event" and bool((record.data or {}).get("ignored"))


def journal(session: Session, record: Record, operation: str, base_version: int) -> None:
    session.add(Change(
        save_id=record.save_id, device_id="automation", record_id=record.id,
        kind=record.kind, operation=operation, base_version=base_version,
        new_version=record.version, payload=sync.serialize(record),
    ))


def roll_obligation_identity(record: Record) -> tuple[str, str, int] | None:
    """Return the conservative identity used to find duplicate roll obligations.

    A repairable duplicate must point to the same Sim, have the same named roll
    type, and be due on the same Global Day.  Incomplete legacy rows are left
    alone because grouping them without those three facts could merge unrelated
    obligations.
    """
    if record.kind != "roll" or record.deleted:
        return None
    data = record.data or {}
    sim_id = str(data.get("sim_id") or "").strip()
    roll_type = re.sub(r"\s+", " ", str(data.get("roll_type") or "").strip().casefold())
    raw_day = record.global_day if record.global_day is not None else data.get("due_global_day")
    try:
        due_day = int(raw_day)
    except (TypeError, ValueError):
        return None
    if not sim_id or not roll_type or due_day < 1:
        return None
    return sim_id, roll_type, due_day


def _obligation_keeper(record: Record) -> tuple[int, int, str, str]:
    """Prefer the richest and most recently maintained pending obligation."""
    data = record.data or {}
    useful = (
        "die", "bad_results", "result_rules", "failure_outcome", "success_outcome",
        "source", "source_id", "event_id", "event_rule_id", "pregnancy_id",
    )
    richness = sum(value not in (None, "", [], {}) for value in (data.get(key) for key in useful))
    return richness, int(record.version or 0), str(record.updated_at or record.created_at or ""), record.id


def duplicate_obligation_groups(records: list[Record]) -> list[dict]:
    """Describe active duplicate obligations and which pending rows are repairable."""
    identities: dict[tuple[str, str, int], list[Record]] = defaultdict(list)
    for record in records:
        identity = roll_obligation_identity(record)
        if identity is not None:
            identities[identity].append(record)

    groups = []
    for identity, matches in identities.items():
        if len(matches) < 2:
            continue
        completed = [item for item in matches if bool((item.data or {}).get("completed"))]
        pending = [item for item in matches if not bool((item.data or {}).get("completed"))]
        keeper = max(pending, key=_obligation_keeper) if pending and not completed else None
        redundant = list(pending) if completed else [item for item in pending if item is not keeper]
        example = completed[0] if completed else keeper or matches[0]
        groups.append({
            "identity": identity,
            "label": example.label,
            "matches": matches,
            "pending": pending,
            "completed": completed,
            "keeper": completed[0] if completed else keeper,
            "redundant": redundant,
        })
    return sorted(groups, key=lambda group: (group["identity"][2], group["label"].casefold()))


def duplicate_obligation_summary(records: list[Record]) -> dict:
    groups = duplicate_obligation_groups(records)
    return {
        "groups": len(groups),
        "repairable": sum(len(group["redundant"]) for group in groups),
        "protected_completed": sum(max(0, len(group["completed"]) - 1) for group in groups),
        "preview": [{
            "label": group["label"],
            "global_day": group["identity"][2],
            "copies": len(group["matches"]),
            "repairable": len(group["redundant"]),
            "completed": len(group["completed"]),
        } for group in groups[:12]],
    }


def repair_duplicate_obligations(session: Session, save: ChronicleSave) -> dict:
    """Archive redundant pending obligations without touching completed results."""
    rolls = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "roll",
        Record.deleted.is_(False),
    )))
    groups = duplicate_obligation_groups(rolls)
    archived = 0
    for group in groups:
        keeper = group["keeper"]
        for record in group["redundant"]:
            base = record.version
            record.deleted = True
            record.data = {
                **(record.data or {}),
                "duplicate_repair": True,
                "duplicate_of": keeper.id if keeper else "",
                "retired_reason": "Duplicate obligation",
                "retired_global_day": save.global_day,
            }
            record.version += 1
            journal(session, record, "delete", base)
            archived += 1
    save.revision += archived
    return {
        "groups": len(groups),
        "archived": archived,
        "protected_completed": sum(max(0, len(group["completed"]) - 1) for group in groups),
    }


AUTOMATIC_GENERATION_SOURCES = {"parents", "spouse"}


def surname_at_birth(sim: Record) -> str:
    """Return the preserved family surname a Sim had at birth."""
    data = sim.data or {}
    return str(data.get("surname_at_birth") or data.get("maiden_name") or data.get("last_name") or "").strip()


def married_surname(sim: Record) -> str:
    data = sim.data or {}
    return str(data.get("married_surname") or data.get("married_name") or "").strip()


def _sim_display_name(data: dict) -> str:
    return " ".join(
        str(data.get(key) or "").strip()
        for key in ("title", "first_name", "last_name", "suffix")
        if str(data.get(key) or "").strip()
    )


def apply_married_surnames(
    session: Session,
    relationship: Record,
    first: Record,
    second: Record,
    surname_rule: str = "automatic",
    respect_existing: bool = False,
) -> int:
    """Preserve birth surnames and apply a marriage naming convention."""
    normalized = str(surname_rule or "automatic").strip().casefold().replace("-", "_")
    allowed = {"automatic", "partner1_takes_partner2", "partner2_takes_partner1", "keep", "hyphenate"}
    if normalized not in allowed:
        normalized = "automatic"

    sims = (first, second)
    proposed: dict[str, dict] = {sim.id: dict(sim.data or {}) for sim in sims}
    for sim in sims:
        data = proposed[sim.id]
        birth = surname_at_birth(sim)
        if birth:
            data["surname_at_birth"] = birth
            data["maiden_name"] = birth  # legacy export/import compatibility

    relation_data = relationship.data or {}
    relation_type = str(relation_data.get("type") or "").casefold()
    status = str(relation_data.get("status") or "Active").casefold()
    is_marriage = bool(relation_data.get("legally_married")) or "marriage" in relation_type
    if is_marriage and status not in {"ended", "divorced", "annulled"} and normalized != "keep":
        first_current = str(proposed[first.id].get("last_name") or surname_at_birth(first)).strip()
        second_current = str(proposed[second.id].get("last_name") or surname_at_birth(second)).strip()
        targets: list[tuple[Record, str]] = []
        if normalized == "hyphenate":
            parts = [surname_at_birth(first) or first_current, surname_at_birth(second) or second_current]
            unique_parts = [part for index, part in enumerate(parts) if part and part not in parts[:index]]
            combined = "-".join(unique_parts)
            targets = [(first, combined), (second, combined)] if combined else []
        else:
            if normalized == "automatic":
                first_female = str(proposed[first.id].get("sex") or "").casefold() in {"female", "woman", "f"}
                second_female = str(proposed[second.id].get("sex") or "").casefold() in {"female", "woman", "f"}
                normalized = "partner1_takes_partner2" if first_female and not second_female else "partner2_takes_partner1"
            targets = [(first, second_current)] if normalized == "partner1_takes_partner2" else [(second, first_current)]
        for target, new_surname in targets:
            if not new_surname:
                continue
            data = proposed[target.id]
            if respect_existing and married_surname(target) and str(data.get("married_name_source_relationship_id") or "") != relationship.id:
                continue
            data["last_name"] = new_surname
            data["married_surname"] = new_surname
            data["married_name"] = new_surname  # legacy export/import compatibility
            data["married_name_source_relationship_id"] = relationship.id

    changed = 0
    for sim in sims:
        data = proposed[sim.id]
        if data == (sim.data or {}):
            continue
        base = sim.version
        sim.data = data
        sim.label = _sim_display_name(data) or sim.label
        sim.version += 1
        journal(session, sim, "upsert", base)
        changed += 1
    return changed


def backfill_married_surnames(session: Session, save: ChronicleSave) -> int:
    """Bring existing active marriages into the automatic name-history model."""
    sims = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False)
    )))
    by_id = {sim.id: sim for sim in sims}
    relationships = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "relationship", Record.deleted.is_(False)
    ).order_by(Record.global_day, Record.created_at)))
    changed = 0
    for relationship in relationships:
        data = relationship.data or {}
        first = by_id.get(str(data.get("partner1_id") or ""))
        second = by_id.get(str(data.get("partner2_id") or ""))
        if first and second:
            changed += apply_married_surnames(
                session, relationship, first, second, str(data.get("surname_rule") or "automatic"), respect_existing=True
            )
    return changed


def sync_generations(session: Session, save: ChronicleSave) -> int:
    """Fill and maintain automatic generations from parents, then spouses.

    A known parent's generation always wins and places the child one generation
    later.  A spouse is used only when both parent links are unknown, and spouses
    share the same generation.  Existing generations without an automatic source
    are treated as intentional manual values and are never overwritten.
    """
    sims = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "sim",
        Record.deleted.is_(False),
    )))
    if not sims:
        return 0
    by_id = {sim.id: sim for sim in sims}
    relationships = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "relationship",
        Record.deleted.is_(False),
    ).order_by(Record.global_day.desc(), Record.created_at.desc())))
    spouses: dict[str, list[str]] = {sim.id: [] for sim in sims}
    for relationship in relationships:
        data = relationship.data or {}
        relation_type = str(data.get("type") or "").casefold()
        status = str(data.get("status") or "active").casefold()
        married = bool(data.get("legally_married")) or "marriage" in relation_type or "spouse" in relation_type
        if not married or status in {"ended", "divorced", "annulled"}:
            continue
        first, second = str(data.get("partner1_id") or ""), str(data.get("partner2_id") or "")
        if first in by_id and second in by_id and first != second:
            spouses[first].append(second)
            spouses[second].append(first)

    changed = 0
    # Several passes allow a parent's or spouse's newly inferred value to feed
    # the next relationship without recursion or expensive graph rebuilding.
    for _ in range(max(1, len(sims))):
        pass_changed = False
        for sim in sims:
            data = dict(sim.data or {})
            current_generation = data.get("generation")
            current_source = str(data.get("generation_source") or "").casefold()
            if current_generation not in (None, "") and current_source not in AUTOMATIC_GENERATION_SOURCES:
                continue

            parent_ids = [str(data.get(field) or "") for field in ("mother_id", "father_id")]
            parent_ids = [parent_id for parent_id in parent_ids if parent_id in by_id]
            parent_generations = [
                int((by_id[parent_id].data or {}).get("generation"))
                for parent_id in parent_ids
                if (by_id[parent_id].data or {}).get("generation") not in (None, "")
            ]
            inferred = max(parent_generations) + 1 if parent_generations else None
            source = "parents" if inferred is not None else None
            source_ids = parent_ids if inferred is not None else []

            # A spouse is a fallback only when both parents are genuinely
            # unknown, not when a linked parent's generation is merely missing.
            if inferred is None and not parent_ids:
                for spouse_id in spouses.get(sim.id, []):
                    spouse_generation = (by_id[spouse_id].data or {}).get("generation")
                    if spouse_generation not in (None, ""):
                        inferred = int(spouse_generation)
                        source = "spouse"
                        source_ids = [spouse_id]
                        break

            normalized_current = int(current_generation) if current_generation not in (None, "") else None
            existing_ids = list(data.get("generation_source_ids") or [])
            if normalized_current == inferred and current_source == (source or "") and existing_ids == source_ids:
                continue
            base = sim.version
            data["generation"] = inferred
            if source:
                data["generation_source"] = source
                data["generation_source_ids"] = source_ids
            else:
                data.pop("generation_source", None)
                data.pop("generation_source_ids", None)
            sim.data = data
            sim.version += 1
            journal(session, sim, "upsert", base)
            changed += 1
            pass_changed = True
        if not pass_changed:
            break
    return changed


SIM_SCALAR_REFERENCES = {
    "sim_id", "mother_id", "father_id", "partner1_id", "partner2_id",
    "other_sim_id", "head_id", "head_sim_id", "head_of_household_id", "heir_id",
    "current_heir_id", "first_recorded_sim_id", "inferred_mother_id",
    "inferred_father_id", "inferred_other_parent_id",
}
SIM_LIST_REFERENCES = {"sim_ids", "member_ids", "parent_ids", "children_ids", "affected_sim_ids"}
SIM_DEPENDENT_KINDS = {
    "relationship", "pregnancy", "roll", "event_result", "illness", "death",
    "game_candidate", "game_history", "detection_candidate", "task",
}


def sim_delete_impact(session: Session, sim: Record) -> dict:
    """Describe every tracker row that a permanent Sim deletion would touch."""
    from collections import Counter

    records = list(session.scalars(select(Record).where(
        Record.save_id == sim.save_id, Record.id != sim.id, Record.deleted.is_(False),
    )))
    dependent, detached = [], []
    for record in records:
        data = record.data or {}
        scalar_hit = any(str(data.get(key) or "") == sim.id for key in SIM_SCALAR_REFERENCES)
        list_hit = any(sim.id in {str(value) for value in (data.get(key) or [])}
                       for key in SIM_LIST_REFERENCES if isinstance(data.get(key), list))
        if not scalar_hit and not list_hit:
            continue
        (dependent if record.kind in SIM_DEPENDENT_KINDS else detached).append(record)
    portraits = list(session.scalars(select(Portrait).where(Portrait.record_id == sim.id)))
    return {"dependent":dependent, "detached":detached, "portraits":portraits,
            "dependent_counts":dict(Counter(item.kind for item in dependent)),
            "detached_counts":dict(Counter(item.kind for item in detached))}


def purge_sim(session: Session, save: ChronicleSave, sim: Record) -> dict:
    """Permanently remove an accidental Sim while preserving referential integrity."""
    if sim.kind != "sim" or sim.save_id != save.id:
        raise ValueError("Only a Sim in the open save can be deleted.")
    impact = sim_delete_impact(session, sim)
    for record in impact["dependent"]:
        base = record.version
        record.deleted = True
        record.data = {**(record.data or {}), "archived_reason":"Related Sim was permanently deleted", "deleted_sim_id":sim.id}
        record.version += 1
        journal(session, record, "delete", base)
    for record in impact["detached"]:
        data = dict(record.data or {})
        changed = False
        for key in SIM_SCALAR_REFERENCES:
            if str(data.get(key) or "") == sim.id:
                data[key] = None; changed = True
        for key in SIM_LIST_REFERENCES:
            values = data.get(key)
            if isinstance(values, list) and sim.id in {str(value) for value in values}:
                data[key] = [value for value in values if str(value) != sim.id]; changed = True
        if changed:
            base = record.version; record.data = data; record.version += 1; journal(session, record, "upsert", base)
    for portrait in impact["portraits"]:
        sync.sync_portrait(session, save, portrait, sim.id, portrait.stage, deleted=True)
        session.delete(portrait)
    settings = dict(save.settings or {})
    for key in ("current_heir_id", "main_sim_id", "founder_sim_id"):
        if str(settings.get(key) or "") == sim.id:
            settings[key] = None
    save.settings = settings
    base = sim.version
    sim.deleted = True; sim.version += 1
    journal(session, sim, "delete", base)
    session.flush()
    session.delete(sim)
    save.revision += 1 + len(impact["dependent"]) + len(impact["detached"]) + len(impact["portraits"])
    return {"archived":len(impact["dependent"]), "detached":len(impact["detached"]), "portraits":len(impact["portraits"])}


def end_illnesses_for_death(session: Session, save: ChronicleSave, sim: Record, death_day: int) -> int:
    """Close every still-active illness for a Sim on their recorded death day."""
    if int(death_day) > int(save.global_day) and not bool((sim.data or {}).get("death_confirmed")):
        return 0
    illnesses = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "illness",
        Record.deleted.is_(False),
        Record.data["sim_id"].as_string() == sim.id,
    )))
    changed = 0
    for illness in illnesses:
        data = dict(illness.data or {})
        closed = str(data.get("status") or "active").strip().casefold() in CLOSED_ILLNESSES
        ended_by_death = str(data.get("outcome") or "").strip().casefold() == "ended by death"
        # If a failed roll moves an already scheduled death earlier, move the
        # associated illness end date too instead of leaving it on the old day.
        if closed and not (ended_by_death and int(data.get("end_global_day") or death_day) != int(death_day)):
            continue
        base = illness.version
        data.update({"status": "Deceased", "end_global_day": int(death_day), "outcome": "Ended by death"})
        illness.data = data
        illness.version += 1
        journal(session, illness, "upsert", base)
        changed += 1
    return changed


def retire_pregnancy_rolls(session: Session, save: ChronicleSave, pregnancy_id: str, reason: str = "Pregnancy closed") -> int:
    """Archive unfinished maternal rolls when a pregnancy no longer needs them."""
    rolls = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "roll",
        Record.deleted.is_(False),
    )))
    changed = 0
    source_prefix = f"maternal:{pregnancy_id}:"
    for roll in rolls:
        data = dict(roll.data or {})
        if data.get("completed"):
            continue
        if data.get("source_id") != pregnancy_id and not str(data.get("source") or "").startswith(source_prefix):
            continue
        base = roll.version
        roll.deleted = True
        roll.data = {**data, "retired_reason": reason, "retired_global_day": save.global_day}
        roll.version += 1
        journal(session, roll, "delete", base)
        changed += 1
    return changed


def retire_dead_sim_rolls(session: Session, save: ChronicleSave, sims: list[Record] | None = None) -> int:
    """Archive every unfinished roll once its Sim's death day has arrived."""
    sims = sims if sims is not None else list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False)
    )))
    dead_ids = {
        sim.id for sim in sims
        if bool((sim.data or {}).get("game_was_dead")) or (
            sim.data.get("death_global_day") is not None
            and int(sim.data["death_global_day"]) <= save.global_day
        )
    }
    if not dead_ids:
        return 0
    rolls = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False)
    )))
    changed = 0
    for roll in rolls:
        data = dict(roll.data or {})
        post_death_roll = bool(data.get("allow_after_death")) or (bool(data.get("occult_roll")) and data.get("occult_rule_key") == "ghost_persistence")
        if data.get("completed") or data.get("sim_id") not in dead_ids or post_death_roll:
            continue
        base = roll.version
        roll.deleted = True
        roll.data = {**data, "retired_reason": "Sim is deceased", "retired_global_day": save.global_day}
        roll.version += 1
        journal(session, roll, "delete", base)
        changed += 1
    return changed


def retire_prechallenge_rolls(session: Session, save: ChronicleSave) -> int:
    """Archive unfinished obligations dated before Global Day 1.

    Events and Sims may legitimately retain pre-challenge dates for historical
    context.  Those dates must never become actionable rolls, while completed
    imported results remain part of the chronicle.
    """
    rolls = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False)
    )))
    changed = 0
    for roll in rolls:
        data = dict(roll.data or {})
        raw_day = roll.global_day if roll.global_day is not None else data.get("due_global_day")
        try:
            due_day = int(raw_day)
        except (TypeError, ValueError):
            continue
        if data.get("completed") or due_day >= 1:
            continue
        base = roll.version
        roll.deleted = True
        roll.data = {
            **data,
            "retired_reason": "Obligation predates challenge start",
            "retired_global_day": save.global_day,
        }
        roll.version += 1
        journal(session, roll, "delete", base)
        changed += 1
    return changed


def seed_defaults(session: Session, save: ChronicleSave) -> int:
    created = 0
    existing_rules = {item.label.casefold() for item in session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "roll_rule", Record.deleted.is_(False)))}
    for stage, age, die, bad in DEFAULT_STAGES:
        if stage.casefold() in existing_rules:
            continue
        record = Record(save_id=save.id, kind="roll_rule", label=stage, data={"age_days": age, "die": die, "bad_results": bad, "active": True})
        session.add(record); session.flush(); journal(session, record, "upsert", 0); created += 1
    for label, die, bad in DEFAULT_MATERNAL_RULES:
        if label.casefold() in existing_rules:
            continue
        record = Record(save_id=save.id, kind="roll_rule", label=label, data={"age_days": None, "die": die, "bad_results": bad, "active": True, "source": "built-in maternal baseline"})
        session.add(record); session.flush(); journal(session, record, "upsert", 0); created += 1
    existing_causes = {item.label.casefold() for item in session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "death_causes", Record.deleted.is_(False)))}
    for group, causes in DEFAULT_DEATH_CAUSES.items():
        if group.casefold() in existing_causes:
            continue
        record = Record(save_id=save.id, kind="death_causes", label=group.title(), data={"causes": causes, "active": True})
        session.add(record); session.flush(); journal(session, record, "upsert", 0); created += 1
    existing_guidance = {item.label.casefold() for item in session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "era_guidance", Record.deleted.is_(False)))}
    for title, category, start_year, end_year, text in DEFAULT_ERA_GUIDANCE:
        if title.casefold() in existing_guidance:
            continue
        record = Record(save_id=save.id, kind="era_guidance", label=title, data={"category": category, "start_year": start_year, "end_year": end_year, "location": "All", "rule_text": text, "active": True, "source": "Built-in editable baseline"})
        session.add(record); session.flush(); journal(session, record, "upsert", 0); created += 1
    existing_planner = {(item.label.casefold(), int(item.data.get("start_year", -9999))) for item in session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "planner_rule", Record.deleted.is_(False)))}
    for label, start_year, end_year, die, bad, notes in DEFAULT_PLANNER_RULES:
        if (label.casefold(), start_year) in existing_planner:
            continue
        record = Record(save_id=save.id, kind="planner_rule", label=label, data={"start_year": start_year, "end_year": end_year, "die": die, "bad_results": bad, "notes": notes, "active": True})
        session.add(record); session.flush(); journal(session, record, "upsert", 0); created += 1
    existing_multiple={(int(item.data.get("start_year",-9999)),int(item.data.get("end_year",9999))) for item in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="multiple_birth_rule",Record.deleted.is_(False)))}
    for start_year,end_year in DEFAULT_MULTIPLE_BIRTH_ERAS:
        if (start_year,end_year) in existing_multiple: continue
        record=Record(save_id=save.id,kind="multiple_birth_rule",label=f"Multiple births · {start_year}–{end_year}",data={"start_year":start_year,"end_year":end_year,"max_babies":None,"quintuplet_policy":"","active":True,"notes":"Historical range supplied; enter only sourced limits. Blank means no enforced limit."})
        session.add(record);session.flush();journal(session,record,"upsert",0);created+=1
    created += seed_occult_rules(session, save)
    save.revision += created
    event_created=seed_event_catalog(session,save)
    generation_updates=sync_generations(session,save)
    save.revision+=generation_updates
    save_settings=dict(save.settings or {});save_settings["defaults_schema_version"]="4.1.1";save.settings=save_settings
    return created+event_created+generation_updates


def seed_occult_rules(session: Session, save: ChronicleSave) -> int:
    """Install the supplied occult rules without enabling automatic scheduling."""
    existing = {
        str((item.data or {}).get("default_id") or "")
        for item in session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "occult_rule", Record.deleted.is_(False)
        ))
    }
    created = 0
    for definition in occult_rules.DEFAULT_OCCULT_RULES:
        data = {key:value for key,value in definition.items() if key != "label"}
        default_id = f"{data['rule_key']}:{data.get('start_year',-9999)}:{data.get('end_year',9999)}"
        data["default_id"] = default_id
        if default_id in existing:
            continue
        record = Record(save_id=save.id, kind="occult_rule", label=definition["label"], data=data)
        session.add(record); session.flush(); journal(session, record, "upsert", 0)
        existing.add(default_id); created += 1
    return created


def multiple_birth_limit(session: Session, save: ChronicleSave, global_day: int | None) -> dict | None:
    day=int(global_day if global_day is not None else save.global_day)
    year=save.start_year+(day-1)//max(1,save.days_per_year)
    matches=[]
    for item in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="multiple_birth_rule",Record.deleted.is_(False))):
        data=item.data or {}
        if not bool(data.get("active",True)) or (data.get("max_babies") in (None,"") and not data.get("quintuplet_policy")): continue
        start=int(data.get("start_year",-9999));end=int(data.get("end_year",9999))
        if start<=year<=end: matches.append((end-start,item))
    if not matches: return None
    item=min(matches,key=lambda pair:pair[0])[1]
    raw_max=item.data.get("max_babies")
    return {"record":item,"year":year,"max_babies":max(1,int(raw_max)) if raw_max not in (None,"") else None,"quintuplet_policy":str(item.data.get("quintuplet_policy") or "")}


def validate_multiple_birth_count(session: Session, save: ChronicleSave, global_day: int | None, count: int) -> None:
    rule=multiple_birth_limit(session,save,global_day)
    if rule and rule["max_babies"] is not None and int(count)>rule["max_babies"]:
        raise ValueError(f"The editable multiple-birth rule for {rule['year']} allows at most {rule['max_babies']} babies. Correct the count or edit that rule first.")
    policy=str((rule or {}).get("quintuplet_policy") or "").casefold()
    if rule and int(count)==5 and any(marker in policy for marker in ("reroll","not allowed","disallow")):
        raise ValueError(f"The editable multiple-birth rule for {rule['year']} says quintuplets must be rerolled. Correct the count or edit that rule first.")


def seed_event_catalog(session: Session, save: ChronicleSave, *, force: bool = False) -> int:
    """Install and repair the approved 655-event catalog without duplicates.

    Earlier builds treated any large partial import as complete.  The integrity
    pass now checks stable catalog IDs so every new or migrated save receives
    every approved row while preserving custom events and intentional archives.
    """
    marker = str((save.settings or {}).get("event_catalog_version") or "")
    rows = json.loads(gzip.decompress(base64.b64decode(EVENT_LIBRARY_GZIP_BASE64)).decode("utf-8"))
    existing_ids = {str(item.data.get("catalog_id") or item.data.get("event_id") or "") for item in session.scalars(
        select(Record).where(Record.save_id == save.id, Record.kind == "event")
    )}
    approved_ids={str(row.get("event_id") or "") for row in rows}
    if marker == "recovered-655-v2-integrity" and approved_ids.issubset(existing_ids) and not force:
        return 0
    created = 0
    for row in rows:
        catalog_id = str(row.get("event_id") or "")
        if catalog_id in existing_ids:
            continue
        def rebase(value):
            if value is None: return None
            value = int(value)
            absolute_year = 1200 + (value - 1) // 4
            challenge_day = ((value - 1) % 4) + 1
            return (absolute_year - save.start_year) * save.days_per_year + min(challenge_day, save.days_per_year)
        start = rebase(row.get("start_global_day"))
        end = rebase(row.get("end_global_day"))
        data = {
            "catalog_id": catalog_id, "start_global_day": start, "end_global_day": end,
            "scope": row.get("scope") or "Historical event", "location": row.get("location") or "Global",
            "roll_required": bool(row.get("roll_required")), "affected_class": row.get("affected_class") or "All applicable Sims",
            "active": bool(row.get("active", 1)), "source": row.get("source") or "Recovered approved catalog",
            "notes": row.get("notes") or "", "die": "d20" if row.get("roll_required") else "", "bad_results": "",
        }
        record = Record(save_id=save.id, kind="event", label=str(row.get("event_name") or catalog_id), global_day=start, data=data)
        session.add(record); session.flush(); journal(session, record, "upsert", 0); created += 1
    settings_data = dict(save.settings or {}); settings_data["event_catalog_version"] = "recovered-655-v2-integrity"
    save.settings = settings_data; save.revision += created
    return created


def event_roll_spec(notes: str) -> dict:
    """Extract a lethal event roll from prose without treating enlistment as death."""
    text = str(notes or "").replace(";", ". ")
    rolls = list(re.finditer(r"(?:\broll\s+(?:an?\s+)?(?:the\s+)?)?\bd(\d+)\b", text, re.I))
    if not rolls:
        return {"die": "d20", "bad_results": ""}
    lethal = re.compile(r"\b(?:die|dies|died|death|dead|killed|fatal|executed|hanged|slain|perish(?:es|ed)?)\b", re.I)
    for index, match in enumerate(rolls):
        tail = text[match.end():rolls[index + 1].start() if index + 1 < len(rolls) else len(text)]
        numbers = []
        for clause in re.split(r"[.;\n]+", tail):
            if lethal.search(clause):
                leading = re.match(r"\s*([\d\s,orand\-–—]+)\s*(?:means|:|=)", clause, re.I)
                if leading:
                    ranges = re.findall(r"\d+\s*[-–—]\s*\d+|\d+", leading.group(1))
                    numbers.extend(ranges)
        if numbers:
            return {"die": f"d{match.group(1)}", "bad_results": " ".join(dict.fromkeys(numbers))}
    return {"die": f"d{rolls[0].group(1)}", "bad_results": ""}


def event_key(event: Record) -> str:
    """Return the stable catalog identity used by both 3.x imports and 4.x seeds."""
    data = event.data or {}
    return str(data.get("catalog_id") or data.get("event_id") or data.get("legacy_id") or event.id)


def _event_rule_map(session: Session, save: ChronicleSave) -> dict[str, dict]:
    """Index imported editable event rules without copying or discarding them."""
    result: dict[str, dict] = {}
    rules = session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "event_rule", Record.deleted.is_(False),
    ).order_by(Record.updated_at))
    for record in rules:
        data = dict(record.data or {})
        key = str(data.get("event_id") or data.get("catalog_id") or data.get("legacy_id") or "").strip()
        if key:
            data["record_id"] = record.id
            result[key] = data
    return result


def _result_numbers(text: str) -> str:
    """Extract the die values on the left side of an editable result table."""
    found: list[str] = []
    normalized = str(text or "").replace("–", "-").replace("—", "-")
    for clause in re.split(r"[;\n]+", normalized):
        left = clause.split(":", 1)[0] if ":" in clause else clause
        found.extend(re.findall(r"\d+\s*-\s*\d+|(?<!\d)\d+(?!\d)", left))
    return " ".join(dict.fromkeys(value.replace(" ", "") for value in found))


def _mapped_roll_outcome(actual: int, result_rules: str) -> str:
    """Return the prose attached to a matching numeric result or range."""
    normalized = str(result_rules or "").replace("–", "-").replace("—", "-")
    for clause in (part.strip() for part in re.split(r"[;\n]+", normalized) if part.strip()):
        if ":" not in clause:
            continue
        left, outcome = clause.split(":", 1)
        if failed(actual, left):
            return outcome.strip()
    return ""


def _lethal_outcome(text: str) -> bool:
    return bool(re.search(
        r"\b(?:die|dies|died|death|dead|killed|fatal|executed|hanged|slain|perish(?:es|ed)?|"
        r"drown(?:s|ed)?|starv(?:e|es|ed|ation)|murder(?:s|ed)?|succumb(?:s|ed)?)\b",
        str(text or ""), re.I,
    ))


def event_roll_configuration(event: Record, rule_data: dict | None = None) -> dict:
    """Merge native event fields, recovered prose and the imported rule table."""
    data = event.data or {}
    rule = rule_data or {}
    prose = event_roll_spec(data.get("notes") or "")
    result_rules = str(
        data.get("configured_bad_results") or rule.get("bad_results") or data.get("result_rules")
        or data.get("bad_results") or ""
    ).strip()
    bad_results = _result_numbers(result_rules) if ":" in result_rules else result_rules
    if not bad_results:
        bad_results = str(prose.get("bad_results") or "")
    outcome_text = " ".join(part.split(":", 1)[-1] for part in re.split(r"[;\n]+", result_rules))
    lethal = _lethal_outcome(outcome_text) or (not result_rules and bool(prose.get("bad_results")))
    return {
        "die": str(data.get("configured_die") or rule.get("die") or prose.get("die") or data.get("die") or "d20"),
        "bad_results": bad_results,
        "result_rules": result_rules,
        "failure_outcome": _mapped_roll_outcome(int(re.findall(r"\d+", bad_results)[0]), result_rules) if bad_results and result_rules else "",
        "failure_is_lethal": lethal,
        "event_rule_id": rule.get("record_id"),
    }


def _event_is_global(event: Record) -> bool:
    data = event.data or {}
    scope = str(data.get("scope") or "").strip().casefold()
    location = str(data.get("location") or "").strip().casefold()
    return scope.startswith("global") or location.startswith("global") or scope in {"world", "worldwide", "all", "everyone", "all sims"}


_EVENT_LOCATION_GROUPS = {
    "britain": {
        "britain", "great britain", "united kingdom", "uk", "british isles",
        "england", "scotland", "wales",
    },
    "low countries": {
        "low countries", "netherlands", "belgium", "luxembourg", "holland",
    },
    "europe": {
        "europe", "britain", "great britain", "united kingdom", "uk", "british isles",
        "england", "scotland", "wales", "ireland", "france", "germany", "italy",
        "spain", "portugal", "netherlands", "belgium", "luxembourg", "holland",
        "austria", "switzerland", "poland", "denmark", "norway", "sweden", "finland",
        "iceland", "greece", "hungary", "bohemia", "czechia", "slovakia", "romania",
        "bulgaria", "serbia", "croatia", "slovenia", "bosnia", "albania", "ukraine",
        "belarus", "lithuania", "latvia", "estonia", "moldova", "russia", "persia",
        "holy roman empire", "ottoman empire",
    },
}

_EVENT_LOCATION_ALIASES = {
    "pan european": "europe",
    "pan-european": "europe",
    "great britain": "britain",
    "united kingdom": "britain",
    "uk": "britain",
    "british isles": "britain",
    "holland": "netherlands",
}


def _normalized_location(value: object) -> str:
    text = re.sub(r"[^a-z0-9 -]+", " ", str(value or "").casefold())
    return re.sub(r"\s+", " ", text).strip()


def _location_parts(value: object) -> list[str]:
    parts = []
    for raw in re.split(r"[,/]", str(value or "")):
        normalized = _normalized_location(raw)
        if normalized and normalized not in {"see notes", "affected areas"}:
            parts.append(_EVENT_LOCATION_ALIASES.get(normalized, normalized))
    return parts


def _event_location_matches(target: object, places: object) -> bool:
    """Match historical regions without broadening country-specific events.

    England is part of both Britain and Europe, but an event explicitly limited
    to France must not become applicable merely because both are European.
    Therefore only a *target* region expands to its member countries.
    """
    targets, recorded_places = _location_parts(target), _location_parts(places)
    for target_part in targets:
        members = _EVENT_LOCATION_GROUPS.get(target_part, {target_part})
        for place in recorded_places:
            place_alias = _EVENT_LOCATION_ALIASES.get(place, place)
            if any(
                member == place_alias or member in place_alias or place_alias in member
                for member in members
            ):
                return True
    return False


def _event_occurrence_key(event: Record) -> tuple[str, int, int, str]:
    data = event.data or {}
    start = int(data.get("start_global_day", event.global_day) or 0)
    end = int(data.get("end_global_day", start) or start)
    return (
        re.sub(r"\s+", " ", event.label.casefold()).strip(),
        start,
        end,
        _normalized_location(data.get("location")),
    )


def _event_external_ids(event: Record) -> set[str]:
    data = event.data or {}
    values = {
        str(data.get(key) or "").strip()
        for key in ("catalog_id", "event_id", "legacy_id")
    }
    values.update(str(value).strip() for value in (data.get("duplicate_event_aliases") or []))
    return {value for value in values if value}


def duplicate_event_groups(records: list[Record]) -> list[dict]:
    """Find exact duplicate event occurrences and select the safest keeper."""
    active = [item for item in records if not item.deleted]
    references: dict[str, int] = defaultdict(int)
    event_ids = {item.id for item in active if item.kind == "event"}
    for item in active:
        data = item.data or {}
        for key in ("event_id", "source_id", "source_event_id"):
            value = str(data.get(key) or "")
            if value in event_ids:
                references[value] += 1
        source = str(data.get("source") or "")
        if source.startswith("event:"):
            source_id = source.split(":", 2)[1]
            if source_id in event_ids:
                references[source_id] += 1

    occurrences: dict[tuple[str, int, int, str], list[Record]] = defaultdict(list)
    for event in (item for item in active if item.kind == "event"):
        occurrences[_event_occurrence_key(event)].append(event)

    def keeper_score(event: Record) -> tuple[int, int, int, int, int, float, str]:
        data = event.data or {}
        protected = sum(bool(data.get(key)) for key in (
            "ignored", "completed", "configured_die", "configured_bad_results", "result_rules",
        ))
        meaningful = sum(value not in (None, "", [], {}) for value in data.values())
        legacy = int(bool(data.get("legacy_table") or data.get("legacy_id")))
        created = event.created_at.timestamp() if event.created_at else 0.0
        return references[event.id], protected, meaningful, legacy, int(event.version or 0), -created, event.id

    groups = []
    for identity, matches in occurrences.items():
        if len(matches) < 2:
            continue
        keeper = max(matches, key=keeper_score)
        groups.append({
            "identity": identity,
            "label": keeper.label,
            "keeper": keeper,
            "redundant": [item for item in matches if item is not keeper],
            "matches": matches,
            "references": sum(references[item.id] for item in matches),
        })
    return sorted(groups, key=lambda group: (group["identity"][1], group["label"].casefold()))


def duplicate_event_summary(records: list[Record]) -> dict:
    groups = duplicate_event_groups(records)
    return {
        "groups": len(groups),
        "repairable": sum(len(group["redundant"]) for group in groups),
        "preview": [{
            "label": group["label"],
            "global_day": group["identity"][1],
            "copies": len(group["matches"]),
            "references": group["references"],
        } for group in groups[:12]],
    }


def repair_duplicate_events(session: Session, save: ChronicleSave) -> dict:
    """Merge exact duplicate events, repoint dependents, and archive extra copies."""
    records = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.deleted.is_(False),
    )))
    groups = duplicate_event_groups(records)
    if not groups:
        return {"groups": 0, "archived": 0, "repointed": 0, "rolls_archived": 0}

    record_id_map: dict[str, str] = {}
    external_id_map: dict[str, str] = {}
    keeper_aliases: dict[str, set[str]] = defaultdict(set)
    for group in groups:
        keeper = group["keeper"]
        canonical_external = event_key(keeper)
        for redundant in group["redundant"]:
            record_id_map[redundant.id] = keeper.id
            for alias in _event_external_ids(redundant):
                if alias != canonical_external:
                    external_id_map[alias] = canonical_external
                    keeper_aliases[keeper.id].add(alias)

    repointed = 0
    grouped_event_ids = {item.id for group in groups for item in group["matches"]}
    for item in records:
        if item.id in grouped_event_ids:
            continue
        data = dict(item.data or {})
        changed = False
        for key in ("event_id", "source_id", "source_event_id"):
            value = str(data.get(key) or "")
            if value in record_id_map:
                data[key] = record_id_map[value]
                changed = True
        if item.kind == "event_rule":
            for key in ("event_id", "catalog_id", "legacy_id"):
                value = str(data.get(key) or "")
                if value in external_id_map:
                    data[key] = external_id_map[value]
                    changed = True
        if isinstance(data.get("event_ids"), list):
            mapped = [record_id_map.get(str(value), str(value)) for value in data["event_ids"]]
            if mapped != data["event_ids"]:
                data["event_ids"] = list(dict.fromkeys(mapped))
                changed = True
        source = str(data.get("source") or "")
        if source.startswith("event:"):
            parts = source.split(":", 2)
            if len(parts) == 3 and parts[1] in record_id_map:
                data["source"] = f"event:{record_id_map[parts[1]]}:{parts[2]}"
                changed = True
        if not changed:
            continue
        base = item.version
        item.data = data
        item.version += 1
        journal(session, item, "upsert", base)
        repointed += 1

    archived = 0
    keepers_updated = 0
    for group in groups:
        keeper = group["keeper"]
        merged = dict(keeper.data or {})
        aliases = set(merged.get("duplicate_event_aliases") or []) | keeper_aliases.get(keeper.id, set())
        for redundant in group["redundant"]:
            other = redundant.data or {}
            for key, value in other.items():
                if key in {"catalog_id", "event_id", "legacy_id", "duplicate_event_aliases"}:
                    continue
                if key in {"roll_required", "completed", "ignored"}:
                    merged[key] = bool(merged.get(key)) or bool(value)
                elif key == "notes" and len(str(value or "")) > len(str(merged.get(key) or "")):
                    merged[key] = value
                elif merged.get(key) in (None, "", [], {}) and value not in (None, "", [], {}):
                    merged[key] = value
            base = redundant.version
            redundant.deleted = True
            redundant.data = {
                **other,
                "duplicate_event_repair": True,
                "duplicate_of": keeper.id,
                "retired_reason": "Duplicate event",
                "retired_global_day": save.global_day,
            }
            redundant.version += 1
            journal(session, redundant, "delete", base)
            archived += 1
        if aliases:
            merged["duplicate_event_aliases"] = sorted(aliases)
        if merged != (keeper.data or {}):
            base = keeper.version
            keeper.data = merged
            keeper.version += 1
            journal(session, keeper, "upsert", base)
            keepers_updated += 1

    save.revision += archived + repointed + keepers_updated
    session.flush()
    roll_result = repair_duplicate_obligations(session, save)
    return {
        "groups": len(groups),
        "archived": archived,
        "repointed": repointed,
        "rolls_archived": roll_result["archived"],
    }


def _event_applies(event: Record, sim: Record, due: int, rule_data: dict | None = None,
                   household: Record | None = None, save: ChronicleSave | None = None,
                   fallback_location: str = "") -> bool:
    data, original = event.data or {}, sim.data or {}
    household_data = household.data if household else {}
    # Household and challenge defaults fill gaps but never replace explicit Sim data.
    sim_data = {**(household_data or {}), **original}
    rule = rule_data or {}
    birth = sim_data.get("birth_global_day", sim.global_day)
    death = sim_data.get("death_global_day")
    if birth is not None and int(birth) > due:
        return False
    if death is not None and int(death) <= due:
        return False
    text = f"{rule.get('eligibility','')} {data.get('affected_class','')} {data.get('notes','')}".casefold()
    sex = str(sim_data.get("sex") or "").casefold()
    explicit_sexes = rule.get("eligible_sexes")
    sex_rule = " ".join(map(str, explicit_sexes)) if isinstance(explicit_sexes, (list, tuple, set)) else str(explicit_sexes or "")
    sex_rule = sex_rule.casefold()
    male_only = bool(re.search(r"\b(?:male|males|men|man|boys?)\b", f"{sex_rule} {text}")) and not bool(re.search(r"\b(?:female|females|women|woman|girls?)\b", f"{sex_rule} {text}"))
    female_only = bool(re.search(r"\b(?:female|females|women|woman|girls?)\b", f"{sex_rule} {text}")) and not male_only
    sim_is_male = bool(re.search(r"\b(?:male|man|boy)\b", sex)) and not bool(re.search(r"\bfemale\b", sex))
    sim_is_female = bool(re.search(r"\b(?:female|woman|girl)\b", sex))
    if male_only and not sim_is_male:
        return False
    if female_only and not sim_is_female:
        return False
    if birth is not None:
        age = due - int(birth)
        try:
            if rule.get("min_age_days") not in (None, "") and age < int(rule["min_age_days"]):
                return False
            if rule.get("max_age_days") not in (None, "") and age > int(rule["max_age_days"]):
                return False
        except (TypeError, ValueError):
            pass
        age_match = re.search(r"\b(\d+)\s*\+", text)
        if age_match and age < int(age_match.group(1)):
            return False
    if _event_is_global(event):
        return True
    target = str(data.get("location") or "").casefold()
    challenge = save.settings if save else {}
    places = " ".join(str(value or "") for value in (
        sim_data.get("country"), sim_data.get("last_game_world"), sim_data.get("birthplace"),
        sim_data.get("location"), sim_data.get("world"), household_data.get("country"),
        household_data.get("location"), household_data.get("world"),
        (challenge or {}).get("challenge_location"), (challenge or {}).get("location"),
        (challenge or {}).get("country"),
    )).casefold()
    if not places.strip():
        places = fallback_location.casefold()
    target_parts = _location_parts(target)
    if target_parts and not _event_location_matches(target, places):
        return False
    social = str(sim_data.get("social_class") or household_data.get("social_class") or "").casefold()
    affected = str(data.get("affected_class") or "").casefold()
    class_words = ("nobility", "noble", "royal", "peasant", "working", "middle", "upper", "lower")
    requested = [word for word in class_words if word in affected]
    return not requested or any(word in social for word in requested)


def _marriage_rule(record: Record) -> bool:
    data = record.data or {}
    text = " ".join(str(value or "") for value in (data.get("rule_key"), record.label, data.get("notes"))).casefold()
    return "non_heir_marriage" in text or "non-heir marriage" in text or "marriage eligibility" in text


def _marriage_roll(record: Record) -> bool:
    data = record.data or {}
    text = " ".join(str(value or "") for value in (data.get("source"), data.get("source_id"), data.get("roll_type"))).casefold()
    return "planner:marriage:" in text or "planner-marriage-" in text or "marriage eligibility" in text or "marriage roll" in text


def marriage_roll_result(actual: int, result_rules: str, bad_results: str = "") -> str:
    """Interpret full marriage tables while keeping every marriage outcome nonfatal."""
    text = str(result_rules or bad_results or "").replace("–", "-").replace("—", "-")
    for clause in (part.strip() for part in re.split(r"[;\n]+", text) if part.strip()):
        match = re.search(r"(?<!\d)(\d+)(?:\s*-\s*(\d+))?(?!\d)\s*:", clause)
        if not match:
            continue
        low = int(match.group(1)); high = int(match.group(2) or low)
        if min(low, high) <= actual <= max(low, high):
            action = clause.split(":", 1)[1].strip()
            if "does not marry" in action.casefold():
                return "Does not marry"
            if "may marry" in action.casefold() or "marries" in action.casefold():
                return "May marry"
            return action or "May marry"
    failure_values = str(bad_results or "")
    if "does not marry" in text.casefold():
        failure_clause = next((part for part in re.split(r"[;\n]+", text) if "does not marry" in part.casefold()), "")
        failure_values = failure_clause.split(":", 1)[0]
    return "Does not marry" if failed(actual, failure_values) else "May marry"


def _pregnancy_count_rule(record: Record) -> bool:
    data = record.data or {}
    text = " ".join(str(value or "") for value in (data.get("rule_key"), record.label, data.get("notes"))).casefold()
    return "side_pregnancy" in text or "side household pregnancy" in text or "pregnancy-count" in text or "pregnancy count" in text


def pregnancy_count_result(actual: int, result_rules: str, zero_results: str = "") -> tuple[int, str]:
    """Translate a pregnancy-count die result without treating it as a fatal failure."""
    text = str(result_rules or "").replace("–", "-").replace("—", "-")
    default_action = ""
    for clause in (part.strip() for part in re.split(r"[;\n]+", text) if part.strip()):
        action = clause.split(":", 1)[1].strip() if ":" in clause else ""
        if "all other" in clause.casefold():
            default_action = action
            continue
        match = re.search(r"(?<!\d)(\d+)(?:\s*-\s*(\d+))?(?!\d)\s*:", clause)
        if not match:
            continue
        low = int(match.group(1)); high = int(match.group(2) or low)
        if min(low, high) <= actual <= max(low, high):
            break
    else:
        action = default_action
    lowered = action.casefold()
    if "no pregn" in lowered or (not action and zero_results and failed(actual, zero_results)):
        count = 0
    elif "one pregn" in lowered:
        count = 1
    else:
        explicit = re.search(r"\b(\d+)\s+pregnan", lowered)
        count = int(explicit.group(1)) if explicit else max(0, int(actual))
    outcome = "No pregnancies" if count == 0 else "1 pregnancy" if count == 1 else f"{count} pregnancies"
    return count, outcome


def create_pregnancy_count_roll(session: Session, save: ChronicleSave, sim: Record) -> tuple[Record, bool]:
    """Create one editable-rule pregnancy allowance for a Sim in the current historical year."""
    if sim.kind != "sim" or sim.deleted or sim.save_id != save.id:
        raise ValueError("Choose a Sim from the active save.")
    death_day = (sim.data or {}).get("death_global_day")
    if bool((sim.data or {}).get("game_was_dead")) or (death_day not in (None, "") and int(death_day) <= save.global_day):
        raise ValueError("Pregnancy-count rolls are only available for living Sims.")
    year = save.start_year + (save.global_day - 1) // max(1, save.days_per_year)
    rules = [record for record in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "planner_rule", Record.deleted.is_(False)
    )) if _pregnancy_count_rule(record) and bool((record.data or {}).get("active", True))]
    rule = next((record for record in sorted(rules, key=lambda item:int((item.data or {}).get("start_year", -9999)), reverse=True)
                 if int((record.data or {}).get("start_year", -9999)) <= year <= int((record.data or {}).get("end_year", 9999))), None)
    if not rule:
        raise ValueError(f"No active pregnancy-count rule covers {year}. Add or enable one under Rules & Data.")
    source = f"planner:pregnancy-count:{sim.id}:{year}"
    existing = session.scalar(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["source"].as_string() == source,
    ).order_by(Record.created_at.desc()).limit(1))
    if existing:
        return existing, False
    rule_data = rule.data or {}
    stored_rules = str(rule_data.get("bad_results") or "")
    if ":" not in stored_rules:
        stored_rules = f"{stored_rules}: No pregnancy; all other results: Schedule that many pregnancies"
    roll = Record(save_id=save.id, kind="roll", label=f"{sim.label} — Pregnancy Count", global_day=save.global_day, data={
        "sim_id":sim.id, "sim_name":sim.label, "source_id":source, "source":source,
        "roll_type":"Pregnancy Count", "die":rule_data.get("die") or "d20", "bad_results":"",
        "result_rules":stored_rules, "zero_results":str(rule_data.get("bad_results") or ""),
        "planner_rule_id":rule.id, "planner_year":year, "due_global_day":save.global_day,
        "completed":False, "nonlethal":True, "pregnancy_count_roll":True,
        "notes":f"Pregnancy allowance for {year}; uses the editable era planner rule",
    })
    session.add(roll); session.flush(); journal(session, roll, "upsert", 0); save.revision += 1
    return roll, True


def pregnancy_allowance_status(session: Session, save: ChronicleSave, sim: Record) -> dict:
    """Return each recorded annual allowance with live used and remaining counts."""
    stored = (sim.data or {}).get("pregnancy_allowances") or {}
    allowances = {str(key):dict(value) for key,value in stored.items() if isinstance(value, dict)} if isinstance(stored, dict) else {}
    if (sim.data or {}).get("pregnancy_allowance_count") is not None:
        year = str((sim.data or {}).get("pregnancy_allowance_year") or save.start_year)
        allowances.setdefault(year, {
            "allowed":int((sim.data or {}).get("pregnancy_allowance_count") or 0),
            "roll_id":(sim.data or {}).get("pregnancy_allowance_roll_id"),
            "recorded_global_day":(sim.data or {}).get("pregnancy_allowance_recorded_global_day"),
        })
    completed_rolls = session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["sim_id"].as_string() == sim.id,
    ).order_by(Record.updated_at.desc()))
    for roll in completed_rolls:
        data = roll.data or {}
        if not data.get("pregnancy_count_roll") or not data.get("completed") or data.get("pregnancy_count") is None:
            continue
        year = str(data.get("planner_year") or (save.start_year + (int(roll.global_day or save.global_day) - 1) // max(1, save.days_per_year)))
        allowances.setdefault(year, {"allowed":int(data.get("pregnancy_count") or 0), "roll_id":roll.id, "recorded_global_day":data.get("completed_global_day")})
    used_by_year: dict[str, int] = {}
    pregnancies = session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "pregnancy", Record.deleted.is_(False),
        Record.data["mother_id"].as_string() == sim.id,
    ))
    for pregnancy in pregnancies:
        data = pregnancy.data or {}
        if str(data.get("status") or "").strip().casefold() in {"cancelled", "canceled"}:
            continue
        day = data.get("conception_global_day")
        if day in (None, ""):
            due = data.get("due_global_day", pregnancy.global_day)
            day = int(due) - save.pregnancy_days if due not in (None, "") else pregnancy.global_day
        if day is None:
            continue
        year = str(save.start_year + (int(day) - 1) // max(1, save.days_per_year))
        used_by_year[year] = used_by_year.get(year, 0) + 1
    rows = []
    for year,value in allowances.items():
        allowed = max(0, int(value.get("allowed") or 0)); used = used_by_year.get(str(year), 0)
        rows.append({"year":int(year), "allowed":allowed, "used":used, "remaining":max(0, allowed - used), **value})
    rows.sort(key=lambda row:row["year"], reverse=True)
    current_year = save.start_year + (save.global_day - 1) // max(1, save.days_per_year)
    return {"rows":rows, "current":next((row for row in rows if row["year"] == current_year), rows[0] if rows else None)}


def backfill_pregnancy_allowances(session: Session, save: ChronicleSave) -> int:
    """Copy completed pre-feature pregnancy-count rolls onto their Sim profiles once."""
    rolls = session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["pregnancy_count_roll"].as_boolean().is_(True),
        Record.data["completed"].as_boolean().is_(True),
    ).order_by(Record.updated_at))
    changed = 0
    for roll in rolls:
        data = roll.data or {}
        if not data.get("pregnancy_count_roll") or not data.get("completed") or data.get("pregnancy_count") is None:
            continue
        sim = session.get(Record, data.get("sim_id")) if data.get("sim_id") else None
        if not sim or sim.kind != "sim" or sim.deleted:
            continue
        year = int(data.get("planner_year") or (save.start_year + (int(roll.global_day or save.global_day) - 1) // max(1, save.days_per_year)))
        sim_data = dict(sim.data or {}); allowances = dict(sim_data.get("pregnancy_allowances") or {})
        entry = {"allowed":int(data.get("pregnancy_count") or 0), "roll_id":roll.id, "recorded_global_day":data.get("completed_global_day"), "actual":data.get("actual")}
        if allowances.get(str(year)) == entry:
            continue
        allowances[str(year)] = entry
        sim_data["pregnancy_allowances"] = allowances
        if int(sim_data.get("pregnancy_allowance_year") or -9999) <= year:
            sim_data.update({
                "pregnancy_allowance_count":entry["allowed"], "pregnancy_allowance_year":year,
                "pregnancy_allowance_roll_id":roll.id, "pregnancy_allowance_recorded_global_day":entry["recorded_global_day"],
            })
        base = sim.version; sim.data = sim_data; sim.version += 1; journal(session, sim, "upsert", base); changed += 1
    save.revision += changed
    return changed


def _setting_int(save: ChronicleSave, key: str, default: int) -> int:
    settings = save.settings or {}
    value = settings.get(key)
    if value in (None, ""):
        value = (settings.get("legacy_settings") or {}).get(key, default)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _schedule_marriage_rolls(session: Session, save: ChronicleSave, sims: list[Record] | None = None) -> tuple[int, int]:
    """Restore the one-time non-heir marriage obligation used by the 3.x planner."""
    rules = [record for record in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "planner_rule", Record.deleted.is_(False)
    )) if _marriage_rule(record) and bool((record.data or {}).get("active", True))]
    if not rules:
        return 0, 0
    sims = sims if sims is not None else list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False)
    )))
    relationships = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "relationship", Record.deleted.is_(False)
    )))
    married_ids = set()
    for relationship in relationships:
        data = relationship.data or {}
        if bool(data.get("legally_married")) or "marriage" in str(data.get("type") or "").casefold():
            married_ids.update(str(data.get(key) or "") for key in ("partner1_id", "partner2_id"))
    existing_rolls = [record for record in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False)
    )) if _marriage_roll(record)]
    existing_sim_ids = {str((record.data or {}).get("sim_id") or "") for record in existing_rolls}
    tracking_start = max(1, _setting_int(save, "roll_tracking_start_day", _setting_int(save, "roll_tracking_start", 1)))
    retired = 0
    for roll in existing_rolls:
        if bool((roll.data or {}).get("completed")):
            continue
        sim_is_married = str((roll.data or {}).get("sim_id") or "") in married_ids
        before_tracking = roll.global_day is not None and int(roll.global_day) < tracking_start
        if not sim_is_married and not before_tracking:
            continue
        base = roll.version
        roll.deleted = True
        reason = "Sim is already married" if sim_is_married else "Eligibility predates roll tracking"
        roll.data = {**(roll.data or {}), "retired_reason":reason, "retired_global_day":save.global_day}
        roll.version += 1; journal(session, roll, "delete", base); retired += 1
    settings = save.settings or {}
    legacy = settings.get("legacy_settings") or {}
    legacy_map = settings.get("legacy_id_map") or {}
    heir_id = str(settings.get("current_heir_id") or legacy_map.get(legacy.get("current_heir_id"), legacy.get("current_heir_id")) or "")
    marriage_age = max(0, _setting_int(save, "marriage_min_age_days", 72))
    created = 0
    for sim in sims:
        data = sim.data or {}
        birth = data.get("birth_global_day", sim.global_day)
        if bool(data.get("game_was_dead")) or birth is None or sim.id == heir_id or sim.id in married_ids or sim.id in existing_sim_ids:
            continue
        due = int(birth) + marriage_age
        death = data.get("death_global_day")
        if due < tracking_start or due > save.global_day or (death is not None and (int(death) <= save.global_day or int(death) <= due)):
            continue
        due_year = save.start_year + (due - 1) // max(1, save.days_per_year)
        rule = next((record for record in sorted(rules, key=lambda item:int((item.data or {}).get("start_year", -9999)), reverse=True)
                     if int((record.data or {}).get("start_year", -9999)) <= due_year <= int((record.data or {}).get("end_year", 9999))), None)
        if not rule:
            continue
        rule_results = str((rule.data or {}).get("bad_results") or "")
        failure_results = "1" if "does not marry" in rule_results.casefold() else rule_results
        source = f"planner:marriage:{sim.id}"
        roll = Record(save_id=save.id, kind="roll", label=f"{sim.label} — Non-Heir Marriage Eligibility", global_day=due, data={
            "sim_id":sim.id, "sim_name":sim.label, "source_id":source, "roll_type":"Non-Heir Marriage Eligibility",
            "die":(rule.data or {}).get("die") or "d20", "bad_results":failure_results, "result_rules":rule_results,
            "source":source, "planner_rule_id":rule.id, "due_global_day":due, "completed":False,
            "nonlethal":True, "failure_outcome":"Does not marry", "success_outcome":"May marry",
            "notes":"Auto-generated when this non-heir reached marriage eligibility",
        })
        session.add(roll);session.flush();journal(session,roll,"upsert",0);existing_sim_ids.add(sim.id);created += 1
    return created, retired


def schedule_marriage_rolls(session: Session, save: ChronicleSave) -> int:
    created, retired = _schedule_marriage_rolls(session, save)
    save.revision += created + retired
    return created


def _occult_year(save: ChronicleSave, day: int) -> int:
    return save.start_year + (max(1, int(day)) - 1) // max(1, save.days_per_year)


def _occult_rule_matches(rule: Record, year: int) -> bool:
    data = rule.data or {}
    return (
        bool(data.get("active", True)) and bool(data.get("auto_schedule", False))
        and int(data.get("start_year", -9999)) <= year <= int(data.get("end_year", 9999))
    )


def _add_occult_roll(session: Session, save: ChronicleSave, rule: Record, sim: Record,
                     due: int, source: str, *, label: str | None = None,
                     overrides: dict | None = None) -> bool:
    if int(due) < 1:
        return False
    exists = session.scalar(select(Record.id).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["source"].as_string() == source,
    ).limit(1))
    if exists:
        return False
    data = rule.data or {}
    payload = {
        "sim_id":sim.id, "sim_name":sim.label, "source_id":rule.id,
        "roll_type":rule.label, "die":data.get("die") or "d20", "bad_results":"",
        "trigger_results":data.get("trigger_results") or "",
        "result_rules":data.get("result_rules") or "",
        "failure_outcome":"", "success_outcome":"", "nonlethal":True,
        "occult_roll":True, "occult_rule_id":rule.id,
        "occult_rule_key":data.get("rule_key"), "occult_type":data.get("occult"),
        "source":source, "due_global_day":int(due), "completed":False,
        "notes":data.get("notes") or "",
    }
    payload.update(overrides or {})
    roll = Record(save_id=save.id, kind="roll", label=label or f"{sim.label} — {rule.label}",
                  global_day=int(due), data=payload)
    session.add(roll); session.flush(); journal(session, roll, "upsert", 0)
    return True


def _occult_inheritance_rolls(session: Session, save: ChronicleSave, sims: list[Record],
                              rules: list[Record], enabled_from: int) -> int:
    rule = next((item for item in rules if (item.data or {}).get("rule_key") == "general_inheritance"
                 and bool((item.data or {}).get("active", True)) and bool((item.data or {}).get("auto_schedule", False))), None)
    if not rule:
        return 0
    by_id = {sim.id:sim for sim in sims}
    created = 0
    for child in sims:
        data = child.data or {}
        birth = data.get("birth_global_day", child.global_day)
        if birth is None or int(birth) < enabled_from or int(birth) > save.global_day:
            continue
        if occult_rules.sim_occult_types(data):
            continue
        mother = by_id.get(str(data.get("mother_id") or ""))
        father = by_id.get(str(data.get("father_id") or ""))
        if not mother or not father:
            continue
        mother_types = occult_rules.sim_occult_types(mother.data)
        father_types = occult_rules.sim_occult_types(father.data)
        mother_dormant = occult_rules.dormant_occult_types(mother.data)
        father_dormant = occult_rules.dormant_occult_types(father.data)
        die = trigger = results = effect = ""
        candidates: list[str] = []
        if mother_types and father_types and mother_types[0] != father_types[0]:
            candidates = [mother_types[0], father_types[0]]
            die, trigger, effect = "d2", "1-2", "inherit_occult_choice"
            results = f"1: Inherits {candidates[0]}; 2: Inherits {candidates[1]}"
        elif bool(mother_types) != bool(father_types):
            candidates = list(mother_types or father_types)
            die, trigger, effect = "d4", "1", "add_dormant_occult"
            results = f"1: Carries dormant {candidates[0]} blood; 2-4: Human without dormant blood"
        elif mother_dormant and father_dormant:
            candidates = list(dict.fromkeys(mother_dormant + father_dormant))
            die, trigger, effect = "d4", "1", "manifest_dormant_occult"
            results = f"1: Manifests {candidates[0]}; 2-4: Remains human"
        elif bool(mother_dormant) != bool(father_dormant):
            candidates = list(mother_dormant or father_dormant)
            die, trigger, effect = "d10", "1", "manifest_dormant_occult"
            results = f"1: Manifests {candidates[0]}; 2-10: Remains human"
        if not candidates:
            continue
        source = f"occult:inheritance:{child.id}"
        created += int(_add_occult_roll(
            session, save, rule, child, max(enabled_from, int(birth)), source,
            label=f"{child.label} — Occult inheritance",
            overrides={"die":die, "trigger_results":trigger, "result_rules":results,
                       "occult_effect":effect, "occult_candidates":candidates},
        ))
    return created


def _ghost_persistence_rolls(session: Session, save: ChronicleSave, sims: list[Record],
                             rules: list[Record], enabled_from: int) -> int:
    rule = next((item for item in rules if (item.data or {}).get("rule_key") == "ghost_persistence"
                 and bool((item.data or {}).get("active", True)) and bool((item.data or {}).get("auto_schedule", False))), None)
    if not rule:
        return 0
    created = 0
    for sim in sims:
        data = sim.data or {}
        death = data.get("death_global_day")
        birth = data.get("birth_global_day", sim.global_day)
        if death in (None, "") or birth in (None, "") or int(death) > save.global_day:
            continue
        age_days = int(death) - int(birth)
        minimum_age_days = 10 * max(1, int(save.days_per_year))
        if age_days < minimum_age_days:
            continue
        if "Ghost" in occult_rules.sim_occult_types(data):
            continue
        cause = str(data.get("cause_of_death") or "").casefold()
        if any(word in cause for word in ("murder", "execut", "assassin")):
            die, trigger, description = "d2", "1", "1: Heads — ghost remains; 2: Tails — spirit moves on"
        elif any(word in cause for word in ("betray", "revenge", "vengeance")):
            die, trigger, description = "d4", "1-2", "1-2: Ghost remains; 3-4: Spirit moves on"
        elif any(word in cause for word in ("accident", "fire", "drown", "fall", "lightning")):
            die, trigger, description = "d4", "1", "1: Ghost remains; 2-4: Spirit moves on"
        elif any(word in cause for word in ("old age", "peaceful")):
            die, trigger, description = "d8", "1", "1: Ghost remains; 2-8: Spirit moves on"
        else:
            die, trigger, description = "d6", "1", "1: Ghost remains; 2-6: Spirit moves on"
        created += int(_add_occult_roll(
            session, save, rule, sim, int(death), f"occult:ghost-persistence:{sim.id}:{death}",
            overrides={"die":die, "trigger_results":trigger, "result_rules":description,
                       "occult_effect":"persistent_ghost", "age_at_death_days":age_days,
                       "age_at_death_years":age_days // max(1, int(save.days_per_year)),
                       "minimum_ghost_age_years":10},
        ))
    return created


def schedule_occult_rolls(session: Session, save: ChronicleSave,
                          sims: list[Record] | None = None) -> int:
    """Schedule only occult obligations whose eligibility can be derived safely."""
    settings = save.settings or {}
    if not bool(settings.get("automatic_occult_rolls", False)):
        return 0
    enabled_from = max(1, int(settings.get("occult_rolls_enabled_from_global_day") or save.global_day))
    sims = sims if sims is not None else list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False)
    )))
    rules = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "occult_rule", Record.deleted.is_(False)
    )))
    created = _occult_inheritance_rolls(session, save, sims, rules, enabled_from)
    created += _ghost_persistence_rolls(session, save, sims, rules, enabled_from)
    living_sims = [sim for sim in sims if occult_rules.living(sim.data, save.global_day)]
    current_year = _occult_year(save, save.global_day)
    year_start = max(1, (current_year - save.start_year) * max(1, save.days_per_year) + 1)

    def condition_ok(rule: Record, sim: Record) -> bool:
        condition = str((rule.data or {}).get("condition") or "")
        if condition in {"coastal", "inland"}:
            return occult_rules.water_access(sim.data, settings) == condition
        if condition == "loose":
            return not bool((sim.data or {}).get("werewolf_confined", False))
        if condition in {"good", "bad"}:
            return occult_rules.aligned(sim.data, condition)
        return True

    def eligible(rule: Record) -> list[Record]:
        occult = str((rule.data or {}).get("occult") or "")
        return [sim for sim in living_sims if occult in occult_rules.sim_occult_types(sim.data) and condition_ok(rule, sim)]

    # One current-year obligation is created per eligible Sim or household. This
    # avoids flooding a newly enabled save with rolls from already-played years.
    annual_rules = [rule for rule in rules if (rule.data or {}).get("cadence") == "annual"
                    and _occult_rule_matches(rule, current_year)]
    for rule in annual_rules:
        due = max(year_start, enabled_from)
        targets = eligible(rule)
        if str((rule.data or {}).get("scope") or "sim") == "household":
            groups: dict[str, list[Record]] = {}
            for sim in targets:
                key = str((sim.data or {}).get("current_household_id") or f"unhoused-{sim.id}")
                groups.setdefault(key, []).append(sim)
            for group, members in groups.items():
                representative = sorted(members, key=lambda item:item.label.casefold())[0]
                source = f"occult:{rule.id}:household:{group}:{current_year}"
                created += int(_add_occult_roll(
                    session, save, rule, representative, due, source,
                    label=f"{rule.label} — {(representative.data or {}).get('game_household_name') or representative.label}",
                    overrides={"occult_household_id":group, "eligible_occult_sim_ids":[item.id for item in members]},
                ))
        else:
            for sim in targets:
                source = f"occult:{rule.id}:{sim.id}:{current_year}"
                created += int(_add_occult_roll(session, save, rule, sim, due, source))

    # Full-moon obligations use an editable anchor and interval. Every moon crossed
    # since enabling is retained, even when the player skips several days at once.
    interval = max(1, int(settings.get("full_moon_interval_days") or 8))
    anchor = max(1, int(settings.get("full_moon_anchor_global_day") or 1))
    first = anchor
    if first < enabled_from:
        first += ((enabled_from - first + interval - 1) // interval) * interval
    moon_day = first
    while moon_day <= save.global_day:
        moon_year = _occult_year(save, moon_day)
        for rule in rules:
            if (rule.data or {}).get("cadence") != "full_moon" or not _occult_rule_matches(rule, moon_year):
                continue
            for sim in eligible(rule):
                source = f"occult:{rule.id}:{sim.id}:moon:{moon_day}"
                created += int(_add_occult_roll(session, save, rule, sim, moon_day, source))
        moon_day += interval

    # Reconcile triggered rolls completed before automatic follow-ups existed.
    # The helper recognizes an already-created manual follow-up, so enabling the
    # automation on an established save cannot duplicate the player's work.
    completed_triggers = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "roll",
        Record.deleted.is_(False),
        Record.data["completed"].as_boolean().is_(True),
        Record.data["triggered"].as_boolean().is_(True),
    )))
    for origin in completed_triggers:
        origin_data = origin.data or {}
        parent_key = str(origin_data.get("source_rule_key") or origin_data.get("occult_rule_key") or "")
        if parent_key not in occult_rules.AUTOMATIC_OCCULT_FOLLOW_UPS:
            continue
        if origin_data.get("rule_followup_automatic") and origin_data.get("rule_followup_ids"):
            continue
        before = dict(origin_data)
        base = origin.version
        followups_created = _schedule_automatic_occult_followup(session, save, origin)
        created += followups_created
        if origin.data != before:
            origin.version += 1
            journal(session, origin, "upsert", base)
            if not followups_created:
                created += 1
    return created


def apply_occult_roll_result(session: Session, roll: Record, actual: int) -> int:
    """Persist safe tracker-side inheritance effects without changing the game."""
    data = roll.data or {}
    if not bool(data.get("occult_roll")):
        return 0
    triggered = failed(actual, str(data.get("trigger_results") or ""))
    effect = str(data.get("occult_effect") or "")
    sim = session.get(Record, data.get("sim_id")) if data.get("sim_id") else None
    if not sim or sim.deleted or not triggered:
        return 0
    sim_data = dict(sim.data or {})
    candidates = [str(value) for value in (data.get("occult_candidates") or []) if value]
    if effect == "add_dormant_occult" and candidates:
        existing = occult_rules.dormant_occult_types(sim_data)
        sim_data["dormant_occult_types"] = list(dict.fromkeys(existing + candidates))
    elif effect == "manifest_dormant_occult" and candidates:
        sim_data["challenge_manifested_occult"] = candidates[0]
        sim_data["dormant_occult_types"] = [value for value in occult_rules.dormant_occult_types(sim_data) if value != candidates[0]]
    elif effect == "inherit_occult_choice" and candidates:
        index = min(max(1, int(actual)), len(candidates)) - 1
        sim_data["challenge_inherited_occult"] = candidates[index]
    elif effect == "persistent_ghost":
        sim_data["persistent_ghost_roll"] = "Spirit remains"
    else:
        return 0
    base = sim.version; sim.data = sim_data; sim.version += 1; journal(session, sim, "upsert", base)
    return 1


def _schedule_automatic_occult_followup(session: Session, save: ChronicleSave, roll: Record) -> int:
    """Schedule deterministic werewolf follow-ups immediately after a trigger."""
    data = roll.data or {}
    if not bool(data.get("occult_roll")) or not bool(data.get("completed")) or not bool(data.get("triggered")):
        return 0
    parent_key = str(data.get("source_rule_key") or data.get("occult_rule_key") or "")
    child_key = occult_rules.AUTOMATIC_OCCULT_FOLLOW_UPS.get(parent_key)
    if not child_key:
        return 0
    sim = session.get(Record, data.get("sim_id")) if data.get("sim_id") else None
    if not sim or sim.kind != "sim" or sim.deleted or sim.save_id != save.id:
        return 0

    year = _occult_year(save, save.global_day)
    candidates = []
    for rule in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "occult_rule", Record.deleted.is_(False)
    )):
        rule_data = rule.data or {}
        if (
            str(rule_data.get("rule_key") or "") == child_key
            and bool(rule_data.get("active", True))
            and int(rule_data.get("start_year", -9999)) <= year <= int(rule_data.get("end_year", 9999))
        ):
            candidates.append(rule)
    if not candidates:
        return 0
    rule = min(candidates, key=lambda item: int((item.data or {}).get("end_year", 9999)) - int((item.data or {}).get("start_year", -9999)))
    lethal = occult_rules.lethal_results(child_key)
    source = f"occult:auto-followup:{roll.id}:{rule.id}:{sim.id}"
    # A player may already have added this follow-up from the rule workbench.
    # Match its stable origin/rule/Sim identity before considering the automatic
    # source token, otherwise the backfill would create a duplicate.
    origin_followups = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["origin_roll_id"].as_string() == roll.id,
        Record.data["sim_id"].as_string() == sim.id,
    )))
    followup = next((item for item in origin_followups if str(
        (item.data or {}).get("source_rule_key") or (item.data or {}).get("occult_rule_key") or ""
    ) == child_key), None)
    created = False
    if not followup:
        created = _add_occult_roll(
            session, save, rule, sim, save.global_day, source,
            overrides={
                "origin_roll_id": roll.id,
                "source_rule_id": rule.id,
                "source_rule_key": child_key,
                "rule_generated": True,
                "automatic_followup": True,
                "rule_context": f"Automatically scheduled after {roll.label}: {data.get('outcome') or 'triggered'}",
                "bad_results": lethal,
                "nonlethal": not bool(lethal),
                "failure_is_lethal": bool(lethal),
            },
        )
        followup = session.scalar(select(Record).where(
            Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
            Record.data["source"].as_string() == source,
        ).limit(1))
    if followup:
        followup_ids = list(data.get("rule_followup_ids") or [])
        if followup.id not in followup_ids:
            followup_ids.append(followup.id)
        roll.data = {
            **data,
            "rule_followup_ids": followup_ids,
            "rule_followup_last_created_global_day": save.global_day,
            "rule_followup_reviewed": True,
            "rule_followup_reviewed_global_day": save.global_day,
            "rule_followup_automatic": True,
        }
    return int(created)


def schedule_rolls(session: Session, save: ChronicleSave) -> int:
    rules = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "roll_rule", Record.deleted.is_(False))))
    sims = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False))))
    save.revision += retire_prechallenge_rolls(session, save)
    save.revision += retire_dead_sim_rolls(session, save, sims)
    created = 0
    for sim in sims:
        birth = sim.data.get("birth_global_day", sim.global_day)
        death = sim.data.get("death_global_day")
        if bool((sim.data or {}).get("game_was_dead")) or (death is not None and int(death) <= save.global_day): continue
        # Servos use the dedicated mechanical-failure table instead of ordinary
        # birth, aging, illness and pregnancy mortality.
        if "Servo" in occult_rules.sim_occult_types(sim.data): continue
        if birth is None: continue
        for rule in rules:
            if not rule.data.get("active", True): continue
            configured_age = rule.data.get("age_days")
            if configured_age in (None, ""):
                configured_age = AGING_STAGE_OFFSETS.get(rule.label.strip().casefold())
            # Maternal and other event-driven rules are not lifecycle milestones.
            if configured_age is None: continue
            if int(configured_age) == 0 and sim.data.get("newborn_rolls_required") is False:
                continue
            due = int(birth) + int(configured_age)
            if due < 1 or (death is not None and due >= int(death)): continue
            source = f"aging:{sim.id}:{rule.id}"
            # Imported 3.x rolls do not have the 4.0 scheduler source token. Match
            # their stable identity too, otherwise every app start can create a
            # second copy of a roll the player may already have completed.
            exists = session.scalar(select(Record.id).where(
                Record.save_id == save.id,
                Record.kind == "roll",
                Record.deleted.is_(False),
                (Record.data["source"].as_string() == source) |
                (
                    (Record.data["sim_id"].as_string() == sim.id) &
                    (Record.data["roll_type"].as_string() == rule.label) &
                    (Record.global_day == due)
                ),
            ).limit(1))
            if exists: continue
            roll = Record(save_id=save.id, kind="roll", label=f"{sim.label} — {rule.label}", global_day=due, data={"sim_id": sim.id, "roll_type": rule.label, "die": rule.data.get("die"), "bad_results": rule.data.get("bad_results"), "source": source, "due_global_day": due, "completed": False})
            session.add(roll); session.flush(); journal(session, roll, "upsert", 0); created += 1
    if (save.settings or {}).get("maternal_rolls_enabled", True):
        maternal_rules = [rule for rule in rules if "maternal" in rule.label.casefold() and rule.data.get("active", True)]
        pregnancies = list(session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "pregnancy", Record.deleted.is_(False),
        )))
        for pregnancy in pregnancies:
            status = str(pregnancy.data.get("status") or "active").casefold()
            if pregnancy.data.get("maternal_rolls_required") is False or status in {"miscarriage", "cancelled", "canceled"}:
                continue
            due = pregnancy.data.get("due_global_day", pregnancy.global_day)
            mother = session.get(Record, pregnancy.data.get("mother_id"))
            birth = mother.data.get("birth_global_day", mother.global_day) if mother else None
            mother_death = mother.data.get("death_global_day") if mother else None
            if due is None or int(due) < 1 or birth is None or (mother and (bool((mother.data or {}).get("game_was_dead")) or "Servo" in occult_rules.sim_occult_types(mother.data))) or (mother_death is not None and int(mother_death) <= save.global_day) or (status in CLOSED_PREGNANCIES and int(due) < save.global_day):
                continue
            age = int(due) - int(birth)
            stage = "preteen" if age < 52 else "teen" if age < 72 else "young adult" if age < 160 else "adult" if age < 240 else "elder"
            rule = next((item for item in maternal_rules if stage in item.label.casefold()), None)
            if not rule:
                continue
            source = f"maternal:{pregnancy.id}:{rule.id}"
            exists = session.scalar(select(Record.id).where(
                Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
                (Record.data["source"].as_string() == source) |
                (
                    (Record.data["source_id"].as_string() == pregnancy.id) &
                    (Record.data["roll_type"].as_string() == rule.label) &
                    (Record.global_day == int(due))
                ),
            ).limit(1))
            if exists:
                continue
            roll = Record(save_id=save.id,kind="roll",label=f"{mother.label} — {rule.label}",global_day=int(due),data={
                "sim_id":mother.id,"sim_name":mother.label,"source_id":pregnancy.id,"roll_type":rule.label,
                "die":rule.data.get("die"),"bad_results":rule.data.get("bad_results"),"source":source,
                "due_global_day":int(due),"completed":False,
            })
            session.add(roll);session.flush();journal(session,roll,"upsert",0);created += 1
    marriage_created, marriage_retired = _schedule_marriage_rolls(session, save, sims)
    created += marriage_created
    created += schedule_occult_rolls(session, save, sims)
    # Reconcile every historical event reached so far. This also backfills a Sim
    # added after an event date, while the stable source key prevents duplicates.
    event_candidates = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "event", Record.deleted.is_(False),
        Record.global_day.is_not(None), Record.global_day <= save.global_day,
    )))
    event_candidates = [
        event for event in event_candidates
        if not event_is_ignored(event) and event.data.get("active", True) and event.data.get("roll_required")
    ]
    event_groups: dict[tuple[str, int, int, str], list[Record]] = {}
    for event in event_candidates:
        event_groups.setdefault(_event_occurrence_key(event), []).append(event)
    events = []
    for group in event_groups.values():
        events.append(next((item for item in group if (item.data or {}).get("catalog_id")), group[0]))
    event_rules = _event_rule_map(session, save)
    households = {
        record.id: record for record in session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "household", Record.deleted.is_(False),
        ))
    }
    household_locations = {
        _normalized_location(value)
        for household in households.values()
        for value in (
            (household.data or {}).get("country"),
            (household.data or {}).get("location"),
            (household.data or {}).get("world"),
        )
        if _normalized_location(value)
    }
    fallback_location = next(iter(household_locations)) if len(household_locations) == 1 else ""
    existing_event_sources = set(session.scalars(select(Record.data["source"].as_string()).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["source"].as_string().like("event:%"),
    )))
    for event in events:
        due = int(event.data.get("start_global_day", event.global_day))
        if due < 1:
            continue
        rule_data = event_rules.get(event_key(event), {})
        spec = event_roll_configuration(event, rule_data)
        equivalent_event_ids = {
            equivalent.id for equivalent in event_groups[_event_occurrence_key(event)]
        }
        for sim in sims:
            source = f"event:{event.id}:{sim.id}"
            death = sim.data.get("death_global_day")
            household = households.get(str((sim.data or {}).get("current_household_id") or ""))
            event_text=f"{event.label} {(event.data or {}).get('scope','')} {(event.data or {}).get('notes','')}".casefold()
            servo_exempt="Servo" in occult_rules.sim_occult_types(sim.data) and any(word in event_text for word in ("disease","illness","plague","epidemic","pandemic","famine","starvation","drown"))
            equivalent_source_exists = any(
                f"event:{event_id}:{sim.id}" in existing_event_sources
                for event_id in equivalent_event_ids
            )
            if bool((sim.data or {}).get("game_was_dead")) or (death is not None and int(death) <= save.global_day) or equivalent_source_exists or servo_exempt or not _event_applies(event, sim, due, rule_data, household, save, fallback_location):
                continue
            roll = Record(save_id=save.id, kind="roll", label=f"{event.label} — {sim.label}", global_day=due, data={
                "event_id": event.id, "source_id": event.id, "sim_id": sim.id, "sim_name": sim.label,
                "roll_type": f"Event — {event.label}", "die": spec["die"], "bad_results": spec["bad_results"],
                "result_rules": spec["result_rules"], "failure_outcome": spec["failure_outcome"],
                "failure_is_lethal": spec["failure_is_lethal"], "nonlethal": not spec["failure_is_lethal"],
                "event_rule_id": spec["event_rule_id"], "source": source, "due_global_day": due, "completed": False,
            })
            session.add(roll); session.flush(); journal(session, roll, "upsert", 0)
            existing_event_sources.add(source); created += 1
    save.revision += created + marriage_retired
    return created


def failed(actual: int, bad_results: str) -> bool:
    text = str(bad_results or "").replace("–", "-").replace("—", "-").replace("�", "-")
    range_pattern = r"(?<!\d)(-?\d+)\s*-\s*(-?\d+)(?!\d)"
    for match in re.finditer(range_pattern, text):
        low, high = map(int, match.groups())
        if min(low, high) <= actual <= max(low, high): return True
    singles = re.sub(range_pattern, " ", text)
    if actual in (int(value) for value in re.findall(r"-?\d+", singles)): return True
    return False


def _event_death_cause(session: Session, roll: Record) -> str | None:
    event_id = (roll.data or {}).get("event_id")
    event = session.get(Record, event_id) if event_id else None
    if not event:
        return None
    data = event.data or {}
    explicit = str(data.get("death_cause") or data.get("cause_of_death") or "").strip()
    if explicit:
        return explicit
    name = event.label
    text = f"{name} {data.get('scope','')} {data.get('notes','')}".casefold()
    if any(word in text for word in ("war", "battle", "siege", "invasion", "revolt", "massacre")):
        return f"Killed during {name}"
    if any(word in text for word in ("plague", "pandemic", "epidemic", "cholera", "influenza", "pox", "fever")):
        return name
    if "famine" in text or "starvation" in text:
        return f"Famine during {name}"
    if "fire" in text:
        return f"Fire during {name}"
    if any(word in text for word in ("flood", "storm", "earthquake", "eruption", "disaster")):
        return f"Disaster during {name}"
    return f"Death during {name}"


def _death_window(session: Session, save: ChronicleSave, roll: Record, sim: Record) -> tuple[int, int]:
    data = roll.data or {}
    start = max(1, int(data.get("death_window_start") or roll.global_day or save.global_day))
    end = int(data.get("death_window_end") or start)
    event_id = data.get("event_id")
    event = session.get(Record, event_id) if event_id else None
    if event:
        start = max(start, int(event.data.get("start_global_day", event.global_day) or start))
        end = max(start, int(event.data.get("end_global_day", event.global_day) or start))
    else:
        roll_type = str(data.get("roll_type") or "").casefold()
        if "maternal" not in roll_type:
            birth = sim.data.get("birth_global_day", sim.global_day)
            current_offset = AGING_STAGE_OFFSETS.get(roll_type)
            if birth is not None and current_offset is not None:
                later = sorted(offset for offset in AGING_STAGE_OFFSETS.values() if offset > current_offset)
                if later:
                    end = max(start, int(birth) + later[0] - 1)
                elif "elder" in roll_type:
                    end = max(start, int(birth) + int((save.settings or {}).get("elder_max_age_days", 320)))
    # Never assign a newly discovered death before the day on which the player
    # resolved the roll. It may be scheduled later within the applicable range.
    start = max(start, save.global_day)
    return start, max(start, end)


def _retire_rolls_after_death(session: Session, save: ChronicleSave, sim_id: str, death_day: int, source_roll_id: str) -> int:
    pending = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["sim_id"].as_string() == sim_id,
    )))
    retired = 0
    for item in pending:
        if item.id == source_roll_id or item.data.get("completed") or item.global_day is None:
            continue
        if death_day > save.global_day and int(item.global_day) < death_day:
            continue
        base = item.version
        item.deleted = True
        item.data = {**item.data, "retired_reason": "Sim dies before this obligation", "retired_global_day": save.global_day,
                     "retired_by_death_roll_id": source_roll_id}
        item.version += 1; journal(session, item, "delete", base); retired += 1
    return retired


def complete_roll(session: Session, save: ChronicleSave, roll: Record, actual: int, outcome_override: str = "") -> dict:
    if roll.kind != "roll" or roll.deleted: raise ValueError("That roll is unavailable.")
    base = roll.version
    pregnancy_count = None
    if bool(roll.data.get("pregnancy_count_roll")):
        pregnancy_count, automatic_outcome = pregnancy_count_result(actual, str(roll.data.get("result_rules") or ""), str(roll.data.get("zero_results") or ""))
        is_bad = False
    elif _marriage_roll(roll):
        automatic_outcome = marriage_roll_result(actual, str(roll.data.get("result_rules") or ""), str(roll.data.get("bad_results") or ""))
        is_bad = automatic_outcome == "Does not marry"
    else:
        is_bad = failed(actual, str(roll.data.get("bad_results") or ""))
        mapped_outcome = _mapped_roll_outcome(actual, str(roll.data.get("result_rules") or ""))
        automatic_outcome = mapped_outcome or (roll.data.get("failure_outcome") if is_bad else roll.data.get("success_outcome"))
        if roll.data.get("event_id") and is_bad:
            # Mixed event tables can contain both lethal and nonlethal failures.
            # The actual result controls death automation, not the event as a whole.
            roll.data = {**roll.data, "nonlethal": not _lethal_outcome(mapped_outcome) if mapped_outcome else not bool(roll.data.get("failure_is_lethal"))}
    rule_trigger_results=str(roll.data.get("trigger_results") or "")
    rule_triggered = bool(rule_trigger_results) and bool(roll.data.get("occult_roll") or roll.data.get("rule_generated")) and failed(actual,rule_trigger_results)
    roll.data = {**roll.data, "actual": actual, "outcome": outcome_override.strip() or automatic_outcome or ("Failed" if is_bad else "Passed"), "completed": True, "completed_global_day": save.global_day,
                 "triggered":rule_triggered if (roll.data.get("occult_roll") or roll.data.get("rule_generated")) and rule_trigger_results else roll.data.get("triggered")}
    allowance_changed = False
    if pregnancy_count is not None:
        roll.data = {**roll.data, "pregnancy_count":pregnancy_count}
        sim_id = roll.data.get("sim_id")
        allowance_sim = session.get(Record, sim_id) if sim_id else None
        if allowance_sim and allowance_sim.kind == "sim" and not allowance_sim.deleted:
            year = int(roll.data.get("planner_year") or (save.start_year + (save.global_day - 1) // max(1, save.days_per_year)))
            sim_data = dict(allowance_sim.data or {}); allowances = dict(sim_data.get("pregnancy_allowances") or {})
            allowances[str(year)] = {"allowed":pregnancy_count, "roll_id":roll.id, "recorded_global_day":save.global_day, "actual":actual}
            sim_data.update({
                "pregnancy_allowances":allowances, "pregnancy_allowance_count":pregnancy_count,
                "pregnancy_allowance_year":year, "pregnancy_allowance_roll_id":roll.id,
                "pregnancy_allowance_recorded_global_day":save.global_day,
            })
            sim_base = allowance_sim.version; allowance_sim.data = sim_data; allowance_sim.version += 1
            journal(session, allowance_sim, "upsert", sim_base); allowance_changed = True
    if _marriage_roll(roll):
        roll.data = {**roll.data, "nonlethal":True}
    occult_changed = apply_occult_roll_result(session, roll, actual)
    automatic_followups = _schedule_automatic_occult_followup(session, save, roll)
    roll.version += 1; journal(session, roll, "upsert", base)
    death = None
    death_created = False
    death_changed = False
    if is_bad and not bool(roll.data.get("nonlethal")):
        sim_id = roll.data.get("sim_id")
        sim = session.get(Record, sim_id) if sim_id else None
        if sim and not sim.deleted and not bool((sim.data or {}).get("death_confirmed")):
            window_start, window_end = _death_window(session, save, roll, sim)
            failed_roll_death_day = random.SystemRandom().randint(window_start, window_end)
            try:
                existing_death_day = int((sim.data or {}).get("death_global_day"))
            except (TypeError, ValueError):
                existing_death_day = None
            death_day = min(failed_roll_death_day, existing_death_day) if existing_death_day is not None else failed_roll_death_day
            roll_type = str(roll.data.get("roll_type") or "").casefold()
            group = "birth" if "maternal" in roll_type or "being born" in roll_type else "elder" if "elder" in roll_type else "infant" if any(x in roll_type for x in ("newborn", "infant")) else "child" if any(x in roll_type for x in ("toddler", "child", "preteen", "teen")) else "adult"
            event_cause = _event_death_cause(session, roll)
            pool = session.scalar(select(Record).where(Record.save_id == save.id, Record.kind == "death_causes", Record.label == group.title()))
            causes = (pool.data.get("causes") if pool else DEFAULT_DEATH_CAUSES[group])
            if isinstance(causes, str):
                causes = [value.strip() for value in re.split(r"[;\n]+", causes) if value.strip()]
            causes = causes or DEFAULT_DEATH_CAUSES[group]
            cause = event_cause or (random.SystemRandom().choice(causes) if (save.settings or {}).get("automatic_death_causes", True) else "Player choice")
            # Only rewrite the schedule when the failed-roll date is earlier.
            # If the Sim was already due to die sooner, that earlier date wins.
            if existing_death_day is None or failed_roll_death_day < existing_death_day:
                previous_cause = (sim.data or {}).get("cause_of_death")
                scheduled = session.scalar(select(Record).where(
                    Record.save_id == save.id, Record.kind == "death", Record.deleted.is_(False),
                    Record.data["sim_id"].as_string() == sim.id,
                ).order_by(Record.global_day.asc()).limit(1))
                date_fields = {
                    "historical_death_date_range": calendar_utils.date_range_label(death_day, save.start_year, save.days_per_year),
                    "death_date_precision": "challenge-day-only",
                }
                if scheduled and not bool((scheduled.data or {}).get("completed")):
                    death = scheduled
                    death_base = death.version
                    death.global_day = death_day
                    death.data = {
                        **death.data, "sim_id": sim.id, "cause": cause,
                        "source_roll_id": roll.id, "completed": False,
                        "rescheduled_from_global_day": existing_death_day,
                        "rescheduled_from_cause": (death.data or {}).get("cause") or previous_cause,
                        **date_fields,
                    }
                    death.version += 1; journal(session, death, "upsert", death_base)
                else:
                    death = Record(save_id=save.id, kind="death", label=f"Death of {sim.label}", global_day=death_day, data={
                        "sim_id": sim.id, "cause": cause, "source_roll_id": roll.id,
                        "completed": False, "rescheduled_from_global_day": existing_death_day,
                        "rescheduled_from_cause": previous_cause,
                        **date_fields,
                    })
                    session.add(death); session.flush(); journal(session, death, "upsert", 0)
                    death_created = True
                sim_data = dict(sim.data or {})
                for key in ("death_date", "historical_death_date", "death_game_hour", "death_game_minute", "death_time"):
                    sim_data.pop(key, None)
                sim_base = sim.version
                sim.data = {
                    **sim_data, "death_global_day": death_day, "cause_of_death": cause,
                    "death_confirmed": False, "rescheduled_from_global_day": existing_death_day,
                    "rescheduled_from_cause": previous_cause,
                    "death_source_roll_id": roll.id, **date_fields,
                }
                sim.version += 1; journal(session, sim, "upsert", sim_base)
                save.revision += end_illnesses_for_death(session, save, sim, death_day)
                death_changed = True
            save.revision += _retire_rolls_after_death(session, save, sim.id, death_day, roll.id)
    save.revision += 1 + int(death_changed) + int(allowance_changed) + occult_changed + automatic_followups
    return {
        "outcome": roll.data["outcome"], "death": sync.serialize(death) if death else None,
        "death_created": death_created, "death_changed": death_changed, "pregnancy_count":pregnancy_count,
        "automatic_followups": automatic_followups,
    }
