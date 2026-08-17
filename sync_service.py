from __future__ import annotations

import os
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


def database_url():
    value=os.environ.get("DATABASE_URL") or os.environ.get("NEON_DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required")
    return value


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
                """SELECT owner_hash,save_id,schema_name,game_anchor_day,tracker_anchor_day,last_game_day
                   FROM public.decades_clock_sync WHERE token_hash=%s AND enabled=TRUE FOR UPDATE""",
                (digest,),
            )
            link=cursor.fetchone()
            if not link:
                raise HTTPException(401,"Invalid or revoked sync token")
            owner_hash,save_id,schema_name,game_anchor,tracker_anchor,last_game=link
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
            "received_at":datetime.now(timezone.utc).isoformat(),
        }
    finally:
        if raw is not None:
            raw.close()
