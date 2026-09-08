"""Advance the local chronicle from an authoritative Sims 3 saved-world clock."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from sqlalchemy import select
from .models import ClockLink
from .sims3_save import SaveReadError


def apply_clock(session, save, scan, reset_id=None):
    from . import sync
    from .infinite_decades import import_allowed, lock_current_branch
    lock_current_branch(session, save)
    if not import_allowed(save):
        return {'advanced':0, 'status':'Infinite Decades branch is frozen or awaiting its matching game checkpoint.'}
    clock = scan.get('saved_clock')
    if not clock or clock.get('verified') is not True:
        return {'advanced':0, 'status':'Clock unavailable; tracker day unchanged.'}
    key = str(scan['file_name']) + '|' + str(clock['world_key'])
    settings = dict(save.settings or {})
    previous = dict(settings.get('sims3_save_clock') or {})
    watermarks = dict(previous.get('world_watermarks') or {})
    watermark = watermarks.get(key) or {}
    ticks, game_day = int(clock['ticks']), int(clock['game_day'])
    # A timestamp or touched file alone must never make an older game state new.
    if watermark and (ticks < int(watermark['ticks']) or
                      str(clock.get('saved_at') or '') < str(watermark.get('saved_at') or '')):
        raise SaveReadError('This world clock is older than the last imported clock. No game data or days were changed; load a newer save.')
    baseline = (previous.get('world_file_key') != key or previous.get('reset_id') != reset_id
                or previous.get('tracker_day') != save.global_day)
    before = int(save.global_day)
    if baseline:
        advanced = 0
        message = 'Clock aligned to the current tracker day; no past days were added.'
        if previous and previous.get('world_file_key') != key:
            message = 'Town/save change detected. Clock re-aligned; challenge progress was preserved.'
    else:
        advanced = max(0, game_day - int(previous['game_day']))
        message = f'Tracker advanced by {advanced} day(s).' if advanced else 'Saved time updated; still the same tracker day.'
    save.global_day = before + advanced
    now = datetime.now(timezone.utc)
    watermarks[key] = {'ticks':ticks, 'saved_at':clock.get('saved_at')}
    state = {**clock, 'world_file_key':key, 'file_name':scan['file_name'], 'tracker_day':save.global_day,
             'reset_id':reset_id, 'world_watermarks':watermarks, 'read_at':now.isoformat(), 'status':message}
    settings.update(sims3_save_clock=state, sims3_saved_clock_enabled=True)
    if advanced:
        settings.update(clock_last_advance_from_global_day=before, clock_last_advance_tracker_global_day=save.global_day,
                        clock_last_advance_game_day=game_day, clock_last_advance_at=now.isoformat())
    save.settings = settings
    save.revision += 1
    # Keep normal Today/live-status displays in sync, without creating a usable
    # private receiver token or rotating an existing relay's credentials.
    link = session.scalar(select(ClockLink).where(ClockLink.save_id == save.id))
    if not link:
        link = ClockLink(save_id=save.id,token_hash=secrets.token_hex(32),enabled=False)
        session.add(link)
    link.game_anchor_day = game_day
    link.tracker_anchor_day = save.global_day
    link.last_game_day = game_day
    link.last_game_hour = clock['game_hour']
    link.last_game_minute = clock['game_minute']
    link.last_seen_at = now
    sync.sync_clock_state(session,save,link)
    return {'advanced':advanced, 'status':message, 'saved_clock':clock}
