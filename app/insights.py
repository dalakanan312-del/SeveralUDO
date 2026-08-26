from __future__ import annotations

from collections import Counter, defaultdict, deque
from statistics import mean, median

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import domain
from .models import ChronicleSave, Record
from .plant_catalog import REGIONS, region_for, rows as catalog_plant_rows


LIFE_STAGES = (
    ("Newborn", 0), ("Infant", 1), ("Toddler", 4), ("Child", 20),
    ("Preteen", 40), ("Teen", 52), ("Young Adult", 72),
    ("Adult", 160), ("Elder", 240),
)


def integer(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def current_year(save: ChronicleSave) -> int:
    return save.start_year + (save.global_day - 1) // max(1, save.days_per_year)


def historical_year(save: ChronicleSave, global_day) -> int | None:
    day = integer(global_day)
    return None if day is None else save.start_year + (day - 1) // max(1, save.days_per_year)


def life_stage(sim: Record, global_day: int) -> str:
    data = sim.data or {}
    birth = integer(data.get("birth_global_day", sim.global_day))
    if birth is None:
        raw = str(data.get("game_age_stage") or "").replace("Age.", "").replace("_", " ").title()
        return raw or "Unknown"
    death = integer(data.get("death_global_day"))
    age = max(0, min(global_day, death) - birth if death is not None else global_day - birth)
    result = LIFE_STAGES[0][0]
    for label, minimum in LIFE_STAGES:
        if age >= minimum:
            result = label
    return result


def sim_number(sim: Record) -> int:
    text = str((sim.data or {}).get("sim_number") or (sim.data or {}).get("legacy_id") or "")
    digits = "".join(ch for ch in text if ch.isdigit())
    return integer(digits, 0) or 0


def all_records(session: Session, save_id: str, *, include_deleted: bool = False) -> list[Record]:
    query = select(Record).where(Record.save_id == save_id)
    if not include_deleted:
        query = query.where(Record.deleted.is_(False))
    return list(session.scalars(query))


def relationship_is_partner(value: Record | dict) -> bool:
    """Return whether a relationship belongs in romantic/partner UI.

    Relationship records also store friendship and broad family connections.
    Treating every two-Sim record as a couple made parents, children and
    siblings appear as love interests in profiles and the family tree.
    """
    data = (value.data or {}) if isinstance(value, Record) else (value or {})
    relationship_type = str(data.get("type") or data.get("category") or "").casefold()
    tags = {str(tag or "").casefold() for tag in (data.get("relationship_tags") or [])}
    if bool(data.get("legally_married")):
        return True
    if any(marker in relationship_type for marker in (
        "marriage", "married", "spouse", "husband", "wife", "widow", "divorc",
        "engag", "fianc", "betroth",
    )):
        return True
    if "family" in tags or relationship_type in {"family", "relative", "kin"}:
        return False
    if "romantic" in tags:
        return True
    if any(marker in relationship_type for marker in (
        "romantic", "romance", "love interest",
        "lover", "sweetheart", "partnership", "partner", "couple",
    )):
        return True
    return False


def family_view(records: list[Record], focus_id: str | None, mode: str = "family", depth: int = 3) -> dict:
    sims = {item.id: item for item in records if item.kind == "sim" and not item.deleted and bool((item.data or {}).get("include_in_family_tree",True))}
    relationships = [item for item in records if item.kind == "relationship" and not item.deleted]
    if not sims:
        return {"focus": None, "levels": [], "edges": [], "sims": sims, "relationships": [],
                "roles": {}, "related": {}, "connection_counts": {}, "max_level_size": 1,
                "visible_count": 0, "canvas_width": 296, "relationship_groups": []}
    focus_id = focus_id if focus_id in sims else max(sims, key=lambda key: sim_number(sims[key]))
    children = defaultdict(set)
    parents = defaultdict(set)
    partners = defaultdict(set)
    for sim in sims.values():
        for parent in ((sim.data or {}).get("mother_id"), (sim.data or {}).get("father_id")):
            if parent in sims:
                parents[sim.id].add(parent); children[parent].add(sim.id)
    closed_relationships = {"ended", "divorced", "annulled", "separated", "widowed", "closed", "inactive"}
    relationship_by_pair = {}
    for rel in relationships:
        first, second = (rel.data or {}).get("partner1_id"), (rel.data or {}).get("partner2_id")
        if first in sims and second in sims and relationship_is_partner(rel):
            partners[first].add(second); partners[second].add(first)
            pair = tuple(sorted((first, second)))
            data = rel.data or {}
            status = str(data.get("status") or "Active").casefold()
            score = (
                status not in closed_relationships,
                bool(data.get("legally_married")) or "marriage" in str(data.get("type") or "").casefold(),
                integer(data.get("start_global_day", rel.global_day), -10**9),
            )
            previous = relationship_by_pair.get(pair)
            if previous is None or score > previous[0]:
                relationship_by_pair[pair] = (score, rel)
    if mode == "direct":
        focus_parents = parents[focus_id]
        focus_children = children[focus_id]
        focus_siblings = {
            sim_id for sim_id in sims if sim_id != focus_id and parents[sim_id].intersection(focus_parents)
        }
        focus_co_parents = set()
        for child_id in focus_children:
            focus_co_parents.update(parents[child_id] - {focus_id})
        distances = {focus_id: 0}
        distances.update({sim_id: -1 for sim_id in focus_parents})
        distances.update({sim_id: 0 for sim_id in partners[focus_id] | focus_siblings | focus_co_parents})
        distances.update({sim_id: 1 for sim_id in focus_children})
    else:
        distances = {focus_id: 0}
        queue = deque([focus_id])
        while queue:
            current = queue.popleft()
            if abs(distances[current]) >= depth:
                continue
            if mode in {"family", "ancestors"}:
                for parent in parents[current]:
                    if parent not in distances:
                        distances[parent] = distances[current] - 1; queue.append(parent)
            if mode in {"family", "descendants"}:
                for child in children[current]:
                    if child not in distances:
                        distances[child] = distances[current] + 1; queue.append(child)
            if mode == "family":
                for partner in partners[current]:
                    if partner not in distances:
                        distances[partner] = distances[current]; queue.append(partner)
    def stable_key(sim_id):
        item = sims[sim_id]
        return (integer((item.data or {}).get("birth_global_day"), 10**9), item.label.casefold())

    level_ids = {
        level: sorted((key for key, value in distances.items() if value == level), key=stable_key)
        for level in sorted(set(distances.values()))
    }

    # Keep partners adjacent, then order each outward generation beneath the
    # relatives that connect it to the focus. This removes most line crossings.
    def group_partners(ids: list[str]) -> list[str]:
        available = set(ids); grouped = []
        for sim_id in ids:
            if sim_id not in available:
                continue
            grouped.append(sim_id); available.remove(sim_id)
            for partner_id in sorted(partners[sim_id].intersection(available), key=stable_key):
                grouped.append(partner_id); available.remove(partner_id)
        return grouped

    if 0 in level_ids:
        zero = level_ids[0]
        focus_group = [focus_id] + sorted(partners[focus_id].intersection(zero), key=stable_key)
        others = [sim_id for sim_id in zero if sim_id != focus_id and sim_id not in partners[focus_id]]
        midpoint = len(others) // 2
        level_ids[0] = group_partners(others[:midpoint] + focus_group + others[midpoint:])
    for level in range(1, max(level_ids, default=0) + 1):
        if level not in level_ids:
            continue
        anchors = {sim_id:index for index, sim_id in enumerate(level_ids.get(level - 1, []))}
        level_ids[level] = group_partners(sorted(level_ids[level], key=lambda sim_id: (
            min((anchors[parent] for parent in parents[sim_id] if parent in anchors), default=10**9),
            stable_key(sim_id),
        )))
    for level in range(-1, min(level_ids, default=0) - 1, -1):
        if level not in level_ids:
            continue
        anchors = {sim_id:index for index, sim_id in enumerate(level_ids.get(level + 1, []))}
        level_ids[level] = group_partners(sorted(level_ids[level], key=lambda sim_id: (
            min((anchors[child] for child in children[sim_id] if child in anchors), default=10**9),
            stable_key(sim_id),
        )))

    focus_data = sims[focus_id].data or {}
    focus_parents = parents[focus_id]
    focus_children = children[focus_id]
    focus_siblings = {
        sim_id for sim_id in sims if sim_id != focus_id and parents[sim_id].intersection(focus_parents)
    }
    focus_co_parents = set()
    for child_id in focus_children:
        focus_co_parents.update(parents[child_id] - {focus_id})

    def sex_word(sim_id: str, female: str, male: str, neutral: str) -> str:
        sex = str((sims[sim_id].data or {}).get("sex") or "").casefold()
        if "female" in sex:
            return female
        if "male" in sex and "female" not in sex:
            return male
        return neutral

    roles = {}
    for sim_id, level in distances.items():
        if sim_id == focus_id:
            role = "Focus"
        elif sim_id in partners[focus_id]:
            rel = relationship_by_pair.get(tuple(sorted((focus_id, sim_id))))
            rel_data = (rel[1].data or {}) if rel else {}
            role = "Spouse" if bool(rel_data.get("legally_married")) or "marriage" in str(rel_data.get("type") or "").casefold() else str(rel_data.get("type") or "Partner")
        elif sim_id in focus_co_parents:
            role = "Co-parent"
        elif sim_id == focus_data.get("mother_id"):
            role = "Mother"
        elif sim_id == focus_data.get("father_id"):
            role = "Father"
        elif sim_id in focus_parents:
            role = "Parent"
        elif sim_id in focus_children:
            role = sex_word(sim_id, "Daughter", "Son", "Child")
        elif sim_id in focus_siblings:
            role = sex_word(sim_id, "Sister", "Brother", "Sibling")
        elif level < 0:
            distance = abs(level)
            role = "Grandparent" if distance == 2 else "Great-grandparent" if distance == 3 else f"{distance - 2}× great-grandparent" if distance > 3 else "Parent generation"
        elif level > 0:
            role = "Grandchild" if level == 2 else "Great-grandchild" if level == 3 else f"{level - 2}× great-grandchild" if level > 3 else "Child generation"
        else:
            role = "Same generation"
        roles[sim_id] = role

    levels = []
    for level in sorted(level_ids):
        members = [sims[sim_id] for sim_id in level_ids[level]]
        direct_label = {-1: "Parents", 0: "Focus & close family", 1: "Children"}.get(level)
        label = direct_label if mode == "direct" else "Focus generation" if level == 0 else f"{abs(level)} generation{'s' if abs(level) != 1 else ''} {'before' if level < 0 else 'after'}"
        levels.append({"level": level, "label": label, "members": members})
    edges = []
    for child_id, parent_ids in parents.items():
        for parent_id in parent_ids:
            if child_id in distances and parent_id in distances:
                child_data = sims[child_id].data or {}
                role = "Mother" if child_data.get("mother_id") == parent_id else "Father" if child_data.get("father_id") == parent_id else "Parent"
                edges.append({"from": parent_id, "to": child_id, "type": "parent", "role": role,
                              "label": f"{role}: {sims[parent_id].label} → {sims[child_id].label}"})
    for _, rel in relationship_by_pair.values():
        first, second = (rel.data or {}).get("partner1_id"), (rel.data or {}).get("partner2_id")
        if first in distances and second in distances:
            data = rel.data or {}; category = str(data.get("type") or "Partner")
            status = str(data.get("status") or "Active")
            active = status.casefold() not in closed_relationships
            edges.append({"from": first, "to": second, "type": "partner", "active": active,
                          "role": category, "label": f"{category}: {sims[first].label} ↔ {sims[second].label} · {status}"})
    visible = set(distances)
    related = {sim_id: sorted((parents[sim_id] | children[sim_id] | partners[sim_id]).intersection(visible)) for sim_id in visible}
    connection_counts = {sim_id: {"parents":len(parents[sim_id].intersection(visible)), "partners":len(partners[sim_id].intersection(visible)), "children":len(children[sim_id].intersection(visible))} for sim_id in visible}
    relationship_groups = [
        {"label": "Parents", "members": [sims[sim_id] for sim_id in sorted(focus_parents, key=stable_key)]},
        {"label": "Partners & spouses", "members": [sims[sim_id] for sim_id in sorted(partners[focus_id], key=stable_key)]},
        {"label": "Siblings", "members": [sims[sim_id] for sim_id in sorted(focus_siblings, key=stable_key)]},
        {"label": "Children", "members": [sims[sim_id] for sim_id in sorted(focus_children, key=stable_key)]},
    ]
    other_co_parents = [sims[sim_id] for sim_id in sorted(focus_co_parents - partners[focus_id], key=stable_key)]
    if other_co_parents:
        relationship_groups.append({"label": "Other co-parents", "members": other_co_parents})
    max_level_size = max((len(level["members"]) for level in levels), default=1)
    return {
        "focus": sims[focus_id], "levels": levels, "edges": edges, "sims": sims,
        "relationships": relationships, "roles": roles, "related": related,
        "connection_counts": connection_counts,
        "max_level_size": max_level_size, "visible_count": len(visible),
        "canvas_width": max_level_size * 234 + 90, "relationship_groups": relationship_groups,
    }


def statistics(records: list[Record], save: ChronicleSave) -> dict:
    current = save.global_day
    sims = [item for item in records if item.kind == "sim" and not item.deleted]
    sims_by_id = {item.id: item for item in sims}
    households = [item for item in records if item.kind == "household" and not item.deleted]
    relationships = [item for item in records if item.kind == "relationship" and not item.deleted]
    pregnancies = [item for item in records if item.kind == "pregnancy" and not item.deleted]
    illnesses = [item for item in records if item.kind == "illness" and not item.deleted]
    rolls = [item for item in records if item.kind == "roll" and not item.deleted]
    events = [item for item in records if item.kind == "event" and not item.deleted]

    living: list[Record] = []
    deceased: list[Record] = []
    future: list[Record] = []
    ages: list[int] = []
    death_ages: list[int] = []
    births = Counter(); deaths = Counter(); causes = Counter(); death_stages = Counter()
    generations = Counter(); stages = Counter(); sexes = Counter(); species = Counter()
    generation_survival = defaultdict(lambda: {"living": 0, "deceased": 0, "total": 0, "survival_rate": 0.0})
    children_by_parent: dict[str, set[str]] = defaultdict(set)
    household_population = defaultdict(lambda: {"living": 0, "deceased": 0, "total": 0})
    living_ages: list[tuple[int, str, str]] = []
    completed_lifespans: list[tuple[int, str, str]] = []
    challenge_births = challenge_deaths = 0
    missing_birth = missing_generation = missing_household = missing_death_cause = 0

    for sim in sims:
        data = sim.data or {}
        birth = integer(data.get("birth_global_day", sim.global_day))
        death = integer(data.get("death_global_day"))
        if birth is None:
            missing_birth += 1
        if data.get("generation") in (None, ""):
            missing_generation += 1
        if not data.get("current_household_id"):
            missing_household += 1
        if birth is not None and birth > current:
            future.append(sim)
            continue

        dead_now = death is not None and death <= current
        if dead_now:
            deceased.append(sim)
            if not str(data.get("cause_of_death") or "").strip():
                missing_death_cause += 1
            if birth is not None:
                lifespan = max(0, death - birth)
                death_ages.append(lifespan)
                completed_lifespans.append((lifespan, sim.label, sim.id))
                stage_at_death = LIFE_STAGES[0][0]
                for label, minimum in LIFE_STAGES:
                    if lifespan >= minimum:
                        stage_at_death = label
                death_stages[stage_at_death] += 1
            year = historical_year(save, death)
            if year is not None:
                deaths[year] += 1
            if 1 <= death <= current:
                challenge_deaths += 1
            causes[str(data.get("cause_of_death") or "Unknown").strip() or "Unknown"] += 1
        else:
            living.append(sim)
            if birth is not None:
                age = max(0, current - birth)
                ages.append(age)
                living_ages.append((age, sim.label, sim.id))

        if birth is not None:
            year = historical_year(save, birth)
            if year is not None:
                births[year] += 1
            if 1 <= birth <= current:
                challenge_births += 1
        generation_key = str(data.get("generation") if data.get("generation") not in (None, "") else "Unknown")
        generations[generation_key] += 1
        generation_survival[generation_key]["deceased" if dead_now else "living"] += 1
        generation_survival[generation_key]["total"] += 1
        sexes[str(data.get("sex") or "Unspecified")] += 1
        stages[life_stage(sim, current)] += 1
        occult_types = data.get("game_occult_types")
        if isinstance(occult_types, (list, tuple, set)) and occult_types:
            species_label = " / ".join(str(value) for value in occult_types if value)
        else:
            species_label = str(data.get("species_occult") or data.get("species") or "Human")
        species[species_label or "Human"] += 1
        for parent in (data.get("mother_id"), data.get("father_id")):
            if parent:
                children_by_parent[str(parent)].add(sim.id)
        household_id = str(data.get("current_household_id") or "")
        if household_id:
            household_population[household_id]["deceased" if dead_now else "living"] += 1
            household_population[household_id]["total"] += 1

    for values in generation_survival.values():
        values["survival_rate"] = round(values["living"] * 100 / values["total"], 1) if values["total"] else 0.0

    adulthood = 72
    survived = died_young = pending = 0
    for child in sims:
        data = child.data or {}
        birth = integer(data.get("birth_global_day", child.global_day))
        death = integer(data.get("death_global_day"))
        if birth is None or birth > current:
            continue
        if death is not None and death < birth + adulthood:
            died_young += 1
        elif current < birth + adulthood and death is None:
            pending += 1
        else:
            survived += 1

    closed_relationship_statuses = {"ended", "divorced", "annulled", "separated", "widowed", "closed", "inactive"}
    relationship_types = Counter(); relationship_statuses = Counter(); marriage_years = Counter()
    marriage_durations: list[int] = []; active_marriages = ended_marriages = 0
    for relationship in relationships:
        data = relationship.data or {}
        relationship_type = str(data.get("type") or "Relationship")
        status = str(data.get("status") or "Active")
        folded_status = status.casefold()
        relationship_types[relationship_type] += 1
        relationship_statuses[status] += 1
        married = bool(data.get("legally_married")) or "marriage" in relationship_type.casefold() or "married" in folded_status
        if not married:
            continue
        if folded_status in closed_relationship_statuses:
            ended_marriages += 1
        else:
            active_marriages += 1
        start = integer(data.get("start_global_day", relationship.global_day))
        end = integer(data.get("end_global_day"))
        if start is not None:
            year = historical_year(save, start)
            if year is not None:
                marriage_years[year] += 1
            marriage_durations.append(max(0, min(end, current) - start) if end is not None else max(0, current - start))

    pregnancy_statuses = Counter(); pregnancy_years = Counter(); pregnancy_by_mother = Counter()
    active_pregnancies = delivered_pregnancies = losses = expected_babies = delivered_babies = multiple_births = 0
    closed_pregnancy_statuses = {"delivered", "miscarriage", "stillbirth", "cancelled", "canceled", "ended", "closed", "complete"}
    for pregnancy in pregnancies:
        data = pregnancy.data or {}
        status = str(data.get("status") or "Active")
        folded_status = status.casefold()
        pregnancy_statuses[status] += 1
        active_pregnancies += folded_status not in closed_pregnancy_statuses
        delivered_pregnancies += folded_status in {"delivered", "complete"}
        losses += folded_status in {"miscarriage", "stillbirth"}
        expected = max(0, integer(data.get("babies_expected"), 0) or 0)
        delivered_count = max(0, integer(data.get("babies_delivered"), 0) or 0)
        expected_babies += expected
        delivered_babies += delivered_count
        multiple_births += max(expected, delivered_count) > 1
        if data.get("mother_id"):
            pregnancy_by_mother[str(data["mother_id"])] += 1
        outcome_day = integer(data.get("end_global_day") or data.get("due_global_day") or pregnancy.global_day)
        year = historical_year(save, outcome_day)
        if year is not None:
            pregnancy_years[year] += 1

    completed_rolls: list[Record] = []
    passed = failed_count = 0
    roll_types = defaultdict(lambda: {"total": 0, "completed": 0, "passed": 0, "failed": 0})
    roll_dice = Counter(); roll_sources = Counter(); roll_years = Counter()
    pending_due = pending_future = event_rolls = 0
    missing_roll_die = missing_roll_results = 0
    for roll in rolls:
        data = roll.data or {}
        roll_type = str(data.get("roll_type") or "Unclassified roll")
        die = str(data.get("die") or "Not recorded")
        source = str(data.get("source_type") or data.get("source") or "Tracker")
        completed = bool(data.get("completed"))
        roll_types[roll_type]["total"] += 1
        roll_dice[die] += 1
        roll_sources[source] += 1
        if not data.get("die"):
            missing_roll_die += 1
        if data.get("event_id") or roll_type.casefold().startswith("event"):
            event_rolls += 1
        if not completed:
            if integer(roll.global_day, current) <= current:
                pending_due += 1
            else:
                pending_future += 1
            continue
        completed_rolls.append(roll)
        roll_types[roll_type]["completed"] += 1
        actual = integer(data.get("actual"))
        outcome = str(data.get("outcome") or "").casefold()
        did_fail = "fail" in outcome or "death" in outcome or (
            actual is not None and domain.failed(actual, str(data.get("bad_results") or ""))
        )
        if did_fail:
            failed_count += 1
            roll_types[roll_type]["failed"] += 1
        else:
            passed += 1
            roll_types[roll_type]["passed"] += 1
        if not str(data.get("outcome") or "").strip() and actual is None:
            missing_roll_results += 1
        completed_day = integer(data.get("completed_global_day"), roll.global_day)
        year = historical_year(save, completed_day)
        if year is not None:
            roll_years[year] += 1

    top_roll_types = []
    for label, values in sorted(roll_types.items(), key=lambda pair: (pair[1]["total"], pair[0]), reverse=True)[:15]:
        values = dict(values)
        values["completion_rate"] = round(values["completed"] * 100 / values["total"], 1) if values["total"] else 0.0
        values["failure_rate"] = round(values["failed"] * 100 / values["completed"], 1) if values["completed"] else 0.0
        top_roll_types.append((label, values))

    event_states = Counter(); event_categories = Counter(); event_locations = Counter(); event_roll_required = 0
    for event in events:
        data = event.data or {}
        start = integer(data.get("start_global_day", event.global_day))
        end = integer(data.get("end_global_day", start), start)
        state = "Upcoming" if start is not None and start > current else "Past" if end is not None and end < current else "Active"
        event_states[state] += 1
        event_categories[str(data.get("category") or data.get("event_type") or "Other")] += 1
        event_locations[str(data.get("location") or data.get("country") or "All locations")] += 1
        event_roll_required += bool(data.get("roll_required"))

    illness_summary = illness_statistics(records, save)
    active_illness_statuses = {"active", "chronic", "improving", "worsening"}
    illness_severities = Counter(); active_contagious = 0
    for illness in illnesses:
        data = illness.data or {}
        if str(data.get("status") or "Active").casefold() in active_illness_statuses:
            illness_severities[str(data.get("severity") or "Unspecified")] += 1
            active_contagious += bool(data.get("contagious"))

    household_sizes = []
    for home in households:
        values = household_population[home.id]
        household_sizes.append((home.label, values["living"], values["total"], home.id))
    assigned_living = sum(values["living"] for values in household_population.values())
    unassigned_living = max(0, len(living) - assigned_living)
    average_household_size = round(assigned_living / len(households), 1) if households else None

    family_sizes = sorted(
        ((len(children), sims_by_id[parent].label if parent in sims_by_id else "Unknown", parent) for parent, children in children_by_parent.items()),
        reverse=True,
    )
    mother_pregnancy_leaders = sorted(
        ((count, sims_by_id[mother].label if mother in sims_by_id else "Unknown", mother) for mother, count in pregnancy_by_mother.items()),
        reverse=True,
    )

    illness_years = dict(illness_summary["episodes_by_year"])
    activity_years = sorted(
        set(births)
        | set(deaths)
        | set(marriage_years)
        | set(pregnancy_years)
        | set(illness_years)
        | set(roll_years)
    )
    yearly_activity = [{
        "year": year,
        "births": births[year], "deaths": deaths[year], "net": births[year] - deaths[year],
        "marriages": marriage_years[year], "pregnancies": pregnancy_years[year],
        "illnesses": illness_years.get(year, 0), "rolls": roll_years[year],
    } for year in activity_years]

    current_year_value = current_year(save)
    current_year_activity = next((row for row in yearly_activity if row["year"] == current_year_value), {
        "year": current_year_value, "births": 0, "deaths": 0, "net": 0,
        "marriages": 0, "pregnancies": 0, "illnesses": 0, "rolls": 0,
    })
    child_total = survived + died_young + pending
    resolved_children = survived + died_young
    quality_items = [
        ("Sims missing birth dates", missing_birth),
        ("Sims missing generations", missing_generation),
        ("Sims without a current household", missing_household),
        ("Deaths missing a cause", missing_death_cause),
        ("Rolls missing a die", missing_roll_die),
        ("Completed rolls missing a result", missing_roll_results),
    ]
    return {
        "population": len(sims), "living": len(living), "deceased": len(deceased), "future": len(future),
        "current_population": len(living) + len(deceased), "households": len(households),
        "relationships": len(relationships), "pregnancies": len(pregnancies), "events": len(events),
        "current_year": current_year_value, "current_global_day": current,
        "survival_rate": round(len(living) * 100 / (len(living) + len(deceased)), 1) if living or deceased else 0.0,
        "challenge_births": challenge_births, "challenge_deaths": challenge_deaths,
        "challenge_net_growth": challenge_births - challenge_deaths,
        "average_living_age": round(mean(ages), 1) if ages else None,
        "average_death_age": round(mean(death_ages), 1) if death_ages else None,
        "median_death_age": round(median(death_ages), 1) if death_ages else None,
        "births": sorted(births.items()), "deaths": sorted(deaths.items()), "causes": causes.most_common(),
        "death_stages": death_stages.most_common(),
        "generations": sorted(generations.items(), key=lambda item: (item[0] == "Unknown", integer(item[0], 10**9))),
        "stages": stages.most_common(), "sexes": sexes.most_common(), "species": species.most_common(),
        "children": {
            "total": child_total, "survived": survived, "died_young": died_young, "pending": pending,
            "survival_rate": round(survived * 100 / resolved_children, 1) if resolved_children else None,
        },
        "rolls": {
            "total": len(rolls), "completed": len(completed_rolls), "pending": len(rolls) - len(completed_rolls),
            "pending_due": pending_due, "pending_future": pending_future, "passed": passed, "failed": failed_count,
            "completion_rate": round(len(completed_rolls) * 100 / len(rolls), 1) if rolls else 0.0,
            "failure_rate": round(failed_count * 100 / len(completed_rolls), 1) if completed_rolls else 0.0,
            "event_rolls": event_rolls, "types": top_roll_types, "dice": roll_dice.most_common(),
            "sources": roll_sources.most_common(),
        },
        "largest_families": [(name, count, parent_id) for count, name, parent_id in family_sizes[:10]],
        "pregnancy": {
            "total": len(pregnancies), "active": active_pregnancies, "delivered": delivered_pregnancies,
            "losses": losses, "expected_babies": expected_babies, "delivered_babies": delivered_babies,
            "multiple_births": multiple_births,
            "average_babies": round(delivered_babies / delivered_pregnancies, 2) if delivered_pregnancies else None,
            "mothers": len(pregnancy_by_mother),
            "leaders": [(name, count, mother_id) for count, name, mother_id in mother_pregnancy_leaders[:10]],
        },
        "pregnancy_statuses": pregnancy_statuses.most_common(),
        "relationship": {
            "active_marriages": active_marriages, "ended_marriages": ended_marriages,
            "average_marriage_duration": round(mean(marriage_durations), 1) if marriage_durations else None,
        },
        "relationship_types": relationship_types.most_common(), "relationship_statuses": relationship_statuses.most_common(),
        "household_sizes": sorted(household_sizes, key=lambda item: (item[1], item[2]), reverse=True)[:15],
        "household": {"assigned_living": assigned_living, "unassigned_living": unassigned_living, "average_size": average_household_size},
        "event_states": event_states.most_common(), "event_categories": event_categories.most_common(12),
        "event_locations": event_locations.most_common(12), "event_roll_required": event_roll_required,
        "illness": {**illness_summary, "active_contagious": active_contagious, "severities": illness_severities.most_common()},
        "generation_survival": sorted(generation_survival.items(), key=lambda item: (item[0] == "Unknown", integer(item[0], 10**9))),
        "yearly_activity": yearly_activity, "current_year_activity": current_year_activity,
        "data_quality": {"issues": sum(value for _, value in quality_items), "items": quality_items},
        "record_holders": {
            "oldest_living": max(living_ages, default=None),
            "longest_lived": max(completed_lifespans, default=None),
            "youngest_death": min(completed_lifespans, default=None),
            "most_children": family_sizes[0] if family_sizes else None,
            "most_pregnancies": mother_pregnancy_leaders[0] if mother_pregnancy_leaders else None,
        },
    }


def pregnancy_dashboard(records: list[Record], save: ChronicleSave) -> dict:
    pregnancies = [item for item in records if item.kind == "pregnancy" and not item.deleted]
    rolls = [item for item in records if item.kind == "roll" and not item.deleted and not bool((item.data or {}).get("completed"))]
    rows = {}
    active = delivered = losses = 0
    for pregnancy in pregnancies:
        data = pregnancy.data or {}
        status = str(data.get("status") or "Active")
        folded = status.casefold()
        active += folded not in {"delivered", "miscarriage", "stillbirth", "cancelled", "canceled", "ended", "closed", "complete"}
        delivered += folded in {"delivered", "complete"}
        losses += folded in {"miscarriage", "stillbirth"}
        conception = integer(data.get("conception_global_day"), save.global_day)
        due = integer(data.get("due_global_day", pregnancy.global_day), conception + max(1, save.pregnancy_days))
        duration = max(1, due - conception)
        calculated = max(0.0, min(100.0, (save.global_day - conception) * 100.0 / duration))
        reported = data.get("game_pregnancy_progress")
        try:
            progress = max(0.0, min(100.0, float(reported))) if reported is not None else calculated
        except (TypeError, ValueError):
            progress = calculated
        if progress >= 90: stage = "Birth approaching"
        elif progress >= 67: stage = "Late pregnancy"
        elif progress >= 34: stage = "Middle pregnancy"
        else: stage = "Early pregnancy"
        next_roll = min((roll for roll in rolls if str((roll.data or {}).get("pregnancy_id") or "") == pregnancy.id
                         and integer(roll.global_day, 10**9) >= save.global_day), key=lambda roll: integer(roll.global_day, 10**9), default=None)
        rows[pregnancy.id] = {
            "progress": round(progress), "stage": data.get("game_pregnancy_band") or stage,
            "days_remaining": max(0, due - save.global_day), "overdue": save.global_day > due and active,
            "next_roll": next_roll, "last_reported_day": data.get("last_progress_global_day"),
            "reported": reported is not None,
        }
    return {"total": len(pregnancies), "active": active, "delivered": delivered, "losses": losses, "rows": rows}


def illness_statistics(records: list[Record], save: ChronicleSave) -> dict:
    illnesses = [item for item in records if item.kind == "illness" and not item.deleted]
    active_statuses = {"active", "chronic", "improving", "worsening"}
    active = [item for item in illnesses if str((item.data or {}).get("status") or "active").casefold() in active_statuses]
    recovered = [item for item in illnesses if str((item.data or {}).get("status") or "").casefold() in {"recovered", "resolved", "ended", "closed"}]
    fatal = [item for item in illnesses if str((item.data or {}).get("status") or "").casefold() == "deceased"
             or "died" in str((item.data or {}).get("outcome") or "").casefold()]
    durations = []
    by_name = Counter(); by_year = Counter(); by_sim = Counter(); sources = Counter()
    for item in illnesses:
        data = item.data or {}; onset = integer(data.get("onset_global_day", item.global_day)); end = integer(data.get("end_global_day"))
        name = str(data.get("illness_name") or item.label or "Unknown")
        by_name[name] += 1
        if onset is not None: by_year[historical_year(save, onset)] += 1
        if data.get("sim_id"): by_sim[str(data["sim_id"])] += 1
        sources[str(data.get("provider") or data.get("source") or "Manual")] += 1
        if onset is not None and end is not None: durations.append(max(0, end - onset))
    sims = {item.id: item.label for item in records if item.kind == "sim" and not item.deleted}
    repeats = sorted(((sims.get(sim_id, "Unknown Sim"), count) for sim_id, count in by_sim.items() if count > 1), key=lambda pair: pair[1], reverse=True)
    return {
        "total": len(illnesses), "active": len(active), "recovered": len(recovered), "fatal": len(fatal),
        "average_duration": round(mean(durations), 1) if durations else None,
        "top_illnesses": by_name.most_common(8), "episodes_by_year": sorted(by_year.items()),
        "repeat_patients": repeats[:8], "sources": sources.most_common(),
    }


def household_census(records: list[Record], save: ChronicleSave) -> dict:
    households = [item for item in records if item.kind == "household" and not item.deleted]
    sims = [item for item in records if item.kind == "sim" and not item.deleted]
    pregnancies = [item for item in records if item.kind == "pregnancy" and not item.deleted
                   and str((item.data or {}).get("status") or "active").casefold() not in {"delivered", "miscarriage", "stillbirth", "cancelled", "canceled", "ended", "closed", "complete"}]
    illnesses = [item for item in records if item.kind == "illness" and not item.deleted
                 and str((item.data or {}).get("status") or "active").casefold() in {"active", "chronic", "improving", "worsening"}]
    finances = sorted((item for item in records if item.kind == "game_history" and (item.data or {}).get("category") == "finance"), key=lambda item: (item.global_day or 0, item.created_at), reverse=True)
    living = [sim for sim in sims if integer((sim.data or {}).get("death_global_day")) is None or int((sim.data or {}).get("death_global_day")) > save.global_day]
    rows = {}
    for household in households:
        members = [sim for sim in sims if (sim.data or {}).get("current_household_id") == household.id]
        living_members = [sim for sim in members if sim in living]
        stages = Counter(life_stage(sim, save.global_day) for sim in living_members)
        child_stages = {"Newborn", "Infant", "Toddler", "Child", "Preteen", "Teen"}
        member_ids = {sim.id for sim in living_members}
        money = [item for item in finances if (item.data or {}).get("tracker_household_id") == household.id]
        detected_balances = {integer((sim.data or {}).get("last_household_funds")) for sim in members
                             if integer((sim.data or {}).get("last_household_funds")) is not None}
        detected_balance = next(iter(detected_balances)) if len(detected_balances) == 1 else None
        rows[household.id] = {
            "members": members, "living": len(living_members), "deceased": len(members) - len(living_members),
            "children": sum(count for stage, count in stages.items() if stage in child_stages),
            "adults": sum(count for stage, count in stages.items() if stage not in child_stages),
            "pregnancies": sum((item.data or {}).get("mother_id") in member_ids for item in pregnancies),
            "illnesses": sum((item.data or {}).get("sim_id") in member_ids for item in illnesses),
            "stages": stages.most_common(), "balance": (money[0].data or {}).get("balance") if money else (household.data or {}).get("last_game_funds", detected_balance),
            "recent_finances": money[:8],
        }
    assigned = {sim.id for household in households for sim in rows[household.id]["members"]}
    unassigned = [sim for sim in living if sim.id not in assigned]
    return {
        "population": len(living), "households": len(households), "unassigned": len(unassigned),
        "pregnancies": len(pregnancies), "illnesses": len(illnesses), "rows": rows,
        "unassigned_sims": unassigned,
    }


def timeline(records: list[Record], save: ChronicleSave, *, kinds: set[str] | None = None, start_year: int | None = None, end_year: int | None = None, query: str = "") -> list[dict]:
    entries = []
    query = query.casefold().strip()
    for item in records:
        if item.deleted:
            continue
        data = item.data or {}
        candidates = []
        if item.kind == "sim":
            candidates.append((data.get("birth_global_day", item.global_day), "birth", f"Birth of {item.label}", data.get("birthplace") or "", data.get("historical_birth_date") or data.get("historical_birth_date_range"), data.get("birth_time")))
            if data.get("death_global_day") is not None:
                candidates.append((data.get("death_global_day"), "death", f"Death of {item.label}", data.get("cause_of_death") or "", data.get("historical_death_date") or data.get("historical_death_date_range"), data.get("death_time")))
        elif item.kind == "relationship":
            candidates.append((data.get("start_global_day", item.global_day), "relationship", item.label, data.get("type") or "Relationship began", data.get("historical_marriage_date") or data.get("historical_marriage_date_range"), data.get("marriage_time")))
            if data.get("end_global_day") is not None:
                candidates.append((data.get("end_global_day"), "relationship", f"End of {item.label}", data.get("status") or "Relationship ended", None, None))
        elif item.kind == "pregnancy":
            candidates.append((data.get("conception_global_day", item.global_day), "pregnancy", item.label, "Pregnancy began", None, None))
            if data.get("delivery_global_day") is not None:
                candidates.append((data.get("delivery_global_day"), "pregnancy", f"Outcome: {item.label}", data.get("outcome") or data.get("status") or "", data.get("historical_delivery_date"), data.get("delivery_time")))
        else:
            candidates.append((item.global_day, item.kind, item.label, data.get("notes") or data.get("outcome") or "", None, None))
        for day, kind, label, details, historical_date, event_time in candidates:
            day = integer(day)
            year = historical_year(save, day)
            if day is None or (kinds and kind not in kinds) or (start_year is not None and year < start_year) or (end_year is not None and year > end_year):
                continue
            if query and query not in f"{label} {details} {kind}".casefold():
                continue
            entries.append({"day": day, "year": year, "kind": kind, "label": label, "details": details, "historical_date":historical_date,"time":event_time,"record_id": item.id})
    return sorted(entries, key=lambda item: (item["day"], item["kind"], item["label"].casefold()), reverse=True)


def timeline_overview(records: list[Record], save: ChronicleSave) -> dict:
    sims=[item for item in records if item.kind=="sim" and not item.deleted]
    rows=[]
    for sim in sims:
        data=sim.data or {};birth=integer(data.get("birth_global_day",sim.global_day));death=integer(data.get("death_global_day"))
        if birth is None or birth>save.global_day: continue
        end=min(save.global_day,death) if death is not None else save.global_day
        rows.append({"sim":sim,"birth":birth,"end":max(birth,end),"death":death,"age":max(0,end-birth)})
    if not rows: return {"lifespans":[],"decades":[]}
    low=min(item["birth"] for item in rows);high=max(item["end"] for item in rows);span=max(1,high-low)
    for item in rows:
        item["left"]=round((item["birth"]-low)*100/span,2);item["width"]=max(1.0,round((item["end"]-item["birth"]+1)*100/span,2))
        item["birth_year"]=historical_year(save,item["birth"]);item["end_year"]=historical_year(save,item["end"])
    entries=timeline(records,save);decades=Counter((int(item["year"])//10)*10 for item in entries)
    return {"lifespans":sorted(rows,key=lambda item:(item["birth"],item["sim"].label))[:100],"decades":sorted(decades.items()),"low_year":historical_year(save,low),"high_year":historical_year(save,high)}


def health_report(records: list[Record], save: ChronicleSave) -> dict:
    active = [item for item in records if not item.deleted]
    sims = {item.id: item for item in active if item.kind == "sim"}
    rules = [item for item in active if item.kind == "roll_rule"]
    issues = []
    numbers = defaultdict(list)
    for sim in sims.values():
        number = str((sim.data or {}).get("sim_number") or "")
        if number:
            numbers[number.casefold()].append(sim)
        for field in ("mother_id", "father_id", "current_household_id"):
            target = (sim.data or {}).get(field)
            expected = "household" if field == "current_household_id" else "sim"
            if target and not any(item.id == target and item.kind == expected for item in active):
                issues.append({"level": "error", "area": "Sims", "message": f"{sim.label} has a missing {field.replace('_id','').replace('_',' ')} reference."})
    for number, matches in numbers.items():
        if len(matches) > 1:
            issues.append({"level": "error", "area": "Sims", "message": f"Duplicate Sim ID {number}: " + ", ".join(item.label for item in matches)})
    for roll in (item for item in active if item.kind == "roll"):
        data = roll.data or {}
        if not data.get("roll_type") or not data.get("die"):
            issues.append({"level": "warning", "area": "Rolls", "message": f"{roll.label} is missing its roll type or die."})
    for group in domain.duplicate_obligation_groups(active):
        if group["redundant"]:
            issues.append({"level": "warning", "area": "Rolls", "message": f"{len(group['matches'])} duplicate obligations: {group['label']}."})
        elif len(group["completed"]) > 1:
            issues.append({"level": "warning", "area": "Rolls", "message": f"{len(group['completed'])} completed copies are preserved for audit: {group['label']}."})
    labels = Counter(item.label.casefold() for item in rules)
    for label, count in labels.items():
        if count > 1:
            issues.append({"level": "warning", "area": "Rules", "message": f"Duplicate rule '{label}' ({count} copies)."})
    for required in ("being born", "newborn", "infant", "toddler", "child", "preteen", "teen", "young adult", "adult", "elder death-age rng"):
        if required not in labels:
            issues.append({"level": "error", "area": "Rules", "message": f"Missing lifecycle rule: {required.title()}."})
    event_count = sum(item.kind == "event" for item in active)
    duplicate_events = domain.duplicate_event_groups(active)
    if duplicate_events:
        redundant = sum(len(group["redundant"]) for group in duplicate_events)
        issues.append({"level": "warning", "area": "Events", "message": f"{len(duplicate_events)} duplicate event groups contain {redundant} redundant copies."})
    if event_count < 600:
        issues.append({"level": "warning", "area": "Events", "message": f"Only {event_count} historical events are installed; the recovered catalog contains 655."})
    return {"issues": issues, "errors": sum(item["level"] == "error" for item in issues), "warnings": sum(item["level"] == "warning" for item in issues), "records": len(active), "events": event_count, "rules": len(rules)}


def planting(save: ChronicleSave, location: str = "", region: str = "") -> dict:
    year = current_year(save)
    selected = region if region in REGIONS else region_for(location or (save.settings or {}).get("challenge_location"))
    rows = catalog_plant_rows(year, selected)
    return {"year": year, "region": selected, "regions": REGIONS, "rows": rows, "available": sum(item["Status"] == "Historically available" for item in rows)}
