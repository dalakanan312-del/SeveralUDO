from __future__ import annotations

import json
import os
import threading
import weakref
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / ".neon_storage.json"
_POOLS = {}
_POOLS_LOCK = threading.RLock()
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
            broken=bool(getattr(self._connection,"closed",False) or getattr(self._connection,"broken",False))
            if not broken and not self._connection.autocommit:
                try:
                    self._connection.rollback()
                except Exception:
                    broken=True
            # Never return a dead Neon socket to the pool. More importantly,
            # cleanup must not hide the useful error raised by the query.
            try:
                self._pool.putconn(self._connection,close=broken)
            except Exception:
                try: self._connection.close()
                except Exception: pass
            self._returned = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __del__(self):
        # Last-resort protection for a request interrupted during a Streamlit
        # rerun. Normal callers should still close explicitly or use `with`.
        try:
            self.close()
        except Exception:
            pass


def reset_pool():
    """Discard pooled sockets after a transient database/network failure."""
    # Detach the pools atomically before closing them. Streamlit can run several
    # sessions in the same process, so another session may be checking out a
    # connection while one session is recovering from a Neon interruption.
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        try: pool.close()
        except Exception: pass


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

    last_error = None
    for attempt in range(2):
        try:
            # Pool creation and checkout must be one atomic operation relative
            # to reset_pool(); otherwise a concurrent recovery can close the
            # newly selected pool immediately before getconn().
            with _POOLS_LOCK:
                pool = _POOLS.get(dsn)
                if pool is None or getattr(pool, "closed", False):
                    pool = ConnectionPool(
                        conninfo=dsn,min_size=0,max_size=20,open=True,timeout=8,
                        check=ConnectionPool.check_connection,max_idle=60,max_lifetime=600,
                    )
                    _POOLS[dsn] = pool
                connection = pool.getconn(timeout=8)
            return _PooledConnection(pool, connection)
        except Exception as error:
            last_error = error
            # A stale pool can survive a Neon compute restart. Recreate it once
            # so an ordinary page load recovers instead of showing PoolTimeout.
            if error.__class__.__name__ not in {"PoolClosed", "PoolTimeout", "OperationalError"}:
                raise
            with _POOLS_LOCK:
                if _POOLS.get(dsn) is pool:
                    _POOLS.pop(dsn, None)
                    try:
                        pool.close()
                    except Exception:
                        pass
            if attempt:
                break

    # Under a burst of simultaneous Streamlit reruns every application-pool
    # slot can briefly be checked out.  The Neon URL already points at its
    # transaction pooler, so use one short-lived connection for this request
    # instead of crashing the whole page.  It is closed normally by db.Connection
    # and never gets returned to the saturated local pool.
    if last_error and last_error.__class__.__name__ == "PoolTimeout":
        import psycopg
        return psycopg.connect(dsn, connect_timeout=10, autocommit=autocommit)
    if last_error:
        raise last_error


def test_connection(dsn=None):
    import psycopg

    target = dsn or load_config().get("pooled_url")
    if not target:
        raise StorageNotConfigured("Neon storage has not been configured.")
    with psycopg.connect(target, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_user, version()")
            return cursor.fetchone()
