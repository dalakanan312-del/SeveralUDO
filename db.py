from __future__ import annotations

import re

import save_manager
import storage


class HybridRow:
    def __init__(self, columns, values):
        self._columns = list(columns)
        self._values = tuple(values)
        self._map = dict(zip(self._columns, self._values))

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else self._map[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return self._columns

    def values(self):
        return self._values


def _translate(statement):
    text = str(statement)
    upper = text.strip().upper()
    if upper.startswith("INSERT OR REPLACE INTO SETTINGS"):
        text = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", text, count=1, flags=re.I)
        text = text.rstrip().rstrip(";") + " ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    elif upper.startswith("INSERT OR REPLACE INTO ROLL_RULE_VALUES"):
        text = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", text, count=1, flags=re.I)
        text = text.rstrip().rstrip(";") + " ON CONFLICT(era_id,roll_type) DO UPDATE SET die=excluded.die,bad_results=excluded.bad_results,notes=excluded.notes"
    elif upper.startswith("INSERT OR IGNORE INTO"):
        text = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", text, count=1, flags=re.I)
        text = text.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    text = re.sub(r"\bBLOB\b", "BYTEA", text, flags=re.I)
    return text.replace("?", "%s")


class Cursor:
    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def description(self):
        return self._cursor.description

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def _columns(self):
        return [item.name for item in self._cursor.description] if self._cursor.description else []

    def fetchone(self):
        row = self._cursor.fetchone()
        return None if row is None else HybridRow(self._columns(), row)

    def fetchall(self):
        columns = self._columns()
        return [HybridRow(columns, row) for row in self._cursor.fetchall()]

    def __iter__(self):
        columns = self._columns()
        for row in self._cursor:
            yield HybridRow(columns, row)

    def close(self):
        self._cursor.close()

    def execute(self, statement, parameters=()):
        self._cursor.execute(_translate(statement), tuple(parameters or ()))
        return self

    def executemany(self, statement, sequence):
        self._cursor.executemany(_translate(statement), sequence)
        return self


class Connection:
    def __init__(self, raw, schema_name):
        self._raw = raw
        self.schema_name = schema_name
        from psycopg import sql

        with raw.cursor() as cursor:
            cursor.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema_name)))

    def execute(self, statement, parameters=()):
        cursor = self._raw.cursor()
        cursor.execute(_translate(statement), tuple(parameters or ()))
        return Cursor(cursor)

    def executemany(self, statement, sequence):
        cursor = self._raw.cursor()
        cursor.executemany(_translate(statement), sequence)
        return Cursor(cursor)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def cursor(self):
        return Cursor(self._raw.cursor())


def connect():
    record = save_manager.active_save()
    if not record:
        raise RuntimeError("No Neon save exists yet.")
    return Connection(storage.raw_connect(), record["schema_name"])


def setting(connection, key, default=None):
    row = connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(connection, key, value):
    connection.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    connection.commit()
    save_manager.touch_active()


def next_id(connection, table, column, prefix, width=4):
    values = [row[0] for row in connection.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE ?", (prefix + "-%",))]
    numbers = []
    for value in values:
        try:
            numbers.append(int(value.rsplit("-", 1)[1]))
        except Exception:
            pass
    return f"{prefix}-{max(numbers, default=0) + 1:0{width}d}"
