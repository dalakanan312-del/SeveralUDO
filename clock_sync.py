from __future__ import annotations

import hashlib
import json
import os
import secrets

import cloud_schema
import save_manager
import storage


DEFAULT_RECEIVER_URL = os.environ.get(
    "CLOCK_SYNC_URL", "https://severaludo-clock-sync.up.railway.app"
).rstrip("/")


def token_hash(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def ensure_registry():
    with storage.raw_connect(use_direct=True) as connection:
        cloud_schema.create_registry(connection)


def status(workspace, save_id):
    ensure_registry()
    with storage.raw_connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT enabled,game_anchor_day,tracker_anchor_day,last_game_day,last_tracker_day,last_seen_at
                   FROM public.decades_clock_sync WHERE owner_hash=%s AND save_id=%s
                   ORDER BY created_at DESC LIMIT 1""",
                (workspace, save_id),
            )
            row=cursor.fetchone()
    if not row:
        return None
    keys=("enabled","game_anchor_day","tracker_anchor_day","last_game_day","last_tracker_day","last_seen_at")
    return dict(zip(keys,row))


def create_link(workspace, save_record):
    ensure_registry()
    token="dcs_"+secrets.token_urlsafe(32)
    digest=token_hash(token)
    with storage.raw_connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE public.decades_clock_sync SET enabled=FALSE WHERE owner_hash=%s AND save_id=%s",
                (workspace,save_record["save_id"]),
            )
            cursor.execute(
                """INSERT INTO public.decades_clock_sync(token_hash,owner_hash,save_id,schema_name,enabled)
                   VALUES(%s,%s,%s,%s,TRUE)""",
                (digest,workspace,save_record["save_id"],save_record["schema_name"]),
            )
        connection.commit()
    return token


def revoke(workspace,save_id):
    with storage.raw_connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE public.decades_clock_sync SET enabled=FALSE WHERE owner_hash=%s AND save_id=%s",
                (workspace,save_id),
            )
        connection.commit()


def config_bytes(token,receiver_url=None):
    payload={
        "receiver_url":(receiver_url or DEFAULT_RECEIVER_URL).rstrip("/")+"/v1/clock",
        "sync_token":token,
        "enabled":True,
    }
    return json.dumps(payload,indent=2).encode("utf-8")

