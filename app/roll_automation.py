"""Opt-in, evidence-based scheduling. Never infer voluntary story actions.

Each generated obligation has a stable identity, source rule and prerequisites.
Missing story facts can be supplied once with a monitored situation, not by
manually recreating the same roll every year. No game or image data is changed.
"""
import re
from sqlalchemy import select, update
from .models import Record, ChronicleSave
from . import domain, record_state, occult_rules, core_rulesets

OPTIONS = {
    'pregnancy': ('Lifetime pregnancy allowance', 'One roll per eligible Sim to set their total number of pregnancies; never repeated yearly.'),
    'pregnancy_yearly': ('Annual Baby Roll', 'D20 once per historical year, using your age table and modifiers, while the lifetime pregnancy allowance remains.'),
    'hp': ('Harry Potter consequences', 'Witch-hunt and Ministry-intervention consequence tables, when their modules are enabled.'),
    'changeling': ('Newborn changeling suspicion', 'Human newborns sharing a household or recorded country with fairies; truth follows at Child stage.'),
    'dehydration': ('Mermaid dehydration follow-ups', 'Two survival checks from the selected core age table after severe dehydration.'),
    'feeding': ('Vampire feeding suspicion', 'After a confirmed unwilling-feeding situation; never guessed from a trait or vague buff.'),
    'avatar': ('Avatar module rolls', 'Known birth, childhood, bending and recurring triggers; other situations require confirmed prerequisites.'),
    'got': ('Game of Thrones module rolls', 'Known annual house, winter, missing-person, feud, vow and realm triggers; other situations require confirmed prerequisites.'),
}
PACKS={'hp':'harry_potter_decades','avatar':'avatar_decades','got':'game_of_thrones_decades'}
FEATURE='roll-automation-situation'

def config(save):return dict((save.settings or {}).get('roll_automation') or {})
def year(save,day=None):return save.start_year+((day if day is not None else save.global_day)-1)//max(1,save.days_per_year)
def number(value,default=None):return record_state.integer(value,default)
def active(row):return row and not row.deleted and not row.data.get('infinite_frozen')
def living(row,save):
    return active(row) and row.kind=='sim' and record_state.living(row,save.global_day) and str(row.data.get('game_species') or row.data.get('species') or 'human').casefold() not in {'dog','cat','horse','fox','pet','animal'}
def enabled(save,key,rule=None):
    cfg=config(save)
    return bool(domain.automation_enabled(save) and cfg.get(key) and
        (key not in PACKS or PACKS[key] in (save.settings or {}).get('selected_rule_packs',[])) and
        (rule is None or (active(rule) and rule.data.get('active',True) and (cfg.get('rules') or {}).get(rule.id,True))))
def since(save,key):return max(1,number((config(save).get('from') or {}).get(key),save.global_day))
def change(session,row,values):
    base=row.version;row.data={**row.data,**values};row.version+=1;domain.journal(session,row,'upsert',base)
def rows(session,save,kinds):
    return list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind.in_(kinds),Record.deleted.is_(False))))
def rules_for(session,save):
    return {str(r.data.get('code') or r.data.get('rule_key') or ''):r for r in rows(session,save,['addon_rule','occult_rule'])
            if active(r) and r.data.get('active',True) and number(r.data.get('start_year'),-9999)<=year(save)<=number(r.data.get('end_year'),9999)}
def normalized_table(text):
    return re.sub(r'(^|;\s*)(\d+(?:\s*[-–]\s*\d+)?)\s+(?!on\b)',r'\1\2: ',str(text or ''))

def create(session,save,key,rule,target,identity,*,day=None,die=None,table=None,bad='',extra=None):
    if not enabled(save,key,rule) or not active(target):return None
    if target.kind=='sim' and not living(target,save):return None
    due=save.global_day if day is None else int(day)
    if due<since(save,key):return None
    if not number(rule.data.get('start_year'),-9999)<=year(save,due)<=number(rule.data.get('end_year'),9999):return None
    source='extra-auto:'+key+':'+identity
    existing=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=='roll',Record.data['source'].as_string()==source))
    # Tombstones prevent rerolling after a manual dismissal or branch rewind.
    resume=existing and existing.deleted and not existing.data.get('completed') and (existing.data.get('extra_auto_paused') or existing.data.get('retired_reason')=='Origin roll reopened')
    if existing and not resume:return existing
    d=rule.data;notation=str(die or d.get('die') or '')
    if not re.fullmatch(r'd(?:[2-9]|[1-9][0-9]{1,2}|1000)',notation,re.I):return None
    payload={'source':source,'source_rule_id':rule.id,'source_id':rule.id,'source_rule_key':d.get('rule_key') or d.get('code'),
        'source_rule_kind':rule.kind,'roll_type':rule.label,'die':notation,'result_rules':normalized_table(table if table is not None else d.get('result_rules') or d.get('rule_text')),
        'bad_results':bad,'nonlethal':not bool(bad),'failure_is_lethal':bool(bad),'completed':False,'due_global_day':due,
        'extra_automation':key,'extra_rule_code':d.get('code') or d.get('rule_key'),'rule_generated':True,
        'trigger_results':d.get('trigger_results') or '', 'automation_year':year(save,due),
        'notes':'Automatically scheduled from confirmed prerequisites. Resolve in Today; this does not change the game.'}
    if target.kind=='household':payload.update(household_id=target.id,household_name=target.label,roll_scope='household')
    else:payload.update(sim_id=target.id,sim_name=target.label,household_id=target.data.get('current_household_id'))
    if rule.kind=='occult_rule':payload.update(occult_roll=True,occult_rule_id=rule.id,occult_rule_key=d.get('rule_key'),occult_type=d.get('occult'))
    payload.update(extra or {})
    if resume:
        base=existing.version;existing.deleted=False;existing.data=payload;existing.global_day=due;existing.version+=1
        domain.journal(session,existing,'upsert',base);save.revision+=1;return existing
    roll=Record(save_id=save.id,kind='roll',label=domain.record_label(f'{target.label} — {rule.label}'),global_day=due,data=payload)
    session.add(roll);session.flush();domain.journal(session,roll,'upsert',0);save.revision+=1
    return roll

def pregnancy(session,save,people,all_rows):
    from .pregnancy_planning import schedule
    schedule(session,save,people,all_rows)

def changelings(session,save,people,rules):
    rule=rules.get('fairy_changeling')
    if not rule or not enabled(save,'changeling',rule):return
    fairies=[s for s in people if 'Fairy' in occult_rules.sim_occult_types(s.data)]
    for sim in people:
        d=sim.data;birth=number(d.get('birth_global_day'),sim.global_day)
        if birth is None or birth<since(save,'changeling') or birth>save.global_day or occult_rules.sim_occult_types(d):continue
        if not any((d.get('current_household_id') and d.get('current_household_id')==f.data.get('current_household_id')) or
                   (str(d.get('country') or '').strip() and str(d['country']).casefold()==str(f.data.get('country') or '').casefold()) for f in fairies):continue
        create(session,save,'changeling',rule,sim,sim.id,day=birth)

def feeding(session,save,people,rules):
    rule=rules.get('vampire_feeding_suspicion')
    if not rule or not enabled(save,'feeding',rule):return
    by_id={s.id:s for s in people}
    # Only a reviewed, explicitly typed report can count. Hungry/feeding buffs,
    # romance bits and a victim's "drained" moodlet do not prove who fed or how.
    candidates=session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='game_candidate',Record.deleted.is_(False),
        Record.data['status'].as_string()=='accepted',Record.data['payload']['type'].as_string()=='vampire_unwilling_feeding'))
    for event in candidates:
        d=event.data;p=d.get('payload') or {};actor=str(p.get('actor_sim_id') or '')
        day=number(p.get('detected_tracker_global_day'),event.global_day)
        sim=by_id.get(actor)
        if not active(event) or not sim or actor!=str(d.get('sim_id') or '') or p.get('confirmed') is not True:continue
        if day is None or not since(save,'feeding')<=day<=save.global_day or 'Vampire' not in occult_rules.sim_occult_types(sim.data):continue
        create(session,save,'feeding',rule,sim,'report:'+event.id,extra={'source_detection_id':event.id,'rule_context':'Accepted report explicitly identifies this vampire as the actor of unwilling feeding.'})

def avatar(session,save,people,rules):
    if not enabled(save,'avatar'):return
    by_id={s.id:s for s in people};days=max(1,save.days_per_year)
    def add(code,sim,identity=None,**kw):
        rule=rules.get(code)
        if rule:return create(session,save,'avatar',rule,sim,identity or code+':'+sim.id,**kw)
    for sim in people:
        d=sim.data;birth=number(d.get('birth_global_day'),sim.global_day);nation=str(d.get('avatar_birth_nation') or '').casefold()
        if birth is None:continue
        parents=[by_id.get(d.get(k)) for k in ('mother_id','father_id')]
        # Dead ancestors remain evidence, even though only living children roll.
        for i,parent in enumerate(parents):
            if not parent and d.get(('mother_id','father_id')[i]):
                candidate=session.get(Record,d[('mother_id','father_id')[i]])
                if candidate and candidate.save_id==save.id and candidate.kind=='sim':parents[i]=candidate
        benders=[p for p in parents if p and str(p.data.get('avatar_bender_status','')).casefold()=='bender']
        traditional_air=all(p and p.data.get('avatar_traditional_air_nomad') for p in parents)
        if since(save,'avatar')<=birth<=save.global_day:
            if not d.get('avatar_bender_status') and not traditional_air and (benders or d.get('avatar_elemental_ancestry')):
                elements={p.data.get('avatar_bending_element') for p in benders}-{None,''}
                high=3 if len(benders)==2 and len(elements)==1 else 2 if benders else 1
                add('ATLA-02',sim,day=birth,table=f'1-{high}: Bender; {high+1}-4: Non-Bender',extra={'extra_success_high':high,'extra_parent_elements':[p.data.get('avatar_bending_element') if p else '' for p in parents]})
            if not d.get('avatar_spiritually_gifted'):
                high=3 if 'air' in nation else 1
                add('ATLA-30',sim,day=birth,table=f'1-{high}: Spiritually Gifted; {high+1}-12: Ordinary spiritual sensitivity')
            if any(p and p.data.get('avatar_human_form_spirit') for p in parents):add('ATLA-33',sim,day=birth)
        child_day=birth+domain.lifecycle_age_days(save,20)
        if since(save,'avatar')<=child_day<=save.global_day:
            add('ATLA-34',sim,day=child_day,table='1: Lifelong animal companion; 2-10: No companion')
            if d.get('avatar_traditional_air_nomad'):
                add('ATLA-35',sim,'ATLA-35:bison:'+sim.id,day=child_day,die='d4',table='1-3: Sky Bison bond; 4: No bond')
        if str(d.get('avatar_bender_status','')).casefold()=='bender' and d.get('avatar_bending_manifested'):
            if not d.get('avatar_natural_strength'):add('ATLA-05',sim)
            if str(d.get('avatar_bending_element','')).casefold()=='water':
                high=3 if d.get('avatar_healer_ancestry') else 2
                add('ATLA-14',sim,table=f'1-{high}: Natural healing aptitude; {high+1}-6: No natural aptitude')
            rank=str(d.get('avatar_mastery_rank') or '').casefold();element=str(d.get('avatar_bending_element') or '').casefold()
            known=str(d.get('avatar_advanced_techniques') or '').casefold()
            if rank=='master' and element=='water' and 'bloodbend' not in known:
                high=2 if d.get('avatar_bloodbender_ancestry') else 1
                add('ATLA-17',sim,table=f'1-{high}: Bloodbending aptitude; {high+1}-20: No aptitude')
            if rank in {'advanced','master'} and element=='earth' and d.get('avatar_technique_discovered') and d.get('avatar_teacher_available') and 'metalbend' not in known:
                high=3 if d.get('avatar_metalbender_ancestry') else 2
                add('ATLA-19',sim,table=f'1-{high}: Metalbending aptitude; {high+1}-10: No aptitude')
        status=str(d.get('avatar_prisoner_status') or '').casefold()
        if status in {'captured','missing'}:
            table='1: Dies; 2: Escapes; 3: Released; 4-6: Remains captured' if status=='captured' else '1: Confirmed dead; 2: Returns; 3-6: Remains missing'
            add('ATLA-46',sim,f'ATLA-46:{sim.id}:{year(save)}',table=table,bad='1',extra={'extra_annual':True})

def got(session,save,people,households,rules):
    if not enabled(save,'got'):return
    cfg=save.settings or {};yr=year(save);winter=str(cfg.get('got_current_season') or '').casefold()=='winter'
    def add(code,target,**kw):
        rule=rules.get(code)
        if rule:return create(session,save,'got',rule,target,f'{code}:{target.id}:{yr}',extra={'extra_annual':True,**kw.pop('extra',{})},**kw)
    for sim in people:
        d=sim.data
        if str(d.get('got_missing_status') or '').casefold() in {'missing','yes','true'}:add('GOT-33',sim,bad='1')
        if str(d.get('got_vow_order') or '').casefold().replace('’',"'") in {"night's watch",'nights watch'}:
            add('GOT-45',sim,die='d20',table='1: Dies on Night\'s Watch service; 2-20: Survives',bad='1')
        birth=number(d.get('birth_global_day'),sim.global_day)
        if d.get('got_first_men_ancestry') and birth is not None and since(save,'got')<=birth<=save.global_day:
            rule=rules.get('GOT-61')
            if rule:create(session,save,'got',rule,sim,'GOT-61:'+sim.id,day=birth,die='d20',table='1: Inherited gift; 2-20: No inherited gift')
    inhabited={s.data.get('current_household_id') for s in people}
    # One shared five-year marriage check, not one for each spouse.
    people_by_id={s.id:s for s in people}
    for rel in rows(session,save,['relationship']):
        d=rel.data;partners=[people_by_id.get(d.get(k)) for k in ('partner1_id','partner2_id')]
        start=number(d.get('marriage_global_day'),number(d.get('start_global_day')))
        if not all(partners) or start is None or not (d.get('legally_married') or str(d.get('type','')).casefold()=='marriage'):continue
        if str(d.get('status','active')).casefold() in {'widowed','divorced','annulled','separated','ended','abandoned','closed'}:continue
        if save.global_day-start<5*max(1,save.days_per_year) or not any(p.data.get('got_house') for p in partners):continue
        ids={p.id for p in partners}
        if any({s.data.get('mother_id'),s.data.get('father_id')}==ids for s in people):continue
        rule=rules.get('GOT-17')
        if rule:create(session,save,'got',rule,partners[0],'GOT-17:'+rel.id,extra={'relationship_id':rel.id,'rule_context':'Five historical years married without a recorded living child of this couple.'})
    for house in households:
        if house.id not in inhabited or not active(house):continue
        d=house.data
        if not d.get('got_house_name'):continue # A generic household is not automatically a Westerosi House.
        fixed=bool(d.get('roll_context_fixed_major_event'))
        if not fixed:add('GOT-T01',house)
        if d.get('roll_context_major_court'):add('GOT-24',house)
        if d.get('roll_context_active_feud'):add('GOT-36',house)
        if winter and d.get('roll_context_winter_affected'):
            poor=str(d.get('got_rank') or '').casefold() in {'poor','smallfolk','peasant','peasants'}
            # These are alternatives, not two winter death rolls for one household.
            rule=rules.get('GOT-T02')
            if rule and enabled(save,'got',rule):
                table='1-2: Household death; 3: Illness; 4: Home damaged; 5: Crops or livestock lost; 6: Food stolen; 7: Relief; 8-10: Endures' if poor else '1: Household death; 2: Illness; 3: Stores lost; 4: Livestock lost; 5: Refugees; 6: No travel; 7-10: Endures'
                add('GOT-T02',house,table=table,extra={'household_consequence_needs_review':True})
            elif poor:add('GOT-65',house,extra={'household_consequence_needs_review':True})
        if d.get('roll_context_war_affected') and str(d.get('got_rank','')).casefold() in {'poor','smallfolk','peasant','peasants'}:add('GOT-53',house,extra={'household_consequence_needs_review':True})
    # Realm checks need a real House to display under, but have one save-wide key.
    realm=next((h for h in households if h.id in inhabited and h.data.get('got_house_name')),None)
    if realm and not cfg.get('roll_context_fixed_major_event'):
        rule=rules.get('GOT-66')
        if rule:create(session,save,'got',rule,realm,f'GOT-66:realm:{yr}',extra={'roll_scope':'event','extra_annual':True,'eligible_household_ids':sorted(inhabited-{None})})
    duration=number(cfg.get('got_season_length'),0)
    if realm and duration>=2 and save.global_day%max(1,save.days_per_year)==0 and cfg.get('game_of_thrones_timeline_mode')!='canon':
        rule=rules.get('GOT-64');threshold=4 if duration>=10 else 7 if duration>=5 else 9
        if rule:create(session,save,'got',rule,realm,f'GOT-64:realm:{yr}',table=f'1-{threshold-1}: Season continues; {threshold}-12: Season changes',extra={'roll_scope':'event','extra_annual':True})

def followups(session,save,origin,rules=None):
    d=origin.data
    if not active(origin) or not d.get('completed') or d.get('catch_up_resolution'):return
    rules=rules or rules_for(session,save);actual=number(d.get('actual'))
    if actual is None:return
    sim=session.get(Record,d.get('sim_id')) if d.get('sim_id') else None
    if sim and (sim.save_id!=save.id or not living(sim,save)):sim=None
    code=str(d.get('extra_rule_code') or d.get('hp_rule_code') or d.get('occult_rule_key') or '')
    if not code and d.get('source_rule_id'):
        source=session.get(Record,d['source_rule_id'])
        if source and source.save_id==save.id:code=str(source.data.get('code') or source.data.get('rule_key') or '')
    key='hp' if code.startswith('HP') or code=='spellcaster_witch_trial' else d.get('extra_automation')
    def add(child,key,target,**kw):
        rule=rules.get(child)
        if number(d.get('completed_global_day'),origin.global_day or 1)<since(save,key):return None
        if rule and target:return create(session,save,key,rule,target,origin.id+':'+child+':'+str(kw.pop('suffix','0')),extra={'origin_roll_id':origin.id,'automatic_followup':True,**kw.pop('extra',{})},**kw)
    if code=='HP-19' and actual in {1,2} and year(save,origin.global_day)>=1692:
        house=session.get(Record,d.get('hp_household_id')) if d.get('hp_household_id') else None
        add('HP-T03','hp',house if house and house.save_id==save.id else sim)
    if code in {'spellcaster_witch_trial','HP-18'} and domain.failed(actual,str(d.get('trigger_results') or d.get('bad_results') or '')) and 1300<=year(save,origin.global_day)<=1691:
        house_id=d.get('occult_household_id') or (sim.data.get('current_household_id') if sim else None)
        house=session.get(Record,house_id) if house_id else None
        add('HP-T02','hp',house if house and house.save_id==save.id else sim)
    if code=='mermaid_dehydration' and sim and domain.failed(actual,str(d.get('trigger_results') or '')) and enabled(save,'dehydration') and number(d.get('completed_global_day'),origin.global_day or 1)>=since(save,'dehydration'):
        age=save.global_day-number(sim.data.get('birth_global_day'),sim.global_day or 1)
        tables=[r for r in rows(session,save,['roll_rule']) if r.data.get('active',True) and core_rulesets.applies_to_selected_core(save,r)
            and not r.data.get('death_age_rng') and domain.lifecycle_rule_age(save,r) is not None and domain.lifecycle_rule_age(save,r)<=age
            and number(r.data.get('start_year'),-9999)<=year(save)<=number(r.data.get('end_year'),9999)]
        table=max(tables,key=lambda r:domain.lifecycle_rule_age(save,r),default=None)
        if table:
            # Occults retain the original selected-core table; no invented table.
            die,bad=table.data.get('die'),str(table.data.get('bad_results') or '')
            for i in (1,2):
                create(session,save,'dehydration',table,sim,f'{origin.id}:{i}',die=die,bad=bad,
                       table=f'{bad}: Dies from dehydration; other results: Survives dehydration',
                       extra={'origin_roll_id':origin.id,'automatic_followup':True,'dehydration_check':i,
                              'roll_type':'Dehydration survival','extra_rule_code':'dehydration_check','death_cause':'Dehydration',
                              'core_ruleset_id':table.data.get('core_ruleset_id'),'source_notes':'Two normal age-table hazard checks required by severe mermaid dehydration.'})
    if not sim:return
    if code=='ATLA-02' and actual<=number(d.get('extra_success_high'),0):
        elements=d.get('extra_parent_elements') or []
        if len(elements)==2 and all(elements) and elements[0]!=elements[1]:add('ATLA-03','avatar',sim,table=f'1: {elements[0]}; 2: {elements[1]}')
    chains={
        'ATLA-10': [('ATLA-11',actual>number(d.get('extra_success_high'),3 if sim.data.get('avatar_state_mastered') else 2),{})],
        'GOT-30': [('GOT-31',actual==4,{'bad':''})],
        'GOT-61': [('GOT-61',actual==1 and d.get('die')=='d20',{'die':'d4','table':'1: Greensight; 2: Skinchanging; 3: Both; 4: Prophetic dreams','suffix':'gift'})],
        'GOT-28': [('GOT-28',actual==1 and d.get('die')=='d20',{'die':'d6','table':'1: Death; 2: Disability; 3-4: Serious injury; 5-6: Recovery','bad':'1','suffix':'injury'})],
        'ATLA-31': [('ATLA-31',actual in {5,6} and d.get('die')=='d6',{'die':'d10','table':'1: Possessed; 2-10: Not possessed','suffix':'possession'})],
        'ATLA-24': [('ATLA-24',actual==1 and d.get('die')=='d20' and not d.get('extra_aptitude_confirmation') and not sim.data.get('avatar_combustion_ancestry'),
                    {'die':'d20','table':'1: Combustionbending aptitude confirmed; 2-20: No aptitude','suffix':'aptitude','extra':{'extra_aptitude_confirmation':True}})],
    }
    for child,trigger,kw in chains.get(code,[]):
        if trigger:add(child,key,sim,**kw)

def situations(session,save,rules,people):
    by_id={s.id:s for s in people}
    for context in rows(session,save,['task']):
        d=context.data
        if d.get('feature')!=FEATURE or not d.get('enabled',True) or not active(context):continue
        target=session.get(Record,d.get('target_id'));rule=session.get(Record,d.get('rule_id'))
        if not target or not rule or target.save_id!=save.id or rule.save_id!=save.id:continue
        if target.kind=='sim' and target.id not in by_id:continue
        start=number(d.get('start_day'),save.global_day)
        if start>save.global_day:continue
        if number(d.get('end_day')) is not None and d['end_day']<save.global_day:continue
        annual=d.get('cadence')=='annual';identity=context.id+(':'+str(year(save)) if annual else '')
        create(session,save,d.get('option'),rule,target,identity,die=d.get('die'),table=d.get('result_rules'),bad=d.get('bad_results',''),
            extra={'situation_id':context.id,'extra_annual':annual,'rule_context':d.get('evidence',''),'source_notes':d.get('evidence','')})

def schedule(session,save):
    cfg=config(save)
    if not domain.automation_enabled(save) or not cfg:return 0
    session.flush()
    session.execute(update(ChronicleSave).where(ChronicleSave.id==save.id).values(revision=ChronicleSave.revision))
    before=save.revision
    retire_paused(session,save)
    if not any(cfg.get(k) for k in OPTIONS):return save.revision-before
    all_rows=rows(session,save,['sim','household','relationship']);people=[s for s in all_rows if living(s,save)]
    rules=rules_for(session,save)
    pregnancy(session,save,people,all_rows);changelings(session,save,people,rules);feeding(session,save,people,rules)
    avatar(session,save,people,rules);got(session,save,people,[h for h in all_rows if h.kind=='household'],rules)
    situations(session,save,rules,people)
    # Only outcomes at/after opt-in, not every historical failure in an old save.
    origins=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='roll',Record.deleted.is_(False),Record.data['completed'].as_boolean().is_(True),
        Record.data['completed_global_day'].as_integer()>=min(since(save,k) for k in OPTIONS if cfg.get(k)))))
    for origin in origins:followups(session,save,origin,rules)
    return save.revision-before

def retire_paused(session,save):
    changed=0;cfg=config(save)
    for r in rows(session,save,['roll']):
        d=r.data;key=d.get('extra_automation')
        if not key or d.get('completed') or d.get('infinite_frozen'):continue
        source=session.get(Record,d.get('source_rule_id')) if d.get('source_rule_id') else None
        paused=not cfg.get(key) or (source and (not active(source) or not source.data.get('active',True) or not (cfg.get('rules') or {}).get(source.id,True)))
        if key in PACKS and PACKS[key] not in (save.settings or {}).get('selected_rule_packs',[]):paused=True
        if paused:
            base=r.version;r.deleted=True;r.data={**d,'extra_auto_paused':True,'retired_reason':'Optional roll automation was paused; completed history is unchanged'};r.version+=1
            domain.journal(session,r,'delete',base);changed+=1
    save.revision+=changed
    return changed
