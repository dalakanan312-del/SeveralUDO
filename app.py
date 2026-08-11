import tempfile
import base64
from datetime import time
from pathlib import Path
import pandas as pd
import streamlit as st
import plotly.express as px
import networkx as nx
from pyvis.network import Network
from db import connect,setting,set_setting,next_id
from calendar_utils import global_day_to_year_day,global_day_label,date_to_global_day,global_day_time_to_date,format_exact_date
import stats_engine as se
import autorolls
import era_rules
import timeline_engine
import plotly.graph_objects as go
import profiles
import save_manager
import storage
import neon_ui
import admin_ops
import workspace_access
import relationship_photos
import marriage_ai
from app_version import APP_VERSION

st.set_page_config(page_title="Decades Tracker",page_icon="🏰",layout="wide")
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
.block-container{padding-top:1.1rem;max-width:1450px;padding-bottom:3rem}
[data-testid="stSidebar"]{min-width:245px}
[data-testid="stMetric"]{
    border:1px solid rgba(128,128,128,.22);
    border-radius:14px;
    padding:12px 14px;
    background:rgba(128,128,128,.045)
}
div[data-testid="stExpander"]{border-radius:12px}
.stButton>button{border-radius:10px}
.stTextInput input,.stNumberInput input,.stSelectbox div[data-baseweb="select"]>div{border-radius:10px}
h1{margin-bottom:.15rem}
.page-subtitle{opacity:.72;margin-top:-.15rem;margin-bottom:1rem}
.section-note{opacity:.72;font-size:.92rem}
.pill{
    display:inline-block;padding:.2rem .55rem;border-radius:999px;
    border:1px solid rgba(128,128,128,.25);margin-right:.3rem;font-size:.88rem
}
</style>
""",unsafe_allow_html=True)

def q(sql,params=()):
    c=connect(); df=pd.read_sql_query(sql,c,params=params); c.close(); return df
def scalar(sql,params=(),default=0):
    c=connect(); r=c.execute(sql,params).fetchone(); c.close(); return r[0] if r and r[0] is not None else default
def sim_options(blank=True):
    df=q("SELECT sim_id,COALESCE(title,'') title,COALESCE(first_name,'') first_name,COALESCE(last_name,'') last_name FROM sims ORDER BY last_name,first_name")
    out=[f"{r.sim_id} — {' '.join(x for x in [r.title,r.first_name,r.last_name] if x).strip()}" for _,r in df.iterrows()]
    return ([""] if blank else [])+out
def sid(v): return v.split(" — ",1)[0] if v else None
def opt_index(opts,s):
    if not s:return 0
    for i,o in enumerate(opts):
        if o.startswith(str(s)+" —"): return i
    return 0
def int_or_none(v):
    try:return int(str(v).strip()) if str(v).strip() else None
    except:return None
def calendar_settings():
    c=connect()
    sy=int(float(setting(c,'start_year',1200)))
    dpy=int(float(setting(c,'days_per_year',4)))
    c.close()
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
    c=connect(); g=int(float(setting(c,'current_global_day',332))); c.close(); return g

def sync_auto_rolls(show_notice=False):
    """Idempotently create any missing rule-driven roll obligations."""
    con=connect()
    try:
        result=autorolls.sync_rolls(con,current_gd())
    finally:
        con.close()
    if show_notice:
        if result["added"]:
            st.success(f"Auto-scheduled {result['added']} missing roll(s).")
        else:
            st.info("Roll schedule is already up to date.")
        if result.get("missing_rule_rows"):
            st.warning(f"{result['missing_rule_rows']} due roll(s) were scheduled without die/bad-result values because that year/species does not yet have a complete roll table.")
    return result

def rule_value(label,default=None):
    c=connect(); r=c.execute("SELECT col_b FROM rules WHERE row_label=? ORDER BY source_row DESC LIMIT 1",(label,)).fetchone(); c.close()
    return r[0] if r and r[0] not in (None,'') else default

def page_header(title,subtitle=None):
    st.title(title)
    if subtitle:
        st.markdown(f"<div class='page-subtitle'>{subtitle}</div>",unsafe_allow_html=True)

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

# Lightweight schema migrations for optional app features.
_schema_con=connect()
profiles.ensure_schema(_schema_con)
relationship_photos.ensure_schema(_schema_con)
_schema_con.close()

with st.sidebar:
    st.title("🏰 Decades")
    st.caption("Challenge Tracker")

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
    nav_labels={
        "🏠 Today":"Today",
        "👤 Sims":"Sims",
        "🌳 Family Tree":"Family Tree",
        "🕰️ Timeline":"Timeline",
        "🤰 Pregnancies":"Pregnancies",
        "🎲 Rolls":"Rolls",
        "💍 Relationships":"Relationships",
        "🏘️ Households":"Households",
        "📜 Events":"Events",
        "📊 Statistics":"Statistics",
        "💾 Saves":"Saves",
        "⚙️ Rules & Data":"Rules & Data",
    }
    nav=st.radio("Navigate",list(nav_labels),label_visibility="collapsed")
    page=nav_labels[nav]
    st.divider()
    cg_sidebar=current_gd()
    cy_sidebar,cd_sidebar=challenge_year_day(cg_sidebar)
    st.metric("Global Day",cg_sidebar)
    st.caption(f"Year {cy_sidebar} • Challenge Day {cd_sidebar} • {sim_weekday(cg_sidebar)}")
    st.caption("✓ Automatic roll scheduling on")
    st.caption(f"Decades Tracker v{APP_VERSION}")
    if st.button("Lock private workspace",use_container_width=True):
        workspace_access.sign_out(st)

if page=="Today":
    page_header("Today","Your play-session dashboard: advance time, handle what is due, and see what comes next.")
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
            sync_auto_rolls(show_notice=False)
            st.success("Time advanced and the automatic roll schedule was refreshed.")
            st.rerun()

    # Current household/heir are useful but secondary.
    with st.expander("Current household & heir"):
        cdb=connect(); heir=setting(cdb,'current_heir_id','SIM-0181'); hh=setting(cdb,'main_household_id','HH-0035'); cdb.close()
        opts=sim_options(); hhs=q("SELECT household_id,household_name FROM households ORDER BY household_name,household_id")
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

    due=q("""SELECT roll_id,due_global_day,sim_id,sim_name,roll_type,die,bad_results,actual_roll,outcome
             FROM rolls WHERE COALESCE(completed,0)=0 AND due_global_day<=?
             ORDER BY due_global_day,sim_name,roll_type""",(g,))
    preg=q("""SELECT pregnancy_id,mother_id,mother_name,father_name,conception_global_day,due_global_day,
                     babies_expected,status,outcome
              FROM pregnancies WHERE due_global_day<=?
                AND COALESCE(status,'') NOT IN ('Delivered','Cancelled','Complete','Miscarriage','Stillbirth')
              ORDER BY due_global_day,mother_name""",(g,))
    active=q("""SELECT event_id,event_name,scope,location,roll_required,affected_class
                FROM events WHERE start_global_day<=? AND end_global_day>=?
                ORDER BY event_name""",(g,g))

    st.subheader("Needs attention")
    a,b,c=st.columns(3)
    a.metric("Rolls due",len(due))
    b.metric("Pregnancies due",len(preg))
    c.metric("Active historical events",len(active))

    task_rolls,task_preg,task_events=st.tabs([
        f"🎲 Rolls due ({len(due)})",
        f"🤰 Pregnancies due ({len(preg)})",
        f"📜 Active events ({len(active)})"
    ])

    with task_rolls:
        if due.empty:
            st.success("No rolls are due right now.")
        else:
            pretty=friendly_df(due,
                rename={"due_global_day":"Due GD","sim_name":"Sim","roll_type":"Roll","die":"Die","bad_results":"Bad results"},
                cols=["due_global_day","sim_name","roll_type","die","bad_results"])
            st.dataframe(pretty,use_container_width=True,hide_index=True,height=min(420,80+len(pretty)*35))
            st.markdown("**Record a result**")
            labels=[f"{r.roll_id} — {r.sim_name or r.sim_id} — {r.roll_type} (GD {r.due_global_day})" for _,r in due.iterrows()]
            pick=st.selectbox("Choose roll",labels,key="today_roll_pick")
            rid=pick.split(" — ",1)[0]
            rr=due[due.roll_id==rid].iloc[0]
            a,b=st.columns(2)
            actual=a.text_input("Actual roll",key="today_roll_actual")
            outcome=b.text_input("Outcome",key="today_roll_outcome")
            if st.button("Save & complete roll",type="primary",key="today_roll_save",use_container_width=True):
                con=connect()
                con.execute("UPDATE rolls SET actual_roll=?,outcome=?,completed=1,completed_global_day=? WHERE roll_id=?",
                            (actual or None,outcome or None,g,rid))
                con.commit(); con.close()
                st.success(f"Completed {rid}.")
                st.rerun()

    with task_preg:
        if preg.empty:
            st.success("No pregnancies are due right now.")
        else:
            pretty=friendly_df(preg,
                rename={"mother_name":"Mother","father_name":"Father","due_global_day":"Due GD","babies_expected":"Expected","status":"Status"},
                cols=["mother_name","father_name","due_global_day","babies_expected","status"])
            st.dataframe(pretty,use_container_width=True,hide_index=True,height=min(420,80+len(pretty)*35))
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
                con.execute("""UPDATE pregnancies SET status=?,babies_delivered=?,delivery_date=?,outcome=?,complication=?
                               WHERE pregnancy_id=?""",
                            (status,babies,gd_caption(g),outcome or None,complication or None,pid))
                con.commit(); con.close()
                sync_auto_rolls(show_notice=False)
                st.success(f"Updated {pid}.")
                st.rerun()

    with task_events:
        if active.empty:
            st.success("No historical events are active today.")
        else:
            pretty=friendly_df(active,
                rename={"event_name":"Event","scope":"Scope","location":"Location","affected_class":"Affected","roll_required":"Roll?"},
                cols=["event_name","scope","location","affected_class","roll_required"])
            st.dataframe(pretty,use_container_width=True,hide_index=True,height=min(420,80+len(pretty)*35))

    st.subheader("Coming up")
    a,b=st.columns([1,3])
    lookahead=a.selectbox("Look ahead",options=[4,8,12,20,40,80],index=3,format_func=lambda x:f"{x} Global Days",key="today_roll_lookahead")
    with b:
        st.caption("Future rolls stay as previews until they actually become due, so your Roll Log stays clean.")
    con=connect(); upcoming_rows=autorolls.upcoming(con,g,lookahead); con.close()
    if upcoming_rows:
        udf=pd.DataFrame(upcoming_rows)
        show=friendly_df(
            udf,
            rename={"due_global_day":"Due GD","year":"Year","sim_name":"Sim","roll_type":"Roll","die":"Die","bad_results":"Bad results","rule_status":"Rule status"},
            cols=["due_global_day","year","sim_name","roll_type","die","bad_results","rule_status"]
        )
        st.dataframe(show,use_container_width=True,hide_index=True,height=min(420,80+len(show)*35))
    else:
        st.info("Nothing automatically scheduled in this window.")

elif page=="Sims":
    page_header("Sims","Browse profiles, add people quickly, or edit family connections without touching raw IDs.")

    tab_directory,tab_add,tab_edit,tab_family=st.tabs([
        "👥 Directory","➕ Add Sim","✏️ Edit Sim","🌳 Family & Relationships"
    ])

    with tab_directory:
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
        sql+=" ORDER BY last_name,first_name,sim_id"
        df=q(sql,tuple(params))
        if not df.empty:
            df["Name"]=(df["title"].fillna("")+" "+df["first_name"].fillna("")+" "+df["last_name"].fillna("")+" "+df["suffix"].fillna("")).str.replace(r"\s+"," ",regex=True).str.strip()
            df["Status"]=df["death_global_day"].apply(lambda x:"Deceased" if pd.notna(x) else "Living")
            show=friendly_df(df,
                rename={"sim_id":"ID","sex":"Gender","generation":"Gen.","birth_global_day":"Birth GD",
                        "death_global_day":"Death GD","current_household_id":"Household","species_occult":"Species"},
                cols=["sim_id","Name","Status","sex","generation","birth_global_day","death_global_day","species_occult","current_household_id"])
            st.dataframe(show,use_container_width=True,hide_index=True,height=390)
            labels=[f"{r.Name} — {r.sim_id}" for _,r in df.iterrows()]
            profile_pick=st.selectbox("Open a Sim profile",labels,key="sim_dir_profile")
            profile_id=profile_pick.rsplit(" — ",1)[-1]
            rr=q("SELECT * FROM sims WHERE sim_id=?",(profile_id,))
            if not rr.empty:
                row=rr.iloc[0]
                con=connect(); photo=profiles.get_photo(con,profile_id); con.close()
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

    with tab_add:
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
            photo=c.file_uploader("Portrait (optional)",type=["png","jpg","jpeg","webp"],key="sim_add_photo",
                                  help="Stored inside the tracker database and included in database backups.")
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
                        con.commit(); con.close()
                        sync_auto_rolls(show_notice=False)
                        st.success(f"Added {first} {last} ({sim_id}).")

    with tab_edit:
        st.subheader("Edit a Sim")
        sopts=sim_options(blank=False)
        if not sopts:
            st.info("No Sims to edit.")
        else:
            selected_label=st.selectbox("Choose a Sim",sopts,key="sim_edit_select")
            selected=sid(selected_label)
            rr=q("SELECT * FROM sims WHERE sim_id=?",(selected,))
            row=rr.iloc[0].to_dict()
            con=connect(); current_photo=profiles.get_photo(con,selected); con.close()
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
            basic_tab,family_tab,life_tab,advanced_tab=st.tabs(["Basic info","Parents & household","Life events","Advanced"])
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
                photo=st.file_uploader("Replace portrait",type=["png","jpg","jpeg","webp"],key=f"sim_edit_photo_{selected}")
                delete_photo=st.checkbox("Remove current portrait when saving",value=False,key=f"sim_delete_photo_{selected}")
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
                st.success("Sim profile saved. Related names were updated automatically.")
                st.rerun()

            with st.expander("Delete this Sim",expanded=False):
                st.warning("This permanently deletes the Sim and cleans up linked records. Export a backup first if you may need to restore them.")
                con=connect()
                dependencies=admin_ops.sim_dependency_summary(con,selected)
                con.close()
                affected=[{"Linked data":label,"Count":count} for label,count in dependencies.items() if count]
                if affected:
                    st.dataframe(pd.DataFrame(affected),use_container_width=True,hide_index=True)
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

    with tab_family:
        st.subheader("Family & relationships")
        st.caption("Choose one Sim and manage their parents, children, and partnerships from one place.")
        sopts=sim_options(blank=False)
        if sopts:
            focal_label=st.selectbox("Sim",sopts,key="family_focal")
            focal=sid(focal_label)
            con=connect()
            frow=con.execute("SELECT * FROM sims WHERE sim_id=?",(focal,)).fetchone()
            photo=profiles.get_photo(con,focal)
            children=con.execute("""SELECT sim_id,TRIM(COALESCE(title,'')||' '||COALESCE(first_name,'')||' '||COALESCE(last_name,'')),
                                           mother_id,father_id,birth_global_day
                                    FROM sims WHERE mother_id=? OR father_id=? ORDER BY birth_global_day""",(focal,focal)).fetchall()
            rels=profiles.sim_relationships(con,focal)
            con.close()
            a,b=st.columns([1,4])
            with a:
                if photo: st.image(photo["image_data"],width=220)
                else: st.markdown("## 👤")
            with b:
                st.subheader(profiles.display_name(frow) or focal)
                st.caption(focal)
            parents_tab,children_tab,partners_tab=st.tabs(["Parents","Children","Partners / marriages"])
            with parents_tab:
                opts=sim_options()
                a,b=st.columns(2)
                mom=a.selectbox("Mother",opts,index=opt_index(opts,frow["mother_id"]),key=f"family_mom_{focal}")
                dad=b.selectbox("Father",opts,index=opt_index(opts,frow["father_id"]),key=f"family_dad_{focal}")
                if st.button("Save parents",type="primary",use_container_width=True,key=f"family_parent_save_{focal}"):
                    con=connect(); con.execute("UPDATE sims SET mother_id=?,father_id=? WHERE sim_id=?",(sid(mom),sid(dad),focal)); con.commit(); con.close()
                    st.success("Parents updated."); st.rerun()
            with children_tab:
                if children:
                    crows=[{"Sim ID":r[0],"Child":r[1],"Mother ID":r[2],"Father ID":r[3],"Birth GD":r[4]} for r in children]
                    st.dataframe(pd.DataFrame(crows),use_container_width=True,hide_index=True)
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
            with partners_tab:
                if rels:
                    rows=[]
                    for r in rels:
                        other_id=r["partner2_id"] if r["partner1_id"]==focal else r["partner1_id"]
                        other_name=r["partner2_name"] if r["partner1_id"]==focal else r["partner1_name"]
                        rows.append({"Relationship ID":r["relationship_id"],"Partner":other_name or other_id,"Type":r["type"],
                                     "Start GD":r["start_global_day"],"End GD":r["end_global_day"],"Status":r["status"]})
                    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
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

elif page=="Family Tree":
    page_header("Family Tree","Explore ancestors, descendants, spouses, and nearby family with a cleaner generation-based layout.")

    sims=q("""SELECT sim_id,COALESCE(title,'') title,first_name,last_name,suffix,mother_id,father_id,
                     birth_date,death_date,birth_global_day,death_global_day,generation
              FROM sims WHERE COALESCE(include_in_tree,1)=1""")
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
        show_portraits=st.checkbox("Show uploaded portraits",value=True,key="tree_portraits")
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

        net=Network(height="760px",width="100%",directed=True,bgcolor="#ffffff",font_color="#222222")
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
            con=connect(); profiles.ensure_schema(con)
            for node in sub.nodes:
                pr=profiles.get_photo(con,node)
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
                    st.dataframe(pd.DataFrame(partners),use_container_width=True,hide_index=True)
                else: st.caption("None recorded")

elif page=="Timeline":
    page_header("Timeline","Explore the challenge chronologically without digging through individual tables.")
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

        tab_events,tab_lifespans,tab_decades=st.tabs(["Chronology","Sim lifespans","Decade overview"])

        with tab_events:
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
                st.subheader("Chronological feed")
                feed=view.sort_values(["global_day","category","title"],ascending=[False,True,True])[
                    ["global_day","year","category","title","primary_sim","household_id","details","source_id"]
                ]
                st.dataframe(feed,use_container_width=True,hide_index=True,height=520)

        with tab_lifespans:
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
                st.dataframe(sdf,use_container_width=True,hide_index=True)
            else:
                st.caption("Choose one or more Sims to compare their lifespans.")

        with tab_decades:
            d=view.copy()
            if d.empty:
                st.info("No timeline items in this range.")
            else:
                summary=d.groupby(["decade","category"]).size().reset_index(name="events")
                fig=px.bar(summary,x="decade",y="events",color="category",title="Timeline activity by decade")
                st.plotly_chart(fig,use_container_width=True)
                pivot=summary.pivot_table(index="decade",columns="category",values="events",fill_value=0).reset_index()
                pivot["Total"]=pivot.drop(columns=["decade"]).sum(axis=1)
                st.dataframe(pivot,use_container_width=True,hide_index=True)

elif page=="Pregnancies":
    page_header("Pregnancies","Track active pregnancies, deliveries, and outcomes.")
    st.caption("Add pregnancies, record delivery/outcome details, or revise an existing record.")

    tab_browse,tab_add,tab_update=st.tabs(["Pregnancies","➕ Add","✅ Record outcome"])
    with tab_browse:
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
        show=friendly_df(pdf,
            rename={"pregnancy_id":"ID","mother_name":"Mother","father_name":"Father","conception_global_day":"Conceived GD",
                    "due_global_day":"Due GD","babies_expected":"Expected","babies_delivered":"Delivered","status":"Status",
                    "outcome":"Outcome","complication":"Complication"},
            cols=["pregnancy_id","mother_name","father_name","conception_global_day","due_global_day",
                  "babies_expected","babies_delivered","status","outcome","complication"])
        st.dataframe(show,use_container_width=True,hide_index=True,height=500)
        with st.expander("Show all pregnancy fields"):
            st.dataframe(pdf,use_container_width=True,hide_index=True,height=320)

    with tab_add:
        opts=sim_options()
        a,b,c=st.columns(3)
        mother=a.selectbox("Mother",opts,key="preg_add_mother")
        father=b.selectbox("Father",opts,key="preg_add_father")
        conception=c.number_input("Conception Global Day",min_value=-10000,max_value=20000,value=current_gd(),step=1,key="preg_add_conception")
        plen=int(float(rule_value('Pregnancy Length (challenge days)',3)))
        due=conception+plen
        st.info(f"Due Global Day **{due}** — {gd_caption(due)}")
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

    with tab_update:
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
                con.commit(); con.close()
                sync_auto_rolls(show_notice=False)
                st.success(f"Saved outcome for {pid}; maternal roll schedule refreshed.")


elif page=="Rolls":
    page_header("Rolls","See what is due, record outcomes, and inspect the automatic schedule.")
    tab_browse,tab_result,tab_add,tab_auto=st.tabs(["Roll log","✅ Record result","➕ Add manually","🗓️ Automatic schedule"])
    with tab_browse:
        a,b=st.columns([1,1])
        only_open=a.checkbox("Only incomplete",True,key="roll_browse_open")
        cutoff=b.number_input("Due through Global Day",min_value=-10000,max_value=20000,value=current_gd(),key="roll_browse_cutoff")
        sql="SELECT * FROM rolls WHERE due_global_day<=?"+(" AND COALESCE(completed,0)=0" if only_open else "")+" ORDER BY due_global_day,sim_name,roll_type"
        rdf=q(sql,(cutoff,))
        overdue=int((rdf.due_global_day<current_gd()).sum()) if not rdf.empty and "due_global_day" in rdf else 0
        a,b,c=st.columns(3)
        a.metric("Shown",len(rdf)); b.metric("Overdue",overdue); c.metric("Due today",int((rdf.due_global_day==current_gd()).sum()) if not rdf.empty else 0)
        show=friendly_df(rdf,
            rename={"due_global_day":"Due GD","sim_name":"Sim","roll_type":"Roll","die":"Die","bad_results":"Bad results",
                    "actual_roll":"Actual","outcome":"Outcome","completed":"Done"},
            cols=["due_global_day","sim_name","roll_type","die","bad_results","actual_roll","outcome","completed"])
        st.dataframe(show,use_container_width=True,hide_index=True,height=520)
        with st.expander("Show IDs and technical roll fields"):
            st.dataframe(rdf,use_container_width=True,hide_index=True,height=320)

    with tab_result:
        rdf=q("SELECT * FROM rolls ORDER BY due_global_day DESC,roll_id")
        if rdf.empty:
            st.info("No rolls recorded.")
        else:
            labels=[f"{r.roll_id} — GD {int(r.due_global_day) if pd.notna(r.due_global_day) else '?'} — {r.sim_name or r.sim_id or ''} — {r.roll_type or ''}" for _,r in rdf.iterrows()]
            choice=st.selectbox("Roll",labels,key="roll_edit_select")
            rid=choice.split(" — ",1)[0]
            row=q("SELECT * FROM rolls WHERE roll_id=?",(rid,)).iloc[0].to_dict()
            a,b,c=st.columns(3)
            actual=a.text_input("Actual roll",value=str(row.get("actual_roll") or ""),key="roll_edit_actual")
            outcome=b.text_input("Outcome",value=row.get("outcome") or "",key="roll_edit_outcome")
            completed_day=c.number_input("Completed Global Day",-10000,20000,int(row.get("completed_global_day") or current_gd()),key="roll_edit_day")
            a,b=st.columns(2)
            completed=a.checkbox("Completed",value=bool(row.get("completed") or 0),key="roll_edit_completed")
            notes=b.text_input("Notes",value=row.get("notes") or "",key="roll_edit_notes")
            if st.button("Save roll",type="primary",key="roll_edit_save"):
                con=connect()
                con.execute("""UPDATE rolls SET actual_roll=?,outcome=?,completed=?,completed_global_day=?,notes=?
                               WHERE roll_id=?""",
                            (actual or None,outcome or None,1 if completed else 0,completed_day if completed else None,notes or None,rid))
                con.commit(); con.close(); st.success(f"Saved {rid}")

    with tab_add:
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



    with tab_auto:
        st.subheader("Automatic roll schedule")
        st.caption("Milestone timing comes from Rules Config; die and bad-result values come from the matching year/species roll table. Due rolls are inserted automatically and future rolls stay as previews.")
        a,b,c=st.columns(3)
        a.metric("Current Global Day",current_gd())
        ccon=connect()
        tracking=setting(ccon,"roll_tracking_start","339")
        ccon.close()
        b.metric("Automatic tracking begins",tracking)
        c.metric("Rules source","Era-aware tables")
        if st.button("Refresh automatic rolls now",type="primary",key="auto_roll_refresh"):
            sync_auto_rolls(show_notice=True)

        horizon=st.slider("Preview the next Global Days",1,240,40,key="auto_roll_horizon")
        con=connect()
        rows=autorolls.upcoming(con,current_gd(),horizon)
        con.close()
        if rows:
            adf=pd.DataFrame(rows)
            missing=adf[adf["missing"]>0].copy()
            st.metric("Upcoming obligations",len(adf),f"{len(missing)} future roll(s) not inserted yet")
            st.dataframe(
                adf[["due_global_day","year","species","era_name","rule_status","sim_id","sim_name","roll_type","die","bad_results","source_id","kind","already_scheduled","missing"]],
                use_container_width=True,hide_index=True,height=480
            )
            if not missing.empty:
                st.caption("Future rows marked missing are intentional: they will be created automatically when their Global Day arrives.")
        else:
            st.info("No automatic obligations in this preview window.")

        st.markdown("**Automatically supported from your current rules:**")
        st.write("Being Born, Newborn, Infant, Toddler, Child, Preteen, Teen, Young Adult, Adult, Elder Death-Age RNG, and maternal follow-up rolls.")
        st.caption("Add or edit later-year and occult/species roll tables under Rules & Data → Roll Tables. The scheduler automatically selects the matching table by historical year and species.")


elif page=="Relationships":
    page_header("Relationships","Browse partnerships by name, see both people together, and add or end relationships without editing spouse IDs.")

    tab_browse,tab_add,tab_edit=st.tabs(["💞 Browse","➕ Add relationship","✏️ Edit / end"])

    with tab_browse:
        rdf=q("SELECT * FROM relationships ORDER BY start_global_day DESC,relationship_id")
        active=int((rdf.status.fillna("").str.lower()=="active").sum()) if not rdf.empty else 0
        a,b,c=st.columns(3)
        a.metric("Relationships",len(rdf)); b.metric("Active",active); c.metric("Ended",len(rdf)-active)

        search=st.text_input("Find a person",key="rel_browse_search",placeholder="Type either partner's name…")
        view=rdf.copy()
        if search and not view.empty:
            s=search.lower()
            view=view[
                view.partner1_name.fillna("").str.lower().str.contains(s,regex=False)
                |view.partner2_name.fillna("").str.lower().str.contains(s,regex=False)
                |view.partner1_id.fillna("").str.lower().str.contains(s,regex=False)
                |view.partner2_id.fillna("").str.lower().str.contains(s,regex=False)
            ]
        show=friendly_df(view,
            rename={"partner1_name":"Partner 1","partner2_name":"Partner 2","type":"Type","start_global_day":"Start GD",
                    "end_global_day":"End GD","status":"Status","location":"Location","children_count":"Children"},
            cols=["partner1_name","partner2_name","type","start_global_day","end_global_day","status","location","children_count"])
        st.dataframe(show,use_container_width=True,hide_index=True,height=390)

        if not view.empty:
            labels=[f"{r.partner1_name or r.partner1_id} + {r.partner2_name or r.partner2_id} — {r.type or 'Relationship'} — {r.relationship_id}"
                    for _,r in view.iterrows()]
            pick=st.selectbox("Open relationship",labels,key="rel_browse_pick")
            rid=pick.rsplit(" — ",1)[-1]
            row=q("SELECT * FROM relationships WHERE relationship_id=?",(rid,)).iloc[0].to_dict()
            con=connect()
            p1photo=profiles.get_photo(con,row.get("partner1_id")) if row.get("partner1_id") else None
            p2photo=profiles.get_photo(con,row.get("partner2_id")) if row.get("partner2_id") else None
            marriage_photo=relationship_photos.get_photo(con,rid)
            con.close()
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

    with tab_add:
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

    with tab_edit:
        rdf=q("SELECT * FROM relationships ORDER BY start_global_day DESC,relationship_id")
        if rdf.empty:
            st.info("No relationships to edit.")
        else:
            labels=[f"{r.partner1_name or r.partner1_id} + {r.partner2_name or r.partner2_id} — {r.type or 'Relationship'} — {r.relationship_id}"
                    for _,r in rdf.iterrows()]
            choice=st.selectbox("Choose relationship",labels,key="rel_edit_select")
            rid=choice.rsplit(" — ",1)[-1]
            row=q("SELECT * FROM relationships WHERE relationship_id=?",(rid,)).iloc[0].to_dict()
            con=connect()
            current_marriage_photo=relationship_photos.get_photo(con,rid)
            partner1_photo=profiles.get_photo(con,row.get("partner1_id")) if row.get("partner1_id") else None
            partner2_photo=profiles.get_photo(con,row.get("partner2_id")) if row.get("partner2_id") else None
            con.close()
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

elif page=="Households":
    page_header("Households","Create households, view members, move Sims, and edit household details.")
    tab_browse,tab_create,tab_assign,tab_edit=st.tabs(["Households","Create household","🚚 Move a Sim","✏️ Edit household"])

    with tab_browse:
        hdf=q("SELECT * FROM households ORDER BY household_name,household_id")
        a,b,c=st.columns(3)
        a.metric("Households",len(hdf))
        b.metric("Active",int(hdf.active.fillna(0).astype(bool).sum()) if not hdf.empty else 0)
        c.metric("Living members",scalar("SELECT COUNT(*) FROM sims WHERE death_global_day IS NULL AND current_household_id IS NOT NULL"))
        show=friendly_df(hdf,
            rename={"household_name":"Household","location":"Location","social_class":"Class","living_members":"Living",
                    "total_assigned_members":"Associated","active":"Active","head_sim_id":"Head ID"},
            cols=["household_id","household_name","location","social_class","living_members","total_assigned_members","active"])
        st.dataframe(show,use_container_width=True,hide_index=True,height=400)
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
                st.dataframe(friendly_df(mem,rename={"sim_id":"ID","birth_global_day":"Birth GD","death_global_day":"Death GD"},
                                         cols=["sim_id","Name","Status","birth_global_day","death_global_day"]),
                             use_container_width=True,hide_index=True)

    with tab_create:
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

    with tab_assign:
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

    with tab_edit:
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


elif page=="Events":
    page_header("Historical Events","Manage challenge-wide events and record their effects.")
    g=current_gd()
    st.caption(f"Current Global Day: {g} — {gd_caption(g)}")
    tab_browse,tab_add,tab_result,tab_edit=st.tabs(["Events","➕ Add","✅ Record effect","✏️ Edit"])

    with tab_browse:
        edf=q("SELECT * FROM events ORDER BY start_global_day,event_name")
        active_now=edf[(edf.start_global_day<=g)&(edf.end_global_day>=g)] if not edf.empty else edf
        a,b,c=st.columns(3)
        a.metric("Historical events",len(edf)); b.metric("Active today",len(active_now)); c.metric("Recorded effects",scalar("SELECT COUNT(*) FROM event_results"))
        show=friendly_df(edf,
            rename={"event_name":"Event","start_global_day":"Start GD","end_global_day":"End GD","scope":"Scope",
                    "location":"Location","affected_class":"Affected","roll_required":"Roll?","active":"Enabled"},
            cols=["event_name","start_global_day","end_global_day","scope","location","affected_class","roll_required","active"])
        st.dataframe(show,use_container_width=True,hide_index=True,height=420)
        results=q("SELECT * FROM event_results ORDER BY global_day DESC,result_id")
        if not results.empty:
            st.subheader("Recorded effects")
            rshow=friendly_df(results,
                rename={"global_day":"Global Day","sim_id":"Sim ID","household_id":"Household","outcome":"Outcome",
                        "status":"Status","cause_effect":"Cause / effect","death":"Death?"},
                cols=["global_day","sim_id","household_id","outcome","status","cause_effect","death"])
            st.dataframe(rshow,use_container_width=True,hide_index=True,height=350)
        with st.expander("Show event IDs and technical fields"):
            st.dataframe(edf,use_container_width=True,hide_index=True,height=280)

    with tab_add:
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

    with tab_result:
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

    with tab_edit:
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


elif page=="Statistics":
    page_header("Statistics","Detailed analytics, family records, demographic trends, and challenge records.")
    st.caption("Live analytics calculated from the SQLite database. Global Day is the canonical time coordinate.")

    c=connect()
    cg=current_gd()
    sy=int(float(setting(c,"start_year",1200)))
    ctx=se.prepare(c,cg,sy)
    c.close()

    sims=ctx["sims"]; living=sims[sims.living].copy(); deceased=sims[sims.death_global_day.notna()].copy()
    born_to_date=sims[sims.birth_global_day.notna() & (sims.birth_global_day<=cg)].copy()
    deceased_to_date=sims[sims.death_global_day.notna() & (sims.death_global_day<=cg)].copy()
    not_yet_born=sims[sims.birth_global_day.notna() & (sims.birth_global_day>cg)].copy()
    yearly=se.population_yearly(ctx); decades=se.decade_summary(ctx)
    births,parent_counts,mothers,fathers,sibling_gaps=se.births_stats(ctx)
    siblings,full_groups=se.sibling_table(ctx)
    lineage=se.lineage_table(ctx)
    rel,marr_counts=se.relationship_stats(ctx)
    hhstats=se.household_stats(ctx)
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

    tabs=st.tabs([
        "Overview","Population","Births & Fertility","Mortality","Age & Longevity",
        "Generations & Dynasty","Family Structure","Gender","Households","Relationships",
        "Decades","Events","Individual Sims","Records"
    ])

    with tabs[0]:
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

    with tabs[1]:
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

    with tabs[2]:
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

    with tabs[3]:
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

    with tabs[4]:
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

    with tabs[5]:
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

    with tabs[6]:
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

    with tabs[7]:
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

    with tabs[8]:
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

    with tabs[9]:
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

    with tabs[10]:
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

    with tabs[11]:
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

    with tabs[12]:
        st.subheader("Individual Sim statistics")
        opts=sim_options(blank=False)
        sel=st.selectbox("Sim",opts,key="stats_sim")
        ss=sid(sel)
        prof=se.individual_profile(ctx,ss)
        if prof:
            con=connect(); stats_photo=profiles.get_photo(con,ss); con.close()
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

    with tabs[13]:
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


elif page=="Saves":
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
    st.dataframe(pd.DataFrame(summary_rows),use_container_width=True,hide_index=True)

    create_tab,duplicate_tab,manage_tab,import_tab=st.tabs([
        "➕ New blank save","📑 Duplicate save","✏️ Manage saves","📥 Import save"
    ])

    with create_tab:
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

    with duplicate_tab:
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

    with manage_tab:
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

    with import_tab:
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

elif page=="Rules & Data":
    page_header("Rules & Data","Configure era roll tables, inspect imported rules, and back up your database.")
    c=connect()
    era_rules.ensure_schema(c)
    c.close()

    tab_rules,tab_tables,tab_data=st.tabs(["Imported Rules","Roll Tables","Data & Backup"])

    with tab_rules:
        st.subheader("Imported challenge rules")
        st.dataframe(q("SELECT section,row_label,col_b,col_c,col_d,col_e FROM rules ORDER BY source_row"),
                     use_container_width=True,hide_index=True,height=520)

    with tab_tables:
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

    with tab_data:
        st.subheader("Migration inventory")
        counts={t:scalar(f"SELECT COUNT(*) FROM {t}") for t in ['sims','households','pregnancies','rolls','relationships','events','event_results','raw_import_rows']}
        st.json(counts)
        st.success("Every non-empty row from the source workbooks is also retained in raw_import_rows as a lossless archive.")
        st.download_button("Download database backup",save_manager.export_database_bytes(save_manager.active_save_id()),file_name=f"{save_manager.active_save()['name'].replace(' ','_')}.db",mime='application/octet-stream')
