
from __future__ import annotations
from datetime import datetime, timezone
from stats_engine import LIFE_STAGES, life_stage_from_age_days

MAX_PHOTO_BYTES = 8 * 1024 * 1024

def ensure_schema(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS sim_photos(
            sim_id TEXT PRIMARY KEY,
            image_data BLOB NOT NULL,
            mime_type TEXT,
            filename TEXT,
            updated_at TEXT,
            FOREIGN KEY(sim_id) REFERENCES sims(sim_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS sim_lifestage_photos(
            sim_id TEXT NOT NULL,
            life_stage TEXT NOT NULL,
            image_data BLOB NOT NULL,
            mime_type TEXT,
            filename TEXT,
            updated_at TEXT,
            PRIMARY KEY(sim_id,life_stage),
            FOREIGN KEY(sim_id) REFERENCES sims(sim_id)
        )
    """)
    con.commit()


LIFE_STAGE_NAMES=tuple(stage[0] for stage in LIFE_STAGES)


def normalize_life_stage(life_stage):
    text=str(life_stage or "").strip().casefold()
    return next((name for name in LIFE_STAGE_NAMES if name.casefold()==text),None)

def get_photo(con, sim_id):
    return con.execute(
        "SELECT image_data,mime_type,filename,updated_at FROM sim_photos WHERE sim_id=?",
        (sim_id,)
    ).fetchone()


def get_lifestage_photo(con,sim_id,life_stage):
    stage=normalize_life_stage(life_stage)
    if not stage:return None
    return con.execute(
        """SELECT image_data,mime_type,filename,updated_at,life_stage
           FROM sim_lifestage_photos WHERE sim_id=? AND life_stage=?""",
        (sim_id,stage),
    ).fetchone()


def current_life_stage(con,sim_id,current_global_day):
    row=con.execute("SELECT birth_global_day,death_global_day FROM sims WHERE sim_id=?",(sim_id,)).fetchone()
    if not row or row[0] is None:return None
    reference=int(current_global_day)
    if row[1] is not None:reference=min(reference,int(row[1]))
    return life_stage_from_age_days(max(0,reference-int(row[0])))


def get_current_photo(con,sim_id,current_global_day):
    stage=current_life_stage(con,sim_id,current_global_day)
    photo=get_lifestage_photo(con,sim_id,stage) if stage else None
    return photo or get_photo(con,sim_id)

def get_photos(con, sim_ids):
    identifiers=[identifier for identifier in dict.fromkeys(sim_ids) if identifier]
    if not identifiers:
        return {}
    placeholders=",".join("?" for _ in identifiers)
    rows=con.execute(
        f"SELECT sim_id,image_data,mime_type,filename,updated_at FROM sim_photos WHERE sim_id IN ({placeholders})",
        identifiers,
    ).fetchall()
    return {row["sim_id"]:row for row in rows}

def save_photo(con, sim_id, uploaded_file):
    if uploaded_file is None:
        return
    data=uploaded_file.getvalue()
    if len(data)>MAX_PHOTO_BYTES:
        raise ValueError("Photo is larger than 8 MB.")
    # Schema is normally created at app startup. CREATE IF NOT EXISTS here is
    # deliberately left inside the caller's transaction so saving a new Sim +
    # portrait remains atomic.
    con.execute("""
        CREATE TABLE IF NOT EXISTS sim_photos(
            sim_id TEXT PRIMARY KEY,
            image_data BLOB NOT NULL,
            mime_type TEXT,
            filename TEXT,
            updated_at TEXT,
            FOREIGN KEY(sim_id) REFERENCES sims(sim_id)
        )
    """)
    mime=getattr(uploaded_file,"type",None) or "application/octet-stream"
    filename=getattr(uploaded_file,"name",None)
    con.execute("""INSERT INTO sim_photos(sim_id,image_data,mime_type,filename,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(sim_id) DO UPDATE SET
                     image_data=excluded.image_data,
                     mime_type=excluded.mime_type,
                     filename=excluded.filename,
                     updated_at=excluded.updated_at""",
                (sim_id,data,mime,filename,datetime.now(timezone.utc).isoformat()))


def save_lifestage_photo(con,sim_id,life_stage,uploaded_file):
    stage=normalize_life_stage(life_stage)
    if not stage:raise ValueError("Choose a valid life stage.")
    if uploaded_file is None:return
    data=uploaded_file.getvalue()
    if len(data)>MAX_PHOTO_BYTES:raise ValueError("Photo is larger than 8 MB.")
    mime=getattr(uploaded_file,"type",None) or "application/octet-stream"
    filename=getattr(uploaded_file,"name",None)
    con.execute("""INSERT INTO sim_lifestage_photos(sim_id,life_stage,image_data,mime_type,filename,updated_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(sim_id,life_stage) DO UPDATE SET
                   image_data=excluded.image_data,mime_type=excluded.mime_type,
                   filename=excluded.filename,updated_at=excluded.updated_at""",
                (sim_id,stage,data,mime,filename,datetime.now(timezone.utc).isoformat()))

def delete_photo(con, sim_id):
    con.execute("DELETE FROM sim_photos WHERE sim_id=?",(sim_id,))


def delete_lifestage_photo(con,sim_id,life_stage):
    stage=normalize_life_stage(life_stage)
    if stage:con.execute("DELETE FROM sim_lifestage_photos WHERE sim_id=? AND life_stage=?",(sim_id,stage))

def display_name(row):
    def val(k):
        try:
            x=row[k]
        except Exception:
            try:x=getattr(row,k)
            except Exception:return ""
        return "" if x is None else str(x).strip()
    return " ".join(x for x in [val("title"),val("first_name"),val("last_name"),val("suffix")] if x).strip()

def sync_spouse_ids(con, sim_ids=None, commit=True):
    """Rebuild legacy spouse_ids from marriage records so duplicate editing is unnecessary."""
    if sim_ids is None:
        sim_ids=[r[0] for r in con.execute("SELECT sim_id FROM sims")]
    for sim_id in sim_ids:
        partners=[]
        for r in con.execute("""SELECT partner1_id,partner2_id FROM relationships
                                WHERE lower(COALESCE(type,''))='marriage'
                                  AND (partner1_id=? OR partner2_id=?)""",(sim_id,sim_id)):
            other=r[1] if r[0]==sim_id else r[0]
            if other and other not in partners:
                partners.append(other)
        con.execute("UPDATE sims SET spouse_ids=? WHERE sim_id=?",
                    (", ".join(partners) if partners else None,sim_id))
    if commit:
        con.commit()

def sync_marriage(con,sim_id,spouse_id,global_day,date_text=None,location=None):
    """Create/update one marriage and mirror its details onto both Sim profiles."""
    if not sim_id or not spouse_id or sim_id==spouse_id or global_day is None:
        return None
    names={}
    for row in con.execute("""SELECT sim_id,TRIM(COALESCE(title,'')||' '||COALESCE(first_name,'')||' '||
                               COALESCE(last_name,'')||' '||COALESCE(suffix,'')) FROM sims WHERE sim_id IN (?,?)""",
                           (sim_id,spouse_id)):
        names[row[0]]=row[1]
    existing=con.execute("""SELECT relationship_id,partner1_id,partner2_id FROM relationships WHERE
                            ((partner1_id=? AND partner2_id=?) OR (partner1_id=? AND partner2_id=?))
                            AND (LOWER(COALESCE(type,''))='marriage' OR COALESCE(legally_married,0)=1)
                            ORDER BY start_global_day DESC LIMIT 1""",
                         (sim_id,spouse_id,spouse_id,sim_id)).fetchone()
    if existing:
        relationship_id=existing[0]
        con.execute("""UPDATE relationships SET partner1_name=?,partner2_name=?,type='Marriage',
                       start_global_day=?,start_date=?,status='Active',location=?,legally_married=1
                       WHERE relationship_id=?""",
                    (names.get(existing[1]),names.get(existing[2]),int(global_day),date_text or None,
                     location or None,relationship_id))
    else:
        numbers=[]
        for row in con.execute("SELECT relationship_id FROM relationships WHERE relationship_id LIKE ?",("REL-%",)):
            try:numbers.append(int(str(row[0]).rsplit("-",1)[1]))
            except Exception:pass
        relationship_id=f"REL-{max(numbers,default=0)+1:04d}"
        con.execute("""INSERT INTO relationships(relationship_id,partner1_id,partner2_id,partner1_name,
                       partner2_name,type,start_global_day,start_date,status,location,legally_married,children_count,notes)
                       VALUES(?,?,?,?,?,'Marriage',?,?,'Active',?,1,0,?)""",
                    (relationship_id,sim_id,spouse_id,names.get(sim_id),names.get(spouse_id),int(global_day),
                     date_text or None,location or None,"Created automatically from Sim marriage details"))
    con.execute("""UPDATE sims SET marriage_global_day=?,marriage_date=?,marriage_place=? WHERE sim_id IN (?,?)""",
                (int(global_day),date_text or None,location or None,sim_id,spouse_id))
    sync_spouse_ids(con,[sim_id,spouse_id],commit=False)
    return relationship_id

def sim_relationships(con, sim_id):
    return con.execute("""SELECT * FROM relationships
                          WHERE partner1_id=? OR partner2_id=?
                          ORDER BY start_global_day DESC,relationship_id""",
                       (sim_id,sim_id)).fetchall()

def sync_cached_names(con, sim_ids=None):
    """Keep denormalized display-name columns in related tables aligned after a Sim is renamed."""
    if sim_ids is None:
        sim_ids=[r[0] for r in con.execute("SELECT sim_id FROM sims")]
    for sim_id in [x for x in sim_ids if x]:
        r=con.execute("""SELECT TRIM(COALESCE(title,'')||' '||COALESCE(first_name,'')||' '||
                                      COALESCE(last_name,'')||' '||COALESCE(suffix,''))
                         FROM sims WHERE sim_id=?""",(sim_id,)).fetchone()
        if not r:
            continue
        nm=r[0].strip()
        con.execute("UPDATE relationships SET partner1_name=? WHERE partner1_id=?",(nm,sim_id))
        con.execute("UPDATE relationships SET partner2_name=? WHERE partner2_id=?",(nm,sim_id))
        con.execute("UPDATE pregnancies SET mother_name=? WHERE mother_id=?",(nm,sim_id))
        con.execute("UPDATE pregnancies SET father_name=? WHERE father_id=?",(nm,sim_id))
        con.execute("UPDATE rolls SET sim_name=? WHERE sim_id=?",(nm,sim_id))
    con.commit()
