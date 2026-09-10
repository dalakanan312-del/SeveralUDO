"""Hold rewound game reports before ingestion; explicitly review any historical undo.

Checkpoint sequences belong to this database. They must never be transferred as
clock state to a different database. Completed rolls are preserved unless the
player explicitly confirms a whole-checkpoint rollback, after a save backup.
"""
import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select, func, or_, delete, update
from .models import ClockCheckpoint, Record, Change, ActionPreview, ChronicleSave
from . import infinite_decades, sync

KEY = 'clock_recovery'
EXCLUDED = {'clock_state', 'clock_protocol_state', 'clock_diagnostic', 'save_metadata',
            'portrait_blob', 'dice_audit_record', 'why_evidence'}
MAX_RECORDS = 3000


def lock(session, save):
    if session.get_bind().dialect.name == 'sqlite':
        session.execute(update(ChronicleSave).where(ChronicleSave.id==save.id).values(revision=ChronicleSave.revision))
    else:
        session.execute(select(ChronicleSave.id).where(ChronicleSave.id==save.id).with_for_update())
    session.refresh(save)


def state(save):
    return dict((save.settings or {}).get(KEY) or {})


def held(save):
    value=state(save)
    return value.get('epoch')==epoch(save) and value.get('status') in {'review', 'catching_up', 'awaiting_full'}


def epoch(save):
    return str(infinite_decades.state(save).get('epoch', '')) + ':' + str((save.settings or {}).get('clock_recovery_epoch', ''))


def configuration(save):
    # Never retain credentials in a checkpoint. A digest also prevents rolling
    # old records back under a different calendar, rulepack or save setting.
    values = {k:v for k,v in (save.settings or {}).items() if not k.startswith('clock_')}
    body = [save.start_year, save.days_per_year, save.pregnancy_days, values]
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def tick(day, hour=0, minute=0):
    return int(day)*1440 + int(hour or 0)*60 + int(minute or 0)


def time_label(value):
    day, within = divmod(int(value), 1440)
    return f'Day {day} · {within//60:02d}:{within%60:02d}'


def put_state(save, value):
    save.settings = {**(save.settings or {}), KEY: value}


def checkpoint(session, save, link):
    if held(save) or link.last_game_day is None:
        return
    point = tick(link.last_game_day, link.last_game_hour, link.last_game_minute)
    latest = session.scalar(select(ClockCheckpoint).where(ClockCheckpoint.save_id==save.id,
        ClockCheckpoint.epoch==epoch(save)).order_by(ClockCheckpoint.created_at.desc(), ClockCheckpoint.game_minute.desc()).limit(1))
    # Ten game-minute boundaries; retain about two weeks of continuous play.
    if latest and point//10 == latest.game_minute//10:
        return
    session.flush()
    sequence = session.scalar(select(func.max(Change.sequence)).where(Change.save_id==save.id)) or 0
    session.add(ClockCheckpoint(save_id=save.id, epoch=epoch(save), game_minute=point,
        global_day=save.global_day, change_sequence=sequence, configuration=configuration(save)))
    session.flush()
    old = select(ClockCheckpoint.id).where(ClockCheckpoint.save_id==save.id).order_by(ClockCheckpoint.created_at.desc()).offset(2048)
    session.execute(delete(ClockCheckpoint).where(ClockCheckpoint.id.in_(old)))


def gate(session, save, link, report, day, hour, minute):
    """Called AFTER protocol validation and BEFORE any population/calendar changes."""
    point = tick(day, hour, minute)
    previous = tick(link.last_game_day, link.last_game_hour, link.last_game_minute) if link.last_game_day is not None else None
    recovery = state(save)
    if recovery and recovery.get('epoch') != epoch(save):
        recovery = {}  # A different Infinite Decades branch has its own timeline.
        put_state(save, recovery)
    if not held(save) and previous is not None and point < previous:
        recovery = dict(id=uuid4().hex, status='review', epoch=epoch(save),
            from_minute=previous, restored_minute=point, latest_minute=point,
            tracker_day=save.global_day,
            target_day=max(1, save.global_day + day - previous//1440),
            anchor_game=link.game_anchor_day, anchor_tracker=link.tracker_anchor_day,
            detected_at=datetime.now(timezone.utc).isoformat(), ordered=bool(report.get('report_sequence')))
        put_state(save, recovery)
        save.revision += 1
    if not recovery or recovery.get('status') not in {'review', 'catching_up', 'awaiting_full'}:
        return None
    if point < recovery['restored_minute']:
        # Another reload went back even farther; a previous preview must not
        # silently realign to the first, now-incorrect recovery point.
        recovery={**recovery,'id':uuid4().hex,'status':'review','restored_minute':point,
            'target_day':max(1,recovery['target_day']+day-recovery['restored_minute']//1440)}
        put_state(save,recovery)
    # Holding reports acknowledges their sequence, but never imports their Sims.
    # Require a full population snapshot after skipping deltas during recovery.
    full = not report.get('report_kind') or str(report.get('report_kind')).casefold()=='full'
    if recovery['status']=='catching_up' and point >= recovery['from_minute']:
        recovery = {**recovery, 'status':'awaiting_full'}
    if recovery['status']=='awaiting_full' and full and point >= recovery.get('resume_minute', recovery['from_minute']):
        finish(save, link, recovery, recovery.get('resolution', 'caught_up'))
        return None
    recovery = {**recovery, 'latest_minute':point}
    put_state(save, recovery)
    link.last_game_day, link.last_game_hour, link.last_game_minute = day, hour, minute
    link.last_seen_at = datetime.now(timezone.utc)
    return {'ok':True, 'status':'recovery_hold', 'automation_paused':True, 'advanced':0, 'clock_anchor_repaired':False,
        'tracker_global_day':save.global_day, 'game_time':{'day':day,'hour':hour,'minute':minute},
        'message':'Game time moved backward. Open Crash Recovery; no game changes were imported.',
        'recovery_required':True}


def finish(save, link, recovery, resolution):
    new_epoch=uuid4().hex
    save.settings = {**(save.settings or {}), KEY:{**recovery,'status':'resolved','resolution':resolution},
        'clock_recovery_epoch':new_epoch,
        'clock_game_day_high_watermark':link.last_game_day}
    put_state(save,{**state(save),'epoch':epoch(save)})
    save.revision += 1


def nearest(session, save):
    recovery = state(save)
    if not recovery.get('restored_minute') and recovery.get('restored_minute') != 0:
        return None
    return session.scalar(select(ClockCheckpoint).where(ClockCheckpoint.save_id==save.id,
        ClockCheckpoint.epoch==epoch(save), ClockCheckpoint.game_minute<=recovery['restored_minute']).order_by(
        ClockCheckpoint.game_minute.desc(), ClockCheckpoint.created_at.desc()).limit(1))


def content(row):
    return copy.deepcopy({key:getattr(row,key) for key in ('kind','label','global_day','data','deleted','version')})


def changes_since(session, save, point):
    """Bounded conservative plan. Missing history blocks undo, never guesses it."""
    if not point:
        return [], ['No earlier recovery checkpoint exists on this tracker. Checkpoints begin with reports received after this update.']
    errors = []
    if point.configuration != configuration(save):
        errors.append('Calendar, rulepack or save settings changed since that checkpoint. Automatic rollback is unavailable; keep history or restore a backup separately.')
    ids = select(Change.record_id).where(Change.save_id==save.id, Change.sequence>point.change_sequence)
    rows = list(session.scalars(select(Record).where(Record.save_id==save.id, Record.kind.notin_(EXCLUDED),
        or_(Record.id.in_(ids),Record.updated_at>point.created_at,Record.created_at>point.created_at)).order_by(Record.kind,Record.label).limit(MAX_RECORDS+1)))
    if len(rows)>MAX_RECORDS:
        return [], ['More than 3,000 records changed. Use a full save backup for this larger recovery.']
    if not rows:
        return [], errors
    row_ids = [r.id for r in rows]
    latest = select(Change.record_id,func.max(Change.sequence).label('seq')).where(Change.save_id==save.id,
        Change.record_id.in_(row_ids),Change.sequence<=point.change_sequence).group_by(Change.record_id).subquery()
    before = {c.record_id:c.payload for c in session.scalars(select(Change).join(latest,Change.sequence==latest.c.seq))}
    result = []
    for row in rows:
        prior = before.get(row.id)
        after = content(row)
        if prior is not None and (not isinstance(prior,dict) or prior.get('id')!=row.id
                or prior.get('kind')!=row.kind or not isinstance(prior.get('label'),str)
                or not isinstance(prior.get('data'),dict) or 'deleted' not in prior):
            errors.append('Earlier history has an unsupported format: '+row.label)
            continue
        if prior and all(prior.get(k)==after[k] for k in ('kind','label','global_day','data','deleted')):
            continue
        if row.data.get('infinite_frozen'):
            errors.append('A frozen branch record changed; recover its branch separately: '+row.label)
        if not prior:
            created = row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None else row.created_at
            captured = point.created_at.replace(tzinfo=timezone.utc) if point.created_at.tzinfo is None else point.created_at
            if created <= captured:
                errors.append('Earlier record state was not retained: '+row.label)
            if row.deleted:
                continue
        result.append({'id':row.id, 'kind':row.kind, 'label':row.label, 'before':prior, 'current':after,
            'action':'Restore earlier state' if prior else 'Archive record created after checkpoint'})
    if len(json.dumps(result,default=str).encode()) > 20_000_000:
        return [], ['The recovery is too large for a safe interactive preview. Use a full save backup.']
    return result, errors


def fingerprint(save, point, changes):
    recovery=state(save)
    value=[recovery.get('id'),recovery.get('status'),epoch(save),save.global_day,configuration(save),
           point.id if point else None,[(x['id'],x['current']) for x in changes]]
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def apply_rollback(session, save, link, changes):
    """Caller holds save lock, has checked preview and made a complete backup."""
    for item in changes:
        row=session.get(Record,item['id']);base=row.version;prior=item['before']
        if prior:
            row.label=prior['label'];row.global_day=prior.get('global_day')
            row.data=copy.deepcopy(prior.get('data') or {});row.deleted=bool(prior.get('deleted'))
        else:
            row.deleted=True
        row.version=base+1
        # Exact restoration, without applying new randomized birth-time defaults.
        session.add(Change(save_id=save.id,device_id='crash-recovery',record_id=row.id,kind=row.kind,
            operation='delete' if row.deleted else 'upsert',base_version=base,new_version=row.version,payload=sync.serialize(row)))
    recovery=state(save)
    save.global_day=recovery['target_day']
    link.game_anchor_day=recovery['restored_minute']//1440;link.tracker_anchor_day=save.global_day
    put_state(save,{**recovery,'status':'awaiting_full','resolution':'rollback','resume_minute':recovery['restored_minute']})
    save.settings={**save.settings,'clock_game_day_high_watermark':link.game_anchor_day}
    save.revision += 1
    # An old roll/automation preview must not apply to restored history.
    for preview in session.scalars(select(ActionPreview).where(ActionPreview.save_id==save.id,ActionPreview.consumed.is_(False))):
        preview.consumed=True
    sync.sync_clock_state(session,save,link)
    sync.ensure_save_metadata(session,save)
