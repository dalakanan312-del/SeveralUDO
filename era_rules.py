
from __future__ import annotations

_ENSURED_SCHEMAS=set()

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

def roll_spec(con, year, roll_type, species='Human'):
    era=matching_era(con,year,species)
    if not era:
        return None
    val=con.execute("""SELECT die,bad_results,notes FROM roll_rule_values
                       WHERE era_id=? AND roll_type=?""",(era["era_id"],roll_type)).fetchone()
    if not val:
        return {"era_id":era["era_id"],"era_name":era["era_name"],"die":None,"bad_results":None,"notes":None}
    return {"era_id":era["era_id"],"era_name":era["era_name"],"die":val["die"],"bad_results":val["bad_results"],"notes":val["notes"]}

def next_era_id(con):
    nums=[]
    for (eid,) in con.execute("SELECT era_id FROM roll_rule_eras WHERE era_id LIKE ?",("ERA-%",)):
        try:
            nums.append(int(str(eid).rsplit("-",1)[1]))
        except Exception:
            pass
    return f"ERA-{max(nums,default=0)+1:04d}"
