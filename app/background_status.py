"""Small, non-secret operational status; never store credentials or report payloads."""
from datetime import datetime, timezone
from threading import Lock

_lock = Lock()
_states = {}


def record(name, state, message):
    with _lock:
        _states[name] = {'name': name, 'state': state, 'message': message,
                         'checked_at': datetime.now(timezone.utc).isoformat()}


def snapshot():
    with _lock:
        return [dict(v) for v in _states.values()]
