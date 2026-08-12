from __future__ import annotations

import pandas as pd


ERA_DDL = """CREATE TABLE IF NOT EXISTS era_guidance(
    rule_id TEXT PRIMARY KEY,title TEXT NOT NULL,category TEXT,start_year INTEGER,end_year INTEGER,
    location TEXT,rule_text TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,source TEXT,notes TEXT)"""
CAMPAIGN_DDL = """CREATE TABLE IF NOT EXISTS military_campaigns(
    campaign_id TEXT PRIMARY KEY,event_id TEXT,name TEXT NOT NULL,start_global_day INTEGER,end_global_day INTEGER,
    location TEXT,min_age_days INTEGER,max_age_days INTEGER,eligible_sexes TEXT,eligible_classes TEXT,
    active INTEGER NOT NULL DEFAULT 1,notes TEXT)"""
SERVICE_DDL = """CREATE TABLE IF NOT EXISTS military_service(
    service_id TEXT PRIMARY KEY,campaign_id TEXT,event_id TEXT,sim_id TEXT,sim_name TEXT,role TEXT,status TEXT,
    enlisted_global_day INTEGER,return_global_day INTEGER,outcome TEXT,injury TEXT,notes TEXT)"""


def ensure_schema(con):
    con.execute(ERA_DDL)
    con.execute(CAMPAIGN_DDL)
    con.execute(SERVICE_DDL)
    con.commit()


def sim_name(row):
    return " ".join(str(row.get(k) or "").strip() for k in ("title", "first_name", "last_name") if str(row.get(k) or "").strip())


def ancestor_depths(sim_id, by_id, limit=5):
    found = {}
    frontier = [(sim_id, 0)]
    while frontier:
        current, depth = frontier.pop(0)
        if depth >= limit or current not in by_id:
            continue
        row = by_id[current]
        for parent in (row.get("mother_id"), row.get("father_id")):
            if parent and (parent not in found or depth + 1 < found[parent]):
                found[parent] = depth + 1
                frontier.append((parent, depth + 1))
    return found


def kinship_warning(first_id, second_id, sims):
    if not first_id or not second_id:
        return ""
    if first_id == second_id:
        return "Same Sim"
    by_id = {str(r["sim_id"]): r for _, r in sims.iterrows()}
    a, b = ancestor_depths(str(first_id), by_id), ancestor_depths(str(second_id), by_id)
    if second_id in a or first_id in b:
        return "Direct ancestor / descendant"
    shared = set(a) & set(b)
    if not shared:
        return ""
    da, db = min((a[x], b[x]) for x in shared)
    if da == db == 1:
        return "Sibling or half-sibling"
    if (da, db) in ((1, 2), (2, 1)):
        return "Aunt/uncle and niece/nephew"
    if da == db == 2:
        return "First cousins"
    return "Shared close ancestor"


def succession_ranking(sims, root_id=None, system="Absolute primogeniture", require_legitimate=False):
    if sims.empty:
        return pd.DataFrame()
    frame = sims.copy()
    frame = frame[frame.death_global_day.isna()]
    if "include_in_tree" in frame:
        frame = frame[frame.include_in_tree.fillna(1) == 1]
    if require_legitimate and "legitimate" in frame:
        frame = frame[frame.legitimate.fillna(0) == 1]
    frame = frame[~frame.succession_override.fillna("").str.lower().str.contains("exclude|disinherit", regex=True)]
    if root_id:
        descendants = {str(root_id)}
        changed = True
        while changed:
            before = len(descendants)
            children = frame[frame.mother_id.fillna("").isin(descendants) | frame.father_id.fillna("").isin(descendants)]
            descendants.update(children.sim_id.astype(str))
            changed = len(descendants) != before
        frame = frame[frame.sim_id.astype(str).isin(descendants - {str(root_id)})]
    frame["priority"] = frame.succession_override.fillna("").str.lower().str.contains("heir|priority").map({True: 0, False: 1})
    frame["birth_sort"] = pd.to_numeric(frame.birth_global_day, errors="coerce").fillna(10**9)
    frame["sex_sort"] = 0
    if system == "Male-preference primogeniture":
        frame["sex_sort"] = (~frame.sex.fillna("").str.lower().str.startswith("m")).astype(int)
    elif system == "Female-preference primogeniture":
        frame["sex_sort"] = (~frame.sex.fillna("").str.lower().str.startswith("f")).astype(int)
    frame = frame.sort_values(["priority", "sex_sort", "birth_sort", "sim_id"]).copy()
    frame.insert(0, "rank", range(1, len(frame) + 1))
    frame["name"] = frame.apply(sim_name, axis=1)
    return frame


def eligible_for_campaign(sims, households, start_day, minimum_age, maximum_age, location="All", sexes="All", classes="All"):
    frame = sims.copy()
    frame = frame[frame.birth_global_day.notna() & (frame.birth_global_day <= start_day)]
    frame = frame[frame.death_global_day.isna() | (frame.death_global_day >= start_day)]
    frame["age_days"] = start_day - pd.to_numeric(frame.birth_global_day)
    frame = frame[(frame.age_days >= minimum_age) & (frame.age_days <= maximum_age)]
    hh = households[["household_id", "location", "social_class"]].rename(columns={"location": "household_location"}) if not households.empty else pd.DataFrame(columns=["household_id", "household_location", "social_class"])
    frame = frame.merge(hh, left_on="current_household_id", right_on="household_id", how="left")
    if location and location.lower() != "all":
        frame = frame[frame.household_location.fillna("").str.casefold() == location.casefold()]
    allowed_sexes = {x.strip().casefold() for x in sexes.split(",") if x.strip()}
    if allowed_sexes and "all" not in allowed_sexes:
        frame = frame[frame.sex.fillna("").str.casefold().isin(allowed_sexes)]
    allowed_classes = {x.strip().casefold() for x in classes.split(",") if x.strip()}
    if allowed_classes and "all" not in allowed_classes:
        frame = frame[frame.social_class.fillna("").str.casefold().isin(allowed_classes)]
    frame["name"] = frame.apply(sim_name, axis=1)
    return frame
