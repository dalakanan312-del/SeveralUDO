"""Small, authenticated presentation endpoints; no independent rules engine."""
import json
from sqlalchemy.exc import IntegrityError
from urllib.parse import urlencode
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select,func,and_,or_
from . import usability as ui,domain,play_clarity as clarity,heritage
from .models import Record,ClockLink,UiPreference,ChronicleSave,Membership

SECTIONS={'decisions':'Needs your decision','happening':'Happening today','completed':'Completed'}
LIMIT=24

def guard_action(request,save,row):
    if not request.headers.get('X-Decades-Fragment'):return
    if request.headers.get('X-UI-Save')!=save.id or request.session.get('save_id')!=save.id:
        raise HTTPException(409,'The open save changed. Refresh before acting.')
    if request.headers.get('X-Decades-Fragment')!='preview' and ui.integer(request.headers.get('X-Record-Version'))!=row.version:
        raise HTTPException(409,'This record changed after you opened it. Refresh and review the latest details.')

def board(session,save,params,section=None):
    """Filter and paginate in SQL before loading record JSON."""
    g=save.global_day;window=params.get('window','today')
    if window not in {'today','overdue','future'}:window='today'
    def when(day):return day<g if window=='overdue' else day>g if window=='future' else day==g
    d=Record.data;k=Record.kind
    day=func.coalesce(d['due_global_day'].as_integer(),Record.global_day)
    post_death=or_(d['allow_after_death'].as_boolean().is_(True),and_(d['occult_roll'].as_boolean().is_(True),d['occult_rule_key'].as_string()=='ghost_persistence'))
    roll=and_(k=='roll',d['completed'].as_boolean().is_not(True),or_(ui.related_living_sql(save),post_death),when(Record.global_day))
    hidden=select(Record.id).where(Record.save_id==save.id,Record.kind=='event',or_(Record.deleted.is_(True),Record.data['ignored'].as_boolean().is_(True),Record.data['hidden'].as_boolean().is_(True))).correlate(None)
    roll=and_(roll,or_(d['event_id'].as_string().is_(None),d['event_id'].as_string().notin_(hidden)))
    decision=or_(roll,and_(heritage.due_tasks_sql(save),when(day)),
        and_(k=='game_candidate',d['status'].as_string()=='pending',when(Record.global_day)),
        and_(k=='pregnancy',func.lower(func.coalesce(d['status'].as_string(),'active')).in_(['active','pregnant','expecting']),ui.related_living_sql(save,'mother_id'),when(day)),
        and_(k=='sim',d['death_confirmed'].as_boolean().is_not(True),when(d['death_global_day'].as_integer())),
        and_(k=='university_term',d['completed'].as_boolean().is_not(True),func.lower(func.coalesce(d['status'].as_string(),'in progress')).in_(['planned','in progress','active','probation']),ui.related_living_sql(save),when(d['end_global_day'].as_integer())))
    # Ongoing records belong to Today; future/overdue show starts, not repeated history.
    start=func.coalesce(d['start_global_day'].as_integer(),d['onset_global_day'].as_integer(),Record.global_day)
    end=func.coalesce(d['end_global_day'].as_integer(),start)
    happening=or_(and_(k=='event',d['active'].as_boolean().is_not(False),d['hidden'].as_boolean().is_not(True),d['ignored'].as_boolean().is_not(True),and_(start<=g,end>=g) if window=='today' else when(start)),
        and_(k=='illness',ui.related_living_sql(save),and_(start<=g,or_(d['end_global_day'].as_integer().is_(None),end>=g)),func.lower(func.coalesce(d['status'].as_string(),'active')).notin_(['recovered','ended','dismissed','deceased','resolved','complete','fatal'])) if window=='today' else and_(k=='illness',ui.related_living_sql(save),when(start)),
        and_(k=='sim',ui.living_sql(save),when(d['birth_global_day'].as_integer())),
        and_(k=='sim',d['death_confirmed'].as_boolean().is_(True),when(d['death_global_day'].as_integer())),
        and_(k=='relationship',when(func.coalesce(d['marriage_global_day'].as_integer(),d['start_global_day'].as_integer(),Record.global_day))))
    complete=or_(and_(k=='task',d['feature'].as_string().in_([heritage.PREFIX+x for x in ('routine_task','thread','commitment')]),d['completed'].as_boolean().is_(True),when(d['completed_global_day'].as_integer())),and_(k=='roll',d['completed'].as_boolean().is_(True),when(d['completed_global_day'].as_integer())),
        and_(k=='game_candidate',d['status'].as_string().in_(['accepted','dismissed']),when(func.coalesce(d['accepted_global_day'].as_integer(),d['dismissed_global_day'].as_integer()))),
        and_(k=='pregnancy',func.lower(d['status'].as_string()).in_(['delivered','complete','completed']),when(func.coalesce(d['actual_delivery_global_day'].as_integer(),d['delivery_global_day'].as_integer(),d['end_global_day'].as_integer()))))
    predicates={'decisions':decision,'happening':happening,'completed':complete};groups=[]
    for key in ([section] if section else SECTIONS):
        page=max(1,min(100000,ui.integer(params.get(key+'_page'),1)))
        kinds={'decisions':('roll','game_candidate','pregnancy','sim','university_term','task'),'happening':('event','illness','sim','relationship'),'completed':('roll','game_candidate','pregnancy','task')}
        criteria=[Record.save_id==save.id,Record.kind.in_(kinds[key]),Record.deleted.is_(False),predicates[key]]
        household=str(params.get('household') or '')
        if household and household!='all':criteria.append(clarity.household_predicate(save,household))
        count=session.scalar(select(func.count()).select_from(Record).where(*criteria)) or 0
        pages=max(1,(count+LIMIT-1)//LIMIT);page=min(page,pages)
        rows=list(session.scalars(select(Record).where(*criteria).order_by(Record.global_day,Record.created_at,Record.id).offset((page-1)*LIMIT).limit(LIMIT)))
        ids={str(r.data.get('sim_id') or r.data.get('mother_id') or '') for r in rows}
        by_id={r.id:r for r in session.scalars(select(Record).where(Record.save_id==save.id,Record.id.in_(ids)))} if ids else {}
        panels,consumed=clarity.birth_panels(session,save,rows,key,window)
        def url(number):return '/p/today?'+urlencode({'window':window,'household':household, key+'_page':number})
        groups.append({'id':key,'label':SECTIONS[key] if window=='today' or key!='happening' else 'Starting '+window,'count':count,'rows':rows,'by_id':by_id,'page':page,'pages':pages,'previous':url(page-1),'next':url(page+1)})
        groups[-1].update(birth_panels=panels,rows=[r for r in rows if r.id not in consumed])
    households=list(session.execute(select(Record.id,Record.label).where(Record.save_id==save.id,Record.kind=='household',Record.deleted.is_(False)).order_by(Record.label)))
    history=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='event',Record.deleted.is_(False),
        Record.data['active'].as_boolean().is_not(False),Record.data['ignored'].as_boolean().is_not(True),
        Record.data['hidden'].as_boolean().is_not(True),start<=g,end>=g).order_by(Record.label).limit(24))) if window=='today' else []
    return {'board_groups':groups,'board_window':window,'board_household':str(params.get('household') or ''),'board_households':households,'daily_history':history}

def record_href(row):
    if row.kind=='task' and heritage.feature(row) in {'routine_task','thread','commitment'}:
        return '/p/'+('seasonal-routines' if heritage.feature(row)=='routine_task' else 'story-threads')+'#item-'+row.id
    if row.kind=='sim':return '/sims/'+row.id
    if row.kind=='pregnancy':return '/pregnancies/'+row.id
    if row.kind=='relationship':return '/relationships/'+row.id
    if row.kind=='game_candidate':return '/p/automation?'+urlencode({'q':row.label,'review_quality':'all'})+'#candidate-'+row.id
    return '/p/'+{'event':'events','illness':'illnesses','university_term':'university'}.get(row.kind,'rolls')+'?'+urlencode({'q':row.label})

def render(request,session,ctx,templates):
    ctx.update(board(session,ctx['save'],ctx['ui_query']))
    return templates.TemplateResponse(request,'today_workboard.html',ctx)

def schedule(m,session,save):
    heritage.schedule_routines(session,save)
    marker=(save.global_day,6)
    if domain.automation_enabled(save) and m._TODAY_SCHEDULE_CHECKED.get(save.id)!=marker:
        save.revision+=domain.retire_prechallenge_rolls(session,save)
        domain.schedule_marriage_rolls(session,save)
        save.revision+=domain.schedule_occult_rolls(session,save)
        save.revision+=domain.schedule_event_rolls(session,save)
        save.revision+=domain.schedule_campaign_rolls(session,save)
        m._TODAY_SCHEDULE_CHECKED[save.id]=marker

def register(m):
    m.templates.env.globals.update(date_certainty=ui.date_certainty,review_quality=ui.review_quality,review_changes=ui.review_changes,ui_record_href=record_href,roll_presentation=clarity.roll_presentation)

    @m.app.post('/api/ui/preferences')
    async def save_preferences(request:Request):
        raw=await request.body()
        if len(raw)>20000:raise HTTPException(413,'Preference update is too large.')
        try:body=json.loads(raw)
        except (ValueError,TypeError):raise HTTPException(400,'Invalid preferences.')
        if not isinstance(body,dict):raise HTTPException(400)
        with m.db() as session:
            user=m.signed_in(request,session)
            if not user:raise HTTPException(401)
            memberships=select(Membership.workspace_id).where(Membership.user_id==user.id)
            save=session.scalar(select(ChronicleSave).where(ChronicleSave.id==str(body.get('save_id') or ''),ChronicleSave.workspace_id.in_(memberships)))
            if not save:raise HTTPException(404)
            row=session.scalar(select(UiPreference).where(UiPreference.user_id==user.id).with_for_update())
            if not row:
                try:
                    with session.begin_nested():
                        session.add(UiPreference(user_id=user.id,values={}));session.flush()
                except IntegrityError:
                    pass # Another tab created this user's preference row first.
                row=session.scalar(select(UiPreference).where(UiPreference.user_id==user.id).with_for_update())
            values=dict(row.values or {}) if row else {}
            page=str(body.get('page') or '')[:100]
            if 'filters' in body:
                filters=body['filters']
                if not isinstance(filters,dict) or page not in m.FEATURES:raise HTTPException(400)
                scopes=dict(values.get('pages') or {})
                scopes[save.id+':'+page]={k:([str(item)[:200] for item in v[:50] if isinstance(item,(str,int,bool))] if isinstance(v,list) else str(v)[:200]) for k,v in filters.items() if k in ui.QUERY_KEYS and isinstance(v,(str,int,bool,list))}
                values['pages']=dict(list(scopes.items())[-150:])
            for key,allowed in [('density',{'comfortable','compact'}),('thumbnail',{'small','medium','large'})]:
                if key in body:
                    if body[key] not in allowed:raise HTTPException(400)
                    values[key]=body[key]
            if 'detail' in body:
                detail=body['detail']
                if not isinstance(detail,dict) or not isinstance(detail.get('open'),bool):raise HTTPException(400)
                entries=dict(values.get('details') or {});entries[save.id+':'+str(detail.get('key',''))[:200]]=detail['open'];values['details']=dict(list(entries.items())[-400:])
            if 'tab' in body:
                if body['tab'] not in {'overview','family','health','education','occult','game-record','chronicle','portraits','edit-sim'}:raise HTTPException(400)
                values['profile_tab']=body['tab']
            if 'paused' in body:
                if not isinstance(body['paused'],bool):raise HTTPException(400)
                link=session.scalar(select(ClockLink).where(ClockLink.save_id==save.id))
                pauses=dict(values.get('paused_clocks') or {})
                if body['paused'] and link:pauses[save.id]=[link.last_game_day,link.last_game_hour,link.last_game_minute]
                else:pauses.pop(save.id,None)
                values['paused_clocks']=pauses
            if len(json.dumps(values))>64000:raise HTTPException(400,'Too many saved preferences. Reset older filters first.')
            row.values=values
        return {'ok':True}

    @m.app.get('/api/ui/today/{section}')
    def today_section(request:Request,section:str):
        if section not in SECTIONS:raise HTTPException(404)
        with m.db() as session:
            ctx=m.context(request,session,page='today')
            if not ctx['user'] or not ctx['save']:raise HTTPException(401)
            if request.headers.get('X-UI-Save') and request.headers['X-UI-Save']!=ctx['save'].id:raise HTTPException(409,'The open save changed.')
            if section=='decisions':schedule(m,session,ctx['save'])
            ctx.update(board(session,ctx['save'],ctx['ui_query'],section))
            ctx['group']=ctx['board_groups'][0]
            return m.templates.TemplateResponse(request,'_today_section.html',ctx)

    @m.app.get('/api/ui/rolls/{roll_id}')
    def roll_card(request:Request,roll_id:str):
        with m.db() as session:
            row=session.get(Record,roll_id)
            if not row or row.kind!='roll' or row.deleted:raise HTTPException(404)
            save=m.owned_save(request,session,row.save_id)
            person=session.get(Record,row.data.get('sim_id')) if row.data.get('sim_id') else None
            return m.templates.TemplateResponse(request,'_single_roll.html',{'request':request,'item':row,'save':save,'person':person if person and person.save_id==save.id else None})

    @m.app.get('/api/ui/rolls/{roll_id}/followups')
    def followups(request:Request,roll_id:str):
        with m.db() as session:
            row=session.get(Record,roll_id)
            if not row or row.kind!='roll' or row.deleted:raise HTTPException(404)
            m.owned_save(request,session,row.save_id)
            children=list(session.scalars(select(Record).where(Record.save_id==row.save_id,Record.kind=='roll',Record.deleted.is_(False),Record.data['origin_roll_id'].as_string()==row.id).order_by(Record.global_day).limit(100)))
            return {'items':[{'label':r.label,'day':r.global_day,'outcome':r.data.get('outcome'),'completed':bool(r.data.get('completed')),'href':'/p/rolls?'+urlencode({'q':r.label,'living':'all'})} for r in children], 'note':'Scheduled follow-ups only; later choices or new game reports can create more.'}
