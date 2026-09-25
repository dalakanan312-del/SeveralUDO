"""Player-supplied annual marriage odds; permissions are configured, never inferred law.

Uses ordinary rolls and relationships so previews, backups and cloud sync retain
the same records. Enabling this alternative never replays completed decisions.
"""
from sqlalchemy import select, or_, and_, update
from .models import Record, ChronicleSave
from . import domain, record_state

ERAS = (
    (-9000, -3001, '9000–3000 BCE', ((8,1),(6,2),(8,1),(6,2),(8,1))),
    (-3000, 499, '3000 BCE–500 CE', ((10,1),(8,2),(10,1),(8,2),(10,1))),
    (500, 999, '500–1000', ((10,1),(8,2),(10,1),(8,2),(10,1))),
    (1000, 1449, '1000–1450', ((12,1),(8,2),(10,1),(8,1),(10,1))),
    (1450, 1749, '1450–1750', ((20,1),(8,1),(10,1),(10,1),(12,1))),
    (1750, 1899, '1750–1900', ((20,1),(10,2),(12,1),(10,1),(12,1))),
    (1900, 1945, '1900–1945', ((20,1),(8,2),(12,1),(12,1),(20,1))),
    (1946, 1969, '1946–1969', ((20,1),(6,2),(10,1),(12,1),(20,1))),
    (1970, 9999, '1970–Present', ((20,1),(8,1),(10,1),(12,1),(20,1))),
)
CLOSED = {'ended','divorced','annulled','widowed','abandoned','inactive','closed'}
SUCCESS = {'awaiting_spouse','awaiting_match','refusal_pending','must_marry','planned'}


def integer(value, default=None):
    return record_state.integer(value, default)


def enabled(save):
    return (save.settings or {}).get('marriage_roll_mode') == 'era_annual'


def year_at(save, day=None):
    return save.start_year + (int(save.global_day if day is None else day)-1)//max(1,save.days_per_year)


def year_bounds(save, year):
    first=(year-save.start_year)*max(1,save.days_per_year)+1
    return first, first+max(1,save.days_per_year)-1


def era_for(year):
    return next((era for era in ERAS if era[0]<=year<=era[1]),None)


def rows(session, save):
    return list(session.scalars(select(Record).where(Record.save_id==save.id,Record.deleted.is_(False),
        or_(Record.kind.in_(['sim','household','relationship','event']),
            and_(Record.kind=='roll',Record.data['annual_marriage'].as_boolean().is_(True),
                 Record.data['planner_year'].as_integer()==year_at(save))))))


def is_active(row):
    return not row.deleted and not row.data.get('infinite_frozen')


def living(sim, save):
    species=str(sim.data.get('game_species') or sim.data.get('species') or 'human').casefold()
    return is_active(sim) and record_state.living(sim,save.global_day) and species not in {'dog','cat','horse','fox','animal','pet'}


def marriage_state(sim, save, records):
    """Use the latest prior marriage; a dead spouse ends an otherwise active link."""
    by_id={r.id:r for r in records if r.kind=='sim'}
    ended=[]; unknown=False; active=False
    for rel in records:
        d=rel.data
        if rel.kind!='relationship' or not is_active(rel) or sim.id not in (d.get('partner1_id'),d.get('partner2_id')):continue
        if not (d.get('legally_married') or 'marriage' in str(d.get('type','')).casefold() or str(d.get('type','')).casefold()=='spouse'):continue
        start=integer(d.get('start_global_day'),integer(rel.global_day))
        if start is not None and start>save.global_day:continue
        if str(d.get('status','')).casefold()=='planned' and not d.get('legally_married'):continue
        end=integer(d.get('end_global_day'))
        other=by_id.get(d.get('partner2_id') if d.get('partner1_id')==sim.id else d.get('partner1_id'))
        death=integer(other.data.get('death_global_day')) if other else None
        if death is not None and death<=save.global_day:end=min(end,death) if end is not None else death
        status=str(d.get('status') or 'Active').casefold()
        # Separation does not grant permission to remarry while still legally married.
        if (end is None or end>save.global_day) and status not in CLOSED:
            if other and not record_state.living(other,save.global_day):unknown=True
            else:active=True
        elif end is None:unknown=True
        else:ended.append((end,rel.id))
    return {'active':active,'unknown_end':unknown,'last_end':max(ended)[0] if ended else None,
            'relationship_id':max(ended)[1] if ended else None}


def facts(sim, records):
    home=next((r for r in records if r.kind=='household' and r.id==sim.data.get('current_household_id')),None)
    def value(*keys):
        return next((str(obj.data[k]).strip().casefold() for obj in (sim,home) if obj
                     for k in keys if obj.data.get(k) not in (None,'')), '')
    return {'region':value('country','current_country','region','location'),
            'class':value('social_class','class'),'faith':value('faith','religion'),
            'custom':value('marriage_custom','local_custom')}


def eligibility(sim,save,records):
    year=year_at(save);d=sim.data
    if not living(sim,save):return 'Not a living Sim in the active branch'
    birth=integer(d.get('birth_global_day'))
    if birth is None:return 'Birth date needed to check marriage age'
    age=(save.global_day-birth)//max(1,save.days_per_year)
    if age<13:return 'Pre-Teens do not make marriage rolls'
    if not era_for(year):return 'Outside the supplied marriage timeline'
    if d.get('marriage_eligibility')=='blocked':return 'Marriage not permitted by the recorded local custom'
    event_until=integer(d.get('marriage_event_until_year'))
    if d.get('marriage_eligibility')=='event' and (event_until is None or year<=event_until):
        return 'Timeline-required marriage overrides the yearly table'
    for event in records:
        if event.kind=='event' and is_active(event) and event.data.get('active',True) and sim.id in (event.data.get('required_marriage_sim_ids') or []):
            if integer(event.data.get('start_global_day'),1)<=save.global_day<=integer(event.data.get('end_global_day'),9999999):
                return 'Timeline-required marriage overrides the yearly table'
    first_year=integer(d.get('marriage_available_from_year'))
    if first_year is not None and year<first_year:return f'Marriage not permitted until {first_year}'
    min_days=domain.age_setting_days(save,'marriage_min_age_days',72)
    f=facts(sim,records)
    policies=(save.settings or {}).get('annual_marriage_policies') or []
    for policy in policies:
        if not integer(policy.get('from_year'),-9999)<=year<=integer(policy.get('until_year'),9999):continue
        if not all(not policy.get(k) or str(policy[k]).strip().casefold()==f[k] for k in ('region','class','faith','custom')):continue
        if policy.get('permission')=='blocked':return 'Marriage blocked by '+str(policy.get('label') or 'local eligibility rule')
        min_days=max(min_days,integer(policy.get('min_age'),13)*max(1,save.days_per_year))
    if save.global_day-birth<min_days:return 'Below the configured marriage age'
    state=marriage_state(sim,save,records)
    if state['active']:return 'Already married'
    if state['unknown_end']:return 'Record when the previous marriage ended before rolling again'
    if state['last_end'] is not None and year<=year_at(save,state['last_end']):return 'Remarriage rolls begin the following calendar year'
    return ''


def specification(sim,save,records):
    reason=eligibility(sim,save,records)
    if reason:return None,reason
    year=year_at(save);era=era_for(year);age=(save.global_day-int(sim.data['birth_global_day']))//max(1,save.days_per_year)
    stage='Teen' if age<18 else 'Young Adult' if age<40 else 'Adult' if age<60 else 'Elder'
    state=marriage_state(sim,save,records);remarriage=state['last_end'] is not None
    years=year-year_at(save,state['last_end']) if remarriage else None
    extended=bool(sim.data.get('marriage_practical_extension')) and bool(str(sim.data.get('marriage_practical_reason') or '').strip())
    early=remarriage and age<60 and years<= (10 if extended else 5)
    index=(3 if early else 4) if remarriage else (0 if age<18 else 1 if age<40 else 2)
    sides,wins=era[3][index];positive='May remarry' if remarriage else 'May marry';negative='Does not remarry' if remarriage else 'Does not marry'
    result=f"{'1' if wins==1 else '1-2'}: {positive}; {wins+1}-{sides}: {negative}"
    note=(f'{stage}, age {age}; '+(f'{years} calendar year(s) unmarried. '+('Practical extension selected (up to ten years). ' if extended and early else '') if remarriage else 'First marriage. '))
    if age>=60 and remarriage:note+='Elders always use the after-five-years table. '
    return {'die':f'd{sides}','bad_results':f'{wins+1}-{sides}','result_rules':result,
        'success_results':'1' if wins==1 else '1-2','nonlethal':True,'failure_is_lethal':False,
        'planner_year':year,'marriage_age':age,'marriage_stage':stage,'marriage_era':era[2],
        'remarriage_roll':remarriage,'relationship_id':state['relationship_id'],
        'marriage_years_unmarried':years,'marriage_practical_extension_used':bool(early and extended and years>5),
        'source_notes':note+'Roll once per calendar year, only where legally and socially permitted. A successful result needs an eligible spouse; refusal requires 1 on d6.',
        'roll_type':'Annual Remarriage Eligibility' if remarriage else 'Annual Marriage Eligibility'},''


def change(session,row,updates):
    base=row.version;row.data={**row.data,**updates};row.version+=1;domain.journal(session,row,'upsert',base)


def retire(session,save,roll,reason):
    base=roll.version;roll.deleted=True;roll.version+=1
    roll.data={**roll.data,'retired_reason':reason,'retired_global_day':save.global_day}
    domain.journal(session,roll,'delete',base)


def schedule(session,save):
    if not domain.automation_enabled(save):return 0,0
    if not enabled(save):
        pending=session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='roll',Record.deleted.is_(False),
            Record.data['completed'].as_boolean().is_not(True),
            or_(Record.data['annual_marriage'].as_boolean().is_(True),Record.data['marriage_refusal'].as_boolean().is_(True))))
        retired=0
        for roll in pending:
            if is_active(roll):retire(session,save,roll,'Annual marriage scheduling turned off');retired+=1
        return 0,retired
    session.execute(update(ChronicleSave).where(ChronicleSave.id==save.id).values(revision=ChronicleSave.revision))
    session.refresh(save)
    records=rows(session,save);by_id={r.id:r for r in records if r.kind=='sim'};year=year_at(save)
    rolls=[r for r in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='roll',Record.deleted.is_(False),
           or_(*(Record.data[k].as_string().ilike('%marriage%') for k in ('source','source_id','roll_type')))))
           if domain._marriage_roll(r) and is_active(r)]
    created=changed=0;specs={}
    if enabled(save):
        specs={sid:specification(sim,save,records) for sid,sim in by_id.items()}
    for roll in rolls:
        d=roll.data
        if d.get('completed'):continue
        annual=d.get('annual_marriage') or d.get('marriage_refusal')
        if bool(annual)!=enabled(save):
            retire(session,save,roll,'Marriage scheduling mode changed; completed results are preserved');changed+=1;continue
        if not enabled(save):continue
        spec,reason=specs.get(d.get('sim_id'),(None,'Sim is no longer available'))
        if integer(d.get('planner_year'))!=year:reason='Unresolved yearly check expired; no outcome was invented'
        if reason:retire(session,save,roll,reason);changed+=1;continue
        if d.get('annual_marriage') and spec:
            due=max(year_bounds(save,year)[0],min(save.global_day,integer(roll.global_day,save.global_day)))
            updates={**spec,'due_global_day':due}
            if roll.global_day!=due or any(d.get(k)!=v for k,v in updates.items()):
                roll.global_day=due;change(session,roll,updates);changed+=1
    if not enabled(save):return 0,changed
    # No retroactive backlog: start with this year. Rewinds do not reroll history.
    already={r.data.get('sim_id') for r in rolls if not r.deleted and not r.data.get('marriage_refusal')
        and integer(r.data.get('planner_year'),year_at(save,r.global_day or save.global_day))==year}
    for rel in records:
        if rel.kind=='relationship' and is_active(rel) and rel.data.get('annual_marriage_year')==year and str(rel.data.get('status','Active')).casefold() not in CLOSED:
            already.update((rel.data.get('partner1_id'),rel.data.get('partner2_id')))
    tracking=max(1,domain._setting_int(save,'roll_tracking_start_day',1))
    if save.global_day<tracking:return 0,changed
    for sid,(spec,reason) in specs.items():
        if reason or sid in already:continue
        sim=by_id[sid];source=f'planner:marriage:annual:{sid}:{year}'
        # First eligibility/import/permission can occur in the middle of a year;
        # never backdate the check to before this eligibility was established.
        due=save.global_day
        roll=Record(save_id=save.id,kind='roll',label=domain.record_label(f'{sim.label} — {spec["roll_type"]} · {year}'),global_day=due,
            data={**spec,'sim_id':sid,'sim_name':sim.label,'household_id':sim.data.get('current_household_id'),
                  'source':source,'annual_marriage':True,'completed':False,'due_global_day':due})
        session.add(roll);session.flush();domain.journal(session,roll,'upsert',0);created+=1
    return created,changed


def candidates(sim,save,records):
    from .main import kinship_warning
    people=[r for r in records if r.kind=='sim'];depth=max(1,min(8,integer((save.settings or {}).get('kinship_detection_generations'),3)))
    result=[]
    unavailable={r.data.get('sim_id') for r in records if r.kind=='roll' and is_active(r)
        and r.data.get('annual_marriage') and integer(r.data.get('planner_year'))==year_at(save)
        and r.data.get('marriage_decision') in {'no_marriage','refused','refusal_pending'}}
    promised={}
    for rel in records:
        d=rel.data
        if rel.kind=='relationship' and is_active(rel) and str(d.get('status','Active')).casefold() not in CLOSED and str(d.get('type','')).casefold() in {'betrothal','engagement'}:
            promised[d.get('partner1_id')]=d.get('partner2_id');promised[d.get('partner2_id')]=d.get('partner1_id')
    for other in people:
        if other.id==sim.id or other.id in unavailable or eligibility(other,save,records):continue
        if promised.get(other.id) not in (None,sim.id) or promised.get(sim.id) not in (None,other.id):continue
        if kinship_warning(sim.id,other.id,people,depth):continue
        result.append(other)
    return sorted(result,key=lambda r:(r.label.casefold(),r.id))


def validate_roll(session,save,roll):
    if not enabled(save):raise ValueError('Enable annual marriage rules before resolving this roll.')
    if integer(roll.data.get('planner_year'))!=year_at(save):raise ValueError('This yearly decision has expired. Refresh the rolls for the current year.')
    records=rows(session,save);sim=next((r for r in records if r.id==roll.data.get('sim_id') and r.kind=='sim'),None)
    reason=eligibility(sim,save,records) if sim else 'Sim is no longer available'
    if reason:raise ValueError(reason)
    if roll.data.get('annual_marriage') and not roll.data.get('completed'):
        current,_=specification(sim,save,records)
        if any(roll.data.get(k)!=current.get(k) for k in ('die','success_results','marriage_stage')):
            raise ValueError('Marriage eligibility or age changed. Refresh the yearly rolls before rolling.')
    if roll.data.get('marriage_refusal'):
        origin=session.get(Record,roll.data.get('origin_roll_id'))
        if not origin or origin.deleted or origin.save_id!=save.id or not origin.data.get('completed') or origin.data.get('marriage_decision')!='refusal_pending':
            raise ValueError('This refusal is no longer awaiting a result.')
    return sim,records


def apply_result(session,save,roll,actual):
    sim,records=validate_roll(session,save,roll)
    if roll.data.get('marriage_refusal'):
        origin=session.get(Record,roll.data['origin_roll_id'])
        updates={'marriage_decision':'refused' if actual==1 else 'must_marry','refusal_result':actual}
        if actual==1:updates['suggested_marriage_global_day']=None;updates['suggested_marriage_date_range']=None
        change(session,origin,updates);save.revision+=1
        return 'Refuses marriage; roll again next year' if actual==1 else 'Cannot refuse; proceed with marriage or an arranged match'
    clean={k:v for k,v in roll.data.items() if k not in ('marriage_success','suggested_marriage_global_day','suggested_marriage_date_range','suggested_marriage_date_source')}
    roll.data=clean
    if not domain.failed(actual,roll.data['success_results']):
        roll.data={**roll.data,'marriage_decision':'no_marriage'}
        return 'Does not remarry' if roll.data.get('remarriage_roll') else 'Does not marry'
    available=candidates(sim,save,records)
    roll.data={**roll.data,'marriage_decision':'awaiting_spouse' if available else 'awaiting_match',
               'marriage_candidate_count':len(available),'marriage_success':True}
    if not available:return 'Formal courtship / arranged match — no eligible spouse available; roll again next year'
    first,last=year_bounds(save,year_at(save));suggested=domain.roll_rng(session).randint(max(first,save.global_day),last)
    roll.data={**roll.data,'suggested_marriage_global_day':suggested,
               'suggested_marriage_date_range':domain.calendar_utils.date_range_label(suggested,save.start_year,save.days_per_year),
               'suggested_marriage_date_source':'Annual marriage success — within this calendar year'}
    return 'May remarry' if roll.data.get('remarriage_roll') else 'May marry'


def request_refusal(session,save,origin):
    if not domain.automation_enabled(save):raise ValueError('Turn on automation to schedule the refusal follow-up.')
    validate_roll(session,save,origin)
    if not origin.data.get('completed') or not origin.data.get('marriage_success') or origin.data.get('marriage_decision') not in {'awaiting_spouse','awaiting_match','must_marry','refusal_pending'}:
        raise ValueError('Only an unresolved successful yearly roll may request refusal.')
    existing=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=='roll',Record.deleted.is_(False),
        Record.data['origin_roll_id'].as_string()==origin.id,Record.data['marriage_refusal'].as_boolean().is_(True)))
    if existing:return existing
    if origin.data.get('refusal_result') is not None:raise ValueError('The refusal decision has already been rolled.')
    child=Record(save_id=save.id,kind='roll',label=domain.record_label(f'{origin.data.get("sim_name")} — Refuse this marriage?'),global_day=save.global_day,
        data={'sim_id':origin.data['sim_id'],'sim_name':origin.data.get('sim_name'),'household_id':origin.data.get('household_id'),
              'origin_roll_id':origin.id,'source':f'planner:marriage:refusal:{origin.id}','roll_type':'Marriage refusal',
              'refusal_original_suggested_day':origin.data.get('suggested_marriage_global_day'),
              'marriage_refusal':True,'automatic_followup':True,'planner_year':origin.data['planner_year'],
              'die':'d6','bad_results':'2-6','result_rules':'1: Refuses marriage; 2-6: Cannot refuse',
              'nonlethal':True,'failure_is_lethal':False,'completed':False})
    session.add(child);session.flush();domain.journal(session,child,'upsert',0)
    change(session,origin,{'marriage_decision':'refusal_pending'});save.revision+=2;return child


def plan_match(session,save,origin,spouse_id):
    sim,records=validate_roll(session,save,origin)
    if not origin.data.get('completed') or not origin.data.get('marriage_success') or origin.data.get('marriage_decision') not in {'awaiting_spouse','awaiting_match','must_marry'}:
        raise ValueError('Resolve the yearly result and any refusal roll first.')
    spouse=next((r for r in candidates(sim,save,records) if r.id==spouse_id),None)
    if not spouse:raise ValueError('That spouse is no longer eligible. Review the current candidates.')
    # A second Sim cannot evade their own refusal outcome by accepting this match.
    for r in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='roll',Record.deleted.is_(False),
            Record.data['annual_marriage'].as_boolean().is_(True),Record.data['sim_id'].as_string()==spouse.id)):
        if integer(r.data.get('planner_year'))==year_at(save) and r.data.get('marriage_decision') in {'refused','refusal_pending'}:
            raise ValueError('This Sim has refused marriage or has a refusal roll pending this year.')
    first,last=year_bounds(save,year_at(save));suggested=integer(origin.data.get('suggested_marriage_global_day'))
    if suggested is None or not max(first,save.global_day)<=suggested<=last:
        suggested=max(first,save.global_day)
    existing=next((r for r in records if r.kind=='relationship' and is_active(r)
        and {r.data.get('partner1_id'),r.data.get('partner2_id')}=={sim.id,spouse.id}
        and str(r.data.get('status','Active')).casefold() not in CLOSED
        and str(r.data.get('type','')).casefold() in {'courtship','betrothal','engagement','romantic','love interest'}),None)
    fields={'partner1_id':sim.id,'partner2_id':spouse.id,'partner1_name':sim.label,'partner2_name':spouse.label,
            'type':'Betrothal','status':'Active','legally_married':False,'start_global_day':save.global_day,
            'suggested_marriage_global_day':suggested,'source_marriage_roll_id':origin.id,
            'annual_marriage_year':year_at(save),
            'suggested_marriage_date_range':domain.calendar_utils.date_range_label(suggested,save.start_year,save.days_per_year),
            'source':'Annual era marriage roll','notes':'Marriage required this calendar year. Record the actual wedding after playing it.'}
    if existing:change(session,existing,fields);rel=existing
    else:
        rel=Record(save_id=save.id,kind='relationship',label=domain.record_label(f'{sim.label} & {spouse.label}'),global_day=save.global_day,data=fields)
        session.add(rel);session.flush();domain.journal(session,rel,'upsert',0)
    change(session,origin,{'marriage_decision':'planned','marriage_plan_id':rel.id,'chosen_spouse_id':spouse.id,'suggested_marriage_global_day':suggested})
    # Resolve the other half of the same plan without inventing a die result.
    for r in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='roll',Record.deleted.is_(False),
            Record.data['annual_marriage'].as_boolean().is_(True),Record.data['sim_id'].as_string()==spouse.id)):
        if r.id==origin.id or integer(r.data.get('planner_year'))!=year_at(save) or r.data.get('infinite_frozen'):continue
        if not r.data.get('completed'):retire(session,save,r,'Marriage planned from the eligible partner’s successful annual roll')
        elif r.data.get('marriage_success'):change(session,r,{'marriage_decision':'planned','marriage_plan_id':rel.id,'chosen_spouse_id':sim.id})
    save.revision+=3
    return rel


def reopen_origin(session,save,roll):
    """Return the parent for a refusal correction; never erase a planned wedding."""
    if not (roll.data.get('annual_marriage') or roll.data.get('marriage_refusal')):return None
    if roll.data.get('marriage_plan_id'):raise ValueError('This result already has a betrothal or wedding plan. Edit the relationship instead; its source result is retained.')
    if roll.data.get('annual_marriage'):
        completed=session.scalar(select(Record.id).where(Record.save_id==save.id,Record.deleted.is_(False),
            Record.kind=='roll',Record.data['origin_roll_id'].as_string()==roll.id,
            Record.data['marriage_refusal'].as_boolean().is_(True),Record.data['completed'].as_boolean().is_(True)))
        if completed:raise ValueError('Reopen the completed refusal roll first so its decision is not silently discarded.')
        return None
    parent=session.get(Record,roll.data.get('origin_roll_id'))
    if not parent or parent.deleted or parent.save_id!=save.id or not parent.data.get('completed') or parent.data.get('marriage_plan_id'):
        raise ValueError('The original yearly decision is no longer available for correction.')
    return parent
