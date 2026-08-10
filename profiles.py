
from __future__ import annotations
from datetime import datetime, timezone

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
    con.commit()

def get_photo(con, sim_id):
    ensure_schema(con)
    return con.execute(
        "SELECT image_data,mime_type,filename,updated_at FROM sim_photos WHERE sim_id=?",
        (sim_id,)
    ).fetchone()

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

def delete_photo(con, sim_id):
    con.execute("DELETE FROM sim_photos WHERE sim_id=?",(sim_id,))

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
