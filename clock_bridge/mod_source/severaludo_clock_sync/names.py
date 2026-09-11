"""Small, guarded name resolver; compatible with the game's Python 3.7.

LocalizedString.__str__ is a protobuf dump, NOT the displayed game text.
Use actual string tables when available and let the caller fall back to a
readable tuning name. No network, package-directory crawl or optional mod is
needed. Resource reads happen in bounded batches on the game's polling thread.
"""

import json
import pkgutil
import re
import struct
import time

STBL_TYPE = 0x220557DA
MAX_TABLE_BYTES = 4 * 1024 * 1024
MAX_LABELS = 60000
TABLES_PER_BATCH = 4
_HASH = re.compile(r"^(?:hash|localization(?:\s+key)?|string(?:\s+id)?)\s*[:#=]?\s*(-?(?:0x)?[0-9a-f]+)(?:\s|$)", re.I)
DISPLAY_FIELDS = (
    "display_name", "display_name_gender_neutral", "trait_name", "stat_name",
    "skill_name", "milestone_name", "career_name", "name",
)
_bundled = None
_native = {}
_resources = None
_resource_cursor = 0
_next_batch = 0


def safe_attr(owner, name):
    try:
        return getattr(owner, name, None)
    except Exception:
        return None


def localization_key(value):
    """Do not confuse a tuning guid64 with a 32-bit localization key."""
    if isinstance(value, bool) or value is None:
        return None
    raw = value if isinstance(value, int) else None
    for attr in ("hash", "_string_id", "localization_key"):
        candidate = safe_attr(value, attr)
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            raw = candidate
            break
    if isinstance(value, str):
        match = _HASH.match(value.strip())
        if match:
            raw = match.group(1)
        elif re.fullmatch(r"-?(?:0x[0-9a-f]+|[0-9]+)", value.strip(), re.I):
            raw = value.strip()
    try:
        if isinstance(raw, str):
            raw = int(raw, 16 if 'x' in raw.lower() or re.search('[a-f]', raw, re.I) else 10)
        return (int(raw) & 0xffffffff) or None if raw is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def readable_text(value):
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if (not text or len(text) > 500 or _HASH.match(text)
            or re.fullmatch(r'-?(?:0x[0-9a-f]+|[0-9]+)', text, re.I) or text.startswith('<')
            or ' object at ' in text or 'tokens {' in text or text.startswith('namespace(')
            or text in ('None', 'True', 'False')):
        return ""
    # Gender alternatives are text, not a reason to expose template syntax.
    text = re.sub(r'(?:\{(?:F|M)\d*\.[^}]+\})+',
                  lambda match: ' / '.join(dict.fromkeys(re.findall(r'\{(?:F|M)\d*\.([^}]+)\}', match.group(0)))), text)
    if re.search(r'\{[^}]*\}', text):
        return ""  # A token-dependent title requires context we do not have.
    return ' '.join(text.split())


def bundled_labels():
    global _bundled
    if _bundled is None:
        _bundled = {}
        try:
            raw = pkgutil.get_data(__package__, 'game_names.json')
            document = json.loads(raw.decode('utf-8')) if raw else {}
            for key, value in document.get('names', {}).items():
                key = localization_key(int(key))
                label = readable_text(value)
                if key and label:
                    _bundled[key] = label
        except Exception:
            pass  # A missing dictionary never stops clock/pregnancy reports.
    return _bundled


def read_stbl(raw):
    """Read uncompressed bytes supplied by EA's ResourceLoader."""
    if not isinstance(raw, bytes) or not 21 <= len(raw) <= MAX_TABLE_BYTES or raw[:4] != b'STBL':
        return {}
    count = struct.unpack_from('<Q', raw, 7)[0]
    if count > MAX_LABELS:
        return {}
    labels = {}
    cursor = 21
    for _ in range(count):
        if cursor + 7 > len(raw):
            return {}  # Do not trust a partially corrupt resource.
        key, _flags, size = struct.unpack_from('<IBH', raw, cursor)
        cursor += 7
        if cursor + size > len(raw):
            return {}
        if size <= 500:
            label = readable_text(raw[cursor:cursor + size].decode('utf-8', errors='replace'))
            if key and label:
                labels[key] = label
        cursor += size
    return labels


def advance_resources():
    """Incrementally cache loaded English mod strings, never during import.

    English matches the tracker's rule classifiers. Some game builds expose
    only custom STBLs through this API; bundled labels/tuning names cover the
    remainder. Missing packs or unavailable resources are normal, not errors.
    """
    global _resources, _resource_cursor, _next_batch
    now = time.monotonic()
    if now < _next_batch:
        return
    _next_batch = now + 10
    try:
        import sims4.resources as resources
        if _resources is None:
            keys = resources.get_all_resources_of_type(STBL_TYPE)
            _resources = sorted(
                (key for key in keys if (int(key.instance) >> 56) == 0),
                key=lambda key: (int(key.group), int(key.instance)),
            )
        for _ in range(TABLES_PER_BATCH):
            if _resource_cursor >= len(_resources) or len(_native) >= MAX_LABELS:
                break
            key = _resources[_resource_cursor]
            _resource_cursor += 1
            try:
                stream = resources.ResourceLoader(key).load()
                if stream is None:
                    continue
                try:
                    labels = read_stbl(stream.read(MAX_TABLE_BYTES + 1))
                finally:
                    stream.close()
                for string_id, label in labels.items():
                    if string_id in _native or len(_native) < MAX_LABELS:
                        _native[string_id] = label
            except Exception:
                continue
            if time.monotonic() - now > .025:
                break
    except Exception:
        pass  # Try again on a later poll if game services are not ready.


def display_values(value):
    for attr in DISPLAY_FIELDS:
        candidate = safe_attr(value, attr)
        if candidate is None:
            continue
        # The native factory exposes _string_id; calling it loses information
        # and can require tokens, so prefer reading the factory directly.
        if callable(candidate) and localization_key(candidate) is None:
            try:
                candidate = candidate()
            except Exception:
                continue
        yield candidate
    yield value


def resolve_display(value):
    first_key = None
    for candidate in display_values(value):
        key = localization_key(candidate)
        if key:
            first_key = first_key or key
            label = _native.get(key) or bundled_labels().get(key)
            if label:
                return label, key, 'string-table'
        else:
            label = readable_text(candidate)
            if label:
                return label, first_key, 'game-text'
    return '', first_key, 'unresolved'


def diagnostics():
    return {
        'version': 1,
        'bundled_labels': len(bundled_labels()),
        'loaded_resource_labels': len(_native),
        'remaining_string_tables': max(0, len(_resources or ()) - _resource_cursor),
    }
