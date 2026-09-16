"""Editable birth defaults based on the actual parents' recorded marriage."""
from sqlalchemy import select
from .models import Record

CLOSED = {'ended', 'divorced', 'annulled', 'separated', 'widowed', 'closed', 'inactive'}
PENDING = {'planned', 'pending', 'proposed', 'cancelled', 'canceled', 'scheduled'}
MARRIAGE = {'marriage', 'married', 'spouse', 'husband', 'wife'}


def integer(value):
    try:
        return int(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def suggestion(session, save, mother_id, father_id, birth_day):
    """A missing match means unknown, never automatically illegitimate."""
    day = integer(birth_day)
    parents = {str(mother_id or ''), str(father_id or '')}
    if not save or day is None or '' in parents or len(parents) != 2:
        return {}
    for rid in parents:
        sim = session.get(Record, rid)
        if not sim or sim.kind != 'sim' or sim.save_id != save.id:
            return {}
        if sim.deleted and not sim.data.get('infinite_frozen'):
            return {}
        death = integer(sim.data.get('death_global_day'))
        if death is not None and death < day:
            return {}
    rows = session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == 'relationship',
        Record.data['partner1_id'].as_string().in_(parents), Record.data['partner2_id'].as_string().in_(parents))
        .order_by(Record.global_day, Record.id))
    for row in rows:
        data = row.data or {}
        if {data.get('partner1_id'), data.get('partner2_id')} != parents:
            continue
        if row.deleted and not data.get('infinite_frozen'):
            continue
        frozen_day = integer(data.get('infinite_frozen_global_day'))
        if data.get('infinite_frozen') and (frozen_day is None or day > frozen_day):
            continue
        status = str(data.get('status') or 'Active').strip().casefold()
        kind = str(data.get('type') or '').strip().casefold()
        legal = str(data.get('legally_married') or '').casefold() in {'true', '1', 'yes'}
        if not (kind in MARRIAGE or legal) or status in PENDING:
            continue
        start = integer(data.get('start_global_day'))
        if start is None:
            start = integer(row.global_day)
        end = integer(data.get('end_global_day'))
        if start is None or start > day or (status in CLOSED and end is None):
            continue
        # Same-day widowhood may be a recorded childbirth death; other endings
        # do not prove the marriage was still in force at birth that day.
        if end is not None and (day > end or day == end and status != 'widowed'):
            continue
        return {'value': 'Legitimate', 'relationship_id': row.id,
                'source': f'Recorded parental marriage at birth (GD {day})'}
    return {}


def apply_default(session, save, data):
    result = dict(data)
    if str(result.get('legitimacy') or '').strip():
        return result
    # A midpoint used for spreadsheet imports is not an observed birth date.
    if result.get('birth_year_only') or result.get('birth_global_day_estimated'):
        return result
    inferred = suggestion(session, save, result.get('mother_id'), result.get('father_id'), result.get('birth_global_day'))
    result.pop('legitimacy_marriage_id', None)
    result.pop('legitimacy_source', None)
    if inferred:
        result.update(legitimacy=inferred['value'], legitimacy_source=inferred['source'],
                      legitimacy_marriage_id=inferred['relationship_id'])
    return result
