"""Local-only, opt-in reader for completed Sims 3 saves. Never touches the game."""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from . import sims3_save, save_scanner

router = APIRouter()
_lock = threading.RLock()
_stop = threading.Event()
_thread = None
_observed = {}
_previews = {}
_log = logging.getLogger(__name__)


def state_path():
    configured = os.getenv('DECADES_SIMS3_READER_STATE')
    if configured: return Path(configured)
    return Path(os.getenv('LOCALAPPDATA') or Path.home() / '.local/share') / 'DecadesTracker' / 'sims3-save-reader.json'


def _read():
    path = state_path()
    if not path.exists(): return {}
    result = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(result, dict): raise ValueError('Invalid save-reader settings')
    return result


def _write(value):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def status(save_id):
    with _lock: return copy.deepcopy(_read().get(save_id, {}))


def configure(save_id, file_name, enabled, scope='household', sync_clock=None):
    """Names must be discovered locally. Never follow a path supplied by a browser."""
    available = {p.name:p for p in sims3_save.discover_saves()}
    if file_name not in available: raise sims3_save.SaveReadError('Choose a detected Sims 3 save folder.')
    scope = scope if scope in {'residents', 'save'} else 'household'
    with _lock:
        values = _read()
        previous = values.get(save_id, {})
        clock_enabled = bool(previous.get('sync_clock')) if sync_clock is None else bool(sync_clock)
        reset_clock = (previous.get('enabled') != bool(enabled) or bool(previous.get('sync_clock')) != clock_enabled)
        if previous.get('file_name') != file_name or previous.get('scope') != scope:
            previous = {}
            reset_clock = True
        values[save_id] = {**previous, 'file_name':file_name, 'enabled':bool(enabled), 'scope':scope,
                           'sync_clock':clock_enabled,
                           'clock_reset_id':uuid4().hex if reset_clock else previous.get('clock_reset_id'),
                           'status':'Waiting for a completed game save.' if enabled else 'Automatic reading is paused.'}
        _write(values)
        _observed.pop(save_id, None)


def _population(scan, scope):
    scan = copy.deepcopy(scan)
    scan['population_scope'] = scope
    active = scan['slot']['active_household_game_id']
    for home in scan['households']:
        # Inclusion is not evidence that a household was played by the user.
        home['include_in_scan'] = (scope == 'save' or home['game_household_id'] == active
                                   or (scope == 'residents' and home.get('has_home_lot', False)))
    return scan


def apply_scan(session, save, scan, selected, *, sync_clock=False, clock_reset_id=None):
    from . import backup_service, game_modes, domain
    from .models import Record
    if game_modes.for_save(save)['id'] != 'sims3' or scan.get('game_edition') != 'sims3':
        raise sims3_save.SaveReadError('This reader can only import into a Sims 3 tracker save.')
    backup_service.create_snapshot(session, save, 'before Sims 3 save-file import', force=True)
    clock_result = {'advanced':0, 'status':'Automatic saved-clock advancement is off.'}
    if sync_clock and domain.automation_enabled(save):
        from .sims3_save_clock import apply_clock
        clock_result = apply_clock(session, save, scan, clock_reset_id)
    tracked = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.deleted.is_(False))))
    # Upgrade the previous raw household ID only when its world also matches.
    # No name-only merge: two worlds can each have a household with ID 37.
    for home in scan.get('households') or []:
        for record in tracked:
            if record.kind != 'household': continue
            data = record.data or {}
            if (str(data.get('game_household_id') or '') == home.get('raw_household_game_id')
                    and str(data.get('last_game_world') or '').casefold() == home.get('world_name','').casefold()):
                base = record.version
                record.data = {**data, 'game_household_id':home['game_household_id'],
                               'game_household_source_world':home['source_world_key']}
                record.version += 1
                domain.journal(session, record, 'upsert', base)
                save.revision += 1
    selected = set(selected)
    by_game_id = {str(r.data.get('game_sim_id') or ''):r for r in tracked if r.kind == 'sim' and r.data.get('game_sim_id')}
    candidates = {str((r.data.get('payload') or {}).get('game_sim_id') or r.data.get('game_sim_id') or ''):r for r in tracked
                  if r.kind == 'game_candidate' and r.data.get('action') in {'new_sim','new_baby'}}
    stale = 0
    for sim in scan.get('sims') or []:
        sid = sim['game_sim_id']
        if sid not in selected: continue
        existing, candidate = by_game_id.get(sid), candidates.get(sid)
        previous = (existing.data.get('sims3_save_observed_at') if existing else None)
        if not previous and candidate: previous = (candidate.data.get('payload') or {}).get('source_world_saved_at')
        incoming = sim.get('source_world_saved_at')
        if previous and incoming and incoming < previous:
            selected.discard(sid); stale += 1
    result = save_scanner.reconcile_scan(session, save, scan, selected, advance_clock=False, pre_advanced=clock_result['advanced'])
    by_game_id = {str(r.data.get('game_sim_id') or ''):r for r in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == 'sim', Record.deleted.is_(False))) if r.data.get('game_sim_id')}
    for sim in scan.get('sims') or []:
        record = by_game_id.get(sim['game_sim_id'])
        if record and sim['game_sim_id'] in selected and sim.get('source_world_saved_at'):
            if record.data.get('sims3_save_observed_at') != sim['source_world_saved_at']:
                base = record.version
                record.data = {**record.data, 'sims3_save_observed_at':sim['source_world_saved_at']}
                record.version += 1; domain.journal(session, record, 'upsert', base)
                save.revision += 1
    return {**result, 'advanced':clock_result['advanced'], 'clock_status':clock_result['status'],
            'stale_sim_snapshots_skipped':stale}


def _import_key(scan, binding):
    return (f"{binding['file_name']}:{binding.get('scope')}:{scan['fingerprint']}:v{sims3_save.READER_REVISION}"
            f":clock{int(bool(binding.get('sync_clock')))}:{binding.get('clock_reset_id') or ''}")


def tick():
    """One background check. Stat unchanged files cheaply; parse only a new save."""
    from .config import settings
    from .db import SessionLocal
    from .models import ChronicleSave
    from . import domain, game_modes, sync
    if not settings.local_mode: return
    with _lock: bindings = copy.deepcopy(_read())
    available = {p.name:p for p in sims3_save.discover_saves()}
    for save_id, binding in bindings.items():
        if not binding.get('enabled'): continue
        try:
            with SessionLocal() as session:
                save = session.get(ChronicleSave, save_id)
                if save is None or game_modes.for_save(save)['id'] != 'sims3': continue
                if not domain.automation_enabled(save):
                    _set_status(save_id, binding, 'Paused by the tracker’s master automation switch.', reset_clock=True)
                    _observed.pop(save_id, None)
                    continue
            folder = available.get(binding.get('file_name'))
            if folder is None: raise sims3_save.SaveReadError('The selected save folder is not available. Choose it again if you used Save As.')
            signature = sims3_save.file_signature(folder)
            observation = (signature, binding.get('clock_reset_id'), bool(binding.get('sync_clock')), sims3_save.READER_REVISION)
            if _observed.get(save_id) == observation: continue
            scan = _population(sims3_save.inspect_save(folder, all_worlds=binding.get('scope') in {'residents','save'}), binding.get('scope'))
            # Equal bytes after a touch/restart must not replay automations.
            if (scan['fingerprint'] == binding.get('last_fingerprint')
                    and binding.get('reader_revision') == sims3_save.READER_REVISION
                    and binding.get('last_clock_reset_id') == binding.get('clock_reset_id')):
                _observed[save_id] = observation
                _set_status(save_id, binding, 'Up to date. Waiting for your next game save.')
                continue
            with _lock:
                if _read().get(save_id) != binding: continue  # re-paired or paused during scan
                with SessionLocal() as session:
                    save = session.get(ChronicleSave, save_id)
                    if save is None or not domain.automation_enabled(save): continue
                    import_key = _import_key(scan, binding)
                    if (save.settings or {}).get('sims3_reader_import_key') == import_key:
                        _observed[save_id] = observation
                        _set_status(save_id, binding, 'This saved snapshot was already imported. Waiting for your next game save.')
                        continue
                    # A restored old save cannot overwrite newer imported facts.
                    previous_date = binding.get('last_modified_at')
                    if previous_date and (scan['modified_at'] < previous_date or (
                            scan['modified_at'] == previous_date and scan['fingerprint'] != binding.get('last_fingerprint'))):
                        raise sims3_save.SaveReadError('An older or conflicting save was found. Automatic import is paused until a newer game save is written; preview it to review manually.')
                    _, sims = save_scanner.relevant_population(scan)
                    result = apply_scan(session, save, scan, {s['game_sim_id'] for s in sims},
                                        sync_clock=bool(binding.get('sync_clock')), clock_reset_id=binding.get('clock_reset_id'))
                    save.settings = {**(save.settings or {}), 'sims3_reader_import_key':import_key}
                    sync.ensure_save_metadata(session, save)
                    session.commit()
                values = _read()
                values[save_id] = {**binding, 'last_fingerprint':scan['fingerprint'], 'reader_revision':sims3_save.READER_REVISION,
                                   'last_clock_reset_id':binding.get('clock_reset_id'), 'detected_clock':scan.get('saved_clock'),
                                   'last_modified_at':scan['modified_at'], 'last_import_at':datetime.now(timezone.utc).isoformat(),
                                   'result':result, 'world_count':scan.get('world_count',1),
                                   'warnings':scan.get('warnings',[]),
                                   'status':f"Read {len(sims)} Sim(s) across {scan.get('world_count',1)} saved world(s). {result['candidates']} new review item(s) in Automation Inbox. {result['clock_status']}"}
                _write(values)
                _observed[save_id] = observation
        except (sims3_save.SaveReadError, OSError, UnicodeError, ValueError) as exc:
            _set_status(save_id, binding, str(exc))
        except Exception:
            _log.exception('Sims 3 save import failed; transaction rolled back')
            _set_status(save_id, binding, 'Could not import this save. No partial import was kept; see the tracker diagnostic log.')


def _set_status(save_id, binding, message, *, reset_clock=False):
    with _lock:
        values = _read()
        current = values.get(save_id)
        if current == binding and current.get('status') != message:
            values[save_id] = {**current, 'status':message}
            if reset_clock: values[save_id]['clock_reset_id'] = uuid4().hex
            _write(values)


def _run():
    while not _stop.wait(10):
        try: tick()
        except Exception: _log.exception('Sims 3 save-reader check failed')


def start():
    global _thread
    from .config import settings
    if not settings.local_mode or os.getenv('DECADES_DISABLE_SIMS3_READER') == '1': return
    if _thread and _thread.is_alive(): return
    _stop.clear()
    _thread = threading.Thread(target=_run, name='DecadesSims3SaveReader', daemon=True)
    _thread.start()


def stop():
    _stop.set()
    if _thread: _thread.join(timeout=5)


def _local_save(request, session):
    from . import main, game_modes
    if not main.settings.local_mode: raise HTTPException(400, 'Save-file reading is available in the local desktop tracker.')
    save = main.context(request, session).get('save')
    if not save or game_modes.for_save(save)['id'] != 'sims3':
        raise HTTPException(400, 'Open a Sims 3 tracker save first.')
    return save


@router.post('/api/sims3-save/settings')
async def reader_settings(request: Request):
    from . import main
    form = await request.form()
    with main.db() as session:
        save = _local_save(request, session)
        try:
            sync_clock = form.get('sync_clock') == 'on' if form.get('clock_setting_present') else None
            configure(save.id, str(form.get('file_name') or ''), form.get('enabled') == 'on', str(form.get('scope') or 'household'), sync_clock)
            save.settings = {**(save.settings or {}), 'sims3_saved_clock_enabled':bool(status(save.id).get('sync_clock') and form.get('enabled') == 'on')}
            save.revision += 1
            from . import sync
            sync.ensure_save_metadata(session, save)
        except sims3_save.SaveReadError as exc: raise HTTPException(400, str(exc)) from exc
    return RedirectResponse('/p/clock#save-reader', 303)


@router.post('/api/sims3-save/preview')
async def preview_save(request: Request):
    from . import main
    from starlette.concurrency import run_in_threadpool
    form = await request.form()
    with main.db() as session:
        save = _local_save(request, session)
        available = {p.name:p for p in sims3_save.discover_saves()}
        folder = available.get(str(form.get('file_name') or ''))
        if folder is None: raise HTTPException(400, 'Choose a detected Sims 3 save folder.')
        try:
            scope = str(form.get('scope') or 'household')
            scan = _population(await run_in_threadpool(sims3_save.inspect_save, folder, all_worlds=scope in {'residents','save'}), scope)
            comparison = save_scanner.compare_scan(session, save, scan)
            scan['comparison'], scan['relevant_sims'] = comparison, comparison['rows']
            _previews[save.id] = scan
        except (sims3_save.SaveReadError, OSError, UnicodeError) as exc:
            request.session['game_save_notice'] = str(exc)
    return RedirectResponse('/p/clock#save-reader', 303)


@router.post('/api/sims3-save/apply')
async def apply_preview(request: Request):
    from . import main
    form = await request.form()
    with _lock, main.db() as session:
        save = _local_save(request, session)
        scan = _previews.get(save.id)
        if not scan: raise HTTPException(409, 'Preview the game save first.')
        allowed = {s['game_sim_id'] for s in scan['relevant_sims']}
        selected = set(form.getlist('game_sim_id')) & allowed
        if not selected: raise HTTPException(400, 'Select at least one Sim.')
        result = apply_scan(session, save, scan, selected)
        _previews.pop(save.id, None)
        request.session['clock_notice'] = f"Read-only import complete: {result['candidates']} review item(s). This manual import does not change the clock; use automatic saved-clock advancement for that."
    return RedirectResponse('/p/automation', 303)


def page_context(save_id):
    return {'sims3_reader':status(save_id), 'sims3_save_files':[p.name for p in sims3_save.discover_saves()],
            'sims3_save_preview':_previews.get(save_id)}
