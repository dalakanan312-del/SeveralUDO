"""Routes for reviewed dynasty changes; all writes use owned-save and epoch guards."""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from . import dynasty_tools as t, infinite_dynasty as d
from .models import ActionPreview


def context(session,save,request):
    if not save or not d.state(save): return {}
    selected=str(request.query_params.get('resume') or d.state(save).get('active_branch_id') or '')
    try:row=t.branch(session,save,selected)
    except ValueError as exc:raise HTTPException(404,str(exc)) from exc
    logs=t.records(session,save,{t.KIND})
    held=sorted((r for r in logs if r.data.get('feature')=='held_report' and r.data.get('status')=='pending'),key=lambda r:r.created_at)
    audits=sorted((r for r in logs if r.data.get('feature')=='correction'),key=lambda r:r.created_at,reverse=True)
    order=str(request.query_params.get('queue_sort') or request.cookies.get('dynasty_queue_sort') or 'oldest')
    if order not in {'oldest','surname','custom'}: order='oldest'
    return {'branch_queue':t.queue(session,save,order),'queue_sort':order,
            'resume_branch':row,'resume':t.resume_summary(session,save,row),
            'held_branch_reports':held,'dynasty_corrections':audits[:20],
            'split_suggestions':t.split_suggestions(session,save)}


def preview_response(request,session,save,operation,args):
    from . import main as m
    user=m.signed_in(request,session)
    if not user: raise HTTPException(401)
    if request.session.get('save_id')!=save.id: raise HTTPException(409,'The active save changed. Return to Infinite Decades.')
    try: ticket=t.prepare(session,save,user.id,operation,args)
    except ValueError as exc: raise HTTPException(409,str(exc)) from exc
    return m.templates.TemplateResponse(request,'dynasty_preview.html',m.context(request,session,
        page='infinite-decades',dynasty_ticket=ticket,tracker_calendar=d.tracker_calendar(save)))


def register(app,db,owned_save):
    router=APIRouter()

    @router.post('/infinite/{save_id}/tools/preview')
    async def preview(request:Request,save_id:str):
        form=await request.form(); args=dict(form)
        args['sim_ids']=form.getlist('sim_ids')
        if args.get('operation')=='correction' and args.get('field') in {'mother_id','father_id'}:
            args['value']=str(form.get('parent_id') or '')
        with db() as session:
            save=owned_save(request,session,save_id)
            return preview_response(request,session,save,str(form.get('operation') or ''),args)

    @router.post('/infinite/{save_id}/tools/confirm/{token}')
    async def confirm(request:Request,save_id:str,token:str):
        from . import main as m
        form=await request.form()
        try:
            with db() as session:
                save=owned_save(request,session,save_id);user=m.signed_in(request,session)
                if request.session.get('save_id')!=save.id: raise HTTPException(409,'The active save changed.')
                ticket=session.scalar(select(ActionPreview).where(ActionPreview.id==token,ActionPreview.user_id==user.id,ActionPreview.save_id==save.id))
                if not ticket: raise HTTPException(404)
                if ticket.payload.get('kind') in {'switch','undo_switch'}:
                    # Only checkpoint attestations can be added after preview, never a destination or consequence.
                    args={**ticket.payload['args'],**{key:str(form.get(key) or '') for key in ('load_confirmed','checkpoint_confirmed','current_game_save_name')}}
                    ticket.payload={**ticket.payload,'args':args};session.flush()
                t.confirm(session,save,ticket)
                request.session['infinite_notice']='Reviewed dynasty changes applied. Family history and completed roll results are preserved.'
                request.session.pop('today_undo',None);request.session.pop('last_roll',None)
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        return RedirectResponse(f'/infinite/{save_id}/history' if ticket.payload['kind'].startswith('history_') else '/p/infinite-decades',303)

    @router.post('/infinite/{save_id}/tools/preferences')
    async def preferences(request:Request,save_id:str):
        form=await request.form()
        try:
            with db() as session:
                save=owned_save(request,session,save_id)
                t.save_preferences(session,save,str(form.get('branch_id') or ''),form)
                request.session['infinite_notice']='Branch order, handover notes and play-until goal saved.'
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        return RedirectResponse('/p/infinite-decades',303)

    @router.post('/infinite/{save_id}/tools/report/{row_id}')
    async def review_report(request:Request,save_id:str,row_id:str):
        form=await request.form();action=str(form.get('action') or '')
        if action not in {'accept','discard'}: raise HTTPException(400,'Choose accept or discard.')
        try:
            with db() as session:
                save=owned_save(request,session,save_id)
                if action=='accept' and form.get('confirmed')!='yes': raise ValueError('Confirm this is the household you intend to play for this branch.')
                t.review_held(session,save,row_id,action=='accept')
                request.session['infinite_notice']='Report '+('accepted for this branch. Its household is now recognized.' if action=='accept' else 'discarded; it cannot change this branch.')
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        return RedirectResponse('/p/infinite-decades#held-reports',303)

    app.include_router(router)
    from .dynasty_history_ui import register as register_history
    register_history(app,db,owned_save)
