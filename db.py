from __future__ import annotations

import re
from collections import defaultdict

import save_manager
import storage

_TABLE_REVISIONS = defaultdict(int)


def statement_tables(statement):
    """Return the ordinary table names referenced by a simple app query."""
    text = re.sub(r"\s+", " ", str(statement))
    names = re.findall(
        r"\b(?:FROM|JOIN|UPDATE|INTO|DELETE\s+FROM)\s+(?:[a-zA-Z_][\w]*\.)?([a-zA-Z_][\w]*)",
        text,
        flags=re.I,
    )
    return tuple(sorted({name.lower() for name in names}))


def cache_token(schema_name, tables=None):
    """Small hashable version token for only the data a view depends on."""
    selected = tuple(sorted(set(tables or ())))
    return tuple((table, _TABLE_REVISIONS[(schema_name, table)]) for table in selected)


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
    def __init__(self, cursor, connection):
        self._cursor = cursor
        self._connection = connection
        self._schema_name = connection.schema_name

    def _select_schema(self):
        # Neon/PgBouncer may assign a different server connection after each
        # commit. Select once per transaction rather than before every query.
        if self._connection._schema_selected_in_transaction:
            return
        from psycopg import sql

        self._cursor.execute(
            sql.SQL("SET LOCAL search_path TO {}, public").format(
                sql.Identifier(self._schema_name)
            )
        )
        self._connection._schema_selected_in_transaction = True

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
        self._select_schema()
        translated=_translate(statement)
        self._cursor.execute(translated, tuple(parameters or ()))
        command=translated.lstrip().split(None,1)[0].upper() if translated.strip() else ""
        if command in {"INSERT","UPDATE","DELETE"} and self._cursor.rowcount != 0:
            self._connection._changed = True
            self._connection._changed_tables.update(statement_tables(translated))
        return self

    def executemany(self, statement, sequence):
        self._select_schema()
        translated=_translate(statement)
        self._cursor.executemany(translated, sequence)
        command=translated.lstrip().split(None,1)[0].upper() if translated.strip() else ""
        if command in {"INSERT","UPDATE","DELETE"} and self._cursor.rowcount != 0:
            self._connection._changed = True
            self._connection._changed_tables.update(statement_tables(translated))
        return self


class Connection:
    def __init__(self, raw, schema_name):
        self._raw = raw
        self.schema_name = schema_name
        self._schema_selected_in_transaction = False
        self._changed = False
        self._changed_tables = set()
        storage.ensure_search_path(raw, schema_name)

    def execute(self, statement, parameters=()):
        return Cursor(self._raw.cursor(), self).execute(statement, parameters)

    def executemany(self, statement, sequence):
        return Cursor(self._raw.cursor(), self).executemany(statement, sequence)

    def commit(self):
        self._raw.commit()
        self._schema_selected_in_transaction = False
        changed=self._changed
        changed_tables=set(self._changed_tables)
        self._changed=False
        self._changed_tables.clear()
        if changed:
            for table in changed_tables:
                _TABLE_REVISIONS[(self.schema_name, table)] += 1
            save_manager.touch_active()

    def rollback(self):
        self._raw.rollback()
        self._schema_selected_in_transaction = False
        self._changed=False
        self._changed_tables.clear()

    def close(self):
        self._raw.close()

    def cursor(self):
        return Cursor(self._raw.cursor(), self)


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


def next_id(connection, table, column, prefix, width=4):
    values = [row[0] for row in connection.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE ?", (prefix + "-%",))]
    numbers = []
    for value in values:
        try:
            numbers.append(int(value.rsplit("-", 1)[1]))
        except Exception:
            pass
    return f"{prefix}-{max(numbers, default=0) + 1:0{width}d}"
