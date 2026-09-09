"""Transactional previews: run the real rules, roll back, then confirm that exact plan.

Only changed records are retained. No clone of a whole save, preview-only rule
engine, rerolled consequences, or live mutation before confirmation.
"""
import copy
import hashlib
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from sqlalchemy import event, select, update, delete
from fastapi import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from .models import Record, ChronicleSave, ActionPreview
from . import domain, dice, core_rulesets, advanced, avatar_rules, harry_potter_rules, game_of_thrones_rules

RECORD_FIELDS=('id','save_id','kind','label','global_day','data','version','deleted','updated_by_device')
SAVE_FIELDS=('name','global_day','start_year','days_per_year','pregnancy_days','settings','revision')

def snapshot(obj, fields): return copy.deepcopy({k:getattr(obj,k) for k in fields})

def fingerprint(save):
    data=snapshot(save,SAVE_FIELDS)
    # Poll receipt metadata does not change any rule or consequence.
    for key in ('clock_ui_observation','clock_receipt_summary'):data['settings'].pop(key,None)
    return hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()

def simulate(session,save,operation):
    session.flush();before_save=snapshot(save,SAVE_FIELDS);original={};created=set()
    def before_flush(s,*args):
        for row in list(s.dirty):
            if isinstance(row,Record) and row.id not in original and row.id not in created:
                old=s.connection().execute(select(*[getattr(Record,k) for k in RECORD_FIELDS]).where(Record.id==row.id)).mappings().first()
                if old:original[row.id]=copy.deepcopy(dict(old))
    def after_flush(s,*args):
        for row in s.new:
            if isinstance(row,Record):created.add(row.id)
    transaction=session.begin_nested()
    event.listen(session,'before_flush',before_flush);event.listen(session,'after_flush',after_flush)
    try:
        result=operation() or {};session.flush()
        ids=set(original)|created
        after=[dict(r) for r in session.execute(select(*[getattr(Record,k) for k in RECORD_FIELDS]).where(Record.id.in_(ids))).mappings()] if ids else []
        plan={'before_save':before_save,'after_save':snapshot(save,SAVE_FIELDS),'records':[], 'result':copy.deepcopy(result)}
        for row in after:
            old=original.get(row['id'])
            if old!=row:plan['records'].append({'before':old,'after':copy.deepcopy(row)})
        return plan
    finally:
        event.remove(session,'before_flush',before_flush);event.remove(session,'after_flush',after_flush)
        transaction.rollback()


def apply_settings(session,save,form):
    def integer(value):
        try:return int(value)
        except (TypeError,ValueError):return None
    prior=max(1,int(save.days_per_year))
    save.name=str(form.get('name') or save.name).strip()
    save.start_year=max(-9999,min(9999,integer(form.get('start_year')) or save.start_year))
    save.days_per_year=max(1,min(365,integer(form.get('days_per_year')) or save.days_per_year))
    save.pregnancy_days=max(1,min(100,integer(form.get('pregnancy_days')) or save.pregnancy_days))
    values=dict(save.settings or {})
    for key in ('challenge_location','default_species','succession_system','succession_root_id','sim_menu_order'):
        if key in form:values[key]=str(form.get(key) or '').strip()
    for key in ('roll_tracking_start_day','try_for_baby_daily_limit','delivery_day_limit','elder_min_age_days','elder_max_age_days','marriage_min_age_days','inheritance_rule_cutoff_year','free_save_a_sims','full_moon_anchor_global_day','full_moon_interval_days','kinship_detection_generations'):
        if key in form and integer(form.get(key)) is not None:values[key]=integer(form[key])
    scope=str(form.get('settings_scope') or ('succession' if str(form.get('return_to') or '').startswith('/p/challenge') else 'rules'))
    if scope=='succession':values['succession_require_legitimate']='succession_require_legitimate' in form
    elif scope=='rules' and 'sim_menu_order' not in form:
        for key in ('maternal_rolls_enabled','automatic_death_causes','automatic_birth_circumstances'):values[key]=key in form
    save.settings=values
    changes=domain.rescale_age_timing(session,save,prior,save.days_per_year)
    save.revision+=1+sum(changes.values());domain.schedule_rolls(session,save)
    return {'calendar':changes}


def apply_packs(session,save,form):
    raw=list(form.getlist('rule_pack'));allowed={p['id'] for p in advanced.RULE_PACKS};selected=[v for v in raw if v in allowed]
    legacy=core_rulesets.MORBID if 'morbidgamer' in raw else core_rulesets.SEVERALUDO if 'severaludo' in raw else ''
    core=str(form.get('core_ruleset') or legacy or core_rulesets.SEVERALUDO)
    if core not in core_rulesets.CORE_IDS:core=core_rulesets.SEVERALUDO
    save.settings={**(save.settings or {}),'selected_rule_packs':raw if legacy else selected,'core_ruleset_id':core,'rule_pack_selection_version':3}
    save.revision+=1+core_rulesets.sync_rules(session,save)+avatar_rules.sync_pack(session,save,selected)+harry_potter_rules.sync_pack(session,save,selected)+harry_potter_rules.sync_canon_events(session,save,selected)+game_of_thrones_rules.sync_pack(session,save,selected)
    save.revision+=domain.retire_inactive_core_rolls(session,save);domain.schedule_rolls(session,save)
    return {}


def describe(plan,kind):
    counts={'moved':0,'added':0,'retired':0};effects=[];completed=0
    if kind in {'settings','packs'}:
        for field,label in [('name','Save name'),('start_year','Starting year'),('days_per_year','Days per year'),('pregnancy_days','Pregnancy duration')]:
            old,new=plan['before_save'][field],plan['after_save'][field]
            if old!=new:effects.append(f'{label}: {old} → {new}')
        for field,label in [('core_ruleset_id','Core ruleset'),('selected_rule_packs','Optional rulepacks')]:
            old=plan['before_save']['settings'].get(field);new=plan['after_save']['settings'].get(field)
            if old!=new:effects.append(f'{label}: {old or "None"} → {new or "None"}')
    for change in plan['records']:
        old,row=change['before'],change['after'];d=row['data'];before=(old or {}).get('data') or {}
        if row['kind']=='roll':
            if old and before.get('completed'):
                completed+=1
                if kind in {'settings','packs'}:raise ValueError('This change would alter completed history. It has been blocked for review.')
            if not old and not row['deleted']:
                counts['added']+=1;effects.append(f"Schedules {row['label']} on GD {row['global_day']}" )
            elif old and not old['deleted'] and row['deleted']:
                counts['retired']+=1;effects.append(f"Retires pending {row['label']} (GD {old['global_day']})")
            elif old and not d.get('completed') and old['global_day']!=row['global_day']:
                counts['moved']+=1;effects.append(f"Moves {row['label']}: GD {old['global_day']} → GD {row['global_day']}")
        elif row['kind']=='sim':
            if d.get('death_global_day')!=before.get('death_global_day'):
                text=f"Schedules {row['label']}'s death on GD {d.get('death_global_day')}"
                if before.get('death_global_day') is not None:text+=f"; replaces existing GD {before['death_global_day']} death"
                effects.append(text+'; '+str(d.get('cause_of_death') or 'cause not specified'))
                if d.get('death_global_day') is not None and d['death_global_day']>plan['after_save']['global_day']:
                    effects.append('Active illnesses remain open now and close when the scheduled death occurs.')
            for key,label in [('hp_hogwarts_house','Hogwarts house'),('species_occult','Occult status'),('occult_alignment','Occult alignment'),('pregnancy_allowance_count','Pregnancy allowance')]:
                if d.get(key)!=before.get(key):effects.append(f"{row['label']} — {label}: {before.get(key,'not recorded')} → {d.get(key,'not recorded')}")
        elif row['kind']=='illness' and (d.get('end_global_day')!=before.get('end_global_day') or d.get('status')!=before.get('status')):
            effects.append(f"{row['label']}: {d.get('status','updated')} · ends GD {d.get('end_global_day','not recorded')}")
        elif row['kind'] not in {'death','roll_rule','occult_rule','addon_rule','event_rule','planner_rule','clock_state','save_metadata','clock_diagnostic','dice_audit','dynasty_branch'}:
            effects.append(f"{'Adds' if not old else 'Updates'} {row['label']} ({row['kind'].replace('_',' ')}) · GD {row['global_day']}")
    result=plan.get('result') or {}
    if result.get('suggested_marriage_global_day') is not None:effects.append(f"Suggests marriage on GD {result['suggested_marriage_global_day']}")
    if kind=='roll' and not effects:effects.append('Records the result; no automatic follow-up or death is created.')
    return {'counts':counts,'effects':effects,'outcome':result.get('outcome'),'completed_unchanged':kind!='roll'}


def prepare(session,save,user,kind,form,roll=None,native=False):
    session.flush()
    if session.get_bind().dialect.name=='sqlite':session.execute(update(ChronicleSave).where(ChronicleSave.id==save.id).values(revision=ChronicleSave.revision))
    session.refresh(save,with_for_update=True)
    if roll:session.refresh(roll)
    now=datetime.now(timezone.utc)
    session.execute(delete(ActionPreview).where(ActionPreview.user_id==user.id,ActionPreview.created_at<now-timedelta(days=1)))
    actual=None;wording=str(form.get('outcome') or '')
    if roll:
        if roll.deleted or roll.kind!='roll' or roll.data.get('completed'):raise ValueError('That roll is no longer pending.')
        if native:
            # Reopening a preview never gives another throw for this obligation.
            from .models import DiceAudit
            audit_context='pending-preview-v'+str(roll.version)
            audit=session.scalar(select(DiceAudit).where(DiceAudit.save_id==save.id,DiceAudit.context==audit_context,DiceAudit.context_id==roll.id).order_by(DiceAudit.created_at.desc()).limit(1))
            if not audit:
                notation,_=dice.notation_for_roll(roll.data.get('die'),roll.data.get('bad_results'))
                audit=dice.audited_roll(session,notation,save.id,audit_context,roll.id)
            actual=audit.total
        else:
            try:actual=int(form.get('actual'))
            except (TypeError,ValueError):raise ValueError('Enter a whole-number result.')
            notation,_=dice.notation_for_roll(roll.data.get('die'),roll.data.get('bad_results'));quantity,sides,modifier=dice.parse(notation)
            if not quantity+modifier<=actual<=quantity*sides+modifier:raise ValueError('That result is outside the configured die range.')
    session.flush();stamp=fingerprint(save)
    plan=simulate(session,save,lambda:domain.complete_roll(session,save,roll,actual,wording) if roll else apply_packs(session,save,form) if kind=='packs' else apply_settings(session,save,form))
    description=describe(plan,kind)
    if roll:
        sim=session.get(Record,roll.data.get('sim_id')) if roll.data.get('sim_id') else None
        if sim and sim.save_id==save.id and sim.data.get('death_global_day') is not None and not plan['result'].get('death_changed'):
            description['effects'].append(f"Existing death on GD {sim.data['death_global_day']} remains unchanged, including any recorded time.")
    # Full settings/record JSON remains private on the server, never in HTML.
    if len(json.dumps(plan,default=str))>8*1024*1024:raise ValueError('This change is too large for one preview. Make smaller calendar or rulepack changes.')
    ticket=ActionPreview(user_id=user.id,save_id=save.id,operation=kind,fingerprint=stamp,
        payload={'plan':plan,'description':description,'actual':actual,'roll_id':roll.id if roll else None,
                 'return_to':safe_return(str(form.get('return_to') or '/p/today'))})
    session.add(ticket);session.flush();return ticket


def safe_return(value):return value if value.startswith('/') and not value.startswith('//') and '\\' not in value else '/p/today'


def confirm(session,save,ticket):
    # Serialize with normal save writers. No partial plan is applied on a stale tab.
    if session.get_bind().dialect.name=='sqlite':session.execute(update(ChronicleSave).where(ChronicleSave.id==save.id).values(revision=ChronicleSave.revision))
    session.refresh(save,with_for_update=True)
    if ticket.consumed:raise ValueError('This preview has already been confirmed.')
    created=ticket.created_at.replace(tzinfo=timezone.utc) if ticket.created_at.tzinfo is None else ticket.created_at
    if datetime.now(timezone.utc)-created>timedelta(minutes=15):raise ValueError('This preview expired. Review a fresh preview.')
    if fingerprint(save)!=ticket.fingerprint:raise ValueError('The save changed after this preview. Review a fresh preview; nothing was applied.')
    plan=ticket.payload['plan']
    for change in plan['records']:
        old,new=change['before'],change['after'];row=session.get(Record,new['id'])
        if old and (not row or snapshot(row,RECORD_FIELDS)!=old):raise ValueError('A related record changed. Review a fresh preview; nothing was applied.')
        if not old and row:raise ValueError('A related record already exists. Refresh the preview.')
    for change in plan['records']:
        old,new=change['before'],change['after'];row=session.get(Record,new['id']) if old else Record()
        for key,value in new.items():setattr(row,key,copy.deepcopy(value))
        session.add(row);session.flush();domain.journal(session,row,'delete' if row.deleted else 'upsert',old['version'] if old else 0)
    # Retain any newer receipt-only telemetry while applying the reviewed settings.
    telemetry={k:v for k,v in (save.settings or {}).items() if k in {'clock_ui_observation','clock_receipt_summary'}}
    for key,value in plan['after_save'].items():setattr(save,key,copy.deepcopy(value))
    save.settings={**save.settings,**telemetry};ticket.consumed=True;session.flush()
    return ticket.payload


def response(m,request,session,save,form,kind,roll=None,native=False):
    user=m.signed_in(request,session)
    if not user:raise HTTPException(401)
    m.owned_save(request,session,save.id)
    if request.session.get('save_id')!=save.id:raise HTTPException(409,'The active save changed.')
    try:ticket=prepare(session,save,user,kind,form,roll,native)
    except ValueError as exc:raise HTTPException(400,str(exc))
    data={**ticket.payload['description'],'token':ticket.id,'actual':ticket.payload['actual'],'save_id':save.id,'kind':kind,'label':roll.label if roll else 'Calendar and rules' if kind=='settings' else 'Rulepack selection'}
    if request.headers.get('X-Decades-Fragment'):return JSONResponse({'preview':data})
    return m.templates.TemplateResponse(request,'action_preview.html',m.context(request,session,preview=data,page='rules' if kind!='roll' else 'rolls'))


def register(m):
    from fastapi import Request
    @m.app.post('/api/previews/{token}/confirm')
    def confirm_action(request:Request,token:str):
        with m.db() as session:
            user=m.signed_in(request,session)
            if not user:raise HTTPException(401)
            ticket=session.scalar(select(ActionPreview).where(ActionPreview.id==token,ActionPreview.user_id==user.id).with_for_update())
            if not ticket:raise HTTPException(404)
            save=m.owned_save(request,session,ticket.save_id)
            if request.session.get('save_id')!=save.id:raise HTTPException(409,'The active save changed.')
            if ticket.operation=='roll':
                previous=[session.get(Record,c['before']['id']) for c in ticket.payload['plan']['records'] if c['before']]
                m.set_today_undo(request,'Confirmed roll result',[r for r in previous if r])
            try:payload=confirm(session,save,ticket)
            except ValueError as exc:raise HTTPException(409,str(exc))
            if ticket.operation=='roll':
                request.session['today_undo']['delete_ids']=[c['after']['id'] for c in payload['plan']['records'] if not c['before']]
                row=session.get(Record,payload['roll_id'])
                request.session['last_roll']={'number':payload['actual'],'die':row.data.get('die'),'roll':row.label,'outcome':row.data.get('outcome'),'failed':row.data.get('outcome')=='Failed'}
            else:request.session['rules_notice']='Reviewed changes applied. Completed roll history was preserved.'
            if request.headers.get('X-Decades-Fragment'):return JSONResponse({'ok':True,'kind':'roll' if ticket.operation=='roll' else 'settings','id':payload.get('roll_id'),'save_id':save.id,'return_to':payload['return_to']})
        return RedirectResponse(payload['return_to'],303)
