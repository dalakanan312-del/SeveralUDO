from __future__ import annotations

import json
import os
import weakref
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / ".neon_storage.json"
_POOLS = {}
_CONNECTION_SCHEMAS = weakref.WeakKeyDictionary()


class StorageNotConfigured(RuntimeError):
    pass


def _env_file_values():
    values = {}
    if not ENV_PATH.exists():
        return values
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _state():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


@lru_cache(maxsize=1)
def _load_config_cached():
    file_values = _env_file_values()
    state = _state()
    pooled = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("NEON_DATABASE_URL")
        or file_values.get("DATABASE_URL")
        or file_values.get("NEON_DATABASE_URL")
        or state.get("pooled_url")
    )
    direct = (
        os.environ.get("NEON_DIRECT_URL")
        or file_values.get("NEON_DIRECT_URL")
        or state.get("direct_url")
        or pooled
    )
    return {
        "pooled_url": pooled,
        "direct_url": direct,
        "active_save_id": state.get("active_save_id"),
        "source": "environment" if pooled and (os.environ.get("DATABASE_URL") or os.environ.get("NEON_DATABASE_URL") or file_values) else "local",
    }


def load_config():
    return dict(_load_config_cached())


def configured():
    return bool((load_config().get("pooled_url") or "").strip())


def save_config(pooled_url, direct_url=None, active_save_id=None):
    data = {
        "pooled_url": (pooled_url or "").strip(),
        "direct_url": (direct_url or pooled_url or "").strip(),
        "active_save_id": active_save_id,
    }
    STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _load_config_cached.cache_clear()
    return data


def update_active_save(save_id):
    state = _state()
    state["active_save_id"] = save_id
    # Never duplicate environment-provided credentials into the state file.
    if load_config().get("source") == "environment":
        state.pop("pooled_url", None)
        state.pop("direct_url", None)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    _load_config_cached.cache_clear()


class _PooledConnection:
    def __init__(self, pool, connection):
        self._pool = pool
        self._connection = connection
        self._returned = False

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def close(self):
        if not self._returned:
            if not self._connection.autocommit:
                self._connection.rollback()
            self._pool.putconn(self._connection)
            self._returned = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type:
            self._connection.rollback()
        self.close()


def ensure_search_path(connection, schema_name):
    raw = getattr(connection, "_connection", connection)
    if _CONNECTION_SCHEMAS.get(raw) == schema_name:
        return
    from psycopg import sql

    with raw.cursor() as cursor:
        cursor.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema_name))
        )
    raw.commit()
    _CONNECTION_SCHEMAS[raw] = schema_name


def raw_connect(use_direct=False, autocommit=False):
    config = load_config()
    dsn = (config.get("direct_url") if use_direct else config.get("pooled_url")) or config.get("pooled_url")
    if not dsn:
        raise StorageNotConfigured("Neon storage has not been configured.")
    if use_direct or autocommit:
        import psycopg
        return psycopg.connect(dsn, autocommit=autocommit)

    from psycopg_pool import ConnectionPool

    pool = _POOLS.get(dsn)
    if pool is None:
        pool = ConnectionPool(conninfo=dsn, min_size=1, max_size=8, open=True)
        _POOLS[dsn] = pool
    return _PooledConnection(pool, pool.getconn())


def test_connection(dsn=None):
    import psycopg

    target = dsn or load_config().get("pooled_url")
    if not target:
        raise StorageNotConfigured("Neon storage has not been configured.")
    with psycopg.connect(target, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_user, version()")
            return cursor.fetchone()
