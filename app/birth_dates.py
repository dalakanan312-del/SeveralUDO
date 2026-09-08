"""Stable fictional birth-time fallbacks, never a replacement for recorded facts."""
import hashlib
from datetime import date, timedelta
from sqlalchemy import or_, select
from . import calendar_utils
from .models import Record

MARKER = 'randomized-within-challenge-day'
GENERATED_KEYS = ('birth_time_randomized', 'birth_randomized_time', 'birth_randomized_global_day')


def integer(value):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def historical_date(save, day, hour, minute):
    """Keep four-day quarters; proportionally partition other year lengths."""
    try:
        if save.days_per_year == 4:
            return calendar_utils.exact_historical_label(day, hour, minute, save.start_year, 4)
        year, part = calendar_utils.global_day_parts(day, save.start_year, save.days_per_year)
        first = date(year, 1, 1)
        days = (date(year + 1, 1, 1) - first).days
        per_year = max(1, int(save.days_per_year))
        offset = ((part - 1) * 1440 + hour * 60 + minute) * days // (per_year * 1440)
        return calendar_utils.format_historical_date(first + timedelta(days=min(days - 1, offset)))
    except (ValueError, OverflowError):
        # Fictional calendars may use year zero or years outside datetime's range.
        return ''


def fields_for(save, data, identity):
    day = integer(data.get('birth_global_day'))
    if day is None or not identity or data.get('birth_year_only') or data.get('infinite_frozen'):
        return {}
    first = integer(data.get('estimated_birth_global_day_range_start'))
    last = integer(data.get('estimated_birth_global_day_range_end'))
    if first is not None and last is not None and first != last:
        return {}  # A year/age range is not a known birth day.
    if str(data.get('birth_time') or '').strip() or str(data.get('historical_birth_date') or '').strip():
        return {}
    hour, minute = integer(data.get('birth_game_hour')), integer(data.get('birth_game_minute'))
    hour = hour if hour is not None and 0 <= hour < 24 else None
    minute = minute if minute is not None and 0 <= minute < 60 else None
    if hour is not None and minute is not None:
        return {}  # Exact numeric time exists, even if the display label is absent.
    # Record-keyed randomness makes concurrent requests/devices choose the same
    # fallback. Persist it too, so copying or restoring a Sim retains the choice.
    seed = hashlib.sha256(('birth-time-v1:'+str(identity)).encode()).digest()
    chosen = int.from_bytes(seed[:8], 'big') % 1440
    hour = hour if hour is not None else chosen // 60
    minute = minute if minute is not None else chosen % 60
    clock = f'{hour:02d}:{minute:02d}'
    previous_source = str(data.get('birth_time_source') or '').strip()
    explanation = 'Randomized within recorded Global Day; exact birth time was not recorded'
    result = {'birth_game_hour':hour, 'birth_game_minute':minute, 'birth_time':clock,
              'birth_time_randomized':True, 'birth_randomized_time':clock,
              'birth_randomized_global_day':day, 'birth_date_precision':MARKER,
              'birth_time_source':explanation + ('; '+previous_source if previous_source else ''),
              'historical_birth_date_range':calendar_utils.date_range_label(day, save.start_year, save.days_per_year)}
    label = historical_date(save, day, hour, minute)
    if label:
        result['historical_birth_date'] = label
    return result


def apply_to_record(record, save):
    from .domain import automation_enabled
    if (not save or record.kind != 'sim' or record.deleted
            or (record.data or {}).get('infinite_frozen') or not automation_enabled(save)):
        return False
    previous = record.data or {}
    if (previous.get('birth_time_randomized') and previous.get('birth_time')
            and previous.get('birth_randomized_time') != previous.get('birth_time')):
        updated = dict(previous)
        for key in GENERATED_KEYS:
            updated.pop(key, None)
        parts = str(updated['birth_time']).split(':')
        hour, minute = (integer(parts[0]), integer(parts[1])) if len(parts) >= 2 else (None, None)
        day = integer(updated.get('birth_global_day'))
        if day is not None and hour is not None and minute is not None and 0 <= hour < 24 and 0 <= minute < 60:
            updated.update(birth_game_hour=hour, birth_game_minute=minute)
            label = historical_date(save, day, hour, minute)
            if label:
                updated['historical_birth_date'] = label
        updated['birth_date_precision'] = 'exact' if updated.get('historical_birth_date') else 'clock-time-no-calendar-map'
        if 'Randomized within' in str(updated.get('birth_time_source') or ''):
            updated['birth_time_source'] = 'Recorded birth time replaced randomized fallback'
        record.data = updated
        return True
    fields = fields_for(save, record.data or {}, record.id)
    if not fields:
        return False
    record.data = {**(record.data or {}), **fields}
    return True


def fill_missing(session, save):
    """Backfill once per missing time; journal changes for backup/cloud sync."""
    from .domain import automation_enabled, journal
    if not save or not automation_enabled(save):
        return 0
    changed = 0
    rows = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == 'sim', Record.deleted.is_(False),
        or_(Record.data['birth_time'].as_string().is_(None), Record.data['birth_time'].as_string() == ''),
    )))
    for record in rows:
        base = record.version
        if apply_to_record(record, save):
            record.version += 1
            journal(session, record, 'upsert', base)
            changed += 1
    save.revision += changed
    return changed


def retain_edit_provenance(previous, updated, save):
    """An unrelated profile edit must not relabel a randomized time as exact."""
    if not previous.get('birth_time_randomized'):
        if (previous.get('historical_birth_date') and not updated.get('birth_time')
                and not updated.get('birth_year_only')
                and integer(previous.get('birth_global_day')) == integer(updated.get('birth_global_day'))):
            updated['historical_birth_date'] = previous['historical_birth_date']
            updated['birth_date_precision'] = previous.get('birth_date_precision', 'exact')
        return
    same_day = integer(previous.get('birth_global_day')) == integer(updated.get('birth_global_day'))
    same_time = previous.get('birth_randomized_time') == updated.get('birth_time')
    for key in GENERATED_KEYS:
        updated.pop(key, None)
    if same_day and same_time and not updated.get('birth_year_only'):
        updated.update({key:previous[key] for key in GENERATED_KEYS if key in previous})
        updated['birth_date_precision'] = MARKER
        updated['birth_time_source'] = previous.get('birth_time_source')
        label = historical_date(save, updated['birth_global_day'], updated['birth_game_hour'], updated['birth_game_minute'])
        if label:
            updated['historical_birth_date'] = label
    else:
        updated.pop('birth_time_source', None)
        if updated.get('birth_time'):
            updated['birth_time_source'] = 'Manually recorded birth time'
