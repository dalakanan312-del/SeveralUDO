"""Dated, shared dynasty history. Never share a personal survival result.

Ledgers use dynasty_tool records, outside rewindable branch checkpoints. Moving
people reserves their original records at departure; receipt is an explicit,
idempotent reviewed action after the destination reaches that date.
"""
import copy
from types import SimpleNamespace
from sqlalchemy import select
from . import infinite_dynasty as d, dynasty_tools as t
from .models import Record, Portrait, uid

FEATURES = {'world_decision', 'visit', 'parcel', 'branch_relation', 'heirloom_history'}
RELATIONS = {'Allied', 'Rival', 'Estranged', 'Feuding', 'Neutral'}


def logs(session, save, feature=None):
    query = select(Record).where(Record.save_id == save.id, Record.kind == t.KIND, Record.deleted.is_(False))
    if feature: query = query.where(Record.data['feature'].as_string() == feature)
    return list(session.scalars(query))


def spoiler_free(save):
    return bool(d.state(save).get('spoiler_free', False))


def cutoff(save):
    return save.global_day if spoiler_free(save) else 10**12


def event_day(row):
    data = row.data if hasattr(row, 'data') else row.get('data', {})
    kind = row.kind if hasattr(row, 'kind') else row['kind']
    day = row.global_day if hasattr(row, 'global_day') else row.get('global_day')
    key = {'sim':'birth_global_day', 'death':'death_global_day', 'roll':'completed_global_day',
           'relationship':'start_global_day', 'event':'start_global_day'}.get(kind, 'effective_day')
    return t.integer(data.get(key), t.integer(day))


def visible_history(save, rows):
    if not spoiler_free(save): return list(rows)
    # Undated narrative cannot safely be placed before the current date.
    selected=[r for r in rows if event_day(r) is not None and event_day(r) <= save.global_day]
    result=[]
    for row in selected:
        if not isinstance(row,dict):result.append(row);continue
        entry=copy.deepcopy(row)
        # A checkpoint's old pregnancy/illness record may contain a later
        # outcome. Omit it until that terminal event is reached, rather than
        # displaying its future outcome beneath an earlier start date.
        terminal=[t.integer(entry['data'].get(k)) for k in ('death_global_day','end_global_day','recovered_global_day','delivery_global_day','delivered_global_day','completed_global_day')]
        if any(day is not None and day>save.global_day for day in terminal):continue
        result.append(entry)
    return result


def history_records(save, rows):
    """Read-only projection: do not leak a future death through a birth card."""
    if not spoiler_free(save):return list(rows)
    result=[]
    for row in rows:
        day=event_day(row)
        if day is None and row.kind not in {'sim','household'}:continue
        if day is not None and day>save.global_day:continue
        if row.kind!='sim' and any(t.integer(row.data.get(k),0)>save.global_day for k in ('end_global_day','recovered_global_day','delivery_global_day','delivered_global_day','completed_global_day')) and row.kind not in {'event','household'}:
            continue
        data=copy.deepcopy(row.data)
        for field in ('death_global_day','end_global_day','recovered_global_day','delivery_global_day'):
            value=t.integer(data.get(field))
            if value is not None and value>save.global_day:
                data.pop(field,None)
                if field=='death_global_day':
                    for key in ('death_confirmed','game_was_dead','death_cause','cause_of_death'):data.pop(key,None)
        result.append(SimpleNamespace(id=row.id,kind=row.kind,label=row.label,global_day=row.global_day,data=data,
            deleted=row.deleted,created_at=row.created_at,updated_at=row.updated_at,version=row.version))
    return result


def text(args, key, limit=4000, required=False):
    value = str(args.get(key) or '').strip()
    if required and not value: raise ValueError('Fill in '+key.replace('_',' ')+'.')
    if len(value) > limit: raise ValueError(key.replace('_',' ').title()+' is too long.')
    return value


def dated(save, args):
    day = t.integer(args.get('day'))
    if day is None or not 1 <= day <= save.global_day:
        raise ValueError('Choose a recorded Global Day, from 1 through the current branch day.')
    return day


def known_person(session, save, sid):
    row = session.get(Record, sid)
    if not row or row.save_id != save.id or row.kind != 'sim' or (row.deleted and not row.data.get('infinite_frozen')):
        raise ValueError('Choose a Sim from this dynasty.')
    return row


def entry_person(payload, sid, day):
    entry = next((r for r in t.members(payload) if r['id'] == sid), None)
    if not entry: raise ValueError('The selected Sim is not a member of that branch.')
    data = entry['data']; birth = t.integer(data.get('birth_global_day')); death = t.integer(data.get('death_global_day'))
    if birth is None or birth > day or (death is not None and death <= day) or ((data.get('death_confirmed') or data.get('game_was_dead')) and death is None):
        raise ValueError('The selected Sim must have a recorded birth and be living on the event date.')
    return entry


def ledger_row(session, save, rid, feature):
    row = session.get(Record, rid)
    if not row or row.save_id != save.id or row.kind != t.KIND or row.deleted or row.data.get('feature') != feature:
        raise ValueError('That dynasty record is not available.')
    return row


def points(session, save):
    return [(br, t.point(session, save, br)) for br in d.branches(session, save) if d.metadata(br).get('status') != 'archive']


def plan(session, save, action, args):
    if not d.enabled(save): raise ValueError('Turn on Infinite Decades first.')
    active = d.active_branch(session, save)
    if not active: raise ValueError('Choose a current branch first.')
    if action == 'park':
        br = t.branch(session, save, args.get('branch_id')); meta = d.metadata(br)
        if br.id == active.id or meta.get('status') not in {'waiting','paused'}:
            raise ValueError('Park a waiting or paused branch. Switch away from your current family first.')
        parked = text(args,'parked') == 'yes'
        reminder=t.integer(args.get('reminder_year'))
        if reminder is not None and not -9999<=reminder<=9999:raise ValueError('Enter a valid reminder year.')
        return {'branch_id':br.id, 'parked':parked, 'reason':text(args,'reason',1000),
                'reminder_year':reminder, 'before':meta.get('parked',False),
                'effects':[f"{'Park' if parked else 'Return'} {br.label} {'outside' if parked else 'to'} the normal play queue. No history, dates, or completion status changes."]}
    if d.frozen(save): raise ValueError('Resume an active branch before recording new history.')
    if action == 'world':
        day = dated(save,args); event = session.get(Record,args.get('event_id'))
        if not event or event.save_id != save.id or event.kind != 'event' or (event.deleted and not event.data.get('infinite_frozen')):
            raise ValueError('Choose a historical event from this save.')
        start = t.integer(event.data.get('start_global_day'), event.global_day or 1)
        end = t.integer(event.data.get('end_global_day'))
        if day < start or (end is not None and day > end): raise ValueError('Choose a day within this event.')
        # One explicit world-level decision per event occurrence; never inferred
        # from personal/household rolls, even if a result sounds universal.
        if any(r.data.get('event_id') == event.id and r.data.get('day') == day for r in logs(session,save,'world_decision')):
            raise ValueError('A shared decision already exists for this event on this day.')
        outcome = text(args,'outcome',4000,True)
        return {'event_id':event.id,'label':event.label,'day':day,'outcome':outcome,'branch_id':active.id,
                'effects':[f'Record one shared world decision: {event.label}, GD {day}: {outcome}',
                           'Other branches automatically see this decision when they reach this date. No personal or household survival roll is completed, skipped, or changed.']}
    if action == 'visit':
        day=dated(save,args); source=t.branch(session,save,args.get('branch_id'))
        if source.id==active.id: raise ValueError('Choose a visitor from another branch.')
        person=entry_person(t.point(session,save,source),text(args,'sim_id'),day)
        return {'day':day,'source_id':source.id,'branch_id':active.id,'sim_id':person['id'],
                'label':text(args,'label',200,True),'notes':text(args,'notes'),
                'effects':[f"Record {person['label']} visiting {active.label} on GD {day}.",
                           'Their owning branch, household, clock, pregnancy tracking and aging remain unchanged. This is a player-recorded visit, not a game detection.']}
    if action == 'end_visit':
        row=ledger_row(session,save,args.get('row_id'),'visit');day=dated(save,args)
        if row.data.get('end_day') is not None or row.data['branch_id']!=active.id or day<row.data['day']:
            raise ValueError('End an open visit in the receiving branch, on or after arrival.')
        return {'row_id':row.id,'day':day,'effects':[f'End {row.label} on GD {day}. Ownership stays unchanged.']}
    if action == 'relation':
        day=dated(save,args);other=t.branch(session,save,args.get('branch_id'));relationship=text(args,'relationship')
        if other.id==active.id or relationship not in RELATIONS: raise ValueError('Choose a different branch and a supported relationship.')
        return {'day':day,'branch_ids':sorted([active.id,other.id]),'relationship':relationship,'notes':text(args,'notes'),
                'effects':[f'Record {active.label} and {other.label} as {relationship.lower()} from GD {day}.',
                           'This can supply optional drama prompts. It never changes game relationships or source-rule odds.']}
    if action == 'heirloom':
        day=dated(save,args); owner=t.branch(session,save,args.get('branch_id'))
        person=entry_person(t.point(session,save,owner),text(args,'sim_id'),day)
        rid=text(args,'row_id'); previous=ledger_row(session,save,rid,'heirloom_history') if rid else None
        source_id=text(args,'heirloom_id'); source=None
        if source_id and not previous:
            source=session.get(Record,source_id)
            if not source or source.save_id!=save.id or source.kind!='heirloom':raise ValueError('Choose a tracked heirloom from this save.')
            if any(r.data.get('heirloom_id')==source.id for r in logs(session,save,'heirloom_history')):raise ValueError('This heirloom already has an ownership history. Use its handover form.')
        history=copy.deepcopy(previous.data['history']) if previous else []
        if history and day<=history[-1]['day']: raise ValueError('Record handovers after the last ownership date; do not overwrite earlier history.')
        label=previous.label if previous else (source.label if source else text(args,'label',200,True))
        history.append({'day':day,'branch_id':owner.id,'sim_id':person['id'],'name':person['label'],'notes':text(args,'notes')})
        return {'row_id':rid,'heirloom_id':previous.data.get('heirloom_id','') if previous else source_id,
                'label':label,'day':day,'history':history,'before':previous.version if previous else None,
                'effects':[f"Record {label} with {person['label']} in {owner.label}, from GD {day}.",
                           'Keep all earlier owners and dates. This records ownership; it does not move an object in the Sims game.']}
    if action == 'send': return send_plan(session,save,active,args)
    if action == 'receive': return receive_plan(session,save,active,args)
    raise ValueError('Unknown dynasty-history action.')


def send_plan(session,save,source,args):
    target=t.branch(session,save,args.get('branch_id')); payload=t.point(session,save,target)
    if target.id==source.id or d.metadata(target).get('status') not in {'paused','waiting'}:
        raise ValueError('Choose a waiting or paused receiving branch.')
    day=save.global_day
    if payload['global_day']>day: raise ValueError('The receiving branch is already past this departure date. Catch up the sending branch first.')
    mode=text(args,'mode');label=text(args,'label',200,True);notes=text(args,'notes')
    result={'day':day,'source_id':source.id,'branch_id':target.id,'mode':mode,'label':label,'notes':notes,'status':'pending'}
    if mode=='people':
        selected=sorted(set(args.get('sim_ids') or []))
        if not selected: raise ValueError('Select the Sims who are moving.')
        current=d.snapshot(session,save,include_portraits=False)
        selected_people=[entry_person(current,sid,day) for sid in selected]
        living=[r for r in t.members(current) if d.alive(SimpleNamespace(deleted=False,data=r['data']),save)]
        if not any(r['id'] not in selected for r in living):raise ValueError('Leave a living Sim in the sending branch.')
        if set(selected)&set(payload['member_sim_ids']):raise ValueError('A selected Sim already belongs to the receiving branch.')
        # Fingerprint only departing records, not unrelated report receipts.
        outgoing=outgoing_payload(session,save,selected,include_portraits=False)
        for entry in outgoing['records']:
            if entry['kind'] in {'sim','household'}:
                entry['data']={k:v for k,v in entry['data'].items() if not k.startswith(('game_','last_'))}
        result.update(sim_ids=selected,stamp=t.digest(outgoing['records']))
        effects=[f"Reserve {', '.join(r['label'] for r in selected_people)} for {target.label}, leaving {source.label} on GD {day}.",
                 'They stop receiving tasks in the source branch and keep the same IDs, ancestry, portraits and history. They are held safely in transit, not aged forward or added early.']
    elif mode=='inheritance':
        amount=t.integer(args.get('amount'));home=session.get(Record,args.get('household_id'))
        if amount is None or not 1<=amount<=10**12:raise ValueError('Enter a positive whole-number inheritance amount.')
        if not home or home.save_id!=save.id or home.kind!='household' or home.deleted:raise ValueError('Choose a current household for the inheritance ledger.')
        result.update(amount=amount,household_id=home.id)
        effects=[f'Record an outgoing inheritance of §{amount:,} from {home.label} on GD {day}.',
                 'This creates a dated ledger entry, not a change to game-reported household funds. The recipient gets one matching ledger entry only after review.']
    else:raise ValueError('Choose a family transfer or inheritance.')
    result['effects']=effects+[f'{target.label} is currently at GD {payload["global_day"]}. Receipt unlocks at GD {day}; a parked branch can still receive it after resuming.']
    return result


def outgoing_payload(session,save,selected,include_portraits=True):
    incoming=d.snapshot(session,save,selected,include_portraits=include_portraits)
    # Carry only person-owned / person-linked records and their households, not
    # the sender's unrelated rules, world events or economic ledger.
    homes={r['data'].get('current_household_id') or r['data'].get('household_id') for r in t.members(incoming)}
    incoming['records']=[r for r in incoming['records'] if r['id'] in selected or
        (r['kind']=='household' and r['id'] in homes) or (r['kind'] not in {'sim','household'} and d._references(r['data'],set(selected)))]
    return incoming


def receive_plan(session,save,active,args):
    row=ledger_row(session,save,args.get('row_id'),'parcel');data=row.data
    if data['status']!='pending' or data['branch_id']!=active.id:raise ValueError('Receive this item in its intended branch. It may already have been received.')
    if save.global_day<data['day']:raise ValueError(f"This delivery unlocks on GD {data['day']}. Keep playing this branch first.")
    result={'row_id':row.id,'version':row.version,'day':data['day'],'mode':data['mode']}
    if data['mode']=='people':
        payload=d.unpack_snapshot(data['payload']); ids=set(data['sim_ids'])
        for sid in ids:
            current=known_person(session,save,sid)
            if not current.data.get('infinite_frozen') or current.data.get('infinite_branch_id')!=active.id:
                raise ValueError('A reserved Sim changed ownership. Review their branch history before receiving.')
        # Never overwrite an active record changed after departure.
        conflicts=[]
        for entry in payload['records']:
            current=session.get(Record,entry['id'])
            if current and not current.data.get('infinite_frozen') and entry['kind'] not in {'household'}:
                clean={k:v for k,v in current.data.items() if k not in d.MARKERS}
                if clean!=entry['data']:conflicts.append(current.label)
        if conflicts:raise ValueError('Shared records changed since departure: '+', '.join(conflicts[:5])+'. Resolve these before receiving; nothing was overwritten.')
        result['effects']=[f"Receive {row.label}, effective GD {data['day']}, with original identities, birth dates, histories and portraits.",
                           'Existing household records and unrelated receiving-branch data stay unchanged. No roll is passed automatically. Review any now-due tasks.']
    else:
        home=session.get(Record,args.get('household_id'))
        if not home or home.save_id!=save.id or home.kind!='household' or home.deleted:raise ValueError('Choose a receiving household in this branch.')
        result['household_id']=home.id
        result['effects']=[f"Record §{data['amount']:,} received by {home.label}, effective GD {data['day']}. The game balance is unchanged; this is the tracker ledger."]
    return result


def write_entry(session,save,kind,label,day,data):
    row=Record(id=uid(),save_id=save.id,kind=kind,label=label,global_day=day,version=0,data={},deleted=False)
    session.add(row);d._touch(session,row,data);return row


def apply(session,save,action,p):
    if action=='park':
        d._update_branch(session,t.branch(session,save,p['branch_id']),parked=p['parked'],park_reason=p['reason'],park_reminder_year=p['reminder_year']);return
    if action in {'world','visit','relation'}:
        feature={'world':'world_decision','visit':'visit','relation':'branch_relation'}[action]
        row=t.tool_record(session,save,feature,**p);row.global_day=p['day']
        row.label=p.get('label') or p.get('relationship');d._touch(session,row)
        return
    if action=='end_visit':
        row=ledger_row(session,save,p['row_id'],'visit');d._touch(session,row,{**row.data,'end_day':p['day']});return
    if action=='heirloom':
        data={k:v for k,v in p.items() if k not in {'row_id','before','effects'}}
        if p['row_id']:
            row=ledger_row(session,save,p['row_id'],'heirloom_history');d._touch(session,row,{**row.data,**data})
        else:
            row=t.tool_record(session,save,'heirloom_history',**data);row.label=p['label'];d._touch(session,row)
        return
    if action=='send':
        data={k:v for k,v in p.items() if k not in {'effects','stamp'}}
        if p['mode']=='people':
            selected=set(p['sim_ids']);incoming=outgoing_payload(session,save,selected)
            remaining={r.id for r in t.records(session,save,{'sim'}) if not r.deleted and r.id not in selected}
            left=d.snapshot(session,save,remaining,include_candidates=True);leftids={r['id'] for r in left['records']}
            data['payload']=d.pack_snapshot(incoming)
            for entry in incoming['records']:
                if entry['id'] not in leftids:d._freeze(session,session.get(Record,entry['id']),p['day'],p['branch_id'])
            d._update_branch(session,d.active_branch(session,save),left,current_global_day=save.global_day)
            save.settings={**save.settings,'current_heir_id':left['settings'].get('current_heir_id'),'main_household_id':left['settings'].get('main_household_id')}
        parcel=t.tool_record(session,save,'parcel',**data);parcel.label=p['label'];parcel.global_day=p['day'];d._touch(session,parcel)
        if p['mode']=='inheritance':
            write_entry(session,save,'economy_entry',p['label']+' — sent',p['day'],{'household_id':p['household_id'],'amount':p['amount'],'entry_type':'expense','category':'Inheritance','source':'Dynasty inheritance','dynasty_parcel_id':parcel.id,'notes':p['notes']})
        return
    if action=='receive':
        row=ledger_row(session,save,p['row_id'],'parcel');data=row.data
        if p['mode']=='people':
            payload=d.unpack_snapshot(data['payload'])
            for entry in payload['records']:
                current=session.get(Record,entry['id'])
                if current and not current.data.get('infinite_frozen'):continue
                if not current:
                    current=Record(id=entry['id'],save_id=save.id,kind=entry['kind'],version=0,data={});session.add(current)
                current.label=entry['label'];current.global_day=entry.get('global_day')
                d._touch(session,current,{**entry['data'],'infinite_branch_id':data['branch_id']},deleted=entry.get('deleted',False))
            # Portrait rows remain attached to stable Sim IDs while in transit;
            # restore the reserved bytes only if a stage portrait is missing.
            import base64
            for photo in payload.get('portraits',[]):
                exists=session.scalar(select(Portrait.id).where(Portrait.save_id==save.id,Portrait.record_id==photo['record_id'],Portrait.stage==photo['stage']))
                if not exists:session.add(Portrait(save_id=save.id,record_id=photo['record_id'],stage=photo['stage'],mime_type=photo['mime_type'],image=base64.b64decode(photo['image']),source='dynasty-transfer'))
        else:
            write_entry(session,save,'economy_entry',row.label+' — received',p['day'],{'household_id':p['household_id'],'amount':data['amount'],'entry_type':'income','category':'Inheritance','source':'Dynasty inheritance','dynasty_parcel_id':row.id,'notes':data['notes']})
        d._touch(session,row,{**data,'status':'received','received_day':save.global_day})
        d._update_branch(session,d.active_branch(session,save),d.snapshot(session,save,include_candidates=True),current_global_day=save.global_day)


def world_context(session,save,event_id=None):
    if not d.enabled(save):return []
    key=('dynasty_world',save.id,save.revision,save.global_day)
    if key not in session.info:session.info[key]=logs(session,save,'world_decision')
    return sorted((r for r in session.info[key] if r.data['day']<=save.global_day and
                   (not event_id or r.data['event_id']==event_id)),key=lambda r:r.data['day'],reverse=True)


def contradictions(session,save,views=None):
    views=views if views is not None else points(session,save)
    limit=cutoff(save);copies={};marriages={};issues=[]
    for br,payload in views:
        for entry in payload['records']:
            if entry.get('deleted'):continue
            if entry['kind']=='sim' and t.integer(entry['data'].get('birth_global_day'),limit+1)<=limit:
                copies.setdefault(entry['id'],[]).append((br,entry))
            if entry['kind']=='relationship' and event_day(entry) is not None and event_day(entry)<=limit and (entry['data'].get('legally_married') or str(entry['data'].get('type','')).casefold() in {'married','marriage','spouse'}):
                marriages.setdefault(entry['id'],(br,entry))
    for sid,versions in copies.items():
        for field in ('birth_global_day','mother_id','father_id'):
            values={str(e['data'][field]) for _,e in versions if e['data'].get(field) not in ('',None)}
            if len(values)>1:
                issues.append({'sim_id':sid,'message':f"{versions[0][1]['label']}: different recorded {field.replace('_',' ')} across "+', '.join(br.label for br,_ in versions)+'.','evidence':'; '.join(f"{br.label}: {e['data'].get(field,'unknown')}" for br,e in versions)})
        deaths=[(br,t.integer(e['data'].get('death_global_day'))) for br,e in versions if t.integer(e['data'].get('death_global_day')) is not None and t.integer(e['data'].get('death_global_day'))<=limit]
        if len({day for _,day in deaths})>1:
            issues.append({'sim_id':sid,'message':versions[0][1]['label']+': different death dates across branches.','evidence':'; '.join(f'{br.label}: GD {day}' for br,day in deaths)})
        for br,marriage in marriages.values():
            if sid not in {marriage['data'].get('partner1_id'),marriage['data'].get('partner2_id')}:continue
            day=event_day(marriage)
            for death_branch,death in deaths:
                if death<day:
                    issues.append({'sim_id':sid,'message':f"{versions[0][1]['label']}: marriage on GD {day} follows a recorded death on GD {death}.",'evidence':f'{br.label}: {marriage["label"]}; {death_branch.label}: death GD {death}.'});break
    return issues


def journey(session,save,sid,views=None):
    person=known_person(session,save,sid);views=views if views is not None else points(session,save);rows=[]
    owners={br.id:br.label for br,_ in views};birth=t.integer(person.data.get('birth_global_day'))
    for br,payload in views:
        meta=d.metadata(br); seeds=meta.get('seed_sim_ids',[]);split=meta.get('split_global_day',payload['global_day'])
        if sid in seeds:
            initial=not meta.get('parent_branch_id') or meta.get('parent_branch_id')==d.state(save).get('starting_branch_id')
            rows.append({'day':split,'text':('Started dynasty in ' if initial else 'Founded / joined split: ')+br.label,'source':'Recorded branch checkpoint'})
        elif birth is not None and any(r['id']==sid for r in t.members(payload)) and birth>=split:
            rows.append({'day':birth,'text':'Born in '+br.label,'source':'Birth date and branch membership'})
    for row in logs(session,save):
        data=row.data
        if data.get('feature')=='transfer' and sid in data.get('sim_ids',[]):
            rows.append({'day':row.global_day,'text':f"Moved from {owners.get(data.get('source_id'),'previous branch')} to {owners.get(data.get('branch_id'),'receiving branch')}",'source':'Reviewed transfer'})
        if data.get('feature')=='parcel' and data.get('mode')=='people' and sid in data.get('sim_ids',[]):
            rows.append({'day':data['day'],'text':f"Left {owners.get(data['source_id'],'previous branch')} for {owners.get(data['branch_id'],'receiving branch')}"+(' · in transit' if data['status']=='pending' else ' · received'),'source':'Dated transfer ledger'})
    unique={(r['day'],r['text']):r for r in rows if r['day']<=cutoff(save)}
    return sorted(unique.values(),key=lambda r:(r['day'],r['text']))


def checklist(session,save,year,views=None):
    views=views if views is not None else points(session,save);day=(year-save.start_year+1)*save.days_per_year
    result=[]
    albums=t.records(session,save,{'decade_snapshot'})
    album=next((a for a in albums if not a.deleted and t.integer(a.data.get('portrait_year'))==year),None)
    all_coverage=t.snapshot_coverage(session,save,year,album)
    for br,payload in views:
        meta=d.metadata(br)
        if meta.get('split_global_day',1)>day:continue
        rows=[r for r in payload['records'] if not r.get('deleted')]
        rolls=[r for r in rows if r['kind']=='roll' and not r['data'].get('completed') and t.integer(r.get('global_day'),day+1)<=min(day,payload['global_day'])]
        pregnancies=[r for r in rows if r['kind']=='pregnancy' and not r['data'].get('delivered') and str(r['data'].get('status','')).casefold() not in {'delivered','ended','miscarriage','miscarried','stillbirth','cancelled','canceled','closed','complete','completed'} and t.integer(r['data'].get('due_global_day'),t.integer(r.get('global_day'),day+1))<=min(day,payload['global_day'])]
        coverage=[x for x in all_coverage if x['branch_id']==br.id]
        result.append({'branch':br,'reached':payload['global_day']>=day,'day':payload['global_day'],'parked':meta.get('parked',False),'rolls':len(rolls),'births':len(pregnancies),'coverage':coverage})
    return result


def drama_prompts(session,save):
    if not d.enabled(save) or not d.state(save).get('branch_drama'):return []
    active=d.state(save).get('active_branch_id');names={br.id:br.label for br in d.branches(session,save)};pairs={}
    for row in sorted(logs(session,save,'branch_relation'),key=lambda r:(r.data['day'],r.created_at)):
        if row.data['day']<=save.global_day and active in row.data['branch_ids']:pairs[tuple(row.data['branch_ids'])]=row
    prompts=[]
    hooks={'Allied':'An ally asks for help that will cost your household something. What do you offer?',
           'Rival':'A rival family challenges your claim or achievement. How do you answer?',
           'Estranged':'A family you no longer speak to sends an invitation. Do you reopen contact?',
           'Feuding':'A family feud threatens a shared celebration. Who will attempt a truce?'}
    for row in pairs.values():
        relation=row.data['relationship'];other=next(b for b in row.data['branch_ids'] if b!=active)
        if relation in hooks:prompts.append({'label':names.get(other,'Other branch')+' · '+relation,'prompt':hooks[relation],'notes':row.data['notes']})
    for row in logs(session,save,'visit'):
        if row.data['branch_id']==active and row.data['day']<=save.global_day and (row.data.get('end_day') is None or row.data['end_day']>save.global_day):
            prompts.append({'label':row.label,'prompt':'A visiting relative brings a request, news, or an old disagreement. What happens during the visit?','notes':row.data['notes']})
    return prompts
