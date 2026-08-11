
from __future__ import annotations
from collections import Counter
import era_rules

AGING_SECTION = "AGING & REQUIRED ROLLS"

def _ival(v, default=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default

def _name(row):
    return " ".join(x for x in [row["title"],row["first_name"],row["last_name"],row["suffix"]] if x).strip()

def _setting_int(con,key,default):
    r=con.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
    return _ival(r[0],default) if r else default

def _aging_rules(con):
    aging=[]
    for r in con.execute("""
        SELECT row_label,col_c,col_d,col_e FROM rules
        WHERE section=? AND row_label IS NOT NULL
        ORDER BY source_row
    """,(AGING_SECTION,)):
        offset=_ival(r["col_c"])
        qty=_ival(r["col_e"],0)
        rtype=(r["col_d"] or "").strip()
        if offset is None or qty<=0 or not rtype or rtype.lower()=="none":
            continue
        aging.append({"label":r["row_label"],"offset":offset,"roll_type":rtype,"qty":qty})
    return aging

def _maternal_stage(age_days):
    if age_days is None: return None
    if age_days < 52: return "Preteen"
    if age_days < 72: return "Teen"
    if age_days < 160: return "Young Adult"
    if age_days < 240: return "Adult"
    return "Elder"

def _applies(value,candidates,universal=("all","global","any","everywhere","everyone","all countries","all classes")):
    target=(value or "").strip().casefold()
    if not target or target in universal:
        return True
    for candidate in candidates:
        text=(candidate or "").strip().casefold()
        if text and (target in text or text in target):
            return True
    return False

def _existing_count(con, source_id, sim_id, due_gd, roll_type):
    return con.execute("""
        SELECT COUNT(*) FROM rolls
        WHERE COALESCE(source_id,'')=COALESCE(?, '')
          AND COALESCE(sim_id,'')=COALESCE(?, '')
          AND due_global_day=? AND roll_type=?
    """,(source_id,sim_id,due_gd,roll_type)).fetchone()[0]

def _next_roll_id(con):
    nums=[]
    for (rid,) in con.execute("SELECT roll_id FROM rolls WHERE roll_id LIKE ?",("ROLL-%",)):
        try: nums.append(int(str(rid).rsplit("-",1)[1]))
        except Exception: pass
    return f"ROLL-{max(nums,default=0)+1:04d}"

def _insert_missing(con, *, source_id, sim_id, sim_name, due_gd, roll_type, die, bad_results, qty, note):
    have=_existing_count(con,source_id,sim_id,due_gd,roll_type)
    added=0
    for _ in range(max(0,int(qty)-int(have))):
        rid=_next_roll_id(con)
        con.execute("""INSERT INTO rolls(
            roll_id,due_global_day,sim_id,sim_name,source_id,roll_type,die,bad_results,completed,notes
        ) VALUES(?,?,?,?,?,?,?,?,0,?)""",
        (rid,int(due_gd),sim_id,sim_name,source_id,roll_type,die,bad_results,note))
        added+=1
    return added

def _spec_for(con,due_gd,roll_type,species,start_year,days_per_year):
    year=start_year+(int(due_gd)-1)//days_per_year
    spec=era_rules.roll_spec(con,year,roll_type,species)
    if not spec:
        return {"year":year,"era_id":None,"era_name":None,"die":None,"bad_results":None,"rule_status":"No era table"}
    has_values=bool(spec.get("die") or spec.get("bad_results"))
    return {"year":year,**spec,"rule_status":"Ready" if has_values else "Missing roll type"}

def preview(con, current_gd):
    """Return all rule-driven obligations through/future based on known Sims/pregnancies."""
    era_rules.ensure_schema(con)
    aging=_aging_rules(con)
    tracking=1
    start_year=_setting_int(con,"start_year",1200)
    days_per_year=_setting_int(con,"days_per_year",4)
    obligations=[]

    sims=con.execute("""SELECT sim_id,title,first_name,last_name,suffix,birth_global_day,death_global_day,species_occult
                        FROM sims WHERE birth_global_day IS NOT NULL AND birth_global_day<=?""",(current_gd,)).fetchall()
    for s in sims:
        species=(s["species_occult"] or "Human").strip() or "Human"
        nm=_name(s)
        for rule in aging:
            due=int(s["birth_global_day"])+rule["offset"]
            if due<tracking: continue
            if s["death_global_day"] is not None and due>int(s["death_global_day"]): continue
            rt=rule["roll_type"]
            spec=_spec_for(con,due,rt,species,start_year,days_per_year)
            obligations.append({
                "source_id":s["sim_id"],"sim_id":s["sim_id"],"sim_name":nm,"species":species,
                "due_global_day":due,"roll_type":rt,"die":spec["die"],"bad_results":spec["bad_results"],
                "quantity":rule["qty"],"kind":"Life stage","year":spec["year"],"era_id":spec["era_id"],
                "era_name":spec["era_name"],"rule_status":spec["rule_status"]
            })

    pregnancies=con.execute("""SELECT p.*,s.title,s.first_name,s.last_name,s.suffix,
                                      s.birth_global_day AS mother_birth,s.species_occult AS mother_species
                               FROM pregnancies p LEFT JOIN sims s ON s.sim_id=p.mother_id
                               WHERE p.conception_global_day IS NOT NULL AND p.conception_global_day<=?
                                 AND p.due_global_day IS NOT NULL""",(current_gd,)).fetchall()
    for p in pregnancies:
        # Miscarriages do not create a maternal delivery roll.
        if (p["status"] or "").strip().lower() == "miscarriage":
            continue
        due=int(p["due_global_day"])
        if due<tracking or p["mother_birth"] is None or p["mother_id"] is None: continue
        stage=_maternal_stage(due-int(p["mother_birth"]))
        rt=f"Maternal — {stage}"
        species=(p["mother_species"] or "Human").strip() or "Human"
        spec=_spec_for(con,due,rt,species,start_year,days_per_year)
        qty=_ival(p["babies_delivered"],None)
        if qty is None or qty<=0: qty=_ival(p["babies_expected"],1) or 1
        obligations.append({
            "source_id":p["pregnancy_id"],"sim_id":p["mother_id"],
            "sim_name":p["mother_name"] or _name(p),"species":species,
            "due_global_day":due,"roll_type":rt,"die":spec["die"],"bad_results":spec["bad_results"],
            "quantity":qty,"kind":"Maternal","year":spec["year"],"era_id":spec["era_id"],
            "era_name":spec["era_name"],"rule_status":spec["rule_status"]
        })

    events=con.execute("""SELECT event_id,event_name,start_global_day,location,affected_class,notes
                          FROM events
                          WHERE COALESCE(active,1)=1 AND COALESCE(roll_required,0)=1
                            AND start_global_day IS NOT NULL""").fetchall()
    event_sims=con.execute("""SELECT s.sim_id,s.title,s.first_name,s.last_name,s.suffix,
                                     s.birth_global_day,s.death_global_day,s.birthplace,s.species_occult,
                                     h.location AS household_location,h.social_class
                              FROM sims s LEFT JOIN households h ON h.household_id=s.current_household_id""").fetchall()
    for event in events:
        due=int(event["start_global_day"])
        if due<tracking:
            continue
        for sim in event_sims:
            if sim["birth_global_day"] is not None and int(sim["birth_global_day"])>due:
                continue
            if sim["death_global_day"] is not None and int(sim["death_global_day"])<due:
                continue
            if not _applies(event["location"],[sim["household_location"],sim["birthplace"]]):
                continue
            if not _applies(event["affected_class"],[sim["social_class"]]):
                continue
            obligations.append({
                "source_id":event["event_id"],"sim_id":sim["sim_id"],"sim_name":_name(sim),
                "species":(sim["species_occult"] or "Human").strip() or "Human",
                "due_global_day":due,"roll_type":f"Event — {event['event_name'] or event['event_id']}",
                "die":None,"bad_results":None,"quantity":1,"kind":"Event",
                "year":start_year+(due-1)//days_per_year,"era_id":None,
                "era_name":None,"rule_status":"Event-defined",
            })
    return obligations

def sync_rolls(con,current_gd):
    obligations=preview(con,current_gd)
    added=0; by_kind=Counter(); skipped_missing_rules=0
    for o in obligations:
        if o["due_global_day"]>current_gd: continue
        # A due obligation with no era table is still created so the user never misses the roll;
        # die/results remain blank and the note tells them to configure that era.
        if o["kind"]=="Event":
            note=f"Auto-generated for {o['roll_type']}"
        else:
            note = f"Auto-generated from {o['era_name']}" if o["rule_status"]=="Ready" \
                   else f"Auto-generated obligation; {o['rule_status']} for {o['species']} in year {o['year']}"
        n=_insert_missing(
            con,source_id=o["source_id"],sim_id=o["sim_id"],sim_name=o["sim_name"],
            due_gd=o["due_global_day"],roll_type=o["roll_type"],die=o["die"],bad_results=o["bad_results"],
            qty=o["quantity"],note=note
        )
        if n:
            added+=n; by_kind[o["kind"]]+=n
            if o["rule_status"] not in ("Ready","Event-defined"): skipped_missing_rules+=n
    con.commit()
    return {"added":added,"by_kind":dict(by_kind),"considered":len(obligations),
            "missing_rule_rows":skipped_missing_rules}

def upcoming(con,current_gd,days_ahead=20):
    obligations=preview(con,current_gd)
    rows=[]
    for o in obligations:
        if current_gd < o["due_global_day"] <= current_gd+days_ahead:
            have=_existing_count(con,o["source_id"],o["sim_id"],o["due_global_day"],o["roll_type"])
            rows.append({**o,"already_scheduled":have,"missing":max(0,o["quantity"]-have)})
    return rows
