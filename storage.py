from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / ".neon_storage.json"
_POOLS = {}


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


def load_config():
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


def configured():
    return bool((load_config().get("pooled_url") or "").strip())


def save_config(pooled_url, direct_url=None, active_save_id=None):
    data = {
        "pooled_url": (pooled_url or "").strip(),
        "direct_url": (direct_url or pooled_url or "").strip(),
        "active_save_id": active_save_id,
    }
    STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def update_active_save(save_id):
    state = _state()
    state["active_save_id"] = save_id
    # Never duplicate environment-provided credentials into the state file.
    if load_config().get("source") == "environment":
        state.pop("pooled_url", None)
        state.pop("direct_url", None)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


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
