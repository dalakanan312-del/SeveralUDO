
from pathlib import Path
import sqlite3
import save_manager

def active_db_path():
    return save_manager.active_db_path()

# Backward-compatible symbol. Do not use for switching logic; call active_db_path().
DB_PATH = Path(__file__).with_name("decades.db")

def connect():
    con=sqlite3.connect(active_db_path())
    con.row_factory=sqlite3.Row
    return con

def setting(con,key,default=None):
    r=con.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
    return r[0] if r else default

def set_setting(con,key,value):
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",(key,str(value)))
    con.commit()
    save_manager.touch_active()

def next_id(con,table,col,prefix,width=4):
    vals=[r[0] for r in con.execute(f"SELECT {col} FROM {table} WHERE {col} LIKE ?",(prefix+'-%',))]
    nums=[]
    for v in vals:
        try: nums.append(int(v.rsplit('-',1)[1]))
        except: pass
    return f"{prefix}-{max(nums,default=0)+1:0{width}d}"
