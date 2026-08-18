import tempfile
import base64
import re
import secrets
import html
import io
from functools import wraps
from datetime import time
from pathlib import Path
import pandas as pd
import streamlit as st
from db import connect,setting,set_setting,next_id,statement_tables,cache_token
from calendar_utils import global_day_to_year_day,global_day_label,date_to_global_day,global_day_time_to_date,format_exact_date
import stats_engine as se
import autorolls
import era_rules
import timeline_engine
import profiles
import save_manager
import storage
import neon_ui
import admin_ops
import workspace_access
import relationship_photos
import marriage_ai
import dice_roller
import roll_outcomes
import notebook
import plant_reference
import illnesses
import challenge_management as cm
import event_library
import action_queue
import death_causes
import cloud_schema
import clock_sync
import play_planner
from app_version import APP_VERSION
from page_registry import navigation_labels, grouped_pages

st.set_page_config(page_title="Decades Tracker",page_icon="🏰",layout="wide")

# Railway currently runs a Streamlit release from before segmented_control.
# Keep the focused, one-section-at-a-time pages while presenting a compatible
# horizontal selector on older releases.
if not hasattr(st,"segmented_control"):
    def _segmented_control_compat(label,options,default=None,**kwargs):
        choices=list(options)
        index=choices.index(default) if default in choices else 0
        return st.radio(label,choices,index=index,horizontal=True,**kwargs)
    st.segmented_control=_segmented_control_compat

# Streamlit fragments are the core of the 3.2 interaction model: a control on
# the active page reruns that page only. Keep a compatibility decorator for
# older local Streamlit builds; Railway uses the native implementation.
if not hasattr(st,"fragment"):
    def _fragment_compat(func=None,**_kwargs):
        return func if func is not None else (lambda wrapped: wrapped)
    st.fragment=_fragment_compat

def workspace_fragment(func):
    """Restore the workspace context on Streamlit's independent fragment reruns."""
    @st.fragment
    @wraps(func)
    def guarded_fragment(*args,**kwargs):
        fragment_workspace=st.session_state.get("workspace_id")
        if not fragment_workspace:
            st.info("This private workspace is locked. Reopen it to continue.")
            return None
        save_manager.set_workspace(fragment_workspace,st.session_state.get("active_save_id"))
        return func(*args,**kwargs)
    return guarded_fragment

def rerun_current_fragment():
    """Refresh only the active page when supported; safely fall back on older Streamlit."""
    try:
        st.rerun(scope="fragment")
    except (TypeError, ValueError):
        st.rerun()
    except Exception as error:
        if "scope" not in str(error).casefold() and "fragment" not in str(error).casefold():
            raise
        st.rerun()

if not storage.configured():
    neon_ui.render_connection_setup(st)
    st.stop()

workspace=workspace_access.render_gate(st)
if not workspace:
    st.stop()
save_manager.set_workspace(workspace,st.session_state.get("active_save_id"))
save_manager.ensure_setup()
if neon_ui.render_first_save_setup(st):
    st.stop()

st.markdown("""
<style>
:root{
    --decades-gold:#b98a43;--decades-gold-soft:rgba(185,138,67,.13);
    --decades-wine:#753747;--decades-ink:#3d2a1d;
    --decades-line:rgba(185,138,67,.22);--decades-surface:rgba(128,128,128,.045);
    --decades-card:rgba(255,255,255,.035);--decades-muted:rgba(128,128,128,.72);
}
.stApp{background-image:radial-gradient(circle at 85% -10%,rgba(185,138,67,.08),transparent 28rem)}
.stApp:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.045;background-image:repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(115,78,38,.18) 4px);z-index:0}
.block-container{padding-top:1.2rem;max-width:1280px;padding-bottom:4rem}
[data-testid="stSidebar"]{
    min-width:255px;
    border-right:1px solid var(--decades-line);
    background:linear-gradient(180deg,rgba(199,154,74,.07),transparent 18rem)
}
[data-testid="stSidebar"] .block-container{padding-top:1.2rem}
.sidebar-brand{padding:.35rem .15rem .8rem}
.sidebar-brand-title{font-family:Georgia,serif;font-size:1.72rem;font-weight:700;letter-spacing:.02em;line-height:1.1}
.sidebar-brand-subtitle{color:var(--decades-gold);font-size:.75rem;font-weight:700;letter-spacing:.16em;text-transform:uppercase;margin-top:.28rem}
[data-testid="stSidebar"] [role="radiogroup"] label{
    border-radius:10px;padding:.22rem .45rem;margin:.08rem 0;transition:background .15s ease,transform .15s ease
}
[data-testid="stSidebar"] [role="radiogroup"] label:hover{background:var(--decades-gold-soft);transform:translateX(2px)}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked){background:var(--decades-gold-soft);box-shadow:inset 3px 0 0 var(--decades-gold)}
[data-testid="stMetric"]{
    border:1px solid var(--decades-line);border-radius:18px;padding:14px 16px;
    background:linear-gradient(145deg,var(--decades-gold-soft),var(--decades-surface));
    box-shadow:0 8px 24px rgba(0,0,0,.06)
}
div[data-testid="stExpander"]{border-radius:13px;border-color:var(--decades-line);overflow:hidden}
.stButton>button{border-radius:10px;border-color:var(--decades-line);font-weight:650;transition:transform .12s ease,box-shadow .12s ease}
.stButton>button:hover{transform:translateY(-1px);box-shadow:0 6px 16px rgba(0,0,0,.12);border-color:var(--decades-gold)}
.stButton>button[kind="primary"]{background:linear-gradient(135deg,#9b6a2d,#c79a4a);border:0;color:white}
.stTextInput input,.stNumberInput input,.stTextArea textarea,.stSelectbox div[data-baseweb="select"]>div{border-radius:10px;border-color:rgba(128,128,128,.28)}
[data-baseweb="tab-list"]{gap:.35rem;border-bottom:1px solid var(--decades-line)}
[data-baseweb="tab"]{border-radius:10px 10px 0 0;padding-left:1rem;padding-right:1rem}
[data-baseweb="tab"][aria-selected="true"]{background:var(--decades-gold-soft);color:var(--decades-gold)}
[data-testid="stDataFrame"]{border:1px solid var(--decades-line);border-radius:12px;overflow:hidden}
[data-testid="stAlert"]{border-radius:12px}
[data-testid="stImage"] img{border-radius:13px;box-shadow:0 8px 24px rgba(0,0,0,.13)}
h1,h2,h3{font-family:Georgia,serif;letter-spacing:-.018em}
h1{margin-bottom:.1rem;font-size:clamp(2rem,4vw,3.1rem)}
.page-kicker{color:var(--decades-gold);font-size:.72rem;font-weight:750;letter-spacing:.17em;text-transform:uppercase;margin-bottom:.2rem}
.page-subtitle{opacity:.74;margin-top:-.12rem;margin-bottom:1.2rem;max-width:850px;font-size:1rem}
.chronicle-note{
    position:relative;margin:.25rem 0 1.25rem;padding:1rem 1.2rem 1rem 1.45rem;
    border:1px solid var(--decades-line);border-left:4px solid var(--decades-gold);border-radius:3px 13px 13px 3px;
    background:linear-gradient(135deg,rgba(214,178,112,.16),rgba(123,52,68,.055));
    box-shadow:0 10px 28px rgba(0,0,0,.07);font-family:Georgia,serif
}
.chronicle-note:before{content:"❦";position:absolute;right:1rem;top:.55rem;color:var(--decades-gold);font-size:1.35rem;opacity:.8}
.chronicle-note-title{font-weight:700;font-size:1.05rem;letter-spacing:.035em;margin-bottom:.25rem}
.chronicle-note-text{opacity:.8;font-style:italic;max-width:880px;padding-right:2rem}
.dice-case{margin:.75rem 0 .45rem;padding:.85rem 1rem;border:1px solid var(--decades-line);border-radius:13px;background:linear-gradient(145deg,rgba(82,43,24,.13),rgba(199,154,74,.12));box-shadow:inset 0 0 22px rgba(75,43,22,.08)}
.dice-case-title{font-family:Georgia,serif;font-weight:700;letter-spacing:.04em}
.dice-case-note{font-size:.88rem;opacity:.72;margin-top:.18rem}
.section-note{opacity:.72;font-size:.92rem}
.pill{
    display:inline-block;padding:.2rem .55rem;border-radius:999px;
    border:1px solid var(--decades-line);background:var(--decades-gold-soft);margin-right:.3rem;font-size:.88rem
}
.v3-hero{margin:.2rem 0 1.2rem;padding:1.35rem 1.5rem;border:1px solid var(--decades-line);border-radius:22px;background:linear-gradient(135deg,rgba(185,138,67,.16),rgba(117,55,71,.08));box-shadow:0 12px 38px rgba(0,0,0,.07)}
.v3-eyebrow{font-size:.72rem;text-transform:uppercase;letter-spacing:.17em;font-weight:800;color:var(--decades-gold)}
.v3-hero-title{font:700 clamp(1.7rem,3vw,2.55rem)/1.1 Georgia,serif;margin:.25rem 0}
.v3-hero-copy{opacity:.76;max-width:760px;font-size:1rem}
.v3-section{display:flex;justify-content:space-between;align-items:end;gap:1rem;margin:1.65rem 0 .7rem}
.v3-section-title{font:700 1.35rem/1.2 Georgia,serif}.v3-section-note{opacity:.65;font-size:.9rem}
.v3-card{border:1px solid var(--decades-line);border-radius:17px;padding:1rem 1.1rem;margin:.55rem 0;background:var(--decades-card);box-shadow:0 5px 18px rgba(0,0,0,.045)}
.v3-card:hover{border-color:rgba(185,138,67,.5);box-shadow:0 9px 25px rgba(0,0,0,.075)}
.v3-card-top{display:flex;justify-content:space-between;align-items:start;gap:1rem}.v3-card-title{font:700 1.05rem/1.25 Georgia,serif}.v3-card-badge{white-space:nowrap;border-radius:999px;padding:.18rem .52rem;background:var(--decades-gold-soft);color:var(--decades-gold);font-size:.75rem;font-weight:750}
.v3-card-meta{display:flex;gap:.85rem;flex-wrap:wrap;margin:.45rem 0 0;color:var(--decades-muted);font-size:.86rem}.v3-card-body{margin-top:.55rem;opacity:.82;line-height:1.48}
.v3-empty{text-align:center;padding:2.1rem 1rem;border:1px dashed var(--decades-line);border-radius:18px;opacity:.72}
.v3-nav-group{font-size:.68rem;font-weight:800;letter-spacing:.16em;text-transform:uppercase;color:var(--decades-gold);margin:1rem .4rem .25rem}
[data-testid="stSidebar"] .stButton>button{justify-content:flex-start;text-align:left;border-color:transparent;background:transparent;box-shadow:none;padding:.34rem .55rem}
[data-testid="stSidebar"] .stButton>button:hover{background:var(--decades-gold-soft);transform:translateX(2px);box-shadow:none}
[data-testid="stSidebar"] .stButton>button[kind="primary"]{background:linear-gradient(90deg,rgba(185,138,67,.24),rgba(185,138,67,.07));color:inherit;border-left:3px solid var(--decades-gold);border-radius:8px}
.v3-hero{position:relative;overflow:hidden}
.v3-hero:after{content:"";position:absolute;width:13rem;height:13rem;border:1px solid rgba(185,138,67,.12);border-radius:50%;right:-5rem;top:-7rem;box-shadow:0 0 0 2rem rgba(185,138,67,.025),0 0 0 4rem rgba(185,138,67,.018)}
.v3-card{backdrop-filter:blur(3px)}
.v3-chronicle-list{border:1px solid var(--decades-line);border-radius:14px;overflow:hidden;background:var(--decades-card);margin:.45rem 0}
.v3-chronicle-row{display:grid;grid-template-columns:minmax(12rem,1.35fr) minmax(16rem,2.65fr) auto;align-items:center;gap:.75rem;padding:.58rem .78rem;border-bottom:1px solid var(--decades-line)}
.v3-chronicle-row:last-child{border-bottom:0}.v3-chronicle-row:hover{background:rgba(185,138,67,.07)}
.v3-chronicle-title{font:700 .96rem/1.25 Georgia,serif;min-width:0}.v3-chronicle-meta{display:flex;gap:.65rem 1rem;align-items:center;flex-wrap:wrap;color:var(--decades-muted);font-size:.82rem;min-width:0}
.v3-chronicle-body{grid-column:2/4;font-size:.82rem;line-height:1.35;opacity:.76;white-space:pre-line;margin-top:-.28rem}.v3-chronicle-badge{white-space:nowrap;border-radius:999px;padding:.14rem .48rem;background:var(--decades-gold-soft);color:var(--decades-gold);font-size:.7rem;font-weight:750}
@media(max-width:760px){.v3-chronicle-row{grid-template-columns:1fr auto}.v3-chronicle-meta,.v3-chronicle-body{grid-column:1/3}.v3-chronicle-body{margin-top:0}}
[data-testid="stDataFrame"]{opacity:.94}
@media(max-width:760px){
    .block-container{padding:.9rem .85rem 2.5rem}
    [data-baseweb="tab-list"]{overflow-x:auto;flex-wrap:nowrap}
    [data-baseweb="tab"]{white-space:nowrap;padding-left:.7rem;padding-right:.7rem}
    [data-testid="stMetric"]{padding:10px 11px}
}
</style>
""",unsafe_allow_html=True)

@st.cache_data(ttl=30,show_spinner=False)
def _cached_query(sql,params,workspace_key,save_id,revision):
    c=connect()
    try:return pd.read_sql_query(sql,c,params=params)
    finally:c.close()

@st.cache_data(ttl=30,show_spinner=False)
def _cached_scalar(sql,params,default,workspace_key,save_id,revision):
    c=connect()
    try:
        row=c.execute(sql,params).fetchone()
        return row[0] if row and row[0] is not None else default
    finally:c.close()

def _query_revision(sql=None,tables=None):
    record=save_manager.active_save()
    if sql is not None:
        tables=statement_tables(sql)
    if tables is not None:
        return workspace,record["save_id"],cache_token(record["schema_name"],tables)
    return workspace,record["save_id"],record.get("updated_at") or ""

def q(sql,params=()):
    return _cached_query(sql,tuple(params or ()),*_query_revision(sql=sql)).copy()

def scalar(sql,params=(),default=0):
    return _cached_scalar(sql,tuple(params or ()),default,*_query_revision(sql=sql))

@st.cache_data(ttl=60,show_spinner=False)
def _cached_sim_photo(sim_id,global_day,life_stage,workspace_key,save_id,revision):
    con=connect()
    try:
        return (profiles.get_lifestage_photo(con,sim_id,life_stage) if life_stage
                else profiles.get_current_photo(con,sim_id,global_day))
    finally:con.close()

@st.cache_data(ttl=60,show_spinner=False)
def _cached_relationship_photo(relationship_id,workspace_key,save_id,revision):
    con=connect()
    try:return relationship_photos.get_photo(con,relationship_id)
    finally:con.close()

@st.cache_data(ttl=30,show_spinner=False)
def _cached_upcoming_rolls(global_day,horizon,workspace_key,save_id,revision):
    con=connect()
    try:return autorolls.upcoming(con,global_day,horizon)
    finally:con.close()

@st.cache_data(ttl=60,show_spinner=False)
def _cached_statistics(global_day,start_year,workspace_key,save_id,revision):
    con=connect()
    try:
        ctx=se.prepare(con,global_day,start_year)
    finally:con.close()
    ctx["_yearly"]=se.population_yearly(ctx)
    ctx["_decades"]=se.decade_summary(ctx)
    ctx["_birth_bundle"]=se.births_stats(ctx)
    ctx["_sibling_bundle"]=se.sibling_table(ctx)
    ctx["_lineage"]=se.lineage_table(ctx)
    ctx["_relationship_bundle"]=se.relationship_stats(ctx)
    ctx["_household_stats"]=se.household_stats(ctx)
    # Nested graph functions cannot be serialized by Streamlit's data cache.
    # Their derived tables are cached above; recreate the helpers after load.
    ctx.pop("descendants",None)
    ctx.pop("ancestors",None)
    return ctx

def cached_sim_photo(sim_id,life_stage=None):
    return _cached_sim_photo(sim_id,current_gd(),life_stage,
        *_query_revision(tables=("sim_photos","sim_lifestage_photos","sims","settings"))) if sim_id else None

def current_sim_life_stage(sim_id):
    if not sim_id:return None
    con=connect()
    try:return profiles.current_life_stage(con,sim_id,current_gd())
    finally:con.close()

def cached_relationship_photo(relationship_id):
    return _cached_relationship_photo(relationship_id,*_query_revision(tables=("relationship_photos",))) if relationship_id else None

def cached_upcoming_rolls(global_day,horizon):
    return _cached_upcoming_rolls(global_day,horizon,*_query_revision(tables=("rolls","sims","pregnancies","events","households","rules","roll_rule_eras","roll_rule_values","settings")))

@st.cache_data(ttl=30,show_spinner=False)
def _cached_today_counts(global_day,workspace_key,save_id,revision):
    con=connect()
    try:
        row=con.execute("""SELECT
            (SELECT COUNT(*) FROM action_queue WHERE status='open' AND source_type='roll' AND due_global_day<=?) AS rolls_due,
            (SELECT COUNT(*) FROM pregnancies WHERE due_global_day<=? AND COALESCE(status,'') NOT IN
                ('Delivered','Cancelled','Complete','Miscarriage','Stillbirth')) AS pregnancies_due,
            (SELECT COUNT(*) FROM events WHERE start_global_day<=? AND end_global_day>=?) AS active_events,
            (SELECT COUNT(*) FROM illnesses WHERE onset_global_day<=?
                AND COALESCE(status,'Active') IN ('Active','Improving','Worsening','Chronic')
                AND (end_global_day IS NULL OR end_global_day>=?)) AS active_illnesses,
            (SELECT COUNT(*) FROM sims WHERE death_global_day=?) AS deaths_today""",
            (global_day,global_day,global_day,global_day,global_day,global_day,global_day)).fetchone()
        return tuple(int(value or 0) for value in row)
    finally:
        con.close()

def today_counts(global_day):
    return _cached_today_counts(global_day,*_query_revision(
        tables=("action_queue","rolls","pregnancies","events","illnesses","sims")))

def is_death_outcome(outcome):
    return bool(re.search(r"\b(death|dead|dies|died|killed|fatal)\b",str(outcome or ""),re.I))

def should_record_roll_death(roll,outcome):
    """Event rolls require an explicit fatal result; failed aging rolls are fatal."""
    if is_death_outcome(outcome):
        return True
    roll_type=str(roll.get("roll_type") or "").strip().casefold()
    if roll_type.startswith("event") or roll_type.startswith("maternal"):
        return False
    aging_types={str(rule["roll_type"]).strip().casefold() for rule in autorolls.DEFAULT_AGING_RULES}
    aging_types.update({"being born","newborn","infant","toddler","child","preteen","teen","young adult","adult","elder death-age rng"})
    return roll_type in aging_types and str(outcome or "").strip().casefold() in {"bad result","dies","death"}

def random_death_for_roll(roll,actual_roll=None,connection=None):
    """Choose a stable valid date inside an event span or the roll's quarter."""
    due=int(roll.get("due_global_day") or current_gd())
    low=high=due
    source=roll.get("source_id")
    con=connection or connect()
    owns_connection=connection is None
    try:
        event=con.execute("SELECT start_global_day,end_global_day,event_name FROM events WHERE event_id=?",(source,)).fetchone()
        sim=con.execute("SELECT birth_global_day,death_global_day FROM sims WHERE sim_id=?",(roll.get("sim_id"),)).fetchone()
        cause=None
        if event:
            low=int(event[0] if event[0] is not None else due)
            high=int(event[1] if event[1] is not None else low)
            cause=str(event[2]) if death_causes.enabled(con) and event[2] else None
        else:
            low,high=autorolls.aging_death_window(
                con,roll.get("sim_id"),due,roll.get("roll_type"),actual_roll)
            cause=death_causes.choose(con,roll.get("roll_type"))
    finally:
        if owns_connection:
            con.close()
    low,high=sorted((low,high))
    if sim and sim[0] is not None:
        low=max(low,int(sim[0]))
    if sim and sim[1] is not None:
        high=min(high,int(sim[1]))
    if high<low:
        low=high=due
    death_gd=low+secrets.randbelow(high-low+1)
    minute=secrets.randbelow(24*60)
    sy,dpy=calendar_settings()
    exact=format_exact_date(global_day_time_to_date(death_gd,time(minute//60,minute%60),sy,dpy))
    return death_gd,exact,cause

def _event_applies(value,candidates,universal=("all","global","any","everywhere","everyone","all countries","all classes")):
    target="" if value is None or pd.isna(value) else str(value).strip().casefold()
    if not target or target in universal:
        return True
    texts=("" if candidate is None or pd.isna(candidate) else str(candidate).strip().casefold()
           for candidate in candidates)
    return any(text and (target in text or text in target) for text in texts)

def add_applicable_events(rows):
    """Attach applicable historical-event names using batched lookups."""
    if rows is None or len(rows)==0:
        return rows
    frame=rows.copy() if isinstance(rows,pd.DataFrame) else pd.DataFrame(rows)
    if frame.empty or "due_global_day" not in frame:
        return rows
    days=pd.to_numeric(frame["due_global_day"],errors="coerce").dropna()
    if days.empty:
        return rows
    events=q("""SELECT event_id,event_name,start_global_day,end_global_day,scope,location,affected_class
                FROM events WHERE COALESCE(active,1)=1 AND start_global_day<=? AND end_global_day>=?
                ORDER BY start_global_day,event_name""",(int(days.max()),int(days.min())))
    sim_ids=[str(value) for value in frame.get("sim_id",pd.Series(dtype=str)).dropna().unique() if str(value).strip()]
    sims={}
    if sim_ids:
        placeholders=",".join("?" for _ in sim_ids)
        sim_rows=q(f"""SELECT s.sim_id,s.birthplace,h.location AS household_location,h.social_class
                         FROM sims s LEFT JOIN households h ON h.household_id=s.current_household_id
                         WHERE s.sim_id IN ({placeholders})""",sim_ids)
        sims={str(row.sim_id):row for _,row in sim_rows.iterrows()}
    contexts=[]
    for _,roll in frame.iterrows():
        due=int(roll["due_global_day"])
        roll_sim_id=roll.get("sim_id")
        sim=sims.get("" if roll_sim_id is None or pd.isna(roll_sim_id) else str(roll_sim_id))
        names=[]
        for _,event in events.iterrows():
            if not (int(event.start_global_day)<=due<=int(event.end_global_day)):
                continue
            scope="" if event.get("scope") is None or pd.isna(event.get("scope")) else str(event.get("scope")).strip().casefold()
            location="" if event.get("location") is None or pd.isna(event.get("location")) else str(event.get("location")).strip().casefold()
            global_scope=scope.startswith("global") or location.startswith("global") or scope in {"world","worldwide","all","everyone","all sims"}
            if sim is not None:
                if not global_scope and not _event_applies(event.location,[sim.get("household_location"),sim.get("birthplace")]):
                    continue
                if not global_scope and not _event_applies(event.affected_class,[sim.get("social_class")]):
                    continue
            elif not global_scope and not (_event_applies(event.location,[]) and _event_applies(event.affected_class,[])):
                continue
            event_name=event.get("event_name")
            names.append(str(event.get("event_id") if event_name is None or pd.isna(event_name) or not str(event_name).strip() else event_name))
        contexts.append(" | ".join(dict.fromkeys(names)))
    frame["applicable_events"]=contexts
    return frame if isinstance(rows,pd.DataFrame) else frame.to_dict("records")
def sim_options(blank=True):
    df=q("SELECT sim_id,COALESCE(title,'') title,COALESCE(first_name,'') first_name,COALESCE(last_name,'') last_name FROM sims ORDER BY last_name,first_name")
    out=[f"{r.sim_id} — {' '.join(x for x in [r.title,r.first_name,r.last_name] if x).strip()}" for _,r in df.iterrows()]
    return ([""] if blank else [])+out
def sid(v): return v.split(" — ",1)[0] if v else None
def _sim_id_number(sim_id):
    match=re.search(r"(\d+)$",str(sim_id or ""))
    return int(match.group(1)) if match else -1

def remember_sim(sim_id):
    if not sim_id:return
    recent=[str(value) for value in st.session_state.get("recent_sim_ids",[]) if str(value)!=str(sim_id)]
    st.session_state["recent_sim_ids"]=[str(sim_id)]+recent[:7]

# Numeric ID descending is the default. Pages may opt into recent-first while
# retaining the same numeric order for every remaining Sim.
_legacy_sim_options=sim_options
def sim_options(blank=True,prefer_recent=False):
    df=q("SELECT sim_id,COALESCE(title,'') title,COALESCE(first_name,'') first_name,COALESCE(last_name,'') last_name FROM sims")
    records=df.to_dict("records")
    recent={value:index for index,value in enumerate(st.session_state.get("recent_sim_ids",[]))}
    use_recent=prefer_recent or st.session_state.get("sim_menu_order")=="Recently used first"
    records.sort(key=lambda row:(
        0 if use_recent and str(row.get("sim_id")) in recent else 1,
        recent.get(str(row.get("sim_id")),999),
        -_sim_id_number(row.get("sim_id")),
        str(row.get("sim_id") or ""),
    ))
    out=[f"{row['sim_id']} — {' '.join(x for x in [row['title'],row['first_name'],row['last_name']] if x).strip()}" for row in records]
    return ([""] if blank else [])+out

def sid(v): return re.split(r"\s+[—�]\s+",str(v),maxsplit=1)[0] if v else None

def opt_index(opts,s):
    if not s:return 0
    for i,o in enumerate(opts):
        if o.startswith(str(s)+" —"): return i
    return 0
def sid(v): return str(v).split(None,1)[0] if v else None

def opt_index(opts,s):
    if not s:return 0
    for index,option in enumerate(opts):
        if str(option).startswith(str(s)+" "):return index
    return 0

def int_or_none(v):
    try:return int(str(v).strip()) if str(v).strip() else None
    except:return None

def active_cache_key():
    return workspace,save_manager.active_save_id()

@st.cache_data(ttl=5,show_spinner=False)
def _cached_clock_settings(workspace_key,save_id):
    c=connect()
    try:
        return (
            int(float(setting(c,'start_year',1200))),
            int(float(setting(c,'days_per_year',4))),
            int(float(setting(c,'current_global_day',332))),
        )
    finally:
        c.close()

def calendar_settings():
    sy,dpy,_=_cached_clock_settings(*active_cache_key())
    return sy,dpy

def challenge_year_day(g):
    sy,dpy=calendar_settings()
    return global_day_to_year_day(g,sy,dpy)

def challenge_date_label(g):
    sy,dpy=calendar_settings()
    return global_day_label(g,sy,dpy)

def gd_caption(g):
    if g is None:return ""
    y,d=challenge_year_day(g)
    return f"Year {y} • Day {d} • {challenge_date_label(g)}"

def exact_date_from_ingame_time(global_day,clock_time):
    if global_day is None or clock_time is None:return None
    sy,dpy=calendar_settings()
    return format_exact_date(global_day_time_to_date(global_day,clock_time,sy,dpy))

SIM_WEEKDAYS=["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"]
def sim_weekday(g):
    """Global Day 1 is Sunday; the seven-day Sim week repeats continuously."""
    if g is None:return ""
    return SIM_WEEKDAYS[(int(g)-1)%7]

def current_gd():
    return _cached_clock_settings(*active_cache_key())[2]

def sync_auto_rolls(show_notice=False):
    """Idempotently create any missing rule-driven roll obligations."""
    con=connect()
    try:
        result=autorolls.sync_rolls(con,current_gd())
        planner_added=play_planner.sync_scheduled_rolls(con,current_gd())
        action_queue.sync(con,current_gd())
    finally:
        con.close()
    if show_notice:
        if result["added"] or planner_added:
            st.success(f"Auto-scheduled {result['added'] + planner_added} missing roll(s).")
        else:
            st.info("Roll schedule is already up to date.")
        if result.get("missing_rule_rows"):
            st.warning(f"{result['missing_rule_rows']} due roll(s) were scheduled without die/bad-result values because that year/species does not yet have a complete roll table.")
    return result

def rule_value(label,default=None):
    c=connect(); r=c.execute("SELECT col_b FROM rules WHERE row_label=? ORDER BY source_row DESC LIMIT 1",(label,)).fetchone(); c.close()
    return r[0] if r and r[0] not in (None,'') else default

def page_header(title,subtitle=None):
    st.markdown(
        f"<div class='v3-hero'><div class='v3-eyebrow'>Decades Tracker {APP_VERSION}</div>"
        f"<div class='v3-hero-title'>{html.escape(str(title))}</div>"
        f"<div class='v3-hero-copy'>{html.escape(str(subtitle or ''))}</div></div>",unsafe_allow_html=True)

def section_heading(title,note=""):
    st.markdown(f"<div class='v3-section'><div class='v3-section-title'>{html.escape(str(title))}</div><div class='v3-section-note'>{html.escape(str(note))}</div></div>",unsafe_allow_html=True)

def friendly_cards(rows,title,meta=(),body=None,badge=None,empty="Nothing to show here yet.",limit=50):
    records=rows.to_dict("records") if isinstance(rows,pd.DataFrame) else list(rows or [])
    if not records:
        st.markdown(f"<div class='v3-empty'>{html.escape(empty)}</div>",unsafe_allow_html=True); return
    # Build one compact HTML list instead of one Streamlit element per record.
    # Large directories previously produced dozens of frontend elements on every
    # interaction; batching them makes reruns substantially lighter.
    rendered=[]
    for row in records[:limit]:
        heading=title(row) if callable(title) else row.get(title)
        badge_text=(badge(row) if callable(badge) else row.get(badge)) if badge else ""
        meta_bits=[]
        for item in meta:
            label,value=item(row) if callable(item) else (item,row.get(item))
            if value not in (None,"",False) and pd.notna(value): meta_bits.append(f"<span><b>{html.escape(str(label))}</b> {html.escape(str(value))}</span>")
        body_text=(body(row) if callable(body) else row.get(body)) if body else ""
        rendered.append(
            f"<div class='v3-chronicle-row'><div class='v3-chronicle-title'>{html.escape(str(heading or 'Untitled'))}</div>"
            f"<div class='v3-chronicle-meta'>{''.join(meta_bits)}</div>"
            f"{f'<div class=\"v3-chronicle-badge\">{html.escape(str(badge_text))}</div>' if badge_text else '<span></span>'}"
            f"{f'<div class=\"v3-chronicle-body\">{html.escape(str(body_text))}</div>' if body_text else ''}</div>"
        )
    st.markdown("<div class='v3-chronicle-list'>"+"".join(rendered)+"</div>",unsafe_allow_html=True)
    if len(records)>limit: st.caption(f"Showing the first {limit} of {len(records)} items. Narrow the filters to see more.")

def chronicle_note(title,text):
    st.markdown(
        f"<div class='chronicle-note'><div class='chronicle-note-title'>{title}</div>"
        f"<div class='chronicle-note-text'>{text}</div></div>",
        unsafe_allow_html=True,
    )

def chronicle_value(value,fallback=""):
    return fallback if value is None or not pd.notna(value) or not str(value).strip() else str(value).strip()

def timeline_chronicle_entry(row,start_year,days_per_year):
    date=global_day_label(int(row["global_day"]),start_year,days_per_year)
    person=chronicle_value(row.get("primary_sim"))
    voice=f"I, {person}, record" if person else "The household chronicler records"
    details=chronicle_value(row.get("details"))
    subject=chronicle_value(row.get("title"),chronicle_value(row.get("category"),"an event of note"))
    sentence=f"{voice}: {subject}."
    if details:
        sentence+=f" {details.rstrip('.')} ."
    return f"{date} — {sentence}".replace(" .",".")

def roll_chronicle_entry(row,start_year,days_per_year):
    day=int(row.get("due_global_day") or 1)
    date=global_day_label(day,start_year,days_per_year)
    person=chronicle_value(row.get("sim_name"),chronicle_value(row.get("sim_id"),"an unnamed soul"))
    trial=chronicle_value(row.get("roll_type"),"the appointed trial")
    completed=row.get("completed")
    if pd.notna(completed) and bool(completed):
        result=chronicle_value(row.get("outcome"),"the result was entered")
        actual=chronicle_value(row.get("actual_roll"))
        lot=f" The lot cast was {actual}." if actual else ""
        return f"{date} — I, {person}, faced {trial}.{lot} {result.rstrip('.')}."
    return f"{date} — I, {person}, am called to face {trial}; the result has yet to be written."

def historical_dice_tray(required_die,key_prefix,result_key,bad_results=None):
    notation=chronicle_value(required_die)
    spec=dice_roller.parse(notation)
    rng_bounds=[int(n) for n in re.findall(r"\d+",str(bad_results or ""))[:2]] if notation.casefold()=="rng" else []
    shown=notation if spec else "the common dice set"
    st.markdown(
        f"<div class='dice-case'><div class='dice-case-title'>⚜ The carved dice case · {shown}</div>"
        "<div class='dice-case-note'>The appointed die is selected from the rule table. Every control has a written label and may be reached by keyboard.</div></div>",
        unsafe_allow_html=True,
    )
    if len(rng_bounds)==2:
        low,high=sorted(rng_bounds)
        if st.button(f"Draw a number from {low} to {high}",key=f"{key_prefix}_rng",use_container_width=True):
            result=low+secrets.randbelow(high-low+1)
            st.session_state[result_key]=str(result)
            st.session_state[f"{key_prefix}_detail"]=f"RNG {low}–{high}: {result}"
        choices=[]
    else:
        choices=[spec["sides"]] if spec else list(dice_roller.SUPPORTED_DICE)
    # Range-based RNG rolls render their own control above and intentionally
    # have no dice buttons. Streamlit rejects st.columns(0).
    if not choices:
        if st.session_state.get(f"{key_prefix}_detail"):
            st.success(f"Cast result â€” {st.session_state[f'{key_prefix}_detail']}")
        return
    columns=st.columns(len(choices))
    for column,sides in zip(columns,choices):
        roll_notation=notation if spec else f"d{sides}"
        if column.button(
            f"Cast {roll_notation}",key=f"{key_prefix}_d{sides}",use_container_width=True,
            help=f"Roll {roll_notation} and place the total in the Actual roll field.",
        ):
            result=dice_roller.roll(roll_notation)
            st.session_state[result_key]=str(result["total"])
            detail=" + ".join(str(value) for value in result["rolls"])
            modifier=result["modifier"]
            st.session_state[f"{key_prefix}_detail"]=(
                f"{roll_notation}: {detail}{f' {modifier:+d}' if modifier else ''} = {result['total']}"
            )
    if st.session_state.get(f"{key_prefix}_detail"):
        st.success(f"Cast result — {st.session_state[f'{key_prefix}_detail']}")

def friendly_df(df,rename=None,cols=None):
    out=df.copy()
    if cols:
        cols=[c for c in cols if c in out.columns]
        out=out[cols]
    if rename:
        out=out.rename(columns=rename)
    return out

def status_badge(text):
    return f"<span class='pill'>{text}</span>"

@st.cache_data(show_spinner=False,max_entries=256)
def compressed_thumbnail(image_data,max_size=150):
    """Return a small web thumbnail while preserving the original portrait."""
    if not image_data:return None
    try:
        from PIL import Image
        image=Image.open(io.BytesIO(bytes(image_data)))
        image.thumbnail((int(max_size),int(max_size)))
        if image.mode not in ("RGB","L"):
            background=Image.new("RGB",image.size,"#171512")
            if "A" in image.getbands(): background.paste(image,mask=image.getchannel("A"))
            else: background.paste(image)
            image=background
        output=io.BytesIO(); image.save(output,format="WEBP",quality=68,method=4)
        return output.getvalue()
    except Exception:
        return bytes(image_data)

@st.cache_resource(show_spinner=False)
def _ensure_optional_features(workspace_key,save_id,release="2026-08-17-play-planner-v1"):
    """Run migrations once per deployed process/save, not on every page click."""
    con=connect()
    try:
        profiles.ensure_schema(con)
        relationship_photos.ensure_schema(con)
        notebook.ensure_schema(con)
        illnesses.ensure_schema(con)
        cm.ensure_schema(con)
        event_library.ensure_event_library(con)
        action_queue.ensure_schema(con)
        death_causes.ensure_schema(con)
        play_planner.ensure_schema(con)
        cloud_schema.ensure_game_sync_schema(con)
        cloud_schema.ensure_performance_indexes(con)
        action_queue.seed_event_configs(con)
        autorolls.repair_generated_roll_dice(con)
        # Reconcile imported/approved event rolls once for each save on every
        # deployment, even when the player has not advanced the calendar yet.
        autorolls.sync_rolls(con,int(float(setting(con,"current_global_day",1))))
        action_queue.sync(con,int(float(setting(con,"current_global_day",1))))
    finally:
        con.close()
    return True

_ensure_optional_features(*active_cache_key())

with st.sidebar:
    st.markdown(
        "<div class='sidebar-brand'><div class='sidebar-brand-title'>🏰 Decades</div>"
        f"<div class='sidebar-brand-subtitle'>Your living family chronicle · {APP_VERSION}</div></div>",
        unsafe_allow_html=True,
    )

    saves=save_manager.list_saves()
    active_id=save_manager.active_save_id()
    save_labels=[f"{s['name']}  •  {s['save_id']}" for s in saves]
    active_index=next((i for i,s in enumerate(saves) if s["save_id"]==active_id),0)
    selected_save_label=st.selectbox("Active save",save_labels,index=active_index,key="sidebar_save_selector")
    selected_save_id=selected_save_label.rsplit("  •  ",1)[-1]
    if selected_save_id!=active_id:
        save_manager.set_active(selected_save_id)
        st.session_state["active_save_id"]=selected_save_id
        st.rerun()

    st.caption(f"💾 {save_manager.active_save()['name']}")
    st.selectbox("Sim menu order",["Highest ID first","Recently used first"],key="sim_menu_order",
                 help="Recent mode keeps numeric descending order after this session's recently used Sims.")
    nav_labels={
        "🏠 Today":"Today",
        "🕰️ Game Clock Sync":"Game Clock Sync",
        "👤 Sims":"Sims",
        "🌳 Family Tree":"Family Tree",
        "🕰️ Timeline":"Timeline",
        "🤰 Pregnancies":"Pregnancies",
        "🎲 Rolls":"Rolls",
        "💍 Relationships":"Relationships",
        "🏘️ Households":"Households",
        "📜 Events":"Events",
        "🩺 Illnesses":"Illnesses",
        "📊 Statistics":"Statistics",
        "📓 Notes":"Notes",
        "🌿 Planting Reference":"Planting Reference",
        "📚 Challenge Guides":"Challenge Guides",
        "💾 Saves":"Saves",
        "⚙️ Rules & Data":"Rules & Data",
        "✅ Rules Health":"Rules Health",
    }
    nav_labels["Challenge Management"]="Challenge Management"
    nav_labels=navigation_labels()
    page=st.session_state.get("active_tracker_page","Today")
    if page not in nav_labels.values():page="Today"
    sidebar_counts=today_counts(current_gd())
    badge_counts={
        "Today":sum(sidebar_counts),"Rolls":sidebar_counts[0],
        "Pregnancies":sidebar_counts[1],"Events":sidebar_counts[2],
        "Illnesses":sidebar_counts[3],
    }
    for group,pages in grouped_pages():
        st.markdown(f"<div class='v3-nav-group'>{html.escape(group)}</div>",unsafe_allow_html=True)
        for spec in pages:
            count=badge_counts.get(spec.name,0)
            label=f"{spec.label}{f'  ·  {count}' if count else ''}"
            if st.button(label,key=f"nav_{spec.name}",use_container_width=True,
                         type="primary" if page==spec.name else "secondary"):
                st.session_state["active_tracker_page"]=spec.name
                st.rerun()
    st.divider()
    cg_sidebar=current_gd()
    cy_sidebar,cd_sidebar=challenge_year_day(cg_sidebar)
    st.metric("Global Day",cg_sidebar)
    st.caption(f"Year {cy_sidebar} • Challenge Day {cd_sidebar} • {sim_weekday(cg_sidebar)}")
    st.caption("✓ Automatic roll scheduling on")
    st.caption(f"Decades Tracker v{APP_VERSION}")
    st.divider()
    st.markdown("**Help preserve the chronicle**")
    st.caption("If this tracker enriches your challenge, you can help support its hosting and continued development.")
    st.link_button("☕ Support SeveralUDO on Ko-fi","https://ko-fi.com/SeveralUDO",use_container_width=True)
    workspace_access.render_account_settings(st,workspace)
    if st.button("Lock private workspace",use_container_width=True):
        workspace_access.sign_out(st)

# The game-clock receiver writes these rows outside Streamlit, so read the next
# candidate directly instead of using the short-lived query cache. This keeps
# the prompt responsive while leaving the rest of the app's caching intact.
_candidate_con=connect()
try:
    _candidate_row=_candidate_con.execute("""SELECT detection_id,game_sim_id,first_name,last_name,sex,age_stage,is_baby,
        game_day,game_hour,game_minute,birth_global_day,household_name
        FROM game_birth_candidates WHERE status='pending'
        ORDER BY detected_at,detection_id LIMIT 1""").fetchone()
finally:
    _candidate_con.close()

if _candidate_row:
    _candidate={key:_candidate_row[key] for key in (
        "detection_id","game_sim_id","first_name","last_name","sex","age_stage","is_baby",
        "game_day","game_hour","game_minute","birth_global_day","household_name"
    )}
    _is_detected_baby=bool(_candidate["is_baby"])

    @st.dialog("New baby detected!" if _is_detected_baby else "New Sim detected!")
    def _review_detected_sim():
        # Dialog interactions rerun independently from the main app, just like
        # fragments. Restore the workspace ContextVars before date helpers,
        # queries, and form controls access the active save.
        dialog_workspace=st.session_state.get("workspace_id")
        if not dialog_workspace:
            st.warning("This private workspace is locked. Reopen it to review the detected Sim.")
            return
        save_manager.set_workspace(dialog_workspace,st.session_state.get("active_save_id"))
        st.write("Would you like to add this Sim to the tracker?")
        detected_time=(
            f"{int(_candidate['game_hour']):02d}:{int(_candidate['game_minute']):02d}"
            if _candidate["game_hour"] is not None and _candidate["game_minute"] is not None else "time unavailable"
        )
        st.caption(
            f"Detected in {_candidate['household_name'] or 'the active household'} at "
            f"game day {_candidate['game_day']} · {detected_time} · {_candidate['age_stage'] or 'unknown life stage'}"
        )
        if not _is_detected_baby:
            st.info("The birth Global Day is estimated from this Sim's current life stage. You can correct it before adding them.")

        with st.form(f"detected_sim_{_candidate['detection_id']}"):
            a,b=st.columns(2)
            first=a.text_input("First name",value=_candidate["first_name"] or "")
            last=b.text_input("Last name",value=_candidate["last_name"] or "")
            a,b=st.columns(2)
            sex_values=["Female","Male","Other","Unknown"]
            detected_sex_text=str(_candidate["sex"] or "Unknown").casefold()
            detected_sex=("Female" if "female" in detected_sex_text else
                          "Male" if "male" in detected_sex_text else
                          "Other" if "other" in detected_sex_text else "Unknown")
            sex=a.selectbox("Gender / sex",sex_values,index=sex_values.index(detected_sex) if detected_sex in sex_values else 3)
            birth_gd=b.number_input("Birth Global Day",min_value=-10000,max_value=20000,
                                    value=int(_candidate["birth_global_day"] or current_gd()),step=1)
            st.caption("Estimated birth: "+gd_caption(birth_gd))

            opts=sim_options()
            hdf=q("SELECT household_id,household_name FROM households ORDER BY household_name,household_id")
            hopts=[""]+[f"{r.household_id} — {r.household_name or ''}" for _,r in hdf.iterrows()]
            detected_household=str(_candidate["household_name"] or "").strip().casefold()
            household_index=next((i for i,label in enumerate(hopts)
                                  if detected_household and label.split(" — ",1)[-1].strip().casefold()==detected_household),0)
            a,b,c=st.columns(3)
            mother=a.selectbox("Mother",opts,key=f"detected_mother_{_candidate['detection_id']}")
            father=b.selectbox("Father",opts,key=f"detected_father_{_candidate['detection_id']}")
            household=c.selectbox("Household",hopts,index=household_index,
                                  key=f"detected_household_{_candidate['detection_id']}")
            birth_status=st.selectbox(
                "How did this Sim join the family?",
                ["Naturally Born","Married In","Adopted In","Other Partner","Other","Unknown"],
                index=0 if _is_detected_baby else 1,
            )
            add_sim=st.form_submit_button("Add this Sim",type="primary",use_container_width=True)

        if add_sim:
            if not first.strip() and not last.strip():
                st.error("Enter at least a first or last name.")
            else:
                con=connect()
                try:
                    tracker_sim_id=next_id(con,"sims","sim_id","SIM")
                    parent_ids=[value for value in (sid(mother),sid(father)) if value]
                    generation=0
                    if parent_ids:
                        marks=",".join("?" for _ in parent_ids)
                        generations=con.execute(
                            f"SELECT generation FROM sims WHERE sim_id IN ({marks}) AND generation IS NOT NULL",
                            tuple(parent_ids),
                        ).fetchall()
                        if generations:
                            generation=max(int(row[0]) for row in generations)+1
                    # Exact time is a true birth time only for a newly detected
                    # baby. For older Sims, retain the estimated day without
                    # pretending their current clock time was their birth time.
                    birth_date=None
                    if _is_detected_baby and _candidate["game_hour"] is not None and _candidate["game_minute"] is not None:
                        birth_date=exact_date_from_ingame_time(
                            int(birth_gd),time(int(_candidate["game_hour"]),int(_candidate["game_minute"]))
                        )
                    note=(f"Detected automatically from The Sims 4 ({_candidate['age_stage'] or 'unknown stage'}; "
                          f"game Sim ID {_candidate['game_sim_id']}).")
                    con.execute("""INSERT INTO sims(
                        sim_id,include_in_tree,first_name,last_name,sex,generation,mother_id,father_id,
                        birth_global_day,birth_date,birth_status,multiple_birth,notes,current_household_id,
                        legitimate,species_occult
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (tracker_sim_id,1,first.strip() or None,last.strip() or None,sex,generation,
                     sid(mother),sid(father),int(birth_gd),birth_date,birth_status,
                     "Single" if _is_detected_baby else "Unknown",note,sid(household),1,"Human"))
                    con.execute("""UPDATE game_birth_candidates SET status='added',resolved_at=?,created_sim_id=?
                                   WHERE detection_id=? AND status='pending'""",
                                (pd.Timestamp.now(tz="UTC").isoformat(),tracker_sim_id,_candidate["detection_id"]))
                    con.commit()
                    scheduled=autorolls.schedule_sim_lifecycle(con,tracker_sim_id,current_gd())
                finally:
                    con.close()
                st.success(f"Added {first} {last} and scheduled {scheduled} lifecycle roll(s).")
                st.rerun()

        if st.button("Skip this Sim",use_container_width=True,key=f"skip_detected_{_candidate['detection_id']}"):
            con=connect()
            con.execute("UPDATE game_birth_candidates SET status='dismissed',resolved_at=? WHERE detection_id=?",
                        (pd.Timestamp.now(tz="UTC").isoformat(),_candidate["detection_id"]))
            con.commit(); con.close(); st.rerun()

    _review_detected_sim()

# Show one automatic-discovery dialog at a time. Pregnancy candidates remain
# pending while a newly detected Sim/baby is being reviewed first.
if not _candidate_row:
    _pregnancy_candidate_con=connect()
    try:
        _pregnancy_candidate_row=_pregnancy_candidate_con.execute("""SELECT
            detection_id,game_sim_id,first_name,last_name,partner_game_sim_id,
            partner_first_name,partner_last_name,pregnancy_progress,game_day,game_hour,game_minute,
            conception_global_day,due_global_day,babies_expected,household_name
            FROM game_pregnancy_candidates WHERE status='pending'
            ORDER BY detected_at,detection_id LIMIT 1""").fetchone()
    finally:
        _pregnancy_candidate_con.close()

    if _pregnancy_candidate_row:
        _pregnancy_candidate={key:_pregnancy_candidate_row[key] for key in (
            "detection_id","game_sim_id","first_name","last_name","partner_game_sim_id",
            "partner_first_name","partner_last_name","pregnancy_progress","game_day","game_hour",
            "game_minute","conception_global_day","due_global_day","babies_expected","household_name"
        )}

        @st.dialog("New pregnancy detected!")
        def _review_detected_pregnancy():
            dialog_workspace=st.session_state.get("workspace_id")
            if not dialog_workspace:
                st.warning("This private workspace is locked. Reopen it to review the detected pregnancy.")
                return
            save_manager.set_workspace(dialog_workspace,st.session_state.get("active_save_id"))
            pregnant_name=" ".join(filter(None,(
                _pregnancy_candidate["first_name"],_pregnancy_candidate["last_name"]
            ))) or "A household Sim"
            st.write(f"The game reports that **{pregnant_name}** is pregnant. Add this pregnancy to the tracker?")
            detected_time=(
                f"{int(_pregnancy_candidate['game_hour']):02d}:{int(_pregnancy_candidate['game_minute']):02d}"
                if _pregnancy_candidate["game_hour"] is not None and _pregnancy_candidate["game_minute"] is not None
                else "time unavailable"
            )
            progress=_pregnancy_candidate["pregnancy_progress"]
            progress_text=(f"{round(float(progress)*100)}% progress" if progress is not None else "progress unavailable")
            st.caption(
                f"Detected in {_pregnancy_candidate['household_name'] or 'the active household'} at "
                f"game day {_pregnancy_candidate['game_day']} · {detected_time} · {progress_text}. "
                "You can correct every estimate before adding it."
            )
            options=sim_options()

            def _matching_sim_index(first_name,last_name):
                wanted=" ".join(filter(None,(first_name,last_name))).strip().casefold()
                if not wanted:return 0
                return next((index for index,label in enumerate(options)
                             if label.split("—",1)[-1].strip().casefold()==wanted),0)

            mother_index=_matching_sim_index(_pregnancy_candidate["first_name"],_pregnancy_candidate["last_name"])
            father_index=_matching_sim_index(_pregnancy_candidate["partner_first_name"],_pregnancy_candidate["partner_last_name"])
            with st.form(f"detected_pregnancy_{_pregnancy_candidate['detection_id']}"):
                a,b=st.columns(2)
                mother=a.selectbox("Pregnant Sim",options,index=mother_index)
                father=b.selectbox("Other parent (if known)",options,index=father_index)
                a,b=st.columns(2)
                conception=a.number_input("Estimated conception Global Day",min_value=-10000,max_value=20000,
                    value=int(_pregnancy_candidate["conception_global_day"] or current_gd()),step=1)
                pregnancy_length=max(1,int(float(rule_value("Pregnancy Length (challenge days)",3))))
                suggested_due=int(_pregnancy_candidate["due_global_day"] or (int(conception)+pregnancy_length))
                due=b.number_input("Estimated due Global Day",min_value=-10000,max_value=20000,
                    value=suggested_due,step=1)
                expected=st.number_input("Babies expected",1,10,
                    int(_pregnancy_candidate["babies_expected"] or 1),step=1)
                add_pregnancy=st.form_submit_button("Add this pregnancy",type="primary",use_container_width=True)

            if add_pregnancy:
                mother_id=sid(mother)
                father_id=sid(father)
                if not mother_id:
                    st.error("Choose the pregnant Sim before adding this pregnancy.")
                else:
                    con=connect()
                    try:
                        pregnancy_id=next_id(con,"pregnancies","pregnancy_id","PREG")
                        names={row[0]:row[1] for row in con.execute("""SELECT sim_id,
                            TRIM(COALESCE(title,'')||' '||COALESCE(first_name,'')||' '||COALESCE(last_name,''))
                            FROM sims""")}
                        notes=(f"Detected automatically from The Sims 4 at game day "
                               f"{_pregnancy_candidate['game_day']} ({progress_text}).")
                        con.execute("""INSERT INTO pregnancies(
                            pregnancy_id,mother_id,mother_name,father_id,father_name,
                            conception_global_day,due_global_day,babies_expected,status,notes)
                            VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (pregnancy_id,mother_id,names.get(mother_id),father_id,names.get(father_id),
                             int(conception),int(due),int(expected),"Pregnant",notes))
                        con.execute("""UPDATE game_pregnancy_candidates SET status='added',resolved_at=?,
                            created_pregnancy_id=? WHERE detection_id=? AND status='pending'""",
                            (pd.Timestamp.now(tz="UTC").isoformat(),pregnancy_id,
                             _pregnancy_candidate["detection_id"]))
                        con.commit()
                    finally:
                        con.close()
                    sync_auto_rolls(show_notice=False)
                    st.success(f"Added {pregnancy_id}; pregnancy and maternal rolls are now scheduled.")
                    st.rerun()

            if st.button("Skip this pregnancy",use_container_width=True,
                         key=f"skip_detected_pregnancy_{_pregnancy_candidate['detection_id']}"):
                con=connect()
                con.execute("""UPDATE game_pregnancy_candidates SET status='dismissed',resolved_at=?
                    WHERE detection_id=?""",
                    (pd.Timestamp.now(tz="UTC").isoformat(),_pregnancy_candidate["detection_id"]))
                con.commit();con.close();st.rerun()

        _review_detected_pregnancy()

def undo_today_action(action):
    con=connect()
    try:
        kind=action.get("kind")
        if kind=="roll":
            con.execute("UPDATE rolls SET actual_roll=?,outcome=?,completed=?,completed_global_day=? WHERE roll_id=?",
                        (action.get("actual_roll"),action.get("outcome"),action.get("completed"),
                         action.get("completed_global_day"),action.get("roll_id")))
            con.execute("UPDATE action_queue SET status=?,updated_at=? WHERE roll_id=?",
                        (action.get("queue_status","open"),str(pd.Timestamp.utcnow()),action.get("roll_id")))
            if action.get("sim_id"):
                con.execute("UPDATE sims SET death_global_day=?,death_date=?,cause_of_death=? WHERE sim_id=?",
                            (action.get("death_global_day"),action.get("death_date"),
                             action.get("cause_of_death"),action.get("sim_id")))
        elif kind=="pregnancy":
            con.execute("""UPDATE pregnancies SET status=?,babies_delivered=?,delivery_date=?,outcome=?,complication=?
                           WHERE pregnancy_id=?""",
                        (action.get("status"),action.get("babies_delivered"),action.get("delivery_date"),
                         action.get("outcome"),action.get("complication"),action.get("pregnancy_id")))
        elif kind=="illness":
            con.execute("UPDATE illnesses SET status=?,end_global_day=? WHERE illness_id=?",
                        (action.get("status"),action.get("end_global_day"),action.get("illness_id")))
        elif kind=="death":
            con.execute("""UPDATE sims SET death_global_day=?,death_date=?,death_place=?,cause_of_death=?
                           WHERE sim_id=?""",
                        (action.get("death_global_day"),action.get("death_date"),action.get("death_place"),
                         action.get("cause_of_death"),action.get("sim_id")))
        con.commit()
    finally:
        con.close()

@workspace_fragment
def render_today():
    page_header("Today","Your play-session dashboard: advance time, handle what is due, and see what comes next.")
    undo=st.session_state.get("today_undo")
    if undo:
        a,b=st.columns([4,1])
        a.info(f"Last action: {undo.get('label','Today update')}")
        if b.button("Undo",key="today_undo_button",use_container_width=True):
            undo_today_action(undo); st.session_state.pop("today_undo",None)
            st.success("The last Today action was restored."); st.rerun()
    g0=current_gd()
    y0,d0=challenge_year_day(g0)

    # Main time control
    col_gd,col_year,col_challenge_day,col_weekday,col_date=st.columns([1.35,0.9,0.9,0.95,1.55])
    with col_gd:
        g=st.number_input("Current Global Day",min_value=-10000,max_value=20000,value=g0,step=1,
                          help="Use this as your main game-time input.")
    historical_year,challenge_day=challenge_year_day(g)
    col_year.metric("Historical year",historical_year)
    col_challenge_day.metric("Challenge day",challenge_day)
    col_weekday.metric("Sim weekday",sim_weekday(g))
    col_date.metric("Date range",challenge_date_label(g))
    if g!=g0:
        if st.button("Save new Global Day & refresh schedule",type="primary",use_container_width=True):
            cdb=connect(); set_setting(cdb,'current_global_day',g); cdb.close()
            _cached_clock_settings.clear()
            sync_auto_rolls(show_notice=False)
            st.success("Time advanced and the automatic roll schedule was refreshed.")
            st.rerun()

    # Collapsed expanders still execute on every Streamlit rerun, so keep this
    # secondary editor out of the hot path until it is requested.
    edit_focus=st.checkbox("Edit current household & heir",False,key="today_edit_focus")
    if edit_focus:
        cdb=connect(); heir=setting(cdb,'current_heir_id','SIM-0181'); hh=setting(cdb,'main_household_id','HH-0035'); cdb.close()
        opts=sim_options(prefer_recent=True); hhs=q("SELECT household_id,household_name FROM households ORDER BY household_name,household_id")
        a,b=st.columns(2)
        with a:
            nv=st.selectbox("Current heir",opts,index=opt_index(opts,heir))
            if st.button("Save heir",use_container_width=True):
                cdb=connect(); set_setting(cdb,'current_heir_id',sid(nv)); cdb.close(); st.success("Heir saved.")
        with b:
            hh_opts=[""]+[f"{r.household_id} — {r.household_name or ''}" for _,r in hhs.iterrows()]
            idx=next((i for i,o in enumerate(hh_opts) if o.startswith(str(hh)+" —")),0)
            nvhh=st.selectbox("Main household",hh_opts,index=idx)
            if st.button("Save household",use_container_width=True):
                cdb=connect(); set_setting(cdb,'main_household_id',sid(nvhh)); cdb.close(); st.success("Household saved.")

    rolls_count,preg_count,event_count,sick_count,death_count=today_counts(g)

    section_heading("What needs you now","Choose a category and complete one task at a time")
    a,b,c,d,e=st.columns(5)
    a.metric("Rolls due",rolls_count)
    b.metric("Pregnancies due",preg_count)
    c.metric("Active historical events",event_count)
    d.metric("Active illnesses",sick_count)
    e.metric("Deaths today",death_count)

    filter_col,density_col=st.columns([3,1])
    with filter_col:
        due_scope=st.segmented_control("Due window",["Due + overdue","Today only","Overdue only"],
                                       default="Due + overdue",key="today_due_scope")
    with density_col:
        card_density=st.selectbox("List spacing",["Comfortable","Compact"],key="today_card_density")
    if card_density=="Compact":
        st.markdown("<style>.v3-chronicle-row{padding:.38rem .65rem}.v3-chronicle-body{display:none}</style>",unsafe_allow_html=True)
    due_operator={"Today only":"=","Overdue only":"<"}.get(due_scope,"<=")

    task_view=st.segmented_control("Today task",[
        f"🎲 Rolls due ({rolls_count})",
        f"🤰 Pregnancies due ({preg_count})",
        f"📜 Active events ({event_count})",
        f"🩺 Illnesses ({sick_count})",
        f"⚰ Deaths ({death_count})"
    ],default=None,label_visibility="collapsed",key="today_task_view") or "Rolls due"

    if "Rolls due" in task_view:
        roll_kind=st.selectbox("Roll filter",["All","Event","Aging","Pregnancy","Planner"],key="today_roll_kind")
        roll_page_size=50
        filtered_count=scalar(f"SELECT COUNT(*) FROM action_queue WHERE status='open' AND source_type='roll' AND due_global_day{due_operator}? AND (?='All' OR category=?)",(g,roll_kind,roll_kind))
        roll_pages=max(1,(filtered_count+roll_page_size-1)//roll_page_size)
        roll_page=st.number_input("Roll page",1,roll_pages,1,key="today_roll_page") if roll_pages>1 else 1
        due=q(f"""SELECT r.roll_id,r.due_global_day,r.sim_id,r.sim_name,r.source_id,r.roll_type,r.die,
                        r.bad_results,r.actual_roll,r.outcome
                 FROM action_queue a JOIN rolls r ON r.roll_id=a.roll_id
                 WHERE a.status='open' AND a.source_type='roll' AND a.due_global_day{due_operator}?
                   AND (?='All' OR a.category=?)
                 ORDER BY a.priority,a.due_global_day,r.sim_name,r.roll_type LIMIT ? OFFSET ?""",
              (g,roll_kind,roll_kind,roll_page_size,(int(roll_page)-1)*roll_page_size))
        due=add_applicable_events(due)
        if roll_pages>1:
            st.caption(f"Showing page {int(roll_page)} of {roll_pages}. Every due roll remains available without loading the entire backlog at once.")
        if due.empty:
            st.success("No rolls are due right now.")
        else:
            friendly_cards(due,lambda r:r.get("roll_type") or "Scheduled roll",
                meta=(lambda r:("Sim",r.get("sim_name") or r.get("sim_id")),lambda r:("Due",f"Global Day {r.get('due_global_day')}"),lambda r:("Die",r.get("die"))),
                body=lambda r:(f"Historical context: {r.get('applicable_events')}\n\n" if r.get('applicable_events') else "")+
                              f"Bad results: {r.get('bad_results') or 'Use the current rule table'}",badge="roll_id")
            st.markdown("**Record a result**")
            labels=[f"{r.roll_id} — {r.sim_name or r.sim_id} — {r.roll_type} (GD {r.due_global_day})" for _,r in due.iterrows()]
            pick=st.selectbox("Choose roll",labels,key="today_roll_pick")
            rid=pick.split(" — ",1)[0]
            rr=due[due.roll_id==rid].iloc[0]
            remember_sim(rr.get("sim_id"))
            selected_photo=cached_sim_photo(rr.get("sim_id"))
            if selected_photo:
                st.image(compressed_thumbnail(selected_photo["image_data"],150),width=150,
                         caption=rr.get("sim_name") or rr.get("sim_id"))
            if rr.get("applicable_events"):
                st.info(f"Applicable historical events: {rr.get('applicable_events')}")
            actual_key=f"today_roll_actual_{rid}"
            outcome_key=f"today_roll_outcome_{rid}"
            historical_dice_tray(rr.get("die"),f"today_dice_{rid}",actual_key,rr.get("bad_results"))
            a,b=st.columns(2)
            actual=a.text_input("Actual roll",key=actual_key)
            automatic=roll_outcomes.automatic_outcome(actual,rr.get("bad_results"),rr.get("roll_type"),rr.get("die"))
            if automatic is not None:
                st.session_state[outcome_key]=automatic
            outcome=b.text_input("Outcome",value=automatic or "",key=outcome_key,
                                 help="Calculated from the editable Bad results rule when a numeric roll is entered.")
            if st.button("Save & complete roll",type="primary",key="today_roll_save",use_container_width=True):
                con=connect()
                old=con.execute("SELECT actual_roll,outcome,completed,completed_global_day FROM rolls WHERE roll_id=?",(rid,)).fetchone()
                old_queue=con.execute("SELECT status FROM action_queue WHERE roll_id=?",(rid,)).fetchone()
                old_sim=(con.execute("SELECT death_global_day,death_date,cause_of_death FROM sims WHERE sim_id=?",(rr.get("sim_id"),)).fetchone()
                         if rr.get("sim_id") else None)
                st.session_state["today_undo"]={
                    "kind":"roll","label":f"Completed {rid}","roll_id":rid,
                    "actual_roll":old[0] if old else None,"outcome":old[1] if old else None,
                    "completed":old[2] if old else 0,"completed_global_day":old[3] if old else None,
                    "queue_status":old_queue[0] if old_queue else "open","sim_id":rr.get("sim_id"),
                    "death_global_day":old_sim[0] if old_sim else None,"death_date":old_sim[1] if old_sim else None,
                    "cause_of_death":old_sim[2] if old_sim else None,
                }
                con.execute("UPDATE rolls SET actual_roll=?,outcome=?,completed=1,completed_global_day=? WHERE roll_id=?",
                            (actual or None,outcome or None,g,rid))
                con.execute("UPDATE action_queue SET status='complete',updated_at=? WHERE roll_id=?",
                            (str(pd.Timestamp.utcnow()),rid))
                death_record=None
                if rr.get("sim_id") and should_record_roll_death(rr,outcome):
                    death_record=random_death_for_roll(rr,actual,connection=con)
                    con.execute("""UPDATE sims SET death_global_day=COALESCE(death_global_day,?),
                                   death_date=COALESCE(death_date,?),cause_of_death=COALESCE(cause_of_death,?)
                                   WHERE sim_id=?""",(*death_record,rr.get("sim_id")))
                con.commit(); con.close()
                if death_record:
                    st.success(f"Completed {rid}. Death recorded as {death_record[1]} (Global Day {death_record[0]}).")
                else:
                    st.success(f"Completed {rid}.")
                rerun_current_fragment()

    if "Pregnancies due" in task_view:
        preg=q(f"""SELECT pregnancy_id,mother_id,mother_name,father_name,conception_global_day,due_global_day,
                         babies_expected,status,outcome FROM pregnancies WHERE due_global_day<=?
                  AND COALESCE(status,'') NOT IN ('Delivered','Cancelled','Complete','Miscarriage','Stillbirth')
                  ORDER BY due_global_day,mother_name""".replace("due_global_day<=?",f"due_global_day{due_operator}?"),(g,))
        if preg.empty:
            st.success("No pregnancies are due right now.")
        else:
            friendly_cards(preg,lambda r:f"{r.get('mother_name') or r.get('mother_id')} is due",
                meta=(lambda r:("Father",r.get("father_name")),lambda r:("Due",f"Global Day {r.get('due_global_day')}"),lambda r:("Babies expected",r.get("babies_expected"))),
                badge=lambda r:r.get("status") or "Active")
            st.markdown("**Record an outcome**")
            labels=[f"{r.pregnancy_id} — {r.mother_name or r.mother_id} (GD {r.due_global_day})" for _,r in preg.iterrows()]
            pick=st.selectbox("Choose pregnancy",labels,key="today_preg_pick")
            pid=pick.split(" — ",1)[0]
            a,b=st.columns(2)
            status=a.selectbox("Outcome",["Delivered","Miscarriage","Stillbirth","Complete","Other"],key="today_preg_status")
            babies=b.number_input("Babies delivered",0,10,1,key="today_preg_babies")
            a,b=st.columns(2)
            outcome=a.text_input("Outcome summary",key="today_preg_outcome")
            complication=b.text_input("Complication",key="today_preg_complication")
            if st.button("Save pregnancy outcome",type="primary",key="today_preg_save",use_container_width=True):
                con=connect()
                old=con.execute("""SELECT status,babies_delivered,delivery_date,outcome,complication
                                   FROM pregnancies WHERE pregnancy_id=?""",(pid,)).fetchone()
                st.session_state["today_undo"]={
                    "kind":"pregnancy","label":f"Updated {pid}","pregnancy_id":pid,
                    "status":old[0] if old else None,"babies_delivered":old[1] if old else None,
                    "delivery_date":old[2] if old else None,"outcome":old[3] if old else None,
                    "complication":old[4] if old else None,
                }
                con.execute("""UPDATE pregnancies SET status=?,babies_delivered=?,delivery_date=?,outcome=?,complication=?
                               WHERE pregnancy_id=?""",
                            (status,babies,gd_caption(g),outcome or None,complication or None,pid))
                con.commit(); con.close()
                sync_auto_rolls(show_notice=False)
                st.success(f"Updated {pid}.")
                st.rerun()

    if "Active events" in task_view:
        active=q("""SELECT event_id,event_name,scope,location,roll_required,affected_class FROM events
                    WHERE start_global_day<=? AND end_global_day>=? ORDER BY event_name""",(g,g))
        if active.empty:
            st.success("No historical events are active today.")
        else:
            friendly_cards(active,"event_name",meta=("scope","location","affected_class"),
                badge=lambda r:"Roll required" if r.get("roll_required") else "In effect")

    if "Illnesses" in task_view:
        sick=q("""SELECT illness_id,sim_id,sim_name,illness_name,onset_global_day,status,severity,contagious,treatment
                  FROM illnesses WHERE onset_global_day<=?
                    AND COALESCE(status,'Active') IN ('Active','Improving','Worsening','Chronic')
                    AND (end_global_day IS NULL OR end_global_day>=?)
                  ORDER BY CASE severity WHEN 'Critical' THEN 1 WHEN 'Severe' THEN 2 WHEN 'Moderate' THEN 3 ELSE 4 END,
                           onset_global_day,sim_name""",(g,g))
        if sick.empty:
            st.success("No active illnesses today.")
        else:
            friendly_cards(sick,lambda r:f"{r.get('sim_name') or r.get('sim_id')} — {r.get('illness_name')}",
                meta=(lambda r:("Began",f"Global Day {r.get('onset_global_day')}"),"status",lambda r:("Contagious","Yes" if r.get("contagious") else "No")),
                body="treatment",badge="severity")
            illness_labels=[f"{row.illness_id} — {row.sim_name or row.sim_id} — {row.illness_name}" for _,row in sick.iterrows()]
            recovery_pick=st.selectbox("Resolve an active illness",illness_labels,key="today_illness_resolve")
            recovery_id=recovery_pick.split(" — ",1)[0]
            if st.button("Mark selected illness recovered",key="today_illness_recover",use_container_width=True):
                con=connect(); old=con.execute("SELECT status,end_global_day,sim_id FROM illnesses WHERE illness_id=?",(recovery_id,)).fetchone()
                st.session_state["today_undo"]={"kind":"illness","label":f"Resolved {recovery_id}",
                    "illness_id":recovery_id,"status":old[0] if old else "Active",
                    "end_global_day":old[1] if old else None}
                con.execute("UPDATE illnesses SET status='Recovered',end_global_day=? WHERE illness_id=?",(g,recovery_id))
                con.commit(); con.close()
                if old:remember_sim(old[2])
                st.success(f"Marked {recovery_id} recovered."); st.rerun()
        st.markdown("**Quickly record an illness**")
        with st.form("today_add_illness",clear_on_submit=True):
            opts=sim_options(blank=False,prefer_recent=True)
            a,b=st.columns(2)
            ill_sim=a.selectbox("Sim",opts,key="today_illness_sim") if opts else ""
            ill_name=b.text_input("Illness",placeholder="Influenza, fever, consumption…",key="today_illness_name")
            a,b,c=st.columns(3)
            severity=a.selectbox("Severity",illnesses.SEVERITIES,index=1,key="today_illness_severity")
            status=b.selectbox("Status",illnesses.ACTIVE_STATUSES,key="today_illness_status")
            contagious=c.checkbox("Contagious",key="today_illness_contagious")
            treatment=st.text_input("Treatment or care",key="today_illness_treatment")
            notes=st.text_area("Notes",height=100,key="today_illness_notes")
            save_illness=st.form_submit_button("Add illness for today",type="primary",use_container_width=True)
        if save_illness:
            if not ill_sim: st.error("Add a Sim before recording an illness.")
            elif not ill_name.strip(): st.error("Enter the illness name.")
            else:
                remember_sim(sid(ill_sim))
                con=connect(); iid=illnesses.next_id(con)
                con.execute("""INSERT INTO illnesses(illness_id,sim_id,sim_name,illness_name,onset_global_day,status,
                               severity,contagious,treatment,notes) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (iid,sid(ill_sim),ill_sim.split(" — ",1)[1],ill_name.strip(),g,status,severity,
                             1 if contagious else 0,treatment.strip() or None,notes.strip() or None))
                con.commit(); con.close(); st.success(f"Recorded {ill_name.strip()} for {ill_sim.split(' — ',1)[1]}."); st.rerun()

    if "Deaths" in task_view:
        deaths=q("""SELECT sim_id,title,first_name,last_name,suffix,death_global_day,death_date,
                            death_place,cause_of_death
                     FROM sims WHERE death_global_day=?
                     ORDER BY last_name,first_name,sim_id""",(g,))
        if deaths.empty:
            st.success("No Sims are scheduled to die today.")
        else:
            deaths=deaths.copy()
            deaths["Sim"]=(deaths[["title","first_name","last_name","suffix"]].fillna("")
                           .agg(" ".join,axis=1).str.replace(r"\s+"," ",regex=True).str.strip())
            portrait_rows=[]
            for _,death_row in deaths.head(6).iterrows():
                portrait=cached_sim_photo(death_row.sim_id)
                if portrait:portrait_rows.append((death_row.Sim or death_row.sim_id,compressed_thumbnail(portrait["image_data"],120)))
            if portrait_rows:
                for column,(name,portrait) in zip(st.columns(len(portrait_rows)),portrait_rows):
                    column.image(portrait,width=120,caption=name)
            friendly_cards(
                deaths,lambda r:r.get("Sim") or r.get("sim_id"),
                meta=(lambda r:("When",r.get("death_date") or gd_caption(r.get("death_global_day"))),
                      lambda r:("Global Day",r.get("death_global_day")),"death_place"),
                body=lambda r:r.get("cause_of_death") or "Cause of death not yet recorded",
                badge=lambda r:"Kill off today",
            )
            death_labels=[f"{row.sim_id} — {row.Sim or row.sim_id}" for _,row in deaths.iterrows()]
            death_pick=st.selectbox("Record death details",death_labels,key="today_death_pick")
            death_sim=sid(death_pick)
            chosen=deaths[deaths.sim_id==death_sim].iloc[0]
            a,b=st.columns(2)
            death_cause=a.text_input("Cause of death",value=chronicle_value(chosen.cause_of_death),key="today_death_cause")
            death_place=b.text_input("Place of death",value=chronicle_value(chosen.death_place),key="today_death_place")
            if st.button("Confirm death details",key="today_death_confirm",type="primary",use_container_width=True):
                st.session_state["today_undo"]={"kind":"death","label":f"Recorded death for {chosen.Sim}",
                    "sim_id":death_sim,"death_global_day":chosen.death_global_day,"death_date":chosen.death_date,
                    "death_place":chosen.death_place,"cause_of_death":chosen.cause_of_death}
                con=connect(); con.execute("""UPDATE sims SET death_global_day=?,death_date=COALESCE(death_date,?),
                               death_place=?,cause_of_death=? WHERE sim_id=?""",
                            (g,gd_caption(g),death_place or None,death_cause or None,death_sim))
                con.commit(); con.close(); remember_sim(death_sim)
                st.success(f"Death details saved for {chosen.Sim}."); st.rerun()

        upcoming_deaths=q("""SELECT sim_id,title,first_name,last_name,suffix,death_global_day,death_date,cause_of_death
                              FROM sims WHERE death_global_day>?
                              ORDER BY death_global_day,last_name,first_name LIMIT 10""",(g,))
        if not upcoming_deaths.empty:
            section_heading("Coming deaths","The next ten scheduled deaths")
            upcoming_deaths=upcoming_deaths.copy()
            upcoming_deaths["Sim"]=(upcoming_deaths[["title","first_name","last_name","suffix"]].fillna("")
                                    .agg(" ".join,axis=1).str.replace(r"\s+"," ",regex=True).str.strip())
            friendly_cards(
                upcoming_deaths,lambda r:r.get("Sim") or r.get("sim_id"),
                meta=(lambda r:("When",r.get("death_date") or gd_caption(r.get("death_global_day"))),
                      lambda r:("Global Day",r.get("death_global_day"))),
                body="cause_of_death",badge=lambda r:"Upcoming",limit=10,
            )

    section_heading("Coming up","A calm preview—nothing is added to the log until it becomes due")
    a,b=st.columns([2,3])
    with a:
        preview_window=st.segmented_control("Upcoming window",["Next day","Next 7 days","Later"],
                                            default="Next 7 days",key="today_preview_window")
    lookahead={"Next day":1,"Next 7 days":7,"Later":80}[preview_window]
    with b:
        st.caption("Future rolls stay as previews until they actually become due, so your Roll Log stays clean.")
    upcoming_rows=cached_upcoming_rolls(g,lookahead)
    if upcoming_rows:
        udf=add_applicable_events(pd.DataFrame(upcoming_rows))
        friendly_cards(udf,lambda r:r.get("roll_type") or "Upcoming roll",
            meta=(lambda r:("Sim",r.get("sim_name") or r.get("sim_id")),lambda r:("When",f"Year {r.get('year')} · GD {r.get('due_global_day')}"),"die"),
            body=lambda r:(f"Historical context: {r.get('applicable_events')}\n\n" if r.get('applicable_events') else "")+
                          f"Bad results: {r.get('bad_results') or 'Not configured'}",badge="rule_status",limit=20)
    else:
        st.info("Nothing automatically scheduled in this window.")

@workspace_fragment
def render_game_clock_sync():
    page_header("Automatic Game Clock","Let The Sims 4 advance this save's Global Day when its in-game calendar changes.")
    active_record=save_manager.active_save()
    sync_status=clock_sync.status(workspace,active_record["save_id"])
    if sync_status and sync_status.get("enabled"):
        a,b,c=st.columns(3)
        a.metric("Link","Active")
        b.metric("Last game day",sync_status.get("last_game_day") if sync_status.get("last_game_day") is not None else "Waiting")
        c.metric("Tracker Global Day",sync_status.get("last_tracker_day") if sync_status.get("last_tracker_day") is not None else current_gd())
        if sync_status.get("last_seen_at"):
            st.success(f"Last received from The Sims 4: {sync_status['last_seen_at']}")
        else:
            st.info("The private link is ready and waiting for its first report from the game.")
    else:
        st.info("No automatic game-clock link is active for this save.")

    section_heading("Install the bridge","One private configuration per tracker save")
    st.write("The first report pairs the game's current day with this save's current Global Day. Every later in-game day advances the tracker once; loading an older game save will never rewind it automatically.")
    with st.expander("Complete setup and troubleshooting instructions",expanded=not bool(sync_status and sync_status.get("last_seen_at"))):
        st.markdown(r"""
### First-time setup

1. Open the tracker save you want to connect and confirm its **Tracker Global Day** is correct.
2. Select **Create a new private clock link** below. Creating another link later revokes the older configuration.
3. Download all four files shown after creating the link:
   - `SeveralUDOClockSync.ts4script`
   - `config.json`
   - `SeveralUDOClockRelay.ps1`
   - `Start SeveralUDO Clock Relay.bat`
4. Put all four files directly in:
   `Documents\Electronic Arts\The Sims 4\Mods\SeveralUDOClockSync`
5. In The Sims 4, enable **Custom Content and Mods** and **Script Mods Allowed**, then restart the game if either setting changed.
6. Double-click **Start SeveralUDO Clock Relay.bat**. It runs quietly in the background; opening it more than once is safe.
7. Start The Sims 4 and load the household you are actively playing. The first report anchors that in-game day to the tracker's current Global Day.

### Normal use

- The relay can be started **before or after** The Sims 4. Starting it first provides the quickest updates.
- If you forget the BAT file, reports wait safely in `pending_report.json` and are delivered when the relay starts.
- Keep the relay running while playing. Run the BAT again after restarting Windows or whenever the tracker stops receiving reports.
- The tracker advances only when the in-game calendar advances. Loading an older save will not move Global Day backward.
- Household and pregnancy-state changes are also reported so the tracker can offer to add newly detected babies, spouses, other Sims, and pregnancies.

### Useful in-game commands

Open the cheat console with **Ctrl + Shift + C**, then enter:

- `severaludo.clock.status` — shows the detected game day, time, household, and installed Clock Sync version.
- `severaludo.clock.report` — queues a report immediately. “Report queued” means the game-to-relay step succeeded; the tracker updates after the relay delivers it.

### If the tracker still says Waiting

1. Run **Start SeveralUDO Clock Relay.bat** and wait about 20 seconds.
2. Use `severaludo.clock.report` in the game, then refresh this page.
3. Confirm all four files are together in the `SeveralUDOClockSync` folder—not in a second nested folder.
4. If the relay reports **401 Unauthorized**, create a new private link and replace only `config.json`. A newly created link revokes the old token.
5. If ModGuard warns about Clock Sync, make sure you have the current local-only script from this page. Do not approve or keep an older networking build.
6. Never post or share `config.json`; it contains the private credential for this tracker save.

### What the page status means

- **Waiting** — the link exists, but Railway has not accepted its first report.
- **Last received from The Sims 4** — the complete game → queue → relay → tracker path is working.
- **Tracker Global Day unchanged after a successful report** — normal when the reported game day has not advanced since the prior report.
""")
    if st.button("Create a new private clock link",type="primary",use_container_width=True,key="clock_sync_create"):
        token=clock_sync.create_link(workspace,active_record)
        st.session_state[f"clock_sync_token_{active_record['save_id']}"]=token
        st.success("New private link created. Any older link for this save was revoked.")
    token=st.session_state.get(f"clock_sync_token_{active_record['save_id']}")
    mod_path=Path(__file__).resolve().parent/"dist"/"SeveralUDOClockSync.ts4script"
    relay_path=Path(__file__).resolve().parent/"dist"/"SeveralUDOClockRelay.ps1"
    relay_launcher_path=Path(__file__).resolve().parent/"dist"/"Start SeveralUDO Clock Relay.bat"
    if token:
        st.warning("Download the configuration now. For security, its token is only shown during this session.")
        a,b=st.columns(2)
        if mod_path.exists():
            a.download_button("Download Sims 4 script mod",mod_path.read_bytes(),file_name="SeveralUDOClockSync.ts4script",
                              mime="application/zip",use_container_width=True)
        b.download_button("Download private config.json",clock_sync.config_bytes(token),file_name="config.json",
                          mime="application/json",use_container_width=True)
        a,b=st.columns(2)
        if relay_path.exists():
            a.download_button("Download secure relay",relay_path.read_bytes(),file_name="SeveralUDOClockRelay.ps1",
                              mime="text/plain",use_container_width=True)
        if relay_launcher_path.exists():
            b.download_button("Download relay launcher",relay_launcher_path.read_bytes(),
                              file_name="Start SeveralUDO Clock Relay.bat",mime="text/plain",use_container_width=True)
        st.code(r"Documents\Electronic Arts\The Sims 4\Mods\SeveralUDOClockSync",language=None)
        st.caption("Place all four files directly in that folder, double-click the relay launcher, enable Script Mods Allowed, restart The Sims 4, and load your household.")
    elif sync_status and sync_status.get("enabled"):
        st.caption("The token is hidden after creation. Create a new private link if you need to download a replacement configuration.")
        a,b=st.columns(2)
        if relay_path.exists():
            a.download_button("Download secure relay",relay_path.read_bytes(),file_name="SeveralUDOClockRelay.ps1",
                              mime="text/plain",use_container_width=True,key="clock_relay_existing")
        if relay_launcher_path.exists():
            b.download_button("Download relay launcher",relay_launcher_path.read_bytes(),
                              file_name="Start SeveralUDO Clock Relay.bat",mime="text/plain",use_container_width=True,
                              key="clock_relay_launcher_existing")

    with st.expander("Disconnect automatic clock sync"):
        confirm=st.checkbox("Revoke the active game-clock link",key="clock_sync_revoke_confirm")
        if st.button("Disconnect",disabled=not confirm,key="clock_sync_revoke"):
            clock_sync.revoke(workspace,active_record["save_id"])
            st.session_state.pop(f"clock_sync_token_{active_record['save_id']}",None)
            st.success("Automatic clock sync disconnected.")
            st.rerun()

@workspace_fragment
def render_sims():
    page_header("Sims","Browse profiles, add people quickly, or edit family connections without touching raw IDs.")

    sim_section=st.segmented_control("Sim section",["Directory","Add Sim","Edit Sim","Family"],default=None,label_visibility="collapsed",key="sim_section") or "Directory"

    if sim_section=="Directory":
        a,b,c=st.columns([2,1,1])
        search=a.text_input("Search by name or ID",key="sim_dir_search",placeholder="Start typing a name…")
        status_filter=b.selectbox("Status",["All","Living","Deceased"],key="sim_dir_status")
        gen_values=q("SELECT DISTINCT generation FROM sims WHERE generation IS NOT NULL ORDER BY generation")["generation"].tolist()
        gen_choice=c.selectbox("Generation",["All"]+[str(int(x)) for x in gen_values],key="sim_dir_gen")
        term=f"%{search}%"
        sql="""SELECT sim_id,title,first_name,last_name,suffix,sex,generation,mother_id,father_id,birth_global_day,
                      birth_date,death_global_day,death_date,current_household_id,species_occult
               FROM sims WHERE 1=1"""
        params=[]
        if search:
            sql+=" AND (sim_id LIKE ? OR first_name LIKE ? OR last_name LIKE ? OR (COALESCE(first_name,'')||' '||COALESCE(last_name,'')) LIKE ?)"
            params += [term,term,term,term]
        if status_filter=="Living":
            sql+=" AND death_global_day IS NULL"
        elif status_filter=="Deceased":
            sql+=" AND death_global_day IS NOT NULL"
        if gen_choice!="All":
            sql+=" AND generation=?"; params.append(int(gen_choice))
        count_sql="SELECT COUNT(*) FROM ("+sql+") sim_directory"
        sim_total=scalar(count_sql,tuple(params))
        sim_page_size=30; sim_pages=max(1,(sim_total+sim_page_size-1)//sim_page_size)
        sim_page=st.number_input("Directory page",1,sim_pages,1,key="sim_directory_page") if sim_pages>1 else 1
        sql+=" ORDER BY last_name,first_name,sim_id LIMIT ? OFFSET ?"
        params.extend([sim_page_size,(int(sim_page)-1)*sim_page_size])
        df=q(sql,tuple(params))
        if sim_pages>1: st.caption(f"Showing page {int(sim_page)} of {sim_pages} · {sim_total} matching Sims")
        if not df.empty:
            df["Name"]=(df["title"].fillna("")+" "+df["first_name"].fillna("")+" "+df["last_name"].fillna("")+" "+df["suffix"].fillna("")).str.replace(r"\s+"," ",regex=True).str.strip()
            df["Status"]=df["death_global_day"].apply(lambda x:"Deceased" if pd.notna(x) else "Living")
            friendly_cards(df,"Name",
                meta=(lambda r:("Born",f"Global Day {r.get('birth_global_day')}" if pd.notna(r.get('birth_global_day')) else "Unknown"),lambda r:("Generation",r.get("generation")),lambda r:("Household",r.get("current_household_id")),lambda r:("Species",r.get("species_occult"))),
                badge="Status",limit=30)
            labels=[f"{r.Name} — {r.sim_id}" for _,r in df.iterrows()]
            profile_pick=st.selectbox("Open a Sim profile",labels,key="sim_dir_profile")
            profile_id=profile_pick.rsplit(" — ",1)[-1]
            rr=q("SELECT * FROM sims WHERE sim_id=?",(profile_id,))
            if not rr.empty:
                row=rr.iloc[0]
                photo=cached_sim_photo(profile_id)
                left,right=st.columns([1,3])
                with left:
                    if photo:
                        st.image(photo["image_data"],width=220)
                    else:
                        st.markdown("### 👤")
                        st.caption("No portrait yet")
                with right:
                    pname=" ".join(x for x in [row.get("title"),row.get("first_name"),row.get("last_name"),row.get("suffix")] if x and str(x)!="nan")
                    st.subheader(pname or profile_id)
                    status="Deceased" if pd.notna(row.get("death_global_day")) else "Living"
                    badges=[status]
                    if pd.notna(row.get("generation")): badges.append(f"Generation {int(row.get('generation'))}")
                    if row.get("species_occult"): badges.append(str(row.get("species_occult")))
                    st.markdown(" ".join(status_badge(x) for x in badges),unsafe_allow_html=True)
                    bg=int_or_none(row.get("birth_global_day")); dg=int_or_none(row.get("death_global_day"))
                    age_days=(dg if dg is not None else current_gd())-bg if bg is not None else None
                    a,b,c=st.columns(3)
                    a.metric("Birth Global Day",bg if bg is not None else "—")
                    b.metric("Death Global Day",dg if dg is not None else "—")
                    c.metric("Age / lifespan",f"{age_days/4:.1f} years" if age_days is not None and age_days>=0 else "—")
                    con=connect()
                    mom=con.execute("SELECT TRIM(COALESCE(title,'')||' '||COALESCE(first_name,'')||' '||COALESCE(last_name,'')) FROM sims WHERE sim_id=?",(row.get("mother_id"),)).fetchone() if row.get("mother_id") else None
                    dad=con.execute("SELECT TRIM(COALESCE(title,'')||' '||COALESCE(first_name,'')||' '||COALESCE(last_name,'')) FROM sims WHERE sim_id=?",(row.get("father_id"),)).fetchone() if row.get("father_id") else None
                    kids=con.execute("""SELECT sim_id,TRIM(COALESCE(title,'')||' '||COALESCE(first_name,'')||' '||COALESCE(last_name,''))
                                        FROM sims WHERE mother_id=? OR father_id=? ORDER BY birth_global_day""",(profile_id,profile_id)).fetchall()
                    rels=profiles.sim_relationships(con,profile_id)
                    hhname=con.execute("SELECT household_name FROM households WHERE household_id=?",(row.get("current_household_id"),)).fetchone() if row.get("current_household_id") else None
                    con.close()
                    st.markdown(f"**Parents:** {(mom[0] if mom else 'Unknown')} • {(dad[0] if dad else 'Unknown')}")
                    st.markdown(f"**Current household:** {(hhname[0] if hhname else row.get('current_household_id') or 'None')}")
                    if rels:
                        partner_bits=[]
                        for r in rels:
                            other=r["partner2_name"] if r["partner1_id"]==profile_id else r["partner1_name"]
                            partner_bits.append(f"{other or 'Unknown'} ({r['type'] or 'Relationship'} — {r['status'] or 'Unknown'})")
                        st.markdown("**Relationships:** "+ " • ".join(partner_bits))
                    if kids:
                        st.markdown("**Children:** "+ " • ".join((k[1] or k[0]) for k in kids))
        else:
            st.info("No Sims match those filters.")

    if sim_section=="Add Sim":
        st.subheader("Add a Sim")
        st.caption("Only the basics are shown first. Open Advanced details only when you need them.")
        con=connect(); proposed=next_id(con,'sims','sim_id','SIM'); con.close()
        opts=sim_options()
        hdf=q("SELECT household_id,household_name FROM households ORDER BY household_name,household_id")
        hopts=[""]+[f"{r.household_id} — {r.household_name or ''}" for _,r in hdf.iterrows()]
        with st.form("add_sim_form",clear_on_submit=False):
            a,b,c=st.columns([1,2,2])
            sim_id=a.text_input("Sim ID",value=proposed,help="Automatically generated. Change only if you have a specific legacy ID to preserve.")
            first=b.text_input("First name")
            last=c.text_input("Last name")
            a,b,c,d=st.columns(4)
            sex=a.selectbox("Gender / sex",["Female","Male","Other","Unknown"])
            gen=b.number_input("Generation",min_value=0,max_value=100,value=0,step=1)
            species=c.text_input("Species / occult",value="Human")
            bg=d.text_input("Birth Global Day",value=str(current_gd()),help="Defaults to the current Global Day for a newly born Sim.")
            if int_or_none(bg) is not None:
                st.caption("Birth: "+gd_caption(int_or_none(bg)))
            a,b=st.columns(2)
            birth_clock=a.time_input("In-game birth time",value=time(0,0),step=60,key="sim_add_birth_clock")
            auto_birth_date=b.checkbox("Calculate exact birth date from in-game time",value=True,key="sim_add_auto_birth_date")
            calculated_birth_date=exact_date_from_ingame_time(int_or_none(bg),birth_clock) if auto_birth_date else None
            if calculated_birth_date:
                st.caption(f"Calculated exact birth date: {calculated_birth_date}")
            a,b,c=st.columns(3)
            mother=a.selectbox("Mother",opts,key="sim_add_mother")
            father=b.selectbox("Father",opts,key="sim_add_father")
            household=c.selectbox("Household",hopts,key="sim_add_household")
            auto_gen=st.checkbox("Automatically set generation from the recorded parent(s)",value=True,
                                 help="When a parent is selected, the new Sim becomes one generation after the highest recorded parent.")
            a,b,c=st.columns(3)
            birth_status=a.selectbox("Birth status",["Naturally Born","Married In","Adopted In","Other Partner","Other","Unknown"])
            multiple=b.selectbox("Birth type",["Single","Twin","Triplet","Quadruplet","Sextuplet","Unknown"])
            photo=c.file_uploader("Default portrait (fallback, optional)",type=["png","jpg","jpeg","webp"],key="sim_add_photo",
                                  help="Used until you add a portrait for the Sim's current life stage.")
            notes=st.text_area("Notes",placeholder="Anything important about this Sim…")
            with st.expander("Advanced details"):
                a,b,c=st.columns(3)
                title=a.text_input("Title")
                suffix=b.text_input("Suffix")
                maiden=c.text_input("Maiden / married name")
                a,b,c=st.columns(3)
                birth_date=a.text_input("Exact birth date",value=calculated_birth_date or "",disabled=auto_birth_date)
                birthplace=b.text_input("Birthplace")
                fertility=c.text_input("Fertility status",value="Unknown")
                a,b=st.columns(2)
                legitimate=a.checkbox("Legitimate?",value=True)
                succession=b.selectbox("Succession override",["Auto","Include","Exclude","Other"])
                succession_note=st.text_input("Succession note")
            submitted=st.form_submit_button("Add Sim",type="primary",use_container_width=True)
            if submitted:
                if not first.strip() and not last.strip():
                    st.error("Enter at least a first or last name.")
                elif not sim_id.strip():
                    st.error("A Sim ID is required.")
                else:
                    con=connect()
                    exists=con.execute("SELECT 1 FROM sims WHERE sim_id=?",(sim_id.strip(),)).fetchone()
                    if exists:
                        con.close(); st.error(f"{sim_id} already exists. Use Edit Sim instead.")
                    else:
                        save_gen=gen
                        if auto_gen and (sid(mother) or sid(father)):
                            parent_ids=[x for x in [sid(mother),sid(father)] if x]
                            placeholders=",".join("?" for _ in parent_ids)
                            parent_gens=[r[0] for r in con.execute(
                                f"SELECT generation FROM sims WHERE sim_id IN ({placeholders}) AND generation IS NOT NULL",
                                tuple(parent_ids)
                            ).fetchall()]
                            if parent_gens:
                                save_gen=max(int(x) for x in parent_gens)+1
                        con.execute("""INSERT INTO sims(
                            sim_id,include_in_tree,title,first_name,last_name,suffix,maiden_married_name,sex,generation,
                            mother_id,father_id,birth_global_day,birth_date,birthplace,birth_status,multiple_birth,
                            historical_household,notes,current_household_id,legitimate,fertility_status,species_occult,
                            succession_override,succession_note
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (sim_id.strip(),1,title or None,first.strip() or None,last.strip() or None,suffix or None,maiden or None,
                         sex,save_gen,sid(mother),sid(father),int_or_none(bg),(calculated_birth_date if auto_birth_date else birth_date) or None,birthplace or None,birth_status,
                         multiple,None,notes or None,sid(household),1 if legitimate else 0,fertility or None,species or "Human",
                         succession,succession_note or None))
                        if photo is not None:
                            try: profiles.save_photo(con,sim_id.strip(),photo)
                            except ValueError as e:
                                con.rollback(); con.close(); st.error(str(e)); st.stop()
                        con.commit()
                        schedule_through=max(current_gd(),int_or_none(bg) or current_gd())
                        scheduled=autorolls.schedule_sim_lifecycle(con,sim_id.strip(),schedule_through)
                        con.close()
                        st.success(f"Added {first} {last} ({sim_id}) and scheduled {scheduled} lifecycle roll(s).")

    if sim_section=="Edit Sim":
        st.subheader("Edit a Sim")
        sopts=sim_options(blank=False)
        if not sopts:
            st.info("No Sims to edit.")
        else:
            selected_label=st.selectbox("Choose a Sim",sopts,key="sim_edit_select")
            selected=sid(selected_label)
            rr=q("SELECT * FROM sims WHERE sim_id=?",(selected,))
            row=rr.iloc[0].to_dict()
            current_photo=cached_sim_photo(selected)
            top_left,top_right=st.columns([1,4])
            with top_left:
                if current_photo:
                    st.image(current_photo["image_data"],width=220)
                else:
                    st.markdown("## 👤")
                    st.caption("No portrait")
            with top_right:
                nm=" ".join(x for x in [row.get("title"),row.get("first_name"),row.get("last_name"),row.get("suffix")] if x)
                st.subheader(nm or selected)
                st.caption(selected)
            basic_tab,family_tab,life_tab,portraits_tab,advanced_tab=st.tabs(
                ["Basic info","Parents & household","Life events","Life-stage portraits","Advanced"])
            with basic_tab:
                a,b,c,d=st.columns(4)
                title=a.text_input("Title",value=row.get("title") or "",key="sim_edit_title")
                first=b.text_input("First name",value=row.get("first_name") or "",key="sim_edit_first")
                last=c.text_input("Last name",value=row.get("last_name") or "",key="sim_edit_last")
                suffix=d.text_input("Suffix",value=row.get("suffix") or "",key="sim_edit_suffix")
                a,b,c,d=st.columns(4)
                sex=a.text_input("Gender / sex",value=row.get("sex") or "",key="sim_edit_sex")
                gen=b.number_input("Generation",0,100,int(row.get("generation") or 0),key="sim_edit_gen")
                species=c.text_input("Species / occult",value=row.get("species_occult") or "Human",key="sim_edit_species")
                maiden=d.text_input("Maiden / married name",value=row.get("maiden_married_name") or "",key="sim_edit_maiden")
                photo=st.file_uploader("Replace default fallback portrait",type=["png","jpg","jpeg","webp"],key=f"sim_edit_photo_{selected}")
                delete_photo=st.checkbox("Remove default fallback portrait when saving",value=False,key=f"sim_delete_photo_{selected}")
            with family_tab:
                opts=sim_options()
                hdf=q("SELECT household_id,household_name FROM households ORDER BY household_name,household_id")
                hopts=[""]+[f"{r.household_id} — {r.household_name or ''}" for _,r in hdf.iterrows()]
                a,b,c=st.columns(3)
                mother=a.selectbox("Mother",opts,index=opt_index(opts,row.get("mother_id")),key=f"sim_edit_mother_{selected}")
                father=b.selectbox("Father",opts,index=opt_index(opts,row.get("father_id")),key=f"sim_edit_father_{selected}")
                hhidx=next((i for i,o in enumerate(hopts) if row.get("current_household_id") and o.startswith(row.get("current_household_id")+" —")),0)
                household=c.selectbox("Current household",hopts,index=hhidx,key=f"sim_edit_hh_{selected}")
                a,b,c=st.columns(3)
                birth_status=a.text_input("Birth status",value=row.get("birth_status") or "",key="sim_edit_birthstatus")
                multiple=b.text_input("Birth type",value=row.get("multiple_birth") or "",key="sim_edit_multiple")
                legit_default=bool(row.get("legitimate")) if row.get("legitimate") is not None else True
                legitimate=c.checkbox("Legitimate?",value=legit_default,key="sim_edit_legit")
            with life_tab:
                st.caption("Enter a Global Day and optional in-game clock time. The clock maps proportionally across that historical quarter.")
                spouse_rows=q("""SELECT partner1_id,partner2_id FROM relationships WHERE
                                  (partner1_id=? OR partner2_id=?) AND
                                  (LOWER(COALESCE(type,''))='marriage' OR COALESCE(legally_married,0)=1)
                                  ORDER BY start_global_day DESC LIMIT 1""",(selected,selected))
                current_spouse=None
                if not spouse_rows.empty:
                    rel=spouse_rows.iloc[0]; current_spouse=rel.partner2_id if rel.partner1_id==selected else rel.partner1_id
                spouse_opts=[option for option in sim_options() if sid(option)!=selected]
                spouse=st.selectbox("Spouse",spouse_opts,index=opt_index(spouse_opts,current_spouse),key=f"sim_edit_spouse_{selected}",
                                    help="Saving a spouse and marriage day updates both Sims and creates the Marriage record automatically.")
                a,b,c=st.columns(3)
                bg=a.text_input("Birth Global Day",value=str(row.get("birth_global_day") or ""),key="sim_edit_bg")
                mg=b.text_input("Marriage Global Day",value=str(row.get("marriage_global_day") or ""),key="sim_edit_mg")
                dg=c.text_input("Death Global Day",value=str(row.get("death_global_day") or ""),key="sim_edit_dg")
                for lab,val in [("Birth",int_or_none(bg)),("Marriage",int_or_none(mg)),("Death",int_or_none(dg))]:
                    if val is not None: st.caption(f"{lab}: {gd_caption(val)}")
                a,b,c=st.columns(3)
                birth_clock=a.time_input("In-game birth time",value=time(0,0),step=60,key=f"sim_edit_birth_clock_{selected}")
                marriage_clock=b.time_input("In-game marriage time",value=time(0,0),step=60,key=f"sim_edit_marriage_clock_{selected}")
                death_clock=c.time_input("In-game death time",value=time(0,0),step=60,key=f"sim_edit_death_clock_{selected}")
                a,b,c=st.columns(3)
                auto_birth=a.checkbox("Calculate birth date",value=False,key=f"sim_edit_auto_birth_{selected}")
                auto_marriage=b.checkbox("Calculate marriage date",value=False,key=f"sim_edit_auto_marriage_{selected}")
                auto_death=c.checkbox("Calculate death date",value=False,key=f"sim_edit_auto_death_{selected}")
                calculated_birth=exact_date_from_ingame_time(int_or_none(bg),birth_clock) if auto_birth else None
                calculated_marriage=exact_date_from_ingame_time(int_or_none(mg),marriage_clock) if auto_marriage else None
                calculated_death=exact_date_from_ingame_time(int_or_none(dg),death_clock) if auto_death else None
                a,b,c=st.columns(3)
                birth_date=a.text_input("Exact birth date",value=calculated_birth or row.get("birth_date") or "",key="sim_edit_birthdate",disabled=auto_birth)
                marriage_date=b.text_input("Exact marriage date",value=calculated_marriage or row.get("marriage_date") or "",key="sim_edit_marriagedate",disabled=auto_marriage)
                death_date=c.text_input("Exact death date",value=calculated_death or row.get("death_date") or "",key="sim_edit_deathdate",disabled=auto_death)
                for label,value in [("Birth",calculated_birth),("Marriage",calculated_marriage),("Death",calculated_death)]:
                    if value: st.caption(f"Calculated {label.lower()} date: {value}")
                a,b,c=st.columns(3)
                birthplace=a.text_input("Birthplace",value=row.get("birthplace") or "",key="sim_edit_birthplace")
                marriage_place=b.text_input("Marriage place",value=row.get("marriage_place") or "",key="sim_edit_marriageplace")
                death_place=c.text_input("Death place",value=row.get("death_place") or "",key="sim_edit_deathplace")
                cause=st.text_input("Cause of death",value=row.get("cause_of_death") or "",key="sim_edit_cause")
            with advanced_tab:
                fertility=st.text_input("Fertility status",value=row.get("fertility_status") or "",key="sim_edit_fertility")
                succession_opts=["Auto","Include","Exclude","Other"]
                curr_succ=row.get("succession_override") or "Auto"
                if curr_succ not in succession_opts: succession_opts=[curr_succ]+succession_opts
                succession=st.selectbox("Succession override",succession_opts,index=succession_opts.index(curr_succ),key="sim_edit_succ")
                succession_note=st.text_input("Succession note",value=row.get("succession_note") or "",key="sim_edit_succnote")
                notes=st.text_area("Notes",value=row.get("notes") or "",key="sim_edit_notes")
                include_tree=st.checkbox("Include in family tree",value=bool(row.get("include_in_tree") if row.get("include_in_tree") is not None else 1),key="sim_edit_tree")
            with portraits_tab:
                automatic_stage=current_sim_life_stage(selected)
                st.info(f"The tracker currently identifies this Sim as {automatic_stage or 'Unknown'}. That stage's portrait is used automatically wherever the Sim appears.")
                portrait_stage=st.selectbox("Life stage",profiles.LIFE_STAGE_NAMES,
                    index=(profiles.LIFE_STAGE_NAMES.index(automatic_stage) if automatic_stage in profiles.LIFE_STAGE_NAMES else 0),
                    key=f"sim_portrait_stage_{selected}")
                stage_photo=cached_sim_photo(selected,portrait_stage)
                a,b=st.columns([1,3])
                with a:
                    if stage_photo:st.image(stage_photo["image_data"],width=200)
                    else:st.caption(f"No {portrait_stage} portrait yet. The default portrait will be used.")
                with b:
                    stage_upload=st.file_uploader(f"Upload {portrait_stage} portrait",type=["png","jpg","jpeg","webp"],
                                                  key=f"sim_stage_upload_{selected}_{portrait_stage}")
                    save_stage=st.button("Save life-stage portrait",type="primary",use_container_width=True,
                                         disabled=stage_upload is None,key=f"sim_stage_save_{selected}_{portrait_stage}")
                    remove_stage=st.button("Remove this stage portrait",use_container_width=True,
                                           disabled=stage_photo is None,key=f"sim_stage_remove_{selected}_{portrait_stage}")
                if save_stage:
                    con=connect()
                    try:
                        profiles.save_lifestage_photo(con,selected,portrait_stage,stage_upload)
                        con.commit()
                    except ValueError as error:
                        con.rollback()
                        st.error(str(error))
                        st.stop()
                    finally:
                        con.close()
                    st.success(f"Saved the {portrait_stage} portrait."); st.rerun()
                if remove_stage:
                    con=connect(); profiles.delete_lifestage_photo(con,selected,portrait_stage); con.commit(); con.close()
                    st.success(f"Removed the {portrait_stage} portrait. The default portrait will be used."); st.rerun()
                st.markdown("**Portrait coverage**")
                coverage=[]
                con=connect()
                try:
                    for stage in profiles.LIFE_STAGE_NAMES:
                        coverage.append({"Life stage":stage,"Portrait":"Added" if profiles.get_lifestage_photo(con,selected,stage) else "Uses default"})
                finally:con.close()
                friendly_cards(coverage,"Life stage",meta=("Portrait",),limit=len(coverage))
            if st.button("Save Sim changes",type="primary",use_container_width=True,key=f"sim_edit_save_{selected}"):
                con=connect()
                con.execute("""UPDATE sims SET
                    include_in_tree=?,title=?,first_name=?,last_name=?,suffix=?,maiden_married_name=?,sex=?,generation=?,
                    mother_id=?,father_id=?,birth_global_day=?,birth_date=?,birthplace=?,birth_status=?,multiple_birth=?,
                    marriage_global_day=?,marriage_date=?,marriage_place=?,death_global_day=?,death_date=?,death_place=?,
                    cause_of_death=?,notes=?,current_household_id=?,legitimate=?,fertility_status=?,species_occult=?,
                    succession_override=?,succession_note=?
                    WHERE sim_id=?""",
                (1 if include_tree else 0,title or None,first or None,last or None,suffix or None,maiden or None,sex or None,gen,
                 sid(mother),sid(father),int_or_none(bg),(calculated_birth if auto_birth else birth_date) or None,birthplace or None,birth_status or None,multiple or None,
                 int_or_none(mg),(calculated_marriage if auto_marriage else marriage_date) or None,marriage_place or None,int_or_none(dg),(calculated_death if auto_death else death_date) or None,death_place or None,
                 cause or None,notes or None,sid(household),1 if legitimate else 0,fertility or None,species or "Human",
                 succession,succession_note or None,selected))
                marriage_rel=None
                if sid(spouse) and int_or_none(mg) is not None:
                    marriage_rel=profiles.sync_marriage(
                        con,selected,sid(spouse),int_or_none(mg),
                        (calculated_marriage if auto_marriage else marriage_date) or None,
                        marriage_place or None,
                    )
                if delete_photo:
                    profiles.delete_photo(con,selected)
                elif photo is not None:
                    try: profiles.save_photo(con,selected,photo)
                    except ValueError as e:
                        con.rollback(); con.close(); st.error(str(e)); st.stop()
                con.commit()
                profiles.sync_cached_names(con,[selected])
                con.close()
                sync_auto_rolls(show_notice=False)
                st.success("Sim profile saved. "+(f"Marriage {marriage_rel} and both spouse profiles were synchronized." if marriage_rel else "Related names were updated automatically."))
                st.rerun()

            with st.expander("Delete this Sim",expanded=False):
                st.warning("This permanently deletes the Sim and cleans up linked records. Export a backup first if you may need to restore them.")
                con=connect()
                dependencies=admin_ops.sim_dependency_summary(con,selected)
                con.close()
                affected=[{"Linked data":label,"Count":count} for label,count in dependencies.items() if count]
                if affected:
                    friendly_cards(affected,"Linked data",meta=("Count",),limit=30)
                else:
                    st.caption("No linked records were found.")
                confirmation=st.text_input(f"Type {selected} to confirm deletion",key=f"sim_delete_confirm_{selected}")
                if st.button(
                    "Permanently delete Sim",
                    type="secondary",
                    use_container_width=True,
                    disabled=confirmation.strip()!=selected,
                    key=f"sim_delete_btn_{selected}",
                ):
                    con=connect()
                    try:
                        admin_ops.delete_sim(con,selected)
                    except Exception as error:
                        con.rollback(); con.close()
                        st.error(f"Could not delete this Sim: {error}")
                    else:
                        con.close()
                        st.success(f"Deleted {selected} and cleaned up linked records.")
                        st.rerun()

    if sim_section=="Family":
        st.subheader("Family & relationships")
        st.caption("Choose one Sim and manage their parents, children, and partnerships from one place.")
        sopts=sim_options(blank=False)
        if sopts:
            focal_label=st.selectbox("Sim",sopts,key="family_focal")
            focal=sid(focal_label)
            con=connect()
            frow=con.execute("SELECT * FROM sims WHERE sim_id=?",(focal,)).fetchone()
            photo=profiles.get_photo(con,focal)
            con.close()
            a,b=st.columns([1,4])
            with a:
                if photo: st.image(photo["image_data"],width=220)
                else: st.markdown("## 👤")
            with b:
                st.subheader(profiles.display_name(frow) or focal)
                st.caption(focal)
            family_view=st.segmented_control("Family section",["Parents","Children","Partners / marriages"],default="Parents",label_visibility="collapsed",key=f"family_view_{focal}")
            if family_view=="Parents":
                opts=sim_options()
                a,b=st.columns(2)
                mom=a.selectbox("Mother",opts,index=opt_index(opts,frow["mother_id"]),key=f"family_mom_{focal}")
                dad=b.selectbox("Father",opts,index=opt_index(opts,frow["father_id"]),key=f"family_dad_{focal}")
                if st.button("Save parents",type="primary",use_container_width=True,key=f"family_parent_save_{focal}"):
                    con=connect(); con.execute("UPDATE sims SET mother_id=?,father_id=? WHERE sim_id=?",(sid(mom),sid(dad),focal)); con.commit(); con.close()
                    st.success("Parents updated."); st.rerun()
            if family_view=="Children":
                con=connect()
                children=con.execute("""SELECT sim_id,TRIM(COALESCE(title,'')||' '||COALESCE(first_name,'')||' '||COALESCE(last_name,'')),
                                               mother_id,father_id,birth_global_day
                                        FROM sims WHERE mother_id=? OR father_id=? ORDER BY birth_global_day""",(focal,focal)).fetchall()
                con.close()
                if children:
                    crows=[{"Sim ID":r[0],"Child":r[1],"Mother ID":r[2],"Father ID":r[3],"Birth GD":r[4]} for r in children]
                    friendly_cards(crows,"Child",meta=(lambda r:("Born",f"GD {r.get('Birth GD')}"),"Mother ID","Father ID"),badge="Sim ID",limit=40)
                else:
                    st.caption("No recorded children.")
                st.markdown("**Link an existing Sim as a child**")
                child_opts=[""]+[o for o in sopts if sid(o)!=focal]
                a,b=st.columns(2)
                child=a.selectbox("Child",child_opts,key=f"family_child_{focal}")
                role=b.selectbox("This Sim is the child's…",["Mother","Father"],key=f"family_child_role_{focal}")
                if st.button("Add child link",use_container_width=True,key=f"family_child_save_{focal}",disabled=not bool(child)):
                    con=connect()
                    col="mother_id" if role=="Mother" else "father_id"
                    con.execute(f"UPDATE sims SET {col}=? WHERE sim_id=?",(focal,sid(child)))
                    con.commit(); con.close()
                    st.success("Child link added."); st.rerun()
            if family_view=="Partners / marriages":
                con=connect(); rels=profiles.sim_relationships(con,focal); con.close()
                if rels:
                    rows=[]
                    for r in rels:
                        other_id=r["partner2_id"] if r["partner1_id"]==focal else r["partner1_id"]
                        other_name=r["partner2_name"] if r["partner1_id"]==focal else r["partner1_name"]
                        rows.append({"Relationship ID":r["relationship_id"],"Partner":other_name or other_id,"Type":r["type"],
                                     "Start GD":r["start_global_day"],"End GD":r["end_global_day"],"Status":r["status"]})
                    friendly_cards(rows,"Partner",meta=("Type",lambda r:("Started",f"GD {r.get('Start GD')}"),"Status"),badge="Relationship ID",limit=40)
                else:
                    st.caption("No recorded partnerships.")
                st.markdown("**Add a relationship**")
                partner_opts=[""]+[o for o in sopts if sid(o)!=focal]
                a,b=st.columns(2)
                partner=a.selectbox("Partner",partner_opts,key=f"family_partner_{focal}")
                typ=b.selectbox("Relationship type",["Marriage","Engagement","Partnership","Other"],key=f"family_reltype_{focal}")
                a,b=st.columns(2)
                start=a.number_input("Start Global Day",-10000,20000,current_gd(),key=f"family_relstart_{focal}")
                location=b.text_input("Location",key=f"family_relloc_{focal}")
                notes=st.text_area("Relationship notes",key=f"family_relnotes_{focal}")
                if st.button("Add relationship",type="primary",use_container_width=True,key=f"family_reladd_{focal}",disabled=not bool(partner)):
                    con=connect(); rid=next_id(con,'relationships','relationship_id','REL')
                    other=sid(partner)
                    names={r[0]:r[1] for r in con.execute("SELECT sim_id,TRIM(COALESCE(title,'')||' '||COALESCE(first_name,'')||' '||COALESCE(last_name,'')) FROM sims")}
                    con.execute("""INSERT INTO relationships(
                        relationship_id,partner1_id,partner2_id,partner1_name,partner2_name,type,start_global_day,status,
                        location,legally_married,notes
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (rid,focal,other,names.get(focal),names.get(other),typ,start,"Active",location or None,1 if typ=="Marriage" else 0,notes or None))
                    profiles.sync_spouse_ids(con,[focal,other])
                    con.commit(); con.close()
                    st.success("Relationship added."); st.rerun()
        else:
            st.info("No Sims yet.")

@workspace_fragment
def render_family_tree():
    import networkx as nx
    from pyvis.network import Network

    page_header("Family Tree","Explore ancestors, descendants, spouses, and nearby family with a cleaner generation-based layout.")

    only_marked=st.checkbox("Only show Sims marked for the family tree",False,key="tree_only_marked",
                            help="Off by default so a valid Sim or spouse cannot silently disappear.")
    sims=q("""SELECT sim_id,COALESCE(title,'') title,first_name,last_name,suffix,mother_id,father_id,
                     birth_date,death_date,birth_global_day,death_global_day,generation,include_in_tree
              FROM sims WHERE (?=0 OR COALESCE(include_in_tree,1)=1)""",(1 if only_marked else 0,))
    if sims.empty:
        st.info("No Sims yet.")
    else:
        labels={
            r.sim_id:' '.join(str(x) for x in [r.title,r.first_name,r.last_name,r.suffix]
                              if x and str(x)!='nan').strip() or r.sim_id
            for _,r in sims.iterrows()
        }
        opts=[f"{k} — {v}" for k,v in sorted(labels.items(),key=lambda x:x[1].lower())]
        c=connect(); heir=setting(c,'current_heir_id',''); c.close()

        a,b,c,d=st.columns([2.3,1,1,1])
        focus=a.selectbox("Focus Sim",opts,index=opt_index(opts,heir),key="tree_focus")
        view_mode=b.selectbox("View",["Family","Ancestors","Descendants"],key="tree_view")
        depth=c.slider("Depth",1,8,4,key="tree_depth")
        show_spouses=d.checkbox("Spouses",True,key="tree_spouses")
        show_portraits=st.checkbox("Load portraits into the tree",value=False,key="tree_portraits",
                                   help="Off by default for a faster, more stable tree. Turn it on when you want the portrait view.")
        show_dates=st.checkbox("Show birth/death dates in labels",value=False,key="tree_dates")

        # Parent-child graph.
        parent_graph=nx.DiGraph()
        ids=set(labels)
        row_map={r.sim_id:r for _,r in sims.iterrows()}
        for _,r in sims.iterrows():
            parent_graph.add_node(r.sim_id)
            if r.mother_id in ids: parent_graph.add_edge(r.mother_id,r.sim_id)
            if r.father_id in ids: parent_graph.add_edge(r.father_id,r.sim_id)

        f=sid(focus)
        keep={f}
        frontier={f}
        for _ in range(depth):
            nxt=set()
            for node in frontier:
                if view_mode=="Ancestors":
                    nxt |= set(parent_graph.predecessors(node))
                elif view_mode=="Descendants":
                    nxt |= set(parent_graph.successors(node))
                else:
                    nxt |= set(parent_graph.predecessors(node)) | set(parent_graph.successors(node))
            keep |= nxt
            frontier=nxt
            if not frontier:
                break

        # Pull spouse/partner edges from the actual relationship table.
        spouse_edges=[]
        if show_spouses:
            rel=q("""SELECT partner1_id,partner2_id,type,status FROM relationships
                     WHERE partner1_id IS NOT NULL AND partner2_id IS NOT NULL""")
            changed=True
            # Include partners attached to any already visible Sim, but don't recursively expand forever.
            visible=set(keep)
            for _,r in rel.iterrows():
                if r.partner1_id in visible or r.partner2_id in visible:
                    keep.add(r.partner1_id); keep.add(r.partner2_id)
                    spouse_edges.append((r.partner1_id,r.partner2_id,r.type or "Relationship",r.status or ""))

        sub=parent_graph.subgraph([x for x in keep if x in ids]).copy()

        # Summary for the focus Sim.
        focus_row=row_map.get(f)
        ancestors=nx.ancestors(parent_graph,f) if f in parent_graph else set()
        descendants=nx.descendants(parent_graph,f) if f in parent_graph else set()
        partner_count=len({b if a==f else a for a,b,_,_ in spouse_edges if a==f or b==f})
        m1,m2,m3,m4=st.columns(4)
        m1.metric("Visible Sims",len(sub.nodes))
        m2.metric("Recorded ancestors",len(ancestors))
        m3.metric("Recorded descendants",len(descendants))
        m4.metric("Visible partners",partner_count)

        net=Network(height="760px",width="100%",directed=True,bgcolor="#ffffff",font_color="#222222",
                    cdn_resources="in_line")
        net.set_options("""
        {
          "layout": {
            "hierarchical": {
              "enabled": true,
              "direction": "UD",
              "sortMethod": "directed",
              "levelSeparation": 150,
              "nodeSpacing": 170,
              "treeSpacing": 220
            }
          },
          "physics": {"enabled": false},
          "interaction": {"hover": true, "navigationButtons": true, "keyboard": true},
          "edges": {"smooth": {"type": "cubicBezier", "forceDirection": "vertical"}}
        }
        """)

        photo_map={}
        if show_portraits:
            con=connect()
            tree_photos=profiles.get_photos(con,list(sub.nodes))
            for node,pr in tree_photos.items():
                if pr:
                    mime=pr["mime_type"] or "image/jpeg"
                    photo_map[node]=f"data:{mime};base64,{base64.b64encode(pr['image_data']).decode('ascii')}"
            con.close()

        # Prefer stored generation values; fall back to relative generation around focus.
        relative_level={f:0}
        queue=[f]
        while queue:
            node=queue.pop(0)
            base=relative_level[node]
            for p in parent_graph.predecessors(node):
                if p in sub and p not in relative_level:
                    relative_level[p]=base-1; queue.append(p)
            for ch in parent_graph.successors(node):
                if ch in sub and ch not in relative_level:
                    relative_level[ch]=base+1; queue.append(ch)

        focus_gen=None
        try:
            focus_gen=int(focus_row.generation) if focus_row is not None and pd.notna(focus_row.generation) else None
        except Exception:
            focus_gen=None

        for node in sub.nodes:
            r=row_map[node]
            label=labels[node]
            if show_dates:
                dates=" – ".join(x for x in [r.birth_date or "",r.death_date or ""] if x)
                if dates: label=f"{label}\n{dates}"
            title=f"{labels[node]}<br>{r.birth_date or '?'} – {r.death_date or 'Living'}<br>{node}"
            level=None
            if pd.notna(r.generation):
                try: level=int(r.generation)
                except Exception: level=None
            if level is None:
                level=(focus_gen or 0)+relative_level.get(node,0)

            kwargs={"label":label,"title":title,"level":level}
            if node==f:
                kwargs.update({"borderWidth":4,"size":40})
            if node in photo_map:
                kwargs.update({"shape":"circularImage","image":photo_map[node],"size":38})
            else:
                kwargs.update({"shape":"box","margin":10})
            net.add_node(node,**kwargs)

        for parent,child in sub.edges:
            net.add_edge(parent,child,title="Parent → child",arrows="to")

        if show_spouses:
            added=set()
            for a_id,b_id,typ,status in spouse_edges:
                if a_id in sub and b_id in sub:
                    key=tuple(sorted((a_id,b_id)))
                    if key in added: continue
                    added.add(key)
                    net.add_edge(a_id,b_id,title=f"{typ} • {status}",dashes=True,arrows="",color="#888888")

        st.markdown(
            "**Family-tree key:** **→** solid arrow = parent to child &nbsp; | &nbsp; "
            "**┈┈** dashed gray line = marriage or partnership &nbsp; | &nbsp; "
            "**Thick border** = selected Sim"
        )
        st.components.v1.html(net.generate_html(),height=780,scrolling=True)
        st.caption("Solid arrows are parent → child. Dashed lines are marriages/partnerships. Use the navigation buttons to pan and zoom.")

        # Focus profile + direct family list under the visual tree.
        with st.expander("Focus Sim family details",expanded=False):
            parents=[p for p in parent_graph.predecessors(f)] if f in parent_graph else []
            children=[ch for ch in parent_graph.successors(f)] if f in parent_graph else []
            partners=[]
            rels=q("""SELECT * FROM relationships WHERE partner1_id=? OR partner2_id=?
                      ORDER BY start_global_day DESC""",(f,f))
            for _,r in rels.iterrows():
                other=r.partner2_id if r.partner1_id==f else r.partner1_id
                partners.append({
                    "Partner":labels.get(other,other),
                    "Type":r.type,
                    "Status":r.status,
                    "Start GD":r.start_global_day,
                    "End GD":r.end_global_day
                })
            a,b,c=st.columns(3)
            with a:
                st.markdown("**Parents**")
                if parents:
                    for x in parents: st.write(labels.get(x,x))
                else: st.caption("None recorded")
            with b:
                st.markdown("**Children**")
                if children:
                    for x in children: st.write(labels.get(x,x))
                else: st.caption("None recorded")
            with c:
                st.markdown("**Partners / marriages**")
                if partners:
                    friendly_cards(partners,lambda r:r.get("name") or r.get("partner_name") or r.get("sim_id") or "Partner",
                        meta=tuple(key for key in ("type","status","start_global_day") if key in partners[0]),limit=30)
                else: st.caption("None recorded")

@workspace_fragment
def render_timeline():
    import plotly.express as px
    import plotly.graph_objects as go

    page_header("The Great Chronicle","Explore the challenge chronologically without digging through individual tables.")
    chronicle_note(
        "From the keeper of the household annals",
        "Here are gathered the births, unions, deaths, journeys, and turns of fortune witnessed across the generations.",
    )
    st.caption("Everything that happened, in one place. Filter it down to a person, household, category, or time period.")

    c=connect()
    sy=int(float(setting(c,"start_year",1200)))
    dpy=int(float(setting(c,"days_per_year",4)))
    cg=current_gd()
    include_rolls=st.checkbox("Include roll obligations/results",False,key="timeline_include_rolls")
    tdf=timeline_engine.build(c,sy,dpy,include_rolls=include_rolls)
    c.close()

    if tdf.empty:
        st.info("No dated records are available yet.")
    else:
        all_categories=tdf.category.dropna().unique().tolist()
        default_categories=[x for x in all_categories if x!="Roll"]
        with st.expander("Timeline filters",expanded=True):
            a,b=st.columns([2,1])
            categories=a.multiselect("Categories",all_categories,default=default_categories)
            mode=b.radio("Horizontal scale",["Global Day","Historical Year"],horizontal=True)
            a,b=st.columns(2)
            sim_search=a.text_input("Filter to Sim name / ID",key="timeline_sim_search")
            hh_search=b.text_input("Filter to household ID",key="timeline_hh_search")
            min_gd,max_gd=int(tdf.global_day.min()),int(tdf.global_day.max())
            slider_max=max(max_gd,cg)
            default_end=max(min_gd,min(slider_max,cg))
            range_default=(min_gd,default_end)
            gd_range=st.slider("Global Day range",min_value=min_gd,max_value=slider_max,value=range_default,key="timeline_gd_range")
            st.caption("The default view ends at the current save. Drag the upper handle forward to inspect future imported records.")

        view=tdf[(tdf.global_day>=gd_range[0])&(tdf.global_day<=gd_range[1])].copy()
        if categories:
            view=view[view.category.isin(categories)]
        if sim_search:
            ss=sim_search.lower().strip()
            mask=(view.primary_sim.fillna("").str.lower().str.contains(ss,regex=False)
                  |view.primary_sim_id.fillna("").str.lower().str.contains(ss,regex=False)
                  |view.secondary_sim_id.fillna("").str.lower().str.contains(ss,regex=False)
                  |view.title.fillna("").str.lower().str.contains(ss,regex=False))
            view=view[mask]
        if hh_search:
            view=view[view.household_id.fillna("").str.lower().str.contains(hh_search.lower().strip(),regex=False)]

        a,b,c=st.columns(3)
        a.metric("Visible timeline items",len(view))
        b.metric("First visible Global Day",int(view.global_day.min()) if not view.empty else "—")
        c.metric("Last visible Global Day",int(view.global_day.max()) if not view.empty else "—")

        timeline_section=st.segmented_control("Timeline section",["Chronicle","Lives","Decades"],default=None,label_visibility="collapsed",key="timeline_section") or "Chronicle"

        if timeline_section=="Chronicle":
            if view.empty:
                st.info("Nothing matches these filters.")
            else:
                xcol="global_day" if mode=="Global Day" else "year"
                fig=px.scatter(
                    view,x=xcol,y="category",color="category",
                    hover_name="title",
                    hover_data={"global_day":True,"year":True,"details":True,"source_id":True,
                                "primary_sim":True,"household_id":True,"category":False},
                    title="Unified challenge timeline"
                )
                current_x=cg if mode=="Global Day" else challenge_year_day(cg)[0]
                fig.add_vline(x=current_x,line_dash="dash",annotation_text="Current",annotation_position="top")
                fig.update_traces(marker={"size":10,"opacity":0.8})
                fig.update_layout(height=max(520,120+len(view.category.unique())*35),legend_title_text="Category")
                fig.update_xaxes(rangeslider_visible=True)
                st.plotly_chart(fig,use_container_width=True)
                st.subheader("Entries from the chronicle")
                feed=view.sort_values(["global_day","category","title"],ascending=[False,True,True])[
                    ["global_day","year","category","title","primary_sim","household_id","details","source_id"]
                ].copy()
                feed.insert(0,"Chronicle entry",feed.apply(lambda row:timeline_chronicle_entry(row,sy,dpy),axis=1))
                friendly_cards(feed,"title",
                    meta=(lambda r:("When",f"Year {r.get('year')} · GD {r.get('global_day')}"),
                          "category","primary_sim","household_id"),
                    body="Chronicle entry",badge="source_id",limit=80)

        if timeline_section=="Lives":
            simsdf=q("""SELECT sim_id,TRIM(COALESCE(title,'')||' '||COALESCE(first_name,'')||' '||COALESCE(last_name,'')||' '||COALESCE(suffix,'')) name,
                               birth_global_day,death_global_day,generation,current_household_id
                        FROM sims WHERE birth_global_day IS NOT NULL ORDER BY last_name,first_name""")
            sim_labels=[f"{r.sim_id} — {r['name'].strip()}" for _,r in simsdf.iterrows()]
            default_ids=[]
            heir_id=scalar("SELECT value FROM settings WHERE key='current_heir_id'",default="")
            for lab in sim_labels:
                if str(lab).startswith(str(heir_id)+" —"):
                    default_ids=[lab]
                    break
            selected=st.multiselect("Sims to compare",sim_labels,default=default_ids,max_selections=30)
            if selected:
                ids=[x.split(" — ",1)[0] for x in selected]
                sdf=simsdf[simsdf.sim_id.isin(ids)].copy()
                fig=go.Figure()
                for _,r in sdf.iterrows():
                    end=int(r.death_global_day) if pd.notna(r.death_global_day) else cg
                    status="Died" if pd.notna(r.death_global_day) else "Living / current"
                    fig.add_trace(go.Scatter(
                        x=[int(r.birth_global_day),end],y=[r["name"],r["name"]],
                        mode="lines+markers",name=r["name"],
                        hovertemplate=f"{r['name']}<br>Born GD {int(r.birth_global_day)}<br>End GD {end}<br>{status}<extra></extra>"
                    ))
                fig.add_vline(x=cg,line_dash="dash",annotation_text="Current")
                fig.update_layout(title="Sim lifespans on the Global Day axis",height=max(420,100+len(sdf)*36),
                                  showlegend=False,xaxis_title="Global Day",yaxis_title="")
                fig.update_xaxes(rangeslider_visible=True)
                st.plotly_chart(fig,use_container_width=True)
                friendly_cards(sdf,"name",
                    meta=(lambda r:("Born",f"GD {r.get('birth_global_day')}"),
                          lambda r:("Died",f"GD {r.get('death_global_day')}" if pd.notna(r.get('death_global_day')) else "Living"),
                          "generation","current_household_id"),badge="sim_id",limit=30)
            else:
                st.caption("Choose one or more Sims to compare their lifespans.")

        if timeline_section=="Decades":
            d=view.copy()
            if d.empty:
                st.info("No timeline items in this range.")
            else:
                summary=d.groupby(["decade","category"]).size().reset_index(name="events")
                fig=px.bar(summary,x="decade",y="events",color="category",title="Timeline activity by decade")
                st.plotly_chart(fig,use_container_width=True)
                pivot=summary.pivot_table(index="decade",columns="category",values="events",fill_value=0).reset_index()
                pivot["Total"]=pivot.drop(columns=["decade"]).sum(axis=1)
                friendly_cards(pivot,lambda r:f"The {int(r.get('decade'))}s",
                    meta=(lambda r:("Recorded events",int(r.get("Total") or 0)),),
                    body=lambda r:", ".join(f"{k}: {int(v)}" for k,v in r.items() if k not in ("decade","Total") and pd.notna(v) and int(v)>0),
                    badge="Total",limit=40)

@workspace_fragment
def render_pregnancies():
    page_header("Pregnancies","Track active pregnancies, deliveries, and outcomes.")
    st.caption("Add pregnancies, record delivery/outcome details, or revise an existing record.")

    preg_section=st.segmented_control("Pregnancy section",["Browse","Add","Record outcome"],default=None,label_visibility="collapsed",key="preg_section") or "Browse"
    if preg_section=="Browse":
        pdf=q("SELECT * FROM pregnancies ORDER BY due_global_day DESC,pregnancy_id")
        active_count=int(pdf.status.fillna("").isin(["Pregnant",""]).sum()) if not pdf.empty else 0
        completed_count=int(pdf.status.fillna("").isin(["Delivered","Complete"]).sum()) if not pdf.empty else 0
        loss_count=int(pdf.status.fillna("").isin(["Miscarriage","Stillbirth"]).sum()) if not pdf.empty else 0
        a,b,c=st.columns(3)
        a.metric("Active",active_count); b.metric("Delivered / complete",completed_count); c.metric("Pregnancy losses",loss_count)
        status_filter=st.multiselect(
            "Filter by status",
            sorted([x for x in pdf.status.dropna().unique().tolist() if str(x).strip()]),
            default=[]
        )
        if status_filter:
            pdf=pdf[pdf.status.fillna("").isin(status_filter)]
        friendly_cards(pdf,lambda r:r.get("mother_name") or r.get("mother_id") or "Unknown mother",
            meta=(lambda r:("Father",r.get("father_name") or r.get("father_id")),
                  lambda r:("Conceived",f"GD {r.get('conception_global_day')}"),
                  lambda r:("Due",f"GD {r.get('due_global_day')}"),
                  lambda r:("Babies",r.get("babies_delivered") or r.get("babies_expected"))),
            body=lambda r:r.get("outcome") or r.get("complication") or r.get("notes"),
            badge=lambda r:r.get("status") or "Pregnant",limit=60)

    if preg_section=="Add":
        opts=sim_options()
        a,b,c=st.columns(3)
        mother=a.selectbox("Mother",opts,key="preg_add_mother")
        father=b.selectbox("Father",opts,key="preg_add_father")
        conception=c.number_input("Conception Global Day",min_value=-10000,max_value=20000,value=current_gd(),step=1,key="preg_add_conception")
        plen=int(float(rule_value('Pregnancy Length (challenge days)',3)))
        due=conception+plen
        st.info(f"Due Global Day **{due}** — {gd_caption(due)}")
        mother_id=sid(mother)
        if mother_id:
            spacing=q("""SELECT p.min_birth_spacing_days,
                (SELECT MAX(birth_global_day) FROM sims WHERE mother_id=?) AS last_birth
                FROM sim_family_plans p WHERE p.sim_id=?""",(mother_id,mother_id))
            if not spacing.empty:
                minimum=int(spacing.iloc[0].get("min_birth_spacing_days") or 0)
                last_birth=spacing.iloc[0].get("last_birth")
                if minimum and pd.notna(last_birth):
                    gap=int(conception)-int(last_birth)
                    if gap<minimum:
                        st.warning(f"Birth-spacing plan: only {gap} challenge day(s) since the last birth; this Sim's minimum is {minimum}.")
                    else:
                        st.success(f"Birth-spacing plan met: {gap} challenge days since the last birth.")
        a,b,c=st.columns(3)
        expected=a.number_input("Babies expected",1,10,1,key="preg_add_expected")
        status=b.selectbox("Starting status",["Pregnant","Other"],key="preg_add_status")
        notes=c.text_input("Notes",key="preg_add_notes")
        if st.button("Add pregnancy",type="primary",key="preg_add_btn"):
            con=connect()
            pid=next_id(con,'pregnancies','pregnancy_id','PREG')
            mid=sid(mother); fid=sid(father)
            names={r[0]:r[1] for r in con.execute("SELECT sim_id,TRIM(COALESCE(title,'')||' '||COALESCE(first_name,'')||' '||COALESCE(last_name,'')) FROM sims")}
            con.execute("""INSERT INTO pregnancies(
                pregnancy_id,mother_id,mother_name,father_id,father_name,conception_global_day,due_global_day,
                babies_expected,status,notes
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (pid,mid,names.get(mid),fid,names.get(fid),conception,due,expected,status,notes or None))
            con.commit(); con.close()
            sync_auto_rolls(show_notice=False)
            st.success(f"Added {pid}; maternal roll schedule refreshed.")

    if preg_section=="Record outcome":
        allp=q("""SELECT pregnancy_id,mother_id,mother_name,father_id,father_name,conception_global_day,due_global_day,status,
                         babies_expected,babies_delivered,delivery_date,outcome,complication,multiple_rule_check,notes
                  FROM pregnancies ORDER BY due_global_day DESC,pregnancy_id""")
        if allp.empty:
            st.info("No pregnancy records yet.")
        else:
            labels=[
                f"{r.pregnancy_id} — {r.mother_name or r.mother_id or ''} — due GD {int(r.due_global_day) if pd.notna(r.due_global_day) else '?'} — {r.status or 'No status'}"
                for _,r in allp.iterrows()
            ]
            choice=st.selectbox("Pregnancy",labels,key="preg_update_select")
            pid=choice.split(" — ",1)[0]
            rr=q("SELECT * FROM pregnancies WHERE pregnancy_id=?",(pid,))
            pr=rr.iloc[0].to_dict()
            st.caption(f"Conceived GD {pr.get('conception_global_day')} • Due GD {pr.get('due_global_day')}")
            a,b,c=st.columns(3)
            statuses=["Pregnant","Delivered","Complete","Miscarriage","Stillbirth","Cancelled","Other"]
            cur=pr.get("status") or "Pregnant"
            if cur not in statuses: statuses=[cur]+statuses
            status=a.selectbox("Status",statuses,index=statuses.index(cur),key="preg_update_status")
            babies=b.number_input("Babies delivered",0,10,int(pr.get("babies_delivered") or 0),key="preg_update_babies")
            delivery_gd=c.text_input("Delivery Global Day",value=str(pr.get("due_global_day") or ""),key="preg_update_delivery_gd")
            if int_or_none(delivery_gd) is not None:
                st.caption(gd_caption(int_or_none(delivery_gd)))
            a,b=st.columns(2)
            outcome=a.text_area("Outcome",value=pr.get("outcome") or "",placeholder="e.g. Live birth, miscarriage, mother survived, etc.",key="preg_update_outcome")
            complication=b.text_area("Complication",value=pr.get("complication") or "",placeholder="e.g. None, hemorrhage, breech, infection",key="preg_update_complication")
            a,b=st.columns(2)
            multiple=a.text_input("Multiple-rule check / result",value=pr.get("multiple_rule_check") or "",key="preg_update_multiple")
            notes=b.text_area("Notes",value=pr.get("notes") or "",key="preg_update_notes")
            mark_complete=st.checkbox("Mark complete when saving",value=status in ["Delivered","Complete","Miscarriage","Stillbirth","Cancelled"],key="preg_update_complete")
            if st.button("Save pregnancy outcome",type="primary",key="preg_update_save"):
                final_status="Complete" if mark_complete and status=="Delivered" else status
                con=connect()
                con.execute("""UPDATE pregnancies SET status=?,babies_delivered=?,delivery_date=?,outcome=?,
                               complication=?,multiple_rule_check=?,notes=? WHERE pregnancy_id=?""",
                            (final_status,babies,
                             gd_caption(int_or_none(delivery_gd)) if int_or_none(delivery_gd) is not None else pr.get("delivery_date"),
                             outcome or None,complication or None,multiple or None,notes or None,pid))
                if final_status.strip().lower()=="miscarriage":
                    con.execute(
                        "DELETE FROM rolls WHERE source_id=? AND lower(COALESCE(roll_type,'')) LIKE ?",
                        (pid,"maternal%"),
                    )
                con.commit(); con.close()
                sync_auto_rolls(show_notice=False)
                st.success(f"Saved outcome for {pid}; roll schedule refreshed.")
            with st.expander("Delete this pregnancy",expanded=False):
                st.warning("This permanently deletes the pregnancy record and its linked rolls.")
                preg_delete_confirm=st.text_input(f"Type {pid} to confirm deletion",key=f"preg_delete_confirm_{pid}")
                if st.button(
                    "Permanently delete pregnancy",disabled=preg_delete_confirm.strip()!=pid,
                    key=f"preg_delete_btn_{pid}"
                ):
                    con=connect()
                    try:
                        admin_ops.delete_pregnancy(con,pid)
                    except Exception as error:
                        con.rollback(); con.close(); st.error(f"Could not delete pregnancy: {error}")
                    else:
                        con.close(); st.success(f"Deleted {pid} and its linked rolls."); st.rerun()


@workspace_fragment
def render_rolls():
    page_header("The Book of Trials","See what is due, record outcomes, and inspect the automatic schedule.")
    chronicle_note(
        "Recorded by those who endured",
        "Each appointed trial is set down in the voice of the person who faced it; unwritten outcomes remain open until fate is decided.",
    )
    roll_section=st.segmented_control("Roll section",["Chronicle","Enter outcome","Add roll","Upcoming"],default=None,label_visibility="collapsed",key="roll_section") or "Chronicle"
    if roll_section=="Chronicle":
        a,b=st.columns([1,1])
        only_open=a.checkbox("Only incomplete",True,key="roll_browse_open")
        cutoff=b.number_input("Due through Global Day",min_value=-10000,max_value=20000,value=current_gd(),key="roll_browse_cutoff")
        where="due_global_day<=?"+(" AND COALESCE(completed,0)=0" if only_open else "")
        total=scalar(f"SELECT COUNT(*) FROM rolls WHERE {where}",(cutoff,))
        overdue=scalar(f"SELECT COUNT(*) FROM rolls WHERE {where} AND due_global_day<?",(cutoff,current_gd()))
        due_today=scalar(f"SELECT COUNT(*) FROM rolls WHERE {where} AND due_global_day=?",(cutoff,current_gd()))
        pages=max(1,(int(total)+79)//80)
        page_no=st.number_input("Chronicle page",1,pages,1,key="roll_chronicle_page") if pages>1 else 1
        rdf=q(f"SELECT * FROM rolls WHERE {where} ORDER BY due_global_day,sim_name,roll_type LIMIT 80 OFFSET ?",(cutoff,(int(page_no)-1)*80))
        a,b,c=st.columns(3)
        a.metric("Matching",total); b.metric("Overdue",overdue); c.metric("Due today",due_today)
        roll_sy,roll_dpy=calendar_settings()
        if not rdf.empty:
            rdf=rdf.copy()
            rdf.insert(0,"Chronicle entry",rdf.apply(lambda row:roll_chronicle_entry(row,roll_sy,roll_dpy),axis=1))
        friendly_cards(rdf,lambda r:r.get("roll_type") or "Appointed trial",
            meta=(lambda r:("Sim",r.get("sim_name") or r.get("sim_id")),
                  lambda r:("Due",f"GD {r.get('due_global_day')}"),"die",
                  lambda r:("Result",r.get("actual_roll"))),
            body=lambda r:r.get("outcome") or r.get("Chronicle entry") or f"Bad results: {r.get('bad_results') or 'Use current rules'}",
            badge=lambda r:"Complete" if r.get("completed") else "Open",limit=80)

    if roll_section=="Enter outcome":
        roll_find=st.text_input("Find a roll",placeholder="Sim, roll type, or roll ID",key="roll_edit_search")
        if roll_find.strip():
            like=f"%{roll_find.strip()}%"
            rdf=q("""SELECT * FROM rolls WHERE roll_id LIKE ? OR COALESCE(sim_name,'') LIKE ? OR COALESCE(roll_type,'') LIKE ?
                     ORDER BY due_global_day DESC,roll_id LIMIT 150""",(like,like,like))
        else:
            rdf=q("SELECT * FROM rolls ORDER BY COALESCE(completed,0),due_global_day DESC,roll_id LIMIT 150")
            st.caption("Showing the 150 most relevant rolls. Search to reach older records instantly.")
        if rdf.empty:
            st.info("No rolls recorded.")
        else:
            labels=[f"{r.roll_id} — GD {int(r.due_global_day) if pd.notna(r.due_global_day) else '?'} — {r.sim_name or r.sim_id or ''} — {r.roll_type or ''}" for _,r in rdf.iterrows()]
            choice=st.selectbox("Roll",labels,key="roll_edit_select")
            rid=choice.split(" — ",1)[0]
            row=q("SELECT * FROM rolls WHERE roll_id=?",(rid,)).iloc[0].to_dict()
            a,b,c=st.columns(3)
            actual_key=f"roll_edit_actual_{rid}"
            outcome_key=f"roll_edit_outcome_{rid}"
            actual=a.text_input("Actual roll",value=str(row.get("actual_roll") or ""),key=actual_key)
            automatic=roll_outcomes.automatic_outcome(actual,row.get("bad_results"),row.get("roll_type"),row.get("die"))
            if automatic is not None:
                st.session_state[outcome_key]=automatic
            outcome=b.text_input("Outcome",value=automatic or row.get("outcome") or "",key=outcome_key,
                                 help="Calculated from the editable Bad results rule when a numeric roll is entered.")
            completed_day=c.number_input("Completed Global Day",-10000,20000,int(row.get("completed_global_day") or current_gd()),key="roll_edit_day")
            a,b=st.columns(2)
            completed=a.checkbox("Completed",value=bool(row.get("completed") or 0),key="roll_edit_completed")
            notes=b.text_input("Notes",value=row.get("notes") or "",key="roll_edit_notes")
            if st.button("Save roll",type="primary",key="roll_edit_save"):
                con=connect()
                con.execute("""UPDATE rolls SET actual_roll=?,outcome=?,completed=?,completed_global_day=?,notes=?
                               WHERE roll_id=?""",
                            (actual or None,outcome or None,1 if completed else 0,completed_day if completed else None,notes or None,rid))
                death_record=None
                if completed and row.get("sim_id") and should_record_roll_death(row,outcome):
                    death_record=random_death_for_roll(row,actual)
                    con.execute("""UPDATE sims SET death_global_day=COALESCE(death_global_day,?),
                                   death_date=COALESCE(death_date,?),cause_of_death=COALESCE(cause_of_death,?)
                                   WHERE sim_id=?""",(*death_record,row.get("sim_id")))
                con.commit(); con.close()
                st.success(f"Saved {rid}."+(f" Death recorded as {death_record[1]} (GD {death_record[0]})." if death_record else ""))

    if roll_section=="Add roll":
        opts=sim_options()
        a,b,c=st.columns(3)
        sim=a.selectbox("Sim",opts,key="roll_add_sim")
        due=b.number_input("Due Global Day",-10000,20000,current_gd(),key="roll_add_due")
        rtype=c.text_input("Roll type",key="roll_add_type")
        a,b,c=st.columns(3)
        die=a.text_input("Die",placeholder="e.g. d20",key="roll_add_die")
        bad=b.text_input("Bad results",key="roll_add_bad")
        source=c.text_input("Source ID",key="roll_add_source")
        if st.button("Add roll",key="roll_add_btn"):
            con=connect(); rid=next_id(con,'rolls','roll_id','ROLL')
            ss=sid(sim)
            nm=con.execute("SELECT TRIM(COALESCE(title,'')||' '||COALESCE(first_name,'')||' '||COALESCE(last_name,'')) FROM sims WHERE sim_id=?",(ss,)).fetchone()
            con.execute("""INSERT INTO rolls(roll_id,due_global_day,sim_id,sim_name,source_id,roll_type,die,bad_results,completed)
                           VALUES(?,?,?,?,?,?,?,?,0)""",(rid,due,ss,nm[0] if nm else None,source or None,rtype or None,die or None,bad or None))
            con.commit(); con.close(); st.success(f"Added {rid}")



    if roll_section=="Upcoming":
        st.subheader("Automatic roll schedule")
        st.caption("Milestone timing comes from Rules Config; die and bad-result values come from the matching year/species roll table. Due rolls are inserted automatically and future rolls stay as previews.")
        a,b,c=st.columns(3)
        a.metric("Current Global Day",current_gd())
        b.metric("Automatic tracking begins",1)
        c.metric("Rules source","Era-aware tables")
        if st.button("Refresh automatic rolls now",type="primary",key="auto_roll_refresh"):
            sync_auto_rolls(show_notice=True)

        horizon=st.slider("Preview the next Global Days",1,240,40,key="auto_roll_horizon")
        rows=cached_upcoming_rolls(current_gd(),horizon)
        if rows:
            adf=pd.DataFrame(rows)
            missing=adf[adf["missing"]>0].copy()
            st.metric("Upcoming obligations",len(adf),f"{len(missing)} future roll(s) not inserted yet")
            friendly_cards(adf,lambda r:r.get("roll_type") or "Upcoming trial",
                meta=(lambda r:("Sim",r.get("sim_name") or r.get("sim_id")),
                      lambda r:("When",f"Year {r.get('year')} · GD {r.get('due_global_day')}"),
                      "die",lambda r:("Kind",r.get("kind"))),
                body=lambda r:f"Bad results: {r.get('bad_results') or 'Not configured'}",
                badge=lambda r:r.get("rule_status") or "Scheduled",limit=60)
            if not missing.empty:
                st.caption("Future rows marked missing are intentional: they will be created automatically when their Global Day arrives.")
        else:
            st.info("No automatic obligations in this preview window.")

        with st.expander("Repair one Sim's lifecycle schedule",expanded=False):
            st.caption("Use this for a Sim entered before automatic newborn scheduling was enabled. Existing and completed rolls are preserved.")
            repair_options=sim_options(blank=False)
            repair_sim=st.selectbox("Sim to repair",repair_options,key="roll_repair_sim") if repair_options else None
            if st.button("Schedule missing lifecycle rolls",disabled=not bool(repair_sim),key="roll_repair_btn"):
                repair_id=sid(repair_sim)
                con=connect()
                birth_row=con.execute("SELECT birth_global_day FROM sims WHERE sim_id=?",(repair_id,)).fetchone()
                through=max(current_gd(),int(birth_row[0]) if birth_row and birth_row[0] is not None else current_gd())
                added=autorolls.schedule_sim_lifecycle(con,repair_id,through)
                con.close()
                st.success(f"Scheduled {added} missing lifecycle roll(s) for {repair_id}.")

        st.markdown("**Automatically supported from your current rules:**")
        st.write("Being Born, Newborn, Infant, Toddler, Child, Preteen, Teen, Young Adult, Adult, Elder Death-Age RNG, maternal follow-up rolls, and historical-event rolls for eligible living Sims.")
        st.caption("Add or edit later-year and occult/species roll tables under Rules & Data → Roll Tables. The scheduler automatically selects the matching table by historical year and species.")


@workspace_fragment
def render_relationships():
    page_header("Relationships","Browse partnerships by name, see both people together, and add or end relationships without editing spouse IDs.")

    relationship_section=st.segmented_control("Relationship section",["Browse","Add","Edit or end"],default=None,label_visibility="collapsed",key="relationship_section") or "Browse"

    if relationship_section=="Browse":
        relationship_total=scalar("SELECT COUNT(*) FROM relationships")
        active=scalar("SELECT COUNT(*) FROM relationships WHERE LOWER(COALESCE(status,''))='active'")
        a,b,c=st.columns(3)
        a.metric("Relationships",relationship_total); b.metric("Active",active); c.metric("Ended",relationship_total-active)

        search=st.text_input("Find a person",key="rel_browse_search",placeholder="Type either partner's name…")
        if search.strip():
            like=f"%{search.strip().lower()}%"
            view=q("""SELECT * FROM relationships WHERE LOWER(COALESCE(partner1_name,'')) LIKE ?
                       OR LOWER(COALESCE(partner2_name,'')) LIKE ? OR LOWER(COALESCE(partner1_id,'')) LIKE ?
                       OR LOWER(COALESCE(partner2_id,'')) LIKE ? ORDER BY start_global_day DESC,relationship_id LIMIT 100""",
                   (like,like,like,like))
        else:
            view=q("SELECT * FROM relationships ORDER BY start_global_day DESC,relationship_id LIMIT 100")
            if relationship_total>100: st.caption("Showing the 100 most recent relationships. Search by either person to reach older records.")
        friendly_cards(view,lambda r:f"{r.get('partner1_name') or r.get('partner1_id')} + {r.get('partner2_name') or r.get('partner2_id')}",
            meta=("type",lambda r:("Started",f"Global Day {r.get('start_global_day')}"),"location",lambda r:("Children",r.get("children_count"))),
            body="notes",badge=lambda r:r.get("status") or "Unknown",empty="No relationships match this search.")

        if not view.empty:
            labels=[f"{r.partner1_name or r.partner1_id} + {r.partner2_name or r.partner2_id} — {r.type or 'Relationship'} — {r.relationship_id}"
                    for _,r in view.iterrows()]
            pick=st.selectbox("Open relationship",labels,key="rel_browse_pick")
            rid=pick.rsplit(" — ",1)[-1]
            row=q("SELECT * FROM relationships WHERE relationship_id=?",(rid,)).iloc[0].to_dict()
            p1photo=cached_sim_photo(row.get("partner1_id"))
            p2photo=cached_sim_photo(row.get("partner2_id"))
            marriage_photo=cached_relationship_photo(rid)
            left,mid,right=st.columns([1,2,1])
            with left:
                if p1photo: st.image(p1photo["image_data"],width=180)
                else: st.markdown("## 👤")
                st.markdown(f"**{row.get('partner1_name') or row.get('partner1_id') or 'Unknown'}**")
            with mid:
                if marriage_photo:
                    st.image(marriage_photo["image_data"],use_column_width=True)
                st.markdown("### 💞")
                st.markdown(f"**{row.get('type') or 'Relationship'}**")
                st.markdown(status_badge(row.get("status") or "Unknown"),unsafe_allow_html=True)
                st.caption(f"Started GD {row.get('start_global_day') if row.get('start_global_day') is not None else '—'}")
                if row.get("end_global_day") is not None:
                    st.caption(f"Ended GD {row.get('end_global_day')}")
                if row.get("location"): st.caption(row.get("location"))
                if row.get("notes"): st.write(row.get("notes"))
            with right:
                if p2photo: st.image(p2photo["image_data"],width=180)
                else: st.markdown("## 👤")
                st.markdown(f"**{row.get('partner2_name') or row.get('partner2_id') or 'Unknown'}**")

    if relationship_section=="Add":
        st.subheader("Add a relationship")
        st.caption("Choose the two people by name. Marriage spouse links are updated automatically.")
        opts=sim_options()
        a,b=st.columns(2)
        p1=a.selectbox("First person",opts,key="rel_add_p1")
        p2=b.selectbox("Second person",opts,key="rel_add_p2")
        a,b=st.columns(2)
        typ=a.selectbox("Relationship type",["Marriage","Engagement","Partnership","Other"],key="rel_add_type")
        start=b.number_input("Start Global Day",-10000,20000,current_gd(),key="rel_add_start")
        a,b=st.columns(2)
        relationship_clock=a.time_input("In-game start time",value=time(0,0),step=60,key="rel_add_clock")
        relationship_date=exact_date_from_ingame_time(start,relationship_clock)
        b.text_input("Calculated exact start date",value=relationship_date or "",disabled=True,key="rel_add_exact_date")
        a,b=st.columns(2)
        location=a.text_input("Location",key="rel_add_location")
        status=b.selectbox("Starting status",["Active","Other"],key="rel_add_status")
        marriage_photo=st.file_uploader(
            "Marriage / couple portrait (optional)",type=["png","jpg","jpeg","webp"],key="rel_add_photo",
            help="Stored inside this private save and included in exports and backups."
        )
        notes=st.text_area("Notes",key="rel_add_notes")
        if st.button("Add relationship",type="primary",use_container_width=True,key="rel_add_btn"):
            s1,s2=sid(p1),sid(p2)
            if not s1 or not s2:
                st.error("Choose both people.")
            elif s1==s2:
                st.error("A Sim cannot be in a relationship with themselves.")
            else:
                con=connect()
                existing=con.execute("""SELECT relationship_id FROM relationships
                                        WHERE ((partner1_id=? AND partner2_id=?) OR (partner1_id=? AND partner2_id=?))
                                          AND lower(COALESCE(type,''))=lower(?)
                                          AND lower(COALESCE(status,''))='active'
                                        LIMIT 1""",(s1,s2,s2,s1,typ)).fetchone()
                if existing:
                    con.close(); st.warning(f"An active {typ.lower()} already exists between these Sims ({existing[0]}).")
                else:
                    rid=next_id(con,'relationships','relationship_id','REL')
                    names={r[0]:r[1] for r in con.execute("SELECT sim_id,TRIM(COALESCE(title,'')||' '||COALESCE(first_name,'')||' '||COALESCE(last_name,'')) FROM sims")}
                    con.execute("""INSERT INTO relationships(
                        relationship_id,partner1_id,partner2_id,partner1_name,partner2_name,type,start_global_day,start_date,status,
                        location,legally_married,notes
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (rid,s1,s2,names.get(s1),names.get(s2),typ,start,relationship_date,status,location or None,1 if typ=="Marriage" else 0,notes or None))
                    if marriage_photo is not None:
                        try:
                            relationship_photos.save_photo(con,rid,marriage_photo)
                        except ValueError as error:
                            con.rollback(); con.close(); st.error(str(error)); st.stop()
                    profiles.sync_spouse_ids(con,[s1,s2])
                    con.commit(); con.close()
                    st.success(f"Added {typ.lower()} between {names.get(s1,s1)} and {names.get(s2,s2)}.")
                    st.rerun()

    if relationship_section=="Edit or end":
        rdf=q("SELECT * FROM relationships ORDER BY start_global_day DESC,relationship_id")
        if rdf.empty:
            st.info("No relationships to edit.")
        else:
            labels=[f"{r.partner1_name or r.partner1_id} + {r.partner2_name or r.partner2_id} — {r.type or 'Relationship'} — {r.relationship_id}"
                    for _,r in rdf.iterrows()]
            choice=st.selectbox("Choose relationship",labels,key="rel_edit_select")
            rid=choice.rsplit(" — ",1)[-1]
            row=q("SELECT * FROM relationships WHERE relationship_id=?",(rid,)).iloc[0].to_dict()
            current_marriage_photo=cached_relationship_photo(rid)
            partner1_photo=cached_sim_photo(row.get("partner1_id"))
            partner2_photo=cached_sim_photo(row.get("partner2_id"))
            st.markdown(f"### {row.get('partner1_name') or row.get('partner1_id')} + {row.get('partner2_name') or row.get('partner2_id')}")
            if current_marriage_photo:
                st.image(current_marriage_photo["image_data"],width=420)
            a,b,c=st.columns(3)
            type_options=["Marriage","Engagement","Partnership","Other"]
            cur_type=row.get("type") or "Other"
            if cur_type not in type_options: type_options=[cur_type]+type_options
            typ=a.selectbox("Type",type_options,index=type_options.index(cur_type),key="rel_edit_type")
            start=b.number_input("Start Global Day",-10000,20000,int(row.get("start_global_day") or current_gd()),key="rel_edit_start")
            end=c.text_input("End Global Day",value=str(row.get("end_global_day") or ""),placeholder="Leave blank while active",key="rel_edit_end")
            a,b,c=st.columns(3)
            relationship_clock=a.time_input("In-game start time",value=time(0,0),step=60,key=f"rel_edit_clock_{rid}")
            auto_relationship_date=b.checkbox("Calculate exact start date",value=False,key=f"rel_edit_auto_date_{rid}")
            calculated_relationship_date=exact_date_from_ingame_time(start,relationship_clock) if auto_relationship_date else None
            relationship_start_date=c.text_input(
                "Exact start date",value=calculated_relationship_date or row.get("start_date") or "",
                disabled=auto_relationship_date,key=f"rel_edit_exact_date_{rid}"
            )
            a,b=st.columns(2)
            statuses=["Active","Ended by death","Separated","Divorced","Ended","Other"]
            cur=row.get("status") or "Active"
            if cur not in statuses: statuses=[cur]+statuses
            status=a.selectbox("Status",statuses,index=statuses.index(cur),key="rel_edit_status")
            location=b.text_input("Location",value=row.get("location") or "",key="rel_edit_location")
            marriage_photo=st.file_uploader(
                "Upload or replace marriage / couple portrait",type=["png","jpg","jpeg","webp"],key=f"rel_edit_photo_{rid}"
            )
            remove_marriage_photo=st.checkbox(
                "Remove the current marriage portrait when saving",value=False,
                disabled=not bool(current_marriage_photo),key=f"rel_remove_photo_{rid}"
            )
            st.markdown("#### Generate a marriage portrait with AI")
            marriage_year=challenge_year_day(start)[0]
            missing_portraits=[]
            if not partner1_photo: missing_portraits.append(row.get("partner1_name") or "Partner 1")
            if not partner2_photo: missing_portraits.append(row.get("partner2_name") or "Partner 2")
            if missing_portraits:
                st.info("Add an individual portrait for " + " and ".join(missing_portraits) + " before generating a couple portrait.")
            elif not marriage_ai.configured():
                st.info("AI generation is ready, but the app owner must add OPENAI_API_KEY to Railway first.")
            else:
                st.caption(
                    f"Uses both individual portraits as identity references and creates era-accurate wedding clothing for {marriage_year}. "
                    "Generation uses the paid OpenAI API."
                )
                allow_replace=True
                if current_marriage_photo:
                    allow_replace=st.checkbox(
                        "Replace the current marriage portrait",value=False,key=f"rel_ai_replace_{rid}"
                    )
                if st.button(
                    f"Generate {marriage_year} marriage portrait",type="secondary",use_container_width=True,
                    disabled=not allow_replace,key=f"rel_ai_generate_{rid}"
                ):
                    with st.spinner("Creating the era-accurate marriage portrait…"):
                        try:
                            generated=marriage_ai.generate_portrait(
                                partner1_photo,partner2_photo,
                                row.get("partner1_name") or row.get("partner1_id") or "Partner 1",
                                row.get("partner2_name") or row.get("partner2_id") or "Partner 2",
                                marriage_year,
                            )
                            con=connect()
                            relationship_photos.save_photo_bytes(
                                con,rid,generated,"image/png",f"marriage-{rid}-{marriage_year}-ai.png"
                            )
                            con.commit(); con.close()
                        except Exception as error:
                            st.error(f"The portrait could not be generated: {error}")
                        else:
                            st.success("Marriage portrait generated and saved privately to this save.")
                            st.rerun()
            notes=st.text_area("Notes",value=row.get("notes") or "",key="rel_edit_notes")
            if status!="Active" and not int_or_none(end):
                st.info("If this relationship has ended, enter the ending Global Day above.")
            if st.button("Save relationship",type="primary",use_container_width=True,key="rel_edit_save"):
                con=connect()
                con.execute("""UPDATE relationships SET type=?,start_global_day=?,start_date=?,end_global_day=?,status=?,location=?,notes=?,legally_married=?
                               WHERE relationship_id=?""",
                            (typ,start,(calculated_relationship_date if auto_relationship_date else relationship_start_date) or None,
                             int_or_none(end),status,location or None,notes or None,1 if typ=="Marriage" else 0,rid))
                if remove_marriage_photo:
                    relationship_photos.delete_photo(con,rid)
                elif marriage_photo is not None:
                    try:
                        relationship_photos.save_photo(con,rid,marriage_photo)
                    except ValueError as error:
                        con.rollback(); con.close(); st.error(str(error)); st.stop()
                profiles.sync_spouse_ids(con,[row.get("partner1_id"),row.get("partner2_id")])
                con.commit(); con.close()
                st.success("Relationship saved.")
                st.rerun()
            with st.expander("Delete this marriage / relationship",expanded=False):
                st.warning("This permanently deletes the relationship and its saved couple portrait. The Sims are not deleted.")
                rel_delete_confirm=st.text_input(f"Type {rid} to confirm deletion",key=f"rel_delete_confirm_{rid}")
                if st.button(
                    "Permanently delete relationship",disabled=rel_delete_confirm.strip()!=rid,
                    key=f"rel_delete_btn_{rid}"
                ):
                    con=connect()
                    try:
                        admin_ops.delete_relationship(con,rid)
                    except Exception as error:
                        con.rollback(); con.close(); st.error(f"Could not delete relationship: {error}")
                    else:
                        con.close(); st.success(f"Deleted {rid}."); st.rerun()

@workspace_fragment
def render_households():
    page_header("Households","Create households, view members, move Sims, and edit household details.")
    household_section=st.segmented_control("Household section",["Browse","Create","Move Sim","Edit"],default=None,label_visibility="collapsed",key="household_section") or "Browse"

    if household_section=="Browse":
        household_total=scalar("SELECT COUNT(*) FROM households")
        active_households=scalar("SELECT COUNT(*) FROM households WHERE COALESCE(active,0)=1")
        hdf=q("SELECT * FROM households ORDER BY household_name,household_id LIMIT 150")
        a,b,c=st.columns(3)
        a.metric("Households",household_total)
        b.metric("Active",active_households)
        c.metric("Living members",scalar("SELECT COUNT(*) FROM sims WHERE death_global_day IS NULL AND current_household_id IS NOT NULL"))
        friendly_cards(hdf,lambda r:r.get("household_name") or r.get("household_id"),
            meta=("location",lambda r:("Class",r.get("social_class")),lambda r:("Living",r.get("living_members")),lambda r:("Associated",r.get("total_assigned_members"))),
            body="notes",badge=lambda r:"Active" if r.get("active") else "Inactive")
        if not hdf.empty:
            labels=[f"{r.household_id} — {r.household_name or ''}" for _,r in hdf.iterrows()]
            pick=st.selectbox("View household members",labels,key="hh_show")
            hh=pick.split(" — ",1)[0]
            mem=q("""SELECT sim_id,first_name,last_name,birth_global_day,death_global_day
                     FROM sims WHERE current_household_id=? ORDER BY birth_global_day""",(hh,))
            if mem.empty:
                st.caption("No Sims are currently assigned to this household.")
            else:
                mem["Name"]=(mem.first_name.fillna("")+" "+mem.last_name.fillna("")).str.strip()
                mem["Status"]=mem.death_global_day.apply(lambda x:"Deceased" if pd.notna(x) else "Living")
                friendly_cards(mem,"Name",
                    meta=("Status",lambda r:("Born",f"GD {r.get('birth_global_day')}"),
                          lambda r:("Died",f"GD {r.get('death_global_day')}" if pd.notna(r.get('death_global_day')) else None)),
                    badge="sim_id",limit=60)

    if household_section=="Create":
        st.subheader("Create a household")
        con=connect(); proposed=next_id(con,'households','household_id','HH'); con.close()
        sim_choices=sim_options()
        with st.form("create_household_form",clear_on_submit=False):
            a,b=st.columns([1,2])
            household_id=a.text_input("Household ID",value=proposed)
            household_name=b.text_input("Household name")
            a,b,c=st.columns(3)
            branch_type=a.text_input("Branch type",placeholder="Main, cadet, abbey…")
            location=b.text_input("Location")
            social_class=c.text_input("Social class")
            a,b=st.columns(2)
            head=a.selectbox("Household head (optional)",sim_choices,key="hh_create_head")
            start_day=b.text_input("Start Global Day",value=str(current_gd()))
            active=st.checkbox("Active household",value=True,key="hh_create_active")
            assign_head=st.checkbox(
                "Move the selected household head into this household",
                value=True,
                disabled=not bool(head),
                key="hh_create_assign_head",
            )
            notes=st.text_area("Notes",key="hh_create_notes")
            submitted=st.form_submit_button("Create household",type="primary",use_container_width=True)
        if submitted:
            identifier=household_id.strip()
            name=household_name.strip()
            if not identifier:
                st.error("A Household ID is required.")
            elif not name:
                st.error("Enter a household name.")
            else:
                con=connect()
                try:
                    exists=con.execute("SELECT 1 FROM households WHERE household_id=?",(identifier,)).fetchone()
                    if exists:
                        raise ValueError(f"{identifier} already exists.")
                    head_id=sid(head)
                    con.execute("""INSERT INTO households(
                        household_id,household_name,branch_type,location,social_class,head_sim_id,active,
                        start_global_day,end_global_day,living_members,total_assigned_members,notes,data_source
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (identifier,name,branch_type or None,location or None,social_class or None,head_id,
                     1 if active else 0,int_or_none(start_day),None,0,0,notes or None,"Created in tracker"))
                    if head_id and assign_head:
                        con.execute("UPDATE sims SET current_household_id=? WHERE sim_id=?",(identifier,head_id))
                    admin_ops.refresh_household_counts(con,identifier,commit=False)
                    con.commit()
                except Exception as error:
                    con.rollback(); con.close(); st.error(f"Could not create household: {error}")
                else:
                    con.close(); st.success(f"Created {name} ({identifier})."); st.rerun()

    if household_section=="Move Sim":
        opts=sim_options()
        hdf=q("SELECT household_id,household_name FROM households ORDER BY household_id")
        hopts=[""]+[f"{r.household_id} — {r.household_name or ''}" for _,r in hdf.iterrows()]
        a,b=st.columns(2)
        sim=a.selectbox("Sim",opts,key="hh_assign_sim")
        house=b.selectbox("New household",hopts,key="hh_assign_house")
        if sim:
            ss=sid(sim)
            cur=q("SELECT current_household_id FROM sims WHERE sim_id=?",(ss,))
            st.caption(f"Current household: {cur.iloc[0].current_household_id if not cur.empty and cur.iloc[0].current_household_id else 'None'}")
        if st.button("Assign household",type="primary",key="hh_assign_btn"):
            if not sim:
                st.error("Choose a Sim first.")
            else:
                con=connect()
                sim_id=sid(sim); new_household=sid(house)
                old_row=con.execute("SELECT current_household_id FROM sims WHERE sim_id=?",(sim_id,)).fetchone()
                old_household=old_row[0] if old_row else None
                con.execute("UPDATE sims SET current_household_id=? WHERE sim_id=?",(new_household,sim_id))
                for household in {old_household,new_household}:
                    if household:
                        admin_ops.refresh_household_counts(con,household,commit=False)
                con.commit(); con.close()
                st.success("Household assignment saved.")
                st.rerun()

    if household_section=="Edit":
        hdf=q("SELECT * FROM households ORDER BY household_id")
        if hdf.empty:
            st.info("No households.")
        else:
            hh=st.selectbox("Household",hdf.household_id.tolist(),key="hh_edit_select")
            row=q("SELECT * FROM households WHERE household_id=?",(hh,)).iloc[0].to_dict()
            a,b,c=st.columns(3)
            name=a.text_input("Household name",value=row.get("household_name") or "",key="hh_edit_name")
            location=b.text_input("Location",value=row.get("location") or "",key="hh_edit_location")
            social=c.text_input("Social class",value=row.get("social_class") or "",key="hh_edit_social")
            a,b,c=st.columns(3)
            start=a.text_input("Start Global Day",value=str(row.get("start_global_day") or ""),key="hh_edit_start")
            end=b.text_input("End Global Day",value=str(row.get("end_global_day") or ""),key="hh_edit_end")
            active=c.checkbox("Active",value=bool(row.get("active") if row.get("active") is not None else 1),key="hh_edit_active")
            notes=st.text_area("Notes",value=row.get("notes") or "",key="hh_edit_notes")
            if st.button("Save household",type="primary",key="hh_edit_save"):
                con=connect()
                con.execute("""UPDATE households SET household_name=?,location=?,social_class=?,start_global_day=?,end_global_day=?,active=?,notes=?
                               WHERE household_id=?""",
                            (name or None,location or None,social or None,int_or_none(start),int_or_none(end),1 if active else 0,notes or None,hh))
                con.commit(); con.close(); st.success(f"Saved {hh}")
            with st.expander("Delete this household",expanded=False):
                member_count=scalar("SELECT COUNT(*) FROM sims WHERE current_household_id=?",(hh,))
                st.warning(
                    f"This permanently deletes the household. {member_count} assigned Sim(s) will become unassigned; the Sims are not deleted."
                )
                hh_delete_confirm=st.text_input(f"Type {hh} to confirm deletion",key=f"hh_delete_confirm_{hh}")
                if st.button(
                    "Permanently delete household",disabled=hh_delete_confirm.strip()!=hh,
                    key=f"hh_delete_btn_{hh}"
                ):
                    con=connect()
                    try:
                        admin_ops.delete_household(con,hh)
                    except Exception as error:
                        con.rollback(); con.close(); st.error(f"Could not delete household: {error}")
                    else:
                        con.close(); st.success(f"Deleted {hh}; assigned Sims are now unassigned."); st.rerun()


@workspace_fragment
def render_challenge_management():
    page_header("Challenge Management","Era guidance, succession, marriage matches, and wartime service in one place.")
    g=current_gd(); year,_=challenge_year_day(g)
    challenge_section=st.segmented_control("Challenge section",["Era rules","Succession","Matchmaking","War & conscription"],default=None,label_visibility="collapsed",key="challenge_section") or "Era rules"

    if challenge_section=="Era rules":
        st.subheader(f"Rules in force — Year {year}")
        location_rows=q("SELECT DISTINCT location FROM households WHERE location IS NOT NULL AND location<>'' ORDER BY location")
        locations=["All"]+[str(x) for x in location_rows.location.tolist()]
        loc=st.selectbox("Challenge location",locations,key="era_location")
        rdf=q("SELECT * FROM era_guidance WHERE active=1 AND (start_year IS NULL OR start_year<=?) AND (end_year IS NULL OR end_year>=?) AND (location IS NULL OR location='' OR LOWER(location)='all' OR LOWER(location)=LOWER(?)) ORDER BY category,title",(year,year,loc))
        a,b,c=st.columns(3)
        a.metric("Current year",year); b.metric("Applicable guidance",len(rdf)); c.metric("Active historical events",scalar("SELECT COUNT(*) FROM events WHERE active=1 AND start_global_day<=? AND end_global_day>=?",(g,g)))
        if rdf.empty: st.info("No custom era guidance applies yet. Add your first editable rule below.")
        else: friendly_cards(rdf,"title",meta=("category",lambda r:("Years",f"{r.get('start_year')}–{r.get('end_year')}"),"location"),body="rule_text",badge=lambda r:"In force")
        with st.expander("Add editable era guidance",expanded=rdf.empty):
            a,b,c=st.columns(3)
            title=a.text_input("Rule title",key="era_add_title")
            category=b.selectbox("Category",["Marriage & family","Inheritance","Military","Careers & education","Clothing","Building & technology","Health","Economy","Other"],key="era_add_cat")
            rule_loc=c.text_input("Location","All",key="era_add_loc")
            a,b=st.columns(2)
            start=a.number_input("Start year",-10000,10000,year,key="era_add_start")
            end=b.number_input("End year",-10000,10000,year,key="era_add_end")
            body=st.text_area("Rule or guidance",key="era_add_body")
            if st.button("Add era rule",type="primary",key="era_add_btn"):
                if not title.strip() or not body.strip(): st.error("Enter a title and guidance.")
                elif end<start: st.error("End year must be on or after the start year.")
                else:
                    con=connect(); rid=next_id(con,"era_guidance","rule_id","RULE")
                    con.execute("INSERT INTO era_guidance(rule_id,title,category,start_year,end_year,location,rule_text,active,source) VALUES(?,?,?,?,?,?,?,?,?)",(rid,title.strip(),category,int(start),int(end),rule_loc.strip() or "All",body.strip(),1,"App entry")); con.commit(); con.close(); st.rerun()
        all_rules=q("SELECT * FROM era_guidance ORDER BY category,title")
        if not all_rules.empty:
            with st.expander("Edit or delete existing guidance"):
                labels=[f"{r.rule_id} — {r.title}" for _,r in all_rules.iterrows()]
                chosen=st.selectbox("Rule",labels,key="era_manage")
                rid=chosen.split(" — ",1)[0]
                existing=all_rules[all_rules.rule_id==rid].iloc[0]
                categories=["Marriage & family","Inheritance","Military","Careers & education","Clothing","Building & technology","Health","Economy","Other"]
                a,b,c=st.columns(3)
                edit_title=a.text_input("Title",value=existing.title or "",key=f"era_edit_title_{rid}")
                edit_category=b.selectbox("Category",categories,index=categories.index(existing.category) if existing.category in categories else len(categories)-1,key=f"era_edit_cat_{rid}")
                edit_location=c.text_input("Location",value=existing.location or "All",key=f"era_edit_loc_{rid}")
                a,b,c=st.columns(3)
                edit_start=a.number_input("Start year",-10000,10000,int(existing.start_year or year),key=f"era_edit_start_{rid}")
                edit_end=b.number_input("End year",-10000,10000,int(existing.end_year or year),key=f"era_edit_end_{rid}")
                edit_active=c.checkbox("Active",value=bool(existing.active),key=f"era_edit_active_{rid}")
                edit_body=st.text_area("Guidance",value=existing.rule_text or "",key=f"era_edit_body_{rid}")
                edit_notes=st.text_input("Notes",value=existing.notes or "",key=f"era_edit_notes_{rid}")
                if st.button("Save changes",type="primary",key="era_edit_save"):
                    if not edit_title.strip() or not edit_body.strip(): st.error("Enter a title and guidance.")
                    elif edit_end<edit_start: st.error("End year must be on or after the start year.")
                    else:
                        con=connect(); con.execute("""UPDATE era_guidance SET title=?,category=?,start_year=?,end_year=?,location=?,rule_text=?,active=?,notes=? WHERE rule_id=?""",(edit_title.strip(),edit_category,int(edit_start),int(edit_end),edit_location.strip() or "All",edit_body.strip(),1 if edit_active else 0,edit_notes.strip() or None,rid)); con.commit(); con.close(); st.success("Era guidance updated."); st.rerun()
                if st.button("Delete selected era rule",key="era_delete"):
                    con=connect(); con.execute("DELETE FROM era_guidance WHERE rule_id=?",(rid,)); con.commit(); con.close(); st.rerun()

    if challenge_section=="Succession":
        sims=q("SELECT * FROM sims ORDER BY birth_global_day,sim_id")
        st.subheader("Line of succession")
        con=connect()
        system=setting(con,"succession_system","Absolute primogeniture")
        legitimate=str(setting(con,"succession_require_legitimate","0"))=="1"
        root=setting(con,"succession_root_id","")
        con.close()
        opts=sim_options(); systems=["Absolute primogeniture","Male-preference primogeniture","Female-preference primogeniture","Eldest living"]
        a,b,c=st.columns(3)
        new_system=a.selectbox("Succession system",systems,index=systems.index(system) if system in systems else 0)
        root_choice=b.selectbox("Dynasty founder / root",opts,index=opt_index(opts,root),help="Leave blank to rank all living Sims.")
        new_legitimate=c.checkbox("Require legitimacy",value=legitimate)
        if st.button("Save succession rules",key="succ_save"):
            con=connect(); set_setting(con,"succession_system",new_system); set_setting(con,"succession_root_id",sid(root_choice) or ""); set_setting(con,"succession_require_legitimate",1 if new_legitimate else 0); con.close(); st.success("Succession rules saved.")
        ranked=cm.succession_ranking(sims,sid(root_choice),new_system,new_legitimate)
        if ranked.empty: st.info("No living eligible heirs match these rules.")
        else:
            heir=ranked.iloc[0]
            st.success(f"Current recommended heir: {heir['name'] or heir.sim_id}")
            friendly_cards(ranked,lambda r:f"#{r.get('rank')} · {r.get('name') or r.get('sim_id')}",meta=("sex",lambda r:("Born",f"Global Day {r.get('birth_global_day')}"),"generation"),body="succession_note",badge="succession_override")
        st.caption("Use a Sim's Succession Override field to mark them Heir/Priority or Exclude/Disinherit; the ranking updates automatically.")

    if challenge_section=="Matchmaking":
        sims=q("SELECT * FROM sims ORDER BY birth_global_day,sim_id")
        st.subheader("Marriage eligibility & matchmaking")
        con=connect(); min_age=int(float(setting(con,"marriage_min_age_days",72))); con.close()
        new_min=st.number_input("Minimum marriage age (challenge days)",0,10000,min_age,key="match_min_age")
        if new_min!=min_age:
            con=connect(); set_setting(con,"marriage_min_age_days",int(new_min)); con.close()
        married=set()
        rels=q("SELECT * FROM relationships WHERE COALESCE(status,'Active')='Active' AND (COALESCE(legally_married,0)=1 OR LOWER(COALESCE(type,'')) LIKE ?)",( "%marriage%",))
        if not rels.empty:
            married.update(rels.partner1_id.dropna().astype(str)); married.update(rels.partner2_id.dropna().astype(str))
        eligible=sims[sims.death_global_day.isna() & sims.birth_global_day.notna()].copy()
        eligible=eligible[(g-eligible.birth_global_day)>=new_min]
        eligible=eligible[~eligible.sim_id.astype(str).isin(married)]
        eligible["name"]=eligible.apply(cm.sim_name,axis=1)
        st.caption(f"{len(eligible)} living, unmarried Sim(s) meet the current age rule.")
        if len(eligible)<2: st.info("At least two eligible Sims are needed for matchmaking.")
        else:
            labels=[f"{r.sim_id} — {r['name']}" for _,r in eligible.iterrows()]
            first=st.selectbox("First Sim",labels,key="match_first"); fid=sid(first)
            rows=[]
            first_row=eligible[eligible.sim_id==fid].iloc[0]
            for _,candidate in eligible[eligible.sim_id!=fid].iterrows():
                warning=cm.kinship_warning(fid,candidate.sim_id,sims)
                same_house=bool(first_row.current_household_id and first_row.current_household_id==candidate.current_household_id)
                score=max(0,100-abs(int(first_row.birth_global_day)-int(candidate.birth_global_day)))+(10 if same_house else 0)-(100 if warning else 0)
                rows.append({"sim_id":candidate.sim_id,"name":candidate["name"],"age_days":g-int(candidate.birth_global_day),"compatibility":score,"kinship_warning":warning or "None"})
            candidates=pd.DataFrame(rows).sort_values(["compatibility","name"],ascending=[False,True])
            friendly_cards(candidates,"name",meta=(lambda r:("Age",f"{r.get('age_days')} challenge days"),lambda r:("Compatibility",r.get("compatibility"))),badge="kinship_warning")
            second=st.selectbox("Chosen match",[f"{r.sim_id} — {r['name']}" for _,r in candidates.iterrows()],key="match_second"); second_id=sid(second)
            warning=cm.kinship_warning(fid,second_id,sims)
            if warning: st.error(f"Close-relative warning: {warning}. The tracker will not create this courtship.")
            if st.button("Create active courtship",type="primary",disabled=bool(warning),key="match_create"):
                p1=sims[sims.sim_id==fid].iloc[0]; p2=sims[sims.sim_id==second_id].iloc[0]
                con=connect(); rel_id=next_id(con,"relationships","relationship_id","REL")
                con.execute("INSERT INTO relationships(relationship_id,partner1_id,partner2_id,partner1_name,partner2_name,type,start_global_day,status,legally_married,children_count,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(rel_id,fid,second_id,cm.sim_name(p1),cm.sim_name(p2),"Courtship",g,"Active",0,0,"Created by Matchmaking")); con.commit(); con.close(); st.success(f"Created {rel_id}."); st.rerun()

    if challenge_section=="War & conscription":
        sims=q("SELECT * FROM sims ORDER BY birth_global_day,sim_id")
        households=q("SELECT * FROM households ORDER BY household_name,household_id")
        st.subheader("War & conscription")
        campaigns=q("SELECT * FROM military_campaigns ORDER BY start_global_day DESC,campaign_id")
        services=q("SELECT * FROM military_service ORDER BY enlisted_global_day DESC,service_id")
        a,b,c=st.columns(3); a.metric("Campaigns",len(campaigns)); b.metric("Serving now",int(services.status.fillna("").eq("Active").sum()) if not services.empty else 0); c.metric("Service records",len(services))
        if not services.empty: friendly_cards(services,"sim_name",meta=("role",lambda r:("Enlisted",f"Global Day {r.get('enlisted_global_day')}"),lambda r:("Returned",r.get("return_global_day"))),body=lambda r:r.get("outcome") or r.get("injury"),badge="status")
        with st.expander("Create a campaign",expanded=campaigns.empty):
            events=q("SELECT event_id,event_name FROM events ORDER BY start_global_day DESC")
            event_opts=[""]+[f"{r.event_id} — {r.event_name}" for _,r in events.iterrows()]
            event=st.selectbox("Linked historical event (optional)",event_opts,key="war_event")
            a,b,c=st.columns(3); name=a.text_input("Campaign name",key="war_name"); start=b.number_input("Start day",-10000,20000,g,key="war_start"); end=c.number_input("End day",-10000,20000,g,key="war_end")
            a,b,c=st.columns(3); location=a.text_input("Location","All",key="war_loc"); min_age=b.number_input("Minimum age (days)",0,10000,72,key="war_min"); max_age=c.number_input("Maximum age (days)",0,10000,240,key="war_max")
            a,b=st.columns(2); sexes=a.text_input("Eligible sexes (comma-separated)","All",key="war_sexes"); classes=b.text_input("Eligible classes (comma-separated)","All",key="war_classes")
            notes=st.text_area("Campaign notes",key="war_notes")
            if st.button("Create campaign",type="primary",key="war_create"):
                if not name.strip(): st.error("Enter a campaign name.")
                else:
                    con=connect(); cid=next_id(con,"military_campaigns","campaign_id","WAR")
                    con.execute("INSERT INTO military_campaigns(campaign_id,event_id,name,start_global_day,end_global_day,location,min_age_days,max_age_days,eligible_sexes,eligible_classes,active,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(cid,sid(event),name.strip(),int(start),int(end),location.strip() or "All",int(min_age),int(max_age),sexes.strip() or "All",classes.strip() or "All",1,notes.strip() or None)); con.commit(); con.close(); st.rerun()
        if not campaigns.empty:
            labels=[f"{r.campaign_id} — {r['name']}" for _,r in campaigns.iterrows()]
            selected=st.selectbox("Campaign roster",labels,key="war_selected"); cid=selected.split(" — ",1)[0]; campaign=campaigns[campaigns.campaign_id==cid].iloc[0]
            roster=cm.eligible_for_campaign(sims,households,int(campaign.start_global_day),int(campaign.min_age_days or 0),int(campaign.max_age_days or 10000),campaign.location or "All",campaign.eligible_sexes or "All",campaign.eligible_classes or "All")
            existing=set(services[services.campaign_id==cid].sim_id.astype(str)) if not services.empty else set()
            roster=roster[~roster.sim_id.astype(str).isin(existing)]
            friendly_cards(roster,"name",meta=("sex",lambda r:("Age",f"{r.get('age_days')} days"),lambda r:("Location",r.get("household_location")),"social_class"),badge=lambda r:"Eligible")
            chosen=st.multiselect("Select Sims to enlist",roster.sim_id.tolist(),format_func=lambda x: next((str(n) for i,n in zip(roster.sim_id,roster.name) if i==x),x),key="war_enlist_select")
            role=st.text_input("Service role","Conscript",key="war_role")
            if st.button("Enlist selected Sims",type="primary",disabled=not chosen,key="war_enlist"):
                con=connect()
                for sim_id in chosen:
                    row=roster[roster.sim_id==sim_id].iloc[0]; service_id=next_id(con,"military_service","service_id","SVC")
                    con.execute("INSERT INTO military_service(service_id,campaign_id,event_id,sim_id,sim_name,role,status,enlisted_global_day) VALUES(?,?,?,?,?,?,?,?)",(service_id,cid,campaign.event_id,sim_id,row["name"],role.strip() or "Conscript","Active",int(campaign.start_global_day)))
                con.commit(); con.close(); st.rerun()
        if not services.empty:
            with st.expander("Update a service outcome"):
                labels=[f"{r.service_id} — {r.sim_name}" for _,r in services.iterrows()]; choice=st.selectbox("Service record",labels,key="svc_edit"); service_id=choice.split(" — ",1)[0]; row=services[services.service_id==service_id].iloc[0]
                a,b,c=st.columns(3); status=a.selectbox("Status",["Active","Returned","Injured","Missing","Killed"],index=["Active","Returned","Injured","Missing","Killed"].index(row.status) if row.status in ["Active","Returned","Injured","Missing","Killed"] else 0); return_day=b.number_input("Return/outcome day",-10000,20000,int(row.return_global_day or g)); injury=c.text_input("Injury",value=row.injury or "")
                outcome=st.text_area("Outcome",value=row.outcome or ""); record_death=st.checkbox("If killed, also record this as the Sim's death",value=False,disabled=status!="Killed")
                if st.button("Save service outcome",key="svc_save"):
                    con=connect(); con.execute("UPDATE military_service SET status=?,return_global_day=?,outcome=?,injury=? WHERE service_id=?",(status,int(return_day) if status!="Active" else None,outcome.strip() or None,injury.strip() or None,service_id))
                    if status=="Killed" and record_death: con.execute("UPDATE sims SET death_global_day=COALESCE(death_global_day,?),cause_of_death=COALESCE(cause_of_death,'Military service') WHERE sim_id=?",(int(return_day),row.sim_id))
                    con.commit(); con.close(); st.rerun()

@workspace_fragment
def render_play_planner():
    page_header("Play Planner","Plan rotations, family growth, marriage eligibility, and future milestones.")
    g=current_gd(); _,dpy=calendar_settings()
    section=st.segmented_control("Planner section",["Next to play","Family plans","Dynasties","Planner rules"],
        default=None,label_visibility="collapsed",key="planner_section") or "Next to play"

    if section=="Next to play":
        con=connect()
        try:
            recommendations=play_planner.rotation_recommendations(con,g)
        finally: con.close()
        rows=[{key:row[key] for key in row.keys()} for row in recommendations]
        for index,row in enumerate(rows):
            row["wait"]="Never played" if row["last_played"] is None else f"{g-int(row['last_played'])} days ago"
            row["recommendation"]="Play next" if index==0 else "Waiting"
        friendly_cards(rows,lambda r:r.get("household_name") or r.get("household_id"),
            meta=("location","social_class",lambda r:("Living",r.get("living_members")),lambda r:("Last played",r.get("wait"))),
            badge="recommendation",limit=100,empty="Create an active household to begin a rotation.")
        section_heading("Plan or record a play session","Assign future years or record what you just played")
        households=q("SELECT household_id,household_name FROM households WHERE COALESCE(active,1)=1 ORDER BY household_name,household_id")
        hopts=[f"{r.household_id} — {r.household_name or r.household_id}" for _,r in households.iterrows()]
        if hopts:
            a,b,c=st.columns(3); household=a.selectbox("Household",hopts,key="planner_play_household")
            play_status=b.selectbox("Entry type",["Played","Planned"],key="planner_play_status")
            played_day=c.number_input("Global Day",-10000,20000,g,key="planner_play_day")
            household_id=sid(household)
            members=q("""SELECT sim_id,COALESCE(first_name,'') first_name,COALESCE(last_name,'') last_name FROM sims
                WHERE current_household_id=? AND birth_global_day<=? AND (death_global_day IS NULL OR death_global_day>=?)""",(household_id,g,g))
            mopts=[""]+[f"{r.sim_id} — {r.first_name} {r.last_name}" for _,r in members.iterrows()]
            played_sim=st.selectbox("Primary Sim played (optional)",mopts,key="planner_play_sim")
            notes=st.text_input("Session notes",key="planner_play_notes")
            if st.button("Save rotation entry",type="primary",key="planner_play_save"):
                con=connect(); play_planner.record_rotation(con,played_day,household_id,sid(played_sim),play_status,notes or None); con.close()
                st.success("Rotation entry saved."); st.rerun()
        history=q("""SELECT r.rotation_id,r.global_day,r.status,r.notes,h.household_name,
            TRIM(COALESCE(s.first_name,'')||' '||COALESCE(s.last_name,'')) sim_name
            FROM play_rotation r LEFT JOIN households h ON h.household_id=r.household_id
            LEFT JOIN sims s ON s.sim_id=r.sim_id ORDER BY r.global_day DESC,r.rotation_id DESC LIMIT 30""")
        if not history.empty:
            section_heading("Recent rotation","Latest recorded play sessions")
            friendly_cards(history,lambda r:r.get("household_name") or "Household",
                meta=(lambda r:("When",gd_caption(r.get("global_day"))),"sim_name"),body="notes",badge="status",limit=30)

    if section=="Family plans":
        opts=sim_options(blank=False)
        if not opts: st.info("Add a Sim before creating a family plan."); return
        chosen=st.selectbox("Sim",opts,key="planner_family_sim"); sim_id=sid(chosen)
        con=connect()
        try:
            plan=con.execute("SELECT target_children,min_birth_spacing_days,notes FROM sim_family_plans WHERE sim_id=?",(sim_id,)).fetchone()
            adulthood=int(float(setting(con,"adulthood_age_days",72)))
            survival=play_planner.child_survival(con,sim_id,adulthood)
            forecast=play_planner.milestone_forecast(con,sim_id,g)
            last_birth=con.execute("SELECT MAX(birth_global_day) FROM sims WHERE mother_id=?",(sim_id,)).fetchone()[0]
        finally: con.close()
        a,b,c,d=st.columns(4); a.metric("Children",survival["children"]); b.metric("Reached adulthood",survival["survived"])
        c.metric("Died before adulthood",survival["died_young"]); d.metric("Still too young",survival["pending"])
        target=st.number_input("Target number of children",0,100,int(plan[0] if plan and plan[0] is not None else survival["children"]),key="planner_target")
        spacing=st.number_input("Minimum birth spacing (challenge days)",0,1000,int(plan[1] if plan and plan[1] is not None else dpy),key="planner_spacing")
        plan_notes=st.text_area("Family-plan notes",value=plan[2] if plan and plan[2] else "",key="planner_family_notes")
        if last_birth is not None and spacing:
            next_day=int(last_birth)+int(spacing); st.info(f"Next planned conception: Global Day {next_day} — {gd_caption(next_day)}")
        st.caption(f"{max(0,int(target)-survival['children'])} additional child(ren) needed to reach this goal.")
        if st.button("Save family plan",type="primary",key="planner_family_save"):
            con=connect(); con.execute("""INSERT INTO sim_family_plans(sim_id,target_children,min_birth_spacing_days,notes)
                VALUES(?,?,?,?) ON CONFLICT(sim_id) DO UPDATE SET target_children=excluded.target_children,
                min_birth_spacing_days=excluded.min_birth_spacing_days,notes=excluded.notes""",
                (sim_id,int(target),int(spacing),plan_notes or None)); con.commit(); con.close(); st.success("Family plan saved.")
        section_heading("Milestone forecast","Derived from the Sim's birth day and editable aging rules")
        future=[row for row in forecast if row["status"]=="Upcoming"][:12]
        friendly_cards(future,"milestone",meta=(lambda r:("When",gd_caption(r.get("global_day"))),),badge="status",limit=12,
            empty="No future milestones are currently scheduled.")

    if section=="Dynasties":
        dynasty=q("""SELECT COALESCE(NULLIF(TRIM(last_name),''),'Unknown') dynasty,COUNT(*) total,
            COUNT(*) FILTER(WHERE birth_global_day<=? AND (death_global_day IS NULL OR death_global_day>=?)) living,
            MIN(birth_global_day) first_recorded FROM sims
            GROUP BY COALESCE(NULLIF(TRIM(last_name),''),'Unknown') ORDER BY living,total DESC,dynasty""",(g,g))
        if not dynasty.empty:
            dynasty["state"]=dynasty["living"].apply(lambda value:"Extinct" if int(value)==0 else ("Endangered" if int(value)<=2 else "Surviving"))
            a,b,c=st.columns(3); a.metric("Surviving",int((dynasty.state=="Surviving").sum()))
            b.metric("Endangered",int((dynasty.state=="Endangered").sum())); c.metric("Extinct",int((dynasty.state=="Extinct").sum()))
            state=st.selectbox("Dynasty status",["All","Surviving","Endangered","Extinct"],key="planner_dynasty_filter")
            shown=dynasty if state=="All" else dynasty[dynasty.state==state]
            friendly_cards(shown,"dynasty",meta=("living","total",lambda r:("First recorded",gd_caption(r.get("first_recorded")) if pd.notna(r.get("first_recorded")) else "Unknown")),badge="state",limit=100)
        con=connect(); adult_days=int(float(setting(con,"adulthood_age_days",72))); con.close()
        section_heading("Children reaching adulthood","Younger living children remain pending until they reach the configured age")
        parents=q("""WITH links AS (SELECT mother_id parent_id,birth_global_day,death_global_day FROM sims WHERE mother_id IS NOT NULL
            UNION ALL SELECT father_id,birth_global_day,death_global_day FROM sims WHERE father_id IS NOT NULL)
            SELECT p.sim_id,TRIM(COALESCE(p.title,'')||' '||COALESCE(p.first_name,'')||' '||COALESCE(p.last_name,'')) parent,
            COUNT(*) children,SUM(CASE WHEN l.death_global_day IS NOT NULL AND l.death_global_day<l.birth_global_day+? THEN 1 ELSE 0 END) died_young,
            SUM(CASE WHEN NOT(l.death_global_day IS NOT NULL AND l.death_global_day<l.birth_global_day+?) AND
                (l.death_global_day IS NOT NULL OR l.birth_global_day+?<=?) THEN 1 ELSE 0 END) survived,
            SUM(CASE WHEN l.death_global_day IS NULL AND l.birth_global_day+?>? THEN 1 ELSE 0 END) pending
            FROM links l JOIN sims p ON p.sim_id=l.parent_id GROUP BY p.sim_id,p.title,p.first_name,p.last_name
            ORDER BY survived DESC,children DESC,parent""",(adult_days,adult_days,adult_days,g,adult_days,g))
        friendly_cards(parents,"parent",meta=("children","survived","died_young","pending"),badge="sim_id",limit=100,
            empty="No parent-child records are available yet.")

    if section=="Planner rules":
        con=connect(); marriage_now=int(float(setting(con,"marriage_min_age_days",72))); adult_now=int(float(setting(con,"adulthood_age_days",72))); con.close()
        a,b=st.columns(2); marriage_age=a.number_input("Marriage eligibility age (challenge days)",0,1000,marriage_now,key="planner_marriage_age")
        adult_age=b.number_input("Survived-to-adulthood age (challenge days)",1,1000,adult_now,key="planner_adult_age")
        if st.button("Save planner ages",key="planner_age_save"):
            con=connect(); set_setting(con,"marriage_min_age_days",marriage_age); set_setting(con,"adulthood_age_days",adult_age); con.close(); sync_auto_rolls(False); st.success("Planner ages saved.")
        rules=q("SELECT rule_key,start_year,end_year,die,bad_results,active FROM planner_rules ORDER BY rule_key,start_year")
        labels=[f"{r.rule_key} · {int(r.start_year)}–{int(r.end_year)}" for _,r in rules.iterrows()]
        selected=st.selectbox("Rule period",labels,key="planner_rule_select"); rr=rules.iloc[labels.index(selected)]
        a,b,c=st.columns(3); start=a.number_input("Start year",-10000,10000,int(rr.start_year),key="planner_rule_start")
        end=b.number_input("End year",-10000,10000,int(rr.end_year),key="planner_rule_end"); die=c.text_input("Die",value=rr.die or "",key="planner_rule_die")
        results=st.text_area("Results",value=rr.bad_results or "",key="planner_rule_results"); active=st.checkbox("Active",bool(rr.active),key="planner_rule_active")
        if st.button("Save planner rule",type="primary",key="planner_rule_save"):
            con=connect(); con.execute("""UPDATE planner_rules SET start_year=?,end_year=?,die=?,bad_results=?,active=?
                WHERE rule_key=? AND start_year=? AND end_year=?""",(int(start),int(end),die or None,results or None,1 if active else 0,rr.rule_key,int(rr.start_year),int(rr.end_year)))
            con.commit(); con.close(); sync_auto_rolls(False); st.success("Planner rule saved."); st.rerun()

@workspace_fragment
def render_illnesses():
    page_header("Illnesses","Track sickness, treatment, recovery, chronic conditions, and outcomes for every Sim.")
    g=current_gd()
    illness_total=scalar("SELECT COUNT(*) FROM illnesses")
    active_total=scalar("SELECT COUNT(*) FROM illnesses WHERE COALESCE(status,'Active') IN ('Active','Improving','Worsening','Chronic')")
    contagious_total=scalar("SELECT COUNT(*) FROM illnesses WHERE COALESCE(status,'Active') IN ('Active','Improving','Worsening','Chronic') AND COALESCE(contagious,0)=1")
    a,b,c=st.columns(3)
    a.metric("Recorded illnesses",illness_total); b.metric("Currently active",active_total)
    c.metric("Contagious and active",contagious_total)
    illness_section=st.segmented_control("Illness section",["Register","Add","Update or resolve"],default=None,label_visibility="collapsed",key="illness_section") or "Register"

    if illness_section=="Register":
        idf=q("SELECT * FROM illnesses ORDER BY onset_global_day DESC,sim_name,illness_name LIMIT 200")
        if idf.empty:
            st.info("No illnesses have been recorded in this save.")
        else:
            a,b=st.columns([2,1])
            search=a.text_input("Search illnesses",placeholder="Sim or illness name",key="illness_search")
            status_filter=b.selectbox("Status",["All statuses"]+list(illnesses.ALL_STATUSES),key="illness_status_filter")
            shown=idf.copy()
            if search:
                term=search.casefold()
                shown=shown[shown.apply(lambda row: term in f"{row.sim_name or ''} {row.illness_name or ''} {row.notes or ''}".casefold(),axis=1)]
            if status_filter!="All statuses": shown=shown[shown.status==status_filter]
            friendly_cards(shown,lambda r:f"{r.get('sim_name') or r.get('sim_id')} — {r.get('illness_name')}",
                meta=(lambda r:("Began",f"Global Day {r.get('onset_global_day')}"),"status",lambda r:("Contagious","Yes" if r.get("contagious") else "No")),
                body=lambda r:r.get("outcome") or r.get("treatment") or r.get("notes"),badge="severity",empty="No illnesses match these filters.")

    if illness_section=="Add":
        opts=sim_options(blank=False)
        if not opts:
            st.info("Add a Sim before recording an illness.")
        else:
            sim=st.selectbox("Sim",opts,key="illness_add_sim")
            a,b,c=st.columns(3)
            name=a.text_input("Illness",key="illness_add_name")
            onset=b.number_input("Onset Global Day",-10000,20000,g,key="illness_add_onset")
            severity=c.selectbox("Severity",illnesses.SEVERITIES,index=1,key="illness_add_severity")
            a,b=st.columns(2)
            status=a.selectbox("Status",illnesses.ACTIVE_STATUSES,key="illness_add_status")
            contagious=b.checkbox("Contagious",key="illness_add_contagious")
            treatment=st.text_input("Treatment or care",key="illness_add_treatment")
            notes=st.text_area("Notes",key="illness_add_notes")
            if st.button("Add illness",type="primary",key="illness_add_btn"):
                if not name.strip(): st.error("Enter the illness name.")
                else:
                    con=connect(); iid=illnesses.next_id(con)
                    con.execute("""INSERT INTO illnesses(illness_id,sim_id,sim_name,illness_name,onset_global_day,status,
                                   severity,contagious,treatment,notes) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                                (iid,sid(sim),sim.split(" — ",1)[1],name.strip(),int(onset),status,severity,
                                 1 if contagious else 0,treatment.strip() or None,notes.strip() or None))
                    con.commit(); con.close(); st.success(f"Added {iid}."); st.rerun()

    if illness_section=="Update or resolve":
        idf=q("SELECT * FROM illnesses ORDER BY onset_global_day DESC,sim_name,illness_name LIMIT 250")
        if idf.empty:
            st.info("No illnesses to update.")
        else:
            labels=[f"{r.illness_id} — {r.sim_name or r.sim_id} — {r.illness_name}" for _,r in idf.iterrows()]
            choice=st.selectbox("Illness record",labels,key="illness_edit_choice")
            iid=choice.split(" — ",1)[0]
            row=idf[idf.illness_id==iid].iloc[0]
            opts=sim_options(blank=False); current_sim=next((x for x in opts if x.startswith(str(row.sim_id)+" — ")),opts[0] if opts else "")
            sim=st.selectbox("Sim",opts,index=opts.index(current_sim) if current_sim in opts else 0,key=f"illness_edit_sim_{iid}") if opts else ""
            a,b,c=st.columns(3)
            name=a.text_input("Illness",value=row.illness_name or "",key=f"illness_edit_name_{iid}")
            onset=b.number_input("Onset Global Day",-10000,20000,int(row.onset_global_day or g),key=f"illness_edit_onset_{iid}")
            severity=c.selectbox("Severity",illnesses.SEVERITIES,index=illnesses.SEVERITIES.index(row.severity) if row.severity in illnesses.SEVERITIES else 1,key=f"illness_edit_severity_{iid}")
            a,b,c=st.columns(3)
            status=a.selectbox("Status",illnesses.ALL_STATUSES,index=illnesses.ALL_STATUSES.index(row.status) if row.status in illnesses.ALL_STATUSES else 0,key=f"illness_edit_status_{iid}")
            end_default=int(row.end_global_day) if pd.notna(row.end_global_day) else g
            ended=status in ("Recovered","Fatal","Resolved")
            end_day=b.number_input("End Global Day",-10000,20000,end_default,disabled=not ended,key=f"illness_edit_end_{iid}")
            contagious=c.checkbox("Contagious",value=bool(row.contagious or 0),key=f"illness_edit_contagious_{iid}")
            treatment=st.text_input("Treatment or care",value=row.treatment or "",key=f"illness_edit_treatment_{iid}")
            outcome=st.text_input("Outcome",value=row.outcome or "",key=f"illness_edit_outcome_{iid}")
            notes=st.text_area("Notes",value=row.notes or "",key=f"illness_edit_notes_{iid}")
            if st.button("Save illness",type="primary",key=f"illness_edit_save_{iid}"):
                if not name.strip(): st.error("Enter the illness name.")
                else:
                    sim_name=sim.split(" — ",1)[1] if sim else row.sim_name
                    con=connect(); con.execute("""UPDATE illnesses SET sim_id=?,sim_name=?,illness_name=?,onset_global_day=?,
                                                   end_global_day=?,status=?,severity=?,contagious=?,treatment=?,outcome=?,notes=?
                                                   WHERE illness_id=?""",
                                               (sid(sim),sim_name,name.strip(),int(onset),int(end_day) if ended else None,status,
                                                severity,1 if contagious else 0,treatment.strip() or None,outcome.strip() or None,
                                                notes.strip() or None,iid))
                    con.commit(); con.close(); st.success("Illness updated."); st.rerun()
            with st.expander("Delete this illness record"):
                confirm=st.checkbox("I understand this permanently deletes the selected illness record.",key=f"illness_delete_confirm_{iid}")
                if st.button("Delete illness record",disabled=not confirm,key=f"illness_delete_{iid}"):
                    con=connect(); con.execute("DELETE FROM illnesses WHERE illness_id=?",(iid,)); con.commit(); con.close()
                    st.success("Illness record deleted."); st.rerun()

@workspace_fragment
def render_events():
    page_header("Historical Events","Manage challenge-wide events and record their effects.")
    g=current_gd()
    st.caption(f"Current Global Day: {g} — {gd_caption(g)}")
    event_section=st.segmented_control("Event section",["Browse","Batch resolve","Structured rules","Add","Record effect","Edit"],default=None,label_visibility="collapsed",key="event_section") or "Browse"

    if event_section=="Browse":
        event_total=scalar("SELECT COUNT(*) FROM events")
        active_total=scalar("SELECT COUNT(*) FROM events WHERE start_global_day<=? AND end_global_day>=?",(g,g))
        a,b,c=st.columns(3)
        a.metric("Historical events",event_total); b.metric("Active today",active_total); c.metric("Recorded effects",scalar("SELECT COUNT(*) FROM event_results"))
        event_search=st.text_input("Find an event",placeholder="Search by event, place, or scope…",key="events_v3_search")
        event_where=""
        event_params=()
        if event_search.strip():
            like=f"%{event_search.strip()}%"
            event_where=" WHERE COALESCE(event_name,'') LIKE ? OR COALESCE(location,'') LIKE ? OR COALESCE(scope,'') LIKE ?"
            event_params=(like,like,like)
        event_matches=scalar(f"SELECT COUNT(*) FROM events{event_where}",event_params)
        event_pages=max(1,(int(event_matches)+39)//40)
        event_page=st.number_input("Event page",1,event_pages,1,key="event_browse_page") if event_pages>1 else 1
        event_view=q(f"SELECT * FROM events{event_where} ORDER BY start_global_day,event_name LIMIT 40 OFFSET ?",event_params+((int(event_page)-1)*40,))
        friendly_cards(event_view,lambda r:r.get("event_name") or r.get("event_id"),
            meta=(lambda r:("When",f"GD {r.get('start_global_day')}–{r.get('end_global_day')}"),"scope","location",lambda r:("Affected",r.get("affected_class"))),
            body="notes",badge=lambda r:"Roll required" if r.get("roll_required") else ("Active" if r.get("active") else "Disabled"),limit=40)
        results=q("SELECT * FROM event_results ORDER BY global_day DESC,result_id LIMIT 20")
        if not results.empty:
            section_heading("Recorded effects")
            friendly_cards(results,lambda r:r.get("outcome") or r.get("cause_effect") or "Recorded effect",
                meta=(lambda r:("When",f"Global Day {r.get('global_day')}"),"sim_id","household_id"),body="notes",badge=lambda r:"Death" if r.get("death") else (r.get("status") or "Recorded"),limit=20)
        st.caption("Choose Edit to inspect or change an event's technical fields.")

    if event_section=="Batch resolve":
        batches=q("""SELECT e.event_id,e.event_name,e.start_global_day,e.end_global_day,COUNT(*) AS open_rolls
                     FROM events e JOIN rolls r ON r.source_id=e.event_id
                     WHERE COALESCE(r.completed,0)=0 AND COALESCE(r.roll_type,'') LIKE 'Event%'
                     GROUP BY e.event_id,e.event_name,e.start_global_day,e.end_global_day
                     ORDER BY e.start_global_day,e.event_name""")
        if batches.empty:
            st.success("No open event-roll batches.")
        else:
            batch_labels=[f"{r.event_id} — {r.event_name} ({int(r.open_rolls)} remaining)" for _,r in batches.iterrows()]
            batch_choice=st.selectbox("Event batch",batch_labels,key="event_batch_choice")
            batch_event=batch_choice.split(" — ",1)[0]
            batch=q("""SELECT roll_id,sim_id,sim_name,die,bad_results,actual_roll,outcome
                       FROM rolls WHERE source_id=? AND COALESCE(completed,0)=0 ORDER BY sim_name,roll_id""",(batch_event,))
            event_row=q("SELECT * FROM events WHERE event_id=?",(batch_event,)).iloc[0]
            st.caption(f"GD {event_row.start_global_day}–{event_row.end_global_day} · progress {int(event_row.get('start_global_day') is not None)} · {len(batch)} remaining")
            for index,row in batch.iterrows():
                if f"batch_{row.roll_id}" in st.session_state:
                    batch.at[index,"actual_roll"]=str(st.session_state[f"batch_{row.roll_id}"])
                    batch.at[index,"outcome"]=roll_outcomes.automatic_outcome(batch.at[index,"actual_roll"],row.bad_results,"Event",row.die)
            edited=st.data_editor(batch,hide_index=True,use_container_width=True,
                                  disabled=["roll_id","sim_id","sim_name","die","bad_results"],key=f"event_batch_editor_{batch_event}")
            a,b=st.columns(2)
            roll_all=a.button("Roll all remaining dice",key=f"event_batch_roll_all_{batch_event}",use_container_width=True)
            save_batch=b.button("Save completed entries",type="primary",key=f"event_batch_save_{batch_event}",use_container_width=True)
            if roll_all:
                for _,row in batch.iterrows():
                    spec=dice_roller.parse(row.die)
                    if spec:
                        result=dice_roller.roll(row.die)
                        st.session_state[f"batch_{row.roll_id}"]=result["total"]
                st.rerun()
            if save_batch:
                con=connect(); completed_count=0
                for _,row in edited.iterrows():
                    actual=str(row.get("actual_roll") or "").strip()
                    if not actual: continue
                    outcome=roll_outcomes.automatic_outcome(actual,row.get("bad_results"),"Event",row.get("die")) or str(row.get("outcome") or "")
                    con.execute("UPDATE rolls SET actual_roll=?,outcome=?,completed=1,completed_global_day=? WHERE roll_id=?",
                                (actual,outcome,g,row.roll_id)); completed_count+=1
                    if row.get("sim_id") and is_death_outcome(outcome):
                        death=random_death_for_roll({**row.to_dict(),"source_id":batch_event,"due_global_day":int(event_row.start_global_day)},actual)
                        con.execute("UPDATE sims SET death_global_day=COALESCE(death_global_day,?),death_date=COALESCE(death_date,?),cause_of_death=COALESCE(cause_of_death,?) WHERE sim_id=?",(*death,row.sim_id))
                con.commit(); action_queue.sync(con,g); con.close(); st.success(f"Completed {completed_count} event rolls."); st.rerun()

    if event_section=="Structured rules":
        events_with_rules=q("""SELECT e.event_id,e.event_name,e.affected_class,e.notes,c.die,c.bad_results,c.eligibility,
                               c.min_age_days,c.max_age_days,c.eligible_sexes,c.frequency,c.followup_die,c.followup_results
                               FROM events e LEFT JOIN event_rule_configs c ON c.event_id=e.event_id
                               WHERE COALESCE(e.roll_required,0)=1 ORDER BY e.start_global_day,e.event_name""")
        labels=[f"{r.event_id} — {r.event_name}" for _,r in events_with_rules.iterrows()]
        if not labels: st.info("No roll-required events.")
        else:
            choice=st.selectbox("Event rule",labels,key="structured_event_choice"); eid=choice.split(" — ",1)[0]
            row=events_with_rules[events_with_rules.event_id==eid].iloc[0]
            parsed=autorolls.event_roll_spec(row.notes)
            a,b=st.columns(2)
            die=a.text_input("Primary die",value=row.die or parsed["die"],key=f"structured_die_{eid}")
            frequency=b.selectbox("Frequency",["once","daily","yearly","per household"],index=["once","daily","yearly","per household"].index(row.frequency) if row.frequency in ["once","daily","yearly","per household"] else 0,key=f"structured_frequency_{eid}")
            bad=st.text_area("Numbered outcomes",value=row.bad_results or parsed["bad_results"] or "",key=f"structured_bad_{eid}")
            eligibility=st.text_input("Eligibility summary",value=row.eligibility or row.affected_class or "All Sims",key=f"structured_eligibility_{eid}")
            a,b,c=st.columns(3)
            min_age=a.number_input("Minimum age (days)",0,10000,int(row.min_age_days or 0),key=f"structured_min_{eid}")
            max_age=b.number_input("Maximum age (days)",0,10000,int(row.max_age_days or 10000),key=f"structured_max_{eid}")
            sexes=c.text_input("Eligible sexes",value=row.eligible_sexes or "All",key=f"structured_sexes_{eid}")
            a,b=st.columns(2); follow_die=a.text_input("Follow-up die",value=row.followup_die or "",key=f"structured_follow_die_{eid}"); follow_results=b.text_input("Follow-up outcomes",value=row.followup_results or "",key=f"structured_follow_results_{eid}")
            st.caption(f"Original library rule: {row.notes or 'No prose rule provided.'}")
            if st.button("Save structured event rule",type="primary",key=f"structured_save_{eid}"):
                con=connect(); action_queue.save_event_config(con,eid,die,bad,eligibility,min_age,max_age,sexes,frequency,follow_die,follow_results); con.close(); st.success("Structured rule saved and open rolls updated.")

    if event_section=="Add":
        a,b,c=st.columns(3)
        start=a.number_input("Start Global Day",-10000,20000,g,key='evs')
        end=b.number_input("End Global Day",-10000,20000,g,key='eve')
        name=c.text_input("Event name",key="event_add_name")
        a,b,c=st.columns(3)
        scope=a.text_input("Scope","Global / Era",key="event_add_scope")
        location=b.text_input("Location","All",key="event_add_location")
        affected=c.text_input("Affected class","All",key="event_add_affected")
        roll_required=st.checkbox("Roll required",key="event_add_roll")
        notes=st.text_area("Notes",key="event_add_notes")
        if st.button("Add event",key="event_add_btn"):
            con=connect(); eid=next_id(con,'events','event_id','EVT')
            con.execute("""INSERT INTO events(event_id,start_global_day,end_global_day,event_name,scope,location,roll_required,
                           affected_class,active,source,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (eid,start,end,name,scope,location,1 if roll_required else 0,affected,1,'App entry',notes or None))
            con.commit(); con.close(); st.success(f"Added {eid}")

    if event_section=="Record effect":
        edf=q("SELECT event_id,event_name,start_global_day,end_global_day FROM events ORDER BY start_global_day DESC,event_id")
        if edf.empty:
            st.info("No events.")
        else:
            elabels=[f"{r.event_id} — {r.event_name} — GD {r.start_global_day}–{r.end_global_day}" for _,r in edf.iterrows()]
            ech=st.selectbox("Event",elabels,key="event_result_event")
            eid=ech.split(" — ",1)[0]
            opts=sim_options()
            hdf=q("SELECT household_id,household_name FROM households ORDER BY household_id")
            hopts=[""]+[f"{r.household_id} — {r.household_name or ''}" for _,r in hdf.iterrows()]
            a,b,c=st.columns(3)
            gd=a.number_input("Result Global Day",-10000,20000,g,key="event_result_gd")
            sim=b.selectbox("Affected Sim (optional)",opts,key="event_result_sim")
            house=c.selectbox("Household (optional)",hopts,key="event_result_hh")
            a,b,c=st.columns(3)
            roll_choice=a.text_input("Roll choice / value",key="event_result_roll")
            outcome=b.text_input("Outcome",key="event_result_outcome")
            status=c.text_input("Status",key="event_result_status")
            a,b=st.columns(2)
            death=a.checkbox("Death occurred",key="event_result_death")
            cause=b.text_input("Cause / effect",key="event_result_cause")
            notes=st.text_area("Notes",key="event_result_notes")
            if st.button("Save event result",type="primary",key="event_result_save"):
                con=connect(); rid=next_id(con,'event_results','result_id','ER')
                con.execute("""INSERT INTO event_results(result_id,event_id,global_day,household_id,sim_id,roll_choice,
                               outcome,status,death,cause_effect,completed,notes)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (rid,eid,gd,sid(house),sid(sim),roll_choice or None,outcome or None,status or None,
                             1 if death else 0,cause or None,1,notes or None))
                if death and sid(sim):
                    con.execute("""UPDATE sims SET death_global_day=COALESCE(death_global_day,?),
                                   cause_of_death=CASE WHEN ?<>'' THEN ? ELSE cause_of_death END
                                   WHERE sim_id=?""",(gd,cause or "",cause or "",sid(sim)))
                con.commit(); con.close(); st.success(f"Saved {rid}")

    if event_section=="Edit":
        edf=q("SELECT * FROM events ORDER BY start_global_day DESC,event_id")
        if edf.empty:
            st.info("No events.")
        else:
            labels=[f"{r.event_id} — {r.event_name}" for _,r in edf.iterrows()]
            ch=st.selectbox("Event",labels,key="event_edit_select")
            eid=ch.split(" — ",1)[0]
            row=q("SELECT * FROM events WHERE event_id=?",(eid,)).iloc[0].to_dict()
            a,b,c=st.columns(3)
            name=a.text_input("Event name",value=row.get("event_name") or "",key="event_edit_name")
            start=b.number_input("Start Global Day",-10000,20000,int(row.get("start_global_day") or g),key="event_edit_start")
            end=c.number_input("End Global Day",-10000,20000,int(row.get("end_global_day") or g),key="event_edit_end")
            a,b,c=st.columns(3)
            scope=a.text_input("Scope",value=row.get("scope") or "",key="event_edit_scope")
            location=b.text_input("Location",value=row.get("location") or "",key="event_edit_location")
            affected=c.text_input("Affected class",value=row.get("affected_class") or "",key="event_edit_affected")
            active=st.checkbox("Active",value=bool(row.get("active") if row.get("active") is not None else 1),key="event_edit_active")
            notes=st.text_area("Notes",value=row.get("notes") or "",key="event_edit_notes")
            if st.button("Save event",type="primary",key="event_edit_save"):
                con=connect()
                con.execute("""UPDATE events SET event_name=?,start_global_day=?,end_global_day=?,scope=?,location=?,
                               affected_class=?,active=?,notes=? WHERE event_id=?""",
                            (name,start,end,scope,location,affected,1 if active else 0,notes or None,eid))
                con.commit(); con.close(); st.success(f"Saved {eid}")


@workspace_fragment
def render_challenge_guides():
    page_header("Challenge Guides","Keep the two foundational Ultimate Decades rule sets beside your tracker.")
    st.caption("These guides remain the property of their creators and are displayed from their public source pages.")
    guide=st.segmented_control(
        "Guide",
        ["Several's Ultimate Decades Overhaul","MorbidGamer's Ultimate Decades Challenge"],
        default="Several's Ultimate Decades Overhaul",
        label_visibility="collapsed",
        key="challenge_guide_tab",
    )
    if guide=="Several's Ultimate Decades Overhaul":
        st.subheader("Several's Ultimate Decades Overhaul")
        st.info("Google Sites blocks inline frames. These official sections open directly without making you search for the right page.")
        a,b=st.columns(2)
        a.link_button("Open starting rules","https://sites.google.com/view/severaludo/starting-rules",use_container_width=True)
        b.link_button("Open basic start information","https://sites.google.com/view/severaludo/basic-start-info",use_container_width=True)
        a,b=st.columns(2)
        a.link_button("Browse events by era","https://sites.google.com/view/severaludo/events-by-era",use_container_width=True)
        b.link_button("Browse events by location","https://sites.google.com/view/severaludo/events-by-location",use_container_width=True)
        st.link_button("Open the complete SeveralUDO site","https://sites.google.com/view/severaludo/home",use_container_width=True)
    else:
        source_url="https://docs.google.com/document/d/1VKVvnDblpT2ngUs9JE9VcFyfhK9ZKXyqrYYaSun1pl4/preview"
        edit_url="https://docs.google.com/document/d/1VKVvnDblpT2ngUs9JE9VcFyfhK9ZKXyqrYYaSun1pl4/edit"
        st.subheader("MorbidGamer's Ultimate Decades Challenge")
        st.link_button("Open MorbidGamer's rules in a new tab",edit_url,use_container_width=True)
        st.components.v1.iframe(source_url,height=900,scrolling=True)
    st.caption("If an embedded guide is blocked by its host or asks you to sign in, use the open-in-new-tab button above it.")

@workspace_fragment
def render_statistics():
    import plotly.express as px

    page_header("Statistics","Detailed analytics, family records, demographic trends, and challenge records.")
    st.caption("Live analytics calculated from the SQLite database. Global Day is the canonical time coordinate.")

    cg=current_gd()
    sy,_=calendar_settings()
    ctx=_cached_statistics(cg,sy,*_query_revision(tables=("sims","households","pregnancies","relationships","rolls","events","event_results","settings")))
    se.attach_graph_helpers(ctx)

    sims=ctx["sims"]; living=sims[sims.living].copy(); deceased=sims[sims.death_global_day.notna()].copy()
    born_to_date=sims[sims.birth_global_day.notna() & (sims.birth_global_day<=cg)].copy()
    deceased_to_date=sims[sims.death_global_day.notna() & (sims.death_global_day<=cg)].copy()
    not_yet_born=sims[sims.birth_global_day.notna() & (sims.birth_global_day>cg)].copy()
    yearly=ctx["_yearly"]; decades=ctx["_decades"]
    births,parent_counts,mothers,fathers,sibling_gaps=ctx["_birth_bundle"]
    siblings,full_groups=ctx["_sibling_bundle"]
    lineage=ctx["_lineage"]
    rel,marr_counts=ctx["_relationship_bundle"]
    hhstats=ctx["_household_stats"]
    names=ctx["name"]

    def fnum(v,d=1):
        if v is None or pd.isna(v): return "—"
        return f"{float(v):,.{d}f}"
    def fint(v):
        if v is None or pd.isna(v): return "—"
        return f"{int(v):,}"
    def fpct(v):
        if v is None or pd.isna(v): return "—"
        return f"{float(v):.1f}%"
    def top_row(df,col,ascending=False):
        if df is None or df.empty or col not in df:return None
        x=df.dropna(subset=[col])
        if x.empty:return None
        return x.sort_values(col,ascending=ascending).iloc[0]
    def snapshot_age(year):
        endgd=min(se.gd_for_year_end(year,sy),cg)
        alive=sims[(sims.birth_global_day.notna())&(sims.birth_global_day<=endgd)&(sims.death_global_day.isna()|(sims.death_global_day>endgd))].copy()
        if alive.empty:return None
        return ((endgd-alive.birth_global_day)/4).mean()
    def stage_survival(threshold_days):
        eligible=sims[sims.birth_global_day.notna() & (sims.birth_global_day<=cg-threshold_days)]
        if eligible.empty:return None,0,0
        survived=((eligible.death_global_day.isna()) | (eligible.death_global_day>=eligible.birth_global_day+threshold_days)).sum()
        return survived/len(eligible)*100,int(survived),len(eligible)
    def sim_label(sid_):
        return f"{sid_} — {names.get(sid_,sid_)}" if sid_ else "—"

    stats_sections=["Overview","Population","Births & Fertility","Mortality","Age & Longevity",
                    "Generations & Dynasty","Family Structure","Gender","Households","Relationships",
                    "Decades","Events","Individual Sims","Records"]
    stats_section=st.selectbox("Statistics section",stats_sections,key="stats_section")

    if stats_section=="Overview":
        st.subheader("Challenge snapshot")
        a,b,c1,d=st.columns(4)
        a.metric("Total Sims ever recorded",fint(len(sims)))
        b.metric("Current living population",fint(len(living)))
        c1.metric("Deaths through current day",fint(len(deceased_to_date)),f"{len(deceased):,} deceased records total")
        cur_year,_=challenge_year_day(cg)
        d.metric("Current Global Day",f"{cg:,}",f"Year {cur_year}")
        st.caption(f"Born by current Global Day: {len(born_to_date):,} • Not yet born in the current snapshot: {len(not_yet_born):,}")
        a,b,c1,d=st.columns(4)
        a.metric("Recorded challenge births",fint(len(births)))
        b.metric("Relationships",fint(len(rel)))
        c1.metric("Households",fint(len(hhstats)))
        d.metric("Historical event definitions",fint(len(ctx["events"])))
        st.subheader("At a glance")
        if not yearly.empty:
            hi=top_row(yearly,"population_end")
            boom=top_row(yearly,"births")
            dead=top_row(yearly,"deaths")
            growth=top_row(yearly,"growth_rate_pct")
            a,b,c1,d=st.columns(4)
            a.metric("Highest population",fint(hi.population_end),f"Year {int(hi.year)}")
            b.metric("Biggest baby boom year",fint(boom.births),f"Year {int(boom.year)}")
            c1.metric("Deadliest year",fint(dead.deaths),f"Year {int(dead.year)}")
            d.metric("Fastest growth year",fpct(growth.growth_rate_pct),f"Year {int(growth.year)}")
            st.plotly_chart(px.line(yearly,x="year",y="population_end",title="Population over time",labels={"population_end":"Population"}),use_container_width=True)

    if stats_section=="Population":
        st.subheader("Population")
        total=len(sims); live=len(living); dead=len(deceased_to_date); born_now=len(born_to_date)
        a,b,c1,d=st.columns(4)
        a.metric("Total Sims ever recorded",fint(total))
        b.metric("Living now",fint(live),fpct(se.pct(live,born_now))+" of Sims born by now")
        c1.metric("Deceased by now",fint(dead),fpct(se.pct(dead,born_now))+" of Sims born by now")
        d.metric("Living : deceased",f"{live}:{dead}")
        if not yearly.empty:
            hi=top_row(yearly,"population_end"); lo=top_row(yearly,"population_end",ascending=True)
            a,b,c1,d=st.columns(4)
            a.metric("Highest population reached",fint(hi.population_end),f"Year {int(hi.year)}")
            b.metric("Lowest population reached",fint(lo.population_end),f"Year {int(lo.year)}")
            c1.metric("Average yearly population",fnum(yearly.population_end.mean()))
            d.metric("New Sims introduced",fint(yearly.introduced.sum()))
            st.plotly_chart(px.line(yearly,x="year",y=["population_start","population_end"],title="Population by year"),use_container_width=True)
            st.dataframe(yearly[["year","population_start","population_end","net_change","growth_rate_pct","births","introduced","deaths","natural_increase"]],use_container_width=True,hide_index=True)
        if not decades.empty:
            st.subheader("Population by decade")
            st.dataframe(decades[["decade","population_start","population_end","population_change","avg_population","births","deaths","natural_increase"]],use_container_width=True,hide_index=True)
            st.plotly_chart(px.bar(decades,x="decade",y="avg_population",title="Average population by decade"),use_container_width=True)

    if stats_section=="Births & Fertility":
        st.subheader("Births & fertility")
        multiples=births[births.multiple_birth.fillna("").str.lower()!="single"].copy()
        mult_groups=births[births.multiple_birth.fillna("").str.lower().isin(["twin","triplet","quadruplet","sextuplet"])].dropna(subset=["birth_global_day"]).groupby(["mother_id","birth_global_day","multiple_birth"],dropna=False).size().reset_index(name="babies")
        twins=int((mult_groups.multiple_birth.str.lower()=="twin").sum()) if not mult_groups.empty else 0
        trips=int((mult_groups.multiple_birth.str.lower()=="triplet").sum()) if not mult_groups.empty else 0
        a,b,c1,d=st.columns(4)
        a.metric("Total births",fint(len(births)))
        b.metric("Singleton births",fint((births.multiple_birth.fillna("").str.lower()=="single").sum()))
        c1.metric("Twin sets",fint(twins))
        d.metric("Triplet sets",fint(trips))
        a,b,c1,d=st.columns(4)
        a.metric("Multiples rate",fpct(se.pct(len(multiples),len(births))))
        b.metric("Babies born as multiples",fint(len(multiples)))
        c1.metric("Distinct mothers",fint(births.mother_id.dropna().nunique()))
        d.metric("Distinct fathers",fint(births.father_id.dropna().nunique()))
        if not yearly.empty:
            boom=top_row(yearly,"births")
            a,b,c1=st.columns(3)
            a.metric("Average births / year",fnum(yearly.births.mean()))
            a.caption("Across tracked years")
            b.metric("Highest-birth year",fint(boom.births),f"Year {int(boom.year)}")
            bdec=top_row(decades,"births") if not decades.empty else None
            c1.metric("Highest-birth decade",fint(bdec.births) if bdec is not None else "—",f"{int(bdec.decade)}s" if bdec is not None else "")
            st.plotly_chart(px.bar(yearly,x="year",y="births",title="Births per year"),use_container_width=True)
        if not decades.empty:
            st.plotly_chart(px.bar(decades,x="decade",y="births",title="Births per decade"),use_container_width=True)
        pc_all=pd.Series({sid_:len(ctx["children"].get(sid_,[])) for sid_ in sims.sim_id},name="children")
        dist=pc_all.value_counts().sort_index().rename_axis("children").reset_index(name="sims")
        a,b,c1,d=st.columns(4)
        a.metric("Avg children / recorded parent",fnum(parent_counts.mean()) if not parent_counts.empty else "—")
        a.caption("Parents with ≥1 recorded child")
        b.metric("Avg maternal children",fnum(pd.Series([len(ctx["children"].get(x,[])) for x in mothers.sim_id]).mean()) if not mothers.empty else "—")
        c1.metric("Avg paternal children",fnum(pd.Series([len(ctx["children"].get(x,[])) for x in fathers.sim_id]).mean()) if not fathers.empty else "—")
        maxp=parent_counts.index[0] if not parent_counts.empty else None
        d.metric("Most children by one Sim",fint(parent_counts.iloc[0]) if not parent_counts.empty else "—",sim_label(maxp) if maxp else "")
        st.subheader("Children-count distribution")
        st.dataframe(dist,use_container_width=True,hide_index=True)
        # Parent reproductive spans
        prow=[]
        for pid_,kids in ctx["children"].items():
            kd=sims[sims.sim_id.isin(kids)&sims.birth_global_day.notna()].sort_values("birth_global_day")
            if kd.empty: continue
            first=int(kd.iloc[0].birth_year); last=int(kd.iloc[-1].birth_year)
            prow.append({"parent_id":pid_,"parent":names.get(pid_,pid_),"children":len(kd),"first_child_year":first,"last_child_year":last,"years_between_first_last":(kd.iloc[-1].birth_global_day-kd.iloc[0].birth_global_day)/4})
        pdf=pd.DataFrame(prow).sort_values(["children","parent"],ascending=[False,True]) if prow else pd.DataFrame()
        if not pdf.empty:
            st.subheader("Parent fertility spans")
            st.dataframe(pdf,use_container_width=True,hide_index=True,height=360)
        if sibling_gaps:
            a,b,c1=st.columns(3)
            a.metric("Average spacing between siblings",f"{sum(sibling_gaps)/len(sibling_gaps)/4:.2f} years")
            a.caption("Adjacent full siblings")
            b.metric("Shortest sibling gap",f"{min(sibling_gaps)/4:.2f} years")
            c1.metric("Longest sibling gap",f"{max(sibling_gaps)/4:.2f} years")
        bg=births.groupby(["birth_year","sex"]).size().reset_index(name="births")
        if not bg.empty:
            st.plotly_chart(px.bar(bg,x="birth_year",y="births",color="sex",title="Births by gender and year"),use_container_width=True)
        genbirth=births.groupby("generation").size().reset_index(name="births").sort_values("generation")
        st.subheader("Births per generation")
        st.dataframe(genbirth,use_container_width=True,hide_index=True)

    if stats_section=="Mortality":
        st.subheader("Deaths & mortality")
        a,b,c1,d=st.columns(4)
        a.metric("Deaths through current day",fint(len(deceased_to_date)))
        a.caption(f"{len(deceased):,} death records exist across the full imported history")
        if not yearly.empty:
            dy=top_row(yearly,"deaths")
            b.metric("Highest-death year",fint(dy.deaths),f"Year {int(dy.year)}")
            dd=top_row(decades,"deaths") if not decades.empty else None
            c1.metric("Highest-death decade",fint(dd.deaths) if dd is not None else "—",f"{int(dd.decade)}s" if dd is not None else "")
            d.metric("Average deaths / year",fnum(yearly.deaths.mean()))
        lif=deceased.lifespan_years.dropna()
        if not lif.empty:
            a,b,c1,d=st.columns(4)
            youngest=deceased.loc[deceased.lifespan_years.idxmin()]
            oldest=deceased.loc[deceased.lifespan_years.idxmax()]
            a.metric("Average age at death",fnum(lif.mean())+" y")
            b.metric("Median age at death",fnum(lif.median())+" y")
            c1.metric("Youngest death",fnum(youngest.lifespan_years,2)+" y",sim_label(youngest.sim_id))
            d.metric("Oldest death",fnum(oldest.lifespan_years,2)+" y",sim_label(oldest.sim_id))
        if not yearly.empty:
            st.plotly_chart(px.bar(yearly,x="year",y=["births","deaths"],barmode="group",title="Births vs deaths per year"),use_container_width=True)
        if not decades.empty:
            st.plotly_chart(px.bar(decades,x="decade",y=["births","deaths"],barmode="group",title="Births vs deaths per decade"),use_container_width=True)
        stages=deceased.death_life_stage.fillna("Unknown").value_counts().rename_axis("life_stage").reset_index(name="deaths")
        st.subheader("Deaths by life stage")
        st.dataframe(stages,use_container_width=True,hide_index=True)
        s72=stage_survival(72); s240=stage_survival(240)
        a,b=st.columns(2)
        a.metric("Percentage reaching adulthood",fpct(s72[0]),f"{s72[1]:,} of {s72[2]:,} eligible cohorts")
        b.metric("Percentage reaching elderhood",fpct(s240[0]),f"{s240[1]:,} of {s240[2]:,} eligible cohorts")
        causes=deceased[deceased.cause_of_death.fillna("").str.strip()!=""].groupby("cause_of_death").size().reset_index(name="deaths").sort_values("deaths",ascending=False)
        if not causes.empty:
            st.subheader("Deaths by standardized cause")
            st.plotly_chart(px.bar(causes.head(30),x="deaths",y="cause_of_death",orientation="h"),use_container_width=True)
            st.dataframe(causes,use_container_width=True,hide_index=True,height=360)
        age_dec=deceased.dropna(subset=["death_decade","lifespan_years"]).groupby("death_decade").lifespan_years.mean().reset_index(name="average_age_at_death")
        age_gen=deceased.dropna(subset=["generation","lifespan_years"]).groupby("generation").lifespan_years.mean().reset_index(name="average_age_at_death")
        a,b=st.columns(2)
        with a: st.subheader("Average age at death by decade"); st.dataframe(age_dec,use_container_width=True,hide_index=True)
        with b: st.subheader("Average age at death by generation"); st.dataframe(age_gen,use_container_width=True,hide_index=True)

    if stats_section=="Age & Longevity":
        st.subheader("Age & longevity")
        ages=living.age_years.dropna()
        lif=deceased.lifespan_years.dropna()
        a,b,c1,d=st.columns(4)
        a.metric("Current average age",fnum(ages.mean())+" y" if not ages.empty else "—")
        b.metric("Current median age",fnum(ages.median())+" y" if not ages.empty else "—")
        if not living.empty:
            old=living.loc[living.age_years.idxmax()] if living.age_years.notna().any() else None
            young=living.loc[living.age_years.idxmin()] if living.age_years.notna().any() else None
            c1.metric("Oldest living Sim",fnum(old.age_years,2)+" y",sim_label(old.sim_id))
            d.metric("Youngest living Sim",fnum(young.age_years,2)+" y",sim_label(young.sim_id))
        a,b,c1,d=st.columns(4)
        a.metric("Average lifespan",fnum(lif.mean())+" y" if not lif.empty else "—")
        b.metric("Median lifespan",fnum(lif.median())+" y" if not lif.empty else "—")
        c1.metric("Longest lifespan",fnum(lif.max(),2)+" y" if not lif.empty else "—")
        d.metric("Shortest lifespan",fnum(lif.min(),2)+" y" if not lif.empty else "—")
        stagepop=living.current_life_stage.fillna("Unknown").value_counts().rename_axis("life_stage").reset_index(name="living")
        stagepop["percentage"]=stagepop.living/len(living)*100 if len(living) else 0
        st.subheader("Current population by life stage")
        st.dataframe(stagepop,use_container_width=True,hide_index=True)
        if not ages.empty:
            st.plotly_chart(px.histogram(living.dropna(subset=["age_years"]),x="age_years",nbins=30,title="Current age distribution"),use_container_width=True)
        lg=deceased.dropna(subset=["sex","lifespan_years"]).groupby("sex").lifespan_years.mean().reset_index(name="average_lifespan")
        lgen=deceased.dropna(subset=["generation","lifespan_years"]).groupby("generation").lifespan_years.mean().reset_index(name="average_lifespan")
        lbd=deceased.dropna(subset=["birth_decade","lifespan_years"]).groupby("birth_decade").lifespan_years.mean().reset_index(name="average_lifespan")
        a,b,c1=st.columns(3)
        with a: st.caption("Lifespan by gender"); st.dataframe(lg,use_container_width=True,hide_index=True)
        with b: st.caption("Lifespan by generation"); st.dataframe(lgen,use_container_width=True,hide_index=True)
        with c1: st.caption("Lifespan by birth decade"); st.dataframe(lbd,use_container_width=True,hide_index=True)
        chosen=st.number_input("Chosen survival age (years)",min_value=0,max_value=200,value=60,step=1,key="survival_age")
        surv=stage_survival(int(chosen*4))
        st.metric(f"Surviving past age {chosen}",fint(surv[1]),fpct(surv[0])+" of eligible cohorts")
        avg_age_dec=[]
        if not decades.empty:
            for dec in decades.decade:
                end=min(int(dec)+9,challenge_year_day(cg)[0])
                avg_age_dec.append({"decade":int(dec),"average_age_at_decade_end":snapshot_age(end)})
            st.subheader("Average living age by decade")
            st.dataframe(pd.DataFrame(avg_age_dec),use_container_width=True,hide_index=True)

    if stats_section=="Generations & Dynasty":
        st.subheader("Generations & dynasty")
        gens=sims.dropna(subset=["generation"]).copy()
        if not gens.empty:
            highest=int(gens.generation.max()); lowest=int(gens.generation.min())
            gc=gens.groupby("generation").agg(sims=("sim_id","count"),living=("living","sum")).reset_index()
            gd=deceased.dropna(subset=["generation"]).groupby("generation").size().rename("deaths")
            gb=births.dropna(subset=["generation"]).groupby("generation").size().rename("births")
            gl=deceased.dropna(subset=["generation","lifespan_years"]).groupby("generation").lifespan_years.mean().rename("avg_lifespan")
            gkids=pd.DataFrame({"sim_id":sims.sim_id,"generation":sims.generation,"children":[len(ctx["children"].get(x,[])) for x in sims.sim_id]}).dropna(subset=["generation"]).groupby("generation").children.mean().rename("avg_children")
            gc=gc.merge(gd,left_on="generation",right_index=True,how="left").merge(gb,left_on="generation",right_index=True,how="left").merge(gl,left_on="generation",right_index=True,how="left").merge(gkids,left_on="generation",right_index=True,how="left").fillna({"deaths":0,"births":0})
            a,b,c1,d=st.columns(4)
            a.metric("Highest generation",highest)
            b.metric("Generations reached",int(highest-lowest+1))
            large=gc.loc[gc.sims.idxmax()]; small=gc.loc[gc.sims.idxmin()]
            c1.metric("Largest generation",fint(large.sims),f"Generation {int(large.generation)}")
            d.metric("Smallest generation",fint(small.sims),f"Generation {int(small.generation)}")
            gc["growth_vs_prior"]=gc.sims.diff()
            st.dataframe(gc,use_container_width=True,hide_index=True)
            st.plotly_chart(px.bar(gc,x="generation",y=["sims","living"],barmode="group",title="Sims and living Sims per generation"),use_container_width=True)
        if not lineage.empty:
            md=lineage.loc[lineage.descendants.idxmax()]
            st.subheader("Lineage records")
            a,b,c1,d=st.columns(4)
            a.metric("Most descendants",fint(md.descendants),sim_label(md.sim_id))
            span=lineage.loc[lineage.generation_span.idxmax()]
            b.metric("Longest-running lineage",f"{int(span.generation_span)} generations",sim_label(span.sim_id))
            c1.metric("Sims with no descendants",fint((lineage.descendants==0).sum()))
            d.metric("Sims whose lineage continues",fint((lineage.living_descendants>0).sum()))
            st.dataframe(lineage.sort_values(["descendants","children"],ascending=False),use_container_width=True,hide_index=True,height=420)
            roots=[x for x in sims.sim_id if len(ctx["parents"].get(x,[]))==0]
            rootdf=lineage[lineage.sim_id.isin(roots)]
            st.caption(f"Root family branches: {len(rootdf):,} • surviving: {(rootdf.living_descendants>0).sum():,} • extinct: {(rootdf.living_descendants==0).sum():,}")
        intervals=[]
        for r in sims.itertuples():
            for child in ctx["children"].get(r.sim_id,[]):
                cgrow=sims[sims.sim_id==child]
                if not cgrow.empty and pd.notna(r.birth_global_day) and pd.notna(cgrow.iloc[0].birth_global_day):
                    intervals.append((cgrow.iloc[0].birth_global_day-r.birth_global_day)/4)
        if intervals: st.metric("Average generation interval",f"{sum(intervals)/len(intervals):.2f} years")

    if stats_section=="Family Structure":
        st.subheader("Siblings & family structure")
        only=int((siblings.siblings==0).sum()) if not siblings.empty else 0
        a,b,c1,d=st.columns(4)
        a.metric("Sibling groups",fint(sum(1 for v in full_groups.values() if len(v)>1)))
        a.caption("Full-sibling groups")
        b.metric("Average siblings / Sim",fnum(siblings.siblings.mean()) if not siblings.empty else "—")
        largest=max((len(v) for v in full_groups.values()),default=0)
        c1.metric("Largest full-sibling group",fint(largest))
        d.metric("Only children",fint(only))
        sdist=siblings.siblings.value_counts().sort_index().rename_axis("siblings").reset_index(name="sims")
        st.dataframe(sdist,use_container_width=True,hide_index=True)
        mult=births.multiple_birth.fillna("").str.lower()
        a,b,c1=st.columns(3)
        a.metric("Twin babies",fint((mult=="twin").sum()))
        b.metric("Triplet babies",fint((mult=="triplet").sum()))
        c1.metric("Sims born as multiples",fpct(se.pct((mult.isin(["twin","triplet","quadruplet","sextuplet"])).sum(),len(births))))
        if sibling_gaps:
            a,b,c1=st.columns(3)
            a.metric("Average sibling age gap",f"{sum(sibling_gaps)/len(sibling_gaps)/4:.2f} y")
            b.metric("Shortest gap",f"{min(sibling_gaps)/4:.2f} y")
            c1.metric("Longest gap",f"{max(sibling_gaps)/4:.2f} y")
        st.subheader("Sibling counts by Sim")
        st.dataframe(siblings.sort_values(["siblings","name"],ascending=[False,True]),use_container_width=True,hide_index=True,height=380)
        if not parent_counts.empty:
            fam=pd.DataFrame({"parent_id":parent_counts.index,"children":parent_counts.values})
            fam["parent"]=fam.parent_id.map(names)
            st.subheader("Parents with the largest families")
            st.dataframe(fam.head(30),use_container_width=True,hide_index=True)

    if stats_section=="Gender":
        st.subheader("Gender demographics")
        gender=sims.groupby("sex").agg(total=("sim_id","count"),living=("living","sum")).reset_index()
        gender["deceased"]=gender.total-gender.living
        gender["percentage"]=gender.total/len(sims)*100 if len(sims) else 0
        st.dataframe(gender,use_container_width=True,hide_index=True)
        if len(gender)>=2:
            male=int(gender.loc[gender.sex.str.lower()=="male","total"].sum()); female=int(gender.loc[gender.sex.str.lower()=="female","total"].sum())
            st.metric("Overall gender ratio",f"{male}:{female}","Male : Female")
        gb=births.groupby("sex").size().rename("births").reset_index()
        gd=deceased.groupby("sex").size().rename("deaths").reset_index()
        gl=deceased.dropna(subset=["lifespan_years"]).groupby("sex").lifespan_years.mean().rename("avg_lifespan").reset_index()
        ga=living.dropna(subset=["age_years"]).groupby("sex").age_years.mean().rename("avg_age").reset_index()
        gkids=pd.DataFrame({"sex":sims.sex,"children":[len(ctx["children"].get(x,[])) for x in sims.sim_id]}).groupby("sex").children.mean().rename("avg_children").reset_index()
        summary=gender.merge(gb,on="sex",how="left").merge(gd,on="sex",how="left").merge(gl,on="sex",how="left").merge(ga,on="sex",how="left").merge(gkids,on="sex",how="left")
        st.subheader("Gender summary")
        st.dataframe(summary,use_container_width=True,hide_index=True)
        bygen=sims.dropna(subset=["generation"]).groupby(["generation","sex"]).size().reset_index(name="sims")
        st.plotly_chart(px.bar(bygen,x="generation",y="sims",color="sex",barmode="group",title="Gender ratio by generation"),use_container_width=True)
        bydec=births.dropna(subset=["birth_decade"]).groupby(["birth_decade","sex"]).size().reset_index(name="births")
        st.plotly_chart(px.bar(bydec,x="birth_decade",y="births",color="sex",barmode="group",title="Gender ratio of births by decade"),use_container_width=True)
        lsg=living.groupby(["current_life_stage","sex"]).size().reset_index(name="living")
        st.subheader("Life-stage distribution by gender")
        st.dataframe(lsg,use_container_width=True,hide_index=True)

    if stats_section=="Households":
        st.subheader("Households")
        active=hhstats[hhstats.active_now]
        sizes=active.living_population
        a,b,c1,d=st.columns(4)
        a.metric("Total households",fint(len(hhstats)))
        b.metric("Active households",fint(len(active)))
        c1.metric("Average active household size",fnum(sizes.mean()) if not sizes.empty else "—")
        d.metric("Median active household size",fnum(sizes.median()) if not sizes.empty else "—")
        if not active.empty:
            large=active.loc[active.living_population.idxmax()]
            nonzero=active[active.living_population>0]
            small=nonzero.loc[nonzero.living_population.idxmin()] if not nonzero.empty else None
            a,b,c1=st.columns(3)
            a.metric("Largest household",fint(large.living_population),f"{large.household_id} — {large.household_name}")
            b.metric("Smallest occupied household",fint(small.living_population) if small is not None else "—",f"{small.household_id} — {small.household_name}" if small is not None else "")
            c1.metric("Households currently occupied",fint((active.living_population>0).sum()))
            total_living=max(1,len(living)); active=active.copy(); active["population_share_pct"]=active.living_population/total_living*100
            st.dataframe(active[["household_id","household_name","location","social_class","living_population","total_associated","population_share_pct"]].sort_values("living_population",ascending=False),use_container_width=True,hide_index=True,height=420)
        created=hhstats.dropna(subset=["start_global_day"]).copy()
        if not created.empty:
            created["year"]=created.start_global_day.apply(lambda x:se.year_from_gd(x,sy))
            created["decade"]=created.year.apply(se.decade_of_year)
            st.subheader("Households created by year")
            st.dataframe(created.groupby("year").size().reset_index(name="households_created"),use_container_width=True,hide_index=True)
        st.info("Historical household-size, household-move, origin/destination, and 'lived in multiple households' statistics are not treated as exact because the migrated Sim records store current household assignment rather than a complete household-membership history.")

    if stats_section=="Relationships":
        st.subheader("Relationships & marriage")
        if rel.empty:
            st.info("No relationship records.")
        else:
            marriages=rel[rel.type.fillna("").str.lower()=="marriage"].copy()
            active=marriages[marriages.status.fillna("").str.lower()=="active"]
            ended=marriages[marriages.status.fillna("").str.lower()!="active"]
            a,b,c1,d=st.columns(4)
            a.metric("Total relationships",fint(len(rel)))
            b.metric("Current relationships",fint((rel.status.fillna("").str.lower()=="active").sum()))
            c1.metric("Ended relationships",fint((rel.status.fillna("").str.lower()!="active").sum()))
            d.metric("Marriages",fint(len(marriages)))
            a,b,c1,d=st.columns(4)
            a.metric("Current marriages",fint(len(active)))
            b.metric("Ended marriages",fint(len(ended)))
            deathended=int(ended.status.fillna("").str.lower().str.contains("death").sum())
            c1.metric("Marriages ending in death",fint(deathended))
            d.metric("Marriage survival %",fpct(se.pct(len(active),len(marriages))))
            starts=marriages.dropna(subset=["start_year"]).groupby("start_year").size().reset_index(name="marriages")
            decs=marriages.dropna(subset=["start_decade"]).groupby("start_decade").size().reset_index(name="marriages")
            a,b=st.columns(2)
            with a: st.subheader("Marriages per year"); st.dataframe(starts,use_container_width=True,hide_index=True,height=300)
            with b: st.subheader("Marriages per decade"); st.dataframe(decs,use_container_width=True,hide_index=True,height=300)
            ages=pd.concat([marriages.p1_age,marriages.p2_age]).dropna()
            if not ages.empty:
                a,b,c1=st.columns(3)
                a.metric("Average marriage age",fnum(ages.mean())+" y")
                b.metric("Youngest marriage age",fnum(ages.min(),2)+" y")
                c1.metric("Oldest marriage age",fnum(ages.max(),2)+" y")
            durations=marriages.duration_years.dropna()
            if not durations.empty:
                a,b,c1=st.columns(3)
                a.metric("Average marriage length",fnum(durations.mean())+" y")
                b.metric("Shortest recorded marriage",fnum(durations.min(),2)+" y")
                c1.metric("Longest recorded marriage",fnum(durations.max(),2)+" y")
            gaps=marriages.age_gap.dropna()
            if not gaps.empty:
                a,b=st.columns(2)
                a.metric("Average partner age gap",fnum(gaps.mean(),2)+" y")
                b.metric("Largest partner age gap",fnum(gaps.max(),2)+" y")
            if not marr_counts.empty:
                mm=marr_counts.loc[marr_counts.marriages.idxmax()]
                never=len(sims)-marr_counts.sim_id.nunique()
                a,b,c1=st.columns(3)
                a.metric("Never-married Sims",fint(never))
                b.metric("Average marriages / married Sim",fnum(marr_counts.marriages.mean()))
                c1.metric("Most-married Sim",fint(mm.marriages),sim_label(mm.sim_id))
                st.dataframe(marr_counts.marriages.value_counts().sort_index().rename_axis("marriages").reset_index(name="sims"),use_container_width=True,hide_index=True)
            # Children per couple
            cp=[]
            for r in marriages.itertuples():
                kids=sims[((sims.mother_id==r.partner1_id)&(sims.father_id==r.partner2_id))|((sims.mother_id==r.partner2_id)&(sims.father_id==r.partner1_id))]
                cp.append({"relationship_id":r.relationship_id,"couple":f"{names.get(r.partner1_id,r.partner1_id)} + {names.get(r.partner2_id,r.partner2_id)}","children":len(kids)})
            cpf=pd.DataFrame(cp)
            if not cpf.empty:
                best=cpf.loc[cpf.children.idxmax()]
                a,b=st.columns(2)
                a.metric("Childless couples",fint((cpf.children==0).sum()))
                b.metric("Most children by a couple",fint(best.children),best.couple)
                st.dataframe(cpf.sort_values("children",ascending=False).head(50),use_container_width=True,hide_index=True)
            st.info("The imported relationship table contains marriages only and records ended marriages as 'Ended by death'. Divorce/separation rates therefore remain zero/unavailable unless those relationship types are added later.")

    if stats_section=="Decades":
        st.subheader("Historical time / Decades progression")
        cur_year,_=challenge_year_day(cg); cur_dec=(cur_year//10)*10
        first_year=int(sims.birth_year.dropna().min()) if sims.birth_year.notna().any() else sy
        a,b,c1,d=st.columns(4)
        a.metric("Current year",cur_year)
        b.metric("Current decade",f"{cur_dec}s")
        c1.metric("Years elapsed since tracker start",cur_year-sy)
        d.metric("Completed decades",(cur_year-sy)//10)
        st.subheader("Decade Summary")
        st.dataframe(decades,use_container_width=True,hide_index=True,height=480)
        if not decades.empty:
            st.plotly_chart(px.line(decades,x="decade",y="population_end",markers=True,title="Population at decade end"),use_container_width=True)
            st.plotly_chart(px.bar(decades,x="decade",y=["births","deaths","marriages","events"],barmode="group",title="Major activity by decade"),use_container_width=True)
            fast=top_row(decades,"population_change")
            decline=top_row(decades,"population_change",ascending=True)
            a,b=st.columns(2)
            a.metric("Fastest-growing decade",fint(fast.population_change),f"{int(fast.decade)}s")
            b.metric("Fastest-declining decade",fint(decline.population_change),f"{int(decline.decade)}s")

    if stats_section=="Events":
        st.subheader("Event statistics")
        ev=ctx["events"].copy(); evr=ctx["event_results"].copy()
        a,b,c1,d=st.columns(4)
        a.metric("Total event definitions",fint(len(ev)))
        b.metric("Recorded event results",fint(len(evr)))
        c1.metric("Distinct event names",fint(ev.event_name.nunique() if not ev.empty else 0))
        d.metric("Distinct affected Sims",fint(evr.sim_id.dropna().nunique() if not evr.empty else 0))
        if not ev.empty:
            ev["year"]=ev.start_global_day.apply(lambda x:se.year_from_gd(x,sy) if pd.notna(x) else None)
            ev["decade"]=ev.year.apply(se.decade_of_year)
            eyear=ev.dropna(subset=["year"]).groupby("year").size().reset_index(name="events")
            edec=ev.dropna(subset=["decade"]).groupby("decade").size().reset_index(name="events")
            etype=ev.groupby("event_name").size().reset_index(name="events").sort_values("events",ascending=False)
            if not etype.empty:
                common=etype.iloc[0]; least=etype.iloc[-1]
                a,b=st.columns(2)
                a.metric("Most common event",fint(common.events),common.event_name)
                b.metric("Least common event",fint(least.events),least.event_name)
            st.plotly_chart(px.bar(etype.head(30),x="events",y="event_name",orientation="h",title="Events by type"),use_container_width=True)
            a,b=st.columns(2)
            with a: st.caption("Events per year"); st.dataframe(eyear,use_container_width=True,hide_index=True,height=300)
            with b: st.caption("Events per decade"); st.dataframe(edec,use_container_width=True,hide_index=True,height=300)
        if not evr.empty:
            bysim=evr.dropna(subset=["sim_id"]).groupby("sim_id").size().reset_index(name="event_results").sort_values("event_results",ascending=False)
            bysim["sim"]=bysim.sim_id.map(names)
            if not bysim.empty:
                top=bysim.iloc[0]; st.metric("Sim with most recorded event results",fint(top.event_results),sim_label(top.sim_id))
                st.dataframe(bysim.head(50),use_container_width=True,hide_index=True)
            byhh=evr.dropna(subset=["household_id"]).groupby("household_id").size().reset_index(name="event_results").sort_values("event_results",ascending=False)
            st.subheader("Event results by household")
            st.dataframe(byhh,use_container_width=True,hide_index=True)
        st.info("The migrated `events` table contains historical world/challenge event definitions (famines, wars, epidemics, etc.), not a standardized personal-life event log. Birth/death/marriage/move/career/graduation event-category counts are therefore not inferred from free text.")

    if stats_section=="Individual Sims":
        st.subheader("Individual Sim statistics")
        opts=sim_options(blank=False)
        sel=st.selectbox("Sim",opts,key="stats_sim")
        ss=sid(sel)
        prof=se.individual_profile(ctx,ss)
        if prof:
            stats_photo=cached_sim_photo(ss)
            if stats_photo:
                pcol,info_col=st.columns([1,4])
                with pcol: st.image(stats_photo["image_data"],width=220)
                with info_col:
                    st.subheader(prof.get("Name",ss))
                    st.caption(ss)
            cols=st.columns(4)
            for i,(k,v) in enumerate(prof.items()):
                cols[i%4].metric(k,"—" if v is None or (isinstance(v,float) and pd.isna(v)) else str(v))
            st.subheader("Major family links")
            kidids=ctx["children"].get(ss,[])
            parids=ctx["parents"].get(ss,[])
            a,b=st.columns(2)
            with a:
                st.caption("Parents")
                st.write([sim_label(x) for x in parids] or ["None recorded"])
                st.caption("Children")
                st.write([sim_label(x) for x in kidids] or ["None recorded"])
            with b:
                rr=rel[(rel.partner1_id==ss)|(rel.partner2_id==ss)]
                st.caption("Relationships")
                st.dataframe(rr,use_container_width=True,hide_index=True)
            evs=ctx["event_results"][ctx["event_results"].sim_id==ss].sort_values("global_day") if not ctx["event_results"].empty else pd.DataFrame()
            if not evs.empty:
                st.subheader("Recorded events affecting this Sim")
                st.dataframe(evs,use_container_width=True,hide_index=True)

    if stats_section=="Records":
        st.subheader("Records & fun stats")
        cards=[]
        if not deceased.empty and deceased.lifespan_years.notna().any():
            longest=deceased.loc[deceased.lifespan_years.idxmax()]
            youngest=deceased.loc[deceased.lifespan_years.idxmin()]
            cards += [("Longest-lived Sim",f"{longest.lifespan_years:.2f} y",sim_label(longest.sim_id)),
                      ("Youngest death",f"{youngest.lifespan_years:.2f} y",sim_label(youngest.sim_id))]
        if not living.empty and living.age_years.notna().any():
            oldest=living.loc[living.age_years.idxmax()]; young=living.loc[living.age_years.idxmin()]
            cards += [("Oldest living Sim",f"{oldest.age_years:.2f} y",sim_label(oldest.sim_id)),
                      ("Youngest living Sim",f"{young.age_years:.2f} y",sim_label(young.sim_id))]
        if not parent_counts.empty:
            p=parent_counts.index[0]; cards.append(("Most children",fint(parent_counts.iloc[0]),sim_label(p)))
        if not lineage.empty:
            r=lineage.loc[lineage.descendants.idxmax()]; cards.append(("Most descendants",fint(r.descendants),sim_label(r.sim_id)))
        if not siblings.empty:
            r=siblings.loc[siblings.siblings.idxmax()]; cards.append(("Most siblings",fint(r.siblings),sim_label(r.sim_id)))
        if not marr_counts.empty:
            r=marr_counts.loc[marr_counts.marriages.idxmax()]; cards.append(("Most-married Sim",fint(r.marriages),sim_label(r.sim_id)))
        if not yearly.empty:
            r=yearly.loc[yearly.births.idxmax()]; cards.append(("Biggest baby boom year",fint(r.births),f"Year {int(r.year)}"))
            r=yearly.loc[yearly.deaths.idxmax()]; cards.append(("Deadliest year",fint(r.deaths),f"Year {int(r.year)}"))
            r=yearly.loc[yearly.population_end.idxmax()]; cards.append(("Highest population year",fint(r.population_end),f"Year {int(r.year)}"))
        if not decades.empty:
            r=decades.loc[decades.births.idxmax()]; cards.append(("Biggest baby boom decade",fint(r.births),f"{int(r.decade)}s"))
            r=decades.loc[decades.deaths.idxmax()]; cards.append(("Deadliest decade",fint(r.deaths),f"{int(r.decade)}s"))
            r=decades.loc[decades.population_change.idxmax()]; cards.append(("Fastest-growing decade",fint(r.population_change),f"{int(r.decade)}s"))
            r=decades.loc[decades.population_change.idxmin()]; cards.append(("Fastest-declining decade",fint(r.population_change),f"{int(r.decade)}s"))
        evr=ctx["event_results"]
        if not evr.empty and evr.sim_id.notna().any():
            e=evr.groupby("sim_id").size().sort_values(ascending=False)
            cards.append(("Most eventful Sim",fint(e.iloc[0]),sim_label(e.index[0])))
        if not rel.empty:
            durations=rel.dropna(subset=["duration_years"])
            if not durations.empty:
                lg=durations.loc[durations.duration_years.idxmax()]; sh=durations.loc[durations.duration_years.idxmin()]
                cards += [("Longest marriage",f"{lg.duration_years:.2f} y",f"{names.get(lg.partner1_id,lg.partner1_id)} + {names.get(lg.partner2_id,lg.partner2_id)}"),
                          ("Shortest marriage",f"{sh.duration_years:.2f} y",f"{names.get(sh.partner1_id,sh.partner1_id)} + {names.get(sh.partner2_id,sh.partner2_id)}")]
            gaps=rel.dropna(subset=["age_gap"])
            if not gaps.empty:
                ag=gaps.loc[gaps.age_gap.idxmax()]
                cards.append(("Largest age-gap marriage",f"{ag.age_gap:.2f} y",f"{names.get(ag.partner1_id,ag.partner1_id)} + {names.get(ag.partner2_id,ag.partner2_id)}"))
        if not hhstats.empty:
            ac=hhstats[hhstats.active_now]
            if not ac.empty:
                h=ac.loc[ac.living_population.idxmax()]
                cards.append(("Largest household",fint(h.living_population),f"{h.household_id} — {h.household_name}"))
        if not lineage.empty:
            lr=lineage.loc[lineage.generation_span.idxmax()]
            cards.append(("Family line spanning most generations",f"{int(lr.generation_span)} generations",sim_label(lr.sim_id)))
        for i in range(0,len(cards),4):
            cols=st.columns(4)
            for j,item in enumerate(cards[i:i+4]):
                cols[j].metric(item[0],item[1],item[2])

    with st.expander("Methodology & currently unsupported metrics"):
        st.markdown("""
- **Global Day** is the canonical time coordinate. Year/decade values are derived from it.
- **Population for a year** is the number of Sims born by that snapshot who had not yet died at that snapshot.
- **Births** exclude `Married In` and `Adopted In` records; those are counted as introductions instead.
- **Life stages** follow the tracker thresholds already used by the app: Newborn 0–<1 challenge day; Infant 1–<4; Toddler 4–<20; Child 20–<40; Preteen 40–<52; Teen 52–<72; Young Adult 72–<160; Adult 160–<240; Elder 240+.
- **Survival percentages** use only cohorts old enough to have reached the selected threshold, avoiding penalizing Sims who are simply still too young.
- **Death causes** are reliably trackable because `cause_of_death` is a dedicated field.
- **Historical household membership/moves** are not reconstructed from current household assignments.
- **Divorce/separation** is not inferred because the migrated relationship table currently contains marriages with statuses `Active` or `Ended by death`.
- **Personal-life event categories** such as birth, death, marriage, graduation, career, and move are not inferred from notes because the current standardized Events table represents historical/challenge events.
""")


@workspace_fragment
def render_notes():
    page_header("Notes","A private notebook for the active save: plans, research, reminders, and family chronicles.")
    notes=q("""SELECT note_id,title,category,body,pinned,created_at,updated_at
               FROM notebook_entries ORDER BY pinned DESC,updated_at DESC,title""")
    notes_section=st.segmented_control("Notes section",["Notebook","New note","Edit or delete"],default=None,label_visibility="collapsed",key="notes_section") or "Notebook"

    if notes_section=="Notebook":
        if notes.empty:
            st.info("No notes yet. Use New note to begin this save's notebook.")
        else:
            categories=sorted(x for x in notes.category.dropna().unique() if str(x).strip())
            a,b=st.columns([2,1])
            search=a.text_input("Search notes",placeholder="Search titles and text",key="notes_search")
            category=b.selectbox("Category",["All categories"]+categories,key="notes_category_filter")
            shown=notes.copy()
            if search:
                term=search.casefold()
                shown=shown[shown.apply(lambda row: term in f"{row.title} {row.category or ''} {row.body or ''}".casefold(),axis=1)]
            if category!="All categories": shown=shown[shown.category==category]
            st.caption(f"{len(shown):,} note{'s' if len(shown)!=1 else ''} shown")
            for _,row in shown.iterrows():
                pin="📌 " if bool(row.pinned) else ""
                label=f"{pin}{row.title} · {row.category or 'Unfiled'}"
                with st.expander(label):
                    st.markdown(row.body or "*Empty note*")
                    st.caption(f"Updated {row.updated_at or row.created_at or '—'} · {row.note_id}")

    if notes_section=="New note":
        with st.form("new_notebook_note",clear_on_submit=True):
            title=st.text_input("Title")
            a,b=st.columns([2,1])
            category=a.text_input("Category",placeholder="Family, Build plans, Research…")
            pinned=b.checkbox("Pin this note")
            body=st.text_area("Note",height=280)
            submitted=st.form_submit_button("Save note",type="primary",use_container_width=True)
        if submitted:
            if not title.strip(): st.error("Give the note a title.")
            else:
                con=connect(); nid=notebook.next_id(con); now=notebook.timestamp()
                con.execute("""INSERT INTO notebook_entries(note_id,title,category,body,pinned,created_at,updated_at)
                               VALUES(?,?,?,?,?,?,?)""",
                            (nid,title.strip(),category.strip() or None,body.strip() or None,1 if pinned else 0,now,now))
                con.commit(); con.close(); st.success(f"Saved {title.strip()}."); st.rerun()

    if notes_section=="Edit or delete":
        if notes.empty:
            st.info("Create a note first.")
        else:
            labels=[f"{r.note_id} — {r.title}" for _,r in notes.iterrows()]
            choice=st.selectbox("Choose note",labels,key="note_edit_choice")
            nid=choice.split(" — ",1)[0]
            row=notes[notes.note_id==nid].iloc[0]
            title=st.text_input("Title",value=row.title,key=f"note_title_{nid}")
            a,b=st.columns([2,1])
            category=a.text_input("Category",value=row.category or "",key=f"note_category_{nid}")
            pinned=b.checkbox("Pinned",value=bool(row.pinned),key=f"note_pinned_{nid}")
            body=st.text_area("Note",value=row.body or "",height=280,key=f"note_body_{nid}")
            if st.button("Save changes",type="primary",key=f"note_save_{nid}"):
                if not title.strip(): st.error("Give the note a title.")
                else:
                    con=connect(); con.execute("""UPDATE notebook_entries SET title=?,category=?,body=?,pinned=?,updated_at=?
                                                   WHERE note_id=?""",
                                               (title.strip(),category.strip() or None,body.strip() or None,
                                                1 if pinned else 0,notebook.timestamp(),nid))
                    con.commit(); con.close(); st.success("Note updated."); st.rerun()
            with st.expander("Delete this note"):
                confirm=st.checkbox("I understand this permanently deletes the selected note.",key=f"note_delete_confirm_{nid}")
                if st.button("Delete note",disabled=not confirm,key=f"note_delete_{nid}"):
                    con=connect(); con.execute("DELETE FROM notebook_entries WHERE note_id=?",(nid,)); con.commit(); con.close()
                    st.success("Note deleted."); st.rerun()

@workspace_fragment
def render_planting_reference():
    page_header("Historical Planting Reference","See which Sims plants fit the current year and challenge location.")
    con=connect()
    main_household=setting(con,"main_household_id","")
    location_row=con.execute("SELECT location FROM households WHERE household_id=?",(main_household,)).fetchone() if main_household else None
    con.close()
    saved_location=(location_row[0] if location_row else "") or "England"
    historical_year,_=challenge_year_day(current_gd())
    a,b=st.columns(2)
    reference_year=int(a.number_input("Historical year",-5000,3000,int(historical_year),key="plant_reference_year",
                                      help="Defaults to the year calculated from the save's current Global Day."))
    challenge_location=b.text_input("Challenge location",value=saved_location,key="plant_reference_location",
                                    help="Defaults to the Main household's saved location.")
    detected=plant_reference.region_for(challenge_location)
    region=st.selectbox("Historical growing region",plant_reference.REGIONS,
                        index=plant_reference.REGIONS.index(detected),key="plant_reference_region",
                        help="Confirm the broad region used for historical introduction dates.")
    if detected=="Other / custom":
        st.warning("The location could not be mapped automatically. Choose the closest historical growing region above; dates will remain approximate.")
    plants=pd.DataFrame(plant_reference.rows(reference_year,region))
    available=int((plants.Status=="Historically available").sum())
    later=int(plants.Status.str.startswith("Not yet").sum())
    fantasy=int((plants.Status=="Challenge-dependent").sum())
    x,y,z=st.columns(3); x.metric("Historically available",available); y.metric("Not introduced yet",later); z.metric("Challenge-dependent",fantasy)
    st.info(f"Showing historical plausibility for {region} in {reference_year}. Sims outdoor seasons remain listed separately for gameplay.")
    a,b,c=st.columns([2,1,1])
    search=a.text_input("Find a plant",placeholder="Apple, potato, death flower…",key="plant_search")
    status=b.selectbox("Historical status",["All statuses","Historically available","Not yet","Challenge-dependent","Needs local research"],key="plant_status")
    season=c.selectbox("Sims outdoor season",["Any","Spring","Summer","Fall","Winter","All seasons"],key="plant_season")
    shown=plants.copy()
    if search:
        term=search.casefold()
        shown=shown[shown.apply(lambda row: term in " ".join(str(x) for x in row).casefold(),axis=1)]
    if status=="Not yet": shown=shown[shown.Status.str.startswith("Not yet")]
    elif status!="All statuses": shown=shown[shown.Status==status]
    if season!="Any":
        shown=shown[shown["Sims outdoor season"].str.contains(season,case=False,na=False)]
    st.caption(f"{len(shown):,} plants shown")
    friendly_cards(shown.sort_values("Plant"),"Plant",
        meta=("Sims outdoor season","Pack","Origin",
              lambda r:("Historical cutoff",r.get("Historical cutoff"))),
        body="Historical note",
        badge="Status",limit=100)
    st.warning("Historical cutoffs are conservative gameplay guidelines. A plant may have existed earlier as an imported luxury, wild species, medicine, or ornamental before it became a practical household crop.")
    st.caption("Research basis: English Heritage's British food timeline, the Royal Horticultural Society's crop histories, Nature's tomato history, and scholarship on the Columbian Exchange. Sims seasons come from EA's gardening guide.")
    st.markdown("[English Heritage food timeline](https://www.english-heritage.org.uk/visit/places/stonehenge/history-and-stories/history/food-timeline/) · [RHS crop facts](https://www.rhs.org.uk/advice/grow-your-own/features/fascinating-facts-and-figures/) · [Columbian Exchange research](https://pubs.aeaweb.org/doi/10.1257/jep.24.2.163) · [EA gardening guide](https://help.ea.com/en/help/the-sims/the-sims-4/the-sims-4-gardening-guide/)")

@workspace_fragment
def render_saves():
    page_header("Saves","Keep completely separate challenge worlds in one tracker. Each save has its own Sims, calendar, photos, rolls, events, relationships, households, and statistics.")

    saves=save_manager.list_saves()
    active=save_manager.active_save()
    st.markdown(f"### Active save: **{active['name']}**")
    sy,dpy=calendar_settings()
    cy,cd=challenge_year_day(current_gd())

    a,b,c,d=st.columns(4)
    a.metric("Calendar start year",sy)
    b.metric("Current historical year",cy)
    c.metric("Current Global Day",current_gd())
    d.metric("Sims in this save",scalar("SELECT COUNT(*) FROM sims"))

    st.subheader("Your saves")
    summary_rows=[]
    for s in saves:
        sims_count=0; year="—"; gd="—"
        try:
            save_manager.set_active(s["save_id"])
            con=connect()
            sims_count=con.execute("SELECT COUNT(*) FROM sims").fetchone()[0]
            vals=dict(con.execute("SELECT key,value FROM settings WHERE key IN ('start_year','days_per_year','current_global_day')").fetchall())
            ssy=int(float(vals.get("start_year",1200))); sdpy=int(float(vals.get("days_per_year",4))); sgd=int(float(vals.get("current_global_day",1)))
            year=global_day_to_year_day(sgd,ssy,sdpy)[0]; gd=sgd
            con.close()
        except Exception:
            pass
        summary_rows.append({
            "Active":"✓" if s["save_id"]==active["save_id"] else "",
            "Save":s["name"],"Historical year":year,"Global Day":gd,"Sims":sims_count,"Save ID":s["save_id"]
        })
    save_manager.set_active(active["save_id"])
    friendly_cards(summary_rows,"Save",
        meta=("Historical year",lambda r:("Global Day",r.get("Global Day")),"Sims"),
        body=lambda r:f"Save ID: {r.get('Save ID')}",
        badge=lambda r:"Active" if r.get("Active") else "Available",limit=30)

    save_section=st.segmented_control("Save section",["New save","Duplicate","Manage","Import"],default=None,label_visibility="collapsed",key="save_section") or "New save"

    if save_section=="New save":
        st.subheader("Create a completely new save")
        st.caption("The new save starts with no Sims or gameplay history. Your challenge rules and roll-table configuration are copied so you do not have to rebuild them.")
        a,b=st.columns(2)
        new_name=a.text_input("Save name",placeholder="e.g. Victorian Legacy",key="save_new_name")
        calendar_start=b.number_input("Calendar start year",-10000,10000,1200,step=1,key="save_new_start")
        a,b=st.columns(2)
        initial_year=a.number_input("Initial historical year",-10000,10000,int(calendar_start),step=1,key="save_new_year")
        initial_day=b.selectbox("Initial challenge day",[1,2,3,4],index=0,key="save_new_day")
        st.caption(f"This will begin at historical year {initial_year}, challenge day {initial_day}.")
        if st.button("Create blank save",type="primary",use_container_width=True,key="save_create_blank"):
            if not new_name.strip():
                st.error("Give the save a name.")
            elif int(initial_year)<int(calendar_start):
                st.error("The initial historical year cannot be earlier than the calendar start year.")
            else:
                created=save_manager.create_blank(new_name.strip(),int(calendar_start),int(initial_year),int(initial_day))
                st.session_state["active_save_id"]=created["save_id"]
                st.success("Save created.")
                st.rerun()

    if save_section=="Duplicate":
        st.subheader("Duplicate an existing save")
        st.caption("This makes a full independent copy, including Sims, photos, relationships, rolls, and current time.")
        labels=[f"{s['name']} — {s['save_id']}" for s in saves]
        source=st.selectbox("Source save",labels,index=next((i for i,s in enumerate(saves) if s["save_id"]==active["save_id"]),0),key="save_dup_source")
        dup_name=st.text_input("New save name",value=f"{active['name']} Copy",key="save_dup_name")
        if st.button("Duplicate save",type="primary",use_container_width=True,key="save_dup_btn"):
            if not dup_name.strip():
                st.error("Give the duplicate a name.")
            else:
                source_id=source.rsplit(" — ",1)[-1]
                created=save_manager.duplicate_save(source_id,dup_name.strip())
                st.session_state["active_save_id"]=created["save_id"]
                st.success("Save duplicated.")
                st.rerun()

    if save_section=="Manage":
        st.subheader("Rename, export, or delete")
        labels=[f"{s['name']} — {s['save_id']}" for s in saves]
        selected=st.selectbox("Save",labels,index=next((i for i,s in enumerate(saves) if s["save_id"]==active["save_id"]),0),key="save_manage_select")
        selected_id=selected.rsplit(" — ",1)[-1]
        selected_rec=save_manager.get_save(selected_id)
        new_title=st.text_input("Save name",value=selected_rec["name"],key="save_rename_name")
        a,b=st.columns(2)
        if a.button("Rename save",use_container_width=True,key="save_rename_btn"):
            save_manager.rename_save(selected_id,new_title)
            st.success("Save renamed.")
            st.rerun()
        package_bytes=save_manager.export_save_package(selected_id)
        safe_name="".join(ch if ch.isalnum() or ch in ("-","_") else "_" for ch in selected_rec["name"]).strip("_") or "Decades_Save"
        b.download_button(
            "Export shareable save",
            data=package_bytes,
            file_name=f"{safe_name}.decades-save",
            mime="application/zip",
            use_container_width=True,
            help="Includes this save's complete database, including portraits and save-specific roll tables."
        )

        st.divider()
        st.markdown("**Delete save**")
        if len(saves)<=1:
            st.caption("You cannot delete your only save.")
        else:
            confirm=st.checkbox(f"I understand this will permanently delete “{selected_rec['name']}” from the app.",key="save_delete_confirm")
            if st.button("Delete save",disabled=not confirm,type="secondary",use_container_width=True,key="save_delete_btn"):
                save_manager.delete_save(selected_id)
                if st.session_state.get("active_save_id")==selected_id:
                    st.session_state.pop("active_save_id",None)
                st.success("Save deleted.")
                st.rerun()

    if save_section=="Import":
        st.subheader("Import a shared save")
        st.caption("Import a `.decades-save` file from another player. Older raw `.db` backups are supported too.")
        imported=st.file_uploader("Save file",type=["decades-save","db","sqlite","sqlite3"],key="save_import_file")
        import_name=st.text_input("Name override (optional)",placeholder="Leave blank to use the shared save's name",key="save_import_name")
        if st.button("Import save",type="primary",use_container_width=True,key="save_import_btn"):
            if imported is None:
                st.error("Choose a save file first.")
            else:
                try:
                    preferred=import_name.strip() or None
                    rec=save_manager.import_save_package(imported.getvalue(),preferred,make_active=True)
                    st.session_state["active_save_id"]=rec["save_id"]
                    st.success(f"Imported “{rec['name']}”.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not import that save: {e}")

    st.info("Share saves using `.decades-save` files. They contain the complete selected world—including Sims, portraits, relationships, households, pregnancies, rolls, events, statistics source data, calendar state, and that save's roll-table configuration. Your original `decades.db` remains a legacy safety copy.")

@workspace_fragment
def render_rules_health():
    page_header("Rules Health","Validate schedules and rule coverage, and run expensive maintenance only when you choose.")
    con=connect(); action_queue.ensure_schema(con); checks=action_queue.validation(con,current_gd()); con.close()
    health=pd.DataFrame(checks)
    errors=sum(row["count"] for row in checks if row["severity"]=="Error")
    warnings=sum(row["count"] for row in checks if row["severity"]=="Warning")
    a,b,c=st.columns(3); a.metric("Errors",errors); b.metric("Warnings",warnings); c.metric("Checks",len(checks))
    friendly_cards(health,"check",meta=("severity","count"),body="detail",badge="severity")
    section_heading("Maintenance","Ordinary page loads use incremental queue updates; full repairs run only here")
    a,b=st.columns(2)
    if a.button("Run lightweight reconciliation",type="primary",use_container_width=True,key="maintenance_incremental"):
        con=connect(); summary=action_queue.run_maintenance(con,current_gd(),full=False); con.close(); st.success(summary); st.rerun()
    full_confirm=b.checkbox("Enable full maintenance",False,key="maintenance_full_confirm")
    if b.button("Run full rules repair",disabled=not full_confirm,use_container_width=True,key="maintenance_full"):
        con=connect(); summary=action_queue.run_maintenance(con,current_gd(),full=True); con.close(); st.success(summary); st.rerun()
    jobs=q("SELECT job_key,status,last_run_at,summary FROM maintenance_jobs ORDER BY last_run_at DESC")
    if not jobs.empty: friendly_cards(jobs,"job_key",meta=("status","last_run_at"),body="summary")

@workspace_fragment
def render_rules_and_data():
    page_header("Rules & Data","Configure era roll tables, inspect imported rules, and back up your database.")
    c=connect()
    era_rules.ensure_schema(c)
    c.close()

    rules_section=st.segmented_control(
        "Rules section",["Challenge Settings","Imported Rules","Roll Tables","Data & Backup"],
        default="Challenge Settings",label_visibility="collapsed",key="rules_data_section"
    )

    if rules_section=="Challenge Settings":
        st.subheader("Automatic challenge defaults")
        st.caption("The tracker works immediately with its built-in defaults. Change these values only when your challenge rules call for it.")
        con=connect()
        setting_rows=dict(con.execute(
            "SELECT key,value FROM settings WHERE key IN ('start_year','days_per_year','current_global_day','automatic_death_causes')"
        ).fetchall())
        pregnancy_row=con.execute(
            "SELECT source_row,col_b FROM rules WHERE row_label=? ORDER BY source_row DESC LIMIT 1",
            ("Pregnancy Length (challenge days)",),
        ).fetchone()
        con.close()
        saved_start=int(float(setting_rows.get("start_year",1200)))
        saved_dpy=max(1,int(float(setting_rows.get("days_per_year",4))))
        saved_gd=int(float(setting_rows.get("current_global_day",1)))
        saved_year,saved_day=global_day_to_year_day(saved_gd,saved_start,saved_dpy)
        saved_pregnancy=max(1,int(float(pregnancy_row["col_b"] if pregnancy_row and pregnancy_row["col_b"] else 3)))
        saved_auto_causes=str(setting_rows.get("automatic_death_causes","1"))!="0"
        chronicle_note(
            "The steward's calendar",
            "Recommended defaults are Start Year 1200, four challenge days per year, and a three-day pregnancy. All may be amended without editing raw tables.",
        )
        with st.form("challenge_settings_form"):
            a,b,c1=st.columns(3)
            calendar_start=a.number_input("Calendar start year",-10000,10000,saved_start,step=1)
            days_year=b.number_input("Challenge days per year",1,365,saved_dpy,step=1)
            pregnancy_days=c1.number_input("Pregnancy length (challenge days)",1,365,saved_pregnancy,step=1)
            a,b=st.columns(2)
            current_year_input=a.number_input("Current historical year",-10000,10000,saved_year,step=1)
            current_day_input=b.number_input(
                "Current challenge day within year",1,int(days_year),min(saved_day,int(days_year)),step=1
            )
            automatic_causes=st.checkbox("Automatically choose causes of death",value=saved_auto_causes,
                                         help="Event deaths use the event name. Aging deaths draw from the editable life-stage lists below.")
            st.warning("Changing the calendar start or days per year changes how every existing Global Day is displayed; records themselves are not deleted.")
            save_challenge_settings=st.form_submit_button("Save challenge settings",type="primary",use_container_width=True)
        if save_challenge_settings:
            new_gd=(int(current_year_input)-int(calendar_start))*int(days_year)+int(current_day_input)
            con=connect()
            try:
                for key,value in (
                    ("start_year",calendar_start),("days_per_year",days_year),("current_global_day",new_gd),
                    ("automatic_death_causes",1 if automatic_causes else 0)
                ):
                    con.execute(
                        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key,str(int(value))),
                    )
                if pregnancy_row:
                    con.execute("UPDATE rules SET col_b=? WHERE source_row=?",(str(int(pregnancy_days)),pregnancy_row["source_row"]))
                else:
                    next_source=con.execute("SELECT COALESCE(MAX(source_row),0)+1 FROM rules").fetchone()[0]
                    con.execute(
                        "INSERT INTO rules(section,row_label,col_b,source_row) VALUES(?,?,?,?)",
                        ("PREGNANCY","Pregnancy Length (challenge days)",str(int(pregnancy_days)),next_source),
                    )
                con.commit()
            except Exception as error:
                con.rollback(); con.close(); st.error(f"Could not save challenge settings: {error}")
            else:
                con.close(); _cached_clock_settings.clear(); sync_auto_rolls(show_notice=False)
                st.success("Challenge settings saved and the automatic roll schedule refreshed.")
                st.rerun()

        st.subheader("Cause-of-death lists")
        st.caption("A fatal aging roll randomly chooses an active cause from its life-stage group. Add, edit, disable, or remove entries here.")
        cause_rows=q("SELECT death_group,cause,active FROM death_cause_pools ORDER BY death_group,cause")
        edited_causes=st.data_editor(cause_rows,num_rows="dynamic",hide_index=True,use_container_width=True,
                                     column_config={"active":st.column_config.CheckboxColumn("Active")},key="death_cause_editor")
        if st.button("Save cause-of-death lists",type="primary",key="death_cause_save"):
            con=connect(); con.execute("DELETE FROM death_cause_pools")
            for _,cause_row in edited_causes.iterrows():
                group=str(cause_row.get("death_group") or "").strip(); cause_text=str(cause_row.get("cause") or "").strip()
                if group and cause_text:
                    con.execute("INSERT INTO death_cause_pools(death_group,cause,active) VALUES(?,?,?) ON CONFLICT DO NOTHING",
                                (group,cause_text,1 if bool(cause_row.get("active")) else 0))
            con.commit(); con.close(); st.success("Cause-of-death lists saved."); st.rerun()

        with st.expander("Try the complete carved dice set",expanded=False):
            st.caption("A practice tray only; these casts are not written to the roll ledger.")
            historical_dice_tray(None,"rules_dice_practice","rules_dice_practice_result")

    if rules_section=="Imported Rules":
        st.subheader("Imported challenge rules")
        st.dataframe(q("SELECT section,row_label,col_b,col_c,col_d,col_e FROM rules ORDER BY source_row"),
                     use_container_width=True,hide_index=True,height=520)

    if rules_section=="Roll Tables":
        st.subheader("Era-aware roll tables")
        st.caption("Create as many year ranges as your challenge needs. The automatic scheduler chooses the active table whose species and year range match the roll.")

        eras=q("""SELECT era_id,era_name,start_year,end_year,species,active,notes
                  FROM roll_rule_eras ORDER BY species,start_year,end_year""")
        if not eras.empty:
            st.dataframe(eras,use_container_width=True,hide_index=True)

        with st.expander("Add a new roll-table era",expanded=eras.empty):
            a,b,c1,d=st.columns(4)
            era_name=a.text_input("Era name",placeholder="e.g. Human — 1700–1749",key="era_add_name")
            start=b.number_input("Start year",-10000,10000,1700,key="era_add_start")
            end=c1.number_input("End year",-10000,10000,1749,key="era_add_end")
            species=d.text_input("Species / occult","Human",key="era_add_species")
            template_opts=["None"]+([f"{r.era_id} — {r.era_name}" for _,r in eras.iterrows()] if not eras.empty else [])
            template=st.selectbox("Copy roll values from",template_opts,key="era_add_template")
            notes=st.text_input("Notes",key="era_add_notes")
            if st.button("Create era",type="primary",key="era_add_btn"):
                if end<start:
                    st.error("End year must be on or after start year.")
                elif not era_name.strip():
                    st.error("Give the era a name.")
                else:
                    con=connect(); era_rules.ensure_schema(con)
                    eid=era_rules.next_era_id(con)
                    con.execute("""INSERT INTO roll_rule_eras(era_id,era_name,start_year,end_year,species,active,notes)
                                   VALUES(?,?,?,?,?,1,?)""",
                                (eid,era_name.strip(),int(start),int(end),species.strip() or "Human",notes or None))
                    if template!="None":
                        src=template.split(" — ",1)[0]
                        vals=con.execute("SELECT roll_type,die,bad_results,notes FROM roll_rule_values WHERE era_id=?",(src,)).fetchall()
                        for v in vals:
                            con.execute("""INSERT OR REPLACE INTO roll_rule_values(era_id,roll_type,die,bad_results,notes)
                                           VALUES(?,?,?,?,?)""",(eid,v["roll_type"],v["die"],v["bad_results"],v["notes"]))
                    con.commit(); con.close()
                    st.success(f"Created {eid}. It is ready for editing below.")

        eras=q("""SELECT era_id,era_name,start_year,end_year,species,active,notes
                  FROM roll_rule_eras ORDER BY species,start_year,end_year""")
        if not eras.empty:
            labels=[f"{r.era_id} — {r.era_name} ({r.start_year}–{r.end_year}, {r.species})" for _,r in eras.iterrows()]
            selected=st.selectbox("Edit roll table",labels,key="era_edit_select")
            eid=selected.split(" — ",1)[0]
            erow=eras[eras.era_id==eid].iloc[0].to_dict()

            a,b,c1,d=st.columns(4)
            ename=a.text_input("Name",value=erow.get("era_name") or "",key="era_edit_name")
            estart=b.number_input("Start year",-10000,10000,int(erow.get("start_year")),key="era_edit_start")
            eend=c1.number_input("End year",-10000,10000,int(erow.get("end_year")),key="era_edit_end")
            especies=d.text_input("Species / occult",value=erow.get("species") or "Human",key="era_edit_species")
            a,b=st.columns([1,3])
            eactive=a.checkbox("Active",value=bool(erow.get("active")),key="era_edit_active")
            enotes=b.text_input("Era notes",value=erow.get("notes") or "",key="era_edit_notes")
            if st.button("Save era settings",key="era_edit_meta_save"):
                if eend<estart:
                    st.error("End year must be on or after start year.")
                else:
                    con=connect()
                    con.execute("""UPDATE roll_rule_eras SET era_name=?,start_year=?,end_year=?,species=?,active=?,notes=?
                                   WHERE era_id=?""",
                                (ename,int(estart),int(eend),especies or "Human",1 if eactive else 0,enotes or None,eid))
                    con.commit(); con.close(); st.success("Era settings saved.")

            # Required types are driven by the actual aging config plus maternal categories;
            # include any extra custom rows already stored for this era.
            con=connect()
            req=[r[0] for r in con.execute("""SELECT DISTINCT col_d FROM rules
                                             WHERE section='AGING & REQUIRED ROLLS'
                                               AND col_d IS NOT NULL AND lower(col_d)<>'none'""").fetchall()]
            req += ["Maternal — Preteen","Maternal — Teen","Maternal — Young Adult","Maternal — Adult","Maternal — Elder"]
            existing=con.execute("SELECT roll_type,die,bad_results,notes FROM roll_rule_values WHERE era_id=?",(eid,)).fetchall()
            existing_map={r["roll_type"]:(r["die"],r["bad_results"],r["notes"]) for r in existing}
            con.close()
            all_types=[]
            for x in req+list(existing_map):
                if x and x not in all_types: all_types.append(x)
            rows=[]
            for rt in all_types:
                vals=existing_map.get(rt,(None,None,None))
                rows.append({"roll_type":rt,"die":vals[0] or "","bad_results":vals[1] or "","notes":vals[2] or ""})
            editor=pd.DataFrame(rows)
            st.caption("Edit the die and bad-result values. You can also add custom roll-type rows at the bottom.")
            edited=st.data_editor(editor,num_rows="dynamic",use_container_width=True,hide_index=True,key=f"era_table_{eid}")
            if st.button("Save roll table",type="primary",key=f"era_table_save_{eid}"):
                con=connect()
                for _,r in edited.iterrows():
                    rt=str(r.get("roll_type") or "").strip()
                    if not rt: continue
                    con.execute("""INSERT OR REPLACE INTO roll_rule_values(era_id,roll_type,die,bad_results,notes)
                                   VALUES(?,?,?,?,?)""",
                                (eid,rt,str(r.get("die") or "").strip() or None,
                                 str(r.get("bad_results") or "").strip() or None,
                                 str(r.get("notes") or "").strip() or None))
                con.commit(); con.close()
                sync_auto_rolls(show_notice=False)
                st.success("Roll table saved and automatic schedule refreshed.")

            # Coverage diagnostics
            st.subheader("Coverage check")
            con=connect()
            obligations=autorolls.preview(con,current_gd())
            con.close()
            odf=pd.DataFrame(obligations)
            if not odf.empty:
                bad=odf[odf.rule_status!="Ready"]
                if bad.empty:
                    st.success("All currently known obligations have a matching complete roll table.")
                else:
                    st.warning(f"{len(bad):,} known obligation(s) currently lack a complete matching roll rule.")
                    st.dataframe(bad[["due_global_day","year","species","roll_type","sim_id","sim_name","rule_status"]].drop_duplicates(),
                                 use_container_width=True,hide_index=True,height=300)

    if rules_section=="Data & Backup":
        st.subheader("Migration inventory")
        counts={t:scalar(f"SELECT COUNT(*) FROM {t}") for t in ['sims','households','pregnancies','rolls','relationships','events','event_results','raw_import_rows']}
        st.json(counts)
        st.success("Every non-empty row from the source workbooks is also retained in raw_import_rows as a lossless archive.")
        st.download_button("Download database backup",save_manager.export_database_bytes(save_manager.active_save_id()),file_name=f"{save_manager.active_save()['name'].replace(' ','_')}.db",mime='application/octet-stream')


# Only the selected page renderer is invoked. Native Streamlit fragments keep
# subsequent page interactions inside that renderer instead of restarting the
# workspace gate, save selector, sidebar, migrations, and detection pipeline.
PAGE_RENDERERS={
    "Today":render_today,
    "Game Clock Sync":render_game_clock_sync,
    "Play Planner":render_play_planner,
    "Sims":render_sims,
    "Family Tree":render_family_tree,
    "Timeline":render_timeline,
    "Pregnancies":render_pregnancies,
    "Rolls":render_rolls,
    "Relationships":render_relationships,
    "Households":render_households,
    "Challenge Management":render_challenge_management,
    "Illnesses":render_illnesses,
    "Events":render_events,
    "Challenge Guides":render_challenge_guides,
    "Statistics":render_statistics,
    "Notes":render_notes,
    "Planting Reference":render_planting_reference,
    "Saves":render_saves,
    "Rules Health":render_rules_health,
    "Rules & Data":render_rules_and_data,
}
PAGE_RENDERERS[page]()
