"""Compact, read-only whole-dynasty register. No snapshot restores or writes."""
from collections import Counter, defaultdict
from urllib.parse import quote
from . import infinite_decades as dynasty


def number(value):
    try:
        return int(value) if value is not None and value != "" else None
    except (ValueError, TypeError):
        return None


def context(save, people, branches):
    from .main import sim_birth_display
    if not save:
        return {"groups": [], "count": 0, "branch_count": 0}
    # Frozen records are archived for gameplay, not removed from family history.
    sims = {s.id: s for s in people if s.kind == "sim" and
            (not s.deleted or (s.data or {}).get("infinite_frozen"))}
    family = {b.id: b for b in branches}
    active_id = dynasty.state(save).get("active_branch_id")
    children = Counter()
    for sim in sims.values():
        d = sim.data or {}
        for parent in {d.get("mother_id"), d.get("father_id")} - {None, "", sim.id}:
            children[parent] += 1
    grouped = defaultdict(list)
    for sim in sims.values():
        d = sim.data or {}
        owner = d.get("infinite_branch_id")
        # Do not assign unowned historical people to the current branch by guess.
        if owner not in family:
            owner = active_id if not d.get("infinite_frozen") else None
        branch = family.get(owner)
        meta = dynasty.metadata(branch)
        observed = number(d.get("infinite_frozen_global_day")) if d.get("infinite_frozen") else None
        if observed is None:
            observed = save.global_day if owner == active_id else number(meta.get("current_global_day"))
        if observed is None:
            observed = save.global_day
        birth, death = number(d.get("birth_global_day")), number(d.get("death_global_day"))
        dead = bool(d.get("death_confirmed") or d.get("game_was_dead") or (death is not None and death <= observed))
        status = "Deceased" if dead else "Not yet born" if birth is not None and birth > observed else "Living"
        end = min(observed, death) if dead and death is not None else observed
        age = None if birth is None or birth > end else (end - birth) // max(1, save.days_per_year)
        # A death without a recorded date cannot establish an age at death.
        if dead and death is None:
            age = None
        raw_sex = str(d.get("sex") or "").strip()
        sex = {"gender.male": "Male", "gender.female": "Female", "male": "Male", "female": "Female"}.get(raw_sex.casefold(), raw_sex)
        preserved = bool(d.get("infinite_frozen"))
        grouped[owner].append({"sim": sim, "id": sim.id, "name": sim.label,
            "sex": sex or "—", "generation": d.get("generation"),
            "born": sim_birth_display(save, sim), "birth_day": birth,
            "birthplace": d.get("birthplace") or d.get("birth_country") or "—",
            "children": children[sim.id], "age": age, "status": status,
            "age_label": "at death" if dead else "at preserved date" if preserved else "now",
            "observed_year": dynasty.year(save, observed), "observed_day": observed,
            "profile": f"/p/infinite-decades?sim_id={quote(sim.id, safe='')}#dynasty-person" if preserved else f"/sims/{quote(sim.id, safe='')}",
            "search": f"{sim.label} {branch.label if branch else ''} {sex} {status} {d.get('birthplace') or ''}".casefold()})
    groups = []
    # Include empty completed/paused branches too. Starting world is a checkpoint,
    # not a second copy of every founder; show it only for unassigned starting Sims.
    for bid in set(family) | set(grouped):
        branch = family.get(bid)
        meta = dynasty.metadata(branch)
        members = sorted(grouped[bid], key=lambda row: (row["birth_day"] if row["birth_day"] is not None else 10**12, row["name"].casefold(), row["id"]))
        if meta.get("status") == "archive" and not members:
            continue
        point = save.global_day if bid == active_id else number(meta.get("current_global_day"))
        split_year = number(meta.get("split_year"))
        if split_year is None and number(meta.get("split_global_day")) is not None:
            split_year = dynasty.year(save, number(meta["split_global_day"]))
        parent = family.get(meta.get("parent_branch_id"))
        groups.append({"id": bid or "unassigned", "name": branch.label if branch else "No recorded branch",
            "split_year": split_year, "split_day": meta.get("split_global_day"),
            "status": meta.get("status", "unassigned"), "parent": parent.label if parent else "",
            "year": dynasty.year(save, point) if point is not None else None,
            "day": point, "members": members, "checkpoint": meta.get("game_save_name", ""),
            "search": f"{branch.label if branch else 'unassigned'} {split_year or ''} {meta.get('status', '')}".casefold()})
    groups.sort(key=lambda g: (g["split_year"] if g["split_year"] is not None else 10**12, g["name"].casefold(), g["id"]))
    return {"groups": groups, "count": len(sims), "branch_count": sum(g["status"] != "archive" and g["status"] != "unassigned" for g in groups)}
