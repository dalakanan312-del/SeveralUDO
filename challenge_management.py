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

# Broad, location-neutral challenge defaults. They intentionally describe
# gameplay constraints rather than claiming that every region changed at the
# same moment; players can tailor or delete every entry for their setting.
DEFAULT_ERA_GUIDANCE = [
    ("DEFAULT-ERA-001", "Household-based livelihoods", "Careers & education", -9999, 1499, "Most work is tied to household, land, craft, trade, or local service. Formal schooling should be uncommon and shaped by class, faith, and location."),
    ("DEFAULT-ERA-002", "Marriage as a household alliance", "Marriage & family", -9999, 1499, "Treat marriage as a family, property, labor, or political alliance as well as a personal relationship. Adjust consent, age, and ceremony rules to the chosen culture."),
    ("DEFAULT-ERA-003", "Customary inheritance", "Inheritance", -9999, 1499, "Use the selected succession system, legitimacy, birth order, and local custom. Property commonly passes through family or household ties rather than an equal modern division."),
    ("DEFAULT-ERA-004", "Limited medical care", "Health", -9999, 1499, "Care is domestic or local. Give illness, childbirth, infection, and injury serious consequences; reserve advanced treatment for what the location and social class could plausibly access."),
    ("DEFAULT-ERA-005", "Levy and household service", "Military", -9999, 1499, "Warfare may draw eligible household members into levies, retinues, garrisons, transport, provisioning, or camp support. Eligibility should follow local class and sex rules."),
    ("DEFAULT-ERA-006", "Early modern households", "Marriage & family", 1500, 1699, "Household formation, inheritance, religion, trade, migration, and reputation remain central. Let class and location determine how much personal choice a couple has."),
    ("DEFAULT-ERA-007", "Print, trade, and expanding literacy", "Careers & education", 1500, 1699, "Literacy and specialized work are expanding but remain uneven. Schools, apprenticeships, clerical work, trade, and maritime careers should depend on place, sex, and class."),
    ("DEFAULT-ERA-008", "Gunpowder-era service", "Military", 1500, 1699, "Campaigns can require soldiers, sailors, militia, laborers, and suppliers. Track absence, injury, disease, desertion, capture, and death separately."),
    ("DEFAULT-ERA-009", "Agrarian and commercial life", "Economy", 1700, 1799, "Land and household production remain important while commerce and wage work grow. Use social class and location to limit occupations, goods, housing, and mobility."),
    ("DEFAULT-ERA-010", "Uneven access to education", "Careers & education", 1700, 1799, "Education and professional careers are expanding unevenly. Keep apprenticeships, domestic service, agriculture, military service, trade, and class restrictions prominent."),
    ("DEFAULT-ERA-011", "Period technology only", "Building & technology", 1700, 1799, "Use lighting, heating, transport, sanitation, communications, and household equipment plausible for the year and location. Treat newer conveniences as rare until locally available."),
    ("DEFAULT-ERA-012", "Industrial transition", "Economy", 1800, 1849, "Where industrialization has reached the challenge location, introduce factories, wage labor, urban crowding, and faster transport gradually; elsewhere retain local agrarian rules."),
    ("DEFAULT-ERA-013", "Changing family law", "Marriage & family", 1800, 1849, "Marriage, property, divorce, legitimacy, and custody remain strongly shaped by local law, religion, class, race, and sex. Record exceptions in era notes."),
    ("DEFAULT-ERA-014", "Industrial hazards", "Health", 1800, 1849, "Add risks from crowding, unsafe work, polluted water, epidemics, and limited surgery. Availability of trained care should depend on location and wealth."),
    ("DEFAULT-ERA-015", "Rail, steam, and telegraph transition", "Building & technology", 1850, 1913, "Introduce rail travel, steam power, telegraphy, photography, sanitation, electricity, and modern appliances only as they become available to the location and household class."),
    ("DEFAULT-ERA-016", "Expanding public life", "Careers & education", 1850, 1913, "Schooling, office work, professions, industry, and public institutions expand, but access remains unequal. Preserve local barriers and reform them at historically appropriate dates."),
    ("DEFAULT-ERA-017", "Mass-army conscription", "Military", 1850, 1913, "For applicable countries, campaigns may use mass conscription or national service. Filter the roster by age, sex, class, location, health, and exemptions."),
    ("DEFAULT-ERA-018", "Total-war disruption", "Military", 1914, 1945, "Major wars may affect soldiers and civilians through conscription, evacuation, rationing, displacement, injury, bereavement, and changed household roles. Apply only to involved locations."),
    ("DEFAULT-ERA-019", "Interwar and wartime households", "Economy", 1914, 1945, "Use event-specific rules for shortages, unemployment, rationing, mobilization, migration, and recovery. Household effects should reflect country and social class."),
    ("DEFAULT-ERA-020", "Modern medicine emerging", "Health", 1914, 1945, "Hospitals, public health, vaccines, antibiotics, and safer surgery expand unevenly. Treatment availability must still follow the exact year, place, and household access."),
    ("DEFAULT-ERA-021", "Postwar household change", "Marriage & family", 1946, 1969, "Household forms and expectations change quickly but unevenly. Let local laws and culture govern marriage, divorce, adoption, legitimacy, gender roles, and reproductive choices."),
    ("DEFAULT-ERA-022", "Consumer technology expansion", "Building & technology", 1946, 1969, "Expand household appliances, automobiles, television, telephones, and modern utilities according to local availability and class rather than granting everything immediately."),
    ("DEFAULT-ERA-023", "Rights and opportunities in transition", "Careers & education", 1970, 1999, "Education, employment, family law, and civil rights broaden in many places at different rates. Use local milestones and retain barriers until the appropriate year."),
    ("DEFAULT-ERA-024", "Late-century household technology", "Building & technology", 1970, 1999, "Introduce personal electronics, modern communications, improved medicine, and changing transport gradually by decade, location, and household means."),
    ("DEFAULT-ERA-025", "Contemporary local rules", "Other", 2000, 9999, "Use current-year rules for the challenge location. Continue tracking differences in law, culture, cost, healthcare, technology, and opportunity rather than assuming one universal standard."),
]


def ensure_schema(con):
    con.execute(ERA_DDL)
    con.execute(CAMPAIGN_DDL)
    con.execute(SERVICE_DDL)
    seeded = con.execute("SELECT value FROM settings WHERE key=?", ("era_guidance_seeded_v1",)).fetchone()
    if not seeded:
        for rule_id, title, category, start_year, end_year, text in DEFAULT_ERA_GUIDANCE:
            con.execute(
                """INSERT INTO era_guidance(rule_id,title,category,start_year,end_year,location,rule_text,active,source,notes)
                   VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(rule_id) DO NOTHING""",
                (rule_id, title, category, start_year, end_year, "All", text, 1, "Built-in starter guidance", "Editable location-neutral baseline"),
            )
        con.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("era_guidance_seeded_v1", "1"),
        )
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
