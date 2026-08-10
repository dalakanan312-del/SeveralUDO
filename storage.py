
from __future__ import annotations
from pathlib import Path
import json, os, threading
ROOT=Path(__file__).resolve().parent
CONFIG_PATH=ROOT/".neon_storage.json"
ENV_PATH=ROOT/".env"
class StorageNotConfigured(RuntimeError):pass

def _load_env_file():
    values={}
    if not ENV_PATH.exists():return values
    try:
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line=raw.strip()
            if not line or line.startswith("#") or "=" not in line:continue
            key,value=line.split("=",1); key=key.strip(); value=value.strip().strip('"').strip("'")
            if key:values[key]=value
    except Exception:pass
    return values

def load_config():
    env_file=_load_env_file(); env=(os.environ.get("DATABASE_URL") or os.environ.get("NEON_DATABASE_URL") or env_file.get("DATABASE_URL") or env_file.get("NEON_DATABASE_URL"))
    if env:
        direct=(os.environ.get("NEON_DIRECT_URL") or env_file.get("NEON_DIRECT_URL") or env)
        return {"pooled_url":env,"direct_url":direct,"active_save_id":None,"source":"environment"}
    if not CONFIG_PATH.exists():return {}
    try:return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:return {}

def configured():return bool((load_config().get("pooled_url") or "").strip())
def save_config(pooled_url,direct_url=None,active_save_id=None):
    data={"pooled_url":(pooled_url or "").strip(),"direct_url":(direct_url or pooled_url or "").strip(),"active_save_id":active_save_id}; CONFIG_PATH.write_text(json.dumps(data,indent=2),encoding="utf-8"); return data
def update_active_save(save_id):
    cfg=load_config()
    if cfg.get("source")=="environment":return
    cfg["active_save_id"]=save_id; CONFIG_PATH.write_text(json.dumps(cfg,indent=2),encoding="utf-8")
def clear_config():CONFIG_PATH.unlink(missing_ok=True)
def raw_connect(use_direct=False,autocommit=False):
    cfg=load_config(); dsn=(cfg.get("direct_url") if use_direct else cfg.get("pooled_url")) or cfg.get("pooled_url")
    if not dsn:raise StorageNotConfigured("Neon storage has not been configured.")
    import psycopg
    return psycopg.connect(dsn,autocommit=autocommit)
def test_connection(dsn):
    import psycopg
    with psycopg.connect(dsn,connect_timeout=10) as con:
        with con.cursor() as cur:cur.execute("SELECT current_database(), current_user, version()");return cur.fetchone()
