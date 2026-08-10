
from __future__ import annotations
import pandas as pd

def _year(gd,start_year=1200,days_per_year=4):
    if gd is None or pd.isna(gd): return None
    return start_year+(int(gd)-1)//days_per_year

def _name(r):
    return " ".join(str(x) for x in [r.get("title"),r.get("first_name"),r.get("last_name"),r.get("suffix")]
                    if x is not None and str(x)!="" and str(x).lower()!="nan").strip()

def build(con,start_year=1200,days_per_year=4,include_rolls=False):
    rows=[]
    sims=pd.read_sql_query("SELECT * FROM sims",con)
    names={}
    if not sims.empty:
        sims["display_name"]=sims.apply(_name,axis=1)
        names=dict(zip(sims.sim_id,sims.display_name))
        for r in sims.itertuples():
            if pd.notna(r.birth_global_day):
                rows.append(dict(global_day=int(r.birth_global_day),category="Birth",title=f"Birth — {r.display_name}",
                                 primary_sim_id=r.sim_id,primary_sim=r.display_name,secondary_sim_id=None,
                                 household_id=r.current_household_id,details=f"{r.birth_status or ''} • {r.birthplace or ''}".strip(" •"),
                                 source_id=r.sim_id))
            if pd.notna(r.death_global_day):
                rows.append(dict(global_day=int(r.death_global_day),category="Death",title=f"Death — {r.display_name}",
                                 primary_sim_id=r.sim_id,primary_sim=r.display_name,secondary_sim_id=None,
                                 household_id=r.current_household_id,details=f"{r.cause_of_death or ''} • {r.death_place or ''}".strip(" •"),
                                 source_id=r.sim_id))

    rel=pd.read_sql_query("SELECT * FROM relationships",con)
    for r in rel.itertuples():
        p1=r.partner1_name or names.get(r.partner1_id,r.partner1_id)
        p2=r.partner2_name or names.get(r.partner2_id,r.partner2_id)
        if pd.notna(r.start_global_day):
            cat="Marriage" if str(r.type).lower()=="marriage" else "Relationship"
            rows.append(dict(global_day=int(r.start_global_day),category=cat,title=f"{cat} — {p1} + {p2}",
                             primary_sim_id=r.partner1_id,primary_sim=p1,secondary_sim_id=r.partner2_id,
                             household_id=None,details=r.location or "",source_id=r.relationship_id))
        if pd.notna(r.end_global_day):
            rows.append(dict(global_day=int(r.end_global_day),category="Relationship End",title=f"Relationship ended — {p1} + {p2}",
                             primary_sim_id=r.partner1_id,primary_sim=p1,secondary_sim_id=r.partner2_id,
                             household_id=None,details=r.status or "",source_id=r.relationship_id))

    preg=pd.read_sql_query("SELECT * FROM pregnancies",con)
    for r in preg.itertuples():
        mom=r.mother_name or names.get(r.mother_id,r.mother_id)
        dad=r.father_name or names.get(r.father_id,r.father_id)
        if pd.notna(r.conception_global_day):
            rows.append(dict(global_day=int(r.conception_global_day),category="Pregnancy",title=f"Pregnancy conceived — {mom}",
                             primary_sim_id=r.mother_id,primary_sim=mom,secondary_sim_id=r.father_id,
                             household_id=None,details=f"Father: {dad or 'Unknown'}",source_id=r.pregnancy_id))
        if pd.notna(r.due_global_day):
            detail=f"{r.status or ''}"
            if r.outcome: detail += f" • {r.outcome}"
            rows.append(dict(global_day=int(r.due_global_day),category="Pregnancy Outcome",title=f"Pregnancy due/outcome — {mom}",
                             primary_sim_id=r.mother_id,primary_sim=mom,secondary_sim_id=r.father_id,
                             household_id=None,details=detail.strip(" •"),source_id=r.pregnancy_id))

    hh=pd.read_sql_query("SELECT * FROM households",con)
    for r in hh.itertuples():
        if pd.notna(r.start_global_day):
            rows.append(dict(global_day=int(r.start_global_day),category="Household",title=f"Household begins — {r.household_name or r.household_id}",
                             primary_sim_id=r.head_sim_id,primary_sim=names.get(r.head_sim_id,r.head_sim_id),
                             secondary_sim_id=None,household_id=r.household_id,details=f"{r.location or ''} • {r.social_class or ''}".strip(" •"),
                             source_id=r.household_id))
        if pd.notna(r.end_global_day):
            rows.append(dict(global_day=int(r.end_global_day),category="Household End",title=f"Household ends — {r.household_name or r.household_id}",
                             primary_sim_id=r.head_sim_id,primary_sim=names.get(r.head_sim_id,r.head_sim_id),
                             secondary_sim_id=None,household_id=r.household_id,details=r.notes or "",source_id=r.household_id))

    ev=pd.read_sql_query("SELECT * FROM events",con)
    for r in ev.itertuples():
        if pd.notna(r.start_global_day):
            rows.append(dict(global_day=int(r.start_global_day),category="Historical Event",title=r.event_name or r.event_id,
                             primary_sim_id=None,primary_sim=None,secondary_sim_id=None,household_id=None,
                             details=f"{r.scope or ''} • {r.location or ''} • {r.affected_class or ''}".strip(" •"),
                             source_id=r.event_id))
        if pd.notna(r.end_global_day) and r.end_global_day!=r.start_global_day:
            rows.append(dict(global_day=int(r.end_global_day),category="Historical Event End",title=f"Ends — {r.event_name or r.event_id}",
                             primary_sim_id=None,primary_sim=None,secondary_sim_id=None,household_id=None,
                             details=r.location or "",source_id=r.event_id))

    er=pd.read_sql_query("SELECT * FROM event_results",con)
    for r in er.itertuples():
        if pd.notna(r.global_day):
            sim=names.get(r.sim_id,r.sim_id)
            rows.append(dict(global_day=int(r.global_day),category="Event Result",title=f"Event result — {sim or r.event_id}",
                             primary_sim_id=r.sim_id,primary_sim=sim,secondary_sim_id=None,household_id=r.household_id,
                             details=f"{r.outcome or ''} • {r.cause_effect or ''}".strip(" •"),source_id=r.result_id))

    if include_rolls:
        rolls=pd.read_sql_query("SELECT * FROM rolls",con)
        for r in rolls.itertuples():
            if pd.notna(r.due_global_day):
                rows.append(dict(global_day=int(r.due_global_day),category="Roll",title=f"Roll — {r.roll_type} — {r.sim_name or r.sim_id or ''}",
                                 primary_sim_id=r.sim_id,primary_sim=r.sim_name or names.get(r.sim_id,r.sim_id),
                                 secondary_sim_id=None,household_id=None,
                                 details=f"{r.die or ''} • Bad: {r.bad_results or ''} • {r.outcome or ''}".strip(" •"),
                                 source_id=r.roll_id))

    if not rows:
        return pd.DataFrame(columns=["global_day","year","decade","category","title","primary_sim_id","primary_sim",
                                     "secondary_sim_id","household_id","details","source_id"])
    out=pd.DataFrame(rows)
    out["year"]=out.global_day.apply(lambda x:_year(x,start_year,days_per_year))
    out["decade"]=(out.year//10)*10
    out=out.sort_values(["global_day","category","title"]).reset_index(drop=True)
    return out
