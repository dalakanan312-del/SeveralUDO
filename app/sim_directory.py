"""Paginated whole-dynasty Sims directory; inactive branches stay read-only."""
from sqlalchemy import and_, case, func, or_, select
from . import infinite_decades as dynasty
from .models import Record


def integer(value, default=1):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def page(session, save, params, size=48):
    state = dynasty.state(save)
    family = dynasty.branches(session, save) if state else []
    by_id = {b.id: b for b in family}
    active_id = state.get('active_branch_id')
    active = by_id.get(active_id)
    branch_choices = [{'id':'all', 'label':'All branches'},
                      {'id':'active', 'label':'Current branch' + (' · ' + active.label if active else '')}]
    branch_choices += [{'id':b.id,'label':b.label} for b in sorted(family,key=lambda b:b.label.casefold()) if b.id != active_id]
    branch_choices.append({'id':'unassigned','label':'No recorded branch'})
    branch = params.get('sim_branch', 'all') if state else 'all'
    if active_id and branch == active_id:
        branch = 'active'
    if branch not in {b['id'] for b in branch_choices} | set(by_id):
        branch = 'all'

    frozen = Record.data['infinite_frozen'].as_boolean().is_(True)
    conditions = [Record.save_id == save.id, Record.kind == 'sim',
                  or_(Record.deleted.is_(False), frozen) if state else Record.deleted.is_(False)]
    owner = Record.data['infinite_branch_id'].as_string()
    owner = case((or_(owner.is_(None), owner == ''),
                  case((frozen, 'unassigned'), else_=active_id or 'unassigned')), else_=owner)
    if state and branch != 'all':
        conditions.append(owner == (active_id if branch == 'active' else branch))

    query = params.get('q', '').strip()
    if query:
        conditions.append(Record.label.ilike(f'%{query}%'))
    status = params.get('record_status', 'all' if state else 'living')
    if status not in {'all', 'living', 'dead'}:
        status = 'all' if state else 'living'
    # A paused Sim's scheduled future death must not be evaluated against the
    # date of whichever other branch happens to be open.
    observed = case((frozen, func.coalesce(Record.data['infinite_frozen_global_day'].as_integer(), save.global_day)),
                    else_=save.global_day)
    death = Record.data['death_global_day'].as_integer()
    dead = or_(Record.data['death_confirmed'].as_boolean().is_(True),
               Record.data['game_was_dead'].as_boolean().is_(True),
               and_(death.is_not(None), death <= observed))
    if status == 'dead':
        conditions.append(dead)
    elif status == 'living':
        birth = func.coalesce(Record.data['birth_global_day'].as_integer(), Record.global_day, 1)
        conditions += [birth <= observed, ~dead]
    birth_order = func.coalesce(Record.data['birth_global_day'].as_integer(),
                               (Record.data['birth_year'].as_integer() - save.start_year) * save.days_per_year + 1)
    sort = params.get('sort','birth')
    ordering = (func.lower(Record.label), Record.id) if sort == 'name' else (
        (birth_order.desc() if sort == 'birth-newest' else birth_order.asc()).nullslast(), func.lower(Record.label), Record.id)
    count = session.scalar(select(func.count()).select_from(Record).where(*conditions)) or 0
    pages = max(1, (count + size - 1) // size)
    number = min(pages, max(1, integer(params.get('list_page'))))
    records = list(session.scalars(select(Record).where(*conditions).order_by(*ordering).offset((number-1)*size).limit(size)))
    cards = {}
    for sim in records:
        d = sim.data or {}
        preserved = bool(d.get('infinite_frozen'))
        bid = d.get('infinite_branch_id') or (None if preserved else active_id)
        cards[sim.id] = {'branch':by_id[bid].label if bid in by_id else 'No recorded branch',
                         'readonly':preserved or dynasty.frozen(save),
                         'preserved_day':d.get('infinite_frozen_global_day') if preserved else None}
    return records, {'list_page':number, 'list_pages':pages, 'list_count':count,
                     'list_q':query, 'list_status':status, 'sim_branch':branch,
                     'sim_branch_choices':branch_choices if state else [], 'sim_cards':cards}
