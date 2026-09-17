"""Compact dynasty ledger UI; all mutations use owned-save and epoch guards."""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from . import dynasty_history as h, dynasty_tools as t, infinite_dynasty as d

SECTIONS={'world','checks','visits','deliveries','parked','relations','heirlooms','decade','journey'}


def register(app,db,owned_save):
    router=APIRouter()

    @router.get('/infinite/{save_id}/history')
    def history(request:Request,save_id:str):
        from . import main as m
        with db() as session:
            save=owned_save(request,session,save_id)
            if not d.state(save):return RedirectResponse('/p/infinite-decades',303)
            section=str(request.query_params.get('section') or 'world')
            if section not in SECTIONS:raise HTTPException(404)
            family=d.branches(session,save);active=d.active_branch(session,save);views=None
            rows=h.logs(session,save);visible=[r for r in rows if t.integer(r.data.get('day'),r.global_day or 1)<=h.cutoff(save)]
            people=sorted((r for r in t.records(session,save,{'sim'}) if not r.deleted or r.data.get('infinite_frozen')),key=lambda r:r.label.casefold())
            branch_names={br.id:br.label for br in family}
            ctx=m.context(request,session,page='infinite-decades',title='Dynasty History',section=section,
                family=family,branch_names=branch_names,branch_meta=d.metadata,active_branch=active,
                people=people,homes=[r for r in t.records(session,save,{'household'}) if not r.deleted],
                transfer_people=[r for r in people if not r.deleted and d.alive(r,save)],
                history_notice=request.session.pop('infinite_notice',None),spoiler_free=h.spoiler_free(save),branch_drama=d.state(save).get('branch_drama',False))
            if section in {'checks','decade','journey'}:views=h.points(session,save)
            if section=='world':
                ctx.update(world_rows=sorted((r for r in visible if r.data.get('feature')=='world_decision'),key=lambda r:r.data['day'],reverse=True),
                    events=sorted((r for r in t.records(session,save,{'event'}) if (not r.deleted or r.data.get('infinite_frozen')) and (h.event_day(r) or 1)<=save.global_day),key=lambda r:r.label.casefold()))
            elif section=='checks':ctx['conflicts']=h.contradictions(session,save,views)
            elif section=='visits':ctx['visits']=[r for r in visible if r.data.get('feature')=='visit']
            elif section=='deliveries':ctx['deliveries']=sorted((r for r in visible if r.data.get('feature')=='parcel'),key=lambda r:(r.data['status']!='pending',r.data['day']))
            elif section=='relations':ctx['relations']=sorted((r for r in visible if r.data.get('feature')=='branch_relation'),key=lambda r:r.data['day'],reverse=True)
            elif section=='heirlooms':
                albums=[]
                for row in rows:
                    if row.data.get('feature')!='heirloom_history':continue
                    chain=[entry for entry in row.data['history'] if entry['day']<=h.cutoff(save)]
                    if chain:albums.append({'record':row,'history':chain,'owner':chain[-1]})
                ctx.update(heirlooms=albums,tracked_heirlooms=[r for r in t.records(session,save,{'heirloom'}) if not r.deleted or r.data.get('infinite_frozen')])
            elif section=='decade':
                year=t.integer(request.query_params.get('year'),d.year(save)//10*10)
                if not -9999<=year<=9999:raise HTTPException(400,'Choose a valid year.')
                if h.spoiler_free(save) and year>d.year(save):year=d.year(save)
                ctx.update(check_year=year,decade_checks=h.checklist(session,save,year,views))
            elif section=='journey':
                sid=str(request.query_params.get('sim_id') or (people[0].id if people else ''))
                try:journey=h.journey(session,save,sid,views) if sid else []
                except ValueError as exc:raise HTTPException(404,str(exc)) from exc
                ctx.update(journey=journey,journey_sim=sid)
            return m.templates.TemplateResponse(request,'dynasty_history.html',ctx)

    @router.post('/infinite/{save_id}/history/preferences')
    async def preferences(request:Request,save_id:str):
        form=await request.form()
        with db() as session:
            save=owned_save(request,session,save_id);d._lock(session,save)
            if not d.state(save):raise HTTPException(409,'Enable Infinite Decades first.')
            with d.branch_operation(session,save):
                d._set_state(save,spoiler_free=form.get('spoiler_free')=='yes',branch_drama=form.get('branch_drama')=='yes')
            request.session['infinite_notice']='History visibility and optional drama prompts saved.'
        return RedirectResponse(f'/infinite/{save_id}/history',303)

    app.include_router(router)
