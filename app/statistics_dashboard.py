"""Read-only statistics over the canonical dynasty records, not checkpoint copies."""
from collections import Counter, defaultdict
from statistics import mean, median
from types import SimpleNamespace
from urllib.parse import quote

from sqlalchemy import or_, select

from . import birth_measurements, domain, dynasty_history, infinite_dynasty, insights, labor
from .models import Record

KINDS = {"sim", "household", "relationship", "pregnancy", "illness", "event", "death", "roll"}


def _copy(row):
    return SimpleNamespace(id=row.id, save_id=row.save_id, kind=row.kind, label=row.label,
        global_day=row.global_day, data=dict(row.data or {}), deleted=False,
        version=row.version, created_at=row.created_at, updated_at=row.updated_at)


def birth_summary(rows, save):
    weights, lengths, durations = [], [], []
    sources = Counter()
    manual = estimated = ongoing = interrupted = 0
    for row in rows:
        data = row.data or {}
        if row.kind == "sim":
            birth = insights.integer(data.get("birth_global_day"), row.global_day)
            if birth is not None and birth > insights.statistics_day(row, save):
                continue
            values = data.get("birth_measurements")
            if not isinstance(values, dict):
                continue
            measured = False
            for kind, target, key, divisor in (("weight", weights, "grams", 1000), ("length", lengths, "cm", 1)):
                entry = values.get(kind)
                if not isinstance(entry, dict):
                    continue
                try:
                    valid = birth_measurements.measurement(kind, entry.get("value"), entry.get("unit"))
                except (TypeError, ValueError):
                    continue
                if valid:
                    target.append(valid[key] / divisor)
                    measured = True
            if measured:
                sources[str(values.get("source") or "Unspecified")] += 1
        elif row.kind == "pregnancy":
            override = data.get("labor_duration_manual")
            state = data.get("labor_tracking") or {}
            if not isinstance(state, dict):
                state = {}
            minutes = insights.integer(override.get("minutes")) if isinstance(override, dict) else None
            if minutes is not None and 0 <= minutes <= 8760 * 60 + 59:
                manual += 1
                durations.append(minutes)
            elif state.get("status") == "ended":
                minutes = insights.integer(state.get("minutes"))
                if minutes is not None and 0 <= minutes <= 8760 * 60 + 59:
                    estimated += 1
                    durations.append(minutes)
            elif state.get("status") == "in_progress":
                ongoing += 1
            elif state.get("status") == "interrupted":
                interrupted += 1
    return {"weights": len(weights), "lengths": len(lengths),
        "weight_kg": round(mean(weights), 2) if weights else None,
        "length_cm": round(mean(lengths), 1) if lengths else None,
        "labor_count": len(durations),
        "labor_average": labor.duration_label(round(mean(durations))) if durations else None,
        "labor_median": labor.duration_label(round(median(durations))) if durations else None,
        "manual": manual, "estimated": estimated, "ongoing": ongoing, "interrupted": interrupted,
        "sources": sources.most_common()}


def context(session, save, params):
    state = infinite_dynasty.state(save)
    active = state.get("active_branch_id")
    # Select metadata only: visiting Statistics must not unpack every portrait-
    # bearing checkpoint or query journals / game reports for a large dynasty.
    branches = {}
    if state:
        for bid, label, meta in session.execute(select(Record.id, Record.label, Record.data["meta"]).where(
                Record.save_id == save.id, Record.kind == infinite_dynasty.KIND)):
            branches[bid] = {"id": bid, "label": label, "meta": meta or {}}
    conditions = [Record.save_id == save.id, Record.kind.in_(KINDS)]
    conditions.append(or_(Record.deleted.is_(False), Record.data["infinite_frozen"].as_boolean().is_(True))
                      if state else Record.deleted.is_(False))
    originals = list(session.scalars(select(Record).where(*conditions)))
    rows = [_copy(row) for row in originals if not (row.kind == "roll" and row.deleted and (row.data or {}).get("retired_reason"))]
    profiles = {r.id: ("/p/infinite-decades?sim_id=" + quote(r.id, safe="") + "#dynasty-person"
                     if r.data.get("infinite_frozen") else "/sims/" + quote(r.id, safe=""))
                for r in rows if r.kind == "sim"}
    hidden = {r.id for r in rows if r.kind == "event" and domain.event_is_ignored(r)}
    rows = [r for r in rows if r.id not in hidden and str(r.data.get("event_id") or "") not in hidden]
    if dynasty_history.spoiler_free(save):
        # Also strips future outcome fields. No changes to persisted records.
        rows = dynasty_history.history_records(save, rows)

    def owner(row):
        bid = row.data.get("infinite_branch_id")
        if bid in branches:
            return bid
        return active if active in branches and not row.data.get("infinite_frozen") else "unassigned"

    sim_owners = {r.id: owner(r) for r in rows if r.kind == "sim"}
    grouped = defaultdict(list)
    for row in rows:
        bid = owner(row)
        # A personal obligation follows its canonical person, including a
        # starting-world Sim subsequently assigned to a playable branch.
        person = row.data.get("mother_id") if row.kind == "pregnancy" else row.data.get("sim_id")
        if row.kind != "sim" and person in sim_owners:
            bid = sim_owners[person]
        meta = branches.get(bid, {}).get("meta", {})
        day = insights.integer(row.data.get("infinite_frozen_global_day")) if row.data.get("infinite_frozen") else None
        if day is None:
            day = save.global_day if bid == active else insights.integer(meta.get("current_global_day"), save.global_day)
        if dynasty_history.spoiler_free(save):
            day = min(day, save.global_day)
        row.statistics_day = day
        # Frozen is an archival flag, not deletion. Normalize it on this detached
        # projection so shared life/death helpers use the chosen observation day.
        row.data.pop("infinite_frozen", None)
        row.data.pop("infinite_frozen_global_day", None)
        grouped[bid].append(row)

    summaries = []
    for bid in set(branches) | set(grouped):
        branch = branches.get(bid, {})
        meta = branch.get("meta", {})
        members = grouped[bid]
        if meta.get("status") == "archive" and not any(r.kind == "sim" for r in members):
            continue
        if bid == "unassigned" and not members:
            continue
        day = save.global_day if bid == active else insights.integer(meta.get("current_global_day"), save.global_day)
        if dynasty_history.spoiler_free(save):
            day = min(day, save.global_day)
        stats = insights.statistics(members, save)
        summaries.append({"id": bid, "label": branch.get("label", "No recorded branch"),
            "status": "Paused" if bid == active and infinite_dynasty.frozen(save) else str(meta.get("status", "unassigned")).title(),
            "current": bid == active, "day": day, "year": insights.historical_year(save, day),
            "living": stats["living"], "deceased": stats["deceased"], "population": stats["population"],
            "pregnancies": stats["pregnancy"]["total"], "due": stats["rolls"]["pending_due"],
            "future": stats["future"]})
    summaries.sort(key=lambda b: (not b["current"], b["status"] in {"Archive", "Unassigned"}, b["label"].casefold(), b["id"]))
    scope = params.get("statistics_branch", "all") if state else "all"
    if scope == "active":
        scope = active
    if scope not in {b["id"] for b in summaries} | {"all"}:
        scope = "all"
    selected = rows if scope == "all" else list(grouped[scope])
    if scope != "all":
        homes = {r.data.get("current_household_id") for r in selected if r.kind == "sim"}
        present = {r.id for r in selected}
        selected.extend(r for r in rows if r.kind == "household" and r.id in homes and r.id not in present)
    selected_branch = next((b for b in summaries if b["id"] == scope), None)
    view_save = SimpleNamespace(global_day=selected_branch["day"] if selected_branch else save.global_day,
        start_year=save.start_year, days_per_year=save.days_per_year, settings=save.settings)
    stats = insights.statistics(selected, view_save)
    stats["birth_details"] = birth_summary(selected, view_save)
    stats["average_living_years"] = round(stats["average_living_age"] / max(1, save.days_per_year), 1) if stats["average_living_age"] is not None else None
    stats["average_death_years"] = round(stats["average_death_age"] / max(1, save.days_per_year), 1) if stats["average_death_age"] is not None else None
    stats["median_death_years"] = round(stats["median_death_age"] / max(1, save.days_per_year), 1) if stats["median_death_age"] is not None else None
    days = [r.statistics_day for r in selected if r.kind == "sim"]
    return {"statistics": stats, "statistics_scope": scope, "statistics_branches": summaries if state else [],
        "statistics_dynasty": bool(state), "statistics_scope_label": selected_branch["label"] if selected_branch else "All branches" if state else "This save",
        "statistics_mixed_dates": bool(state and scope == "all"), "statistics_days_per_year": save.days_per_year,
        "statistics_day_range": (min(days), max(days)) if days else None,
        "statistics_spoiler_free": dynasty_history.spoiler_free(save), "statistics_profiles": profiles}
