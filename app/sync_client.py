from __future__ import annotations

import json
import threading
import time
import logging
from functools import wraps

import httpx
from sqlalchemy import select

from .config import ROOT, settings
from .db import SessionLocal
from .models import Change, ChronicleSave, Record
from . import sync


CONFIG = ROOT / "data" / "cloud-sync.json"
_started = False
_cycle_lock = threading.RLock()


def reported_cycle(function):
    @wraps(function)
    def run():
        from .background_status import record
        with _cycle_lock:
            try:
                result = function()
                record('Desktop / online sync', result['status'], result.get('message') or
                       f"Sent {result.get('pushed',0)} changes; received {result.get('pulled',0)}. Conflicts: {result.get('conflicts',0)}.")
                return result
            except Exception as exc:
                record('Desktop / online sync', 'error', 'Sync stopped safely. Check the connection and selected save on Desktop / Online Sync. Unsent changes were kept.')
                logging.getLogger(__name__).error('Desktop sync failed (%s)', type(exc).__name__)
                raise
    return run


def load_config() -> dict:
    if not CONFIG.exists(): return {}
    try: return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError): return {'configuration_error':True}


def save_config(remote_url: str, device_token: str, remote_save_id: str, local_save_id: str) -> None:
    with _cycle_lock:
        write_config({
        "remote_url": remote_url.rstrip("/"), "token": device_token,
        "remote_save_id": remote_save_id, "local_save_id": local_save_id,
        "pushed_sequence": 0, "pulled_sequence": 0,
        })


def write_config(config):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    pending=CONFIG.with_name(CONFIG.name+'.pending')
    pending.write_text(json.dumps(config,indent=2),encoding='utf-8')
    pending.replace(CONFIG)


@reported_cycle
def cycle() -> dict:
    config = load_config()
    if config.get('configuration_error'):
        raise ValueError('Sync configuration cannot be read; reconnect this save')
    if not config.get("token"): return {"status": "not-configured"}
    headers = {"Authorization": f"Bearer {config['token']}"}
    with SessionLocal() as session:
        from .infinite_decades import state
        local_save = session.get(ChronicleSave, config['local_save_id'])
        if not local_save:
            raise ValueError('The connected local save no longer exists')
        if state(local_save):
            return {"status":"paused", "pushed":0, "pulled":0,
                    "message":"Infinite Decades uses whole-dynasty export/import. Record-by-record cloud sync is paused to protect branch checkpoints."}
        outgoing = list(session.scalars(select(Change).where(
            Change.save_id == config["local_save_id"],
            Change.sequence > int(config.get("pushed_sequence", 0)),
        ).order_by(Change.sequence).limit(250)))
        body = {"save_id": config.get('remote_save_id'), "after": int(config.get("pulled_sequence", 0)), "changes": [{
            "change_id": c.id, "record_id": c.record_id, "kind": c.kind,
            "operation": c.operation, "base_version": c.base_version, "payload": c.payload,
        } for c in outgoing]}
    response = httpx.post(f"{config['remote_url']}/api/sync/push", headers=headers, json=body, timeout=30)
    response.raise_for_status()
    result = response.json()
    if result.get('save_id') and result['save_id'] != config.get('remote_save_id'):
        raise ValueError('The response is for a different hosted save')
    with SessionLocal() as session:
        for change in result.get("changes", []):
            payload = change["payload"]
            record = session.get(Record, change["record_id"])
            if record and record.save_id != config['local_save_id']:
                raise ValueError('Incoming record belongs to a different local save; reconnect the correct save')
            if change['kind'] not in sync.SYNC_KINDS or (record and record.kind != change['kind']):
                raise ValueError('Incoming record has an unsupported or changed type')
            if record and record.version >= int(change["new_version"]): continue
            if record is None:
                record = Record(id=change["record_id"], save_id=config["local_save_id"], kind=change["kind"])
                session.add(record)
            record.kind = change["kind"]; record.label = payload.get("label", "")
            record.global_day = payload.get("global_day"); record.data = payload.get("data", {})
            record.version = int(change["new_version"]); record.deleted = bool(payload.get("deleted"))
            record.updated_by_device = change.get("device_id", "cloud")
            local_save=session.get(ChronicleSave,config["local_save_id"])
            if local_save: sync.materialize_special(session,local_save,record)
        session.commit()
    if outgoing: config["pushed_sequence"] = outgoing[-1].sequence
    config["pulled_sequence"] = int(result.get("cursor", config.get("pulled_sequence", 0)))
    write_config(config)
    return {"status": "ok", "pushed": len(outgoing), "pulled": len(result.get("changes", [])), "conflicts": sum(1 for item in result.get("results", []) if item.get("status") == "conflict")}


def _loop() -> None:
    while True:
        try: cycle()
        except Exception:
            # The wrapper records a safe, user-visible error without credentials.
            pass
        time.sleep(10)


def start() -> None:
    global _started
    if _started or not settings.local_mode: return
    _started = True
    threading.Thread(target=_loop, name="decades-cloud-sync", daemon=True).start()
