"""Save-local switches and explicit prerequisites for optional roll automation."""
import hashlib
import json
import re
from uuid import uuid4
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import update
from . import roll_automation as auto, domain, pregnancy_planning
from .models import Record, ChronicleSave

def stamp(save):return hashlib.sha256(json.dumps(auto.config(save),sort_keys=True).encode()).hexdigest()
def option(rule):
    pack=rule.data.get('rule_pack_id')
    for k,v in auto.PACKS.items():
        if pack==v:return k
    return {'fairy_changeling':'changeling','fairy_changeling_truth':'changeling','vampire_feeding_suspicion':'feeding','mermaid_dehydration':'dehydration'}.get(rule.data.get('rule_key'))

def context(session,save,request):
    definitions=[r for r in auto.rows(session,save,['addon_rule','occult_rule']) if option(r)]
    rules=sorted(definitions,key=lambda r:r.label.casefold())
    targets=[r for r in auto.rows(session,save,['sim','household']) if auto.active(r) and (r.kind=='household' or auto.living(r,save))]
    situations=[r for r in auto.rows(session,save,['task']) if r.data.get('feature')==auto.FEATURE and auto.active(r)]
    selected=next((r for r in targets if r.id==request.query_params.get('target')),None)
    return {'auto_selected':selected,'auto_options':auto.OPTIONS,'auto_config':auto.config(save),'auto_stamp':stamp(save),'auto_rules':rules,
            'auto_targets':sorted(targets,key=lambda r:(r.kind,r.label.casefold())),'auto_situations':situations,
            'auto_nonce':uuid4().hex,'auto_notice':request.session.pop('roll_automation_notice',None)}

def register(m):
    @m.app.post('/api/roll-automation/{action}')
    async def perform(request:Request,action:str):
        form=await request.form()
        with m.db() as session:
            ctx=m.context(request,session);save=ctx.get('save')
            if not save:raise HTTPException(400,'Choose a save first.')
            if form.get('save_id')!=save.id:raise HTTPException(409,'The active save changed. Reopen Roll Automation.')
            if not domain.automation_enabled(save) and action!='settings':raise HTTPException(409,'Resume master automation before recording automatic work.')
            session.execute(update(ChronicleSave).where(ChronicleSave.id==save.id).values(revision=ChronicleSave.revision));session.refresh(save)
            try:
                if action=='settings':
                    if form.get('config_stamp')!=stamp(save):raise HTTPException(409,'These settings changed in another window. Reload before saving.')
                    old=auto.config(save);cfg=dict(old);starts=dict(cfg.get('from') or {})
                    for key in auto.OPTIONS:
                        cfg[key]=form.get(key)=='on'
                        if cfg[key] and not old.get(key):starts.setdefault(key,save.global_day)
                    cfg['from']=starts
                    for key,default in [('pregnancy_min_age',13),('pregnancy_max_age',49)]:
                        n=auto.number(form.get(key),default)
                        if not 13<=n<=120:raise ValueError('Pregnancy eligibility ages must be between 13 and 120.')
                        cfg[key]=n
                    if cfg['pregnancy_max_age']<cfg['pregnancy_min_age']:raise ValueError('Maximum pregnancy age must not be below the minimum.')
                    for key in ('pregnancy_married_only','pregnancy_side_only','pregnancy_yearly_married_only','pregnancy_yearly_side_only'):cfg[key]=form.get(key)=='on'
                    cfg['pregnancy_yearly_mode']=str(form.get('pregnancy_yearly_mode') or 'age_table')
                    if cfg['pregnancy_yearly_mode'] not in {'age_table','custom'}:raise ValueError('Choose the age-based Annual Baby Roll or custom yearly odds.')
                    for key in ('pregnancy_yearly_die','pregnancy_yearly_success'):
                        if key in form:cfg[key]=str(form.get(key) or '').strip()
                    yearly=pregnancy_planning.annual_table(cfg) if cfg['pregnancy_yearly_mode']=='custom' else ('d20','age table')
                    if cfg.get('pregnancy_yearly') and cfg['pregnancy_yearly_mode']=='custom' and not yearly:
                        raise ValueError('Set the yearly pregnancy die and successful results before enabling yearly checks.')
                    allowed={r.id for r in auto.rows(session,save,['addon_rule','occult_rule']) if option(r)}
                    cfg['rules']={rid:rid in form.getlist('rule_on') for rid in allowed}
                    save.settings={**save.settings,'roll_automation':cfg};save.revision+=1
                    retired=auto.retire_paused(session,save);auto.schedule(session,save)
                    notice=f'Automation preferences saved. {retired} unfinished checks retired; completed results unchanged.'
                elif action=='situation':
                    rule=session.get(Record,str(form.get('rule_id') or ''));target=session.get(Record,str(form.get('target_id') or ''))
                    if not rule or rule.save_id!=save.id or rule.kind not in {'addon_rule','occult_rule'} or not option(rule):raise ValueError('Choose an available optional rule.')
                    key=option(rule)
                    if not auto.enabled(save,key,rule):raise ValueError('Enable this automation and its source module first.')
                    if not target or target.save_id!=save.id or not auto.active(target) or target.kind not in {'sim','household'}:raise ValueError('Choose a Sim or household in this save.')
                    if target.kind=='sim' and not auto.living(target,save):raise ValueError('Choose a living Sim.')
                    evidence=str(form.get('evidence') or '').strip()
                    if form.get('confirmed')!='on' or not evidence or len(evidence)>1000:raise ValueError('Confirm the prerequisites and record the situation (up to 1,000 characters).')
                    due=auto.number(form.get('start_day'),save.global_day)
                    if due<save.global_day or due>20000:raise ValueError('Start today or on a future Global Day.')
                    cadence=str(form.get('cadence') or 'once')
                    if cadence not in {'once','annual'}:raise ValueError('Choose once or yearly.')
                    if cadence=='annual' and not re.search(r'annual|year',str(rule.data.get('trigger') or '')+' '+str(rule.data.get('rule_text') or ''),re.I):raise ValueError('This source is not a recurring rule; record each distinct situation once.')
                    notation=str(rule.data.get('die') or '')
                    table=None
                    if notation=='dynamic':
                        notation=str(form.get('die') or '').lower();table=str(form.get('result_rules') or '').strip()
                        if not re.fullmatch(r'd(?:[2-9]|[1-9][0-9]{1,2}|1000)',notation) or not table:raise ValueError('For a branching table, choose its current step’s die and numbered result table from the source.')
                    if not re.fullmatch(r'd(?:[2-9]|[1-9][0-9]{1,2}|1000)',notation):raise ValueError('This is guidance, not a dice table.')
                    bad=str(form.get('bad_results') or '').strip()
                    if bad and not re.fullmatch(r'[0-9, –\-]+',bad):raise ValueError('Use numbers or ranges for lethal results.')
                    nonce=str(form.get('nonce') or '')
                    if not re.fullmatch(r'[a-f0-9]{32}',nonce):raise ValueError('Refresh the form before recording a situation.')
                    existing=next((r for r in auto.rows(session,save,['task']) if r.data.get('feature')==auto.FEATURE and r.data.get('nonce')==nonce),None)
                    if not existing:
                        situation=Record(save_id=save.id,kind='task',label=domain.record_label(f'{target.label} — {rule.label}'),global_day=due,
                            data={'feature':auto.FEATURE,'option':key,'rule_id':rule.id,'target_id':target.id,'sim_id':target.id if target.kind=='sim' else None,
                                  'household_id':target.id if target.kind=='household' else target.data.get('current_household_id'),'nonce':nonce,
                                  'enabled':True,'start_day':due,'cadence':cadence,'die':notation,'result_rules':table,'bad_results':bad,'evidence':evidence})
                        session.add(situation);session.flush();domain.journal(session,situation,'upsert',0);save.revision+=1
                    auto.schedule(session,save);notice='Situation recorded. Applicable checks will appear in Today; no dice were thrown.'
                elif action in {'stop','prerequisites'}:
                    row=session.get(Record,str(form.get('record_id') or ''))
                    if not row or row.save_id!=save.id or not auto.active(row):raise ValueError('This record is unavailable.')
                    if auto.number(form.get('record_version'))!=row.version:raise HTTPException(409,'This record changed. Refresh first.')
                    if action=='stop':
                        if row.kind!='task' or row.data.get('feature')!=auto.FEATURE:raise ValueError('Choose a monitored situation.')
                        auto.change(session,row,{'enabled':False})
                        for roll in auto.rows(session,save,['roll']):
                            if roll.data.get('situation_id')==row.id and not roll.data.get('completed'):
                                base=roll.version;roll.deleted=True;roll.version+=1;domain.journal(session,roll,'delete',base)
                        notice='Stopped monitoring this situation. Completed results were kept.'
                    else:
                        fields={'sim':('avatar_bending_manifested','avatar_traditional_air_nomad','avatar_human_form_spirit','avatar_healer_ancestry','got_first_men_ancestry','avatar_bloodbender_ancestry','avatar_metalbender_ancestry','avatar_combustion_ancestry','avatar_technique_discovered','avatar_teacher_available','avatar_state_mastered'),
                                'household':('roll_context_major_court','roll_context_active_feud','roll_context_winter_affected','roll_context_war_affected','roll_context_fixed_major_event')}
                        if row.kind not in fields:raise ValueError('Choose a Sim or household.')
                        values={k:form.get(k)=='on' for k in fields[row.kind]}
                        if 'pregnancy_rule_permission' in form:
                            permission=str(form.get('pregnancy_rule_permission') or 'inherit')
                            if permission not in {'inherit','allowed','blocked'}:raise ValueError('Choose an allowed pregnancy eligibility setting.')
                            values['pregnancy_rule_permission']=permission
                            values['pregnancy_available_from_year']=auto.number(form.get('pregnancy_available_from_year'))
                            values['pregnancy_rule_note']=str(form.get('pregnancy_rule_note') or '').strip()[:1000]
                        if row.kind=='sim' and form.get('pregnancy_facts_present')=='1':
                            values['pregnancy_fertility_boost']=form.get('pregnancy_fertility_boost')=='on'
                            values['pregnancy_established_partnership']=form.get('pregnancy_established_partnership')=='on'
                        if row.kind=='sim':
                            status=str(form.get('avatar_prisoner_status') or '')
                            if status not in {'','captured','missing'}:raise ValueError('Choose a captivity status.')
                            values['avatar_prisoner_status']=status
                        auto.change(session,row,values);auto.schedule(session,save);notice='Prerequisites saved. Only enabled, applicable modules schedule checks.'
                    save.revision+=1
                else:raise HTTPException(404)
            except ValueError as exc:raise HTTPException(400,str(exc))
            m._TODAY_SCHEDULE_CHECKED.pop(save.id,None)
            request.session['roll_automation_notice']=notice
        return RedirectResponse('/p/roll-automation',303)
