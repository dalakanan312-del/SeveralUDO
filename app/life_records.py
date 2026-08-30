from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import advanced, domain
from .models import ChronicleSave, Record


CHILD_STAGES = {"baby", "infant", "toddler", "child", "teen"}
ADULT_STAGES = {"young adult", "youngadult", "adult", "elder"}
PARTNER_WORDS = ("marriage", "married", "spouse", "courtship", "betrothal", "engagement", "fiance")

DOWRY_BASES = {
    "royal": 20000,
    "royalty": 20000,
    "noble": 10000,
    "nobility": 10000,
    "aristocracy": 10000,
    "gentry": 5000,
    "upper": 5000,
    "merchant": 2500,
    "middle": 2500,
    "artisan": 1200,
    "working": 800,
    "peasant": 500,
    "poor": 200,
}

VIEW_PRESETS = (
    ("Unmarried adults", "/p/relationships", "Marriage planning and courtship candidates"),
    ("Children needing guardians", "/p/life-records#guardianship", "Orphans and incomplete guardian records"),
    ("People away from home", "/p/life-records#absence", "Travel, deployment, imprisonment, study and missing Sims"),
    ("Open legal cases", "/p/life-records#law", "Accusations, trials and unresolved punishments"),
    ("Mourning and recovery", "/p/life-records#care", "Grief, treatment and temporary restrictions"),
    ("Data contradictions", "/p/life-records#tools", "Dates and family links that need correction"),
)


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value) -> str:
    return str(value or "").strip()


def _living(sim: Record, save: ChronicleSave) -> bool:
    return advanced.living(sim, save.global_day)


def _birth(sim: Record) -> int | None:
    return _int((sim.data or {}).get("birth_global_day"), _int(sim.global_day))


def _age(sim: Record, save: ChronicleSave) -> int | None:
    birth = _birth(sim)
    return None if birth is None else max(0, save.global_day - birth)


def _stage(sim: Record) -> str:
    data = sim.data or {}
    return _text(data.get("game_age") or data.get("life_stage") or data.get("age_stage")).casefold()


def _parent_ids(sim: Record) -> set[str]:
    data = sim.data or {}
    values = {_text(value) for value in (data.get("parent_ids") or []) if value}
    values.update(filter(None, (_text(data.get("mother_id")), _text(data.get("father_id")))))
    return values


def _is_minor(sim: Record, save: ChronicleSave) -> bool:
    stage = _stage(sim)
    if stage:
        return stage in CHILD_STAGES
    age = _age(sim, save)
    threshold = max(1, _int((save.settings or {}).get("marriage_min_age_days"), 72))
    return age is not None and age < threshold


def _is_adult(sim: Record, save: ChronicleSave) -> bool:
    stage = _stage(sim)
    if stage:
        return stage in ADULT_STAGES
    age = _age(sim, save)
    threshold = max(1, _int((save.settings or {}).get("marriage_min_age_days"), 72))
    return age is not None and age >= threshold


def _group(records: Iterable[Record]) -> dict[str, list[Record]]:
    result: dict[str, list[Record]] = defaultdict(list)
    for item in records:
        if not item.deleted:
            result[item.kind].append(item)
    return result


def _sim_household(sim: Record, households: dict[str, Record]) -> Record | None:
    return households.get(_text((sim.data or {}).get("current_household_id")))


def _social_class(sim: Record, households: dict[str, Record]) -> str:
    own = _text((sim.data or {}).get("social_class"))
    home = _sim_household(sim, households)
    return own or _text((home.data or {}).get("social_class") if home else "") or "Unrecorded"


def _funds(home: Record | None) -> int | None:
    if not home:
        return None
    data = home.data or {}
    for key in ("last_game_funds", "household_funds", "funds"):
        value = _int(data.get(key))
        if value is not None:
            return max(0, value)
    return None


def _birth_order(sim: Record, sims: list[Record]) -> tuple[int | None, int]:
    parents = _parent_ids(sim)
    if not parents:
        return None, 1
    siblings = [item for item in sims if _parent_ids(item) == parents and _birth(item) is not None]
    siblings.sort(key=lambda item: (_birth(item), item.created_at, item.label.casefold()))
    try:
        return siblings.index(sim) + 1, len(siblings)
    except ValueError:
        return None, len(siblings)


def _dowry_amount(sim: Record, sims: list[Record], households: dict[str, Record]) -> tuple[int, str]:
    social_class = _social_class(sim, households)
    folded = social_class.casefold()
    base = next((amount for key, amount in DOWRY_BASES.items() if key in folded), 1000)
    order, _siblings = _birth_order(sim, sims)
    if order == 1:
        base = round(base * 1.25)
    elif order and order >= 3:
        base = round(base * 0.85)
    home = _sim_household(sim, households)
    funds = _funds(home)
    if funds is not None:
        affordable = max(50, round((funds * 0.15) / 50) * 50)
        base = min(base * 2, max(round(base * 0.5), affordable))
    amount = max(50, round(base / 50) * 50)
    method = f"{social_class} base"
    if order:
        method += f", birth order {order}"
    if funds is not None:
        method += f", household funds §{funds}"
    return amount, method


def dowries(grouped: dict[str, list[Record]], save: ChronicleSave) -> dict:
    sims = grouped.get("sim", [])
    sims_by_id = {item.id: item for item in sims}
    households = {item.id: item for item in grouped.get("household", [])}
    plans = {_text((item.data or {}).get("relationship_id")): item for item in grouped.get("dowry_plan", [])}
    rows = []
    for relationship in grouped.get("relationship", []):
        data = relationship.data or {}
        kind = _text(data.get("type")).casefold()
        status = _text(data.get("status") or "Active").casefold()
        if status in {"ended", "divorced", "annulled", "widowed"}:
            continue
        if bool(data.get("legally_married")) or "marriage" in kind or "married" in kind:
            continue
        if not any(word in kind for word in ("courtship", "betrothal", "engagement", "fiance")) and data.get("suggested_marriage_global_day") in (None, ""):
            continue
        first = sims_by_id.get(_text(data.get("partner1_id")))
        second = sims_by_id.get(_text(data.get("partner2_id")))
        if not first or not second:
            continue
        suggested, method = _dowry_amount(first, sims, households)
        plan = plans.get(relationship.id)
        rows.append({
            "relationship": relationship,
            "first": first,
            "second": second,
            "plan": plan,
            "suggested": suggested,
            "method": method,
            "marriage_day": _int(data.get("suggested_marriage_global_day")),
            "payer_household": _sim_household(first, households),
            "recipient_household": _sim_household(second, households),
        })
    rows.sort(key=lambda row: (row["marriage_day"] if row["marriage_day"] is not None else 10**9, row["relationship"].label.casefold()))
    return {"rows": rows, "plans": list(plans.values())}


def guardianship(grouped: dict[str, list[Record]], save: ChronicleSave) -> dict:
    sims = grouped.get("sim", [])
    sims_by_id = {item.id: item for item in sims}
    existing = {
        _text((item.data or {}).get("ward_sim_id")): item
        for item in grouped.get("guardianship", [])
        if _text((item.data or {}).get("status") or "Active").casefold() not in {"ended", "complete", "closed"}
    }
    rows = []
    for child in sims:
        if not _living(child, save) or not _is_minor(child, save):
            continue
        parents = [sims_by_id[parent_id] for parent_id in _parent_ids(child) if parent_id in sims_by_id]
        if not parents or any(_living(parent, save) for parent in parents):
            continue
        candidates = []
        child_parents = _parent_ids(child)
        for candidate in sims:
            if candidate.id == child.id or not _living(candidate, save) or not _is_adult(candidate, save):
                continue
            relation_score = 0
            if _parent_ids(candidate) & child_parents:
                relation_score += 80
            if candidate.id in set().union(*(_parent_ids(parent) for parent in parents)):
                relation_score += 70
            if _text((candidate.data or {}).get("current_household_id")) == _text((child.data or {}).get("current_household_id")):
                relation_score += 40
            age = _age(candidate, save) or 0
            candidates.append((relation_score + min(age, 100) / 10, candidate))
        candidates.sort(key=lambda value: (value[0], value[1].label.casefold()), reverse=True)
        rows.append({"ward": child, "parents": parents, "record": existing.get(child.id), "candidates": [item for _score, item in candidates[:8]]})
    return {"rows": rows, "active": list(existing.values())}


def birth_order(grouped: dict[str, list[Record]]) -> dict:
    sims = grouped.get("sim", [])
    recorded = {_text((item.data or {}).get("sim_id")): item for item in grouped.get("birth_privilege", [])}
    families: dict[tuple[str, ...], list[Record]] = defaultdict(list)
    for sim in sims:
        parents = tuple(sorted(_parent_ids(sim)))
        if parents:
            families[parents].append(sim)
    rows = []
    for siblings in families.values():
        if len(siblings) < 2:
            continue
        siblings.sort(key=lambda item: (_birth(item) if _birth(item) is not None else 10**9, item.created_at, item.label.casefold()))
        for index, sim in enumerate(siblings, 1):
            default = "Primary heir / first consideration" if index == 1 else "Secondary heir / alternate path" if index == 2 else "Marriage, service, religion, trade, or migration"
            rows.append({"sim": sim, "order": index, "siblings": len(siblings), "default": default, "record": recorded.get(sim.id)})
    rows.sort(key=lambda row: (_text((row["sim"].data or {}).get("dynasty_name")), row["order"], row["sim"].label.casefold()))
    return {"rows": rows}


def milestones_and_dispersal(grouped: dict[str, list[Record]], save: ChronicleSave) -> dict:
    sims = grouped.get("sim", [])
    sims_by_id = {item.id: item for item in sims}
    households = {item.id: item for item in grouped.get("household", [])}
    ceremonies = {_text((item.data or {}).get("sim_id")): item for item in grouped.get("coming_of_age", [])}
    dispersals = {_text((item.data or {}).get("sim_id")): item for item in grouped.get("dispersal_plan", []) if _text((item.data or {}).get("status") or "Planned").casefold() not in {"complete", "cancelled", "ended"}}
    married = set()
    for relationship in grouped.get("relationship", []):
        data = relationship.data or {}
        status = _text(data.get("status") or "Active").casefold()
        kind = _text(data.get("type")).casefold()
        if status not in {"ended", "divorced", "annulled"} and (bool(data.get("legally_married")) or "marriage" in kind or "spouse" in kind):
            married.update((_text(data.get("partner1_id")), _text(data.get("partner2_id"))))
    threshold = max(1, _int((save.settings or {}).get("marriage_min_age_days"), 72))
    coming = []
    dispersal = []
    for sim in sims:
        if not _living(sim, save):
            continue
        birth = _birth(sim)
        if birth is not None and sim.id not in ceremonies and (_stage(sim) in {"teen", "young adult", "youngadult"} or birth + threshold <= save.global_day + save.days_per_year):
            coming.append({"sim": sim, "due": max(save.global_day, birth + threshold), "record": None})
        if not _is_adult(sim, save) or sim.id in married or sim.id in dispersals:
            continue
        home_id = _text((sim.data or {}).get("current_household_id"))
        if not home_id:
            continue
        relatives_at_home = []
        parents = _parent_ids(sim)
        for other in sims:
            if other.id == sim.id or _text((other.data or {}).get("current_household_id")) != home_id:
                continue
            if other.id in parents or (_parent_ids(other) and _parent_ids(other) == parents):
                relatives_at_home.append(other)
        if relatives_at_home:
            dispersal.append({"sim": sim, "home": households.get(home_id), "relatives": relatives_at_home, "record": None})
    return {"coming": coming, "ceremonies": list(ceremonies.values()), "dispersal": dispersal, "plans": list(dispersals.values()), "sims_by_id": sims_by_id}


def roll_explanations(grouped: dict[str, list[Record]], save: ChronicleSave) -> list[dict]:
    by_id = {item.id: item for items in grouped.values() for item in items}
    rows = []
    for roll in grouped.get("roll", []):
        data = roll.data or {}
        if bool(data.get("completed")) or roll.global_day is None or int(roll.global_day) > save.global_day:
            continue
        reasons = []
        source = by_id.get(_text(data.get("event_id") or data.get("source_event_id") or data.get("source_id")))
        if source:
            reasons.append(f"Scheduled from {source.kind.replace('_', ' ')} “{source.label}”.")
        if data.get("age_days") not in (None, ""):
            reasons.append(f"The Sim reached the configured age of {data.get('age_days')} days.")
        if data.get("pregnancy_id"):
            reasons.append("This obligation belongs to an active or recently completed pregnancy.")
        if data.get("followup_for_roll_id") or data.get("source_roll_id"):
            reasons.append("A previous result triggered this conditional follow-up.")
        folded = _text(data.get("roll_type") or roll.label).casefold()
        if "marriage" in folded:
            reasons.append("The Sim met the active marriage or remarriage eligibility rule.")
        if data.get("event_id"):
            reasons.append("The Sim was eligible while the historical event was active.")
        if not reasons:
            reasons.append(f"Created by {_text(data.get('source') or data.get('rule_source') or 'the active rule table')}.")
        rows.append({
            "roll": roll,
            "reasons": reasons,
            "die": _text(data.get("die") or data.get("configured_die") or "Not configured"),
            "bad_results": _text(data.get("bad_results") or data.get("configured_bad_results") or "No bad result recorded"),
            "overdue": max(0, save.global_day - int(roll.global_day)),
        })
    rows.sort(key=lambda row: (row["roll"].global_day, row["roll"].label.casefold()))
    return rows[:80]


def contradictions(grouped: dict[str, list[Record]], save: ChronicleSave) -> list[dict]:
    sims = grouped.get("sim", [])
    sims_by_id = {item.id: item for item in sims}
    issues = []
    numbers: dict[str, list[Record]] = defaultdict(list)
    for sim in sims:
        data = sim.data or {}
        birth = _birth(sim)
        death = _int(data.get("death_global_day"))
        number = _text(data.get("sim_number"))
        if number:
            numbers[number.casefold()].append(sim)
        if birth is not None and death is not None and death < birth:
            issues.append({"severity": "error", "title": "Death before birth", "detail": f"{sim.label} dies on GD {death} but is born on GD {birth}.", "href": f"/sims/{sim.id}#edit-sim"})
        for parent_id in _parent_ids(sim):
            parent = sims_by_id.get(parent_id)
            if not parent or birth is None or _birth(parent) is None:
                continue
            if _birth(parent) >= birth:
                issues.append({"severity": "error", "title": "Parent date conflict", "detail": f"{parent.label} is not older than child {sim.label}.", "href": f"/sims/{sim.id}#edit-sim"})
    for number, matches in numbers.items():
        if len(matches) > 1:
            issues.append({"severity": "warning", "title": "Duplicate Sim ID", "detail": f"{', '.join(item.label for item in matches)} share {number}.", "href": "/p/sims"})
    for relationship in grouped.get("relationship", []):
        data = relationship.data or {}
        start = _int(data.get("start_global_day"), _int(relationship.global_day))
        partners = [sims_by_id.get(_text(data.get("partner1_id"))), sims_by_id.get(_text(data.get("partner2_id")))]
        for partner in (item for item in partners if item):
            if start is not None and _birth(partner) is not None and start < _birth(partner):
                issues.append({"severity": "error", "title": "Relationship before birth", "detail": f"{relationship.label} begins before {partner.label} was born.", "href": f"/relationships/{relationship.id}"})
    pregnancies_by_mother: dict[str, list[Record]] = defaultdict(list)
    for pregnancy in grouped.get("pregnancy", []):
        data = pregnancy.data or {}
        conception = _int(data.get("conception_global_day"))
        due = _int(data.get("due_global_day"), _int(pregnancy.global_day))
        if conception is not None and due is not None and due < conception:
            issues.append({"severity": "error", "title": "Pregnancy dates reversed", "detail": f"{pregnancy.label} is due before conception.", "href": f"/pregnancies/{pregnancy.id}"})
        mother_id = _text(data.get("mother_id"))
        if mother_id:
            pregnancies_by_mother[mother_id].append(pregnancy)
    for mother_id, pregnancies in pregnancies_by_mother.items():
        active = [item for item in pregnancies if _text((item.data or {}).get("status") or "Active").casefold() not in domain.CLOSED_PREGNANCIES]
        if len(active) > 1:
            mother = sims_by_id.get(mother_id)
            issues.append({"severity": "warning", "title": "Overlapping active pregnancies", "detail": f"{mother.label if mother else 'One Sim'} has {len(active)} open pregnancy records.", "href": "/p/pregnancies"})
    issues.sort(key=lambda item: (item["severity"] != "error", item["title"], item["detail"]))
    return issues[:100]


def schedule_grief_candidates(session: Session, save: ChronicleSave, deceased: Record, death_day: int | None = None) -> list[Record]:
    """Create one reviewable grief suggestion per close surviving relative."""
    death_day = _int(death_day, _int((deceased.data or {}).get("death_global_day"), save.global_day))
    sims = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False))))
    relationships = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "relationship", Record.deleted.is_(False))))
    sims_by_id = {item.id: item for item in sims}
    close: dict[str, str] = {}
    for parent_id in _parent_ids(deceased):
        close[parent_id] = "Parent"
    deceased_parents = _parent_ids(deceased)
    for person in sims:
        parents = _parent_ids(person)
        if deceased.id in parents:
            close[person.id] = "Child"
        elif person.id != deceased.id and deceased_parents and parents == deceased_parents:
            close[person.id] = "Sibling"
    for relationship in relationships:
        data = relationship.data or {}
        pair = {_text(data.get("partner1_id")), _text(data.get("partner2_id"))}
        if deceased.id not in pair or not any(word in _text(data.get("type")).casefold() for word in PARTNER_WORDS):
            continue
        other_id = next((value for value in pair if value and value != deceased.id), "")
        if other_id:
            close[other_id] = "Spouse / partner"
    created = []
    durations = {"Spouse / partner": max(1, save.days_per_year), "Parent": max(1, save.days_per_year // 2), "Child": max(1, save.days_per_year // 2), "Sibling": max(1, save.days_per_year // 4)}
    for sim_id, relation in close.items():
        person = sims_by_id.get(sim_id)
        if not person or not _living(person, save):
            continue
        source_key = f"grief:{deceased.id}:{person.id}:{death_day}"
        exists = session.scalar(select(Record.id).where(
            Record.save_id == save.id, Record.kind == "game_candidate", Record.deleted.is_(False),
            Record.data["source_key"].as_string() == source_key,
        ).limit(1))
        if exists:
            continue
        duration = durations.get(relation, max(1, save.days_per_year // 4))
        item = Record(
            save_id=save.id, kind="game_candidate", label=f"Mourning review: {person.label}", global_day=death_day,
            data={"action": "grief_detected", "status": "pending", "sim_id": person.id, "source_key": source_key,
                  "payload": {"deceased_sim_id": deceased.id, "deceased_name": deceased.label, "relationship": relation,
                              "start_global_day": death_day, "suggested_end_global_day": death_day + duration,
                              "source": "Confirmed family death"}},
        )
        session.add(item); session.flush(); domain.journal(session, item, "upsert", 0); created.append(item)
    return created


def annual_newspaper(save: ChronicleSave, records: Iterable[Record], year: int) -> dict:
    start_day = (year - save.start_year) * save.days_per_year + 1
    end_day = start_day + save.days_per_year - 1
    rows = [item for item in records if item.global_day is not None and start_day <= int(item.global_day) <= end_day and not item.deleted]
    births = [item for item in rows if item.kind == "sim"]
    deaths = [item for item in rows if item.kind == "death" or (item.kind == "sim" and _int((item.data or {}).get("death_global_day")) == item.global_day)]
    marriages = [item for item in rows if item.kind == "relationship" and (bool((item.data or {}).get("legally_married")) or "marriage" in _text((item.data or {}).get("type")).casefold())]
    events = [item for item in rows if item.kind == "event"]
    moves = [item for item in rows if item.kind in {"migration", "migration_plan"}]
    legal = [item for item in rows if item.kind in {"legal_case", "reputation_event"}]
    lines = [f"THE {save.name.upper()} CHRONICLE — {year}"]
    facts = []
    sections = (
        ("Births", births), ("Deaths", deaths), ("Marriages", marriages),
        ("Public events", events), ("Arrivals and departures", moves), ("Court and reputation", legal),
    )
    for heading, items in sections:
        if not items:
            continue
        names = "; ".join(item.label for item in sorted(items, key=lambda value: (value.global_day or 0, value.label.casefold()))[:12])
        lines.append(f"{heading}: {names}.")
        facts.extend(item.id for item in items)
    if len(lines) == 1:
        lines.append("No major births, deaths, marriages, migrations, legal matters, or historical events were recorded this year.")
    body = "\n\n".join(lines)
    return {
        "label": f"{save.name} Chronicle — {year}", "body": body, "source_record_ids": facts,
        "year": year, "global_day": end_day, "generated_from_live_records": True,
    }


def build(records: Iterable[Record], save: ChronicleSave) -> dict:
    grouped = _group(records)
    sims = sorted(grouped.get("sim", []), key=lambda item: item.label.casefold())
    current_year = advanced.year_for(save, save.global_day) or save.start_year
    return {
        "sims": sims,
        "households": sorted(grouped.get("household", []), key=lambda item: item.label.casefold()),
        "dowries": dowries(grouped, save),
        "guardianship": guardianship(grouped, save),
        "birth_order": birth_order(grouped),
        "milestones": milestones_and_dispersal(grouped, save),
        "mobility": sorted(grouped.get("social_mobility", []), key=lambda item: (item.global_day or 0, item.updated_at), reverse=True),
        "legal_cases": sorted(grouped.get("legal_case", []), key=lambda item: (item.global_day or 0, item.updated_at), reverse=True),
        "absences": sorted(grouped.get("absence", []), key=lambda item: (item.global_day or 0, item.updated_at), reverse=True),
        "disabilities": sorted(grouped.get("disability", []), key=lambda item: (item.global_day or 0, item.updated_at), reverse=True),
        "mourning": sorted(grouped.get("mourning", []), key=lambda item: (item.global_day or 0, item.updated_at), reverse=True),
        "wellbeing": sorted(grouped.get("wellbeing", []), key=lambda item: (item.global_day or 0, item.updated_at), reverse=True),
        "treatments": sorted(grouped.get("medical_treatment", []), key=lambda item: (item.global_day or 0, item.updated_at), reverse=True),
        "restrictions": sorted(grouped.get("recovery_restriction", []), key=lambda item: (item.global_day or 0, item.updated_at), reverse=True),
        "illnesses": sorted(grouped.get("illness", []), key=lambda item: (item.global_day or 0, item.label.casefold()), reverse=True),
        "roll_explanations": roll_explanations(grouped, save),
        "contradictions": contradictions(grouped, save),
        "saved_views": sorted(grouped.get("saved_view", []), key=lambda item: item.label.casefold()),
        "view_presets": VIEW_PRESETS,
        "newspapers": sorted(grouped.get("newspaper", []), key=lambda item: (_int((item.data or {}).get("year"), 0), item.updated_at), reverse=True),
        "current_year": current_year,
        "earliest_year": save.start_year,
    }
