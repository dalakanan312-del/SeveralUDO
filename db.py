
from __future__ import annotations
import re
import storage
import save_manager

class HybridRow:
    def __init__(self,cols,values):
        self._cols=list(cols); self._values=tuple(values); self._map=dict(zip(self._cols,self._values))
    def __getitem__(self,key): return self._values[key] if isinstance(key,int) else self._map[key]
    def __iter__(self): return iter(self._values)
    def __len__(self): return len(self._values)
    def keys(self): return self._cols
    def values(self): return self._values
    def __repr__(self): return repr(self._map)

def _translate(sql):
    s=str(sql); upper=s.strip().upper()
    if upper.startswith("INSERT OR REPLACE INTO SETTINGS"):
        s=re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO","INSERT INTO",s,count=1,flags=re.I); s=s.rstrip().rstrip(";")+" ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    elif upper.startswith("INSERT OR REPLACE INTO ROLL_RULE_VALUES"):
        s=re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO","INSERT INTO",s,count=1,flags=re.I); s=s.rstrip().rstrip(";")+" ON CONFLICT(era_id,roll_type) DO UPDATE SET die=excluded.die,bad_results=excluded.bad_results,notes=excluded.notes"
    elif upper.startswith("INSERT OR IGNORE INTO"):
        s=re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO","INSERT INTO",s,count=1,flags=re.I); s=s.rstrip().rstrip(";")+" ON CONFLICT DO NOTHING"
    return s.replace("?","%s")

class Cursor:
    def __init__(self,cur): self._cur=cur
    @property
    def description(self): return self._cur.description
    @property
    def rowcount(self): return self._cur.rowcount
    def _cols(self): return [d.name for d in self._cur.description] if self._cur.description else []
    def fetchone(self):
        r=self._cur.fetchone(); return None if r is None else HybridRow(self._cols(),r)
    def fetchall(self):
        rows=self._cur.fetchall(); cols=self._cols(); return [HybridRow(cols,r) for r in rows]
    def __iter__(self):
        cols=self._cols()
        for r in self._cur: yield HybridRow(cols,r)
    def close(self): self._cur.close()

class Connection:
    def __init__(self,raw,schema_name):
        self._raw=raw; self.schema_name=schema_name
        from psycopg import sql
        with self._raw.cursor() as cur: cur.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema_name)))
    def execute(self,sql,params=()):
        cur=self._raw.cursor(); cur.execute(_translate(sql),tuple(params or ())); return Cursor(cur)
    def executemany(self,sql,seq):
        cur=self._raw.cursor(); cur.executemany(_translate(sql),seq); return Cursor(cur)
    def commit(self): self._raw.commit()
    def rollback(self): self._raw.rollback()
    def close(self): self._raw.close()
    def cursor(self): return self._raw.cursor()

def connect():
    rec=save_manager.active_save()
    if not rec: raise RuntimeError("No Neon save exists yet.")
    return Connection(storage.raw_connect(),rec["schema_name"])

def setting(con,key,default=None):
    r=con.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone(); return r[0] if r else default

def set_setting(con,key,value):
    con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value))); con.commit(); save_manager.touch_active()

def next_id(con,table,col,prefix,width=4):
    vals=[r[0] for r in con.execute(f"SELECT {col} FROM {table} WHERE {col} LIKE ?",(prefix+'-%',))]; nums=[]
    for v in vals:
        try: nums.append(int(v.rsplit('-',1)[1]))
        except Exception: pass
    return f"{prefix}-{max(nums,default=0)+1:0{width}d}"

def query_df(con,sql,params=()):
    import pandas as pd
    cur=con.execute(sql,params)
    if not cur.description: return pd.DataFrame()
    cols=[d.name for d in cur.description]; rows=cur.fetchall()
    return pd.DataFrame([[r[i] for i in range(len(cols))] for r in rows],columns=cols)
