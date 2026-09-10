"""Optional household-history tools built on the existing synced note/task records."""
import hashlib
from collections import defaultdict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .models import Record
from . import domain, infinite_decades, names

PREFIX='heritage_'
FEATURES=('property','residence','title','claim','custom','routine','routine_task','thread','commitment')
SEASONS=('Spring','Summer','Autumn','Winter')
CLOSED={'Completed','Resolved','Kept','Broken','Released','Waived','Archived'}

def integer(value,default=None):
    try:return int(value)
    except (ValueError,TypeError):return default

def feature(row):return str((row.data or {}).get('feature','')).removeprefix(PREFIX)

def rows_for(session,save):
    return list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind.in_(['note','task']),Record.deleted.is_(False),Record.data['feature'].as_string().in_([PREFIX+x for x in FEATURES]))))

def record_id(save_id,key):return hashlib.sha256(f'{save_id}:heritage:{key}'.encode()).hexdigest()[:32]

def season_day(save,year,season):
    # Four equal calendar quarters, independent of game seasons/hemisphere.
    return 1+(year-save.start_year)*save.days_per_year+(SEASONS.index(season)*save.days_per_year)//4

def schedule_routines(session,save):
    if not domain.automation_enabled(save) or infinite_decades.frozen(save):return 0
    count=0
    pending=session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='task',Record.deleted.is_(False),
        Record.data['feature'].as_string()==PREFIX+'routine_task',Record.data['status'].as_string()=='Open',Record.data['completed'].as_boolean().is_not(True),Record.data['infinite_frozen'].as_boolean().is_not(True)))
    for task in pending:
        year=integer(task.data.get('year'));season=task.data.get('season')
        if year is None or season not in SEASONS:continue
        due=season_day(save,year,season)
        if task.global_day!=due:
            base=task.version;task.global_day=due;task.data={**task.data,'due_global_day':due};task.version+=1
            domain.journal(session,task,'upsert',base);count+=1
    routines=session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='task',Record.deleted.is_(False),Record.data['feature'].as_string()==PREFIX+'routine'))
    year=save.start_year+(save.global_day-1)//save.days_per_year
    for routine in routines:
        d=routine.data
        if d.get('status')!='Active' or d.get('infinite_frozen'):continue
        if year<integer(d.get('from_year'),save.start_year) or year>integer(d.get('until_year'),year):continue
        due=season_day(save,year,d.get('season','Spring'))
        key=record_id(save.id,f'routine:{routine.id}:{year}')
        prior=session.get(Record,key)
        if prior:
            if not prior.deleted and not prior.data.get('completed') and prior.data.get('status')=='Open' and prior.global_day!=due:
                base=prior.version;prior.global_day=due;prior.data={**prior.data,'due_global_day':due};prior.version+=1
                domain.journal(session,prior,'upsert',base);count+=1
            continue
        # Never manufacture tasks from before this routine was enabled.
        if due>save.global_day or due<integer(d.get('enabled_from_global_day'),save.global_day):continue
        row=Record(id=key,save_id=save.id,kind='task',label=f'{routine.label} — {year}',global_day=due,data={
            'feature':PREFIX+'routine_task','routine_id':routine.id,'household_id':d.get('household_id'),
            'due_global_day':due,'year':year,'season':d.get('season'),'next_step':d.get('instructions',''),
            'status':'Open','completed':False,'private':d.get('private',False)})
        try:
            with session.begin_nested():session.add(row);session.flush()
        except IntegrityError:
            continue # Another Today request already created this year's task.
        domain.journal(session,row,'upsert',0);count+=1
    save.revision+=count
    return count

def due_tasks_sql(save):
    return (Record.kind=='task') & Record.data['feature'].as_string().in_([PREFIX+x for x in ('routine_task','thread','commitment')]) & Record.data['status'].as_string().in_(['Open','Active']) & Record.data['due_global_day'].as_integer().is_not(None) & Record.data['infinite_frozen'].as_boolean().is_not(True)

def property_history(prop,rows,people,events,save):
    stays=[r for r in rows if feature(r)=='residence' and r.data.get('property_id')==prop.id and r.data.get('status')!='Archived']
    stays.sort(key=lambda r:(integer(r.data.get('from_global_day'),0),r.label))
    today=save.global_day
    current=[r for r in stays if integer(r.data.get('from_global_day'),today+1)<=today and (integer(r.data.get('until_global_day')) is None or r.data['until_global_day']>=today) and r.data.get('status')!='Archived']
    associations=[]
    for event in events:
        day=event.global_day
        if day is None or day>today:continue
        if event.kind=='death' and not event.data.get('completed'):continue
        if event.data.get('property_id')==prop.id:
            associations.append({'event':event,'evidence':'Explicit property link'})
        elif any(event.data.get('sim_id')==r.data.get('sim_id') and r.data.get('sim_id') and integer(r.data.get('from_global_day'),today+1)<=day and (integer(r.data.get('until_global_day')) is None or day<=r.data['until_global_day']) for r in stays):
            associations.append({'event':event,'evidence':'Associated through a recorded resident; venue not confirmed'})
    for sim in people:
        for key,label in [('birth_global_day','Birth'),('death_global_day','Death')]:
            day=integer(sim.data.get(key))
            if day is None or day>today:continue
            if key=='death_global_day' and not (sim.data.get('death_confirmed') or sim.data.get('game_was_dead')):continue
            if any(r.data.get('sim_id')==sim.id and integer(r.data.get('from_global_day'),today+1)<=day and (integer(r.data.get('until_global_day')) is None or day<=r.data['until_global_day']) for r in stays):
                associations.append({'event':type('Fact',(),{'label':f'{label} of {sim.label}','global_day':day})(),'evidence':'Associated through recorded residence; venue not confirmed'})
    return {'stays':stays,'current':current,'events':sorted(associations,key=lambda v:v['event'].global_day,reverse=True)}

def custom_names(session,save,custom,people,form):
    d=custom.data;year=save.start_year+(save.global_day-1)//save.days_per_year
    in_era=integer(d.get('from_year'),year)<=year<=integer(d.get('until_year'),year)
    if not in_era and form.get('override_era')!='on':raise ValueError('This custom is outside its configured era. Choose the explicit era override to use it.')
    by_id={s.id:s for s in people};relative=by_id.get(form.get('relative_id'));parent=by_id.get(form.get('parent_id'))
    pool=names.libraries(session,save.id)
    suggestions=names.generate(pool,d.get('culture',''),form.get('sex','Any'),5,surname_culture=d.get('surname_culture',''))
    if not suggestions:raise ValueError('This culture has no matching given names. Add source entries in the name library.')
    if d.get('given_mode')=='Relative' and not relative:raise ValueError('Choose the relative whose given name should be reused.')
    mode=d.get('surname_mode','Regional pool')
    if mode in {'Patronymic','Family surname'} and not parent:raise ValueError('Choose the parent for this naming custom.')
    out=[];seen=set()
    for suggestion in suggestions:
        first=(relative.data.get('first_name') or relative.label.split()[0]) if d.get('given_mode')=='Relative' else suggestion['first_name']
        surname=suggestion['last_name']
        if mode=='Patronymic':surname=d.get('patronymic_pattern','{parent}').replace('{parent}',parent.data.get('first_name') or parent.label.split()[0])
        elif mode=='Family surname':surname=parent.data.get('last_name') or ''
        elif mode=='None':surname=''
        full=' '.join(x for x in [d.get('style_prefix',''),first,surname,d.get('style_suffix','')] if x)
        if full in seen:continue
        seen.add(full);out.append({'name':full,'basis':f'{custom.label} · {d.get("culture")} · {mode}'+(f' · named after {relative.label}' if d.get('given_mode')=='Relative' else ''),'override':not in_era})
    return out

def catchup_eligible(row,save,cutoff):
    if row.deleted or row.data.get('infinite_frozen') or row.data.get('completed'):return False
    day=integer(row.data.get('due_global_day'),row.global_day)
    if day is None or not 1<=day<=cutoff or day>=save.global_day:return False
    if row.kind=='roll':return True
    return row.kind=='task' and feature(row) in {'routine_task','thread','commitment'} and row.data.get('status') in {'Open','Active'}

def apply_catchup(session,save,row,choice,reason):
    base=row.version
    label='Waived by player — no die thrown' if choice=='waive' else 'Historically completed by player — no die thrown'
    row.data={**row.data,'completed':True,'completed_global_day':integer(row.data.get('due_global_day'),row.global_day),
              'catch_up_resolution':choice,'catch_up_recorded_global_day':save.global_day,'catch_up_reason':reason,
              'outcome':label,'status':'Waived' if choice=='waive' else 'Completed'}
    # Do not invoke complete_roll: an administrative historical resolution is
    # not a dice result, death instruction, inheritance award or follow-up trigger.
    row.version+=1;domain.journal(session,row,'upsert',base);save.revision+=1

def chronicle_data(save,people,facts,portraits,selected,from_year,through_year,options):
    current_year=save.start_year+(save.global_day-1)//save.days_per_year
    through_year=min(through_year,current_year)
    cutoff=min(save.global_day,(through_year-save.start_year+1)*save.days_per_year)
    start=1+(from_year-save.start_year)*save.days_per_year
    def birth_date(person):
        data=person.data;year=integer(data.get('birth_year'))
        if data.get('birth_year_only') or (data.get('birth_global_day') is None and year is not None):return None,year
        return integer(data.get('birth_global_day'),person.global_day),None
    chosen=[p for p in people if p.id in selected and (birth_date(p)[0] is None or birth_date(p)[0]<=cutoff) and (birth_date(p)[1] is None or birth_date(p)[1]<=through_year)]
    ids={p.id for p in chosen};by_id={p.id:p for p in chosen};chapters=defaultdict(list)
    cards=[]
    for person in chosen:
        d=person.data;birth,birth_year=birth_date(person)
        death=integer(d.get('death_global_day')) if d.get('death_confirmed') or d.get('game_was_dead') else None
        death=death if death is not None and death<=cutoff else None
        parents=[by_id[x] for x in dict.fromkeys([d.get('mother_id'),d.get('father_id')]) if x in by_id] if options.get('tree') else []
        birth_label=f'Year {birth_year} (year-only)' if birth_year is not None else f'Global Day {birth}' if birth is not None else 'Date unknown'
        cards.append({'id':person.id,'name':person.label,'birth':birth,'birth_label':birth_label,'death':death,'parents':parents,'portrait':portraits.get(person.id) if options.get('portraits') else None})
        if birth_year is not None and from_year<=birth_year<=through_year:chapters[birth_year].append({'day':None,'text':'Birth of '+person.label+' (year-only date)'})
        for day,description in [(birth,'Birth of '+person.label),(death,'Death of '+person.label)]:
            if day is not None and start<=day<=cutoff:chapters[save.start_year+(day-1)//save.days_per_year].append({'day':day,'text':description})
    for fact in facts:
        if fact.global_day is None or not start<=fact.global_day<=cutoff:continue
        if fact.data.get('private') and not options.get('private'):continue
        # Only explicitly selected facts enter an export. Never serialize data,
        # automation payloads, workspace credentials, notes or hidden parentage.
        text=fact.label
        if fact.kind=='story_entry' and options.get('narration'):text+=' — '+str(fact.data.get('body') or '')
        chapters[save.start_year+(fact.global_day-1)//save.days_per_year].append({'day':fact.global_day,'text':text})
    years=[]
    for year in range(from_year,through_year+1):
        facts_for_year=sorted(chapters.get(year,[]),key=lambda x:(x['day'] is None,x['day'] or 0))
        if not facts_for_year and years and not years[-1]['facts']:years[-1]['through']=year
        else:years.append({'year':year,'through':year,'facts':facts_for_year})
    return {'title':save.name,'through':through_year,'cutoff':cutoff,'people':sorted(cards,key=lambda x:(x['birth'] is None,x['birth'] or 0,x['name'])),'chapters':years,
            'statistics':{'people':len(cards),'living':sum(c['death'] is None for c in cards),'deceased':sum(c['death'] is not None for c in cards)} if options.get('statistics') else None,
            'options':options}
