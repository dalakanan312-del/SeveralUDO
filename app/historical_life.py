from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from typing import Iterable

from . import advanced
from .models import ChronicleSave, Record


ACCURACY_PROFILES = {
    "strict": {
        "name": "Strict historical play",
        "description": "Show every applicable warning and treat era preparation as required before advancing.",
        "tone": "required",
    },
    "story": {
        "name": "Story-focused",
        "description": "Keep historical prompts visible, but frame them as story choices rather than blockers.",
        "tone": "recommended",
    },
    "relaxed": {
        "name": "Relaxed Decades",
        "description": "Track the history and family consequences while keeping restrictions informational.",
        "tone": "optional",
    },
}

ERA_TASKS = (
    ("rules", "Review the new era rules", "Confirm marriage, career, education, technology, and household restrictions."),
    ("rolls", "Refresh recurring rolls", "Rebuild aging, marriage, event, occult, and campaign obligations for the new era."),
    ("heirs", "Confirm succession and estates", "Review heirs, legitimacy, wills, dowries, debts, and valuable family property."),
    ("education", "Review education and apprenticeships", "Check every child and teen whose education changes in this era."),
    ("service", "Review military eligibility", "Check active wars, drafts, exemptions, injuries, and returning veterans."),
    ("migration", "Review household locations", "Record moves, evacuations, immigration, and the location inherited by newborns."),
    ("portraits", "Preserve the decade portrait", "Capture or assemble the household image used for the decade snapshot."),
    ("chronicle", "Close the prior era's chronicle", "Record major births, marriages, deaths, scandals, fortunes, and turning points."),
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


def _record_map(records: Iterable[Record]) -> dict[str, list[Record]]:
    grouped: dict[str, list[Record]] = defaultdict(list)
    for item in records:
        if not item.deleted:
            grouped[item.kind].append(item)
    return grouped


def _sim_age(sim: Record, save: ChronicleSave) -> int | None:
    birth = _int((sim.data or {}).get("birth_global_day"), _int(sim.global_day))
    return None if birth is None else max(0, save.global_day - birth)


def _partner_ids(relationships: list[Record]) -> set[str]:
    result = set()
    for item in relationships:
        data = item.data or {}
        status = _text(data.get("status") or "Active").casefold()
        kind = _text(data.get("type")).casefold()
        if status in {"ended", "divorced", "annulled", "widowed"}:
            continue
        if bool(data.get("legally_married")) or any(word in kind for word in ("marriage", "married", "spouse")):
            result.update((_text(data.get("partner1_id")), _text(data.get("partner2_id"))))
    result.discard("")
    return result


def _ancestor_ids(sim_id: str, sims_by_id: dict[str, Record], depth: int = 3) -> set[str]:
    found, frontier = set(), {sim_id}
    for _ in range(max(1, depth)):
        next_frontier = set()
        for current in frontier:
            sim = sims_by_id.get(current)
            if not sim:
                continue
            data = sim.data or {}
            parents = set(_text(value) for value in (data.get("parent_ids") or []) if value)
            parents.update(filter(None, (_text(data.get("mother_id")), _text(data.get("father_id")))))
            next_frontier.update(parents - found)
        found.update(next_frontier)
        frontier = next_frontier
    return found


def _kinship_warning(first: Record, second: Record, sims_by_id: dict[str, Record], depth: int) -> str:
    first_ancestors = _ancestor_ids(first.id, sims_by_id, depth)
    second_ancestors = _ancestor_ids(second.id, sims_by_id, depth)
    if second.id in first_ancestors or first.id in second_ancestors:
        return "direct ancestor"
    if first_ancestors & second_ancestors:
        return "shared ancestor"
    return ""


def era_checklist(grouped: dict[str, list[Record]], save: ChronicleSave) -> dict:
    current_year = advanced.year_for(save, save.global_day) or save.start_year
    decade = current_year - current_year % 10
    completions = {
        _text((item.data or {}).get("task_key"))
        for item in grouped.get("era_check", [])
        if _int((item.data or {}).get("decade")) == decade and bool((item.data or {}).get("completed"))
    }
    guidance = []
    for item in grouped.get("era_guidance", []) + grouped.get("era_rule", []):
        data = item.data or {}
        if not bool(data.get("active", True)):
            continue
        if _int(data.get("start_year"), -9999) <= current_year <= _int(data.get("end_year"), 9999):
            guidance.append(item)
    tasks = [
        {"key": key, "label": label, "detail": detail, "completed": key in completions}
        for key, label, detail in ERA_TASKS
    ]
    return {
        "year": current_year,
        "decade": decade,
        "tasks": tasks,
        "complete": sum(item["completed"] for item in tasks),
        "guidance": sorted(guidance, key=lambda item: (str((item.data or {}).get("category") or ""), item.label.casefold()))[:12],
    }


def estate_summary(grouped: dict[str, list[Record]], save: ChronicleSave) -> dict:
    sims = grouped.get("sim", [])
    households = grouped.get("household", [])
    plans = grouped.get("estate_plan", [])
    plan_by_household = {_text((item.data or {}).get("household_id")): item for item in plans}
    sim_by_id = {item.id: item for item in sims}
    rows = []
    for household in sorted(households, key=lambda item: item.label.casefold()):
        data = household.data or {}
        plan = plan_by_household.get(household.id)
        plan_data = plan.data if plan else {}
        head_id = _text(data.get("head_sim_id"))
        heir_id = _text(plan_data.get("heir_sim_id"))
        funds = data.get("last_game_funds", data.get("household_funds", data.get("funds")))
        rows.append({
            "household": household,
            "head": sim_by_id.get(head_id),
            "heir": sim_by_id.get(heir_id),
            "plan": plan,
            "funds": _int(funds),
            "warning": "Choose an heir" if not heir_id else "",
        })
    transactions = sorted(grouped.get("economy_entry", []), key=lambda item: (_int(item.global_day, 0), item.updated_at), reverse=True)
    totals = Counter()
    for item in transactions:
        data = item.data or {}
        amount = abs(_int(data.get("amount"), 0))
        totals[_text(data.get("entry_type") or "expense").casefold()] += amount
    return {"households": rows, "transactions": transactions[:20], "income": totals["income"], "expenses": totals["expense"], "plans": plans}


def education_summary(grouped: dict[str, list[Record]], save: ChronicleSave) -> dict:
    plans = grouped.get("education_plan", [])
    plan_by_sim = {_text((item.data or {}).get("sim_id")): item for item in plans if _text((item.data or {}).get("status") or "Active").casefold() not in {"completed", "ended"}}
    candidates = []
    for sim in grouped.get("sim", []):
        if not _living(sim, save):
            continue
        age = _sim_age(sim, save)
        stage = _text((sim.data or {}).get("game_age") or (sim.data or {}).get("life_stage") or (sim.data or {}).get("age_stage"))
        school = _text((sim.data or {}).get("game_school"))
        is_young = stage.casefold() in {"child", "teen"} or (age is not None and 24 <= age < 96)
        if is_young or school or sim.id in plan_by_sim:
            candidates.append({"sim": sim, "age": age, "stage": stage or "Age not reported", "school": school, "plan": plan_by_sim.get(sim.id)})
    candidates.sort(key=lambda row: (row["plan"] is not None, row["age"] if row["age"] is not None else 10**9, row["sim"].label.casefold()))
    return {"candidates": candidates, "plans": sorted(plans, key=lambda item: item.label.casefold())}


def reputation_summary(grouped: dict[str, list[Record]]) -> dict:
    sims = grouped.get("sim", [])
    sim_by_id = {item.id: item for item in sims}
    totals = Counter()
    events = sorted(grouped.get("reputation_event", []), key=lambda item: (_int(item.global_day, 0), item.updated_at), reverse=True)
    for item in events:
        totals[_text((item.data or {}).get("sim_id"))] += _int((item.data or {}).get("impact"), 0)
    ranking = sorted(
        ({"sim": sim_by_id[sim_id], "score": score} for sim_id, score in totals.items() if sim_id in sim_by_id),
        key=lambda row: (row["score"], row["sim"].label.casefold()), reverse=True,
    )
    detected = []
    for sim in sims:
        for rel in (sim.data or {}).get("game_relationships") or []:
            if not isinstance(rel, dict):
                continue
            for signal in rel.get("scandal_signals") or []:
                if isinstance(signal, dict):
                    detected.append({"sim": sim, "relationship": rel, "signal": signal})
    return {"events": events[:24], "ranking": ranking, "detected": detected[:24]}


def military_summary(grouped: dict[str, list[Record]], save: ChronicleSave) -> dict:
    sims_by_id = {item.id: item for item in grouped.get("sim", [])}
    war_words = ("war", "battle", "campaign", "invasion", "crusade", "draft", "military")
    active_events = []
    for item in grouped.get("event", []) + grouped.get("campaign", []):
        data = item.data or {}
        text = f"{item.label} {data.get('scope', '')} {data.get('notes', '')}".casefold()
        start = _int(data.get("start_global_day"), _int(item.global_day, -10**9))
        end = _int(data.get("end_global_day"), start)
        if any(word in text for word in war_words) and start <= save.global_day <= (end if end is not None else save.global_day):
            active_events.append(item)
    services = []
    for item in grouped.get("service", []):
        data = item.data or {}
        services.append({"record": item, "sim": sims_by_id.get(_text(data.get("sim_id"))), "status": _text(data.get("status") or "Serving")})
    services.sort(key=lambda row: (_int(row["record"].global_day, 0), row["record"].label.casefold()), reverse=True)
    return {"active_events": active_events, "services": services}


def migration_summary(grouped: dict[str, list[Record]], save: ChronicleSave) -> dict:
    sims_by_id = {item.id: item for item in grouped.get("sim", [])}
    plans = []
    for item in grouped.get("migration_plan", []):
        data = item.data or {}
        plans.append({"record": item, "sim": sims_by_id.get(_text(data.get("sim_id"))), "due": _int(data.get("planned_global_day"), _int(item.global_day))})
    plans.sort(key=lambda row: (row["due"] if row["due"] is not None else 10**9, row["record"].label.casefold()))
    return {"plans": plans, "moves": list(reversed(advanced.migrations_for(sum(grouped.values(), []))))[:20]}


def cemetery_summary(grouped: dict[str, list[Record]], save: ChronicleSave) -> dict:
    memorials_by_sim = {_text((item.data or {}).get("sim_id")): item for item in grouped.get("memorial", [])}
    rows = []
    for sim in grouped.get("sim", []):
        data = sim.data or {}
        death_day = _int(data.get("death_global_day"))
        if death_day is None and not bool(data.get("game_was_dead")):
            continue
        rows.append({
            "sim": sim,
            "day": death_day,
            "year": advanced.year_for(save, death_day),
            "cause": _text(data.get("cause_of_death") or data.get("death_cause") or data.get("game_death_type") or "Cause not recorded"),
            "memorial": memorials_by_sim.get(sim.id),
        })
    rows.sort(key=lambda row: (row["day"] if row["day"] is not None else -1, row["sim"].label.casefold()), reverse=True)
    return {"people": rows}


def heirloom_summary(grouped: dict[str, list[Record]]) -> dict:
    sims_by_id = {item.id: item for item in grouped.get("sim", [])}
    tracked = grouped.get("heirloom", [])
    tracked_by_key = {
        _text((item.data or {}).get("definition_id") or (item.data or {}).get("item_name") or item.label).casefold(): item
        for item in tracked
    }
    tracked_keys = set(tracked_by_key)
    detected = []
    detected_keys = set()
    transfers = []
    heirloom_words = ("portrait", "painting", "photo", "urn", "jewel", "ring", "necklace", "medal", "trophy", "relic", "antique", "heirloom", "keepsake")
    for sim in sims_by_id.values():
        for item in (sim.data or {}).get("game_inventory_items") or []:
            if not isinstance(item, dict):
                continue
            name = _text(item.get("name") or "Unnamed inventory item")
            key = _text(item.get("definition_id") or name).casefold()
            detection_key = (str(item.get("scope") or "personal"), key)
            tracked_item = tracked_by_key.get(key)
            current_holder = _text((tracked_item.data or {}).get("current_holder_sim_id")) if tracked_item else ""
            likely_heirloom = any(word in name.casefold() for word in heirloom_words) or _int(item.get("value"), 0) >= 500
            if tracked_item and str(item.get("scope") or "personal") == "personal" and current_holder != sim.id:
                transfer_key = (tracked_item.id, sim.id)
                if transfer_key not in detected_keys:
                    detected_keys.add(transfer_key)
                    transfers.append({"record": tracked_item, "new_holder": sim, "old_holder": sims_by_id.get(current_holder), "item": item})
            elif key and key not in tracked_keys and detection_key not in detected_keys and likely_heirloom:
                detected_keys.add(detection_key)
                detected.append({"sim": sim, "item": item, "key": key})
    holder_rows = []
    for item in tracked:
        data = item.data or {}
        holder_rows.append({"record": item, "holder": sims_by_id.get(_text(data.get("current_holder_sim_id")))})
    return {"tracked": holder_rows, "detected": detected[:30], "transfers": transfers[:20], "scan_supported": any("game_inventory_scan_supported" in (sim.data or {}) for sim in sims_by_id.values())}


def marriage_market(grouped: dict[str, list[Record]], save: ChronicleSave) -> dict:
    sims = [item for item in grouped.get("sim", []) if _living(item, save)]
    sims_by_id = {item.id: item for item in sims}
    married = _partner_ids(grouped.get("relationship", []))
    min_age = _int((save.settings or {}).get("marriage_min_age_days"), 72)
    depth = max(1, min(8, _int((save.settings or {}).get("kinship_detection_generations"), 3)))
    eligible = [sim for sim in sims if sim.id not in married and (_sim_age(sim, save) or -1) >= min_age]
    pairs = []
    for first, second in combinations(eligible, 2):
        kinship = _kinship_warning(first, second, sims_by_id, depth)
        first_data, second_data = first.data or {}, second.data or {}
        first_class = _text(first_data.get("social_class")); second_class = _text(second_data.get("social_class"))
        class_match = bool(first_class and second_class and first_class.casefold() == second_class.casefold())
        first_home = _text(first_data.get("current_household_id")); second_home = _text(second_data.get("current_household_id"))
        score = 100 - abs((_sim_age(first, save) or 0) - (_sim_age(second, save) or 0)) + (12 if class_match else 0) - (1000 if kinship else 0) - (20 if first_home and first_home == second_home else 0)
        pairs.append({"first": first, "second": second, "score": score, "kinship": kinship, "class_match": class_match})
    pairs.sort(key=lambda row: (row["score"], row["first"].label.casefold(), row["second"].label.casefold()), reverse=True)
    return {"eligible": eligible, "pairs": pairs[:20], "minimum_age": min_age, "kinship_depth": depth}


def demographic_summary(grouped: dict[str, list[Record]], save: ChronicleSave) -> dict:
    sims = grouped.get("sim", [])
    living = [item for item in sims if _living(item, save)]
    households = grouped.get("household", [])
    recent_cutoff = max(1, save.global_day - max(1, save.days_per_year) * 10)
    births = [item for item in sims if (_int((item.data or {}).get("birth_global_day"), -1) or -1) >= recent_cutoff]
    sexes = Counter(_text((item.data or {}).get("sex") or (item.data or {}).get("gender") or "Unknown") for item in living)
    age_groups = Counter()
    for sim in living:
        stage = _text((sim.data or {}).get("game_age") or (sim.data or {}).get("life_stage") or "Unknown").title()
        age_groups[stage] += 1
    children = sum(label.casefold() in {"baby", "infant", "toddler", "child", "teen"} for label in (_text((item.data or {}).get("game_age") or (item.data or {}).get("life_stage")) for item in living))
    warnings = []
    if living and len(births) == 0:
        warnings.append(("Birth gap", "No births are recorded in the last ten challenge years."))
    if len(living) and len(households) and len(living) / len(households) > 7:
        warnings.append(("Crowded households", "The average living household has more than seven Sims."))
    if len(living) >= 4 and children == 0:
        warnings.append(("No next generation", "No living child or teen is currently recorded."))
    adult_like = len(living) - children
    if adult_like and len(births) > adult_like * 2:
        warnings.append(("Rapid growth", "Recent births substantially outnumber the present adult population."))
    return {"living": len(living), "households": len(households), "births": len(births), "children": children, "sexes": sexes.most_common(), "ages": age_groups.most_common(), "warnings": warnings}


def writing_prompt(grouped: dict[str, list[Record]], save: ChronicleSave) -> str:
    current_year = advanced.year_for(save, save.global_day) or save.start_year
    recent = sorted(
        (item for items in grouped.values() for item in items if item.global_day is not None and item.global_day <= save.global_day),
        key=lambda item: (_int(item.global_day, 0), item.updated_at), reverse=True,
    )[:6]
    if not recent:
        return f"Write a diary entry from {current_year} about ordinary household life, hopes, work, and uncertainty."
    facts = "; ".join(f"{item.label} ({item.kind.replace('_', ' ')})" for item in recent)
    return f"Write from the perspective of someone living in {current_year}. Use only these recorded facts: {facts}. Include one private reaction and one hope for the family."


def build(records: Iterable[Record], save: ChronicleSave) -> dict:
    grouped = _record_map(records)
    profile_key = _text((save.settings or {}).get("historical_accuracy_profile") or "story").casefold()
    if profile_key not in ACCURACY_PROFILES:
        profile_key = "story"
    return {
        "profile_key": profile_key,
        "profile": ACCURACY_PROFILES[profile_key],
        "profiles": ACCURACY_PROFILES,
        "era": era_checklist(grouped, save),
        "estate": estate_summary(grouped, save),
        "education": education_summary(grouped, save),
        "reputation": reputation_summary(grouped),
        "military": military_summary(grouped, save),
        "migration": migration_summary(grouped, save),
        "cemetery": cemetery_summary(grouped, save),
        "heirlooms": heirloom_summary(grouped),
        "marriage": marriage_market(grouped, save),
        "demographics": demographic_summary(grouped, save),
        "writing_prompt": writing_prompt(grouped, save),
        "sims": sorted(grouped.get("sim", []), key=lambda item: item.label.casefold()),
        "households": sorted(grouped.get("household", []), key=lambda item: item.label.casefold()),
        "letters": sorted(grouped.get("correspondence", []), key=lambda item: (_int(item.global_day, 0), item.updated_at), reverse=True)[:12],
    }


def compose_correspondence(kind: str, author: Record | None, recipient: Record | None, subject: str,
                           notes: str, save: ChronicleSave, records: Iterable[Record]) -> tuple[str, str]:
    year = advanced.year_for(save, save.global_day) or save.start_year
    author_name = author.label if author else "The household chronicler"
    recipient_name = recipient.label if recipient else "My dear reader"
    topic = subject.strip() or "News from the household"
    recent = sorted(
        (item for item in records if item.global_day is not None and item.global_day <= save.global_day and item.kind in {"event", "death", "pregnancy", "relationship", "reputation_event", "migration"}),
        key=lambda item: (_int(item.global_day, 0), item.updated_at), reverse=True,
    )[:3]
    facts = ", ".join(item.label for item in recent) or "the ordinary affairs of our household"
    detail = notes.strip() or "I hope this account preserves what memory alone may lose."
    if kind == "diary":
        body = f"{year}\n\nToday I must set down {topic.lower()}. Around us, {facts}. {detail}\n\n— {author_name}"
        label = f"Diary of {author_name}: {topic}"
    else:
        body = f"{year}\n\nDear {recipient_name},\n\nI write concerning {topic.lower()}. Our recent news includes {facts}. {detail}\n\nYours,\n{author_name}"
        label = f"Letter from {author_name}: {topic}"
    return label, body
