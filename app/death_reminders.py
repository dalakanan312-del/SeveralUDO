"""Read-only reminders for the active save's unconfirmed scheduled deaths."""
from sqlalchemy import case, func, select
from . import infinite_decades
from .models import Record

LIMIT = 50
REVIEW_URL = '/p/today?view=tools&task=deaths&due=due'


def pending(session, save, user_id, clock_status=None):
    branch = infinite_decades.state(save)
    result = {
        'user_id': user_id, 'save_id': save.id, 'save_name': save.name,
        'branch_id': branch.get('active_branch_id', ''),
        'branch_name': branch.get('branch_name', ''),
        'epoch': str(branch.get('epoch', '')), 'global_day': int(save.global_day),
        'suppressed': bool(infinite_decades.frozen(save) or (clock_status or {}).get('recovery_required')),
        'today_count': 0, 'overdue_count': 0, 'items': [], 'review_url': REVIEW_URL,
    }
    if result['suppressed']:
        return result
    d = Record.data
    day = d['death_global_day'].as_integer()
    criteria = [Record.save_id == save.id, Record.kind == 'sim', Record.deleted.is_(False),
                d['infinite_frozen'].as_boolean().is_not(True),
                d['death_confirmed'].as_boolean().is_not(True),
                d['game_was_dead'].as_boolean().is_not(True), day <= save.global_day]
    counts = session.execute(select(
        func.count(), func.sum(case((day == save.global_day, 1), else_=0))
    ).select_from(Record).where(*criteria)).one()
    result['today_count'] = int(counts[1] or 0)
    result['overdue_count'] = int(counts[0] or 0) - result['today_count']
    # Project only the few reminder fields; never load large game-report JSON.
    rows = session.execute(select(Record.id, Record.label, day,
        d['cause_of_death'].as_string(), d['death_source_roll_id'].as_string()
    ).where(*criteria).order_by(case((day == save.global_day, 0), else_=1), day,
                              func.lower(Record.label), Record.id).limit(LIMIT))
    result['items'] = [
        {'id': row.id, 'name': row.label, 'global_day': row[2],
         'cause': row[3] or 'Cause not recorded', 'source_roll_id': row[4] or '',
         'profile_url': '/sims/' + row.id, 'review_url': REVIEW_URL + '#death-' + row.id}
        for row in rows
    ]
    return result
