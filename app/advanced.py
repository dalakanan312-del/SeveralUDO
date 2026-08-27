from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

from .models import ChronicleSave, Record
from .core_rulesets import CORE_RULESETS


RULE_PACKS = (
    {"id": "historical_events", "name": "Historical Events & Wars", "description": "Global and regional event rolls, campaigns, and conditional follow-ups."},
    {"id": "occult_inheritance", "name": "Occult Inheritance", "description": "Inheritance, discovery, manifestation, ghost, and follow-up rules."},
    {"id": "healthcare_compatibility", "name": "Healthcare Compatibility", "description": "Optional illness telemetry and historically interpreted health episodes."},
    {"id": "avatar_decades", "name": "Avatar: The Last Airbender Decades — Add-on", "description": "Fifty independent bending, nation, Avatar, Spirit, war, and canon-timeline modules. Standard Decades rules remain in force."},
    {"id": "harry_potter_decades", "name": "Harry Potter Decades — Add-on", "description": "Nineteen magical-family modules, three timeline modes, Wizarding history, and five recurring event tables."},
    {"id": "game_of_thrones_decades", "name": "Game of Thrones Decades — Add-on", "description": "Sixty-nine House, succession, court, war, religion, dragon, season, and supernatural modules with BC/AC history."},
)


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def year_for(save: ChronicleSave, day) -> int | None:
    parsed = _int(day)
    return None if parsed is None else save.start_year + (parsed - 1) // max(1, save.days_per_year)


def living(sim: Record, day: int) -> bool:
    data = sim.data or {}
    birth = _int(data.get("birth_global_day"), _int(sim.global_day, 1))
    death = _int(data.get("death_global_day"))
    return (birth is None or birth <= day) and (death is None or death > day) and not bool(data.get("game_was_dead") and death is None)


def migrations_for(records: Iterable[Record]) -> list[Record]:
    return sorted(
        (item for item in records if item.kind == "migration" and not item.deleted),
        key=lambda item: (_int(item.global_day, 10**9), item.label.casefold()),
    )


def birth_country(sim: Record) -> str:
    data = sim.data or {}
    return str(data.get("birth_country") or data.get("birthplace") or data.get("country") or "Unknown").strip() or "Unknown"


def location_at(sim: Record, day: int, migrations: Iterable[Record]) -> str:
    current = birth_country(sim)
    for move in migrations:
        data = move.data or {}
        if str(data.get("sim_id") or "") != sim.id:
            continue
        move_day = _int(data.get("move_global_day"), _int(move.global_day))
        if move_day is not None and move_day <= day:
            current = str(data.get("to_country") or data.get("to_location") or current).strip() or current
    return current


def world_snapshot(records: Iterable[Record], save: ChronicleSave, year: int | None = None) -> dict:
    rows = list(records)
    sims = [item for item in rows if item.kind == "sim" and not item.deleted]
    moves = migrations_for(rows)
    selected_year = min(max(save.start_year, _int(year, year_for(save, save.global_day))), year_for(save, save.global_day))
    day = min(save.global_day, max(1, (selected_year - save.start_year + 1) * max(1, save.days_per_year)))
    countries: dict[str, list[Record]] = defaultdict(list)
    for sim in sims:
        if living(sim, day):
            countries[location_at(sim, day, moves)].append(sim)
    flows = Counter()
    for move in moves:
        move_day = _int((move.data or {}).get("move_global_day"), _int(move.global_day))
        if move_day is None or move_day > day:
            continue
        data = move.data or {}
        flows[(str(data.get("from_country") or "Unknown"), str(data.get("to_country") or "Unknown"))] += 1
    return {
        "year": selected_year,
        "day": day,
        "countries": sorted(((name, sorted(people, key=lambda item: item.label.casefold())) for name, people in countries.items()), key=lambda row: (-len(row[1]), row[0].casefold())),
        "flows": sorted(((source, destination, count) for (source, destination), count in flows.items()), key=lambda row: (-row[2], row[0], row[1])),
        "migrations": list(reversed(moves)),
        "living_count": sum(len(people) for people in countries.values()),
    }


def consistency_report(records: Iterable[Record], save: ChronicleSave) -> dict:
    rows = [item for item in records if not item.deleted]
    sims = [item for item in rows if item.kind == "sim"]
    relationships = [item for item in rows if item.kind == "relationship"]
    moves = migrations_for(rows)
    by_id = {item.id: item for item in sims}
    issues = []
    seen_game_ids: dict[str, Record] = {}
    seen_names: dict[str, Record] = {}

    def add(level: str, title: str, detail: str, record: Record | None = None):
        issues.append({"level": level, "title": title, "detail": detail, "record_id": record.id if record else "", "record_kind": record.kind if record else ""})

    for sim in sims:
        data = sim.data or {}
        birth, death = _int(data.get("birth_global_day")), _int(data.get("death_global_day"))
        if birth is None:
            add("warning", "Missing birth date", f"{sim.label} has no usable birth Global Day.", sim)
        if birth is not None and death is not None and death < birth:
            add("error", "Death precedes birth", f"{sim.label} dies on GD {death} but is born on GD {birth}.", sim)
        for key, label in (("mother_id", "mother"), ("father_id", "second parent")):
            parent = by_id.get(str(data.get(key) or ""))
            if parent and birth is not None:
                parent_birth = _int((parent.data or {}).get("birth_global_day"))
                if parent_birth is not None and parent_birth >= birth:
                    add("error", "Parent date conflict", f"{parent.label} is recorded as {sim.label}'s {label} but is not older.", sim)
        game_id = str(data.get("game_sim_id") or "").strip()
        if game_id:
            if game_id in seen_game_ids:
                add("error", "Duplicate game identity", f"{sim.label} and {seen_game_ids[game_id].label} share game Sim ID {game_id}.", sim)
            seen_game_ids[game_id] = sim
        name_key = sim.label.casefold().strip()
        if name_key and name_key in seen_names:
            add("warning", "Possible duplicate Sim", f"Two active profiles are named {sim.label}.", sim)
        seen_names[name_key] = sim
        if birth_country(sim) == "Unknown":
            add("info", "Birth country missing", f"Add {sim.label}'s birth country for accurate migration history.", sim)

    for relationship in relationships:
        data = relationship.data or {}
        start = _int(data.get("start_global_day"), _int(relationship.global_day))
        for partner_key in ("partner1_id", "partner2_id"):
            partner = by_id.get(str(data.get(partner_key) or ""))
            death = _int((partner.data or {}).get("death_global_day")) if partner else None
            if partner and start is not None and death is not None and start > death:
                add("error", "Relationship begins after death", f"{relationship.label} begins on GD {start}, after {partner.label}'s death on GD {death}.", relationship)

    for move in moves:
        data = move.data or {}
        sim = by_id.get(str(data.get("sim_id") or ""))
        move_day = _int(data.get("move_global_day"), _int(move.global_day))
        birth = _int((sim.data or {}).get("birth_global_day")) if sim else None
        if not sim:
            add("error", "Migration has no Sim", f"{move.label} points to a missing profile.", move)
        elif move_day is not None and birth is not None and move_day < birth:
            add("error", "Migration precedes birth", f"{sim.label}'s move on GD {move_day} predates birth on GD {birth}.", move)

    counts = Counter(item["level"] for item in issues)
    return {"issues": issues, "counts": dict(counts), "total": len(issues), "score": max(0, round(100 - min(100, counts["error"] * 12 + counts["warning"] * 4 + counts["info"])))}


def progress_dashboard(records: Iterable[Record], save: ChronicleSave) -> dict:
    rows = [item for item in records if not item.deleted]
    sims = [item for item in rows if item.kind == "sim"]
    living_sims = [item for item in sims if living(item, save.global_day)]
    generations = [_int((item.data or {}).get("generation")) for item in sims]
    generations = [value for value in generations if value is not None]
    pending = [item for item in rows if item.kind == "roll" and not bool((item.data or {}).get("completed")) and (_int(item.global_day, 10**9) <= save.global_day)]
    complete_fields = 0
    possible_fields = max(1, len(sims) * 4)
    for sim in sims:
        data = sim.data or {}
        complete_fields += sum(value not in (None, "") for value in (data.get("birth_global_day"), data.get("sex"), birth_country(sim) if birth_country(sim) != "Unknown" else None, data.get("generation")))
    selected = list((save.settings or {}).get("selected_rule_packs") or [])
    return {
        "current_year": year_for(save, save.global_day), "global_day": save.global_day,
        "living": len(living_sims), "deceased": len(sims) - len(living_sims), "total_sims": len(sims),
        "generations": len(set(generations)), "highest_generation": max(generations, default=None),
        "pending_due": len(pending), "active_pregnancies": sum(item.kind == "pregnancy" and str((item.data or {}).get("status") or "active").casefold() == "active" for item in rows),
        "active_illnesses": sum(item.kind == "illness" and str((item.data or {}).get("status") or "active").casefold() not in {"recovered", "resolved", "deceased", "ended"} for item in rows),
        "data_completion": round(complete_fields * 100 / possible_fields), "rule_packs": selected,
        "milestones": [
            {"label": "First generation recorded", "complete": bool(sims)},
            {"label": "A successor identified", "complete": bool((save.settings or {}).get("succession_root_id"))},
            {"label": "Three generations reached", "complete": len(set(generations)) >= 3},
            {"label": "Rule packs selected", "complete": bool(selected)},
            {"label": "Core profile data 90% complete", "complete": round(complete_fields * 100 / possible_fields) >= 90},
        ],
    }


def automation_digest(records: Iterable[Record]) -> dict:
    pending = [item for item in records if item.kind == "game_candidate" and not item.deleted and str((item.data or {}).get("status") or "pending") == "pending"]
    weights = {"sim_death": 100, "new_baby": 95, "pregnancy_outcome": 90, "new_sim": 85, "pregnancy_discovered": 80, "illness_detected": 75, "relationship_change": 60, "household_change": 45, "sim_identity_change": 40, "illness_recovered": 35}
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in pending:
        data = item.data or {}; payload = data.get("payload") or data
        action = str(data.get("action") or "game_change")
        confidence = str(payload.get("confidence") or data.get("confidence") or "review").casefold()
        priority = weights.get(action, 50) + (10 if confidence in {"high", "exact"} else -10 if confidence in {"low", "uncertain"} else 0)
        session_key = str(payload.get("session_id") or payload.get("report_id") or f"GD {item.global_day or '?'}")
        groups[session_key].append({"record": item, "action": action, "priority": priority, "confidence": confidence})
    digest_groups = []
    for key, items in groups.items():
        items.sort(key=lambda row: (-row["priority"], row["record"].created_at))
        digest_groups.append({"key": key, "items": items, "count": len(items), "high": sum(row["priority"] >= 75 for row in items)})
    digest_groups.sort(key=lambda row: max((item["priority"] for item in row["items"]), default=0), reverse=True)
    return {"groups": digest_groups, "total": len(pending), "high_priority": sum(row["priority"] >= 75 for group in digest_groups for row in group["items"]), "by_action": Counter(str((item.data or {}).get("action") or "game_change").replace("_", " ").title() for item in pending).most_common()}


def biographies(records: Iterable[Record], save: ChronicleSave) -> list[dict]:
    rows = [item for item in records if not item.deleted]
    sims = [item for item in rows if item.kind == "sim"]
    moves = migrations_for(rows)
    by_sim: dict[str, list[Record]] = defaultdict(list)
    for item in rows:
        data = item.data or {}
        for key in ("sim_id", "mother_id", "partner1_id", "partner2_id"):
            if data.get(key): by_sim[str(data[key])].append(item)
    result = []
    for sim in sims:
        data = sim.data or {}; birth = _int(data.get("birth_global_day"), _int(sim.global_day)); death = _int(data.get("death_global_day"))
        born = year_for(save, birth); died = year_for(save, death)
        facts = []
        if born is not None: facts.append(f"born in {born} in {birth_country(sim)}")
        if data.get("generation") not in (None, ""): facts.append(f"a member of generation {data.get('generation')}")
        career = data.get("game_career") or data.get("career")
        if career: facts.append(f"known for {str(career).replace('_', ' ')}")
        sim_moves = [move for move in moves if str((move.data or {}).get("sim_id") or "") == sim.id]
        if sim_moves:
            route = [birth_country(sim)] + [str((move.data or {}).get("to_country") or "Unknown") for move in sim_moves]
            facts.append("whose life crossed " + " → ".join(dict.fromkeys(route)))
        if death is not None: facts.append(f"died in {died} from {data.get('cause_of_death') or 'an unrecorded cause'}")
        related = by_sim.get(sim.id, [])
        illnesses = [item for item in related if item.kind == "illness"]
        relationships = [item for item in related if item.kind == "relationship"]
        milestones = data.get("game_milestones") or data.get("milestones") or []
        detail = f"{sim.label} was " + (", ".join(facts) if facts else "recorded in the family chronicle") + "."
        if relationships: detail += f" The tracker preserves {len(relationships)} significant relationship{'s' if len(relationships) != 1 else ''}."
        if illnesses: detail += f" {len(illnesses)} illness episode{'s are' if len(illnesses) != 1 else ' is'} recorded."
        if milestones: detail += f" {len(milestones)} game milestone{'s' if len(milestones) != 1 else ''} enrich the surviving profile."
        result.append({"sim": sim, "text": detail, "birth_year": born, "death_year": died, "current_country": location_at(sim, save.global_day, moves)})
    return sorted(result, key=lambda row: (_int((row["sim"].data or {}).get("generation"), 10**9), _int((row["sim"].data or {}).get("birth_global_day"), 10**9), row["sim"].label.casefold()))


def mod_compatibility(records: Iterable[Record]) -> dict:
    rows = [item for item in records if not item.deleted]
    text_parts = []
    capabilities = {}
    clock_versions = []
    for item in rows:
        data = item.data or {}
        for key in ("detected_optional_mods", "installed_mods", "provider", "source", "game_build"):
            value = data.get(key)
            if value: text_parts.append(str(value))
        if data.get("clock_sync_version"): clock_versions.append(str(data["clock_sync_version"]))
        if isinstance(data.get("telemetry_capabilities"), dict): capabilities.update(data["telemetry_capabilities"])
    text = " ".join(text_parts).casefold()
    definitions = (
        ("Clock Sync", True, "Core clock, population, relationship, health, and life-event telemetry."),
        ("Healthcare Redux", any(token in text for token in ("healthcare redux", "adeepindigo", "hcr")), "Optional illness names, symptoms, diagnoses, and recovery signals."),
        ("Relationship & Pregnancy Overhaul", any(token in text for token in ("relationship pregnancy overhaul", "rpo", "lumpinou")), "Optional pregnancy and relationship detail."),
        ("MC Command Center", any(token in text for token in ("mc command center", "mccc", "deaderpool")), "Optional population context; not required by the tracker."),
        ("Responsible Pregnancy", "responsible pregnancy" in text, "Optional pregnancy intent and outcome metadata."),
    )
    return {"mods": [{"name": name, "detected": detected, "detail": detail} for name, detected, detail in definitions], "clock_version": max(clock_versions, default="Not reported"), "capabilities": sorted(capabilities.items()), "capability_count": sum(bool(value) for value in capabilities.values())}


def simulate_rules(records: Iterable[Record], save: ChronicleSave, pregnancy_days=None, kinship_depth=None, mortality_multiplier=None) -> dict:
    rows = [item for item in records if not item.deleted]
    active_pregnancies = [item for item in rows if item.kind == "pregnancy" and str((item.data or {}).get("status") or "active").casefold() == "active"]
    new_pregnancy_days = max(1, min(100, _int(pregnancy_days, save.pregnancy_days)))
    new_kinship = max(1, min(8, _int(kinship_depth, _int((save.settings or {}).get("kinship_detection_generations"), 3))))
    multiplier = max(0.1, min(5.0, float(mortality_multiplier or 1.0)))
    pending_rolls = [item for item in rows if item.kind == "roll" and not bool((item.data or {}).get("completed"))]
    lethal = [item for item in pending_rolls if not bool((item.data or {}).get("nonlethal"))]
    return {
        "pregnancy_days": new_pregnancy_days, "kinship_depth": new_kinship, "mortality_multiplier": multiplier,
        "pregnancy_changes": [{"label": item.label, "current_due": _int((item.data or {}).get("due_global_day"), _int(item.global_day)), "simulated_due": _int((item.data or {}).get("conception_global_day"), save.global_day) + new_pregnancy_days} for item in active_pregnancies],
        "pending_rolls": len(pending_rolls), "lethal_rolls": len(lethal), "simulated_lethal_pressure": round(len(lethal) * multiplier, 1),
        "saved": False,
    }
