
from __future__ import annotations
from pathlib import Path
import json, shutil, sqlite3, uuid, datetime, zipfile, io

ROOT = Path(__file__).resolve().parent
SAVES_DIR = ROOT / "saves"
REGISTRY_PATH = ROOT / "saves.json"
LEGACY_DB = ROOT / "decades.db"

GAMEPLAY_TABLES = [
    "sim_photos","sims","households","pregnancies","rolls","relationships",
    "events","event_results","raw_import_rows"
]

DEFAULT_SAVE_NAME = "My First Save"
SAVE_PACKAGE_EXTENSION = ".decades-save"

def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def _read_registry():
    if not REGISTRY_PATH.exists():
        return {"active_save_id": None, "saves": []}
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"active_save_id": None, "saves": []}

def _write_registry(data):
    REGISTRY_PATH.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding="utf-8")

def ensure_setup():
    SAVES_DIR.mkdir(exist_ok=True)
    reg=_read_registry()
    valid=[]
    for s in reg.get("saves",[]):
        p=Path(s.get("db_path",""))
        if not p.is_absolute():
            p=ROOT/p
        if p.exists():
            s["db_path"]=str(p)
            valid.append(s)
    reg["saves"]=valid

    if not reg["saves"]:
        if not LEGACY_DB.exists():
            raise FileNotFoundError("No existing decades.db was found to initialize the first save.")
        sid="SAVE-"+uuid.uuid4().hex[:10].upper()
        dest=SAVES_DIR/f"{sid}.db"
        shutil.copy2(LEGACY_DB,dest)
        reg["saves"]=[{
            "save_id":sid,"name":DEFAULT_SAVE_NAME,"db_path":str(dest),
            "created_at":_now(),"updated_at":_now()
        }]
        reg["active_save_id"]=sid
        _write_registry(reg)
        return reg

    ids={s["save_id"] for s in reg["saves"]}
    if reg.get("active_save_id") not in ids:
        reg["active_save_id"]=reg["saves"][0]["save_id"]
    _write_registry(reg)
    return reg

def list_saves():
    return ensure_setup()["saves"]

def active_save_id():
    return ensure_setup()["active_save_id"]

def get_save(save_id):
    reg=ensure_setup()
    return next((s for s in reg["saves"] if s["save_id"]==save_id),None)

def active_save():
    return get_save(active_save_id())

def active_db_path():
    return Path(active_save()["db_path"])

def set_active(save_id):
    reg=ensure_setup()
    if not any(s["save_id"]==save_id for s in reg["saves"]):
        raise ValueError("Save not found.")
    reg["active_save_id"]=save_id
    for s in reg["saves"]:
        if s["save_id"]==save_id:
            s["updated_at"]=_now()
    _write_registry(reg)

def _new_save_record(name,path):
    sid=path.stem
    return {"save_id":sid,"name":name.strip() or sid,"db_path":str(path),"created_at":_now(),"updated_at":_now()}

def _append_save(rec,make_active=True):
    reg=ensure_setup()
    reg["saves"].append(rec)
    if make_active:
        reg["active_save_id"]=rec["save_id"]
    _write_registry(reg)
    return rec

def create_blank(name, calendar_start_year=1200, current_year=None, challenge_day=1, source_save_id=None):
    reg=ensure_setup()
    src=get_save(source_save_id or reg["active_save_id"])
    src_path=Path(src["db_path"])
    sid="SAVE-"+uuid.uuid4().hex[:10].upper()
    dest=SAVES_DIR/f"{sid}.db"
    shutil.copy2(src_path,dest)

    con=sqlite3.connect(dest)
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        for table in GAMEPLAY_TABLES:
            exists=con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
            if exists:
                con.execute(f"DELETE FROM {table}")
        start=int(calendar_start_year)
        year=int(current_year if current_year is not None else start)
        day=max(1,min(4,int(challenge_day)))
        gd=(year-start)*4+day
        for k,v in {
            "start_year":start,
            "days_per_year":4,
            "current_global_day":gd,
            "current_heir_id":"",
            "main_household_id":"",
        }.items():
            con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",(k,str(v)))
        con.commit()
    finally:
        con.close()

    return _append_save(_new_save_record(name,dest),True)

def duplicate_save(source_save_id,name):
    src=get_save(source_save_id)
    if not src: raise ValueError("Save not found.")
    sid="SAVE-"+uuid.uuid4().hex[:10].upper()
    dest=SAVES_DIR/f"{sid}.db"
    shutil.copy2(Path(src["db_path"]),dest)
    return _append_save(_new_save_record(name,dest),True)

def rename_save(save_id,new_name):
    reg=ensure_setup()
    for s in reg["saves"]:
        if s["save_id"]==save_id:
            s["name"]=new_name.strip() or s["name"]
            s["updated_at"]=_now()
            _write_registry(reg)
            return s
    raise ValueError("Save not found.")

def delete_save(save_id):
    reg=ensure_setup()
    if len(reg["saves"])<=1:
        raise ValueError("You cannot delete the only save.")
    target=next((s for s in reg["saves"] if s["save_id"]==save_id),None)
    if not target: raise ValueError("Save not found.")
    Path(target["db_path"]).unlink(missing_ok=True)
    reg["saves"]=[s for s in reg["saves"] if s["save_id"]!=save_id]
    if reg.get("active_save_id")==save_id:
        reg["active_save_id"]=reg["saves"][0]["save_id"]
    _write_registry(reg)

def _validate_database(path:Path):
    con=None
    try:
        con=sqlite3.connect(path)
        required={"settings","sims"}
        found={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required.issubset(found):
            raise ValueError("That file does not look like a Decades Tracker save.")
        result=con.execute("PRAGMA quick_check").fetchone()
        if not result or str(result[0]).lower()!="ok":
            raise ValueError("The save database failed SQLite's integrity check.")
    finally:
        if con: con.close()

def export_save_package(save_id):
    """Return bytes for a portable .decades-save package."""
    rec=get_save(save_id)
    if not rec:
        raise ValueError("Save not found.")
    db_path=Path(rec["db_path"])
    _validate_database(db_path)
    manifest={
        "format":"Decades Tracker Save",
        "format_version":1,
        "save_name":rec["name"],
        "exported_at":_now(),
        "database_file":"save.db"
    }
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json",json.dumps(manifest,indent=2,ensure_ascii=False))
        z.writestr("save.db",db_path.read_bytes())
    return buf.getvalue()

def import_save_package(data:bytes, preferred_name=None, make_active=True):
    """Import either .decades-save ZIP bytes or a legacy raw .db."""
    preferred=(preferred_name or "").strip()
    name=preferred or "Imported Save"
    db_bytes=None

    bio=io.BytesIO(data)
    if zipfile.is_zipfile(bio):
        bio.seek(0)
        with zipfile.ZipFile(bio,"r") as z:
            names=set(z.namelist())
            if "save.db" not in names:
                raise ValueError("This archive does not contain a Decades Tracker save.")
            db_bytes=z.read("save.db")
            if "manifest.json" in names:
                try:
                    manifest=json.loads(z.read("manifest.json").decode("utf-8"))
                    if not preferred:
                        name=manifest.get("save_name") or name
                except Exception:
                    pass
    else:
        db_bytes=data

    sid="SAVE-"+uuid.uuid4().hex[:10].upper()
    dest=SAVES_DIR/f"{sid}.db"
    dest.write_bytes(db_bytes)
    try:
        _validate_database(dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return _append_save(_new_save_record(name,dest),make_active)

def import_database(name,data:bytes,make_active=True):
    # Backward-compatible wrapper for old app versions.
    return import_save_package(data,name,make_active)

def touch_active():
    reg=ensure_setup()
    sid=reg["active_save_id"]
    for s in reg["saves"]:
        if s["save_id"]==sid:
            s["updated_at"]=_now()
    _write_registry(reg)
