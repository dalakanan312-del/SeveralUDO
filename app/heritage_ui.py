"""Authenticated household-history pages; no game-file writes or new sync kinds."""
import base64
import copy
import hashlib
import json
from datetime import datetime,timezone,timedelta
from uuid import uuid4
from fastapi import Request,HTTPException
from fastapi.responses import RedirectResponse,HTMLResponse
from sqlalchemy import select,update,or_,and_,func
from sqlalchemy.exc import IntegrityError
from . import heritage as h,domain,infinite_decades,usability,names,exports,drama,advanced
from .models import Record,ChronicleSave,ActionPreview,Portrait

PAGES={
 'historical-addresses':('Historical Address Book','Properties, former residents and recorded occupancy'),
 'titles-estates':('Titles & Estates','Title holders, succession claims and associated properties'),
 'naming-customs':('Naming Customs','Family names, patronymics and configurable era conventions'),
 'seasonal-routines':('Seasonal Routines','Optional recurring household tasks'),
 'story-threads':('Story Board & Promises','Unresolved stories, next steps and character commitments'),
 'catch-up':('Historical Catch-up','Review past obligations without inventing dice results'),
 'family-chronicle':('Share a Family Chronicle','Preview a private, selective, portable family history'),
}
def field(key,label,kind='text',choices=(),required=False,default=''):
    return dict(key=key,label=label,kind=kind,choices=choices,required=required,default=default)
def status(choices,default=None):return field('status','Status','select',choices,True,default or choices[0])
SCHEMAS={
 'property':('historical-addresses','Property',[
  field('address','Address / estate / lot name',required=True),field('country','Country / region'),field('world','Game world'),
  status(('Active','Sold','Destroyed','Archived'))]),
 'residence':('historical-addresses','Residence or ownership period',[
  field('property_id','Property','property',required=True),field('sim_id','Sim','sim'),field('household_id','Household','household'),
  field('role','Role','select',('Resident','Owner','Tenant','Guest'),True,'Resident'),field('from_global_day','From Global Day','number',required=True),
  field('until_global_day','Through Global Day (inclusive; blank = ongoing)','number'),status(('Active','Ended','Archived'))]),
 'title':('titles-estates','Title / estate dignity',[
  field('holder_id','Current holder','sim'),field('held_from_global_day','Holder took title on GD','number',required=True),
  field('property_ids','Associated properties','properties'),field('succession_custom','Succession custom / eligibility','textarea'),
  field('transfer_reason','Reason for latest transfer'),status(('Active','Vacant','Extinct','Archived'))]),
 'claim':('titles-estates','Succession position / claim',[
  field('title_id','Title','title',required=True),field('sim_id','Claimant','sim',required=True),field('rank','Succession position (1 = first)','number',required=True,default=1),
  status(('Recognized','Contested','Withdrawn','Rejected'))]),
 'custom':('naming-customs','Naming custom',[
  field('culture','Given-name source','culture',required=True),field('surname_culture','Regional surname source','culture'),
  field('given_mode','Given name','select',('Source pool','Relative'),True,'Source pool'),
  field('surname_mode','Surname convention','select',('Regional pool','Family surname','Patronymic','None'),True,'Regional pool'),
  field('patronymic_pattern','Patronymic pattern; {parent} is replaced',default='son of {parent}'),
  field('style_prefix','Optional noble / formal prefix'),field('style_suffix','Optional formal suffix'),
  field('from_year','First historical year (optional)','number'),field('until_year','Last historical year (optional)','number'),
  field('source','Source / custom explanation'),status(('Active','Archived'))]),
 'routine':('seasonal-routines','Seasonal routine',[
  field('household_id','Household','household',required=True),field('season','Calendar quarter','select',h.SEASONS,True,'Spring'),
  field('instructions','What to do in game','textarea',required=True),field('from_year','First year','number',required=True),
  field('until_year','Last year (optional)','number'),status(('Active','Paused','Archived'))]),
 'routine_task':('seasonal-routines','Scheduled household task',[
  field('next_step','In-game instructions','textarea'),status(('Open','Completed','Waived')),field('outcome_notes','What actually happened','textarea')]),
 'thread':('story-threads','Unresolved story',[
  field('household_id','Household','household'),field('sim_id','Main Sim','sim'),
  field('priority','Priority','select',('High','Normal','Low'),True,'Normal'),field('next_step','Next step','textarea',required=True),
  field('due_global_day','Optional deadline GD','number'),status(('Open','Paused','Resolved','Archived')),
  field('outcome_notes','Resolution / actual outcome','textarea')]),
 'commitment':('story-threads','Vow / promise / loyalty',[
  field('sim_id','Who made the commitment','sim',required=True),field('promisee_id','Made to','sim'),field('household_id','Household','household'),
  field('witness_ids','Who knows / witnessed it','sims'),field('next_step','The promise and what fulfills it','textarea',required=True),
  field('due_global_day','Optional deadline GD','number'),status(('Active','Kept','Broken','Released','Archived')),
  field('outcome_notes','Fulfillment, breach or release details','textarea')]),
}

def active_ref(row):return not row.deleted and not row.data.get('infinite_frozen')

def guard_scene(request,save):
    marker=request.session.get('heritage_scene')
    if not marker or not request.url.path.startswith('/drama/') or request.method=='GET':return
    if request.url.path in {'/drama/draw','/drama/discard'}:
        request.session.pop('heritage_scene',None);return
    if marker.get('save_id')!=save.id or marker.get('epoch','')!=infinite_decades.state(save).get('epoch',''):
        raise HTTPException(409,'This promise scene belongs to another save or branch. Start a new scene.')

def scene_context(request,session,save):
    marker=request.session.get('heritage_scene') or {}
    if marker.get('save_id')!=save.id or marker.get('epoch','')!=infinite_decades.state(save).get('epoch',''):return None
    if (request.session.get('drama_state') or {}).get('card_id')!='common-promise-returned':return None
    row=session.get(Record,marker.get('record_id'))
    if not row or row.save_id!=save.id or not active_ref(row) or row.version!=marker.get('version'):
        if request.method=='POST':raise HTTPException(409,'The source commitment changed. Start a fresh promise scene.')
        return None
    return {'id':row.id,'label':row.label,'promise':row.data.get('next_step',''),'breach':row.data.get('outcome_notes',''),'private':row.data.get('private',True)}
def plain(form,key,limit=4000):
    value=str(form.get(key) or '').strip()
    if len(value)>limit:raise HTTPException(400,'That field is too long: '+key)
    return value

def choices_for(key,people,homes,rows,cultures):
    if key=='culture':return [(x,x) for x in cultures]
    if key in {'sim','sims'}:return [(x.id,x.label) for x in people]
    if key=='household':return [(x.id,x.label) for x in homes]
    target='property' if key in {'property','properties'} else 'title'
    return [(r.id,r.label+(' (archived)' if r.data.get('status')=='Archived' else '')) for r in rows if h.feature(r)==target]

def render(request,session,ctx,templates):
    save=ctx.get('save')
    if not save:return RedirectResponse('/p/saves',303)
    rows=h.rows_for(session,save);people=usability.people_picker(session,save)
    homes=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='household',Record.deleted.is_(False))))
    cultures=names.library_names(session,save.id) if ctx['page']=='naming-customs' else []
    by_id={r.id:r for r in [*rows,*people,*homes]}
    page=ctx['page'];schemas={k:v for k,v in SCHEMAS.items() if v[0]==page}
    ctx.update(heritage_rows=rows,heritage_schemas=schemas,heritage_people=people,heritage_homes=homes,
               heritage_names={key:value.label for key,value in by_id.items()},heritage_notice=request.session.pop('heritage_notice',None),
               heritage_choices={kind:choices_for(kind,people,homes,rows,cultures) for kind in ('sim','sims','household','property','properties','title','culture')},
               heritage_nonce=uuid4().hex,heritage_form_ids={r.id:uuid4().hex for r in rows},
               heritage_year=save.start_year+(save.global_day-1)//save.days_per_year,
               heritage_groups={kind:sorted([r for r in rows if h.feature(r)==kind],key=lambda r:(r.data.get('status') in h.CLOSED,r.label.casefold())) for kind in schemas})
    if page=='historical-addresses':
        property_id=request.query_params.get('property','');prop=by_id.get(property_id)
        if prop and h.feature(prop)=='property':
            sim_ids=[r.data.get('sim_id') for r in rows if h.feature(r)=='residence' and r.data.get('property_id')==prop.id]
            events=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.deleted.is_(False),Record.kind.in_(['death','migration','event_result','story_entry']),
                or_(Record.data['property_id'].as_string()==prop.id,Record.data['sim_id'].as_string().in_(sim_ids))).order_by(Record.global_day.desc()).limit(100)))
            ctx.update(property_detail=prop,property_history=h.property_history(prop,rows,people,events,save))
    if page=='naming-customs' and request.query_params.get('generate'):
        custom=by_id.get(request.query_params.get('custom_id'))
        if not custom or h.feature(custom)!='custom' or custom.data.get('status')!='Active':raise HTTPException(400,'Choose an active naming custom.')
        try:ctx['heritage_suggestions']=h.custom_names(session,save,custom,people,request.query_params)
        except ValueError as e:ctx['heritage_notice']=str(e)
    if page=='seasonal-routines':
        ctx['routine_schedule']={r.id:h.season_day(save,ctx['heritage_year'],r.data.get('season','Spring')) for r in rows if h.feature(r)=='routine'}
    if page=='catch-up':
        cutoff=max(0,min(save.global_day-1,h.integer(request.query_params.get('through'),save.global_day-1)))
        d=Record.data;due=func.coalesce(d['due_global_day'].as_integer(),Record.global_day)
        criteria=[Record.save_id==save.id,Record.deleted.is_(False),d['infinite_frozen'].as_boolean().is_not(True),d['completed'].as_boolean().is_not(True),due>=1,due<=cutoff,
            or_(Record.kind=='roll',and_(Record.kind=='task',d['feature'].as_string().in_([h.PREFIX+x for x in ('routine_task','thread','commitment')]),d['status'].as_string().in_(['Open','Active'])))]
        household=request.query_params.get('household','');members={p.id for p in people if p.data.get('current_household_id')==household}
        if household:criteria.append(or_(d['household_id'].as_string()==household,d['sim_id'].as_string().in_(members),d['mother_id'].as_string().in_(members)))
        count=session.scalar(select(func.count()).select_from(Record).where(*criteria)) or 0
        page_number=max(1,min(max(1,(count+49)//50),h.integer(request.query_params.get('batch'),1)))
        candidates=list(session.scalars(select(Record).where(*criteria).order_by(due,Record.id).offset((page_number-1)*50).limit(50)))
        ctx.update(catchup_rows=candidates,catchup_count=count,catchup_page=page_number,catchup_cutoff=cutoff,catchup_household=household)
        return templates.TemplateResponse(request,'heritage_catchup.html',ctx)
    if page=='family-chronicle':
        ctx['chronicle_facts']=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.deleted.is_(False),Record.kind.in_(['event_result','story_entry','drama_scene','migration']),Record.global_day<=save.global_day).order_by(Record.global_day.desc()).limit(150)))
        return templates.TemplateResponse(request,'heritage_export_options.html',ctx)
    return templates.TemplateResponse(request,'heritage.html',ctx)

def register(m):
    m.templates.env.globals.update(heritage_feature=h.feature)
    def current(request,session,form):
        ctx=m.context(request,session);save=ctx.get('save')
        if not ctx.get('user'):raise HTTPException(401)
        if not save or save.id!=plain(form,'save_id',64):raise HTTPException(409,'The active save changed. Refresh first.')
        if session.get_bind().dialect.name=='sqlite':session.execute(update(ChronicleSave).where(ChronicleSave.id==save.id).values(revision=ChronicleSave.revision))
        session.scalar(select(ChronicleSave).where(ChronicleSave.id==save.id).with_for_update().execution_options(populate_existing=True))
        infinite_decades.guard_request(request,save)
        if infinite_decades.frozen(save):raise HTTPException(409,'This family branch is read-only.')
        return ctx,save
    def owned(session,save,rid,kind=None):
        row=session.get(Record,rid) if rid else None
        if not row or row.save_id!=save.id or not active_ref(row) or (kind and row.kind!=kind):raise HTTPException(400,'Choose an available record from this save.')
        return row
    def check_ref(session,save,value,kind):
        if not value:return
        expected={'sim':'sim','sims':'sim','household':'household'}.get(kind,'note')
        row=owned(session,save,value,expected)
        if expected=='note' and h.feature(row)!=('property' if kind in {'property','properties'} else 'title'):raise HTTPException(400,'The linked record has the wrong type.')
    def journal_outcome(session,save,row,old):
        if h.feature(row) not in {'thread','commitment','routine_task'}:return
        if old and old.get('private',True)!=row.data.get('private',True):
            linked=session.scalars(select(Record).where(Record.save_id==save.id,Record.deleted.is_(False),Record.kind.in_(['story_entry','drama_scene']),
                or_(Record.data['source_record_id'].as_string()==row.id,Record.data['commitment_record_id'].as_string()==row.id)))
            for entry in linked:
                base=entry.version;entry.data={**entry.data,'private':row.data.get('private',True)};entry.version+=1
                domain.journal(session,entry,'upsert',base);save.revision+=1
        status=row.data.get('status')
        correction=status==old.get('status') and row.data.get('outcome_notes')!=old.get('outcome_notes')
        if status not in h.CLOSED or status=='Archived' or (status==old.get('status') and not correction):return
        text=('Player correction: ' if correction else '')+f'{row.label} — {status}. '+(row.data.get('outcome_notes') or 'Recorded by the player; no in-game change was made automatically.')
        record=Record(id=h.record_id(save.id,f'outcome:{row.id}:{row.version}'),save_id=save.id,kind='story_entry',label=row.label+' — '+status+(' (corrected)' if correction else ''),global_day=save.global_day,
            data={'feature':'heritage_journal','source_record_id':row.id,'sim_id':row.data.get('sim_id'),'household_id':row.data.get('household_id'),
                  'body':text,'private':row.data.get('private',True),'source':'Player-confirmed household history'})
        session.add(record);session.flush();domain.journal(session,record,'upsert',0);save.revision+=1

    @m.app.post('/heritage/{feature}/save')
    async def save_record(request:Request,feature:str):
        if feature not in SCHEMAS:raise HTTPException(404)
        form=await request.form()
        with m.db() as session:
            ctx,save=current(request,session,form);existing=plain(form,'record_id',64)
            nonce=plain(form,'form_id',32)
            if len(nonce)!=32 or any(c not in '0123456789abcdef' for c in nonce):raise HTTPException(400,'Refresh this form before saving.')
            kind='task' if feature in {'routine','routine_task','thread','commitment'} else 'note'
            rid=existing or h.record_id(save.id,feature+':'+nonce);row=session.get(Record,rid)
            if existing:
                row=owned(session,save,existing,kind)
                if h.feature(row)!=feature:raise HTTPException(404)
                if row.version!=h.integer(form.get('version')):raise HTTPException(409,'This record changed. Refresh before saving.')
            elif row:return RedirectResponse('/p/'+SCHEMAS[feature][0]+'#item-'+row.id,303)
            elif feature=='routine_task':raise HTTPException(400,'Routine tasks are created from enabled routines.')
            label=plain(form,'label',200)
            if not label:raise HTTPException(400,'Give the record a name.')
            old=copy.deepcopy(row.data) if row else {};data={}
            for f in SCHEMAS[feature][2]:
                key=f['key'];value=plain(form,key)
                if f['required'] and not value:raise HTTPException(400,'Complete '+f['label'])
                if f['kind']=='number':
                    value=h.integer(value)
                    if value is None and (f['required'] or plain(form,key)):raise HTTPException(400,'Enter a whole number for '+f['label'])
                    if value is not None and not -100000<=value<=2000000:raise HTTPException(400,'Date / number is outside the supported range.')
                elif f['kind']=='select' and value not in f['choices']:raise HTTPException(400,'Choose a supported '+f['label'])
                elif f['kind'] in {'sim','household','property','title'}:check_ref(session,save,value,f['kind'])
                elif f['kind'] in {'sims','properties'}:
                    value=list(dict.fromkeys(form.getlist(key)))
                    if len(value)>100:raise HTTPException(400,'Select at most 100 linked records.')
                    for rid in value:check_ref(session,save,rid,f['kind'])
                elif f['kind']=='culture' and value and value not in names.library_names(session,save.id):raise HTTPException(400,'Choose a source from the name library.')
                data[key]=value
            data.update(feature=h.PREFIX+feature,notes=plain(form,'notes'),private=form.get('private')=='on')
            for a,b in [('from_global_day','until_global_day'),('from_year','until_year')]:
                if data.get(a) is not None and data.get(b) is not None and data[b]<data[a]:raise HTTPException(400,'The end cannot be before the start.')
            if feature=='residence':
                if not data.get('sim_id') and not data.get('household_id'):raise HTTPException(400,'Choose a Sim or household for this period.')
                if data['from_global_day']>save.global_day or (data.get('until_global_day') is not None and data['until_global_day']>save.global_day):raise HTTPException(400,'Residence history must not be in the future. Record planned moves in the migration planner.')
                if data['status']=='Ended' and data.get('until_global_day') is None:raise HTTPException(400,'An ended residence needs an end day.')
            if feature=='claim' and data['rank']<1:raise HTTPException(400,'Succession positions start at 1.')
            if feature=='title':
                start=data['held_from_global_day'];history=list(old.get('tenures') or [])
                if start>save.global_day:raise HTTPException(400,'A confirmed title transfer cannot be in the future. Record a claim instead.')
                if old.get('holder_id')!=data.get('holder_id') and old.get('holder_id'):
                    if start<=h.integer(old.get('held_from_global_day'),start):raise HTTPException(400,'The new tenure must start after the previous tenure began.')
                    history.append({'holder_id':old['holder_id'],'from_global_day':old.get('held_from_global_day'),'until_global_day':start-1,'reason':data.get('transfer_reason','')})
                if data['status']=='Vacant' and data.get('holder_id'):raise HTTPException(400,'Clear the holder to mark a title vacant.')
                data['tenures']=history
            if feature=='custom' and data['surname_mode']=='Patronymic' and (data['patronymic_pattern'].count('{parent}')!=1 or '{' in data['patronymic_pattern'].replace('{parent}','') or '}' in data['patronymic_pattern'].replace('{parent}','')):raise HTTPException(400,'Use exactly one {parent} placeholder in the patronymic pattern.')
            if feature=='routine':
                data['enabled_from_global_day']=save.global_day if not old or old.get('status')!='Active' else old.get('enabled_from_global_day',save.global_day)
            if feature in {'routine_task','thread','commitment'}:
                data['completed']=data['status'] in h.CLOSED
                if data['completed'] and data['status']!=old.get('status'):data['completed_global_day']=save.global_day
                if data['status'] in {'Resolved','Kept','Broken','Released'} and not data.get('outcome_notes'):raise HTTPException(400,'Record what happened before closing this story or commitment.')
            if not row:
                row=Record(id=h.record_id(save.id,feature+':'+nonce),save_id=save.id,kind=kind,label=label,global_day=save.global_day,data={},version=0);session.add(row)
            base=row.version;row.label=label;row.data={**old,**data};row.version+=1
            session.flush();domain.journal(session,row,'upsert',base);save.revision+=1;journal_outcome(session,save,row,old)
            request.session['heritage_notice']='Saved. This records tracker history; it does not change the game.'
            return RedirectResponse('/p/'+SCHEMAS[feature][0]+'#item-'+row.id,303)

    @m.app.post('/heritage/routines/generate')
    async def generate_routines(request:Request):
        form=await request.form()
        with m.db() as session:
            ctx,save=current(request,session,form);count=h.schedule_routines(session,save)
            request.session['heritage_notice']=f'{count} due seasonal task(s) added or corrected. Existing completed tasks are kept; app-wide automation pause is respected.'
            return RedirectResponse('/p/seasonal-routines',303)

    @m.app.post('/heritage/commitment/{record_id}/scene')
    async def promise_scene(request:Request,record_id:str):
        form=await request.form()
        with m.db() as session:
            ctx,save=current(request,session,form);row=owned(session,save,record_id,'task')
            if h.feature(row)!='commitment' or row.data.get('status')!='Broken':raise HTTPException(400,'Choose a recorded broken commitment.')
            actor=owned(session,save,row.data.get('sim_id'),'sim')
            if not advanced.living(actor,save.global_day):raise HTTPException(400,'The person who made this commitment must be living to play their scene.')
            counterpart=row.data.get('promisee_id') or ''
            if counterpart:
                other=owned(session,save,counterpart,'sim')
                if not advanced.living(other,save.global_day):counterpart=''
            request.session['drama_state']=drama.draw_state(save,'common',actor.id,row.data.get('household_id') or '',counterpart,card_id='common-promise-returned')
            request.session.pop('drama_discovery',None)
            request.session['heritage_scene']={'save_id':save.id,'epoch':infinite_decades.state(save).get('epoch',''),'record_id':row.id,'version':row.version}
            request.session['drama_notice']='A promise-themed scene is ready. The recorded commitment and breach are shown separately from fictional choices.'
            return RedirectResponse('/p/drama',303)

    def preview_ticket(session,ctx,save,pid):
        ticket=session.get(ActionPreview,pid)
        if not ticket or ticket.operation!='heritage-catchup' or ticket.user_id!=ctx['user'].id or ticket.save_id!=save.id:raise HTTPException(404)
        return ticket
    def catchup_context(request,session,ctx,ticket):
        ctx.update(catchup_preview=ticket,catchup_items=ticket.payload['items'])
        return m.templates.TemplateResponse(request,'heritage_catchup_preview.html',ctx)

    @m.app.post('/heritage/catch-up/preview')
    async def preview_catchup(request:Request):
        form=await request.form()
        with m.db() as session:
            ctx,save=current(request,session,form);items=[];cutoff=h.integer(form.get('through'),save.global_day-1)
            reason=plain(form,'reason')
            if not reason:raise HTTPException(400,'Add a reason for the historical catch-up.')
            for key,value in form.items():
                if not key.startswith('resolution_') or value=='keep':continue
                if value not in {'historical','waive'}:raise HTTPException(400,'Choose keep, historical completion or waive.')
                row=owned(session,save,key.removeprefix('resolution_'))
                if not h.catchup_eligible(row,save,cutoff) or row.version!=h.integer(form.get('version_'+row.id)):raise HTTPException(409,'An obligation changed or is not eligible. Refresh the batch.')
                items.append({'id':row.id,'kind':row.kind,'label':row.label,'version':row.version,'day':row.global_day,'before':copy.deepcopy(row.data),'choice':value})
            if not 1<=len(items)<=50:raise HTTPException(400,'Choose between 1 and 50 obligations.')
            payload={'items':items,'reason':reason,'day':save.global_day,'epoch':infinite_decades.state(save).get('epoch','')}
            ticket=ActionPreview(user_id=ctx['user'].id,save_id=save.id,operation='heritage-catchup',fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest(),payload=payload)
            session.add(ticket);session.flush();ctx['page']='catch-up'
            return catchup_context(request,session,ctx,ticket)

    @m.app.post('/heritage/catch-up/{preview_id}/confirm')
    async def confirm_catchup(request:Request,preview_id:str):
        form=await request.form()
        with m.db() as session:
            ctx,save=current(request,session,form);ticket=preview_ticket(session,ctx,save,preview_id)
            if ticket.consumed:raise HTTPException(409,'This catch-up was already applied.')
            created=ticket.created_at.replace(tzinfo=timezone.utc) if ticket.created_at.tzinfo is None else ticket.created_at
            if datetime.now(timezone.utc)-created>timedelta(minutes=20):raise HTTPException(409,'This review expired. Prepare a new preview.')
            plan=ticket.payload
            if plan['day']!=save.global_day or plan['epoch']!=infinite_decades.state(save).get('epoch',''):raise HTTPException(409,'The day or family branch changed. Preview again.')
            rows=[]
            for item in plan['items']:
                row=owned(session,save,item['id'])
                if row.version!=item['version'] or row.data!=item['before'] or row.global_day!=item['day'] or not h.catchup_eligible(row,save,save.global_day-1):raise HTTPException(409,'An obligation changed. Nothing was applied; preview again.')
                rows.append((row,item))
            for row,item in rows:h.apply_catchup(session,save,row,item['choice'],plan['reason'])
            ticket.consumed=True
            request.session['heritage_notice']=f'{len(rows)} past obligations resolved without dice throws or automatic consequences.'
            ctx['page']='catch-up';return catchup_context(request,session,ctx,ticket)

    @m.app.post('/heritage/catch-up/{preview_id}/undo')
    async def undo_catchup(request:Request,preview_id:str):
        form=await request.form()
        with m.db() as session:
            ctx,save=current(request,session,form);ticket=preview_ticket(session,ctx,save,preview_id);plan=ticket.payload
            if not ticket.consumed or plan.get('undone'):raise HTTPException(409,'This batch is not available to undo.')
            if plan['epoch']!=infinite_decades.state(save).get('epoch',''):raise HTTPException(409,'Switch back to the matching family branch first.')
            rows=[]
            for item in plan['items']:
                row=owned(session,save,item['id'])
                if row.version!=item['version']+1:raise HTTPException(409,'An affected record changed; the batch cannot be safely undone.')
                rows.append((row,item))
            for row,item in rows:
                base=row.version;row.data=item['before'];row.version+=1;domain.journal(session,row,'upsert',base);save.revision+=1
            ticket.payload={**plan,'undone':True};request.session['heritage_notice']='Catch-up undone. The obligations are pending again.'
            return RedirectResponse('/p/catch-up',303)

    @m.app.post('/heritage/chronicle')
    async def export_chronicle(request:Request):
        form=await request.form()
        with m.db() as session:
            ctx=m.context(request,session);save=ctx.get('save')
            if not ctx.get('user'):raise HTTPException(401)
            if not save or save.id!=plain(form,'save_id',64):raise HTTPException(409,'The active save changed.')
            ids=set(form.getlist('sim_ids'));fact_ids=set(form.getlist('fact_ids'))
            if not 1<=len(ids)<=300 or len(fact_ids)>150:raise HTTPException(400,'Choose 1–300 Sims and at most 150 facts.')
            people=usability.people_picker(session,save)
            year_only={row.id:row.year_only for row in session.execute(select(Record.id,Record.data['birth_year_only'].label('year_only')).where(Record.save_id==save.id,Record.id.in_(ids)))}
            for person in people:
                if person.id in year_only:person.data['birth_year_only']=year_only[person.id]
            if ids-{p.id for p in people}:raise HTTPException(400,'A selected Sim is not in this save.')
            facts=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.id.in_(fact_ids),Record.deleted.is_(False),Record.kind.in_(['event_result','story_entry','drama_scene','migration']))))
            if fact_ids-{f.id for f in facts}:raise HTTPException(400,'A selected fact is not available in this save.')
            year=save.start_year+(save.global_day-1)//save.days_per_year
            start=h.integer(form.get('from_year'),save.start_year);end=h.integer(form.get('through_year'),year)
            if not -100000<=start<=end<=year or end-start>500:raise HTTPException(400,'Choose up to 501 years ending no later than the current year.')
            options={key:form.get(key)=='on' for key in ('tree','portraits','statistics','narration','private')}
            photos={};total=0
            if options['portraits']:
                for portrait in session.scalars(select(Portrait).where(Portrait.save_id==save.id,Portrait.record_id.in_(ids)).order_by(Portrait.created_at.desc())):
                    if portrait.record_id in photos or portrait.mime_type not in {'image/png','image/jpeg','image/webp'} or len(portrait.image)>2000000:continue
                    total+=len(portrait.image)
                    if total>20000000:break
                    photos[portrait.record_id]='data:'+portrait.mime_type+';base64,'+base64.b64encode(portrait.image).decode('ascii')
            data=h.chronicle_data(save,people,facts,photos,ids,start,end,options)
            html=m.templates.get_template('heritage_chronicle.html').render(chronicle=data)
            headers={'Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"}
            if form.get('download')=='yes':headers['Content-Disposition']=f'attachment; filename="{exports.safe_filename(save.name)}-family-chronicle.html"'
            return HTMLResponse(html,headers=headers)
