"""Explicit, save-scoped crash recovery with stale-preview and backup safeguards."""
from collections import Counter
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from . import crash_recovery as recovery, infinite_decades, backup_service, sync
from .models import ClockLink, ActionPreview

PAGES={'crash-recovery':('Crash Recovery','Review a game reload without silently rewriting your chronicle')}


def render(request,session,ctx,templates):
    save=ctx.get('save')
    if not save:return RedirectResponse('/p/saves',303)
    point=recovery.nearest(session,save)
    changes,errors=recovery.changes_since(session,save,point) if recovery.held(save) else ([],[])
    visible=recovery.state(save)
    if visible.get('epoch')!=recovery.epoch(save):visible={}
    ctx.update(recovery=visible,recovery_checkpoint=point,
        recovery_changes=changes,recovery_errors=errors,recovery_counts=Counter(r['kind'] for r in changes),
        recovery_time=recovery.time_label,recovery_notice=request.session.pop('recovery_notice',None))
    return templates.TemplateResponse(request,'crash_recovery.html',ctx)


def register(m):
    def owned(request,session,form):
        save=m.owned_save(request,session,str(form.get('save_id') or ''))
        if request.session.get('save_id')!=save.id:
            raise HTTPException(409,'Switch back to the save whose recovery you opened.')
        recovery.lock(session,save)
        if not infinite_decades.import_allowed(save):
            raise HTTPException(409,'This branch is frozen or waiting for its game checkpoint.')
        state=recovery.state(save)
        if not recovery.held(save) or state.get('epoch')!=recovery.epoch(save) or form.get('recovery_id')!=state.get('id'):
            raise HTTPException(409,'This recovery has changed or is already resolved. Open Crash Recovery again.')
        link=session.scalar(select(ClockLink).where(ClockLink.save_id==save.id))
        if not link:raise HTTPException(409,'The clock connection was removed. No recovery changes were made.')
        return save,link,state

    @m.app.post('/recovery/resolve')
    async def resolve(request:Request):
        form=await request.form()
        with m.db() as session:
            save,link,state=owned(request,session,form)
            choice=form.get('choice')
            if choice not in {'wait','keep'}:raise HTTPException(400,'Choose how to align the clock.')
            if state['status']=='awaiting_full':raise HTTPException(409,'Recovery is already waiting for a fresh full game report.')
            if choice=='wait':
                recovery.put_state(save,{**state,'status':'catching_up','resolution':'kept_history'})
                request.session['recovery_notice']='History kept. Game changes stay paused until the game reaches '+recovery.time_label(state['from_minute'])+' and a full report arrives.'
            else:
                link.game_anchor_day=state['restored_minute']//1440;link.tracker_anchor_day=save.global_day
                recovery.put_state(save,{**state,'status':'awaiting_full','resolution':'kept_and_rebased','resume_minute':state['restored_minute']})
                save.settings={**save.settings,'clock_game_day_high_watermark':link.game_anchor_day}
                request.session['recovery_notice']='All records and completed rolls kept. The restored game day is now aligned to your current tracker day. Waiting for a full report.'
            save.revision+=1
            sync.sync_clock_state(session,save,link)
            sync.ensure_save_metadata(session,save)
        return RedirectResponse('/p/crash-recovery',303)

    @m.app.post('/recovery/preview')
    async def preview(request:Request):
        form=await request.form()
        with m.db() as session:
            save,link,state=owned(request,session,form)
            if state['status']=='awaiting_full':raise HTTPException(409,'Rollback was already confirmed.')
            point=recovery.nearest(session,save)
            changes,errors=recovery.changes_since(session,save,point)
            if errors:raise HTTPException(409,' '.join(errors[:5]))
            ticket=ActionPreview(id=uuid4().hex,user_id=m.signed_in(request,session).id,save_id=save.id,
                operation='crash-recovery',fingerprint=recovery.fingerprint(save,point,changes),
                payload={'checkpoint_id':point.id,'recovery_id':state['id']})
            session.add(ticket);session.flush()
            ctx=m.context(request,session,page='crash-recovery')
            ctx.update(recovery=state,recovery_checkpoint=point,recovery_changes=changes,recovery_errors=[],
                recovery_counts=Counter(r['kind'] for r in changes),recovery_time=recovery.time_label,
                recovery_preview=ticket)
            return m.templates.TemplateResponse(request,'crash_recovery.html',ctx)

    @m.app.post('/recovery/{preview_id}/confirm')
    async def confirm(request:Request,preview_id:str):
        form=await request.form()
        with m.db() as session:
            save,link,state=owned(request,session,form)
            ticket=session.get(ActionPreview,preview_id)
            user=m.signed_in(request,session)
            if not ticket or ticket.save_id!=save.id or ticket.user_id!=user.id or ticket.operation!='crash-recovery':
                raise HTTPException(404,'Recovery preview not found.')
            created=ticket.created_at.replace(tzinfo=timezone.utc) if ticket.created_at.tzinfo is None else ticket.created_at
            if ticket.consumed or datetime.now(timezone.utc)-created>timedelta(minutes=20):
                raise HTTPException(409,'This preview is used or expired. Nothing was changed.')
            point=recovery.nearest(session,save)
            changes,errors=recovery.changes_since(session,save,point)
            if errors or recovery.fingerprint(save,point,changes)!=ticket.fingerprint:
                raise HTTPException(409,'The affected records or settings changed. Review a fresh preview; nothing was changed.')
            # If backup creation fails, the entire transaction fails before any undo.
            backup=backup_service.create_snapshot(session,save,'Before crash recovery',force=True)
            recovery.apply_rollback(session,save,link,changes)
            recovery.put_state(save,{**recovery.state(save),'backup_id':backup.id})
            ticket.consumed=True
            request.session['recovery_notice']='Rollback complete. Your pre-recovery backup is available below. Waiting for a new full game report before automation resumes.'
        return RedirectResponse('/p/crash-recovery',303)
