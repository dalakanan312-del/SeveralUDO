from __future__ import annotations

import secrets


DEFAULT_POOLS = {
    "Being Born": ["Birth complications", "Premature birth", "Congenital condition", "Stillbirth"],
    "Newborn": ["Neonatal infection", "Failure to thrive", "Birth complications", "Sudden infant death"],
    "Infant": ["Childhood illness", "Respiratory infection", "Fever", "Accident"],
    "Toddler": ["Childhood illness", "Drowning", "Household accident", "Fever"],
    "Child": ["Infectious disease", "Accident", "Drowning", "Respiratory illness"],
    "Preteen": ["Infectious disease", "Accident", "Respiratory illness", "Fever"],
    "Teen": ["Infectious disease", "Accident", "Childbirth complications", "Violence"],
    "Young Adult": ["Infectious disease", "Childbirth complications", "Accident", "Violence"],
    "Adult": ["Infectious disease", "Heart disease", "Childbirth complications", "Accident", "Cancer"],
    "Elder": ["Old age", "Heart disease", "Stroke", "Respiratory illness", "Cancer"],
}


def ensure_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS death_cause_pools(
        death_group TEXT NOT NULL,cause TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(death_group,cause))""")
    for group,causes in DEFAULT_POOLS.items():
        for cause in causes:
            con.execute("INSERT INTO death_cause_pools(death_group,cause,active) VALUES(?,?,1) ON CONFLICT DO NOTHING",
                        (group,cause))
    con.execute("INSERT INTO settings(key,value) VALUES('automatic_death_causes','1') ON CONFLICT DO NOTHING")
    con.commit()


def enabled(con):
    row=con.execute("SELECT value FROM settings WHERE key='automatic_death_causes'").fetchone()
    return not row or str(row[0]).strip().casefold() not in {"0","false","off","no"}


def group_for_roll(roll_type):
    text=str(roll_type or "").strip().casefold()
    aliases=(
        ("being born","Being Born"),("newborn","Newborn"),("infant","Infant"),
        ("toddler","Toddler"),("preteen","Preteen"),("young adult","Young Adult"),
        ("child","Child"),("teen","Teen"),("adult","Adult"),("elder","Elder"),
    )
    return next((group for token,group in aliases if token in text),"Adult")


def choose(con,roll_type):
    if not enabled(con):
        return None
    group=group_for_roll(roll_type)
    causes=[row[0] for row in con.execute(
        "SELECT cause FROM death_cause_pools WHERE death_group=? AND COALESCE(active,1)=1 ORDER BY cause",(group,))]
    return causes[secrets.randbelow(len(causes))] if causes else None
