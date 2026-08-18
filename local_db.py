"""SQLite adapter used by the downloadable offline edition."""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict

import local_save_manager as save_manager

_TABLE_REVISIONS = defaultdict(int)


def statement_tables(statement):
    names=re.findall(r"\b(?:FROM|JOIN|UPDATE|INTO|DELETE\s+FROM)\s+([a-zA-Z_][\w]*)",str(statement),flags=re.I)
    return tuple(sorted({name.lower() for name in names}))


def cache_token(schema_name,tables=None):
    return tuple((table,_TABLE_REVISIONS[(schema_name,table)]) for table in sorted(set(tables or ())))


class HybridRow:
    def __init__(self,row):
        self._row=row
    def __getitem__(self,key): return self._row[key]
    def __iter__(self): return iter(tuple(self._row))
    def __len__(self): return len(self._row)
    def keys(self): return self._row.keys()
    def values(self): return tuple(self._row)


class Cursor:
    def __init__(self,cursor,connection):
        self._cursor=cursor; self._connection=connection
    @property
    def description(self): return self._cursor.description
    @property
    def rowcount(self): return self._cursor.rowcount
    def fetchone(self):
        row=self._cursor.fetchone(); return HybridRow(row) if row is not None else None
    def fetchall(self): return [HybridRow(row) for row in self._cursor.fetchall()]
    def __iter__(self):
        for row in self._cursor: yield HybridRow(row)
    def close(self): self._cursor.close()
    def execute(self,statement,parameters=()):
        text=str(statement)
        if "pg_advisory_xact_lock" in text:
            text="SELECT 1"
            parameters=()
        self._cursor.execute(text,tuple(parameters or ()))
        command=text.lstrip().split(None,1)[0].upper() if text.strip() else ""
        if command in {"INSERT","UPDATE","DELETE"} and self._cursor.rowcount!=0:
            self._connection._changed=True
            self._connection._changed_tables.update(statement_tables(text))
        return self
    def executemany(self,statement,sequence):
        self._cursor.executemany(str(statement),sequence)
        self._connection._changed=True
        self._connection._changed_tables.update(statement_tables(statement))
        return self


class Connection:
    def __init__(self,path,raw):
        self.path=path; self.schema_name=str(path); self._raw=raw
        self._changed=False; self._changed_tables=set()
    def execute(self,statement,parameters=()): return Cursor(self._raw.cursor(),self).execute(statement,parameters)
    def executemany(self,statement,sequence): return Cursor(self._raw.cursor(),self).executemany(statement,sequence)
    def cursor(self): return Cursor(self._raw.cursor(),self)
    def commit(self):
        self._raw.commit()
        if self._changed:
            for table in self._changed_tables:_TABLE_REVISIONS[(self.schema_name,table)]+=1
            self._changed=False; self._changed_tables.clear(); save_manager.touch_active()
    def rollback(self): self._raw.rollback(); self._changed=False; self._changed_tables.clear()
    def close(self): self._raw.close()
    def __enter__(self): return self
    def __exit__(self,*_): self.close()


def connect():
    record=save_manager.active_save()
    if not record: raise RuntimeError("No local save exists yet.")
    raw=sqlite3.connect(record["path"],timeout=20)
    raw.row_factory=sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA foreign_keys=ON")
    return Connection(record["path"],raw)


def setting(connection,key,default=None):
    row=connection.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
    return row[0] if row else default


def set_setting(connection,key,value):
    connection.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))
    connection.commit()


def next_id(connection,table,column,prefix,width=4):
    prefix=str(prefix).rstrip("-")
    values=[row[0] for row in connection.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE ?",(prefix+"-%",))]
    nums=[]
    for value in values:
        try: nums.append(int(str(value).rsplit("-",1)[1]))
        except (TypeError,ValueError): pass
    return f"{prefix}-{max(nums,default=0)+1:0{width}d}"
