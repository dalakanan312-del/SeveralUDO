from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import autorolls


DDL = [
    """CREATE TABLE IF NOT EXISTS event_rule_configs(
        event_id TEXT PRIMARY KEY,die TEXT,bad_results TEXT,eligibility TEXT,
        min_age_days INTEGER,max_age_days INTEGER,eligible_sexes TEXT,frequency TEXT,
        followup_die TEXT,followup_results TEXT,effects_json TEXT,updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS action_queue(
        action_id TEXT PRIMARY KEY,source_type TEXT NOT NULL,source_id TEXT,roll_id TEXT UNIQUE,
        sim_id TEXT,household_id TEXT,due_global_day INTEGER,title TEXT,category TEXT,status TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 100,payload_json TEXT,created_at TEXT,updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS maintenance_jobs(
        job_key TEXT PRIMARY KEY,status TEXT,last_run_at TEXT,summary TEXT)""",
]


def ensure_schema(con):
    for ddl in DDL:
        con.execute(ddl)
    con.execute("CREATE INDEX IF NOT EXISTS action_queue_status_due_idx ON action_queue(status,due_global_day,priority)")
    con.execute("CREATE INDEX IF NOT EXISTS action_queue_source_idx ON action_queue(source_type,source_id)")
    con.commit()


def _now():
    return datetime.now(timezone.utc).isoformat()


def sync(con, current_gd):
    """Incrementally mirror outstanding rolls; no historical recalculation."""
    ensure_schema(con)
    con.execute("""INSERT INTO action_queue(action_id,source_type,source_id,roll_id,sim_id,due_global_day,
                   title,category,status,priority,payload_json,created_at,updated_at)
                   SELECT 'ACT-'||r.roll_id,'roll',r.source_id,r.roll_id,r.sim_id,r.due_global_day,
                          COALESCE(r.roll_type,'Scheduled roll'),
                          CASE WHEN LOWER(COALESCE(r.roll_type,'')) LIKE 'event%%' THEN 'Event'
                               WHEN LOWER(COALESCE(r.roll_type,'')) LIKE 'maternal%%' THEN 'Pregnancy'
                               ELSE 'Aging' END,
                          'open',CASE WHEN r.due_global_day<? THEN 10 ELSE 50 END,'{}',?,?
                   FROM rolls r WHERE COALESCE(r.completed,0)=0
                   ON CONFLICT(action_id) DO UPDATE SET due_global_day=excluded.due_global_day,
                     title=excluded.title,status='open',priority=excluded.priority,updated_at=excluded.updated_at""",
                (int(current_gd),_now(),_now()))
    con.execute("""UPDATE action_queue SET status='complete',updated_at=? WHERE source_type='roll'
                   AND roll_id IN (SELECT roll_id FROM rolls WHERE COALESCE(completed,0)=1)""",(_now(),))
    con.execute("""DELETE FROM action_queue WHERE source_type='roll'
                   AND roll_id NOT IN (SELECT roll_id FROM rolls)""")
    con.commit()


def seed_event_configs(con):
    ensure_schema(con)
    added=0
    for row in con.execute("SELECT event_id,notes,affected_class FROM events"):
        spec=autorolls.event_roll_spec(row["notes"])
        cursor=con.execute("""INSERT INTO event_rule_configs(event_id,die,bad_results,eligibility,frequency,updated_at)
                              VALUES(?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING""",
                           (row["event_id"],spec["die"],spec["bad_results"],row["affected_class"],
                            "once",_now()))
        added+=max(0,cursor.rowcount)
    con.commit()
    return added


def save_event_config(con,event_id,die,bad_results,eligibility,min_age,max_age,sexes,frequency,
                      followup_die,followup_results,effects=None):
    con.execute("""INSERT INTO event_rule_configs(event_id,die,bad_results,eligibility,min_age_days,max_age_days,
                   eligible_sexes,frequency,followup_die,followup_results,effects_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET
                   die=excluded.die,bad_results=excluded.bad_results,eligibility=excluded.eligibility,
                   min_age_days=excluded.min_age_days,max_age_days=excluded.max_age_days,
                   eligible_sexes=excluded.eligible_sexes,frequency=excluded.frequency,
                   followup_die=excluded.followup_die,followup_results=excluded.followup_results,
                   effects_json=excluded.effects_json,updated_at=excluded.updated_at""",
                (event_id,die,bad_results,eligibility,min_age,max_age,sexes,frequency,followup_die,
                 followup_results,json.dumps(effects or {}),_now()))
    con.execute("UPDATE rolls SET die=?,bad_results=? WHERE source_id=? AND COALESCE(completed,0)=0",
                (die,bad_results,event_id))
    con.commit()


def validation(con,current_gd):
    checks=[]
    def add(kind,severity,count,detail):
        checks.append({"check":kind,"severity":severity,"count":int(count or 0),"detail":detail})
    add("Open rolls missing a die","Error",con.execute("SELECT COUNT(*) FROM rolls WHERE COALESCE(completed,0)=0 AND NULLIF(TRIM(COALESCE(die,'')),'') IS NULL").fetchone()[0],"Configure a die before resolving these rolls.")
    add("Open rolls missing outcomes","Warning",con.execute("SELECT COUNT(*) FROM rolls WHERE COALESCE(completed,0)=0 AND NULLIF(TRIM(COALESCE(bad_results,'')),'') IS NULL").fetchone()[0],"No automatic outcome can be calculated.")
    add("Event rules needing review","Warning",con.execute("SELECT COUNT(*) FROM event_rule_configs WHERE NULLIF(TRIM(COALESCE(bad_results,'')),'') IS NULL").fetchone()[0],"The library prose did not contain a recognized numbered result.")
    add("Duplicate obligations","Error",con.execute("""SELECT COUNT(*) FROM (SELECT source_id,sim_id,due_global_day,roll_type FROM rolls GROUP BY source_id,sim_id,due_global_day,roll_type HAVING COUNT(*)>1) d""").fetchone()[0],"Duplicate groups are reported but not automatically deleted.")
    add("Open rolls after death","Error",con.execute("""SELECT COUNT(*) FROM rolls r JOIN sims s ON s.sim_id=r.sim_id WHERE COALESCE(r.completed,0)=0 AND s.death_global_day IS NOT NULL AND r.due_global_day>s.death_global_day""").fetchone()[0],"These can be removed with roll maintenance.")
    add("Reached roll events with no eligible roll","Warning",con.execute("""SELECT COUNT(*) FROM events e WHERE COALESCE(e.active,1)=1 AND COALESCE(e.roll_required,0)=1 AND e.start_global_day<=? AND NOT EXISTS(SELECT 1 FROM rolls r WHERE r.source_id=e.event_id)""",(current_gd,)).fetchone()[0],"Review event eligibility or run reconciliation.")
    return checks


def run_maintenance(con,current_gd,full=False):
    autorolls.sync_rolls(con,current_gd)
    seeded=seed_event_configs(con)
    sync(con,current_gd)
    summary=f"Queue synchronized; {seeded} structured event rules added."
    if full:
        repaired=autorolls.repair_generated_roll_dice(con)
        summary+=f" {repaired} generated roll rule rows repaired."
    con.execute("""INSERT INTO maintenance_jobs(job_key,status,last_run_at,summary) VALUES(?,?,?,?)
                   ON CONFLICT(job_key) DO UPDATE SET status=excluded.status,last_run_at=excluded.last_run_at,
                   summary=excluded.summary""",("full" if full else "incremental","complete",_now(),summary))
    con.commit()
    return summary
