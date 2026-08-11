
from __future__ import annotations

import re

_ENSURED_SCHEMAS=set()

DEFAULT_PRE1700_ROLLS = (
    ("Adult", "d20", "3 9 20"),
    ("Being Born", "d20", "5 10 15"),
    ("Child", "d20", "15 20"),
    ("Elder Death-Age RNG", "RNG", "60–120"),
    ("Infant", "d20", "17"),
    ("Maternal — Adult", "d20", "1 5"),
    ("Maternal — Elder", "d20", "1–18"),
    ("Maternal — Preteen", "d20", "1 5 10"),
    ("Maternal — Teen", "d20", "1 5"),
    ("Maternal — Young Adult", "d20", "1"),
    ("Newborn", "d20", "2 6"),
    ("Preteen", "d20", "13 18"),
    ("Teen", "d20", "7"),
    ("Toddler", "d20", "5 10 15"),
    ("Young Adult", "d20", "14 16"),
)

def ensure_schema(con):
    schema_key=getattr(con,"schema_name",None)
    if schema_key and schema_key in _ENSURED_SCHEMAS:
        return
    con.execute("""
    CREATE TABLE IF NOT EXISTS roll_rule_eras(
        era_id TEXT PRIMARY KEY,
        era_name TEXT NOT NULL,
        start_year INTEGER NOT NULL,
        end_year INTEGER NOT NULL,
        species TEXT NOT NULL DEFAULT 'Human',
        active INTEGER NOT NULL DEFAULT 1,
        notes TEXT
    )
    """)
    con.execute("""
    CREATE TABLE IF NOT EXISTS roll_rule_values(
        era_id TEXT NOT NULL,
        roll_type TEXT NOT NULL,
        die TEXT,
        bad_results TEXT,
        notes TEXT,
        PRIMARY KEY(era_id,roll_type),
        FOREIGN KEY(era_id) REFERENCES roll_rule_eras(era_id)
    )
    """)
    con.commit()
    seed_pre1700(con)
    if schema_key:
        _ENSURED_SCHEMAS.add(schema_key)

def seed_pre1700(con):
    exists=con.execute("SELECT 1 FROM roll_rule_eras WHERE era_id='ERA-HUMAN-PRE1700'").fetchone()
    if not exists:
        con.execute("""INSERT INTO roll_rule_eras(era_id,era_name,start_year,end_year,species,active,notes)
                       VALUES('ERA-HUMAN-PRE1700','Human — Pre-1700',-9999,1699,'Human',1,
                              'Seeded automatically from imported Rules Config')""")
    rows=con.execute("""SELECT row_label,col_b,col_c FROM rules
                        WHERE section='CURRENT HUMAN ROLL TABLE — PRE-1700'
                          AND row_label IS NOT NULL""").fetchall()
    for r in rows:
        rt=(r[0] or "").strip()
        if not rt or "CURRENT HUMAN ROLL TABLE" in rt:
            continue
        con.execute("""INSERT OR IGNORE INTO roll_rule_values(era_id,roll_type,die,bad_results,notes)
                       VALUES('ERA-HUMAN-PRE1700',?,?,?,?)""",
                    (rt,r[1],r[2],'Seeded from imported Rules Config'))
    for roll_type,die,bad_results in DEFAULT_PRE1700_ROLLS:
        con.execute("""INSERT OR IGNORE INTO roll_rule_values(era_id,roll_type,die,bad_results,notes)
                       VALUES('ERA-HUMAN-PRE1700',?,?,?,?)""",
                    (roll_type,die,bad_results,'Built-in historical default; editable'))
        con.execute("""UPDATE roll_rule_values
                       SET die=CASE WHEN die IS NULL OR trim(die)='' THEN ? ELSE die END,
                           bad_results=CASE WHEN bad_results IS NULL OR trim(bad_results)='' THEN ? ELSE bad_results END
                       WHERE era_id='ERA-HUMAN-PRE1700' AND roll_type=?""",
                    (die,bad_results,roll_type))
    con.commit()

def list_eras(con):
    ensure_schema(con)
    return con.execute("""SELECT era_id,era_name,start_year,end_year,species,active,notes
                          FROM roll_rule_eras ORDER BY species,start_year,end_year""").fetchall()

def matching_era(con, year, species='Human'):
    ensure_schema(con)
    s=(species or 'Human').strip()
    return con.execute("""SELECT * FROM roll_rule_eras
                          WHERE active=1
                            AND lower(species)=lower(?)
                            AND start_year<=? AND end_year>=?
                          ORDER BY (end_year-start_year) ASC,start_year DESC
                          LIMIT 1""",(s,int(year),int(year))).fetchone()

def _roll_key(value):
    """Make imported and generated roll labels comparable despite punctuation/word order."""
    words=re.findall(r"[a-z0-9]+",str(value or "").casefold())
    ignored={"roll","rolling","rng","required","check"}
    return tuple(sorted(word for word in words if word not in ignored))

def _match_roll_value(con, era_id, requested):
    rows=con.execute("""SELECT roll_type,die,bad_results,notes FROM roll_rule_values
                        WHERE era_id=?""",(era_id,)).fetchall()
    wanted=_roll_key(requested)
    if not wanted:
        return None
    exact=[row for row in rows if str(row["roll_type"] or "").strip().casefold()
           == str(requested or "").strip().casefold()]
    if exact:
        return exact[0]
    normalized=[row for row in rows if _roll_key(row["roll_type"])==wanted]
    if normalized:
        return normalized[0]
    # Imported sheets have used both "Being Born" and "Birth".
    aliases={"born":"birth"}
    wanted_set={aliases.get(word,word) for word in wanted}
    ranked=[]
    for row in rows:
        candidate={aliases.get(word,word) for word in _roll_key(row["roll_type"])}
        if not candidate:
            continue
        if candidate==wanted_set:
            ranked.append((1,row))
    return max(ranked,key=lambda item:item[0])[1] if ranked else None

def roll_spec(con, year, roll_type, species='Human'):
    era=matching_era(con,year,species)
    if not era:
        return None
    val=_match_roll_value(con,era["era_id"],roll_type)
    if not val:
        return {"era_id":era["era_id"],"era_name":era["era_name"],"matched_roll_type":None,
                "die":None,"bad_results":None,"notes":None}
    return {"era_id":era["era_id"],"era_name":era["era_name"],
            "matched_roll_type":val["roll_type"],"die":val["die"],
            "bad_results":val["bad_results"],"notes":val["notes"]}

def next_era_id(con):
    nums=[]
    for (eid,) in con.execute("SELECT era_id FROM roll_rule_eras WHERE era_id LIKE ?",("ERA-%",)):
        try:
            nums.append(int(str(eid).rsplit("-",1)[1]))
        except Exception:
            pass
    return f"ERA-{max(nums,default=0)+1:04d}"
