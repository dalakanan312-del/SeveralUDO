
from __future__ import annotations
import math
from collections import defaultdict, deque
import pandas as pd

LIFE_STAGES = [
    ("Newborn", 0, 1),
    ("Infant", 1, 4),
    ("Toddler", 4, 20),
    ("Child", 20, 40),
    ("Preteen", 40, 52),
    ("Teen", 52, 72),
    ("Young Adult", 72, 160),
    ("Adult", 160, 240),
    ("Elder", 240, float("inf")),
]

def year_from_gd(g, start_year=1200):
    if pd.isna(g): return None
    return start_year + (int(g)-1)//4

def decade_of_year(y):
    if y is None or pd.isna(y): return None
    return (int(y)//10)*10

def gd_for_year_start(y, start_year=1200):
    return (int(y)-start_year)*4 + 1

def gd_for_year_end(y, start_year=1200):
    return (int(y)-start_year)*4 + 4

def life_stage_from_age_days(age_days):
    if age_days is None or pd.isna(age_days) or age_days < 0:
        return None
    for name, lo, hi in LIFE_STAGES:
        if lo <= age_days < hi:
            return name
    return "Elder"

def safe_div(a,b):
    return (a/b) if b not in (0,None) and not pd.isna(b) else None

def median(values):
    s=pd.Series([x for x in values if x is not None and not pd.isna(x)], dtype="float64")
    return float(s.median()) if not s.empty else None

def mean(values):
    s=pd.Series([x for x in values if x is not None and not pd.isna(x)], dtype="float64")
    return float(s.mean()) if not s.empty else None

def pct(n,d):
    v=safe_div(n,d)
    return v*100 if v is not None else None

def format_year_span_days(days):
    if days is None or pd.isna(days): return None
    return float(days)/4.0

def prepare(con, current_gd:int, start_year:int=1200):
    sims=pd.read_sql_query("SELECT * FROM sims",con)
    households=pd.read_sql_query("SELECT * FROM households",con)
    pregnancies=pd.read_sql_query("SELECT * FROM pregnancies",con)
    relationships=pd.read_sql_query("SELECT * FROM relationships",con)
    events=pd.read_sql_query("SELECT * FROM events",con)
    event_results=pd.read_sql_query("SELECT * FROM event_results",con)
    rolls=pd.read_sql_query("SELECT * FROM rolls",con)

    for col in ["birth_global_day","marriage_global_day","death_global_day","generation"]:
        if col in sims: sims[col]=pd.to_numeric(sims[col],errors="coerce")
    for col in ["start_global_day","end_global_day","children_count","legally_married"]:
        if col in relationships: relationships[col]=pd.to_numeric(relationships[col],errors="coerce")
    for col in ["start_global_day","end_global_day"]:
        if col in households: households[col]=pd.to_numeric(households[col],errors="coerce")
    for col in ["start_global_day","end_global_day"]:
        if col in events: events[col]=pd.to_numeric(events[col],errors="coerce")
    if "global_day" in event_results: event_results["global_day"]=pd.to_numeric(event_results["global_day"],errors="coerce")

    sims["birth_year"]=sims["birth_global_day"].apply(lambda x: year_from_gd(x,start_year))
    sims["death_year"]=sims["death_global_day"].apply(lambda x: year_from_gd(x,start_year))
    sims["birth_decade"]=sims["birth_year"].apply(decade_of_year)
    sims["death_decade"]=sims["death_year"].apply(decade_of_year)
    sims["living"]=sims["birth_global_day"].notna() & (sims["birth_global_day"]<=current_gd) & (sims["death_global_day"].isna() | (sims["death_global_day"]>current_gd))
    sims["age_days"]=sims.apply(lambda r: max(0,current_gd-r.birth_global_day) if pd.notna(r.birth_global_day) and r.birth_global_day<=current_gd and r.living else None,axis=1)
    sims["age_years"]=sims["age_days"].apply(lambda x: x/4 if x is not None and not pd.isna(x) else None)
    sims["lifespan_days"]=sims.apply(lambda r: r.death_global_day-r.birth_global_day if pd.notna(r.birth_global_day) and pd.notna(r.death_global_day) and r.death_global_day>=r.birth_global_day else None,axis=1)
    sims["lifespan_years"]=sims["lifespan_days"].apply(lambda x: x/4 if x is not None and not pd.isna(x) else None)
    sims["current_life_stage"]=sims["age_days"].apply(life_stage_from_age_days)
    sims["death_life_stage"]=sims["lifespan_days"].apply(life_stage_from_age_days)

    # Births are challenge-recorded biological births; Married In / Adopted In are tracked separately as introductions.
    bs=sims["birth_status"].fillna("").str.strip().str.lower()
    sims["challenge_birth"] = sims["birth_global_day"].notna() & ~bs.isin(["married in","adopted in"])
    sims["introduced"] = sims["birth_global_day"].notna() & bs.isin(["married in","adopted in"])

    # Name helpers
    sims["display_name"]=(sims["title"].fillna("")+" "+sims["first_name"].fillna("")+" "+sims["last_name"].fillna("")+" "+sims["suffix"].fillna("")).str.replace(r"\s+"," ",regex=True).str.strip()
    name=dict(zip(sims.sim_id,sims.display_name))

    # Parent/child graph
    children=defaultdict(list)
    parents=defaultdict(list)
    for r in sims.itertuples():
        for p in [r.mother_id,r.father_id]:
            if p and p in name:
                children[p].append(r.sim_id)
                parents[r.sim_id].append(p)

    def descendants(sid):
        seen=set(); dq=deque(children.get(sid,[]))
        while dq:
            n=dq.popleft()
            if n in seen: continue
            seen.add(n); dq.extend(children.get(n,[]))
        return seen

    def ancestors(sid):
        seen=set(); dq=deque(parents.get(sid,[]))
        while dq:
            n=dq.popleft()
            if n in seen: continue
            seen.add(n); dq.extend(parents.get(n,[]))
        return seen

    return {
        "sims":sims,"households":households,"pregnancies":pregnancies,"relationships":relationships,
        "events":events,"event_results":event_results,"rolls":rolls,"children":children,"parents":parents,
        "descendants":descendants,"ancestors":ancestors,"name":name,"current_gd":current_gd,"start_year":start_year
    }

def population_yearly(ctx):
    sims=ctx["sims"]; start_year=ctx["start_year"]; current_gd=ctx["current_gd"]
    valid=sims[sims.birth_global_day.notna() & (sims.birth_global_day<=current_gd)]
    if valid.empty: return pd.DataFrame()
    min_y=int(valid.birth_year.min()); cur_y=year_from_gd(current_gd,start_year)
    rows=[]
    for y in range(min_y,cur_y+1):
        s=gd_for_year_start(y,start_year); e=min(gd_for_year_end(y,start_year),current_gd)
        pop_start=((sims.birth_global_day<=s)&(sims.death_global_day.isna()| (sims.death_global_day>s))).sum()
        pop_end=((sims.birth_global_day<=e)&(sims.death_global_day.isna()| (sims.death_global_day>e))).sum()
        births=((sims.challenge_birth)&(sims.birth_year==y)).sum()
        introduced=((sims.introduced)&(sims.birth_year==y)).sum()
        deaths=(sims.death_year==y).sum()
        events=0
        rows.append({"year":y,"decade":decade_of_year(y),"population_start":int(pop_start),"population_end":int(pop_end),
                     "net_change":int(pop_end-pop_start),"births":int(births),"introduced":int(introduced),"deaths":int(deaths),
                     "natural_increase":int(births-deaths)})
    out=pd.DataFrame(rows)
    out["growth_rate_pct"]=out.apply(lambda r: safe_div(r.net_change,r.population_start)*100 if r.population_start else None,axis=1)
    out["birth_rate_per_100"]=out.apply(lambda r: safe_div(r.births,r.population_end)*100 if r.population_end else None,axis=1)
    out["death_rate_per_100"]=out.apply(lambda r: safe_div(r.deaths,r.population_end)*100 if r.population_end else None,axis=1)
    return out

def decade_summary(ctx):
    y=population_yearly(ctx)
    if y.empty:return y
    sims=ctx["sims"]; rel=ctx["relationships"]; hh=ctx["households"]; events=ctx["events"]; start_year=ctx["start_year"]
    rows=[]
    for dec,g in y.groupby("decade"):
        dec=int(dec); start=dec; end=min(dec+9,int(y.year.max()))
        sy=g[g.year==start]
        ey=g[g.year==end]
        pop_start=int(sy.population_start.iloc[0]) if not sy.empty else int(g.population_start.iloc[0])
        pop_end=int(ey.population_end.iloc[0]) if not ey.empty else int(g.population_end.iloc[-1])
        deaths=sims[sims.death_decade==dec]
        lif=deaths.lifespan_years.dropna()
        marriages=rel[(rel.type.fillna("").str.lower()=="marriage") & rel.start_global_day.notna()].copy()
        if not marriages.empty:
            marriages["start_year"]=marriages.start_global_day.apply(lambda x:year_from_gd(x,start_year))
            marriages["decade"]=marriages.start_year.apply(decade_of_year)
            mcount=int((marriages.decade==dec).sum())
        else:mcount=0
        # households active at decade end
        endgd=gd_for_year_end(end,start_year)
        hcount=int(((hh.start_global_day.isna()| (hh.start_global_day<=endgd)) & (hh.end_global_day.isna() | (hh.end_global_day>endgd))).sum()) if not hh.empty else 0
        ev=events.copy()
        if not ev.empty:
            ev["start_year"]=ev.start_global_day.apply(lambda x:year_from_gd(x,start_year) if pd.notna(x) else None)
            ev["decade"]=ev.start_year.apply(decade_of_year)
            evcount=int((ev.decade==dec).sum())
        else:evcount=0
        reached=sims[(sims.birth_year.notna())&(sims.birth_year<=end)].generation.dropna()
        rows.append({
            "decade":dec,"population_start":pop_start,"population_end":pop_end,"population_change":pop_end-pop_start,
            "avg_population":round(g.population_end.mean(),2),"births":int(g.births.sum()),"deaths":int(g.deaths.sum()),
            "natural_increase":int(g.natural_increase.sum()),"marriages":mcount,
            "avg_lifespan":round(lif.mean(),2) if not lif.empty else None,"households":hcount,
            "generation_reached":int(reached.max()) if not reached.empty else None,"events":evcount
        })
    return pd.DataFrame(rows)

def births_stats(ctx):
    sims=ctx["sims"]; births=sims[sims.challenge_birth].copy()
    children=ctx["children"]
    parent_counts=pd.Series({p:len(c) for p,c in children.items()},name="children").sort_values(ascending=False)
    mothers=sims[sims.sim_id.isin([x for x in sims.mother_id.dropna().unique() if x in set(sims.sim_id)])]
    fathers=sims[sims.sim_id.isin([x for x in sims.father_id.dropna().unique() if x in set(sims.sim_id)])]

    # sibling gaps per full sibling family
    gaps=[]
    byfamily=births.dropna(subset=["mother_id","father_id","birth_global_day"]).groupby(["mother_id","father_id"])
    for _,g in byfamily:
        vals=sorted(g.birth_global_day.astype(int).tolist())
        gaps += [b-a for a,b in zip(vals,vals[1:])]
    return births,parent_counts,mothers,fathers,gaps

def sibling_table(ctx):
    sims=ctx["sims"].copy()
    full_groups=defaultdict(list); maternal=defaultdict(list); paternal=defaultdict(list)
    for r in sims.itertuples():
        if r.mother_id: maternal[r.mother_id].append(r.sim_id)
        if r.father_id: paternal[r.father_id].append(r.sim_id)
        if r.mother_id or r.father_id: full_groups[(r.mother_id or "",r.father_id or "")].append(r.sim_id)
    rows=[]
    for r in sims.itertuples():
        fg=full_groups.get((r.mother_id or "",r.father_id or ""),[])
        mg=maternal.get(r.mother_id,[]) if r.mother_id else []
        pg=paternal.get(r.father_id,[]) if r.father_id else []
        sibs=(set(mg)|set(pg))-{r.sim_id}
        rows.append({"sim_id":r.sim_id,"name":r.display_name,"siblings":len(sibs),
                     "full_siblings":max(0,len(fg)-1),"maternal_siblings":max(0,len(mg)-1),
                     "paternal_siblings":max(0,len(pg)-1)})
    return pd.DataFrame(rows),full_groups

def lineage_table(ctx):
    sims=ctx["sims"]; desc=ctx["descendants"]; anc=ctx["ancestors"]; children=ctx["children"]; living=set(sims[sims.living].sim_id)
    genmap=dict(zip(sims.sim_id,sims.generation))
    rows=[]
    for r in sims.itertuples():
        ds=desc(r.sim_id); an=anc(r.sim_id)
        ch=set(children.get(r.sim_id,[]))
        gc=set()
        for c in ch: gc |= set(children.get(c,[]))
        ggc=set()
        for g in gc: ggc |= set(children.get(g,[]))
        dgens=[genmap.get(x) for x in ds if pd.notna(genmap.get(x))]
        span=(max(dgens)-r.generation) if dgens and pd.notna(r.generation) else 0
        rows.append({"sim_id":r.sim_id,"name":r.display_name,"children":len(ch),"grandchildren":len(gc),
                     "great_grandchildren":len(ggc),"descendants":len(ds),"living_descendants":len(ds & living),
                     "ancestors":len(an),"generation_span":int(span) if not pd.isna(span) else 0})
    return pd.DataFrame(rows)

def relationship_stats(ctx):
    rel=ctx["relationships"].copy(); sims=ctx["sims"]; start_year=ctx["start_year"]; names=ctx["name"]
    if rel.empty:return rel,pd.DataFrame()
    rel["start_year"]=rel.start_global_day.apply(lambda x:year_from_gd(x,start_year) if pd.notna(x) else None)
    rel["start_decade"]=rel.start_year.apply(decade_of_year)
    rel["duration_days"]=rel.apply(lambda r:(r.end_global_day-r.start_global_day) if pd.notna(r.start_global_day) and pd.notna(r.end_global_day) and r.end_global_day>=r.start_global_day else None,axis=1)
    rel["duration_years"]=rel.duration_days.apply(lambda x:x/4 if x is not None and not pd.isna(x) else None)
    birth=dict(zip(sims.sim_id,sims.birth_global_day))
    rel["p1_age"]=rel.apply(lambda r:(r.start_global_day-birth.get(r.partner1_id))/4 if pd.notna(r.start_global_day) and pd.notna(birth.get(r.partner1_id)) else None,axis=1)
    rel["p2_age"]=rel.apply(lambda r:(r.start_global_day-birth.get(r.partner2_id))/4 if pd.notna(r.start_global_day) and pd.notna(birth.get(r.partner2_id)) else None,axis=1)
    rel["age_gap"]=rel.apply(lambda r:abs(r.p1_age-r.p2_age) if pd.notna(r.p1_age) and pd.notna(r.p2_age) else None,axis=1)
    counts=defaultdict(int)
    for r in rel.itertuples():
        if str(r.type).lower()=="marriage":
            if r.partner1_id:counts[r.partner1_id]+=1
            if r.partner2_id:counts[r.partner2_id]+=1
    marr=pd.DataFrame([{"sim_id":k,"name":names.get(k,k),"marriages":v} for k,v in counts.items()])
    return rel,marr

def household_stats(ctx):
    hh=ctx["households"].copy(); sims=ctx["sims"]; current_gd=ctx["current_gd"]; start_year=ctx["start_year"]
    living=sims[sims.living]
    counts=living.groupby("current_household_id").size().rename("living_population") if not living.empty else pd.Series(dtype=int)
    allcounts=sims.groupby("current_household_id").size().rename("total_associated") if not sims.empty else pd.Series(dtype=int)
    out=hh.copy()
    out=out.merge(counts,left_on="household_id",right_index=True,how="left").merge(allcounts,left_on="household_id",right_index=True,how="left")
    out["living_population"]=out["living_population"].fillna(0).astype(int)
    out["total_associated"]=out["total_associated"].fillna(0).astype(int)
    out["active_now"]=(out.start_global_day.isna()| (out.start_global_day<=current_gd)) & (out.end_global_day.isna() | (out.end_global_day>current_gd))
    return out

def individual_profile(ctx,sid):
    sims=ctx["sims"]; r=sims[sims.sim_id==sid]
    if r.empty:return {}
    r=r.iloc[0]
    sib,_=sibling_table(ctx); sr=sib[sib.sim_id==sid].iloc[0]
    lin=lineage_table(ctx); lr=lin[lin.sim_id==sid].iloc[0]
    rel=ctx["relationships"]; rr=rel[(rel.partner1_id==sid)|(rel.partner2_id==sid)]
    marriages=rr[rr.type.fillna("").str.lower()=="marriage"]
    evr=ctx["event_results"]; evcount=int((evr.sim_id==sid).sum()) if not evr.empty else 0
    return {
        "Sim ID":sid,"Name":r.display_name,"Birth year":r.birth_year,"Death year":r.death_year,
        "Age / lifespan":r.age_years if r.living else r.lifespan_years,"Generation":r.generation,
        "Life stage":r.current_life_stage if r.living else r.death_life_stage,
        "Household":r.current_household_id,"Status":"Living" if r.living else "Deceased",
        "Parents recorded":len(ctx["parents"].get(sid,[])),"Siblings":int(sr.siblings),
        "Multiple status":r.multiple_birth,"Children":int(lr.children),"Grandchildren":int(lr.grandchildren),
        "Great-grandchildren":int(lr.great_grandchildren),"Total descendants":int(lr.descendants),
        "Ancestors":int(lr.ancestors),"Relationships":len(rr),"Marriages":len(marriages),
        "Recorded event results":evcount,
    }
