
from __future__ import annotations
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parent
SEED_PATH=ROOT/"event_library.json.gz"
SEED_SETTING="event_library_seeded_v1"
REFERENCE_START_YEAR=1200
DAYS_PER_YEAR=4

def ensure_event_library(con):
    setting=con.execute("SELECT value FROM settings WHERE key=?",(SEED_SETTING,)).fetchone()
    if setting and str(setting[0])=='1': return 0
    existing=con.execute("SELECT COUNT(*) FROM events").fetchone()[0]; added=0
    if existing==0 and SEED_PATH.exists():
        import gzip
        with gzip.open(SEED_PATH,'rt',encoding='utf-8') as f: rows=json.load(f)
        cols=['event_id','start_global_day','end_global_day','event_name','scope','location','roll_required','affected_class','active','source','notes']; placeholders=','.join('?' for _ in cols); col_sql=','.join(cols)
        start_setting=con.execute("SELECT value FROM settings WHERE key='start_year'").fetchone(); save_start=int(float(start_setting[0])) if start_setting and start_setting[0] not in (None,'') else REFERENCE_START_YEAR
        def rebase(gd):
            if gd is None:return None
            g=int(gd); absolute_year=REFERENCE_START_YEAR+(g-1)//DAYS_PER_YEAR; quarter=((g-1)%DAYS_PER_YEAR)+1; return (absolute_year-save_start)*DAYS_PER_YEAR+quarter
        for row in rows:
            row=dict(row); row['start_global_day']=rebase(row.get('start_global_day')); row['end_global_day']=rebase(row.get('end_global_day'))
            con.execute(f"INSERT OR IGNORE INTO events({col_sql}) VALUES({placeholders})",tuple(row.get(c) for c in cols)); added+=1
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",(SEED_SETTING,'1')); con.commit(); return added

def detected_dice(notes):
    import re
    if not notes:return []
    found=[]
    for m in re.finditer(r'\b[dD](\d+)\b',str(notes)):
        sides=int(m.group(1))
        if 2<=sides<=1000 and sides not in found: found.append(sides)
    return found
