"""Family Fortunes pages and transactional, idempotent game actions."""
import copy
import hashlib
import json
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from . import family_fortunes as game, domain, infinite_decades, calendar_utils
from .models import Record, ChronicleSave, Portrait

PAGES = {game.PAGE: ('Family Fortunes', 'Marriage negotiations and inheritance disputes')}


def records(session, save):
    return game.active(list(session.scalars(select(Record).where(Record.save_id == save.id,
        Record.deleted.is_(False), Record.kind.in_(game.KINDS - {game.KIND})))))


def render(request, session, ctx, templates):
    save = ctx.get('save')
    rows = records(session, save) if save else []
    homes = sorted((r for r in rows if r.kind == 'household'), key=lambda r: r.label.casefold())
    people = sorted((r for r in rows if r.kind == 'sim' and game.living(r, save)), key=lambda r: r.label.casefold())
    recent = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == game.KIND,
        Record.deleted.is_(False)).order_by(Record.created_at.desc()).limit(24))) if save else []
    current = None
    if request.query_params.get('game'):
        current = session.get(Record, request.query_params['game'])
        if not current or not save or current.save_id != save.id or current.kind != game.KIND or current.deleted:
            raise HTTPException(404, 'That board is not in this save.')
    photo_ids = set(session.scalars(select(Portrait.record_id).where(Portrait.save_id == save.id))) if save else set()
    assessment = None
    problem = ''
    if current and current.data['status'] == 'playing':
        try:
            game.validate(current.data, rows, save)
            if current.data['round']:
                assessment = game.evaluate(current.data, rows, save)
        except ValueError as exc:
            problem = str(exc)
    payload = {'save_id': save.id if save else '', 'epoch': game.epoch(save) if save else [],
        'people': [{'id': p.id, 'name': p.label, 'home': game.home_id(p), 'photo': p.id in photo_ids} for p in people],
        'eligible_ids': [p.id for p in game.eligible(rows, save)] if save else [],
        'homes': {h.id: {'name': h.label, 'funds': game.available_funds(h)} for h in homes},
        'game_id': current.id if current else None, 'version': current.version if current else None,
        'board': current.data if current else None}
    return templates.TemplateResponse(request=request, name='family_fortunes.html', context={**ctx, 'ff_homes': homes, 'ff_people': people,
        'ff_recent': recent, 'ff_game': current, 'ff_data': payload, 'ff_problem': problem,
        'ff_assessment': assessment, 'ff_scenario': game.SCENARIOS[current.data['scenario']] if current else None})


def add_record(session, save, board, suffix, kind, label, data):
    rid = hashlib.sha256((board.id + ':' + suffix).encode()).hexdigest()[:32]
    existing = session.get(Record, rid)
    if existing:
        raise ValueError('An outcome from this board already exists. Refresh before continuing.')
    row = Record(id=rid, save_id=save.id, kind=kind, label=label[:240], global_day=save.global_day,
        data={**data, 'source': game.SOURCE, 'family_fortune_game_id': board.id})
    session.add(row); session.flush()
    domain.journal(session, row, 'upsert', 0); save.revision += 1
    return row


def finish(session, save, board, rows, preview):
    d = board.data; result = preview['evaluation']; by_id = {r.id: r for r in rows}
    body = game.story(d, preview)
    ids = {}
    if d['mode'] == 'matchmaking' and result['agreed']:
        first, second = by_id[d['first_id']], by_id[d['second_id']]
        relation = add_record(session, save, board, 'relationship', 'relationship', f'{first.label} & {second.label}', {
            'partner1_id': first.id, 'partner2_id': second.id, 'partner1_name': first.label, 'partner2_name': second.label,
            'type': 'Betrothal', 'status': 'Active', 'legally_married': False, 'start_global_day': save.global_day,
            'suggested_marriage_global_day': d['marriage_day'],
            'suggested_marriage_date_range': calendar_utils.date_range_label(d['marriage_day'], save.start_year, save.days_per_year)})
        provision = result['summary'][0]
        dowry = add_record(session, save, board, 'dowry', 'dowry_plan', f'{first.label} & {second.label} — negotiated dowry', {
            'relationship_id': relation.id, 'sim_id': first.id, 'household_id': d['household_id'],
            'payer_household_id': d['household_id'], 'recipient_household_id': game.home_id(second),
            'recipient_sim_id': second.id, 'amount': provision['cash'], 'status': 'Planned', 'paid': False,
            'due_global_day': d['marriage_day'], 'promised_heirloom_ids': [a['record_id'] for a in provision['assets'] if a['kind'] == 'heirloom'],
            'promises': [a for a in provision['assets'] if a['kind'] != 'cash'], 'notes': body})
        ids.update(relationship=relation.id, dowry=dowry.id)
    if d['mode'] == 'inheritance':
        plans = sorted((r for r in rows if r.kind == 'estate_plan' and r.data.get('household_id') == d['household_id']), key=lambda r: (r.created_at, r.id))
        details = '\n\nFamily Fortunes proposed settlement (not yet carried out):\n' + body
        fields = {'household_id': d['household_id'],
            'family_fortunes_allocations': result['summary'], 'family_fortunes_reserve': result['reserve'],
            'family_fortunes_debts': result['debts'], 'family_fortune_game_id': board.id,
            'family_fortunes_status': preview['ending']}
        if result['heir_id']:
            fields['heir_sim_id'] = result['heir_id']
        if plans:
            estate = plans[-1]; base = estate.version
            estate.data = {**estate.data, **fields, 'assets': str(estate.data.get('assets') or '') + details}
            estate.version += 1; domain.journal(session, estate, 'upsert', base); save.revision += 1
        else:
            estate = add_record(session, save, board, 'estate', 'estate_plan', d['household_name'] + ' — proposed estate settlement', {**fields, 'assets': details.strip()})
        ids['estate'] = estate.id
    scene = add_record(session, save, board, 'story', 'drama_scene', game.SCENARIOS[d['scenario']]['title'] + ' — ' + preview['ending'], {
        'household_id': d['household_id'], 'sim_id': d.get('first_id') or result['heir_id'],
        'body': body, 'card_title': game.SCENARIOS[d['scenario']]['title'], 'ending_title': preview['ending'],
        'fictional': True, 'planned_not_observed': True})
    provisions = '\n'.join(f'{s["name"]}: §{s["cash"]:,}' + ''.join('; ' + a['label'] + (' — ' + a['detail'] if a['kind'] == 'promise' else '') for a in s['assets'] if a['kind'] != 'cash') for s in result['summary'])
    task = add_record(session, save, board, 'task', 'task', d['household_name'] + ' — ' + preview['ending'], {
        'feature': 'heritage_thread', 'priority': 'Normal',
        'household_id': d['household_id'], 'completed': False, 'status': 'Open', 'due_global_day': save.global_day,
        'next_step': ('Carry out these agreed plans in game, then update their tracker records:\n' if result['agreed'] else 'Review the unresolved family objections before acting on these proposed allocations:\n') + provisions
            + ('\nUnresolved: ' + ' '.join(result['risks']) if result['risks'] else ''),
        'notes': ('Carry out the agreed plans in game, then update the related records. ' if result['agreed'] else
                  'Decide how the household responds to the recorded objections. No disputed claim is an established game fact. ') + body})
    ids.update(story=scene.id, task=task.id)
    return ids


def register(m):
    async def payload(request):
        raw = await request.body()
        if len(raw) > 24000:
            raise HTTPException(413, 'This board submission is too large.')
        try:
            value = json.loads(raw)
            if not isinstance(value, dict): raise ValueError()
        except (ValueError, UnicodeError):
            raise HTTPException(400, 'The board could not be read. Refresh and try again.')
        return value

    def current(request, session, data):
        ctx = m.context(request, session); save = ctx.get('save')
        if not ctx.get('user'): raise HTTPException(401, 'Sign in first.')
        if not save or data.get('save_id') != save.id: raise HTTPException(409, 'The active save changed. Refresh first.')
        if session.get_bind().dialect.name == 'sqlite':
            session.execute(update(ChronicleSave).where(ChronicleSave.id == save.id).values(revision=ChronicleSave.revision))
        session.scalar(select(ChronicleSave).where(ChronicleSave.id == save.id).with_for_update().execution_options(populate_existing=True))
        infinite_decades.guard_request(request, save)
        if infinite_decades.frozen(save): raise HTTPException(409, 'This branch is read-only.')
        if data.get('epoch') != game.epoch(save): raise HTTPException(409, 'The branch or checkpoint changed. Reload the board.')
        return save

    @m.app.post('/family-fortunes/start')
    async def start(request: Request):
        p = await payload(request)
        try:
            with m.db() as session:
                save = current(request, session, p); rows = records(session, save)
                home = next((r for r in rows if r.id == p.get('household_id') and r.kind == 'household'), None)
                if not home: raise ValueError('Choose a household from the active save.')
                beneficiaries = p.get('beneficiary_ids') or []
                if not isinstance(beneficiaries, list) or any(not isinstance(i, str) for i in beneficiaries): raise ValueError('Choose valid beneficiaries.')
                if not isinstance(p.get('mode'), str): raise ValueError('Choose a game mode.')
                data = game.create(rows, save, home, p.get('mode'), game.integer(p.get('budget'), -1),
                    game.integer(p.get('debts'), -1), str(p.get('properties') or ''), beneficiaries)
                board = Record(save_id=save.id, kind=game.KIND, label=home.label + ' — ' + game.SCENARIOS[data['scenario']]['title'], global_day=save.global_day, data=data)
                session.add(board); session.flush(); domain.journal(session, board, 'upsert', 0); save.revision += 1
                url = '/p/family-fortunes?game=' + board.id
            return {'url': url}
        except ValueError as exc:
            return JSONResponse({'detail': str(exc)}, status_code=400)

    @m.app.post('/family-fortunes/{board_id}/action')
    async def action(request: Request, board_id: str):
        p = await payload(request)
        try:
            with m.db() as session:
                save = current(request, session, p)
                board = session.get(Record, board_id, populate_existing=True)
                if not board or board.save_id != save.id or board.kind != game.KIND or board.deleted:
                    raise HTTPException(404, 'That board is not in this save.')
                url = '/p/family-fortunes?game=' + board.id
                if board.data['status'] == 'confirmed' and p.get('action') == 'confirm':
                    return {'url': url}  # Retrying a successful confirmation never duplicates its outcomes.
                if board.data['status'] != 'playing' or board.data.get('infinite_frozen'):
                    raise ValueError('This board has ended. Start another from Family Fortunes.')
                if game.integer(p.get('version'), -1) != board.version:
                    raise HTTPException(409, 'This board was edited in another tab. Reload to use its saved layout.')
                rows = records(session, save); data = copy.deepcopy(board.data); action = p.get('action')
                if action == 'abandon':
                    data.update(status='abandoned', preview=None)
                elif action == 'confirm':
                    if not data.get('preview'): raise ValueError('Review the ending before confirming.')
                    fresh = game.review(data, rows, save)
                    if fresh['fingerprint'] != data['preview']['fingerprint']:
                        raise HTTPException(409, 'A relevant family record changed. Return to the board and review its updated ending. Nothing was applied.')
                    data.update(status='confirmed', result_ids=finish(session, save, board, rows, fresh), confirmed_global_day=save.global_day)
                elif action in {'save', 'negotiate', 'review', 'edit'}:
                    if action != 'edit':
                        if data['round'] >= game.MAX_ROUNDS and p.get('layout') != {k: data[k] for k in ('allocations', 'first_id', 'second_id', 'marriage_day')}:
                            raise ValueError('The final round is locked. Review that offer or start a new board.')
                        layout = p.get('layout')
                        if not isinstance(layout, dict): raise ValueError('The board layout is missing.')
                        data = game.layout(data, layout, rows, save)
                    else:
                        data['preview'] = None
                    if action == 'negotiate': data = game.negotiate(data, rows, save)
                    if action == 'review': data['preview'] = game.review(data, rows, save)
                else:
                    raise ValueError('Choose a board action.')
                base = board.version; board.data = data; board.version += 1
                domain.journal(session, board, 'upsert', base); save.revision += 1
            return {'url': url}
        except ValueError as exc:
            return JSONResponse({'detail': str(exc)}, status_code=400)
