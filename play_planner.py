"""Lightweight play rotation and family-planning helpers."""

from __future__ import annotations

import datetime

import autorolls


RULE_DEFAULTS = (
    ("side_pregnancy", -9999, 1299, "d20", "1-14: Schedule that many pregnancies; 15-20: No pregnancy", "Side-household pregnancy roll"),
    ("side_pregnancy", 1300, 1399, "d20", "1-13: Schedule that many pregnancies; 14-20: No pregnancy", "Side-household pregnancy roll"),
    ("side_pregnancy", 1400, 1499, "d20", "1-11: Schedule that many pregnancies; 12-15: One pregnancy; 16-20: No pregnancy", "Side-household pregnancy roll"),
    ("side_pregnancy", 1500, 1699, "d12", "1-10: Schedule that many pregnancies; 11-12: No pregnancy", "Side-household pregnancy roll"),
    ("side_pregnancy", 1700, 1799, "d10", "1-8: Schedule that many pregnancies; 9-10: No pregnancy", "Side-household pregnancy roll"),
    ("side_pregnancy", 1800, 1899, "d10", "1-8: Schedule that many pregnancies; 9-10: No pregnancy", "Side-household pregnancy roll"),
    ("side_pregnancy", 1900, 9999, "d6", "1-5: Schedule that many pregnancies; 6: No pregnancy", "Side-household pregnancy roll"),
    ("non_heir_marriage", -9999, 1299, "d12", "1: Does not marry; 2-12: May marry", "Non-heir marriage eligibility"),
    ("non_heir_marriage", 1300, 1499, "d10", "1: Does not marry; 2-10: May marry", "Non-heir marriage eligibility"),
    ("non_heir_marriage", 1500, 1699, "d8", "1: Does not marry; 2-8: May marry", "Non-heir marriage eligibility"),
    ("non_heir_marriage", 1700, 1799, "d8", "1: Does not marry; 2-8: May marry", "Non-heir marriage eligibility"),
    ("non_heir_marriage", 1800, 9999, "d6", "1: Does not marry; 2-6: May marry", "Non-heir marriage eligibility"),
)
_ENSURED_SCHEMAS = set()


def ensure_schema(con):
    schema_key = getattr(con, "schema_name", None)
    if schema_key and schema_key in _ENSURED_SCHEMAS:
        return
    con.execute("""CREATE TABLE IF NOT EXISTS play_rotation(
        rotation_id TEXT PRIMARY KEY,global_day INTEGER NOT NULL,sim_id TEXT,household_id TEXT,
        status TEXT NOT NULL DEFAULT 'Planned',played_global_day INTEGER,notes TEXT,created_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS sim_family_plans(
        sim_id TEXT PRIMARY KEY,target_children INTEGER,min_birth_spacing_days INTEGER,notes TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS planner_rules(
        rule_key TEXT NOT NULL,start_year INTEGER NOT NULL,end_year INTEGER NOT NULL,
        die TEXT,bad_results TEXT,notes TEXT,active INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(rule_key,start_year,end_year))""")
    con.executemany("""INSERT INTO planner_rules(rule_key,start_year,end_year,die,bad_results,notes,active)
        VALUES(?,?,?,?,?,?,1) ON CONFLICT DO NOTHING""", RULE_DEFAULTS)
    con.commit()
    if schema_key:
        _ENSURED_SCHEMAS.add(schema_key)


def _setting(con, key, default):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row and row[0] not in (None, "") else default


def _rule(con, key, year):
    return con.execute("""SELECT die,bad_results,notes FROM planner_rules
        WHERE rule_key=? AND active=1 AND start_year<=? AND end_year>=?
        ORDER BY start_year DESC LIMIT 1""", (key, year, year)).fetchone()


def _next_id(con, table, column, prefix):
    values = con.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE ?", (prefix + "-%",)).fetchall()
    numbers = []
    for row in values:
        try:
            numbers.append(int(str(row[0]).rsplit("-", 1)[1]))
        except (TypeError, ValueError):
            pass
    return f"{prefix}-{max(numbers, default=0) + 1:04d}"


def _insert_roll(con, source_id, sim_id, sim_name, due, roll_type, rule, note):
    exists = con.execute("SELECT 1 FROM rolls WHERE source_id=? AND roll_type=? LIMIT 1", (source_id, roll_type)).fetchone()
    if exists:
        return 0
    rid = _next_id(con, "rolls", "roll_id", "ROLL")
    con.execute("""INSERT INTO rolls(roll_id,due_global_day,sim_id,sim_name,source_id,roll_type,
        die,bad_results,completed,notes) VALUES(?,?,?,?,?,?,?,?,0,?)""",
        (rid, due, sim_id, sim_name, source_id, roll_type, rule[0], rule[1], note))
    return 1


def sync_scheduled_rolls(con, current_gd):
    """Create only the planner rolls that should exist by the current day."""
    ensure_schema(con)
    con.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"{con.schema_name}:play-planner-sync",))
    start_year = int(float(_setting(con, "start_year", 1200)))
    days_per_year = max(1, int(float(_setting(con, "days_per_year", 4))))
    year = start_year + (int(current_gd) - 1) // days_per_year
    year_start = (year - start_year) * days_per_year + 1
    added = 0

    pregnancy_rule = _rule(con, "side_pregnancy", year)
    main_household = str(_setting(con, "main_household_id", "") or "")
    if pregnancy_rule:
        households = con.execute("""SELECT h.household_id,h.household_name,h.head_sim_id,
            TRIM(COALESCE(s.title,'')||' '||COALESCE(s.first_name,'')||' '||COALESCE(s.last_name,'')||' '||COALESCE(s.suffix,'')) AS sim_name
            FROM households h LEFT JOIN sims s ON s.sim_id=h.head_sim_id
            WHERE COALESCE(h.active,1)=1 AND h.household_id<>?""", (main_household,)).fetchall()
        for household in households:
            source = f"PLANNER-PREG-{household['household_id']}-{year}"
            added += _insert_roll(con, source, household["head_sim_id"], household["sim_name"], year_start,
                                  "Side Household Pregnancy", pregnancy_rule,
                                  f"Auto-generated annual planner roll for {household['household_name'] or household['household_id']}")

    marriage_rule = _rule(con, "non_heir_marriage", year)
    heir_id = str(_setting(con, "current_heir_id", "") or "")
    marriage_age = int(float(_setting(con, "marriage_min_age_days", 72)))
    if marriage_rule:
        sims = con.execute("""SELECT s.sim_id,s.birth_global_day,
            TRIM(COALESCE(s.title,'')||' '||COALESCE(s.first_name,'')||' '||COALESCE(s.last_name,'')||' '||COALESCE(s.suffix,'')) AS sim_name
            FROM sims s WHERE s.birth_global_day IS NOT NULL AND s.birth_global_day+?<=?
              AND (s.death_global_day IS NULL OR s.death_global_day>=s.birth_global_day+?)
              AND s.sim_id<>? AND NOT EXISTS(SELECT 1 FROM relationships r
                WHERE (r.partner1_id=s.sim_id OR r.partner2_id=s.sim_id)
                  AND (LOWER(COALESCE(r.type,''))='marriage' OR COALESCE(r.legally_married,0)=1))""",
            (marriage_age, current_gd, marriage_age, heir_id)).fetchall()
        for sim in sims:
            due = int(sim["birth_global_day"]) + marriage_age
            due_year = start_year + (due - 1) // days_per_year
            due_rule = _rule(con, "non_heir_marriage", due_year) or marriage_rule
            source = f"PLANNER-MARRIAGE-{sim['sim_id']}"
            added += _insert_roll(con, source, sim["sim_id"], sim["sim_name"], due,
                                  "Non-Heir Marriage Eligibility", due_rule,
                                  "Auto-generated when this non-heir reached marriage eligibility")
    con.commit()
    return added


def rotation_recommendations(con, current_gd):
    return con.execute("""SELECT h.household_id,h.household_name,h.location,h.social_class,
        MAX(CASE WHEN r.status='Played' THEN COALESCE(r.played_global_day,r.global_day) END) AS last_played,
        COUNT(s.sim_id) AS living_members
        FROM households h LEFT JOIN play_rotation r ON r.household_id=h.household_id
        LEFT JOIN sims s ON s.current_household_id=h.household_id AND s.birth_global_day<=?
          AND (s.death_global_day IS NULL OR s.death_global_day>=?)
        WHERE COALESCE(h.active,1)=1 GROUP BY h.household_id,h.household_name,h.location,h.social_class
        ORDER BY last_played NULLS FIRST,h.household_name,h.household_id""", (current_gd, current_gd)).fetchall()


def record_rotation(con, global_day, household_id, sim_id=None, status="Played", notes=None):
    rid = _next_id(con, "play_rotation", "rotation_id", "PLAY")
    con.execute("""INSERT INTO play_rotation(rotation_id,global_day,sim_id,household_id,status,
        played_global_day,notes,created_at) VALUES(?,?,?,?,?,?,?,?)""",
        (rid, int(global_day), sim_id, household_id, status,
         int(global_day) if status == "Played" else None, notes,
         datetime.datetime.now(datetime.timezone.utc).isoformat()))
    con.commit()
    return rid


def child_survival(con, parent_id, adulthood_days=72):
    children = con.execute("""SELECT sim_id,birth_global_day,death_global_day FROM sims
        WHERE mother_id=? OR father_id=?""", (parent_id, parent_id)).fetchall()
    survived = died_young = pending = 0
    for child in children:
        if child["birth_global_day"] is None:
            pending += 1
            continue
        threshold = int(child["birth_global_day"]) + int(adulthood_days)
        if child["death_global_day"] is not None and int(child["death_global_day"]) < threshold:
            died_young += 1
        elif child["death_global_day"] is None and int(_setting(con, "current_global_day", 1)) < threshold:
            pending += 1
        else:
            survived += 1
    return {"children": len(children), "survived": survived, "died_young": died_young, "pending": pending}


def milestone_forecast(con, sim_id, current_gd):
    sim = con.execute("SELECT birth_global_day,death_global_day FROM sims WHERE sim_id=?", (sim_id,)).fetchone()
    if not sim or sim["birth_global_day"] is None:
        return []
    rows = []
    for rule in sorted(autorolls._aging_rules(con), key=lambda item: int(item["offset"])):
        due = int(sim["birth_global_day"]) + int(rule["offset"])
        state = "Completed" if due < current_gd else "Upcoming"
        if sim["death_global_day"] is not None and due > int(sim["death_global_day"]):
            state = "Not reached"
        rows.append({"milestone": rule["label"], "global_day": due, "status": state})
    marriage_age = int(float(_setting(con, "marriage_min_age_days", 72)))
    rows.append({"milestone": "Marriage eligibility", "global_day": int(sim["birth_global_day"]) + marriage_age,
                 "status": "Completed" if int(sim["birth_global_day"]) + marriage_age < current_gd else "Upcoming"})
    return sorted(rows, key=lambda item: item["global_day"])
