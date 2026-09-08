"""Single-dynasty branch controls and read-only family history."""
from types import SimpleNamespace
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy import select
from . import infinite_decades as dynasty
from .models import Record, Portrait


def _people(session, save):
    return list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind.in_({"sim", "household"})))) if save else []


def render(request, session, ctx, templates):
    save = ctx.get("save")
    family = dynasty.branches(session, save)
    family.sort(key=lambda r: (dynasty.metadata(r).get("status") == "archive", -dynasty.metadata(r).get("split_global_day", 1)))
    selected = next((r for r in family if r.id == request.query_params.get("branch_id")), None)
    active = dynasty.active_branch(session, save)
    rows = _people(session, save)
    sims = sorted((r for r in rows if r.kind == "sim"), key=lambda r: (dynasty.household_id(r), r.label.casefold()))
    point = None
    history = []
    if selected:
        point = dynasty.snapshot(session, save) if selected == active and not dynasty.frozen(save) else dynasty.unpack_snapshot(selected.data["snapshot"])
        history = [r for r in point["records"] if r["kind"] in {"roll", "pregnancy", "illness", "relationship", "death", "story_entry", "game_history", "migration", "note"}]
        history.sort(key=lambda r: (r.get("global_day") or 0, r["label"]), reverse=True)
    focus = next((r for r in sims if r.id == request.query_params.get("sim_id")), None)
    focus_day = int((focus.data or {}).get("infinite_frozen_global_day") or save.global_day) if focus else None
    focus_age = None
    if focus and focus.data.get("birth_global_day") is not None:
        end = min(focus_day, int(focus.data.get("death_global_day") or focus_day))
        years, days = divmod(max(0, end - int(focus.data["birth_global_day"])), max(1, save.days_per_year))
        focus_age = f"{years} years, {days} days at the preserved date"
    ctx.update(page="infinite-decades", title="Infinite Decades", branch_state=dynasty.state(save),
        branches=family, branch_meta=dynasty.metadata, branch_year=dynasty.year,
        active_branch=active, next_branch=dynasty.next_branch(family),
        branch_sims=[r for r in sims if not r.deleted], dynasty_sims=sims,
        starting_sims=[r for r in sims if r.data.get("infinite_frozen") and r.data.get("infinite_branch_id") == dynasty.state(save).get("starting_branch_id")],
        branch_homes={r.id:r.label for r in rows if r.kind == "household"}, branch_home_id=dynasty.household_id,
        branch_finish_reason=dynasty.finish_reason(session, save) if save and dynasty.state(save) and not dynasty.frozen(save) else None,
        branch_notice=request.session.pop("infinite_notice", None), viewed_branch=selected, viewed_snapshot=point,
        branch_history=history[:100], branch_history_count=len(history), dynasty_focus=focus,
        dynasty_focus_day=focus_day, dynasty_focus_age=focus_age, dynasty_names={r.id:r.label for r in sims},
        navigation_group=next((g for g in ctx["navigation_groups"] if g["id"] == "play"), None))
    return templates.TemplateResponse(request, "infinite_decades.html", ctx)


def render_tree(request, session, ctx, templates):
    from .family_tree import context_for as family_context
    save = ctx["save"]
    rows = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind.in_({"sim", "relationship", "household"}))))
    ctx.update(page="family-tree", title="Dynasty Family Tree",
        photo_record_ids=set(session.scalars(select(Portrait.record_id).where(Portrait.save_id == save.id))))
    ctx.update(family_context(rows, save, request.query_params, ctx["photo_record_ids"], dynasty.branches(session, save)))
    return templates.TemplateResponse(request, "family_explorer.html", ctx)


def register(app, db, owned_save):
    router = APIRouter()

    @app.exception_handler(dynasty.BranchFrozenError)
    async def frozen_error(request, exc):
        return JSONResponse({"detail":str(exc)}, status_code=409)

    def run(request, save_id, action):
        try:
            with db() as session:
                save = owned_save(request, session, save_id)
                action(session, save)
                request.session["save_id"] = save.id
                request.session.pop("today_undo", None)
                request.session.pop("last_roll", None)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return RedirectResponse("/p/infinite-decades", 303)

    @router.post("/infinite/{save_id}/toggle")
    def toggle(request: Request, save_id: str, enabled: str = Form(...), return_to: str = Form("/p/saves")):
        if enabled not in {"true", "false"}: raise HTTPException(400, "Choose On or Off for Infinite Decades.")
        destination = return_to if return_to in {"/p/saves", "/p/infinite-decades"} else "/p/saves"
        try:
            with db() as session:
                save = owned_save(request, session, save_id)
                if enabled == "true" and not dynasty.state(save):
                    request.session["save_id"] = save.id
                    request.session["infinite_notice"] = "Choose the starting family and checkpoint below to turn Infinite Decades on for this save."
                    destination = "/p/infinite-decades"
                else:
                    dynasty.set_enabled(session, save, enabled == "true")
                    message = (f"Infinite Decades is on for {save.name}. Branches and dates are unchanged. Confirm the matching game checkpoint before receiving reports."
                               if enabled == "true" else f"Infinite Decades is off for {save.name}. " +
                               ("The dynasty is paused; all branches and history are preserved." if dynasty.state(save) else "This is a normal save."))
                    request.session["infinite_notice" if destination == "/p/infinite-decades" else "backup_notice"] = message
                if request.session.get("save_id") == save.id:
                    request.session.pop("today_undo", None)
                    request.session.pop("last_roll", None)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return RedirectResponse(destination, 303)

    @router.post("/infinite/{save_id}/enable")
    def enable(request: Request, save_id: str, sim_ids: list[str] = Form(default=[]), label: str = Form(...),
               modern_year: int = Form(2026), game_save_name: str = Form(...), checkpoint_confirmed: str = Form("")):
        if checkpoint_confirmed != "yes": raise HTTPException(400, "Save a matching in-game starting checkpoint first.")
        def action(session, save):
            dynasty.enable(session, save, sim_ids, label, modern_year, game_save_name)
            request.session["infinite_notice"] = "Infinite Decades enabled in this same dynasty save. Sim IDs and family links are unchanged; the starting world is preserved."
        return run(request, save_id, action)

    @router.post("/infinite/{save_id}/capture")
    def capture(request: Request, save_id: str, sim_ids: list[str] = Form(default=[]), label: str = Form(...),
                game_save_name: str = Form(...), checkpoint_confirmed: str = Form(""), from_start: str = Form("")):
        if checkpoint_confirmed != "yes": raise HTTPException(400, "Save the matching in-game split checkpoint first.")
        def action(session, save):
            method = dynasty.capture_starting if from_start == "yes" else dynasty.capture
            child = method(session, save, sim_ids, label, game_save_name)
            request.session["infinite_notice"] = f"{child.label} is waiting at year {dynasty.metadata(child)['split_year']}. It stays inside this dynasty save; the active branch's day is unchanged."
        return run(request, save_id, action)

    @router.post("/infinite/{save_id}/finish")
    def finish(request: Request, save_id: str):
        def action(session, save):
            reason = dynasty.finish(session, save)
            request.session["infinite_notice"] = "Branch history preserved: " + ("reached modern day." if reason == "modern" else "the family line died out.")
        return run(request, save_id, action)

    @router.post("/infinite/{save_id}/next")
    def next_branch(request: Request, save_id: str, load_confirmed: str = Form("")):
        if load_confirmed != "yes": raise HTTPException(400, "Confirm you will load the next branch's matching Sims checkpoint.")
        def action(session, save):
            target = dynasty.activate_next(session, save)
            request.session["infinite_notice"] = f"Now playing {target.label} at year {dynasty.year(save)} in this same dynasty save. Load its Sims checkpoint, reconnect game reading, and confirm below."
        return run(request, save_id, action)

    @router.post("/infinite/{save_id}/confirm-game")
    def confirm_game(request: Request, save_id: str, confirmed: str = Form("")):
        if confirmed != "yes": raise HTTPException(400, "Confirm the game checkpoint and reader connection match this branch.")
        def action(session, save):
            dynasty.confirm_game(session, save)
            request.session["infinite_notice"] = "Matching game checkpoint confirmed. New reports can update only the active family line."
        return run(request, save_id, action)

    app.include_router(router)
