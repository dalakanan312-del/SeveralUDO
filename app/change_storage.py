"""Lossless journal compression. IDs, versions, cursors and every change survive."""
import base64
import hashlib
import json
import zlib
from sqlalchemy import JSON, Text, cast, select, update
from sqlalchemy.types import TypeDecorator

MARKER = '__decades_change_zlib_v1__'
MAX_BYTES = 150_000_000


def encode(value):
    if not isinstance(value, dict) or MARKER in value:
        return value
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(raw) < 2048 or len(raw) > MAX_BYTES:
        return value
    packed = base64.b64encode(zlib.compress(raw, 6)).decode('ascii')
    if len(packed) + 150 >= len(raw):
        return value
    return {MARKER: packed, 'sha256': hashlib.sha256(raw).hexdigest()}


def decode(value):
    if not isinstance(value, dict) or MARKER not in value:
        return value
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(base64.b64decode(value[MARKER], validate=True), MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or not decoder.eof or decoder.unused_data or hashlib.sha256(raw).hexdigest() != value['sha256']:
            raise ValueError()
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (KeyError, ValueError, TypeError, zlib.error) as exc:
        raise ValueError('A compressed history entry is damaged. Restore the database backup; history was not discarded.') from exc


class JournalJSON(TypeDecorator):
    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encode(value)

    def process_result_value(self, value, dialect):
        return decode(value)


def compact_batch(session, save_id, after=0, limit=250):
    """Explicit maintenance only. A bounded transaction; safe to retry any batch."""
    from .models import Change
    rows = list(session.execute(select(Change.sequence, cast(Change.payload, Text)).where(
        Change.save_id == save_id, Change.sequence > after).order_by(Change.sequence).limit(min(1000,max(1,limit)))))
    changed = saved = 0
    for sequence, raw in rows:
        original = json.loads(raw)
        packed = encode(original)
        if packed != original:
            # Bind the logical value; JournalJSON handles storage encoding once.
            session.execute(update(Change).where(Change.sequence == sequence, Change.save_id == save_id)
                            .values(payload=original).execution_options(synchronize_session=False))
            changed += 1
            saved += max(0, len(raw.encode('utf-8'))-len(json.dumps(packed).encode('utf-8')))
    return {'scanned': len(rows), 'compressed': changed, 'saved_bytes': saved,
            'cursor': rows[-1][0] if rows else after}
