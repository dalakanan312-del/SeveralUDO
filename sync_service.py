from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import psycopg
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from psycopg import sql

import action_queue
import autorolls
import clock_sync
import save_manager
from db import Connection


app=FastAPI(title="SeveralUDO Clock Sync",docs_url=None,redoc_url=None)


class ClockReport(BaseModel):
    game_day:int=Field(ge=0,le=1000000000)
    game_ticks:int|None=None
    mod_version:str|None=None
    game_hour:int|None=Field(default=None,ge=0,le=23)
    game_minute:int|None=Field(default=None,ge=0,le=59)
    household_name:str|None=Field(default=None,max_length=200)
    household_sims:list[dict]=Field(default_factory=list,max_length=100)


def database_url():
    value=os.environ.get("DATABASE_URL") or os.environ.get("NEON_DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required")
    return value


@app.on_event("startup")
def prepare_registry():
    """Apply the small public sync migration before the first mod report."""
    raw=psycopg.connect(database_url(),connect_timeout=10)
    try:
        import cloud_schema
        cloud_schema.create_registry(raw)
    finally:
        raw.close()


@app.get("/health")
def health():
    return {"ok":True}


@app.post("/v1/clock")
def receive_clock(report:ClockReport,authorization:str|None=Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401,"Missing sync token")
    digest=clock_sync.token_hash(authorization[7:].strip())
    raw=psycopg.connect(database_url(),connect_timeout=10)
    try:
        with raw.cursor() as cursor:
            cursor.execute(
                """SELECT owner_hash,save_id,schema_name,game_anchor_day,tracker_anchor_day,last_game_day,
                          COALESCE(members_initialized,FALSE)
                   FROM public.decades_clock_sync WHERE token_hash=%s AND enabled=TRUE FOR UPDATE""",
                (digest,),
            )
            link=cursor.fetchone()
            if not link:
                raise HTTPException(401,"Invalid or revoked sync token")
            owner_hash,save_id,schema_name,game_anchor,tracker_anchor,last_game,members_initialized=link
            cursor.execute(
                sql.SQL("SELECT value FROM {}.settings WHERE key='current_global_day'").format(sql.Identifier(schema_name))
            )
            current_row=cursor.fetchone()
            current=int(float(current_row[0])) if current_row else 1
            if game_anchor is None:
                game_anchor=int(report.game_day)
                tracker_anchor=current
                target=current
            else:
                target=int(tracker_anchor)+(int(report.game_day)-int(game_anchor))
                # Never rewind automatically after restoring an older game save.
                target=max(current,target)
            cursor.execute(
                """UPDATE public.decades_clock_sync SET game_anchor_day=%s,tracker_anchor_day=%s,
                   last_game_day=%s,last_tracker_day=%s,last_seen_at=now() WHERE token_hash=%s""",
                (game_anchor,tracker_anchor,report.game_day,target,digest),
            )
            cursor.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {}.game_birth_candidates(
                detection_id TEXT PRIMARY KEY,game_sim_id TEXT UNIQUE NOT NULL,first_name TEXT,last_name TEXT,
                sex TEXT,age_stage TEXT,is_baby INTEGER,game_day BIGINT,game_hour INTEGER,game_minute INTEGER,birth_global_day INTEGER,
                household_name TEXT,status TEXT NOT NULL DEFAULT 'pending',detected_at TEXT,resolved_at TEXT,
                created_sim_id TEXT)""").format(sql.Identifier(schema_name)))
            cursor.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {}.game_pregnancy_states(
                game_sim_id TEXT PRIMARY KEY,was_pregnant INTEGER NOT NULL DEFAULT 0,
                pregnancy_sequence INTEGER NOT NULL DEFAULT 0,updated_at TEXT)""").format(sql.Identifier(schema_name)))
            cursor.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {}.game_pregnancy_candidates(
                detection_id TEXT PRIMARY KEY,game_sim_id TEXT NOT NULL,pregnancy_sequence INTEGER NOT NULL,
                first_name TEXT,last_name TEXT,partner_game_sim_id TEXT,partner_first_name TEXT,partner_last_name TEXT,
                pregnancy_progress DOUBLE PRECISION,game_day BIGINT,game_hour INTEGER,game_minute INTEGER,
                conception_global_day INTEGER,due_global_day INTEGER,babies_expected INTEGER,household_name TEXT,
                status TEXT NOT NULL DEFAULT 'pending',detected_at TEXT,resolved_at TEXT,created_pregnancy_id TEXT,
                UNIQUE(game_sim_id,pregnancy_sequence))""").format(sql.Identifier(schema_name)))
            pregnancy_length=3
            cursor.execute(sql.SQL("""SELECT col_b FROM {}.rules
                WHERE LOWER(COALESCE(row_label,''))='pregnancy length (challenge days)'
                ORDER BY source_row DESC LIMIT 1""").format(sql.Identifier(schema_name)))
            pregnancy_length_row=cursor.fetchone()
            try:
                if pregnancy_length_row and pregnancy_length_row[0] not in (None,""):
                    pregnancy_length=max(1,int(float(pregnancy_length_row[0])))
            except (TypeError,ValueError):
                pregnancy_length=3
            pregnancy_candidates_added=0
            for member in report.household_sims[:100]:
                game_sim_id=str(member.get("game_sim_id") or "").strip()[:100]
                if not game_sim_id:
                    continue
                cursor.execute(
                    """INSERT INTO public.decades_clock_members(token_hash,game_sim_id) VALUES(%s,%s)
                       ON CONFLICT DO NOTHING RETURNING game_sim_id""",
                    (digest,game_sim_id),
                )
                is_new=cursor.fetchone() is not None
                if members_initialized and is_new:
                    age_stage=str(member.get("age_stage") or "Unknown")[:50]
                    stage_key=age_stage.lower().replace("age.","").replace("_"," ").strip()
                    stage_offsets={"baby":0,"newborn":0,"infant":1,"toddler":4,"child":20,
                                   "preteen":40,"teen":52,"young adult":72,"youngadult":72,
                                   "adult":160,"elder":240}
                    estimated_birth=target-stage_offsets.get(stage_key,0)
                    cursor.execute(
                        sql.SQL("""INSERT INTO {}.game_birth_candidates(
                            detection_id,game_sim_id,first_name,last_name,sex,age_stage,is_baby,game_day,game_hour,game_minute,
                            birth_global_day,household_name,status,detected_at)
                            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s)
                            ON CONFLICT(game_sim_id) DO NOTHING""").format(sql.Identifier(schema_name)),
                        ("SIM-"+uuid.uuid4().hex[:16].upper(),game_sim_id,
                         str(member.get("first_name") or "")[:100] or None,
                         str(member.get("last_name") or "")[:100] or None,
                         str(member.get("sex") or "")[:50] or None,age_stage,
                         1 if bool(member.get("is_baby")) else 0,report.game_day,
                         report.game_hour,report.game_minute,estimated_birth,
                         (report.household_name or "")[:200] or None,
                         datetime.now(timezone.utc).isoformat()),
                    )
                is_pregnant=bool(member.get("is_pregnant"))
                cursor.execute(sql.SQL("""SELECT was_pregnant,pregnancy_sequence
                    FROM {}.game_pregnancy_states WHERE game_sim_id=%s FOR UPDATE""").format(sql.Identifier(schema_name)),
                    (game_sim_id,))
                pregnancy_state=cursor.fetchone()
                if pregnancy_state:
                    was_pregnant=bool(pregnancy_state[0])
                    pregnancy_sequence=int(pregnancy_state[1] or 0)
                    newly_discovered=is_pregnant and not was_pregnant
                    if newly_discovered:
                        pregnancy_sequence+=1
                    cursor.execute(sql.SQL("""UPDATE {}.game_pregnancy_states
                        SET was_pregnant=%s,pregnancy_sequence=%s,updated_at=%s WHERE game_sim_id=%s""").format(sql.Identifier(schema_name)),
                        (1 if is_pregnant else 0,pregnancy_sequence,datetime.now(timezone.utc).isoformat(),game_sim_id))
                else:
                    newly_discovered=is_pregnant
                    pregnancy_sequence=1 if is_pregnant else 0
                    cursor.execute(sql.SQL("""INSERT INTO {}.game_pregnancy_states(
                        game_sim_id,was_pregnant,pregnancy_sequence,updated_at) VALUES(%s,%s,%s,%s)""").format(sql.Identifier(schema_name)),
                        (game_sim_id,1 if is_pregnant else 0,pregnancy_sequence,datetime.now(timezone.utc).isoformat()))
                if newly_discovered:
                    try:
                        progress=max(0.0,min(1.0,float(member.get("pregnancy_progress"))))
                    except (TypeError,ValueError):
                        progress=None
                    elapsed=round(pregnancy_length*progress) if progress is not None else 0
                    conception_day=target-int(elapsed)
                    due_day=conception_day+pregnancy_length
                    try:
                        babies_expected=max(1,int(member.get("pregnancy_offspring_count") or 1))
                    except (TypeError,ValueError):
                        babies_expected=1
                    cursor.execute(sql.SQL("""INSERT INTO {}.game_pregnancy_candidates(
                        detection_id,game_sim_id,pregnancy_sequence,first_name,last_name,
                        partner_game_sim_id,partner_first_name,partner_last_name,pregnancy_progress,
                        game_day,game_hour,game_minute,conception_global_day,due_global_day,babies_expected,
                        household_name,status,detected_at)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s)
                        ON CONFLICT(game_sim_id,pregnancy_sequence) DO NOTHING""").format(sql.Identifier(schema_name)),
                        ("PREGDET-"+uuid.uuid4().hex[:16].upper(),game_sim_id,pregnancy_sequence,
                         str(member.get("first_name") or "")[:100] or None,
                         str(member.get("last_name") or "")[:100] or None,
                         str(member.get("pregnancy_partner_game_sim_id") or "")[:100] or None,
                         str(member.get("pregnancy_partner_first_name") or "")[:100] or None,
                         str(member.get("pregnancy_partner_last_name") or "")[:100] or None,
                         progress,report.game_day,report.game_hour,report.game_minute,
                         conception_day,due_day,babies_expected,(report.household_name or "")[:200] or None,
                         datetime.now(timezone.utc).isoformat()))
                    if cursor.rowcount:
                        pregnancy_candidates_added+=1
            if report.household_sims and not members_initialized:
                cursor.execute(
                    "UPDATE public.decades_clock_sync SET members_initialized=TRUE WHERE token_hash=%s",
                    (digest,),
                )
        raw.commit()
        if target>current:
            save_manager.set_workspace(owner_hash,save_id)
            wrapped=Connection(raw,schema_name)
            wrapped.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("current_global_day",str(target)),
            )
            wrapped.commit()
            autorolls.sync_rolls(wrapped,target)
            action_queue.sync(wrapped,target)
            wrapped.close()
            raw=None
        return {
            "ok":True,"save_id":save_id,"game_day":report.game_day,
            "global_day":target,"advanced_by":max(0,target-current),
            "pregnancies_detected":pregnancy_candidates_added,
            "received_at":datetime.now(timezone.utc).isoformat(),
        }
    finally:
        if raw is not None:
            raw.close()
