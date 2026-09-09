"""Evidence-based play presentation; does not invent links or rewrite history."""
from collections import Counter
from datetime import datetime, timezone
from functools import wraps
from sqlalchemy import select, or_, and_, func
from sqlalchemy.orm import object_session
from .models import Record, ChronicleSave, ClockReceipt
from . import domain, insights, core_rulesets, infinite_decades


def number(value):
    try: return int(value)
    except (ValueError, TypeError): return None


def maternal(data):
    return 'maternal' in str(data.get('roll_type') or '').casefold() or data.get('maternal_baby_index') is not None


def pregnancy_ref(row):
    d = row.data or {}
    if row.kind == 'pregnancy': return row.id
    if row.kind == 'game_candidate': return (d.get('payload') or {}).get('pregnancy_id')
    return d.get('pregnancy_id') or (d.get('source_id') if maternal(d) else None)


def babies(session, save_id, pregnancy_id):
    return list(session.scalars(select(Record).where(Record.save_id == save_id, Record.kind == 'sim',
        Record.deleted.is_(False), Record.data['pregnancy_id'].as_string() == pregnancy_id)
        .order_by(Record.created_at, Record.id)))


def roll_presentation(save, row, person=None):
    d = row.data or {}; session = object_session(row)
    who = person.label if person else d.get('sim_name') or 'This Sim'
    title = row.label; calculation = ''; warning = ''
    if maternal(d):
        index = max(1, number(d.get('maternal_baby_index')) or 1)
        children = babies(session, save.id, pregnancy_ref(row)) if session and pregnancy_ref(row) else []
        explicit = d.get('maternal_baby_id') or d.get('baby_id')
        child = next((c for c in children if c.id == explicit), None)
        # Indexed delivery order is stable; never infer a twin by name/age alone.
        if child is None and index <= len(children): child = children[index-1]
        title = f"{who}'s childbirth survival — delivering {child.label if child else 'baby '+str(index)}"
    elif str(d.get('source') or '').startswith('aging:') or d.get('lifecycle_age_days') is not None or str(d.get('roll_type') or '').casefold() in domain.AGING_STAGE_OFFSETS:
        birth = number((person.data or {}).get('birth_global_day', person.global_day)) if person else None
        offset = number(d.get('lifecycle_age_days'))
        source_rule = None
        if session:
            rid = str(d.get('source') or '').split(':')[-1]
            source_rule = session.get(Record, rid)
            if source_rule and (source_rule.save_id != save.id or source_rule.kind!='roll_rule'):source_rule=None
            if source_rule is None:
                choices=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='roll_rule',
                    Record.deleted.is_(False),Record.label==str(d.get('roll_type') or ''))))
                due_year=save.start_year+((row.global_day or save.global_day)-1)//max(1,save.days_per_year)
                source_rule=next((r for r in choices if core_rulesets.applies_to_selected_core(save,r)
                    and r.data.get('active',True) and int(r.data.get('start_year',-9999))<=due_year<=int(r.data.get('end_year',9999))),None)
        if offset is None:
            if source_rule:offset=domain.lifecycle_rule_age(save,source_rule)
            elif str(d.get('roll_type') or '').casefold() in domain.AGING_STAGE_OFFSETS:
                offset=domain.lifecycle_age_days(save,domain.AGING_STAGE_OFFSETS[str(d['roll_type']).casefold()])
        if birth is not None and offset is not None:
            calculation = f"{d.get('roll_type') or 'Aging'} · GD {row.global_day} · Birth GD {birth} + {offset} day{'s' if offset != 1 else ''}"
            rd = source_rule.data if source_rule else {}
            standard = number(rd.get('standard_age_days'))
            if standard is None: standard = domain.AGING_STAGE_OFFSETS.get(str(d.get('roll_type') or '').casefold())
            calendar = number(d.get('age_calendar_days_per_year')) or save.days_per_year
            if standard is not None and domain.scale_age_days(standard, 4, calendar) == offset:
                calculation += f" · {standard} base days × {calendar}/4 = {offset} days"
            else: calculation += ' · Uses the configured age table'
            if row.global_day != birth + offset: warning = f"Date mismatch: this rule calculates GD {birth+offset}. Refresh pending rolls to review."
            if calendar != save.days_per_year and not d.get('completed'): warning = 'Scheduled under an older calendar. Refresh pending rolls to review.'
        else:
            calculation=f"{d.get('roll_type') or 'Aging'} · GD {row.global_day} · Birth day or source age is missing; calculation cannot be verified."
    evidence = d.get('why_evidence') or {}
    event_context = d.get('rule_context') or d.get('historical_context') or ''
    # Context only belongs on the roll if an actual event supplied it.
    return {'title': title, 'calculation': calculation, 'warning': warning,
            'context': event_context if d.get('event_id') or d.get('event_rule_id') else '',
            'rule_name': d.get('roll_type') or row.label}


def life_schedule(session, save, sim):
    birth = number(sim.data.get('birth_global_day', sim.global_day))
    if birth is None: return []
    rules = [r for r in session.scalars(select(Record).where(Record.save_id == save.id,
        Record.kind == 'roll_rule', Record.deleted.is_(False))) if r.data.get('active', True) and core_rulesets.applies_to_selected_core(save, r)]
    rolls = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == 'roll',
        Record.deleted.is_(False), Record.data['sim_id'].as_string() == sim.id)))
    current = insights.life_stage(sim, save.global_day, save)
    stages = []; seen = set(); exempt = domain.occult_aging_profile(sim)['ordinary_aging_exempt']
    for rule in sorted(rules, key=lambda r: domain.lifecycle_rule_age(save, r) if domain.lifecycle_rule_age(save, r) is not None else 10**9):
        age = domain.lifecycle_rule_age(save, rule)
        if age is None: continue
        due = birth + age; year = save.start_year + (due-1)//max(1, save.days_per_year)
        if not int(rule.data.get('start_year', -9999)) <= year <= int(rule.data.get('end_year', 9999)): continue
        if (rule.label, due) in seen: continue
        seen.add((rule.label, due))
        matches = [r for r in rolls if r.data.get('source') == f'aging:{sim.id}:{rule.id}' or (r.data.get('roll_type') == rule.label and r.global_day == due)]
        completed = [r for r in matches if r.data.get('completed')]
        status = '; '.join(str(r.data.get('outcome') or 'Completed') for r in completed) if completed else 'Pending' if matches else 'Exempt' if exempt else 'Not scheduled'
        death = number(sim.data.get('death_global_day'))
        if death is not None and death <= due and not completed: status = 'After death · not required'
        stages.append({'stage': rule.label, 'day': due, 'offset': age, 'status': status,
            'current': rule.label.casefold() == current.casefold() or (rule.label.casefold()=='being born' and current.casefold()=='newborn'), 'next': False})
    future = [s for s in stages if s['day'] > save.global_day and not s['status'].startswith('After death')]
    if future: future[0]['next'] = True
    return stages


def household_predicate(save, household_id):
    members = select(Record.id).where(Record.save_id == save.id, Record.kind == 'sim', Record.deleted.is_(False),
        func.coalesce(Record.data['current_household_id'].as_string(), Record.data['household_id'].as_string()) == household_id).correlate(None)
    d = Record.data; p = d['payload']
    return or_(Record.id.in_(members), *[d[k].as_string().in_(members) for k in ('sim_id', 'mother_id', 'father_id', 'partner1_id', 'partner2_id')],
        d['household_id'].as_string() == household_id, *[p[k].as_string().in_(members) for k in ('sim_id','mother_id','father_id','partner1_id','partner2_id')],
        p['tracker_household_id'].as_string() == household_id,p['inferred_household_id'].as_string() == household_id,p['inferred_tracker_household_id'].as_string() == household_id)


def birth_panels(session, save, rows, section='decisions', window='today'):
    """Expand only deliveries represented in this bounded page, not all births."""
    refs = {pregnancy_ref(r) for r in rows if pregnancy_ref(r)}
    actor_ids = {r.data.get('sim_id') for r in rows if r.kind == 'roll' and not maternal(r.data)}
    actors = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == 'sim', Record.id.in_(actor_ids)))) if actor_ids else []
    for sim in actors:
        if sim.data.get('pregnancy_id') and sim.data.get('birth_global_day') == next((r.global_day for r in rows if r.data.get('sim_id') == sim.id), None): refs.add(sim.data['pregnancy_id'])
    panels = []; consumed = set()
    for pid in sorted(str(x) for x in refs if x):
        pregnancy = session.get(Record, pid)
        if not pregnancy or pregnancy.save_id != save.id or pregnancy.kind != 'pregnancy' or pregnancy.deleted: continue
        children = babies(session, save.id, pid); child_ids = {c.id for c in children}
        day = number(pregnancy.data.get('actual_delivery_global_day'))
        if day is None and children: day = number(children[0].data.get('birth_global_day'))
        if day is None: continue # An ongoing pregnancy isn't a completed birth.
        related = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.deleted.is_(False), Record.kind == 'roll', or_(
            Record.data['source_id'].as_string() == pid, Record.data['pregnancy_id'].as_string() == pid,
            and_(Record.data['sim_id'].as_string().in_(child_ids), Record.global_day == day))).order_by(Record.global_day, Record.created_at, Record.id)))
        matching = {r.id for r in rows if r.id == pid or r.id in child_ids or r.id in {x.id for x in related} or pregnancy_ref(r) == pid}
        if not matching: continue
        mother = session.get(Record, pregnancy.data.get('mother_id')) if pregnancy.data.get('mother_id') else None
        if mother and mother.save_id != save.id: mother = None
        by_id = {s.id:s for s in children}
        if mother: by_id[mother.id] = mother
        def available(roll):
            if roll.data.get('completed') or roll.data.get('allow_after_death') or roll.data.get('occult_rule_key')=='ghost_persistence':return True
            person=by_id.get(roll.data.get('sim_id'))
            if person is None:return True
            death=number(person.data.get('death_global_day'))
            return not person.data.get('death_confirmed') and not person.data.get('game_was_dead') and (death is None or death>save.global_day)
        related=[r for r in related if available(r)]
        checks = []
        for index in range(1, max(len(children), number(pregnancy.data.get('babies_delivered')) or 1)+1):
            child = children[index-1] if index <= len(children) else None
            checks.append({'index':index,'child':child,'rolls':[r for r in related if
                (maternal(r.data) and ((child and (r.data.get('maternal_baby_id') or r.data.get('baby_id'))==child.id)
                 or (not (r.data.get('maternal_baby_id') or r.data.get('baby_id')) and (number(r.data.get('maternal_baby_index')) or 1)==index))) or (child and r.data.get('sim_id') == child.id)]})
        def in_window(value):
            if value is None:return False
            return value<save.global_day if window=='overdue' else value>save.global_day if window=='future' else value==save.global_day
        assigned={r.id for check in checks for r in check['rolls']}
        unassigned=[r for r in related if r.id not in assigned]
        pending=any(not r.data.get('completed') and in_window(r.global_day) for r in related)
        completed=any(r.data.get('completed') and in_window(r.data.get('completed_global_day')) for r in related)
        home='decisions' if pending else 'completed' if completed or in_window(day) else 'happening'
        # Every section removes its constituent birth rows, but only one shows
        # the delivery panel; this also keeps roll IDs unique in the document.
        if section==home:
            panels.append({'id':pid,'day':day,'title':', '.join(c.label for c in children) or pregnancy.label,
                'mother':mother,'checks':checks,'by_id':by_id,'unassigned':unassigned,'pending_candidates':[r for r in rows if r.kind=='game_candidate' and r.id in matching]})
        # Never hide a detected baby that still needs a review action.
        consumed.update(r.id for r in rows if r.id in matching and (r.kind!='game_candidate' or section==home))
    return panels, consumed


def branch_banner(session, save):
    meta = infinite_decades.state(save)
    branch = session.get(Record, meta.get('active_branch_id')) if meta.get('active_branch_id') else None
    if branch and branch.save_id != save.id: branch = None
    data = infinite_decades.metadata(branch) if branch else {}
    return {'save': save.name, 'branch': meta.get('branch_name') or 'Main family line',
        'checkpoint': data.get('game_save_name') or 'Game checkpoint not recorded',
        'ready': not meta or meta.get('game_ready') is True, 'enabled': bool(meta)}


def receipt_summary(function):
    @wraps(function)
    def wrapped(session, link, report, *args, **kwargs):
        old = (link.last_game_day, link.last_game_hour, link.last_game_minute)
        result = function(session, link, report, *args, **kwargs)
        if result.get('ok') and (result.get('game_time') or result.get('duplicate')):
            save = session.get(ChronicleSave, link.save_id)
            summary = {k:int(result.get(k) or 0) for k in ('new_candidates','illnesses_created','illnesses_ended','households_created','households_updated','household_members_linked','parent_links_updated','population_updates','rolls_created','profile_updates','portraits_updated','generations_updated')}
            summary['game_time_changed'] = old != (link.last_game_day, link.last_game_hour, link.last_game_minute)
            summary['automation_paused'] = bool(result.get('automation_paused'))
            summary['candidate_types'] = dict(Counter(result.get('candidate_types') or []))
            summary['duplicate'] = bool(result.get('duplicate'))
            receipt=session.get(ClockReceipt,save.id)
            if not receipt:receipt=ClockReceipt(save_id=save.id);session.add(receipt)
            receipt.received_at=datetime.now(timezone.utc);receipt.summary=summary
        return result
    return wrapped
