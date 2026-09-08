"""Read-only family graph. Stable record IDs, explicit parentage, no inferred romance."""
from collections import defaultdict
from urllib.parse import quote

CLOSED = {"ended", "divorced", "annulled", "separated", "widowed", "closed", "inactive"}


def number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def make_graph(records, save, photo_ids=(), branches=(), *, status, birth, death):
    from .insights import relationship_is_partner
    people = {r.id: r for r in records if r.kind == "sim" and
              (not r.deleted or (r.data or {}).get("infinite_frozen")) and
              (r.data or {}).get("include_in_family_tree", True)}
    branch_info = {r.id: {"id": r.id, "name": r.label, **(r.data or {}).get("meta", {})} for r in branches}
    homes = {r.id: r.label for r in records if r.kind == "household"}
    photo_ids = set(photo_ids)
    nodes, edges, warnings = [], {}, []
    missing = defaultdict(list)
    for sid, sim in people.items():
        data = sim.data or {}
        frozen = bool(data.get("infinite_frozen"))
        branch = branch_info.get(data.get("infinite_branch_id"), {})
        state = status(sim, save)
        nodes.append({"id": sid, "name": sim.label, "number": str(data.get("sim_number") or ""),
                      "birth": birth(save, sim), "death": death(save, sim) if data.get("death_global_day") is not None else "",
                      "birthDay": number(data.get("birth_global_day")), "status": state,
                      "dead": state == "Deceased", "frozen": frozen,
                      "frozenDay": data.get("infinite_frozen_global_day"),
                      "branch": branch.get("name", ""), "branchId": branch.get("id", ""),
                      "branchStatus": branch.get("status", ""),
                      "birthSurname": str(data.get("surname_at_birth") or data.get("maiden_name") or ""),
                      "marriedSurname": str(data.get("married_surname") or data.get("married_name") or ""),
                      "household": homes.get(data.get("current_household_id"), str(data.get("historical_household") or "")),
                      "portrait": f"/portraits/{quote(sid, safe='')}/current" if sid in photo_ids else "",
                      "profile": f"/p/infinite-decades?sim_id={quote(sid, safe='')}#dynasty-person" if frozen else f"/sims/{quote(sid, safe='')}",
                      "missingParents": missing[sid]})
        parents = [(data.get("mother_id"), "Mother", False), (data.get("father_id"), "Father", False),
                   (data.get("adoptive_mother_id"), "Adoptive mother", True), (data.get("adoptive_father_id"), "Adoptive father", True)]
        adopted = data.get("adoptive_parent_ids")
        if isinstance(adopted, list):
            parents.extend((p, "Adoptive parent", True) for p in adopted if isinstance(p, str))
        for parent, role, adoptive in parents:
            if not parent:
                continue
            if parent == sid:
                warnings.append(f"{sim.label} is recorded as their own parent. That connection is excluded.")
            elif parent not in people:
                missing[sid].append(role)
            else:
                key = (str(parent), sid, "parent")
                edges[key] = {"from": parent, "to": sid, "type": "parent", "role": role,
                              "adoptive": adoptive, "label": f"{role}: {people[parent].label} → {sim.label}"}
    # Collapse duplicate relationship records in the picture, not in saved data.
    # Preserve their history so remarriages between the same pair are not erased.
    pairs = defaultdict(list)
    for rel in records:
        if rel.kind != "relationship" or (rel.deleted and not (rel.data or {}).get("infinite_frozen")) or not relationship_is_partner(rel.data or {}):
            continue
        data = rel.data or {}
        a, b = data.get("partner1_id"), data.get("partner2_id")
        if a in people and b in people and a != b:
            pairs[tuple(sorted((a, b)))].append(rel)
    for (a, b), rows in sorted(pairs.items()):
        def score(r):
            d = r.data or {}
            return (str(d.get("status") or "Active").casefold() not in CLOSED,
                    bool(d.get("legally_married")) or "marriage" in str(d.get("type") or "").casefold(),
                    number(d.get("start_global_day", r.global_day)) or 0, r.id)
        selected = max(rows, key=score)
        data = selected.data or {}
        active = str(data.get("status") or "Active").casefold() not in CLOSED
        married = bool(data.get("legally_married")) or "marriage" in str(data.get("type") or "").casefold()
        role = ("Spouse" if active else "Former spouse") if married else (str(data.get("type") or "Partner") if active else "Former partner")
        history = [{"type": str((r.data or {}).get("type") or "Partner"),
                    "status": str((r.data or {}).get("status") or "Active"),
                    "start": (r.data or {}).get("start_global_day", r.global_day),
                    "end": (r.data or {}).get("end_global_day")} for r in sorted(rows, key=score, reverse=True)]
        edges[(a, b, "partner")] = {"from": a, "to": b, "type": "partner", "role": role,
            "active": active, "label": f"{role}: {people[a].label} ↔ {people[b].label}", "history": history}
    nodes.sort(key=lambda n: (n["birthDay"] if n["birthDay"] is not None else 10**12, n["name"].casefold(), n["id"]))
    return {"schema": 1, "saveId": save.id, "nodes": nodes, "edges": list(edges.values()),
            "warnings": warnings, "branches": [{k: b.get(k, "") for k in ("id", "name", "status")} for b in branch_info.values()],
            "infinite": bool(branch_info), "limit": 180}


def context_for(records, save, params, photo_ids, branches=()):
    # Import after app initialization, not while defining routes.
    from .main import sim_status, sim_birth_display, sim_death_display
    graph = make_graph(records, save, photo_ids, branches, status=sim_status, birth=sim_birth_display, death=sim_death_display)
    ids = {n["id"] for n in graph["nodes"]}
    focus = params.get("focus")
    if focus not in ids:
        focus = next((n["id"] for n in graph["nodes"] if not n["frozen"]), graph["nodes"][0]["id"] if graph["nodes"] else "")
    mode = params.get("mode", "direct")
    if mode not in {"direct", "family", "ancestors", "descendants"}: mode = "direct"
    return {"family_graph": graph, "family_options": {"focus": focus, "mode": mode,
        "depth": max(1, min(8, number(params.get("depth")) or 3)),
        "photos": params.get("photos", "1") != "0", "dates": params.get("dates", "1") != "0",
        "scope": "current" if params.get("scope") == "current" else "dynasty",
        "view": "list" if params.get("view") == "list" else "tree"}}
