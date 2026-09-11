"""Shared presentation rules for the play-first interface."""
from datetime import datetime,timezone
from types import SimpleNamespace
from sqlalchemy import and_,or_,func,select
from starlette.datastructures import QueryParams
from .models import Record,UiPreference,ClockReceipt
from sqlalchemy.orm import object_session

QUERY_KEYS={'q','record_status','living','sort','density','thumbnail','task','roll_kind','due','window','household','review_quality','status','scope','location','rolls','interest','year','start_year','end_year','kind','focus','depth','direction','region','culture','sex','span','mode'}
ACTIVE_PAGES={'sims','relationships','pregnancies','illnesses','university','planner','households','rolls','today','automation','occult-rules'}

def people_picker(session,save):
    """Menus need identity fields, not every Sim's large telemetry payload."""
    keys=('birth_global_day','birth_year','death_global_day','death_confirmed','game_was_dead','infinite_frozen','infinite_frozen_global_day','sim_number','legacy_id','sex','current_household_id','game_sim_id','first_name','last_name','generation','species_occult','mother_id','father_id')
    columns=[Record.data[key].label(key) for key in keys]
    rows=session.execute(select(Record.id,Record.label,Record.global_day,Record.updated_at,*columns).where(Record.save_id==save.id,Record.kind=='sim',Record.deleted.is_(False)))
    return [SimpleNamespace(id=row.id,label=row.label,global_day=row.global_day,updated_at=row.updated_at,kind='sim',deleted=False,data={key:row._mapping[key] for key in keys if row._mapping[key] is not None}) for row in rows]

def preferences(session,user):
    if not user: return {}
    row=session.get(UiPreference,user.id)
    return dict(row.values or {}) if row else {}

def query(request,prefs,save,page):
    scope=(save.id if save else '')+':'+(page or 'home')
    remembered={} if request.query_params.get('reset')=='1' else ((prefs or {}).get('pages') or {}).get(scope,{})
    merged={**{k:v for k,v in remembered.items() if k in QUERY_KEYS},**{k:request.query_params.getlist(k) for k in request.query_params}}
    return QueryParams([(k,str(item)) for k,v in merged.items() for item in (v if isinstance(v,list) else [v])])

def integer(value,default=None):
    try:return int(value)
    except (TypeError,ValueError):return default

def living_sql(save):
    birth=func.coalesce(Record.data['birth_global_day'].as_integer(),Record.global_day,1)
    death=Record.data['death_global_day'].as_integer()
    return and_(Record.kind=='sim',Record.deleted.is_(False),birth<=save.global_day,
        or_(death.is_(None),death>save.global_day),
        Record.data['game_was_dead'].as_boolean().is_not(True),Record.data['death_confirmed'].as_boolean().is_not(True))

def related_living_sql(save,field='sim_id',mode='living'):
    ids=select(Record.id).where(Record.save_id==save.id,Record.kind=='sim',Record.deleted.is_(False),
                               living_sql(save) if mode=='living' else or_(Record.data['death_global_day'].as_integer()<=save.global_day,Record.data['game_was_dead'].as_boolean().is_(True),Record.data['death_confirmed'].as_boolean().is_(True))).correlate(None)
    owner=Record.data[field].as_string()
    return or_(owner.is_(None),owner=='',owner.in_(ids)) if mode=='living' else owner.in_(ids)

def date_certainty(data,event='birth'):
    source=str(data.get(event+'_time_source') or data.get(event+'_date_source') or '').casefold()
    precision=str(data.get(event+'_date_precision') or '').casefold()
    if data.get(event+'_time_randomized') or 'random' in precision or ('random' in source and 'replaced randomized fallback' not in source):
        return {'key':'random','label':'Randomized','detail':'Generated within the recorded day, not an observed game time.'}
    if data.get(event+'_year_only') or precision in {'challenge-day-only','year-only','date-range'} or 'estimat' in source or 'approx' in precision or 'estimat' in precision:
        return {'key':'estimated','label':'Estimated','detail':'Inferred from an age, year or date range; not an exact observation.'}
    if 'manual' in source or 'player' in source or data.get(event+'_manually_entered'):
        return {'key':'manual','label':'Player-entered','detail':'Entered or corrected by the player; not independently verified.'}
    has_time=bool(data.get(event+'_time')) or (data.get(event+'_game_hour') is not None and data.get(event+'_game_minute') is not None)
    if has_time and ('clock sync' in source or 'game observation' in source or 'exact game' in source or 'saved game' in source):
        return {'key':'exact','label':'Game-confirmed','detail':'A game time was retained. Historical dates are mapped through your challenge calendar.'}
    return {'key':'unknown','label':'Source unverified','detail':'The original date source was not retained. A precise-looking date is not proof of an observation.'}

def clock_status(save,link,prefs=None,now=None):
    now=now or datetime.now(timezone.utc);settings=save.settings or {};saved=bool(settings.get('sims3_saved_clock_enabled'))
    seen=link.last_seen_at if link else None
    if seen and seen.tzinfo is None:seen=seen.replace(tzinfo=timezone.utc)
    age=max(0,(now-seen).total_seconds()) if seen else None
    point=[link.last_game_day,link.last_game_hour,link.last_game_minute] if link else []
    reported=settings.get('clock_ui_observation') or {}
    manual=((prefs or {}).get('paused_clocks') or {}).get(save.id)
    paused=reported.get('game_paused') is True or (reported.get('game_paused') is None and manual is not None and manual==point)
    enabled=bool(link and link.enabled) or saved
    state='waiting';label='Waiting for report';detail='No successful report has arrived for this save.'
    if not enabled:state='disabled';label='Not connected';detail='Open Game Connection to connect this save.'
    elif saved:state='saved';label='Saved-game clock';detail='Updates after a completed Sims 3 save, not continuously.'
    elif paused:state='paused';label='Game paused';detail='Pause reported by the game.' if reported.get('game_paused') is True else 'Marked paused by you; resumes when the reported clock changes.'
    elif seen and age<=150:state='connected';label='Receiving reports';detail='The receiver is reachable. Pause state has not been reported.'
    elif seen:detail='No recent report. The game may be paused, closed, or the relay may be waiting; silence alone does not prove a broken connection.'
    summary=settings.get('clock_receipt_summary') or {}
    receipt_row=object_session(link).get(ClockReceipt,save.id) if link is not None and object_session(link) else None
    received=receipt_row.received_at if receipt_row else seen
    if received and received.tzinfo is None:received=received.replace(tzinfo=timezone.utc)
    receipt_age=max(0,(now-received).total_seconds()) if received else None
    if receipt_row:summary=receipt_row.summary or {}
    if summary.get('duplicate') and enabled and not paused:
        state='waiting';label='Repeated report received';detail='The relay is reachable but sent an already processed report. Last successful new game data is shown below.'
    recovery=settings.get('clock_recovery') or {}
    from .crash_recovery import held
    recovering=held(save)
    if recovering:
        state='recovery';label='Game reloaded — changes held'
        detail={'review':'The game clock moved backward. Open Crash Recovery to choose what to keep or undo.',
                'catching_up':'Keeping history; waiting for the game to catch up before importing changes.',
                'awaiting_full':'Recovery choice saved; waiting for a complete game report.'}[recovery['status']]
    labels={'new_baby':'birth detection','pregnancy_started':'pregnancy detection','relationship':'relationship update','relationship_changed':'relationship update','marriage':'marriage detection'}
    changes=[f"{n} {labels.get(k,k.replace('_',' '))}{'s' if n!=1 else ''}" for k,n in (summary.get('candidate_types') or {}).items() if n]
    for key,label_text in [('illnesses_created','illness records added'),('illnesses_ended','illnesses ended'),('households_created','households added'),('households_updated','households updated'),('parent_links_updated','parent links updated'),('population_updates','population updates'),('rolls_created','rolls scheduled'),('profile_updates','profiles with new game history'),('portraits_updated','portraits updated'),('household_members_linked','household assignments updated'),('generations_updated','generations updated')]:
        if summary.get(key):changes.append(f"{summary[key]} {label_text}")
    if not changes and summary.get('new_candidates'):changes.append(f"{summary['new_candidates']} detections awaiting review")
    change_text=' · '.join(changes) if changes else ('Automation paused · no automatic changes' if summary.get('automation_paused') else 'No new changes' if summary else 'Change summary unavailable for this older report')
    receipt=(f"Received {int(receipt_age)} seconds ago" if receipt_age is not None and receipt_age<120 else f"Received {int(receipt_age//60)} minutes ago" if receipt_age is not None else 'No report received')
    receipt+=' · '+('game time changed' if summary.get('game_time_changed') else 'game time unchanged' if summary else 'game time comparison unavailable')+' · '+change_text
    if summary.get('duplicate'):receipt+=' · duplicate report ignored'
    return {'recovery_required':recovering,'receipt_summary':receipt,'state':state,'label':label,'detail':detail,'save_id':save.id,'save_name':save.name,'global_day':save.global_day,
            'game_day':link.last_game_day if link else None,'hour':link.last_game_hour if link else None,'minute':link.last_game_minute if link else None,
            'last_success':seen.isoformat() if seen else None,'age_seconds':age,'can_mark_paused':bool(link and link.enabled and seen and not saved and not recovering),'paused':paused}

def confirmed_sql():
    p=Record.data['payload']
    return and_(Record.data['action'].as_string().notin_(['unknown_illness','save_portrait']),or_(
        p['confidence'].as_string().in_(['confirmed','high','explicit']),p['evidence_kind'].as_string()=='explicit',
        p['is_pregnant'].as_boolean().is_(True),p['is_dead'].as_boolean().is_(True)))

def review_quality(item):
    p=item.data.get('payload') or {}
    certain=item.data.get('action') not in {'unknown_illness','save_portrait'} and (p.get('confidence') in ('confirmed','high','explicit') or p.get('evidence_kind')=='explicit' or p.get('is_pregnant') is True or p.get('is_dead') is True)
    return 'confirmed' if certain else 'suggestion'

REVIEW_FIELDS={'first_name':'First name','last_name':'Surname','sex':'Sex','birth_global_day':'Birth GD','birthplace':'Birthplace','legitimacy':'Legitimacy',
 'death_global_day':'Death GD','cause_of_death':'Cause of death','death_place':'Death place','household_id':'Household',
 'mother_id':'Mother','father_id':'Father','partner1_id':'First partner','partner2_id':'Second partner','relationship_type':'Relationship type',
 'type':'Type','career':'Career','education':'Education','country':'Country','world_name':'World','illness_name':'Illness',
 'name':'Name','severity':'Severity','status':'Status','onset_global_day':'Onset GD','end_global_day':'End GD',
 'due_global_day':'Due GD','babies_expected':'Expected babies','babies_delivered':'Babies delivered','pregnancy_status':'Pregnancy status',
 'traits':'Traits','skills':'Skills','milestones':'Milestones','occult_types':'Occult types'}

def review_changes(item,by_id):
    p=item.data.get('payload') or {};actor=by_id.get(item.data.get('sim_id'))
    target=next((by_id.get(p.get(key)) for key in ('illness_id','pregnancy_id','relationship_id') if by_id.get(p.get(key))),actor)
    old=target.data if target else {};changes=[]
    aliases={'career':'game_career','education':'game_education','traits':'game_traits','skills':'game_skills','milestones':'game_milestones','household_id':'current_household_id','occult_types':'game_occult_types'}
    def display(value):
        if value is None or value=='':return 'Not recorded'
        if isinstance(value,str) and value in by_id:return by_id[value].label
        if isinstance(value,(list,tuple)):return ', '.join(display(v) for v in value[:30])
        if isinstance(value,dict):return '; '.join(str(v) for k,v in value.items() if k in {'name','label','level','title'}) or 'Structured game detail'
        return str(value)[:1200]
    for key,label in REVIEW_FIELDS.items():
        if key not in p:continue
        before=old.get(key,old.get(aliases.get(key,'')))
        changes.append({'key':key,'label':label,'current':display(before),'proposed':display(p[key]),'same':before==p[key]})
    if not changes:changes=[{'key':'summary','label':'Detection','current':target.label if target else 'No matching record','proposed':item.label,'same':False}]
    return changes

NAVIGATION_GROUPS=(
 {'id':'play','label':'Play','description':'Decide, play and review','icon':'▶',
  'pages':('today','play-next','automation','clock','crash-recovery','rolls','save-a-sims','planner','family-projects','seasonal-routines','story-threads','events','challenge','drama-randomizer','drama','avatar','harry-potter','game-of-thrones')},
 {'id':'people','label':'People','description':'Sims, families and daily life','icon':'♟',
  'pages':('sims','households','historical-addresses','titles-estates','relationships','pregnancies','illnesses','university','family-tree','life-records','world','names','naming-customs')},
 {'id':'history','label':'History','description':'Remember and compare','icon':'✒',
  'pages':('timeline','storyline','portrait-studio','writers-room','family-chronicle','notes','statistics','legacy-lab','infinite-decades','branch-comparison','historical-life')},
 {'id':'settings','label':'Settings','description':'Rules, preferences and maintenance','icon':'⚙',
  'pages':('rules','roll-tables','occult-rules','historical-guidance','historical-check','catch-up','plants','guides','tutorial','saves','sync','appearance','ai-settings','account','health','dice-audit','support')},
)
OPTIONAL_PAGES={'avatar':'avatar_decades','harry-potter':'harry_potter_decades','game-of-thrones':'game_of_thrones_decades'}

def visible_navigation(save):
    enabled=set((save.settings or {}).get('selected_rule_packs') or []) if save else set()
    return [{**group,'pages':tuple(page for page in group['pages'] if page not in OPTIONAL_PAGES or OPTIONAL_PAGES[page] in enabled)} for group in NAVIGATION_GROUPS]
