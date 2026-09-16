"""Explicit, reviewed dynasty operations and inexpensive read-only play aids.

Management records live outside rewound branch timelines. No UI projection here
changes a birth date, completes a roll, or simulates an unplayed family.
"""
import copy
import hashlib
import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from sqlalchemy import select
from . import infinite_dynasty as d
from .models import Record, ActionPreview, ClockLink, Portrait, uid

KIND = 'dynasty_tool'
CORRECTION_FIELDS = {'first_name', 'last_name', 'birthplace', 'mother_id', 'father_id', 'legitimacy'}


def integer(value, default=None):
    try: return int(value)
    except (TypeError, ValueError): return default


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()


def records(session, save, kinds=None):
    query = select(Record).where(Record.save_id == save.id)
    if kinds: query = query.where(Record.kind.in_(kinds))
    return list(session.scalars(query))


def branch(session, save, branch_id):
    row = session.get(Record, branch_id)
    if not row or row.save_id != save.id or row.kind != d.KIND:
        raise ValueError('Choose a branch from this dynasty.')
    return row


def point(session, save, row):
    if row.id == d.state(save).get('active_branch_id') and not d.frozen(save):
        # Read-only views need no portrait bytes. The actual switch still saves them.
        payload = d.snapshot(session, save, include_portraits=False)
        payload.pop('portraits', None)
        return payload
    return d.unpack_snapshot(row.data['snapshot'])


def members(payload):
    ids = set(payload['member_sim_ids'])
    return [r for r in payload['records'] if r['kind'] == 'sim' and r['id'] in ids and not r.get('deleted')]


def tool_record(session, save, feature, **data):
    row = Record(id=uid(), save_id=save.id, kind=KIND, label=feature.replace('_', ' ').title(),
                 global_day=save.global_day, data={}, version=0, deleted=False)
    session.add(row)
    d._touch(session, row, {'feature': feature, 'at': datetime.now(timezone.utc).isoformat(), **data})
    return row


def queue(session, save, order='oldest'):
    result = []
    for row in d.branches(session, save):
        meta = d.metadata(row)
        if meta.get('status') == 'archive': continue
        day = save.global_day if row.id == d.state(save).get('active_branch_id') else meta.get('current_global_day', 1)
        names = meta.get('seed_names') or [row.label]
        result.append({'branch': row, 'meta': meta, 'day': day, 'year': d.year(save, day),
                       'surname': names[0].split()[-1].casefold(), 'founders': ', '.join(names)})
    if order == 'surname': key = lambda r: (r['surname'], r['branch'].label.casefold())
    elif order == 'custom': key = lambda r: (integer(r['meta'].get('queue_order'), 9999), r['day'], r['branch'].label)
    else: key = lambda r: (r['meta'].get('status') not in {'waiting', 'paused'}, r['day'], r['branch'].label)
    return sorted(result, key=key)


def expected_stage(save,payload,entry):
    from .insights import life_stages
    from .domain import lifecycle_rule_age
    from .core_rulesets import applies_to_selected_core
    starts=dict(life_stages(save))
    aliases={key.replace(' ','').casefold():key for key in starts}
    aliases.update({'beingborn':'Newborn','birth':'Newborn','baby':'Newborn','elderdeath-agerng':'Elder'})
    rule_save=SimpleNamespace(settings=payload.get('settings',save.settings))
    birth=integer(entry['data'].get('birth_global_day'))
    for rule in payload['records']:
        if rule['kind']!='roll_rule' or rule.get('deleted') or not rule['data'].get('active',True): continue
        obj=SimpleNamespace(**rule)
        if not applies_to_selected_core(rule_save,obj): continue
        age=lifecycle_rule_age(save,obj);label=aliases.get(rule['label'].replace(' ','').casefold())
        if age is None or label is None:continue
        due_year=d.year(save,birth+age)
        if integer(rule['data'].get('start_year'),-9999)<=due_year<=integer(rule['data'].get('end_year'),9999):starts[label]=age
    age=payload['global_day']-birth
    return next((label for label,minimum in sorted(starts.items(),key=lambda pair:pair[1],reverse=True) if age>=minimum),'Not born')


def resume_summary(session, save, row):
    from .domain import occult_aging_profile
    payload = point(session, save, row); day = payload['global_day']
    live = {r['id']: r for r in members(payload)}
    rows = [r for r in payload['records'] if not r.get('deleted')]
    pending = [r for r in rows if r['kind'] == 'roll' and not r['data'].get('completed')]
    pregnancies = [r for r in rows if r['kind'] == 'pregnancy' and not r['data'].get('delivered') and
                   str(r['data'].get('status', '')).casefold() not in {'delivered', 'ended', 'miscarriage', 'miscarried', 'stillbirth', 'cancelled', 'canceled', 'closed', 'complete', 'completed'}]
    history = sorted((r for r in rows if r['kind'] in {'story_entry','death','migration','relationship','game_history'} and
                      r.get('global_day') is not None and r['global_day'] <= day), key=lambda r: (r['global_day'], r['label']), reverse=True)
    birthdays = sorted((r for r in pending if r.get('global_day') is not None and r['global_day'] >= day and
                        (str(r['data'].get('source', '')).startswith('aging:') or 'aging' in r['label'].casefold())), key=lambda r:r['global_day'])
    observations = {r.id:r for r in records(session, save, {'sim'})}
    mismatches = []
    for sid, entry in live.items():
        data = entry['data']; observed = observations.get(sid)
        if data.get('birth_global_day') is None or data.get('death_confirmed') or data.get('game_was_dead') or (integer(data.get('death_global_day'), day+1) <= day): continue
        if occult_aging_profile(SimpleNamespace(data=data))['ordinary_aging_exempt']: continue
        expected = expected_stage(save,payload,entry)
        actual = str((observed.data if observed else data).get('game_age_stage') or '').replace('Age.', '').replace('_', ' ').strip()
        norm = lambda s: s.replace(' ', '').casefold().replace('baby', 'newborn')
        # Preteen is a challenge subdivision of the game's child stage.
        if actual and norm(actual) != 'unknown' and norm(expected) != norm(actual) and not (expected == 'Preteen' and norm(actual) == 'child'):
            mismatches.append({'name':entry['label'], 'expected':expected, 'actual':actual})
    return {'day':day, 'year':d.year(save, day), 'pending':len(pending), 'pregnancies':pregnancies,
            'birthdays':birthdays[:8], 'history':history[:6], 'mismatches':mismatches,
            'notes':d.metadata(row).get('handover_notes', ''), 'goal':goal_status(save, row, payload)}


def goal_status(save, row, payload):
    goal = d.metadata(row).get('play_goal') or {}
    if not goal: return None
    day = payload['global_day']; start = integer(goal.get('start_day'), day)
    living = members(payload); target = integer(goal.get('target'), 1)
    mode = goal.get('kind'); reached = False; evidence = ''
    if mode == 'year': reached = d.year(save, day) >= target; evidence = f'Year {d.year(save, day)} / {target}'
    elif mode == 'generation':
        generation = max((integer(r['data'].get('generation'), 0) for r in living if integer(r['data'].get('birth_global_day'), day+1) <= day), default=0)
        reached = generation >= target; evidence = f'Highest recorded generation {generation} / {target}'
    elif mode == 'birth':
        births = [r for r in living if r['id'] not in set(goal.get('baseline_ids',[])) and start <= integer(r['data'].get('birth_global_day'), -1) <= day]
        reached = bool(births); evidence = ', '.join(r['label'] for r in births) or 'Waiting for a recorded birth after this goal was set'
    elif mode == 'marriage':
        ids = {r['id'] for r in living}
        marriages = [r for r in payload['records'] if not r.get('deleted') and r['kind']=='relationship' and
            r['id'] not in set(goal.get('baseline_ids',[])) and start <= integer(r.get('global_day'), -1) <= day and
            (r['data'].get('legally_married') or str(r['data'].get('type','')).casefold() in {'marriage','married','spouse'}) and
            {r['data'].get('partner1_id'),r['data'].get('partner2_id')} & ids]
        reached = bool(marriages); evidence = ', '.join(r['label'] for r in marriages) or 'Waiting for a recorded marriage after this goal was set'
    return {**goal, 'reached':reached, 'evidence':evidence}


def save_preferences(session, save, branch_id, form):
    d._lock(session, save); row = branch(session, save, branch_id)
    mode = str(form.get('goal_kind') or '')
    if mode not in {'', 'year','birth','marriage','generation'}: raise ValueError('Choose a supported play-until goal.')
    target = integer(form.get('goal_target'))
    if mode in {'year','generation'} and (target is None or not -9999 <= target <= 9999 or (mode=='generation' and target<1)):
        raise ValueError('Enter a valid target year or generation.')
    old = d.metadata(row).get('play_goal') or {}
    day = save.global_day if row.id == d.state(save).get('active_branch_id') else d.metadata(row)['current_global_day']
    baseline=[r['id'] for r in point(session,save,row)['records'] if r['kind'] in {'sim','relationship'}] if mode in {'birth','marriage'} else []
    goal = {'kind':mode,'target':target,'start_day':day,'baseline_ids':baseline} if mode else {}
    if old.get('kind')==mode and old.get('target')==target: goal = old
    with d.branch_operation(session, save):
        d._update_branch(session, row, handover_notes=str(form.get('notes') or '')[:8000],
                         queue_order=max(0,min(9999,integer(form.get('queue_order'),9999))), play_goal=goal)


def correction_plan(session, save, args):
    field = str(args.get('field') or ''); value = str(args.get('value') or '').strip()
    if field not in CORRECTION_FIELDS: raise ValueError('That field is not a safe historical correction.')
    sim = session.get(Record, args.get('sim_id'))
    if not sim or sim.save_id!=save.id or sim.kind!='sim': raise ValueError('Choose a Sim from this dynasty.')
    if len(value)>240: raise ValueError('Keep the correction under 240 characters.')
    if field in {'mother_id','father_id'}:
        parent = session.get(Record, value) if value else None
        if value and (not parent or parent.save_id!=save.id or parent.kind!='sim'): raise ValueError('Choose a parent in this dynasty.')
        value = value or None
        people = {r.id:r.data for r in records(session, save, {'sim'})}
        seen=set(); frontier=[value] if value else []
        while frontier:
            sid=frontier.pop()
            if sid==sim.id: raise ValueError('A Sim cannot be their own ancestor.')
            if sid in seen: continue
            seen.add(sid); data=people.get(sid,{})
            frontier.extend(x for x in [data.get('mother_id'),data.get('father_id'),*(data.get('parent_ids') or [])] if x)
        if value and sim.data.get('father_id' if field=='mother_id' else 'mother_id')==value:
            raise ValueError('The two parent slots cannot contain the same Sim.')
    if field=='legitimacy' and value not in {'','Legitimate','Illegitimate','Unknown'}:
        raise ValueError('Choose Legitimate, Illegitimate, Unknown, or leave it blank.')
    changes=[]
    def add(data, label, where, branch_id=None):
        before={'data':copy.deepcopy(data),'label':label}; after=copy.deepcopy(before)
        after['data'][field]=value
        if field in {'mother_id','father_id'} and 'parent_ids' in data:
            after['data']['parent_ids']=list(dict.fromkeys([x for x in data['parent_ids'] if x!=data.get(field)]+([value] if value else [])))
        if field in {'first_name','last_name'}:
            after['label']=' '.join(str(after['data'].get(k) or '').strip() for k in ('title','first_name','last_name','suffix')).strip()
            if not after['label']: raise ValueError('The Sim must still have a name.')
        if before!=after:
            keys={key for key in before['data'].keys()|after['data'].keys() if (key in before['data'],before['data'].get(key))!=(key in after['data'],after['data'].get(key))}
            # Store/revalidate only reviewed fields, not unrelated game telemetry.
            before['data']={key:before['data'][key] for key in keys if key in before['data']}
            after['data']={key:after['data'][key] for key in keys if key in after['data']}
            changes.append({'sim_id':sim.id,'branch_id':branch_id,'where':where,'before':before,'after':after})
    add(sim.data,sim.label,'Current dynasty record')
    for row in d.branches(session,save):
        checkpoint=d.unpack_snapshot(row.data['snapshot'])
        if field in {'mother_id','father_id'} and value:
            ancestry={r['id']:r['data'] for r in checkpoint['records'] if r['kind']=='sim'}
            seen=set();frontier=[value]
            while frontier:
                sid=frontier.pop()
                if sid==sim.id: raise ValueError(f'That correction would make a parent cycle in {row.label}.')
                if sid in seen:continue
                seen.add(sid);data=ancestry.get(sid,{})
                frontier.extend(x for x in [data.get('mother_id'),data.get('father_id'),*(data.get('parent_ids') or [])] if x)
        for entry in checkpoint['records']:
            if entry['id']==sim.id and entry['kind']=='sim': add(entry['data'],entry['label'],row.label,row.id)
    return {'field':field, 'sim_id':sim.id, 'value':value, 'changes':changes,
            'effects':[f"{c['where']}: {c['before']['data'].get(field) or 'Unrecorded'} → {value or 'Unrecorded'}" for c in changes] +
                       ['Only this identity field is corrected. Birth dates and completed roll results stay unchanged.']}


def apply_corrections(session, save, changes, reverse=False):
    groups={}
    # Recheck every field before writing any, including checkpoint-specific values.
    for c in changes:
        row = branch(session,save,c['branch_id']) if c['branch_id'] else session.get(Record,c['sim_id'])
        if not row or row.save_id!=save.id: raise ValueError('A corrected record is no longer available.')
        payload=groups.setdefault(row.id, d.unpack_snapshot(row.data['snapshot'])) if c['branch_id'] else None
        obj=next((r for r in payload['records'] if r['id']==c['sim_id']),None) if payload else {'data':row.data,'label':row.label}
        old,new=(c['after'],c['before']) if reverse else (c['before'],c['after'])
        if not obj: raise ValueError('A checkpoint copy is no longer available.')
        changed={key for key in old['data'].keys()|new['data'].keys() if (key in old['data'],old['data'].get(key))!=(key in new['data'],new['data'].get(key))}
        if any((key in obj['data'],obj['data'].get(key))!=(key in old['data'],old['data'].get(key)) for key in changed) or (old['label']!=new['label'] and obj['label']!=old['label']):
            raise ValueError('This identity field changed again. Review a new correction; nothing was overwritten.')
        data=copy.deepcopy(obj['data'])
        for key in changed:
            if key in new['data']: data[key]=new['data'][key]
            else: data.pop(key,None)
        label=new['label'] if old['label']!=new['label'] else obj['label']
        if payload: obj.update(data=data,label=label)
        else: row.label=label; d._touch(session,row,data)
    for bid,payload in groups.items():
        row=session.get(Record,bid); names={r['id']:r['label'] for r in payload['records'] if r['kind']=='sim'}
        d._update_branch(session,row,payload,seed_names=[names.get(sid,'Unknown Sim') for sid in d.metadata(row).get('seed_sim_ids',[])])


def progress_stamp(session, save):
    # No private clock token, last-seen timestamp, or giant payload is retained.
    versions=list(session.execute(select(Record.id,Record.version).where(Record.save_id==save.id,
        Record.kind.not_in({KIND,'save_metadata','clock_diagnostic','clock_state','clock_protocol_state'})).order_by(Record.id)))
    return digest({'day':save.global_day,'epoch':d.state(save).get('epoch'),'records':[tuple(r) for r in versions]})


def switch_plan(session,save,args):
    target=branch(session,save,args.get('branch_id')); current=d.active_branch(session,save)
    if target.id==d.state(save).get('active_branch_id') or d.metadata(target).get('status') not in {'waiting','paused'}:
        raise ValueError('Choose a waiting or paused branch.')
    if not d.enabled(save): raise ValueError('Turn Infinite Decades on before switching branches.')
    summary=resume_summary(session,save,target)
    effects=[f"Pause {current.label if current else 'current branch'} at year {d.year(save)} / GD {save.global_day}.",
             f"Resume {target.label} at year {summary['year']} / GD {summary['day']}.",
             'The Sims game clock is unchanged. The next accepted report establishes the new tracker-day anchor.' if d.tracker_calendar(save)
             else 'Save the current game checkpoint and load the selected checkpoint; game reports stay blocked until confirmed.',
             f"{summary['pending']} pending rolls; {len(summary['pregnancies'])} open pregnancies. Completed history and shared albums are preserved."]
    for issue in summary['mismatches']: effects.append(f"Age check — {issue['name']}: tracker {issue['expected']}, last recorded game stage {issue['actual']}. Birth dates will not be rewritten.")
    return {'branch_id':target.id,'effects':effects,'summary':summary,'from_id':current.id if current else None,
            'from_day':save.global_day,'to_day':summary['day']}


def transfer_plan(session,save,args):
    source=d.active_branch(session,save); target=branch(session,save,args.get('branch_id'))
    if d.frozen(save) or not source or source.id==target.id or d.metadata(target).get('status') not in {'paused','waiting'}:
        raise ValueError('Transfer living members of the active branch to a waiting or paused branch.')
    payload=d.unpack_snapshot(target.data['snapshot'])
    if payload['global_day']!=save.global_day:
        raise ValueError(f"Bring both branches to the same tracker date first. Current GD {save.global_day}; {target.label} GD {payload['global_day']}. A transfer must not invent missing years or rewind a Sim.")
    _,chosen=d._selected(session,save,args.get('sim_ids',[]))
    selected=set(r.id for r in chosen)
    if any(not d.alive(r,save) or r.data.get('infinite_frozen') for r in chosen): raise ValueError('Only living members of the active branch can move.')
    active=[r for r in records(session,save,{'sim'}) if not r.deleted and d.alive(r,save)]
    if not any(r.id not in selected for r in active): raise ValueError('Leave a living Sim in the source branch, or finish that branch instead.')
    marriage=str(args.get('marriage') or '')=='yes'
    spouses=[str(args.get('partner1') or ''),str(args.get('partner2') or '')]
    union=selected|set(payload['member_sim_ids'])
    if marriage:
        if not all(spouses) or len(set(spouses))!=2 or not set(spouses)<=union or not set(spouses)&selected:
            raise ValueError('Choose two different partners in the receiving family, including at least one transferred Sim.')
        people={r.id:r for r in records(session,save,{'sim'})}
        preserved={r['id']:r for r in members(payload)}
        if any(not d.alive(SimpleNamespace(deleted=False,data=preserved[sid]['data']) if sid in preserved else people[sid],save) for sid in spouses):
            raise ValueError('Both partners must be alive at this date.')
        if not (set(spouses)&set(payload['member_sim_ids'])): raise ValueError('For a cross-branch marriage choose a partner already in the receiving branch.')
    effects=[f"Move {', '.join(r.label for r in chosen)} from {source.label} to {target.label} at GD {save.global_day}.",
             'The receiving branch tracks these Sims and any children you select. Future children follow the branch that records them.',
             'Original Sim IDs, parent links, portraits and completed results are retained; no duplicate Sims are created.']
    if marriage: effects.append('Record the selected marriage on this tracker day if it is not already recorded.')
    return {'source_id':source.id,'branch_id':target.id,'sim_ids':sorted(selected),'marriage':marriage,'spouses':spouses,
            'progress_stamp':progress_stamp(session,save),'effects':effects}


def apply_transfer(session,save,plan):
    source=branch(session,save,plan['source_id']); target=branch(session,save,plan['branch_id'])
    departing=set(plan['sim_ids'])
    incoming=d.snapshot(session,save,departing)
    remaining={r.id for r in records(session,save,{'sim'}) if not r.deleted and r.id not in departing}
    left=d.snapshot(session,save,remaining,include_candidates=True)
    dest=d.unpack_snapshot(target.data['snapshot']); byid={r['id']:r for r in dest['records']}
    leftids={r['id'] for r in left['records']}
    for entry in incoming['records']:
        # Shared settings/history are not replaced by the source branch's copy.
        owner=entry['data'].get('mother_id') if entry['kind']=='pregnancy' else entry['data'].get('sim_id')
        if entry['id'] not in byid or entry['id'] in departing or owner in departing: byid[entry['id']]=entry
        if entry['id'] not in leftids:
            row=session.get(Record,entry['id']); d._freeze(session,row,save.global_day,target.id)
    dest['records']=list(byid.values()); dest['member_sim_ids']=sorted(set(dest['member_sim_ids'])|departing)
    photos={(p['record_id'],p['stage']):p for p in dest.get('portraits',[])}
    photos.update({(p['record_id'],p['stage']):p for p in incoming.get('portraits',[])})
    dest['portraits']=list(photos.values())
    if plan['marriage']:
        partners=set(plan['spouses'])
        if not any(r['kind']=='relationship' and {r['data'].get('partner1_id'),r['data'].get('partner2_id')}==partners and
            (r['data'].get('legally_married') or str(r['data'].get('type','')).casefold() in {'marriage','married'}) and
            not r['data'].get('end_global_day') and not r.get('deleted') for r in dest['records']):
            from uuid import uuid4
            names={r['id']:r['label'] for r in dest['records'] if r['kind']=='sim'}
            dest['records'].append({'id':uuid4().hex,'kind':'relationship','label':'Marriage — '+' & '.join(names[s] for s in plan['spouses']),
                'global_day':save.global_day,'deleted':False,'data':{'type':'Marriage','legally_married':True,'status':'Married',
                 'partner1_id':plan['spouses'][0],'partner2_id':plan['spouses'][1],'start_global_day':save.global_day,'source':'Reviewed cross-branch transfer'}})
    # Materialize newly recorded marriage history using the same ID as its checkpoint.
    for entry in dest['records']:
        if entry['kind']=='relationship' and entry['data'].get('source')=='Reviewed cross-branch transfer' and not session.get(Record,entry['id']):
            row=Record(id=entry['id'],save_id=save.id,kind='relationship',label=entry['label'],global_day=entry['global_day'],version=0,data={})
            session.add(row)
            d._touch(session,row,{**entry['data'],'infinite_frozen':True,'infinite_frozen_global_day':save.global_day,'infinite_branch_id':target.id},deleted=True)
    d._update_branch(session,source,left,current_global_day=save.global_day)
    d._update_branch(session,target,dest)
    # Preserve source settings only where still applicable.
    save.settings={**save.settings,'current_heir_id':left['settings'].get('current_heir_id'),
                   'main_household_id':left['settings'].get('main_household_id')}
    tool_record(session,save,'transfer',**plan)


def prepare(session,save,user_id,operation,args):
    d._lock(session,save)
    if not d.state(save): raise ValueError('Enable Infinite Decades first.')
    plan=make_plan(session,save,operation,args)
    ticket=ActionPreview(user_id=user_id,save_id=save.id,operation='dynasty',fingerprint=digest(plan),
                         payload={'kind':operation,'args':args,'plan':plan,'epoch':d.state(save).get('epoch')})
    session.add(ticket); session.flush(); return ticket


def make_plan(session,save,operation,args):
    if operation=='switch': return switch_plan(session,save,args)
    if operation=='correction': return correction_plan(session,save,args)
    if operation=='transfer': return transfer_plan(session,save,args)
    if operation=='undo_switch':
        state=d.state(save).get('last_switch') or {}
        if not state.get('from_id') or state.get('to_id')!=d.state(save).get('active_branch_id'):
            raise ValueError('There is no branch switch available to undo.')
        plan=switch_plan(session,save,{'branch_id':state['from_id']})
        changed=progress_stamp(session,save)!=state.get('stamp')
        plan['effects'].insert(0,'New reports or edits have arrived. They will be saved in the outgoing branch, not erased.' if changed else 'No gameplay changes since this switch. Return to the previous branch.')
        plan['changed_since_switch']=changed
        return plan
    if operation=='undo_correction':
        row=session.get(Record,args.get('audit_id'))
        if not row or row.save_id!=save.id or row.kind!=KIND or row.data.get('feature')!='correction' or row.data.get('undone'):
            raise ValueError('Choose an available correction to undo.')
        return {'audit_id':row.id,'changes':row.data['changes'],'effects':['Restore only the corrected identity fields. Later edits to those fields will block undo; unrelated changes and completed rolls are preserved.']}
    raise ValueError('Unknown dynasty action.')


def confirm(session,save,ticket):
    d._lock(session,save); session.refresh(ticket,with_for_update=True)
    created=ticket.created_at.replace(tzinfo=timezone.utc) if ticket.created_at.tzinfo is None else ticket.created_at
    if ticket.consumed or ticket.save_id!=save.id or ticket.operation!='dynasty' or datetime.now(timezone.utc)-created>timedelta(minutes=30):
        raise ValueError('This preview was already used or expired. Open a new preview.')
    body=ticket.payload
    if body['epoch']!=d.state(save).get('epoch'): raise ValueError('The active branch changed. Review again.')
    plan=make_plan(session,save,body['kind'],body['args'])
    if digest(plan)!=ticket.fingerprint: raise ValueError('The affected data changed. Review a fresh preview; nothing was applied.')
    from .backup_service import create_snapshot
    with session.begin_nested(), d.branch_operation(session,save):
        if body['kind'] in {'switch','undo_switch'}:
            if not d.tracker_calendar(save):
                args=body['args']
                if args.get('load_confirmed')!='yes' or (d.state(save).get('status')=='active' and args.get('checkpoint_confirmed')!='yes'):
                    raise ValueError('Confirm the outgoing and incoming game checkpoints before switching.')
            target=d.activate_branch(session,save,plan['branch_id'],str(body['args'].get('current_game_save_name') or ''))
            d._set_state(save,last_switch={'from_id':plan['from_id'],'to_id':target.id,'from_day':plan['from_day'],
                         'to_day':plan['to_day'],'stamp':progress_stamp(session,save)})
        elif body['kind']=='correction':
            create_snapshot(session,save,'before-dynasty-correction',force=True)
            apply_corrections(session,save,plan['changes']); tool_record(session,save,'correction',**plan)
        elif body['kind']=='undo_correction':
            create_snapshot(session,save,'before-dynasty-correction-undo',force=True)
            apply_corrections(session,save,plan['changes'],reverse=True)
            row=session.get(Record,plan['audit_id']); d._touch(session,row,{**row.data,'undone':True})
        elif body['kind']=='transfer':
            create_snapshot(session,save,'before-dynasty-transfer',force=True); apply_transfer(session,save,plan)
        save.revision+=1; ticket.consumed=True
    return plan


def expected_households(session,save):
    people=[r for r in records(session,save,{'sim'}) if not r.deleted and d.alive(r,save)]
    homes={d.household_id(r) for r in people}
    rows=[r for r in records(session,save,{'household'}) if r.id in homes]
    ids={str(r.data.get('game_household_id') or '') for r in rows}|{str(r.data.get('game_household_id') or '') for r in people}
    names={r.label.casefold().strip() for r in rows}|{str(r.data.get('game_household_name') or '').casefold().strip() for r in people}
    active=d.active_branch(session,save); override=d.metadata(active).get('accepted_household') or {}
    if override.get('id'): ids.add(override['id'])
    if override.get('name'): names.add(override['name'].casefold().strip())
    return ids-{''},names-{''}


def hold_mismatched_report(session,save,report):
    if not d.enabled(save) or session.info.get('reviewed_dynasty_report')==digest(report): return None
    incoming_id=str(report.get('active_household_id') or '')
    incoming_name=str(report.get('household_name') or '').strip()
    ids,names=expected_households(session,save)
    if incoming_id and ids: mismatch=incoming_id not in ids
    elif incoming_name and names: mismatch=incoming_name.casefold() not in names
    else: return None  # No evidence is not evidence of a wrong household.
    if not mismatch: return None
    stamp=digest(report)
    held=[r for r in records(session,save,{KIND}) if r.data.get('feature')=='held_report' and r.data.get('status')=='pending']
    if any(r.data.get('digest')==stamp for r in held): return {'ok':True,'status':'branch_review','message':'Report is held for branch review.'}
    if len(held)>=100: return {'ok':False,'status':'branch_review_full','message':'Review held branch reports before accepting more. The relay retains this report.'}
    with d.branch_operation(session,save):
        tool_record(session,save,'held_report',status='pending',branch_id=d.state(save)['active_branch_id'],
            household=incoming_name or incoming_id,digest=stamp,game_day=report.get('game_day'),
            report=d.pack_snapshot({'records':[],'member_sim_ids':[],'report':report}))
    return {'ok':True,'status':'branch_review','advanced':0,'tracker_global_day':save.global_day,
            'message':'Different game household detected. Report held under Infinite Decades; no clock or family changes applied.'}


def review_held(session,save,row_id,accept=False):
    d._lock(session,save); row=session.get(Record,row_id)
    if not row or row.save_id!=save.id or row.kind!=KIND or row.data.get('feature')!='held_report' or row.data.get('status')!='pending':
        raise ValueError('That report is no longer awaiting review.')
    result=None
    with session.begin_nested(), d.branch_operation(session,save):
        if accept:
            if d.frozen(save) or row.data['branch_id']!=d.state(save).get('active_branch_id'):
                raise ValueError('Resume the branch this report was held for before accepting it.')
            report=d.unpack_snapshot(row.data['report'])['report']
            link=session.scalar(select(ClockLink).where(ClockLink.save_id==save.id))
            if not link or not link.enabled: raise ValueError('Reconnect the clock before accepting the report.')
            old=(integer(report.get('game_day'),-1),integer(report.get('hour',report.get('game_hour')),0),integer(report.get('minute',report.get('game_minute')),0))
            latest=(integer(link.last_game_day,-1),integer(link.last_game_hour,0),integer(link.last_game_minute,0))
            if old<latest: raise ValueError('A newer game report is already applied. Discard this older report; it will not rewind your game history.')
            from . import clock
            session.info['reviewed_dynasty_report']=digest(report)
            try: result=clock.receive(session,link,report)
            finally: session.info.pop('reviewed_dynasty_report',None)
            if not result.get('ok') or result.get('status') in {'paused','recovery_hold'}: raise ValueError('This report needs Clock / Crash Recovery review before it can be accepted.')
            active=d.active_branch(session,save)
            d._update_branch(session,active,accepted_household={'id':str(report.get('active_household_id') or ''),'name':str(report.get('household_name') or '')})
        data={k:v for k,v in row.data.items() if k!='report'}
        d._touch(session,row,{**data,'status':'accepted' if accept else 'discarded'})
    return result


def split_suggestions(session,save):
    if d.frozen(save): return []
    people={r.id:r for r in records(session,save,{'sim'}) if not r.deleted and d.alive(r,save)}
    events=records(session,save,{'relationship','migration'})
    recent=[r for r in events if not r.deleted and
            save.global_day-max(1,save.days_per_year)*2 <= integer(r.global_day,-999999) <= save.global_day]
    seen=set(); result=[]
    for event in sorted(recent,key=lambda r:r.global_day,reverse=True):
        if event.kind=='relationship':
            if not (event.data.get('legally_married') or str(event.data.get('type','')).casefold() in {'marriage','married','spouse'}): continue
            ids={event.data.get('partner1_id'),event.data.get('partner2_id')}&people.keys()
        else:
            ids={event.data.get('sim_id')}&people.keys()
            for relation in events:
                data=relation.data
                if relation.kind!='relationship' or relation.deleted or not (data.get('legally_married') or str(data.get('type','')).casefold() in {'marriage','married','spouse'}):continue
                if integer(relation.global_day,save.global_day)>save.global_day or integer(data.get('end_global_day'),save.global_day+1)<=save.global_day:continue
                if str(data.get('status','')).casefold() in {'divorced','annulled','widowed','ended','separated'}:continue
                pair={data.get('partner1_id'),data.get('partner2_id')}&people.keys()
                if event.data.get('sim_id') in pair:ids.update(pair)
        if not ids: continue
        adults=set(ids)
        for sim in people.values():
            if ({sim.data.get('mother_id'),sim.data.get('father_id')}|set(sim.data.get('parent_ids') or [])) & adults and 0<=save.global_day-integer(sim.data.get('birth_global_day'),-999999)<18*save.days_per_year:
                ids.add(sim.id)
        signature=tuple(sorted(ids))
        if signature in seen or len(ids)==len(people): continue
        seen.add(signature)
        result.append({'event':event,'people':[people[s] for s in sorted(ids)],'name':people[next(iter(adults))].label+' family'})
    return result[:12]


def historical_metrics(save,payload,requested_year):
    from .play_support import branch_metrics
    year=integer(requested_year)
    if year is None: return branch_metrics(payload), ''
    day=(year-save.start_year+1)*max(1,save.days_per_year)  # end of the selected year
    if day<1 or day>payload['global_day']:
        blank=branch_metrics({'global_day':day,'records':[],'member_sim_ids':[]})
        for key in ('members','descendants','living','dead','survival','wealth','marriages'): blank[key]=None
        return blank,'This branch has not recorded the end of that year. No future results are projected.'
    projected=copy.deepcopy(payload); projected['global_day']=day; unknown_death=False
    unknown_birth=any(integer(r['data'].get('birth_global_day')) is None for r in members(payload))
    for entry in projected['records']:
        data=entry['data']
        if entry['kind']=='sim':
            death=integer(data.get('death_global_day'))
            if death is None and (data.get('death_confirmed') or data.get('game_was_dead')): unknown_death=True
            data.pop('death_confirmed',None);data.pop('game_was_dead',None)
            if day!=payload['global_day']:
                for key in ('species_occult','occult_types','game_occult_types','last_household_funds'): data.pop(key,None)
        if entry['kind']=='household' and day!=payload['global_day']:
            for key in ('last_game_funds','household_funds','funds','wealth','balance'):data.pop(key,None)
    metrics=branch_metrics(projected)
    if unknown_death: metrics.update(living=None,dead=None,survival=None)
    if unknown_birth: metrics.update(members=None,descendants=None,living=None,dead=None,survival=None)
    if day!=payload['global_day']: metrics.update(wealth=None,occults={},shared_occults=[])
    return metrics,'Recorded birth/death dates and dated events only. Historical wealth and occult status are unavailable unless viewing the exact preserved day. Missing birth or death dates make population/survival figures unknown.'


def snapshot_coverage(session,save,year,album):
    if not d.state(save): return []
    start=(int(year)-save.start_year)*save.days_per_year+1; end=start+save.days_per_year-1
    included=set((album.data or {}).get('member_ids',[])) if album else set()
    contributions=(album.data or {}).get('contributions',{}) if album else {}
    photoids=set(session.scalars(select(Portrait.record_id).where(Portrait.save_id==save.id)))
    result=[]
    for row in d.branches(session,save):
        if d.metadata(row).get('status')=='archive': continue
        payload=point(session,save,row)
        eligible=[r for r in members(payload) if integer(r['data'].get('birth_global_day'),end+1)<=end and integer(r['data'].get('death_global_day'),end+1)>start]
        missing=[r for r in eligible if r['id'] not in included]
        reached=payload['global_day']>=start
        result.append({'name':row.label,'reached':reached,'contributed':row.id in contributions,
            'missing':[r['label'] for r in missing] if reached else [],
            'no_photo':[r['label'] for r in missing if r['id'] not in photoids] if reached else [],
            'year':d.year(save,payload['global_day'])})
    return result
