"""Owned-save routes for optional play support, using existing synced records."""
import hashlib
from uuid import uuid4
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select,update
from sqlalchemy.exc import IntegrityError
from . import play_support as play, infinite_decades, domain, drama
from .models import Record,ChronicleSave

PAGES={'play-next':('Play Session Planner','Priorities and rotational handover notes'),
       'family-projects':('Family Projects','Ambitions, secrets, event recovery and achievements'),
       'historical-check':('Historical Checks','Optional, overridable checks against your selected rules'),
       'writers-room':('Letters & Journals','Fictional perspectives grounded in selected recorded facts'),
       'branch-comparison':('Compare Branches','Read-only Infinite Decades checkpoint comparisons')}
KINDS={'sim','household','pregnancy','roll','relationship','game_candidate','play_rotation','session_journal','task','note','death','event','migration','game_history','illness','heirloom','economy_entry','era_rule','era_guidance','era_check','correspondence','campaign','event_result','drama_scene','education_plan','dowry_plan'}

def rows_for(session,save):
    return list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind.in_(KINDS),Record.deleted.is_(False))))

def discovery(request,session,save):
    state=request.session.get('drama_discovery') or {}
    if state.get('save_id')!=save.id or state.get('epoch','')!=infinite_decades.state(save).get('epoch',''): return None
    if (request.session.get('drama_state') or {}).get('card_id')!='secret-discovery': return None
    row=session.get(Record,state.get('record_id'));witness=session.get(Record,state.get('witness_id'))
    if not row or row.save_id!=save.id or row.deleted or row.data.get('feature')!='secret': return None
    if not witness or witness.save_id!=save.id or witness.id not in row.data.get('knower_ids',[]): return None
    return {'record_id':row.id,'label':row.label,'notes':row.data.get('notes',''),'witness':witness.label}

def guard_scene(request,save):
    marker=request.session.get('drama_discovery')
    if not marker or not request.url.path.startswith('/drama/') or request.method=='GET' or request.url.path in {'/drama/draw','/drama/discard'}: return
    if marker.get('save_id')!=save.id or marker.get('epoch','')!=infinite_decades.state(save).get('epoch',''):
        raise HTTPException(409,'This discovery scene belongs to a different save or branch. Start a new scene.')

def render(request,session,ctx,templates):
    save=ctx.get('save');page=ctx.get('page')
    if page=='branch-comparison':
        options=list(session.execute(select(Record.id,Record.label,Record.data['meta']).where(
            Record.save_id==save.id,Record.kind=='dynasty_branch'))) if save else []
        choices=[{'id':r[0],'label':r[1],'meta':r[2] or {}} for r in options]
        comparisons=[]
        for side,index in [('left',0),('right',1)]:
            chosen=request.query_params.get(side) or (choices[min(index,len(choices)-1)]['id'] if choices else '')
            branch=next((b for b in choices if b['id']==chosen),None)
            if chosen and not branch: raise HTTPException(404,'That branch is not part of this save.')
            if not branch: continue
            if chosen==infinite_decades.state(save).get('active_branch_id') and not infinite_decades.frozen(save):
                observed=play.active(list(session.scalars(select(Record).where(Record.save_id==save.id,Record.deleted.is_(False),
                    Record.kind.in_({'sim','household','economy_entry','relationship','death','migration','story_entry','event_result','drama_scene','roll'})))))
                payload={'global_day':save.global_day,'member_sim_ids':[r.id for r in observed if r.kind=='sim'],
                         'records':[{'id':r.id,'kind':r.kind,'label':r.label,'data':r.data,'global_day':r.global_day,'deleted':False} for r in observed]}
            else:
                row=session.get(Record,chosen)
                try: payload=infinite_decades.unpack_snapshot(row.data['snapshot'])
                except ValueError as error: raise HTTPException(409,str(error)) from error
            comparisons.append({'branch':branch,'metrics':play.branch_metrics(payload)})
            del payload
        ctx.update(compare_options=choices,comparisons=comparisons)
        return templates.TemplateResponse(request,'branch_comparison.html',ctx)
    rows=play.active(rows_for(session,save)) if save else []
    people=sorted((r for r in rows if r.kind=='sim'),key=lambda r:r.label.casefold())
    homes=sorted((r for r in rows if r.kind=='household'),key=lambda r:r.label.casefold())
    source_search=str(request.query_params.get('source_search') or '').strip()[:100]
    source_refs={r.data.get(key) for r in rows for key in ('evidence_id','event_id') if r.data.get('feature') in play.FEATURES}
    sources=sorted((r for r in rows if r.kind in {'event','campaign','heirloom'} and (not source_search or source_search.casefold() in r.label.casefold())),key=lambda r:r.global_day or 0,reverse=True)[:150]
    source_ids={r.id for r in sources}
    sources.extend(r for r in rows if r.id in source_refs-source_ids and r.kind in {'event','campaign','heirloom'})
    ctx.update(play_notice=request.session.pop('play_notice',None),play_sims=people,play_homes=homes,play_names={r.id:r.label for r in rows},
               play_events=sources,source_search=source_search,
               play_metrics=play.METRICS,play_features=play.FEATURES,play_consequences=play.CONSEQUENCES,
               form_ids={**{r.id:uuid4().hex for r in rows},**{key:uuid4().hex for key in (*play.FEATURES,'accuracy','writing','settings')}})
    if page=='family-projects':
        ctx['projects']=play.project_views(rows,save) if save else {key:[] for key in play.FEATURES}
        return templates.TemplateResponse(request,'family_projects.html',ctx)
    if page=='historical-check':
        ctx.update(accuracy_rules=[r for r in rows if r.data.get('feature')=='accuracy'],
                   accuracy_warnings=play.historical_checks(rows,save) if save and (save.settings or {}).get('play_historical_checks',False) else [],
                   accuracy_overrides=[r for r in rows if r.data.get('feature')=='accuracy_override'],
                   accuracy_fields=play.ACCURACY_FIELDS,active_packs=[(save.settings or {}).get('core_ruleset_id','severaludo')]+list((save.settings or {}).get('selected_rule_packs') or []) if save else [],
                   era_sources=[r for r in rows if r.kind in {'era_guidance','era_rule'} and r.data.get('feature')!='accuracy'][:40])
        ctx['form_ids'].update({w['key']:uuid4().hex for w in ctx['accuracy_warnings']})
        return templates.TemplateResponse(request,'historical_checks.html',ctx)
    if page=='writers-room':
        facts=sorted((r for r in rows if play.eligible_writing_fact(r,save)),key=lambda r:(r.global_day,r.updated_at),reverse=True)[:100] if save else []
        ctx.update(writing_facts=facts,perspectives=play.PERSPECTIVES,
                   writings=sorted((r for r in rows if r.kind=='correspondence'),key=lambda r:(r.global_day or 0,r.created_at),reverse=True)[:30])
        return templates.TemplateResponse(request,'writers_room.html',ctx)
    horizon=play.integer(request.query_params.get('days'),7)
    if horizon not in {3,7,14,28}: horizon=7
    plan=play.session_plan(rows,save,horizon) if save else []
    ctx.update(play_plan=plan,play_horizon=horizon)
    return templates.TemplateResponse(request,'play_next.html',ctx)

def register(app,db,context,templates):
    router=APIRouter()
    @router.get('/records/{record_id}/why')
    def explanation(request:Request,record_id:str):
        from .why import explain
        with db() as session:
            ctx=context(request,session,page='rolls',title='Why did this happen?')
            row=session.get(Record,record_id)
            if not ctx.get('save') or not row or row.save_id!=ctx['save'].id: raise HTTPException(404)
            ctx['why']=explain(row)
            return templates.TemplateResponse(request,'why.html',ctx)
    def current(request,session,form):
        save=context(request,session).get('save')
        if not save or save.id!=str(form.get('save_id') or ''): raise HTTPException(409,'The active save changed. Refresh this page.')
        # Serialize edits within one save on both local SQLite and hosted Postgres.
        if session.get_bind().dialect.name=='sqlite':
            session.execute(update(ChronicleSave).where(ChronicleSave.id==save.id).values(revision=ChronicleSave.revision))
        session.scalar(select(ChronicleSave).where(ChronicleSave.id==save.id).with_for_update().execution_options(populate_existing=True))
        infinite_decades.guard_request(request,save)
        if infinite_decades.frozen(save): raise HTTPException(409,'This dynasty branch is read-only.')
        return save
    def identity(save,form,purpose):
        key=str(form.get('form_id') or '')
        if len(key)!=32 or any(c not in '0123456789abcdef' for c in key): raise HTTPException(400,'Refresh the form before saving.')
        return hashlib.sha256(f'{save.id}:{purpose}:{key}'.encode()).hexdigest()[:32]
    def text(form,key,limit=4000):
        value=str(form.get(key) or '').strip()
        if len(value)>limit: raise HTTPException(400,f'{key.replace("_"," ")} is too long.')
        return value
    def ref(rows,value,kinds,required=False):
        row=next((r for r in rows if r.id==value and r.kind in kinds),None)
        if (value or required) and not row: raise HTTPException(400,'Choose a record from this active save.')
        return row
    def put(session,save,form,feature,kind,label,data):
        existing_id=text(form,'record_id',64)
        row=session.get(Record,existing_id) if existing_id else None
        if existing_id:
            if not row or row.save_id!=save.id or row.deleted or row.kind!=kind or row.data.get('feature')!=feature or row.data.get('infinite_frozen'):
                raise HTTPException(404,'That editable record is not in this branch.')
            if row.version!=play.integer(form.get('version')): raise HTTPException(409,'This record changed. Refresh before saving.')
        else:
            rid=identity(save,form,feature)
            if session.get(Record,rid): return session.get(Record,rid)
            row=Record(id=rid,save_id=save.id,kind=kind,label=label,global_day=save.global_day,data={},version=0)
            session.add(row)
        base=row.version or 0
        if feature=='secret':
            old=set(row.data.get('knower_ids') or []);new=set(data.get('knower_ids') or [])
            history=list(row.data.get('knowledge_history') or [])
            if new!=old: history.append({'global_day':save.global_day,'learned':sorted(new-old),'forgotten_or_corrected':sorted(old-new)})
            data['knowledge_history']=history
        row.label=label;row.data={**row.data,**data,'feature':feature};row.version=base+1
        try: session.flush()
        except IntegrityError as error: raise HTTPException(409,'This form was already saved or changed. Refresh before trying again.') from error
        domain.journal(session,row,'upsert',base);save.revision+=1
        return row
    @router.post('/play-support/project/{feature}')
    async def project(request:Request,feature:str):
        if feature not in play.FEATURES: raise HTTPException(404)
        form=await request.form()
        with db() as session:
            save=current(request,session,form);rows=play.active(rows_for(session,save))
            label=text(form,'label',200);notes=text(form,'notes')
            if not label: raise HTTPException(400,'Give this record a name.')
            home=ref(rows,text(form,'household_id',64),{'household'},feature in {'ambition','recovery'})
            data={'household_id':home.id if home else None,'notes':notes,'enabled':form.get('enabled')=='on',
                  'status':text(form,'status',20) or 'Active'}
            if data['status'] not in {'Active','Paused','Completed'}: raise HTTPException(400,'Choose a supported status.')
            if feature in {'ambition','achievement'}:
                metric=text(form,'metric',40)
                if metric not in play.METRICS: raise HTTPException(400,'Choose a progress measure.')
                if metric=='wealth' and not home: raise HTTPException(400,'Choose the household whose funds should be measured.')
                founder=ref(rows,text(form,'founder_id',64),{'sim'},metric in {'descendants','occult','generations'})
                evidence=ref(rows,text(form,'evidence_id',64),{'event','heirloom'},metric in {'event_survivors','heirloom_years'})
                if evidence and metric=='event_survivors' and evidence.kind!='event': raise HTTPException(400,'Choose an event for survival tracking.')
                if evidence and metric=='heirloom_years' and evidence.kind!='heirloom': raise HTTPException(400,'Choose an heirloom for heirloom tracking.')
                target=play.integer(form.get('target'));progress=play.integer(form.get('progress'),0)
                if target is None or not 1<=target<=10**12 or progress<0: raise HTTPException(400,'Enter a positive target and nonnegative progress.')
                data.update(metric=metric,target=target,progress=progress,founder_id=founder.id if founder else None,evidence_id=evidence.id if evidence else None)
            if feature=='secret':
                subject=ref(rows,text(form,'sim_id',64),{'sim'})
                knowers=list(dict.fromkeys(form.getlist('knower_ids')))
                for value in knowers: ref(rows,value,{'sim'},True)
                data.update(sim_id=subject.id if subject else None,knower_ids=knowers,category=text(form,'category',80))
            if feature=='recovery':
                event=ref(rows,text(form,'event_id',64),{'event','campaign'},True)
                due=play.integer(form.get('due_global_day'))
                if due is None or due<0 or (not form.get('record_id') and due<save.global_day): raise HTTPException(400,'Choose today or a future review day for a new recovery plan.')
                effects=form.getlist('consequences')
                if any(effect not in play.CONSEQUENCES for effect in effects): raise HTTPException(400,'Choose supported consequences.')
                debt=play.integer(form.get('debt'),0)
                if debt<0: raise HTTPException(400,'Debt cannot be negative.')
                data.update(event_id=event.id,consequences=effects,debt=debt,due_global_day=due,
                            completed_steps=text(form,'completed_steps'))
                if not form.get('record_id'): data['start_global_day']=save.global_day
            put(session,save,form,feature,play.FEATURES[feature][1],label,data)
            request.session['play_notice']='Saved. This is an optional player plan; no Sim, relationship or money was changed.'
        return RedirectResponse('/p/family-projects#'+feature,303)
    @router.post('/play-support/secret-scene')
    async def secret_scene(request:Request):
        form=await request.form()
        with db() as session:
            save=current(request,session,form);rows=play.active(rows_for(session,save))
            row=ref(rows,text(form,'record_id',64),{'note'},True)
            if row.data.get('feature')!='secret': raise HTTPException(400,'Choose a tracked secret.')
            witness=ref(rows,text(form,'witness_id',64),{'sim'},True)
            if witness.id not in row.data.get('knower_ids',[]): raise HTTPException(400,'That Sim is not recorded as knowing this secret.')
            if not play.advanced.living(witness,save.global_day): raise HTTPException(400,'Choose a living Sim to play this scene.')
            request.session['drama_state']=drama.draw_state(save,'common',witness.id,row.data.get('household_id') or '',row.data.get('sim_id') or '',card_id='secret-discovery')
            request.session['drama_discovery']={'save_id':save.id,'epoch':infinite_decades.state(save).get('epoch',''),
                'record_id':row.id,'witness_id':witness.id}
            request.session['drama_notice']='A secret-themed scene is ready. The recorded discovery is shown separately from fictional choices.'
        return RedirectResponse('/p/drama',303)
    @router.post('/play-support/accuracy-toggle')
    async def accuracy_toggle(request:Request):
        form=await request.form()
        with db() as session:
            save=current(request,session,form)
            save.settings={**save.settings,'play_historical_checks':form.get('enabled')=='on'};save.revision+=1
        return RedirectResponse('/p/historical-check',303)
    @router.post('/play-support/accuracy')
    async def accuracy(request:Request):
        form=await request.form()
        with db() as session:
            save=current(request,session,form);category=text(form,'category',30)
            if category not in play.ACCURACY_FIELDS: raise HTTPException(400,'Choose a check category.')
            match=text(form,'match_text',100);label=text(form,'label',200);source=text(form,'source',1000)
            start=play.integer(form.get('allowed_from_year'));end=play.integer(form.get('allowed_until_year'))
            if not label or not match or not source or (start is None and end is None): raise HTTPException(400,'Provide a name, matching phrase, source and at least one year boundary.')
            if start is not None and end is not None and start>end: raise HTTPException(400,'The first allowed year must not follow the last allowed year.')
            put(session,save,form,'accuracy','era_rule',label,{'category':category,'match_text':match,'allowed_from_year':start,'allowed_until_year':end,
                'source':source,'rule_pack':text(form,'rule_pack',100),'enabled':form.get('enabled')=='on'})
        return RedirectResponse('/p/historical-check',303)
    @router.post('/play-support/accuracy-override')
    async def override(request:Request):
        form=await request.form()
        with db() as session:
            save=current(request,session,form);rows=play.active(rows_for(session,save))
            warning=next((w for w in play.historical_checks(rows,save) if w['key']==form.get('warning_key')),None)
            reason=text(form,'notes')
            if not warning or not reason: raise HTTPException(400,'Choose a current warning and explain the override.')
            if not warning['overridden']:
                put(session,save,form,'accuracy_override','era_check','Override: '+warning['rule'].label,
                    {'warning_key':warning['key'],'record_id':warning['record'].id,'rule_id':warning['rule'].id,'notes':reason})
        return RedirectResponse('/p/historical-check',303)
    @router.post('/play-support/writing')
    async def writing(request:Request):
        form=await request.form()
        with db() as session:
            save=current(request,session,form);rows=play.active(rows_for(session,save))
            author=ref(rows,text(form,'author_id',64),{'sim'},True);recipient=ref(rows,text(form,'recipient_id',64),{'sim'})
            perspective=text(form,'perspective',30);kind=text(form,'writing_kind',15);subject=text(form,'label',180)
            if perspective not in play.PERSPECTIVES or kind not in {'letter','diary'} or not subject: raise HTTPException(400,'Choose a perspective, writing type and subject.')
            selected=list(dict.fromkeys(form.getlist('fact_ids')))
            if not 1<=len(selected)<=8: raise HTTPException(400,'Choose one to eight recorded facts.')
            facts=[ref(rows,key,{'death','event_result','migration','relationship','pregnancy','game_history','drama_scene'},True) for key in selected]
            if any(not play.eligible_writing_fact(r,save) for r in facts): raise HTTPException(400,'Only dated, recorded facts from today or earlier can be included, not pending plans.')
            data=play.correspondence(save,author,recipient,perspective,kind,subject,facts)
            put(session,save,form,'perspective_writing','correspondence',subject,data)
        return RedirectResponse('/p/writers-room',303)
    @router.post('/play-support/handover')
    async def handover(request:Request):
        form=await request.form()
        with db() as session:
            save=current(request,session,form);rows=rows_for(session,save)
            home=next((r for r in rows if r.id==form.get('household_id') and r.kind=='household' and not r.data.get('infinite_frozen')),None)
            if not home: raise HTTPException(400,'Choose a household from this active branch.')
            notes=str(form.get('notes') or '').strip()
            if not 1<=len(notes)<=4000: raise HTTPException(400,'Write 1–4,000 characters of handover notes.')
            rid=identity(save,form,'handover')
            if not session.get(Record,rid):
                members={r.id for r in rows if r.kind=='sim' and play.home_id(r)==home.id}
                baseline={r.id:play.handover_value(r) for r in rows if play.linked(r,home,members)}
                row=Record(id=rid,save_id=save.id,kind='session_journal',label='Handover: '+home.label,global_day=save.global_day,
                    data={'feature':'handover','household_id':home.id,'notes':notes,'body':notes,'baseline':baseline})
                session.add(row);session.flush();domain.journal(session,row,'upsert',0);save.revision+=1
            request.session['play_notice']='Handover saved. The next visit will compare changes with this point.'
        return RedirectResponse('/p/play-next',303)
    app.include_router(router)
