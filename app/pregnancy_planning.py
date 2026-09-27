"""One lifetime allowance, plus independent yearly yes/no pregnancy decisions."""
import re
from sqlalchemy import select, update
from .models import Record, ChronicleSave
from . import domain, record_state

AGE_BANDS=((13,17,1),(18,24,7),(25,29,6),(30,34,5),(35,39,4),(40,44,2),(45,49,1))


def eligibility_reason(session,save,sim):
    d=sim.data
    house=session.get(Record,d.get('current_household_id')) if d.get('current_household_id') else None
    if house and (house.save_id!=save.id or house.deleted):house=None
    for subject in (sim,house):
        if not subject:continue
        if subject.data.get('pregnancy_rule_permission')=='blocked':
            return 'Children are not permitted by this Sim or household’s recorded timeline, class or theme rule.'
        first=record_state.integer(subject.data.get('pregnancy_available_from_year'))
        if first is not None and year(save)<first:return f'Children are not permitted until {first}.'
    if d.get('pregnancy_rule_permission')=='allowed' or (house and house.data.get('pregnancy_rule_permission')=='allowed'):return ''
    birth=record_state.integer(d.get('birth_global_day'),sim.global_day)
    age=(save.global_day-birth)//max(1,save.days_per_year) if birth is not None else None
    if age is not None and age<18:return 'Confirm that this Teen is allowed children under the timeline, class and theme rules in Roll Automation.'
    return ''


def annual_specification(session,save,sim,records=None):
    cfg=(save.settings or {}).get('roll_automation') or {}
    if cfg.get('pregnancy_yearly_mode','age_table')=='custom':
        table=annual_table(cfg)
        return {'die':table[0],'success_results':table[1],'modifiers':[],'mode':'custom'} if table else None
    start=1+(year(save)-save.start_year)*max(1,save.days_per_year)
    birth=record_state.integer(sim.data.get('birth_global_day'),sim.global_day)
    if birth is None:return None
    age=(start-birth)//max(1,save.days_per_year)
    base=next((wins for low,high,wins in AGE_BANDS if low<=age<=high),None)
    if base is None:return None
    all_rows=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.deleted.is_(False),Record.kind.in_(['sim','relationship','pregnancy','household'])))) if records is None else records
    by_id={r.id:r for r in all_rows};house=sim.data.get('current_household_id')
    heir=by_id.get((save.settings or {}).get('current_heir_id'))
    heir_house=heir.data.get('current_household_id') if heir else (save.settings or {}).get('heir_household_id')
    mods=[]
    if house and house==heir_house:mods.append({'label':'Heir household','value':1})
    partnered=False
    for r in all_rows:
        if r.kind!='relationship' or r.data.get('infinite_frozen'):continue
        d=r.data
        if sim.id not in {d.get('partner1_id'),d.get('partner2_id')}:continue
        began=record_state.integer(d.get('marriage_global_day'),record_state.integer(d.get('start_global_day'),r.global_day or 1))
        ended=record_state.integer(d.get('end_global_day'))
        if began>save.global_day or (ended is not None and ended<=save.global_day):continue
        if str(d.get('status','active')).lower() in {'widowed','divorced','separated','ended','annulled','abandoned','closed'}:continue
        partner=by_id.get(d.get('partner2_id') if d.get('partner1_id')==sim.id else d.get('partner1_id'))
        if not partner or not record_state.living(partner,save.global_day):continue
        if d.get('legally_married') or d.get('established_partnership') or str(d.get('type','')).lower() in {'marriage','married','partnership','established partnership','domestic partnership'}:
            partnered=True;break
    if sim.data.get('pregnancy_established_partnership'):partnered=True
    mods.append({'label':'Married or established partnership' if partnered else 'Widowed, separated or no partner','value':1 if partnered else -2})
    gave_birth=False
    for r in all_rows:
        if r.kind=='pregnancy' and r.data.get('mother_id')==sim.id and domain.counts_against_pregnancy_allowance(r):
            if str(r.data.get('status') or '').lower() in {'delivered','complete','completed','stillborn','stillbirth'}:
                day=record_state.integer(r.data.get('actual_delivery_global_day'),record_state.integer(r.data.get('delivery_global_day'),r.global_day))
                gave_birth=gave_birth or (day is not None and year(save,day)==year(save)-1)
        if r.kind=='sim' and r.data.get('mother_id')==sim.id:
            day=record_state.integer(r.data.get('birth_global_day'),r.global_day)
            gave_birth=gave_birth or (day is not None and year(save,day)==year(save)-1)
    if gave_birth:mods.append({'label':'Gave birth during the previous year','value':-4})
    has_young_child=False
    for r in all_rows:
        if not house or r.kind!='sim' or r.data.get('current_household_id')!=house or not record_state.living(r,save.global_day):continue
        if r.data.get('infinite_frozen'):continue
        day=record_state.integer(r.data.get('birth_global_day'),r.global_day)
        if day is not None:
            age_days=save.global_day-day
            is_young=domain.lifecycle_age_days(save,1)<=age_days<domain.lifecycle_age_days(save,20)
        else:is_young=str(r.data.get('game_age_stage') or '').lower() in {'infant','toddler'}
        has_young_child=has_young_child or is_young
    if has_young_child:mods.append({'label':'Household has an Infant or Toddler','value':-2})
    if sim.data.get('pregnancy_fertility_boost'):mods.append({'label':'Fertility treatment or theme fertility bonus','value':2})
    high=max(1,min(10,base+sum(m['value'] for m in mods)))
    return {'die':'d20','success_results':'1' if high==1 else f'1-{high}','age':age,'base':base,'modifiers':mods,'success_high':high,'mode':'age_table','year_start_global_day':start}


def year(save, day=None):
    return save.start_year + ((save.global_day if day is None else day) - 1) // max(1, save.days_per_year)


def lock_save(session, save):
    session.flush()
    session.execute(update(ChronicleSave).where(ChronicleSave.id==save.id).values(revision=ChronicleSave.revision))


def conception_day(save, pregnancy):
    explicit=record_state.integer(pregnancy.data.get('conception_global_day'))
    if explicit is not None:return explicit
    due=record_state.integer(pregnancy.data.get('due_global_day'),pregnancy.global_day)
    return due-save.pregnancy_days if due is not None else save.global_day


def count_rolls(session, save, sim):
    return list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == 'roll', Record.deleted.is_(False),
        Record.data['sim_id'].as_string() == sim.id,
        Record.data['pregnancy_count_roll'].as_boolean().is_(True),
    ).order_by(Record.global_day, Record.created_at, Record.id)))


def canonical(session, save, sim, rolls=None):
    rolls = count_rolls(session, save, sim) if rolls is None else rolls
    by_id = {r.id:r for r in rolls}
    # Keep the allowance the player already sees, not a new annual reroll.
    for key in ('pregnancy_lifetime_roll_id', 'pregnancy_allowance_roll_id'):
        if sim.data.get(key) in by_id:
            return by_id[sim.data[key]]
    return next((r for r in rolls if r.data.get('completed') and r.data.get('pregnancy_count') is not None),
                next((r for r in rolls if not r.data.get('completed')), None))


def status(session, save, sim):
    rolls = count_rolls(session, save, sim)
    chosen = canonical(session, save, sim, rolls)
    entry = None
    if chosen and chosen.data.get('completed') and chosen.data.get('pregnancy_count') is not None:
        d = chosen.data
        entry = {'allowed':max(0, int(d['pregnancy_count'])), 'roll_id':chosen.id,
                 'year':int(d.get('planner_year') or year(save, chosen.global_day or save.global_day)),
                 'recorded_global_day':d.get('completed_global_day')}
    elif chosen is None and sim.data.get('pregnancy_allowance_count') is not None:
        entry = {'allowed':max(0, int(sim.data['pregnancy_allowance_count'])),
                 'roll_id':sim.data.get('pregnancy_allowance_roll_id'),
                 'year':int(sim.data.get('pregnancy_allowance_year') or year(save)),
                 'recorded_global_day':sim.data.get('pregnancy_allowance_recorded_global_day')}
    pregnancies = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == 'pregnancy', Record.deleted.is_(False),
        Record.data['mother_id'].as_string() == sim.id)))
    used = sum(domain.counts_against_pregnancy_allowance(p) and
               conception_day(save,p) <= save.global_day
               for p in pregnancies)
    if entry is not None:
        entry.update(used=used, remaining=max(0, entry['allowed']-used), scope='lifetime')
    return {'current':entry, 'rows':[entry] if entry else [], 'scope':'lifetime',
            'legacy_results':sum(bool(r.data.get('completed')) for r in rolls if r != chosen)}


def reconcile(session, save):
    """Retire duplicate unfinished counts; never rewrite completed throws."""
    changed = 0
    participants=select(Record.data['sim_id'].as_string()).where(Record.save_id==save.id,Record.kind=='roll',Record.deleted.is_(False),Record.data['pregnancy_count_roll'].as_boolean().is_(True))
    sims = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == 'sim', Record.deleted.is_(False),Record.id.in_(participants))))
    for sim in sims:
        if sim.data.get('infinite_frozen'):
            continue
        rolls = count_rolls(session, save, sim)
        chosen = canonical(session, save, sim, rolls)
        if chosen is None:
            continue
        complete = chosen.data.get('completed') and chosen.data.get('pregnancy_count') is not None
        for roll in rolls:
            if roll == chosen or roll.data.get('completed') or roll.data.get('infinite_frozen'):
                continue
            base=roll.version; roll.deleted=True; roll.version+=1
            roll.data={**roll.data, 'retired_reason':'Duplicate lifetime pregnancy allowance', 'retired_global_day':save.global_day}
            domain.journal(session, roll, 'delete', base); changed+=1
        if not complete:
            continue
        d=chosen.data; yr=int(d.get('planner_year') or year(save, chosen.global_day or save.global_day))
        entry={'allowed':int(d['pregnancy_count']), 'roll_id':chosen.id, 'recorded_global_day':d.get('completed_global_day'), 'actual':d.get('actual')}
        allowances=dict(sim.data.get('pregnancy_allowances') or {})
        allowances[str(yr)]=entry
        values={'pregnancy_allowances':allowances, 'pregnancy_allowance_scope':'lifetime',
                'pregnancy_lifetime_roll_id':chosen.id, 'pregnancy_allowance_count':entry['allowed'],
                'pregnancy_allowance_year':yr, 'pregnancy_allowance_roll_id':chosen.id,
                'pregnancy_allowance_recorded_global_day':entry['recorded_global_day']}
        if any(sim.data.get(k)!=v for k,v in values.items()):
            base=sim.version; sim.data={**sim.data, **values}; sim.version+=1
            domain.journal(session, sim, 'upsert', base); changed+=1
        _, updated, _=domain.sync_family_plan_from_pregnancy_roll(session, save, chosen, sim, entry['allowed'])
        changed+=int(updated)
        for plan in session.scalars(select(Record).where(Record.save_id==save.id, Record.kind=='family_plan',
                Record.deleted.is_(False), Record.data['sim_id'].as_string()==sim.id)):
            if plan.data.get('source_pregnancy_roll_id') in {r.id for r in rolls if r!=chosen} and plan.data.get('active', True) and not plan.data.get('infinite_frozen'):
                base=plan.version; plan.data={**plan.data, 'active':False, 'retired_reason':'Superseded duplicate annual allowance plan; retained as history'}; plan.version+=1
                domain.journal(session, plan, 'upsert', base); changed+=1
    save.revision+=changed
    return changed


def annual_table(config):
    die=str(config.get('pregnancy_yearly_die') or '').strip().lower()
    success=str(config.get('pregnancy_yearly_success') or '').strip().replace('–','-')
    if not die and not success:
        return None
    if not re.fullmatch(r'd(?:[2-9]|[1-9][0-9]{1,2}|1000)', die) or not success:
        raise ValueError('Set a yearly pregnancy die (d2–d1000) and its successful results.')
    sides=int(die[1:]); numbers=set()
    for clause in success.split(','):
        match=re.fullmatch(r'\s*(\d+)(?:\s*-\s*(\d+))?\s*',clause)
        if not match:
            raise ValueError('Use numbered yearly success results, such as 1 or 1-2.')
        low=int(match[1]); high=int(match[2] or low)
        if not 1<=low<=high<=sides:
            raise ValueError('Yearly pregnancy success results must fit the selected die.')
        numbers.update(range(low,high+1))
    return die, ','.join(str(n) for n in sorted(numbers))


def annual_reason(session, save, sim, records=None):
    from . import roll_automation as auto
    if not auto.living(sim,save):return 'Choose a living Sim in the active branch.'
    if sim.data.get('infertile'):return 'This Sim is recorded as infertile.'
    restriction=eligibility_reason(session,save,sim)
    if restriction:return restriction
    if not annual_specification(session,save,sim,records):return 'No natural annual baby roll applies outside ages 13–49, or the selected custom odds are missing.'
    capable=sim.data.get('can_be_pregnant')
    if capable is not None and str(capable).lower() not in {'true','yes','1'}:return 'This Sim is recorded as unable to become pregnant.'
    if capable is None and str(sim.data.get('sex') or '').lower() not in {'female','f'}:return 'Record pregnancy capability before scheduling an Annual Baby Roll.'
    allowance=status(session,save,sim)['current']
    if not allowance:return 'Complete the lifetime pregnancy allowance first.'
    if not allowance['remaining']:return 'This Sim has used their lifetime pregnancy allowance.'
    children=(r for r in records if r.kind=='sim' and r.data.get('mother_id')==sim.id) if records is not None else session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='sim',Record.deleted.is_(False),Record.data['mother_id'].as_string()==sim.id))
    for child in children:
        birth=record_state.integer(child.data.get('birth_global_day'),child.global_day)
        if birth is not None and birth<=save.global_day and year(save,birth)==year(save):return 'A birth is already recorded for this historical year.'
    for p in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='pregnancy',Record.deleted.is_(False),Record.data['mother_id'].as_string()==sim.id)):
        if not domain.counts_against_pregnancy_allowance(p):continue
        conception=conception_day(save,p)
        if conception>save.global_day:continue
        if str(p.data.get('status') or '').lower() in {'delivered','completed','complete','stillborn','stillbirth'}:
            delivery=record_state.integer(p.data.get('actual_delivery_global_day'),record_state.integer(p.data.get('delivery_global_day'),p.global_day))
            if delivery is not None and delivery<=save.global_day and year(save,delivery)==year(save):return 'A birth is already recorded for this historical year.'
        if domain.pregnancy_allowance_year(save,p)==year(save):return 'A pregnancy is already recorded for this historical year.'
        if str(p.data.get('status') or 'active').lower() in {'active','pregnant','expecting'}:return 'This Sim is already pregnant.'
    return ''


def create_annual(session, save, sim, records=None):
    if sim.kind!='sim' or sim.save_id!=save.id or sim.deleted:raise ValueError('Choose a Sim from the active save.')
    lock_save(session,save)
    source=f'planner:pregnancy-yearly:{sim.id}:{year(save)}'
    existing=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=='roll',Record.deleted.is_(False),Record.data['source'].as_string()==source))
    if existing:return existing,False
    reason=annual_reason(session,save,sim,records)
    if reason:raise ValueError(reason)
    spec=annual_specification(session,save,sim,records)
    die,success=spec['die'],spec['success_results']; allowance=status(session,save,sim)['current']
    cfg=(save.settings or {}).get('roll_automation') or {}
    due=max(1+(year(save)-save.start_year)*max(1,save.days_per_year),record_state.integer((cfg.get('from') or {}).get('pregnancy_yearly'),save.global_day))
    row=Record(save_id=save.id,kind='roll',label=f'{sim.label} — Annual Baby Roll · {year(save)}',global_day=due,data={
        'sim_id':sim.id,'sim_name':sim.label,'household_id':sim.data.get('current_household_id'),
        'source':source,'source_id':source,'roll_type':'Annual Baby Roll','pregnancy_yearly_roll':True,'annual_baby_spec':spec,'modifier_notes':'; '.join(f"{m['label']} {m['value']:+d}" for m in spec['modifiers']),
        'planner_year':year(save),'due_global_day':due,'die':die,'success_results':success,
        'result_rules':f'{success}: Have one baby this year; all other results: No pregnancy this year',
        'bad_results':'','nonlethal':True,'failure_is_lethal':False,'completed':False,
        'origin_roll_id':allowance.get('roll_id'),'automatic_followup':True,'notes':'Annual Baby Roll: age at the beginning of the year, with recorded modifiers. A success calls for one baby this year; use the normal multiples, birth, newborn, infant and maternal rolls. Record the actual pregnancy/birth in game; this result does not invent one.'})
    session.add(row);session.flush();domain.journal(session,row,'upsert',0);save.revision+=1
    return row,True


def yearly_result(session, save, roll, actual):
    sim=session.get(Record,roll.data.get('sim_id'))
    if not sim or sim.save_id!=save.id:raise ValueError('The pregnancy participant is unavailable.')
    if int(roll.data.get('planner_year',year(save)))!=year(save):raise ValueError('This yearly pregnancy check has expired. Use the current year’s check.')
    reason=annual_reason(session,save,sim)
    if reason:raise ValueError(reason)
    if not 1<=actual<=int(roll.data['die'][1:]):raise ValueError('The result is outside the yearly pregnancy die.')
    success=domain.failed(actual,str(roll.data.get('success_results') or ''))
    roll.data={**roll.data,'pregnancy_yearly_success':success,'nonlethal':True,'failure_is_lethal':False}
    if success and domain.automation_enabled(save):
        task=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=='task',Record.data['source_pregnancy_yearly_roll_id'].as_string()==roll.id))
        if not task:
            task=Record(save_id=save.id,kind='task',label=f'{sim.label} — Have a baby in {year(save)}',global_day=save.global_day,
                data={'feature':'heritage_commitment','pregnancy_yearly_task':True,'sim_id':sim.id,'household_id':sim.data.get('current_household_id'),
                      'source_pregnancy_yearly_roll_id':roll.id,'planner_year':year(save),'due_global_day':save.global_day,
                      'status':'Open','completed':False,'notes':'Have one baby this year. Use normal Several UDO multiples, birth, newborn, infant-survival and maternal-mortality rules. Clock Sync or a manual pregnancy record consumes one lifetime allowance, not this decision.'})
            session.add(task);session.flush();domain.journal(session,task,'upsert',0);save.revision+=1
        elif task.deleted:
            base=task.version;task.deleted=False;task.data={**task.data,'completed':False,'status':'Open'};task.version+=1
            domain.journal(session,task,'upsert',base);save.revision+=1
    return 'Have one baby this year — normal birth and maternal checks apply' if success else 'No pregnancy this year'


def schedule(session, save, people=None, all_rows=None):
    from . import roll_automation as auto
    if not domain.automation_enabled(save):return
    cfg=auto.config(save)
    if not (auto.enabled(save,'pregnancy') or auto.enabled(save,'pregnancy_yearly')):return
    reconcile(session,save)
    all_rows=auto.rows(session,save,['sim','relationship','household','pregnancy']) if all_rows is None else list(all_rows)+auto.rows(session,save,['pregnancy'])
    people=[r for r in all_rows if auto.living(r,save)] if people is None else people
    heir=next((s for s in people if s.id==(save.settings or {}).get('current_heir_id')),None)
    main_house=(save.settings or {}).get('main_household_id') or (save.settings or {}).get('current_household_id') or (heir.data.get('current_household_id') if heir else None)
    married=set()
    for r in all_rows:
        if r.kind=='relationship' and auto.active(r) and (r.data.get('legally_married') or str(r.data.get('type','')).lower()=='marriage') and str(r.data.get('status','active')).lower() not in {'widowed','divorced','separated','ended','annulled','abandoned','complete','closed'}:
            married.update((r.data.get('partner1_id'),r.data.get('partner2_id')))
    for sim in people:
        d=sim.data
        age=(save.global_day-record_state.integer(d.get('birth_global_day'),sim.global_day or 1))//max(1,save.days_per_year)
        capable=d.get('can_be_pregnant',str(d.get('sex','')).lower() in {'female','f'})
        if isinstance(capable,str):capable=capable.lower() in {'true','yes','1'}
        if not capable or d.get('infertile') or eligibility_reason(session,save,sim):continue
        for key in ('pregnancy','pregnancy_yearly'):
            if not auto.enabled(save,key):continue
            if key=='pregnancy' and not record_state.integer(cfg.get('pregnancy_min_age'),13)<=age<=record_state.integer(cfg.get('pregnancy_max_age'),49):continue
            if cfg.get(key+'_married_only',key=='pregnancy') and sim.id not in married:continue
            if cfg.get(key+'_side_only',key=='pregnancy') and (not main_house or not d.get('current_household_id') or d.get('current_household_id')==main_house):continue
            if key=='pregnancy' and status(session,save,sim)['current']:continue
            if key=='pregnancy_yearly':
                try:
                    if annual_reason(session,save,sim,all_rows):continue
                except ValueError:
                    continue # Imported custom odds must not break other automation.
            source=f'planner:pregnancy-count:{sim.id}:lifetime' if key=='pregnancy' else f'planner:pregnancy-yearly:{sim.id}:{year(save)}'
            old=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=='roll',Record.data['source'].as_string()==source))
            if key=='pregnancy' and old is None and canonical(session,save,sim) is None:
                # Legacy yearly-source tombstones still represent a player's
                # dismissal; changing identity to lifetime must not revive them.
                retired=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='roll',Record.deleted.is_(True),Record.data['sim_id'].as_string()==sim.id,Record.data['pregnancy_count_roll'].as_boolean().is_(True))))
                if any(not r.data.get('extra_auto_paused') for r in retired):continue
                old=next((r for r in retired if r.data.get('extra_auto_paused')),None)
            if old and old.deleted:
                if old.data.get('extra_auto_paused') and not old.data.get('completed'):
                    old.deleted=False; auto.change(session,old,{'extra_auto_paused':False,'retired_reason':''});save.revision+=1
                continue
            try:
                roll,created=domain.create_pregnancy_count_roll(session,save,sim) if key=='pregnancy' else create_annual(session,save,sim,all_rows)
                if created:auto.change(session,roll,{'extra_automation':key,'automation_year':year(save),'extra_annual':key=='pregnancy_yearly'})
                elif key=='pregnancy_yearly' and not roll.data.get('completed'):
                    spec=annual_specification(session,save,sim,all_rows)
                    if spec:
                        die,success=spec['die'],spec['success_results']
                        values={'die':die,'success_results':success,'annual_baby_spec':spec,'modifier_notes':'; '.join(f"{m['label']} {m['value']:+d}" for m in spec['modifiers']),'result_rules':f'{success}: Have one baby this year; all other results: No pregnancy this year'}
                        if any(roll.data.get(k)!=v for k,v in values.items()):
                            auto.change(session,roll,values);save.revision+=1
            except ValueError:
                continue # Missing rules/odds are explained on Roll Automation.
    # Old yearly decisions never become extra attempts in a later year.
    for row in auto.rows(session,save,['roll','task']):
        d=row.data
        if not auto.active(row) or d.get('completed'):continue
        if d.get('pregnancy_yearly_roll'):
            actor=session.get(Record,d.get('sim_id'))
            reason='Yearly pregnancy decision expired' if record_state.integer(d.get('planner_year'),year(save))<year(save) else ''
            if not reason and actor and actor.save_id==save.id:
                try:reason=annual_reason(session,save,actor,all_rows)
                except ValueError:reason='Custom yearly pregnancy odds need correction'
            if reason:
                base=row.version;row.deleted=True;row.data={**d,'retired_reason':reason};row.version+=1
                domain.journal(session,row,'delete',base);save.revision+=1
        if d.get('pregnancy_yearly_task'):
            sim=session.get(Record,d.get('sim_id'))
            if not sim or sim.save_id!=save.id:continue
            pregnancies=session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='pregnancy',Record.deleted.is_(False),Record.data['mother_id'].as_string()==sim.id))
            fulfilled=any(domain.counts_against_pregnancy_allowance(p) and str(p.data.get('status','')).lower() in {'delivered','completed','complete','stillborn','stillbirth'} and
                year(save,record_state.integer(p.data.get('actual_delivery_global_day'),record_state.integer(p.data.get('delivery_global_day'),p.global_day or save.global_day)))==d.get('planner_year') for p in pregnancies)
            fulfilled=fulfilled or any(s.kind=='sim' and s.data.get('mother_id')==sim.id and
                record_state.integer(s.data.get('birth_global_day'),s.global_day or save.global_day)<=save.global_day and
                year(save,record_state.integer(s.data.get('birth_global_day'),s.global_day or save.global_day))==d.get('planner_year') for s in all_rows)
            if fulfilled or record_state.integer(d.get('planner_year'),year(save))<year(save):
                auto.change(session,row,{'completed':True,'status':'Completed' if fulfilled else 'Expired','completed_global_day':save.global_day,'resolution':'Birth recorded' if fulfilled else 'Year ended; no birth inferred'})
                save.revision+=1
