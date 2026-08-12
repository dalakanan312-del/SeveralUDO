from __future__ import annotations

from datetime import datetime, timezone


def ensure_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS notebook_entries(
        note_id TEXT PRIMARY KEY,title TEXT NOT NULL,category TEXT,body TEXT,
        pinned INTEGER NOT NULL DEFAULT 0,created_at TEXT,updated_at TEXT
    )""")
    con.commit()


def next_id(con):
    numbers=[]
    for (value,) in con.execute("SELECT note_id FROM notebook_entries WHERE note_id LIKE ?",("NOTE-%",)):
        try: numbers.append(int(str(value).rsplit("-",1)[1]))
        except (TypeError,ValueError): pass
    return f"NOTE-{max(numbers,default=0)+1:04d}"


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
