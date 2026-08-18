"""File-based save manager for the downloadable SQLite edition."""

from __future__ import annotations

import datetime
import io
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path

import cloud_schema

ROOT=Path(__file__).resolve().parent
DATA_DIR=Path(os.environ.get("DECADES_LOCAL_DATA_DIR") or (ROOT/"local_data"))
SAVES_DIR=DATA_DIR/"saves"
REGISTRY=DATA_DIR/"registry.json"
_ACTIVE=None


def _now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def set_workspace(_workspace,active_save_id=None):
    global _ACTIVE
    if active_save_id:_ACTIVE=active_save_id
def workspace_id(): return "local"
def _read():
    if not REGISTRY.exists(): return {"active_save_id":None,"saves":[]}
    try:return json.loads(REGISTRY.read_text(encoding="utf-8"))
    except Exception:return {"active_save_id":None,"saves":[]}
def _write(data):
    DATA_DIR.mkdir(parents=True,exist_ok=True); SAVES_DIR.mkdir(parents=True,exist_ok=True)
    REGISTRY.write_text(json.dumps(data,indent=2),encoding="utf-8")


def _initialize(path,start=1200,year=None,day=1):
    connection=sqlite3.connect(path)
    try:
        for ddl in cloud_schema.TABLE_DDLS: connection.execute(ddl)
        global_day=((int(year if year is not None else start)-int(start))*4)+max(1,min(4,int(day)))
        for key,value in {"start_year":start,"days_per_year":4,"current_global_day":global_day,
                          "current_heir_id":"","main_household_id":"","roll_tracking_start":1}.items():
            connection.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))
        connection.commit()
    finally:connection.close()


def ensure_setup():
    SAVES_DIR.mkdir(parents=True,exist_ok=True)
    if not list_saves():
        starter=ROOT/"STARTER_SAVE.decades-save"
        if starter.exists(): import_save_package(starter.read_bytes(),make_active=True)
        else: create_blank("My Local Save",1200,1200,1)
    return list_saves()
def list_saves(force_refresh=False): return [dict(item) for item in _read().get("saves",[])]
def active_save_id():
    global _ACTIVE
    data=_read(); ids={item["save_id"] for item in data.get("saves",[])}
    if _ACTIVE in ids:return _ACTIVE
    configured=data.get("active_save_id")
    if configured in ids:_ACTIVE=configured; return configured
    _ACTIVE=next(iter(ids),None); return _ACTIVE
def get_save(save_id): return next((item for item in list_saves() if item["save_id"]==save_id),None)
def active_save():
    identifier=active_save_id(); return get_save(identifier) if identifier else None
def set_active(save_id):
    global _ACTIVE
    if not get_save(save_id):raise ValueError("Save not found.")
    _ACTIVE=save_id; data=_read(); data["active_save_id"]=save_id; _write(data)


def _register(name,path,source_note):
    global _ACTIVE
    save_id="SAVE-"+uuid.uuid4().hex[:10].upper(); now=_now()
    record={"save_id":save_id,"name":name or save_id,"schema_name":str(path),"path":str(path),
            "created_at":now,"updated_at":now,"source_note":source_note}
    data=_read(); data.setdefault("saves",[]).append(record); data["active_save_id"]=save_id; _ACTIVE=save_id; _write(data)
    return dict(record)
def create_blank(name,calendar_start_year=1200,current_year=None,challenge_day=1,source_save_id=None):
    path=SAVES_DIR/("save_"+uuid.uuid4().hex+".db"); _initialize(path,calendar_start_year,current_year,challenge_day)
    if source_save_id:
        source=get_save(source_save_id)
        if source: shutil.copy2(source["path"],path)
    return _register(name,path,"Local SQLite save")
def duplicate_save(source_save_id,name):
    source=get_save(source_save_id)
    if not source:raise ValueError("Save not found.")
    path=SAVES_DIR/("save_"+uuid.uuid4().hex+".db"); shutil.copy2(source["path"],path)
    return _register(name,path,"Duplicated local save")
def rename_save(save_id,new_name):
    data=_read()
    for item in data["saves"]:
        if item["save_id"]==save_id:item["name"]=new_name.strip() or item["name"]; item["updated_at"]=_now()
    _write(data)
def delete_save(save_id):
    data=_read()
    if len(data.get("saves",[]))<=1:raise ValueError("You cannot delete the only save.")
    record=next((item for item in data["saves"] if item["save_id"]==save_id),None)
    if record: Path(record["path"]).unlink(missing_ok=True)
    data["saves"]=[item for item in data["saves"] if item["save_id"]!=save_id]
    data["active_save_id"]=data["saves"][0]["save_id"]; _write(data); set_active(data["active_save_id"])
def touch_active():
    data=_read(); identifier=active_save_id()
    for item in data.get("saves",[]):
        if item["save_id"]==identifier:item["updated_at"]=_now()
    _write(data)


def export_database_bytes(save_id):
    record=get_save(save_id)
    if not record:raise ValueError("Save not found.")
    return Path(record["path"]).read_bytes()
def export_save_package(save_id):
    record=get_save(save_id); buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json",json.dumps({"format":"Decades Tracker Save","format_version":1,
            "save_name":record["name"],"exported_at":_now(),"database_file":"save.db"},indent=2))
        archive.writestr("save.db",export_database_bytes(save_id))
    return buffer.getvalue()
def import_save_package(data,preferred_name=None,make_active=True):
    name=(preferred_name or "").strip() or "Imported Save"; stream=io.BytesIO(data); database=data
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            database=archive.read("save.db")
            if "manifest.json" in archive.namelist() and not preferred_name:
                name=json.loads(archive.read("manifest.json").decode("utf-8")).get("save_name") or name
    path=SAVES_DIR/("save_"+uuid.uuid4().hex+".db"); path.write_bytes(database)
    connection=sqlite3.connect(path)
    try:
        if connection.execute("PRAGMA quick_check").fetchone()[0].lower()!="ok":raise ValueError("The local save failed its integrity check.")
        for ddl in cloud_schema.TABLE_DDLS:connection.execute(ddl)
        connection.commit()
    finally:connection.close()
    record=_register(name,path,"Imported local save")
    if not make_active:set_active(active_save_id())
    return record
def import_database(name,data,make_active=True):return import_save_package(data,name,make_active)
def discover_local_saves():return []
