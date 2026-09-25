"""Annual marriage controls; ordinary authenticated save/record mutations."""
from uuid import uuid4
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from . import marriage_rules as rules, domain
from .models import ChronicleSave, Record


def context(session,save,request):
    records=rules.rows(session,save)
    people=sorted([r for r in records if r.kind=='sim' and rules.living(r,save)],key=lambda r:r.label.casefold())
    selected=next((p for p in people if p.id==request.query_params.get('marriage_sim')),people[0] if people else None)
    year=rules.year_at(save);decisions=[]
    for roll in records:
        if roll.kind!='roll' or not rules.is_active(roll) or not roll.data.get('annual_marriage') or not roll.data.get('completed'):continue
        if roll.data.get('marriage_decision') not in rules.SUCCESS or rules.integer(roll.data.get('planner_year'))!=year:continue
        sim=next((p for p in people if p.id==roll.data.get('sim_id')),None)
        if not sim or rules.eligibility(sim,save,records):continue
        decisions.append({'roll':roll,'sim':sim,'candidates':rules.candidates(sim,save,records)})
    return {'enabled':rules.enabled(save),'year':year,'eras':rules.ERAS,'people':people,'selected':selected,
            'reason':rules.eligibility(selected,save,records) if selected else '',
            'policies':(save.settings or {}).get('annual_marriage_policies') or [],'decisions':decisions,
            'notice':request.session.pop('annual_marriage_notice',None)}


def text(form,key,limit=160):
    value=str(form.get(key) or '').strip()
    if len(value)>limit:raise ValueError('That field is too long: '+key)
    return value


def number(form,key,low,high,default=None):
    value=text(form,key)
    if not value:return default
    parsed=rules.integer(value)
    if parsed is None or not low<=parsed<=high:raise ValueError(f'{key.replace("_"," ")} must be between {low} and {high}.')
    return parsed


def register(m):
    async def perform(request,action,record_id=''):
        form=await request.form()
        with m.db() as session:
            ctx=m.context(request,session);save=ctx.get('save')
            if not save:raise HTTPException(400,'Choose a save first.')
            if form.get('marriage_save_id')!=save.id:raise HTTPException(409,'The active save changed. Reopen Relationships.')
            # Serialize duplicate clicks and refresh the record before validation.
            session.execute(update(ChronicleSave).where(ChronicleSave.id==save.id).values(revision=ChronicleSave.revision))
            session.refresh(save)
            row=session.get(Record,record_id) if record_id else None
            if record_id and (not row or row.save_id!=save.id or not rules.is_active(row)):
                raise HTTPException(404,'This record is not available in the active save.')
            if row and rules.integer(form.get('record_version'))!=row.version:
                raise HTTPException(409,'This record changed. Reload Relationships before trying again.')
            try:
                if action=='profile':
                    if row.kind!='sim':raise ValueError('Choose a Sim.')
                    permission=text(form,'marriage_eligibility')
                    if permission not in {'auto','blocked','event'}:raise ValueError('Choose an eligibility setting.')
                    reason=text(form,'marriage_practical_reason',500)
                    extended=form.get('marriage_practical_extension')=='yes'
                    if extended and not reason:raise ValueError('Record the dependent children, farm, business, title or inheritance supporting this extension.')
                    rules.change(session,row,{'marriage_eligibility':permission,
                        'marriage_available_from_year':number(form,'marriage_available_from_year',-9999,9999),
                        'marriage_event_until_year':number(form,'marriage_event_until_year',-9999,9999),
                        'marriage_practical_extension':extended,'marriage_practical_reason':reason,
                        'marriage_custom':text(form,'marriage_custom')})
                    save.revision+=1;domain.schedule_marriage_rolls(session,save)
                    request.session['annual_marriage_notice']='Marriage eligibility and practical-remarriage preference saved.'
                elif action=='policy':
                    policy={'id':uuid4().hex,'label':text(form,'label') or 'Local marriage custom',
                        **{k:text(form,k) for k in ('region','class','faith','custom')},
                        'from_year':number(form,'from_year',-9999,9999,-9999),
                        'until_year':number(form,'until_year',-9999,9999,9999),
                        'min_age':number(form,'min_age',13,120,18),'permission':text(form,'permission')}
                    if policy['from_year']>policy['until_year']:raise ValueError('The end year must not precede the start year.')
                    if policy['permission'] not in {'allowed','blocked'}:raise ValueError('Choose allowed or blocked.')
                    policies=list(save.settings.get('annual_marriage_policies') or [])
                    if len(policies)>=100:raise ValueError('Remove an unused eligibility rule before adding more.')
                    save.settings={**save.settings,'annual_marriage_policies':policies+[policy]};save.revision+=1
                    domain.schedule_marriage_rolls(session,save)
                    request.session['annual_marriage_notice']='Local eligibility rule added. Completed results were preserved.'
                elif action=='remove-policy':
                    policy_id=text(form,'policy_id');policies=list(save.settings.get('annual_marriage_policies') or [])
                    save.settings={**save.settings,'annual_marriage_policies':[p for p in policies if p.get('id')!=policy_id]};save.revision+=1
                    domain.schedule_marriage_rolls(session,save)
                elif action in {'refusal','match'}:
                    if row.kind!='roll' or not row.data.get('annual_marriage'):raise ValueError('Choose a yearly marriage roll.')
                    if action=='refusal':
                        child=rules.request_refusal(session,save,row)
                        return RedirectResponse('/p/today?task=rolls#roll-'+child.id,303)
                    rel=rules.plan_match(session,save,row,text(form,'spouse_id'))
                    request.session['annual_marriage_notice']='Betrothal saved with a wedding date in this calendar year. Record the marriage after playing it.'
                else:raise ValueError('Unknown marriage action.')
            except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        query=('?marriage_sim='+record_id) if action=='profile' else ''
        return RedirectResponse('/p/relationships'+query+'#annual-marriage',303)

    @m.app.post('/api/marriage-rules/policy')
    async def policy(request:Request):return await perform(request,'policy')
    @m.app.post('/api/marriage-rules/remove-policy')
    async def remove_policy(request:Request):return await perform(request,'remove-policy')
    @m.app.post('/api/marriage-rules/sims/{record_id}')
    async def profile(record_id:str,request:Request):return await perform(request,'profile',record_id)
    @m.app.post('/api/marriage-rules/rolls/{record_id}/refusal')
    async def refusal(record_id:str,request:Request):return await perform(request,'refusal',record_id)
    @m.app.post('/api/marriage-rules/rolls/{record_id}/match')
    async def match(record_id:str,request:Request):return await perform(request,'match',record_id)
