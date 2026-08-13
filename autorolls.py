
from __future__ import annotations
from collections import Counter
import era_rules

AGING_SECTION = "AGING & REQUIRED ROLLS"
DEFAULT_AGING_RULES = [
    {"label":"Being Born","offset":0,"roll_type":"Being Born","qty":1},
    {"label":"Newborn","offset":0,"roll_type":"Newborn","qty":1},
    {"label":"Infant","offset":1,"roll_type":"Infant","qty":1},
    {"label":"Toddler","offset":4,"roll_type":"Toddler","qty":1},
    {"label":"Child","offset":20,"roll_type":"Child","qty":1},
    {"label":"Preteen","offset":40,"roll_type":"Preteen","qty":1},
    {"label":"Teen","offset":52,"roll_type":"Teen","qty":1},
    {"label":"Young Adult","offset":72,"roll_type":"Young Adult","qty":1},
    {"label":"Adult","offset":160,"roll_type":"Adult","qty":1},
    {"label":"Elder","offset":240,"roll_type":"Elder Death-Age RNG","qty":1},
]
_BACKFILLED_SCHEMAS = set()
_DICE_REPAIRED_SCHEMAS = set()

def default_die(roll_type):
    text=(roll_type or "").strip().casefold()
    return "d100" if "elder" in text and ("death" in text or "age" in text) else "d20"

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
    return aging or [dict(rule) for rule in DEFAULT_AGING_RULES]

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

def _insert_missing(con, *, source_id, sim_id, sim_name, due_gd, roll_type, die, bad_results, qty, note,
                    existing_counts=None,id_state=None):
    key=(source_id or "",sim_id or "",int(due_gd),roll_type)
    have=existing_counts.get(key,0) if existing_counts is not None else _existing_count(con,source_id,sim_id,due_gd,roll_type)
    added=0
    for _ in range(max(0,int(qty)-int(have))):
        if id_state is None:
            rid=_next_roll_id(con)
        else:
            id_state[0]+=1; rid=f"ROLL-{id_state[0]:04d}"
        con.execute("""INSERT INTO rolls(
            roll_id,due_global_day,sim_id,sim_name,source_id,roll_type,die,bad_results,completed,notes
        ) VALUES(?,?,?,?,?,?,?,?,0,?)""",
        (rid,int(due_gd),sim_id,sim_name,source_id,roll_type,die,bad_results,note))
        added+=1
        if existing_counts is not None: existing_counts[key]=existing_counts.get(key,0)+1
    return added

def _spec_for(con,due_gd,roll_type,species,start_year,days_per_year):
    year=start_year+(int(due_gd)-1)//days_per_year
    spec=era_rules.roll_spec(con,year,roll_type,species)
    if not spec:
        return {"year":year,"era_id":None,"era_name":"Built-in defaults","die":default_die(roll_type),
                "bad_results":None,"rule_status":"Ready"}
    if not spec.get("die"):
        spec={**spec,"die":default_die(roll_type)}
    return {"year":year,**spec,"rule_status":"Ready"}

def repair_generated_roll_dice(con):
    """Refresh incomplete automatic rows from their era table; preserve completed/custom rolls."""
    schema_key=getattr(con,"schema_name",None)
    if schema_key and schema_key in _DICE_REPAIRED_SCHEMAS:
        return 0
    start_year=_setting_int(con,"start_year",1200)
    days_per_year=max(1,_setting_int(con,"days_per_year",4))
    rows=con.execute("""SELECT r.roll_id,r.due_global_day,r.roll_type,r.die,r.bad_results,
                               s.species_occult
                        FROM rolls r LEFT JOIN sims s ON s.sim_id=r.sim_id
                        WHERE COALESCE(r.completed,0)=0 AND r.notes LIKE ?""",
                     ("Auto-generated%",)).fetchall()
    changed=0
    for row in rows:
        roll_type=row["roll_type"] or ""
        if roll_type.casefold().startswith("event"):
            continue
        due=_ival(row["due_global_day"],1)
        year=start_year+(due-1)//days_per_year
        spec=era_rules.roll_spec(con,year,roll_type,row["species_occult"] or "Human")
        canonical=(spec or {}).get("matched_roll_type") or roll_type
        die=(spec or {}).get("die") or row["die"] or default_die(roll_type)
        bad=(spec or {}).get("bad_results")
        if bad is None:
            bad=row["bad_results"]
        if (canonical,die,bad)!=(roll_type,row["die"],row["bad_results"]):
            con.execute("UPDATE rolls SET roll_type=?,die=?,bad_results=? WHERE roll_id=?",
                        (canonical,die,bad,row["roll_id"]))
            changed+=1
    con.commit()
    if schema_key:
        _DICE_REPAIRED_SCHEMAS.add(schema_key)
    return changed

def preview(con, current_gd, due_from=None, due_to=None, event_due_from=None):
    """Return all rule-driven obligations through/future based on known Sims/pregnancies."""
    era_rules.ensure_schema(con)
    aging=_aging_rules(con)
    tracking=1
    start_year=_setting_int(con,"start_year",1200)
    days_per_year=_setting_int(con,"days_per_year",4)
    obligations=[]
    due_from=tracking if due_from is None else int(due_from)
    due_to=None if due_to is None else int(due_to)
    event_due_from=due_from if event_due_from is None else int(event_due_from)
    def in_window(due):
        return int(due)>=due_from and (due_to is None or int(due)<=due_to)
    def event_in_window(due):
        return int(due)>=event_due_from and (due_to is None or int(due)<=due_to)

    sims=con.execute("""SELECT sim_id,title,first_name,last_name,suffix,birth_global_day,death_global_day,species_occult
                        FROM sims WHERE birth_global_day IS NOT NULL AND birth_global_day<=?
                          AND (death_global_day IS NULL OR death_global_day>=?)""",
                     (current_gd,due_from)).fetchall()
    for s in sims:
        species=(s["species_occult"] or "Human").strip() or "Human"
        nm=_name(s)
        for rule in aging:
            due=int(s["birth_global_day"])+rule["offset"]
            if not in_window(due): continue
            if s["death_global_day"] is not None and due>int(s["death_global_day"]): continue
            rt=rule["roll_type"]
            spec=_spec_for(con,due,rt,species,start_year,days_per_year)
            rt=spec.get("matched_roll_type") or rt
            obligations.append({
                "source_id":s["sim_id"],"sim_id":s["sim_id"],"sim_name":nm,"species":species,
                "due_global_day":due,"roll_type":rt,"die":spec["die"],"bad_results":spec["bad_results"],
                "quantity":rule["qty"],"kind":"Life stage","year":spec["year"],"era_id":spec["era_id"],
                "era_name":spec["era_name"],"rule_status":spec["rule_status"]
            })

    pregnancies=con.execute("""SELECT p.*,s.title,s.first_name,s.last_name,s.suffix,
                                      s.birth_global_day AS mother_birth,s.death_global_day AS mother_death,
                                      s.species_occult AS mother_species
                               FROM pregnancies p LEFT JOIN sims s ON s.sim_id=p.mother_id
                               WHERE p.conception_global_day IS NOT NULL AND p.conception_global_day<=?
                                 AND p.due_global_day IS NOT NULL""",(current_gd,)).fetchall()
    for p in pregnancies:
        # Miscarriages do not create a maternal delivery roll.
        if (p["status"] or "").strip().lower() == "miscarriage":
            continue
        due=int(p["due_global_day"])
        if not in_window(due) or p["mother_birth"] is None or p["mother_id"] is None: continue
        if p["mother_death"] is not None and due>int(p["mother_death"]): continue
        stage=_maternal_stage(due-int(p["mother_birth"]))
        rt=f"Maternal — {stage}"
        species=(p["mother_species"] or "Human").strip() or "Human"
        spec=_spec_for(con,due,rt,species,start_year,days_per_year)
        rt=spec.get("matched_roll_type") or rt
        qty=_ival(p["babies_delivered"],None)
        if qty is None or qty<=0: qty=_ival(p["babies_expected"],1) or 1
        obligations.append({
            "source_id":p["pregnancy_id"],"sim_id":p["mother_id"],
            "sim_name":p["mother_name"] or _name(p),"species":species,
            "due_global_day":due,"roll_type":rt,"die":spec["die"],"bad_results":spec["bad_results"],
            "quantity":qty,"kind":"Maternal","year":spec["year"],"era_id":spec["era_id"],
            "era_name":spec["era_name"],"rule_status":spec["rule_status"]
        })

    events=con.execute("""SELECT event_id,event_name,start_global_day,scope,location,affected_class,notes
                          FROM events
                          WHERE COALESCE(active,1)=1 AND COALESCE(roll_required,0)=1
                            AND start_global_day IS NOT NULL""").fetchall()
    event_sims=con.execute("""SELECT s.sim_id,s.title,s.first_name,s.last_name,s.suffix,
                                     s.birth_global_day,s.death_global_day,s.birthplace,s.species_occult,
                                     h.location AS household_location,h.social_class
                              FROM sims s LEFT JOIN households h ON h.household_id=s.current_household_id
                              WHERE s.death_global_day IS NULL OR s.death_global_day>=?""",
                           (event_due_from,)).fetchall()
    for event in events:
        due=int(event["start_global_day"])
        if not event_in_window(due):
            continue
        scope=(event["scope"] or "").strip().casefold()
        global_scope=scope.startswith("global") or scope in {
            "world","worldwide","all","everyone","all sims"
        }
        for sim in event_sims:
            if sim["birth_global_day"] is not None and int(sim["birth_global_day"])>due:
                continue
            if sim["death_global_day"] is not None and int(sim["death_global_day"])<due:
                continue
            if not global_scope and not _applies(event["location"],[sim["household_location"],sim["birthplace"]]):
                continue
            if not global_scope and not _applies(event["affected_class"],[sim["social_class"]]):
                continue
            obligations.append({
                "source_id":event["event_id"],"sim_id":sim["sim_id"],"sim_name":_name(sim),
                "species":(sim["species_occult"] or "Human").strip() or "Human",
                "due_global_day":due,"roll_type":f"Event — {event['event_name'] or event['event_id']}",
                "die":default_die("Event"),"bad_results":None,"quantity":1,"kind":"Event",
                "year":start_year+(due-1)//days_per_year,"era_id":None,
                "era_name":None,"rule_status":"Event-defined",
            })
    return obligations

def sync_rolls(con,current_gd):
    last_synced=_setting_int(con,"auto_rolls_synced_through",0)
    due_from=last_synced+1 if int(current_gd)>last_synced else int(current_gd)
    # Deaths can be recorded after future lifecycle rolls were materialized.
    # Remove only untouched automatic rolls after the Sim's death; completed and
    # manually-created records remain historical data.
    removed=con.execute("""DELETE FROM rolls WHERE roll_id IN (
        SELECT r.roll_id FROM rolls r JOIN sims s ON s.sim_id=r.sim_id
        WHERE COALESCE(r.completed,0)=0 AND r.notes LIKE ?
          AND s.death_global_day IS NOT NULL AND r.due_global_day>s.death_global_day
    )""",("Auto-generated%",)).rowcount
    # Reconcile all reached event dates every time. Event libraries can be
    # imported or enabled after the calendar has already passed their date;
    # lifecycle rolls still use the lightweight incremental window.
    obligations=preview(con,current_gd,due_from=due_from,due_to=current_gd,event_due_from=1)
    existing_counts=Counter()
    max_roll_number=0
    for row in con.execute("SELECT source_id,sim_id,due_global_day,roll_type,roll_id FROM rolls"):
        existing_counts[(row["source_id"] or "",row["sim_id"] or "",int(row["due_global_day"]),row["roll_type"])]+=1
        try:max_roll_number=max(max_roll_number,int(str(row["roll_id"]).rsplit("-",1)[1]))
        except Exception:pass
    id_state=[max_roll_number]
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
            qty=o["quantity"],note=note,existing_counts=existing_counts,id_state=id_state
        )
        if n:
            added+=n; by_kind[o["kind"]]+=n
            if o["rule_status"] not in ("Ready","Event-defined"): skipped_missing_rules+=n
    con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("auto_rolls_synced_through",str(max(last_synced,int(current_gd)))))
    con.commit()
    return {"added":added,"removed":max(0,removed),"by_kind":dict(by_kind),"considered":len(obligations),
            "missing_rule_rows":skipped_missing_rules,"from_day":due_from,"through_day":int(current_gd)}

def schedule_sim_lifecycle(con,sim_id,current_gd):
    """Materialize every lifecycle obligation for a newly created Sim, including future rolls."""
    obligations=[
        item for item in preview(con,current_gd)
        if item["kind"]=="Life stage" and item["sim_id"]==sim_id
    ]
    added=0
    for item in obligations:
        note = f"Auto-generated from {item['era_name']}" if item["rule_status"]=="Ready" \
               else f"Auto-generated obligation; {item['rule_status']} for {item['species']} in year {item['year']}"
        added+=_insert_missing(
            con,source_id=item["source_id"],sim_id=item["sim_id"],sim_name=item["sim_name"],
            due_gd=item["due_global_day"],roll_type=item["roll_type"],die=item["die"],
            bad_results=item["bad_results"],qty=item["quantity"],note=note,
        )
    con.commit()
    return added

def backfill_lifecycle_schedules(con,current_gd):
    """Repair missing lifecycle schedules for existing Sims without duplicating recorded rolls."""
    schema_key=getattr(con,"schema_name",None)
    if schema_key and schema_key in _BACKFILLED_SCHEMAS:
        return 0
    obligations=[item for item in preview(con,current_gd) if item["kind"]=="Life stage"]
    added=0
    for item in obligations:
        note = f"Auto-generated from {item['era_name']}" if item["rule_status"]=="Ready" \
               else f"Auto-generated obligation; {item['rule_status']} for {item['species']} in year {item['year']}"
        added+=_insert_missing(
            con,source_id=item["source_id"],sim_id=item["sim_id"],sim_name=item["sim_name"],
            due_gd=item["due_global_day"],roll_type=item["roll_type"],die=item["die"],
            bad_results=item["bad_results"],qty=item["quantity"],note=note,
        )
    con.commit()
    if schema_key:
        _BACKFILLED_SCHEMAS.add(schema_key)
    return added

def upcoming(con,current_gd,days_ahead=20):
    # Generate only the visible future window. This also lets preview discard
    # Sims who died before the window before any per-rule work begins.
    obligations=preview(con,current_gd,due_from=current_gd+1,due_to=current_gd+days_ahead)
    rows=[]
    for o in obligations:
        if current_gd < o["due_global_day"] <= current_gd+days_ahead:
            have=_existing_count(con,o["source_id"],o["sim_id"],o["due_global_day"],o["roll_type"])
            rows.append({**o,"already_scheduled":have,"missing":max(0,o["quantity"]-have)})
    return rows
