"""Observed labor intervals in game minutes, one per pregnancy (not per baby)."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select

from .models import Record


def point(snapshot):
    """Never substitute tracker Global Day, wall time, or an unknown midnight."""
    keys = ("detected_game_day", "detected_game_hour", "detected_game_minute")
    try:
        values = [snapshot[key] for key in keys]
        if any(isinstance(v, bool) or str(v).strip() != str(int(v)) for v in values):
            return None
        day, hour, minute = map(int, values)
        if day < 0 or not 0 <= hour < 24 or not 0 <= minute < 60:
            return None
        return day * 1440 + hour * 60 + minute
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def transition(prior, snapshot, cycle, epoch):
    now = point(snapshot)
    if now is None:
        return prior
    state = dict(prior or {})
    if state and state.get("cycle") != cycle:
        state = {}
    in_labor = snapshot.get("is_in_labor") is True and snapshot.get("labor_scan_supported") is not False
    pregnant = snapshot.get("is_pregnant")
    restarted = False
    if state and state.get("epoch") != epoch and state.get("status") != "ended":
        # Recovery and branch clocks must never be subtracted across epochs.
        restarted = True
        state = {**state, "status": "interrupted", "minutes": None}
    if state.get("status") == "interrupted" and in_labor and pregnant is not False:
        restarted = True
        state = {}
    if state.get("epoch") == epoch and state.get("start") is not None and now < state["start"]:
        return prior
    if not state and in_labor and pregnant is not False:
        state = {"cycle": cycle, "epoch": epoch, "start": now, "status": "in_progress",
                 "source": "Clock Sync", "evidence": "Estimated from game reports",
                 "signal": str(snapshot.get("labor_signal") or "Game labor flag")[:250]}
        if restarted:
            state["restarted"] = True
    if state.get("status") == "in_progress" and pregnant is False:
        state.update(end=now, minutes=now-state["start"], status="ended",
                     end_evidence="First report showing pregnancy ended")
    return state


def _write(session, save, record, updates):
    from .domain import journal
    if all((record.data or {}).get(k) == v for k, v in updates.items()):
        return
    base = record.version
    record.data = {**record.data, **updates}
    record.version += 1
    journal(session, record, "upsert", base)
    save.revision += 1


def attach(session, save, mother, pregnancy):
    """Also called when a previously pending pregnancy is accepted."""
    state = (mother.data or {}).get("game_labor_tracking") or {}
    if not state or not pregnancy or pregnancy.deleted or pregnancy.kind != "pregnancy":
        return
    data = pregnancy.data or {}
    if (pregnancy.save_id != save.id or data.get("mother_id") != mother.id
            or data.get("infinite_frozen") or data.get("labor_identity_unverified")):
        return
    if state.get("pregnancy_id") and state["pregnancy_id"] != pregnancy.id:
        return
    expected = data.get("game_pregnancy_sequence", data.get("labor_game_cycle"))
    if expected is not None and str(expected) != str(state.get("cycle")):
        return
    state = {**state, "pregnancy_id": pregnancy.id}
    _write(session, save, pregnancy, {"labor_tracking": state, "labor_game_cycle": state["cycle"]})
    _write(session, save, mother, {"game_labor_tracking": state})


def capture(session, save, mother, snapshot):
    from . import crash_recovery
    from .domain import CLOSED_PREGNANCIES
    if (mother.data or {}).get("infinite_frozen"):
        return
    prior = (mother.data or {}).get("game_labor_tracking") or {}
    state = transition(prior, snapshot, int(mother.data.get("game_pregnancy_sequence") or 0), crash_recovery.epoch(save))
    if not state and not prior:
        return
    _write(session, save, mother, {"game_labor_tracking": state})
    if not state:
        return
    linked = session.get(Record, state["pregnancy_id"]) if state.get("pregnancy_id") else None
    if linked:
        attach(session, save, mother, linked)
        return
    rows = list(session.scalars(select(Record).where(Record.save_id == save.id,
        Record.kind == "pregnancy", Record.deleted.is_(False),
        Record.data["mother_id"].as_string() == mother.id)))
    active = [row for row in rows if str(row.data.get("status") or "active").casefold() not in CLOSED_PREGNANCIES]
    if len(active) == 1:
        attach(session, save, mother, active[0])


def clock_label(value):
    if value is None: return "Not observed"
    day, remainder = divmod(int(value), 1440)
    hour, minute = divmod(remainder, 60)
    return f"Game day {day} · {hour:02d}:{minute:02d}"


def duration_label(minutes):
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}h {minutes:02d}m"


def summary(data):
    override = data.get("labor_duration_manual")
    state = data.get("labor_tracking") or {}
    if isinstance(override, dict):
        return duration_label(override["minutes"]) + " · Player-entered"
    if state.get("status") == "ended" and state.get("minutes") is not None:
        return duration_label(state["minutes"]) + " · Estimated"
    if state.get("status") == "in_progress":
        return "In progress · start observed"
    if state.get("status") == "interrupted":
        return "Needs review · game clock changed"
    return "Not recorded"


def fingerprint(data):
    value = [data.get("labor_tracking"), data.get("labor_duration_manual")]
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def manual(form):
    if form.get("labor_mode") == "automatic":
        return None
    values = [str(form.get(key) or "").strip() for key in ("labor_hours", "labor_minutes")]
    if not any(values):
        raise ValueError("Enter the labor length, or choose the observed estimate.")
    if any(value and (not value.isascii() or not value.isdecimal()) for value in values):
        raise ValueError("Enter whole, non-negative hours and minutes.")
    hours, minutes = [int(v or "0") for v in values]
    if hours > 8760 or minutes > 59:
        raise ValueError("Minutes must be 0–59 and hours no more than 8,760.")
    return {"minutes": hours*60+minutes, "source": "Player-entered"}
