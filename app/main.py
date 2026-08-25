from __future__ import annotations

import hashlib
import json
import io
import re
import zipfile
import ipaddress
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.sessions import SessionMiddleware

from . import accounts, auth, automation, backup_service, calendar_utils, clock, clock_bundle, dice, exports, game_metadata, names, notifications, occult_rules, portraits, save_scanner, sync, storyline, insights
from . import domain
from .config import ROOT, settings
from .db import Base, SessionLocal, engine
from .models import BackupSnapshot, Change, ChronicleSave, ClockLink, Conflict, Device, DiceAudit, LegacyWorkspaceCode, Membership, NotificationEvent, NotificationPreference, Portrait, Record, User, Workspace, WorkspaceInvite
from .security import hash_secret, token
from .session_policy import REMEMBER_DEVICE_SECONDS, StaySignedInMiddleware, set_session_mode


FEATURES = {
    "today": ("Today", "Today’s rolls, births, events, illnesses and scheduled deaths"),
    "automation": ("Automation Inbox", "Review births, deaths, moves and relationships detected in game"),
    "sims": ("Sims", "People, life stages, portraits and family details"),
    "households": ("Households", "Residences, branches, class and rotation"),
    "relationships": ("Relationships", "Marriages, partners and couple portraits"),
    "pregnancies": ("Pregnancies", "Pregnancy timelines, outcomes and newborn scheduling"),
    "rolls": ("Rolls", "Automatic obligations, outcomes and audited dice"),
    "events": ("Events", "Historical events, eligibility and effects"),
    "illnesses": ("Illnesses", "Disease, severity, treatment and outcomes"),
    "family-tree": ("Family Tree", "Ancestors, descendants and dynasty lines"),
    "timeline": ("Chronicle", "A narrative history of the save"),
    "planner": ("Play Planner", "Household rotations, family plans and forecasts"),
    "challenge": ("Challenge Management", "Succession, matchmaking and campaigns"),
    "statistics": ("Statistics", "Population, survival, fertility and records"),
    "notes": ("Notes", "A private notebook for this save"),
    "plants": ("Planting Reference", "Historical crops by year, season and location"),
    "names": ("Name Generator", "Offline names from your own sourced historical libraries"),
    "guides": ("Challenge Guides", "SeveralUDO and MorbidGamer references"),
    "rules": ("Rules & Data", "Editable defaults, eras, dice and causes of death"),
    "health": ("Rules Health", "Coverage, duplicates and maintenance checks"),
    "clock": ("Game Clock", "Local or hosted Sims 4 time and population receiver"),
    "sync": ("Sync", "Desktop/cloud status, devices and conflict review"),
    "dice-audit": ("Dice Audit", "Verifiable history and distribution reports"),
    "storyline": ("Storyline", "A living narrative generated from the changing save"),
    "saves": ("Saves & Backups", "Create, rename, duplicate, export and restore chronicles"),
    "account": ("Account & Sharing", "Google sign-in, shared workspaces and notifications"),
    "support": ("About & Support", "Version, help, privacy and project support"),
}

KIND_BY_PAGE = {
    "sims": "sim", "households": "household", "relationships": "relationship",
    "pregnancies": "pregnancy", "rolls": "roll", "events": "event",
    "illnesses": "illness", "notes": "note", "planner": "play_rotation", "automation": "game_candidate",
    "challenge": "campaign", "plants": "plant", "rules": "era_rule",
}

app = FastAPI(title="Decades Tracker", version="4.2.3")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, max_age=REMEMBER_DEVICE_SECONDS, same_site="lax", https_only=not settings.local_mode)
app.add_middleware(StaySignedInMiddleware, persistent_max_age=REMEMBER_DEVICE_SECONDS)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "app" / "templates")
_SAVE_SCAN_CACHE: dict[str, dict] = {}
_TODAY_SCHEDULE_CHECKED: dict[str, tuple[int, int]] = {}


def historical_year(save: ChronicleSave, global_day: int | None) -> str:
    if not save or global_day is None: return "Unknown year"
    return str(save.start_year + (int(global_day) - 1) // save.days_per_year)


def historical_period(save: ChronicleSave, global_day: int | None) -> str:
    if not save or global_day is None: return "Date unknown"
    day = int(global_day)
    year = save.start_year + (day - 1) // save.days_per_year
    part = ((day - 1) % save.days_per_year) + 1
    return f"Year {year} · challenge day {part}/{save.days_per_year}"


def sim_weekday(game_day: int | None) -> str:
    if game_day is None: return "Waiting for game"
    return ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")[int(game_day) % 7]


def tracker_weekday(global_day: int | None) -> str:
    if global_day is None: return "Unknown"
    return ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")[(int(global_day) - 1) % 7]


def challenge_date_label(save: ChronicleSave, global_day: int | None) -> str:
    if not save or global_day is None: return "Date unknown"
    year = save.start_year + (int(global_day) - 1) // save.days_per_year
    day = ((int(global_day) - 1) % save.days_per_year) + 1
    if save.days_per_year == 4:
        ranges = ("Jan 1–Mar 31", "Apr 1–Jun 30", "Jul 1–Sep 30", "Oct 1–Dec 31")
        return f"{ranges[day - 1]}, {year}"
    return f"Year {year}, challenge day {day}"


def event_calendar_fields(save: ChronicleSave, event: str, global_day: int | None, hour, minute) -> dict:
    day = int_or_none(global_day)
    if day is None:
        return {}
    result = {f"historical_{event}_date_range": calendar_utils.date_range_label(day, save.start_year, save.days_per_year)}
    parsed_hour, parsed_minute = int_or_none(hour), int_or_none(minute)
    if parsed_hour is None or parsed_minute is None:
        result[f"{event}_date_precision"] = "challenge-day-only"
        return result
    parsed_hour=max(0,min(23,parsed_hour));parsed_minute=max(0,min(59,parsed_minute))
    result.update({f"{event}_game_hour":parsed_hour,f"{event}_game_minute":parsed_minute,f"{event}_time":f"{parsed_hour:02d}:{parsed_minute:02d}"})
    exact=calendar_utils.exact_historical_label(day,parsed_hour,parsed_minute,save.start_year,save.days_per_year)
    if exact:
        result.update({f"historical_{event}_date":exact,f"{event}_date_precision":"exact"})
    else:
        result[f"{event}_date_precision"]="clock-time-no-calendar-map"
    return result


def birth_calendar_fields(save: ChronicleSave, global_day: int | None, hour, minute) -> dict:
    return event_calendar_fields(save, "birth", global_day, hour, minute)


def resolve_birth_input(save: ChronicleSave, global_day, birth_year, hour=None, minute=None) -> tuple[int | None, dict]:
    """Resolve an exact tracker day or an explicitly approximate historical year."""
    year = int_or_none(birth_year)
    if year is not None:
        first, last = calendar_utils.global_day_range_for_year(year, save.start_year, save.days_per_year)
        representative = calendar_utils.representative_global_day_for_year(year, save.start_year, save.days_per_year)
        return representative, {
            "birth_year": year,
            "birth_year_only": True,
            "birth_global_day_estimated": True,
            "birth_estimate_precision": "historical-year-only",
            "birth_estimate_source": f"Historical birth year {year}; midpoint Global Day used for scheduling",
            "original_birth_estimate_global_day": representative,
            "estimated_birth_global_day_range_start": first,
            "estimated_birth_global_day_range_end": last,
            "historical_birth_date_range": f"{year} (exact date unknown)",
            "birth_date_precision": "historical-year-only",
        }
    day = int_or_none(global_day)
    return day, birth_calendar_fields(save, day, hour, minute)


def death_calendar_fields(save: ChronicleSave, global_day: int | None, hour, minute) -> dict:
    return event_calendar_fields(save, "death", global_day, hour, minute)


def marriage_calendar_fields(save: ChronicleSave, global_day: int | None, hour, minute) -> dict:
    return event_calendar_fields(save, "marriage", global_day, hour, minute)


def sim_birth_display(save: ChronicleSave, record: Record) -> str:
    data=record.data or {}
    if data.get("birth_year_only") and data.get("birth_year") is not None:
        return f"{data.get('birth_year')} (exact date unknown)"
    return str(data.get("historical_birth_date") or data.get("historical_birth_date_range") or challenge_date_label(save,data.get("birth_global_day")))


def sim_death_display(save: ChronicleSave, record: Record) -> str:
    data=record.data or {}
    return str(data.get("historical_death_date") or data.get("historical_death_date_range") or challenge_date_label(save,data.get("death_global_day")))


def relationship_date_display(save: ChronicleSave, record: Record) -> str:
    data=record.data or {}
    day=data.get("marriage_global_day",data.get("start_global_day",record.global_day))
    return str(data.get("historical_marriage_date") or data.get("historical_marriage_date_range") or challenge_date_label(save,day))


def exact_date_from_time(save: ChronicleSave, global_day, hour, minute) -> str:
    if not save or int_or_none(global_day) is None or int_or_none(hour) is None or int_or_none(minute) is None:
        return "Exact date unavailable"
    return calendar_utils.exact_historical_label(int(global_day),int(hour),int(minute),save.start_year,save.days_per_year) or "Exact conversion requires four challenge days per year"


templates.env.globals.update(historical_year=historical_year, historical_period=historical_period, sim_weekday=sim_weekday, tracker_weekday=tracker_weekday, challenge_date_label=challenge_date_label, sim_birth_display=sim_birth_display, sim_death_display=sim_death_display, relationship_date_display=relationship_date_display, exact_date_from_time=exact_date_from_time)


def record_snapshot(record: Record) -> dict:
    return {"id": record.id, "label": record.label, "global_day": record.global_day, "data": dict(record.data or {}), "version": record.version, "deleted": record.deleted}


def set_today_undo(request: Request, label: str, records=(), delete_ids=(), save_global_day=None) -> None:
    request.session["today_undo"] = {"label": label, "records": [record_snapshot(record) for record in records], "delete_ids": list(delete_ids), "save_global_day": save_global_day}


def int_or_none(value) -> int | None:
    try: return int(str(value).strip()) if value not in (None, "") else None
    except (TypeError, ValueError): return None


def concrete_rule_die(rule: Record) -> str:
    """Return a rollable die for any rule-family record, or an empty string."""
    data=rule.data or {};configured=str(data.get("die") or data.get("configured_die") or "").strip()
    try: dice.parse(configured)
    except ValueError: return ""
    return configured.lower().replace(" ","")


def rule_record_key(rule: Record) -> str:
    data=rule.data or {}
    return str(data.get("rule_key") or data.get("key") or rule.id)


def roll_rule_key(roll: Record) -> str:
    data=roll.data or {}
    return str(data.get("source_rule_key") or data.get("occult_rule_key") or data.get("rule_key") or "")


def rule_can_follow(origin: Record, rule: Record) -> bool:
    """Support built-in occult chains and future rules declaring `triggered_by`."""
    parent_key=roll_rule_key(origin);child_data=rule.data or {};child_key=rule_record_key(rule)
    declared=child_data.get("triggered_by") or []
    if isinstance(declared,str): declared=[part.strip() for part in re.split(r"[,;|]+",declared) if part.strip()]
    return parent_key in {str(value) for value in declared} or child_key in occult_rules.follow_up_keys(parent_key)


def create_rule_roll_record(session, save: ChronicleSave, rule: Record, sim: Record, due: int,
                            *, origin: Record | None = None, context_note: str = "") -> tuple[Record,bool]:
    """Create one normalized Today-workbench roll from any concrete rule record."""
    rule_data=rule.data or {};die=concrete_rule_die(rule)
    if not die: raise ValueError("This rule does not have a concrete supported die.")
    key=rule_record_key(rule);occult=rule.kind=="occult_rule"
    lethal=str(rule_data.get("lethal_results") or (occult_rules.lethal_results(key) if occult else "")).strip()
    allow_after_death=bool(rule_data.get("allow_after_death")) or key.startswith("spellcaster_resurrection") or (str(rule_data.get("occult") or "")=="Ghost" and key!="ghost_haunting_death")
    source=(f"rule-workbench:{origin.id}:{rule.id}:{sim.id}" if origin else f"rule-workbench:{rule.id}:{sim.id}:{token(8)}")
    existing=session.scalar(select(Record).where(
        Record.save_id==save.id,Record.kind=="roll",Record.deleted.is_(False),
        Record.data["source"].as_string()==source,
    ).limit(1))
    if existing: return existing,False
    result_rules=str(rule_data.get("result_rules") or rule_data.get("bad_results") or rule_data.get("rules") or "").strip()
    trigger_results=str(rule_data.get("trigger_results") or "").strip()
    payload={
        "sim_id":sim.id,"sim_name":sim.label,"source_id":rule.id,"source_rule_id":rule.id,
        "source_rule_kind":rule.kind,"source_rule_key":key,"rule_generated":True,
        "rule_family":str(rule_data.get("rule_family") or rule_data.get("occult") or rule.kind.replace("_"," ").title()),
        "roll_type":rule.label,"die":die,"bad_results":lethal,"trigger_results":trigger_results,
        "result_rules":result_rules,"nonlethal":not bool(lethal),"failure_is_lethal":bool(lethal),
        "source":source,"due_global_day":int(due),"completed":False,
        "origin_roll_id":origin.id if origin else None,"rule_context":context_note.strip()[:500],
        "notes":str(rule_data.get("notes") or ""),"allow_after_death":allow_after_death,
    }
    if occult:
        payload.update({
            "occult_roll":True,"occult_rule_id":rule.id,"occult_rule_key":key,
            "occult_type":rule_data.get("occult"),"occult_effect":rule_data.get("occult_effect"),
        })
    roll=Record(save_id=save.id,kind="roll",label=f"{sim.label} — {rule.label}",global_day=int(due),data=payload)
    session.add(roll);session.flush();domain.journal(session,roll,"upsert",0)
    return roll,True


def detected_labels(value) -> list[str]:
    """Render old and new Clock Sync collection formats consistently."""
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        value = [{"name": key, "value": item} for key, item in value.items()]
    elif not isinstance(value, (list, tuple, set)):
        value = [value]
    labels = []
    for item in value:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("display_name") or item.get("title") or item.get("trait") or item.get("skill") or "").strip()
            level = item.get("level", item.get("value"))
            label = f"{name} (level {level})" if name and level not in (None, "") else name
        else:
            label = str(item).strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def detected_form_list(value) -> list[str]:
    if isinstance(value, (dict, list, tuple, set)):
        return detected_labels(value)
    text = str(value or "").replace("\r", "\n")
    entries = []
    for line in text.split("\n"):
        for part in line.split(";"):
            label = part.strip()
            if label and label not in entries:
                entries.append(label)
    return entries


def next_sim_number(session, save_id: str) -> str:
    """Allocate the next stable display ID, including archived Sims."""
    import re
    sims = session.scalars(select(Record).where(Record.save_id == save_id, Record.kind == "sim"))
    numbers = []
    for sim in sims:
        value = str((sim.data or {}).get("sim_number") or (sim.data or {}).get("legacy_id") or "")
        match = re.search(r"(\d+)$", value)
        if match:
            numbers.append(int(match.group(1)))
    return f"SIM-{max(numbers, default=0) + 1:04d}"


def assign_household_members(session, save: ChronicleSave, household: Record, member_ids: list[str], *, include_head: bool = True) -> int:
    """Make the submitted membership list authoritative for one household."""
    selected = {str(value) for value in member_ids if value}
    head_id = str((household.data or {}).get("head_sim_id") or "")
    if head_id and include_head:
        selected.add(head_id)
    sims = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False)
    )))
    selected &= {sim.id for sim in sims}
    changed = 0
    for sim in sims:
        current = (sim.data or {}).get("current_household_id")
        desired = household.id if sim.id in selected else (None if current == household.id else current)
        if desired == current:
            continue
        base = sim.version
        sim.data = {**(sim.data or {}), "current_household_id": desired}
        sim.version += 1
        domain.journal(session, sim, "upsert", base)
        changed += 1
    save.revision += changed
    return changed


BOOL_FIELDS = {"active", "roll_required", "legally_married", "contagious", "maternal_rolls_required", "newborn_rolls_required", "pinned", "include_in_tree", "auto_schedule"}
INT_FIELDS = {
    "start_global_day", "end_global_day", "conception_global_day", "due_global_day", "delivery_global_day",
    "birth_global_day", "death_global_day", "global_day", "start_year", "end_year", "age_days", "min_age_days",
    "max_age_days", "max_babies", "target_children", "min_birth_spacing_days", "children_count", "babies_expected", "babies_delivered",
}


def structured_form_data(form) -> dict:
    data = {}
    for key, value in form.multi_items():
        if key in {"label", "name", "title", "global_day", "return_to", "confirm"}:
            continue
        if key in BOOL_FIELDS:
            data[key] = str(value).casefold() in {"1", "true", "yes", "on"}
        elif key in INT_FIELDS:
            data[key] = int_or_none(value)
        elif key == "causes":
            data[key] = [line.strip() for line in str(value).splitlines() if line.strip()]
        else:
            data[key] = str(value).strip()
    return data


def sim_status(record: Record, save: ChronicleSave) -> str:
    death = int_or_none((record.data or {}).get("death_global_day"))
    if death is None: return "Alive"
    return "Deceased" if death <= save.global_day else "Alive · death scheduled"


templates.env.globals.update(sim_status=sim_status, detected_labels=detected_labels, trait_labels=game_metadata.readable_trait_labels)


@contextmanager
def db():
    session = SessionLocal()
    try:
        yield session
        metadata_saves={item for item in list(session.new)+list(session.dirty) if isinstance(item,ChronicleSave)}
        for changed_save in metadata_saves:
            sync.ensure_save_metadata(session,changed_save)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(engine)
    if settings.local_mode:
        with db() as session:
            user = session.scalar(select(User).where(User.email == "local@decades.invalid"))
            if not user:
                user = User(email="local@decades.invalid", display_name="Local Player")
                workspace = Workspace(name="Local Chronicle")
                session.add_all([user, workspace]); session.flush()
                session.add(Membership(user_id=user.id, workspace_id=workspace.id))
                save = ChronicleSave(workspace_id=workspace.id, name="My Decades Challenge")
                session.add(save); session.flush(); domain.seed_defaults(session, save)
            for existing_save in session.scalars(select(ChronicleSave)):
                if not settings.skip_startup_migrations and str((existing_save.settings or {}).get("defaults_schema_version") or "")!="4.1.1":
                    domain.seed_defaults(session,existing_save)
                domain.backfill_pregnancy_allowances(session,existing_save)
                existing_save.revision += domain.backfill_married_surnames(session,existing_save)
        from .sync_client import start
        start()
    if settings.automatic_snapshots:
        backup_service.start()
    notifications.start()


def signed_in(request: Request, session):
    if settings.local_mode:
        return session.scalar(select(User).where(User.email == "local@decades.invalid"))
    return auth.current_user(request, session)


def context(request: Request, session, **extra):
    user = signed_in(request, session)
    saves = []
    active = None
    if user:
        memberships = select(Membership.workspace_id).where(Membership.user_id == user.id)
        saves = list(session.scalars(select(ChronicleSave).where(ChronicleSave.workspace_id.in_(memberships)).order_by(ChronicleSave.updated_at.desc())))
        requested = request.session.get("save_id")
        active = next((item for item in saves if item.id == requested), saves[0] if saves else None)
        if active:
            request.session["save_id"] = active.id
    last_roll = request.session.pop("last_roll", None)
    return {"request": request, "user": user, "saves": saves, "save": active,
            "save_settings": dict(active.settings or {}) if active else {},
            "features": FEATURES, "local_mode": settings.local_mode, "google_enabled": settings.google_enabled, "last_roll": last_roll,
            "occult_notice": request.session.pop("occult_notice", None),
            "app_version": app.version, "notification_cursor": datetime.now(timezone.utc).isoformat(), **extra}


def owned_save(request: Request, session, save_id: str) -> ChronicleSave:
    user = signed_in(request, session)
    if not user: raise HTTPException(401)
    workspaces = select(Membership.workspace_id).where(Membership.user_id == user.id)
    save = session.scalar(select(ChronicleSave).where(ChronicleSave.id == save_id, ChronicleSave.workspace_id.in_(workspaces)))
    if not save: raise HTTPException(404)
    return save


def hidden_event_ids_for(session, save_id: str) -> set[str]:
    return {
        item.id for item in session.scalars(select(Record).where(
            Record.save_id == save_id, Record.kind == "event", Record.deleted.is_(False),
        )) if domain.event_is_ignored(item)
    }


def active_workspace(request: Request, session) -> tuple[User, Workspace]:
    ctx = context(request, session)
    user, save = ctx.get("user"), ctx.get("save")
    if not user or not save:
        raise HTTPException(400, "Open a save first.")
    workspace = session.get(Workspace, save.workspace_id)
    if not workspace:
        raise HTTPException(404)
    return user, workspace


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with db() as session:
        ctx = context(request, session)
        if not ctx["user"]:
            return templates.TemplateResponse(request, "login.html", ctx)
        save = ctx["save"]
        if save:
            ctx["automation_pending"] = session.scalar(select(func.count()).select_from(Record).where(Record.save_id==save.id,Record.kind=="game_candidate",Record.deleted.is_(False),Record.data["status"].as_string()=="pending")) or 0
        counts = {}
        if save:
            counts = dict(session.execute(select(Record.kind, func.count()).where(Record.save_id == save.id, Record.deleted.is_(False)).group_by(Record.kind)).all())
        return templates.TemplateResponse(request, "dashboard.html", {**ctx, "counts": counts})


@app.post("/auth/register")
def register_workspace(
    request: Request,
    email: str = Form(...),
    display_name: str = Form(""),
    workspace_name: str = Form(""),
    save_name: str = Form(""),
    start_year: int = Form(1300),
    stay_signed_in: str = Form(""),
):
    """Create a private hosted workspace without requiring a legacy code."""
    if settings.local_mode:
        raise HTTPException(404)
    try:
        with db() as session:
            user, _workspace, save, recovery_code = accounts.create_recovery_workspace(
                session,
                email=email,
                display_name=display_name,
                workspace_name=workspace_name,
                save_name=save_name,
                start_year=start_year,
            )
            domain.seed_defaults(session, save)
            request.session.clear()
            request.session["user_id"] = user.id
            request.session["save_id"] = save.id
            request.session["new_recovery_code"] = recovery_code
            set_session_mode(request, stay_signed_in)
    except ValueError as exc:
        with db() as session:
            return templates.TemplateResponse(
                request,
                "login.html",
                context(request, session, error=str(exc)),
                status_code=400,
            )
    except SQLAlchemyError as exc:
        with db() as session:
            return templates.TemplateResponse(
                request,
                "login.html",
                context(request, session, error=accounts.legacy_database_error_message(exc)),
                status_code=503,
            )
    return RedirectResponse("/", status_code=303)


@app.get("/auth/google")
async def google_login(request: Request, stay_signed_in: str = ""):
    if not settings.google_enabled:
        raise HTTPException(503, "Google sign-in has not been configured by the deployment owner.")
    set_session_mode(request, stay_signed_in)
    return await auth.oauth.google.authorize_redirect(request, f"{settings.public_url}/auth/google/callback")


@app.get("/workspace/invitations/accept")
def accept_workspace_invitation(request: Request, token: str):
    with db() as session:
        invite = accounts.invitation_for_token(session, token)
        if not invite:
            raise HTTPException(400, "That invitation is invalid, expired, or already used.")
        user = signed_in(request, session)
        if not user:
            request.session["workspace_invitation_id"] = invite.id
            if settings.google_enabled:
                return RedirectResponse("/auth/google", status_code=303)
            raise HTTPException(503, "Google sign-in must be configured before this invitation can be accepted.")
        try:
            membership = accounts.accept_invitation(session, user, token)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        first_save = session.scalar(select(ChronicleSave).where(
            ChronicleSave.workspace_id == membership.workspace_id,
        ).order_by(ChronicleSave.updated_at.desc()))
        if first_save:
            request.session["save_id"] = first_save.id
        request.session["account_notice"] = "Workspace invitation accepted."
    return RedirectResponse("/p/account", status_code=303)


@app.get("/auth/google/callback")
async def google_callback(request: Request):
    oauth_token = await auth.oauth.google.authorize_access_token(request)
    claims = oauth_token.get("userinfo") or await auth.oauth.google.parse_id_token(request, oauth_token)
    with db() as session:
        user, _workspace, recovery = auth.provision_google_user(session, claims)
        request.session["user_id"] = user.id
        accounts.auto_claim_linked_email(session, user)
        pending = request.session.pop("workspace_invitation_id", "")
        if pending:
            try:
                invite = session.get(WorkspaceInvite, pending)
                if not invite:
                    raise ValueError("That invitation is invalid or expired.")
                membership = accounts.accept_invitation_record(session, user, invite)
                first_save = session.scalar(select(ChronicleSave).where(ChronicleSave.workspace_id == membership.workspace_id).order_by(ChronicleSave.updated_at.desc()))
                if first_save:
                    request.session["save_id"] = first_save.id
                request.session["account_notice"] = "Workspace invitation accepted."
            except ValueError as exc:
                request.session["account_notice"] = str(exc)
        if recovery:
            request.session["new_recovery_code"] = recovery
        opened = session.scalar(select(ChronicleSave).where(
            ChronicleSave.workspace_id.in_(select(Membership.workspace_id).where(Membership.user_id == user.id)),
        ).order_by(ChronicleSave.updated_at.desc()))
        if not opened:
            membership = session.scalar(select(Membership).where(Membership.user_id == user.id).order_by(Membership.role.desc()))
            opened = ChronicleSave(workspace_id=membership.workspace_id, name="My Decades Challenge")
            session.add(opened);session.flush();domain.seed_defaults(session,opened)
        request.session["save_id"] = opened.id
    return RedirectResponse("/", status_code=303)


@app.post("/auth/recover")
def recover(request: Request, email: str = Form(...), recovery_code: str = Form(...), stay_signed_in: str = Form("")):
    with db() as session:
        user = auth.recover_user(session, email, recovery_code)
        if not user:
            return templates.TemplateResponse(request, "login.html", context(request, session, error="Email and recovery key did not match."), status_code=401)
        request.session.clear()
        request.session["user_id"] = user.id
        set_session_mode(request, stay_signed_in)
    return RedirectResponse("/", status_code=303)


@app.post("/auth/legacy")
def legacy_workspace_login(request: Request, email: str = Form(...), workspace_code: str = Form(...), stay_signed_in: str = Form("")):
    """First hosted 4.x sign-in for an existing 3.x workspace.

    The raw code is matched only by its one-way hash. The user record is keyed
    by email so a later Google sign-in with the same address upgrades this
    identity instead of creating another owner.
    """
    if settings.local_mode:
        raise HTTPException(404)
    normalized_email = email.strip().casefold()
    if "@" not in normalized_email or normalized_email.startswith("@") or normalized_email.endswith("@"):
        error = "Enter the email address you want connected to this workspace."
        with db() as session:
            return templates.TemplateResponse(request, "login.html", context(request, session, error=error), status_code=400)
    try:
        with db() as session:
            with session.begin_nested():
                user = session.scalar(select(User).where(User.email == normalized_email))
                if user is None:
                    user = User(
                        email=normalized_email,
                        display_name=normalized_email.split("@", 1)[0],
                    )
                    session.add(user)
                    session.flush()
                workspace, imported = accounts.claim_legacy_code(session, user, workspace_code)
            request.session["user_id"] = user.id
            opened = session.scalar(select(ChronicleSave).where(
                ChronicleSave.workspace_id == workspace.id,
            ).order_by(ChronicleSave.updated_at.desc()))
            if opened:
                request.session["save_id"] = opened.id
            request.session["account_notice"] = (
                f"Connected {len(imported)} existing save{'s' if len(imported) != 1 else ''}. "
                "Use this same email when Google sign-in is enabled."
            )
            set_session_mode(request, stay_signed_in)
    except ValueError as exc:
        with db() as session:
            return templates.TemplateResponse(request, "login.html", context(request, session, error=str(exc)), status_code=400)
    except SQLAlchemyError as exc:
        with db() as session:
            return templates.TemplateResponse(
                request,
                "login.html",
                context(request, session, error=accounts.legacy_database_error_message(exc)),
                status_code=503,
            )
    return RedirectResponse("/", status_code=303)


@app.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.post("/workspace/invitations")
def create_workspace_invitation(request: Request, email: str = Form(...), role: str = Form("editor")):
    with db() as session:
        user, workspace = active_workspace(request, session)
        try:
            _invite, raw = accounts.create_invite(session, workspace.id, user, email, role)
        except (PermissionError, ValueError) as exc:
            raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc
        request.session["invitation_link"] = f"{settings.public_url}/workspace/invitations/accept?token={raw}"
        request.session["account_notice"] = "Invitation created. Copy the private link below; it is shown once."
    return RedirectResponse("/p/account", status_code=303)


@app.post("/workspace/invitations/{invite_id}/revoke")
def revoke_workspace_invitation(request: Request, invite_id: str):
    with db() as session:
        user, workspace = active_workspace(request, session)
        try:
            accounts.require_owner(session, user.id, workspace.id)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        invite = session.get(WorkspaceInvite, invite_id)
        if not invite or invite.workspace_id != workspace.id:
            raise HTTPException(404)
        invite.revoked = True
    return RedirectResponse("/p/account", status_code=303)


@app.post("/workspace/members/{member_user_id}/remove")
def remove_workspace_member(request: Request, member_user_id: str):
    with db() as session:
        user, workspace = active_workspace(request, session)
        try:
            accounts.require_owner(session, user.id, workspace.id)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        membership = session.get(Membership, {"user_id": member_user_id, "workspace_id": workspace.id})
        if not membership:
            raise HTTPException(404)
        if membership.role == "owner":
            raise HTTPException(400, "The workspace owner cannot be removed.")
        session.delete(membership)
    return RedirectResponse("/p/account", status_code=303)


@app.post("/workspace/legacy/register")
def register_legacy_workspace_code(request: Request, workspace_code: str = Form(...), label: str = Form("Legacy workspace")):
    with db() as session:
        user, workspace = active_workspace(request, session)
        try:
            accounts.register_legacy_code(session, workspace.id, user, workspace_code, label)
        except (PermissionError, ValueError) as exc:
            raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc
        request.session["account_notice"] = "The old workspace code is now linked by a one-way hash. The code itself was not stored."
    return RedirectResponse("/p/account", status_code=303)


@app.post("/workspace/legacy/claim")
def claim_legacy_workspace_code(request: Request, workspace_code: str = Form(...), neon_database_url: str = Form("")):
    with db() as session:
        user = signed_in(request, session)
        if not user:
            raise HTTPException(401)
        ctx = context(request, session)
        preferred = session.get(Workspace, ctx["save"].workspace_id) if ctx.get("save") else None
        try:
            workspace, imported = accounts.claim_legacy_code(
                session, user, workspace_code,
                neon_database_url.strip() if settings.local_mode else "",
                preferred,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        save = session.scalar(select(ChronicleSave).where(ChronicleSave.workspace_id == workspace.id).order_by(ChronicleSave.updated_at.desc()))
        if save:
            request.session["save_id"] = save.id
        changed = sum(int(item.get("records") or 0) for item in imported)
        request.session["account_notice"] = (
            f"Legacy workspace connected. {len(imported)} save(s) were checked and {changed:,} missing records were safely copied. "
            "Existing records and the original Neon schemas were left unchanged."
            if imported else
            "Legacy workspace linked to this account. Its converted saves are already current."
        )
    return RedirectResponse("/p/account", status_code=303)


def _safe_webhook_url(value: str) -> str:
    url = value.strip()
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Webhook addresses must use a public https:// URL.")
    if parsed.hostname.casefold() in {"localhost", "localhost.localdomain"}:
        raise ValueError("Local webhook addresses are not allowed.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
        if not address.is_global:
            raise ValueError("Private-network webhook addresses are not allowed.")
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise
    return url


@app.post("/account/notifications")
def save_notification_preferences(request: Request, browser_enabled: str = Form(""), email_enabled: str = Form(""), webhook_enabled: str = Form(""), webhook_url: str = Form(""), categories: list[str] = Form(default=[])):
    with db() as session:
        user, workspace = active_workspace(request, session)
        try:
            safe_webhook = _safe_webhook_url(webhook_url)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        pref = notifications.preference(session, user.id, workspace.id)
        pref.browser_enabled = browser_enabled in {"1", "on", "true", "yes"}
        pref.email_enabled = email_enabled in {"1", "on", "true", "yes"}
        pref.webhook_enabled = webhook_enabled in {"1", "on", "true", "yes"}
        pref.webhook_url = safe_webhook
        pref.categories = [value for value in categories if value in notifications.DEFAULT_CATEGORIES]
        request.session["account_notice"] = "Notification preferences saved."
    return RedirectResponse("/p/account", status_code=303)


@app.post("/account/portraits")
def save_portrait_provider(request: Request, provider: str = Form(...), comfyui_url: str = Form(""), openai_api_key: str = Form(""), openai_image_model: str = Form("")):
    if not settings.local_mode:
        raise HTTPException(400, "Hosted portrait settings are controlled by private deployment variables.")
    try:
        portraits.save_local_config(provider, comfyui_url, openai_api_key, openai_image_model)
        request.session["portrait_notice"] = "Portrait provider saved."
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse("/p/account", status_code=303)


@app.post("/account/portraits/test")
def test_portrait_provider(request: Request):
    result = portraits.test_provider()
    request.session["portrait_notice"] = result["message"]
    return RedirectResponse("/p/account", status_code=303)


@app.get("/api/notifications")
def notification_feed(request: Request, after: str = ""):
    with db() as session:
        ctx = context(request, session)
        user, save = ctx.get("user"), ctx.get("save")
        if not user or not save:
            raise HTTPException(401)
        pref = notifications.preference(session, user.id, save.workspace_id)
        if not pref.browser_enabled:
            return {"events": [], "cursor": datetime.now(timezone.utc).isoformat()}
        parsed = None
        if after:
            try:
                parsed = datetime.fromisoformat(after.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                parsed = datetime.now(timezone.utc)
        rows = notifications.recent(session, save.workspace_id, save.id, parsed)
        categories = set(pref.categories or notifications.DEFAULT_CATEGORIES)
        rows = [row for row in rows if not categories or row.category in categories]
        return {
            "events": [{"id": row.id, "category": row.category, "title": row.title, "body": row.body, "url": row.target_url, "created_at": row.created_at.isoformat()} for row in rows],
            "cursor": (rows[-1].created_at if rows else datetime.now(timezone.utc)).isoformat(),
        }


@app.post("/saves/select")
def select_save(request: Request, save_id: str = Form(...)):
    with db() as session:
        save=owned_save(request, session, save_id);save.updated_at=datetime.now(timezone.utc)
    request.session["save_id"] = save_id
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


@app.post("/saves")
def create_save(request: Request, name: str = Form(...), start_year: int = Form(1300), days_per_year: int = Form(4), pregnancy_days: int = Form(4)):
    with db() as session:
        user = signed_in(request, session)
        if not user: raise HTTPException(401)
        membership = session.scalar(select(Membership).where(Membership.user_id == user.id))
        save = ChronicleSave(workspace_id=membership.workspace_id, name=name.strip() or "New Challenge", start_year=start_year, days_per_year=max(1,days_per_year), pregnancy_days=max(1,pregnancy_days))
        session.add(save); session.flush(); domain.seed_defaults(session, save); request.session["save_id"] = save.id
    return RedirectResponse("/", status_code=303)


def remap_payload(value, mapping):
    if isinstance(value, dict): return {key: remap_payload(item, mapping) for key, item in value.items()}
    if isinstance(value, list): return [remap_payload(item, mapping) for item in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def public_save_settings(values: dict) -> dict:
    blocked=("token","secret","password","database_url","api_key","connection")
    return {key:value for key,value in dict(values or {}).items() if not any(part in key.casefold() for part in blocked)}


@app.post("/saves/{save_id}/rename")
def rename_save(request: Request, save_id: str, name: str = Form(...)):
    with db() as session:
        save=owned_save(request,session,save_id);save.name=name.strip() or save.name;save.updated_at=datetime.now(timezone.utc)
    return RedirectResponse("/p/saves",status_code=303)


@app.post("/saves/{save_id}/duplicate")
def duplicate_save(request: Request, save_id: str, name: str = Form("")):
    with db() as session:
        original=owned_save(request,session,save_id)
        clone=ChronicleSave(workspace_id=original.workspace_id,name=name.strip() or f"{original.name} — Copy",global_day=original.global_day,start_year=original.start_year,days_per_year=original.days_per_year,pregnancy_days=original.pregnancy_days,settings=dict(original.settings or {}))
        session.add(clone);session.flush();source=list(session.scalars(select(Record).where(Record.save_id==original.id)));mapping={item.id:__import__('uuid').uuid4().hex for item in source}
        for item in source:
            copied=Record(id=mapping[item.id],save_id=clone.id,kind=item.kind,label=item.label,global_day=item.global_day,data=remap_payload(item.data or {},mapping),version=1,deleted=item.deleted);session.add(copied);session.flush();domain.journal(session,copied,"upsert",0)
        for portrait in session.scalars(select(Portrait).where(Portrait.save_id==original.id)):
            if portrait.record_id in mapping: session.add(Portrait(save_id=clone.id,record_id=mapping[portrait.record_id],stage=portrait.stage,mime_type=portrait.mime_type,image=portrait.image,source="save-duplicate"))
        clone.revision=len(source);request.session["save_id"]=clone.id
    return RedirectResponse("/p/saves",status_code=303)


@app.post("/saves/{save_id}/delete")
def delete_save(request: Request, save_id: str, confirm: str = Form(...)):
    with db() as session:
        save=owned_save(request,session,save_id)
        if confirm.strip()!=save.name: raise HTTPException(400,"Type the save name exactly to delete it")
        session.delete(save);request.session.pop("save_id",None)
    return RedirectResponse("/",status_code=303)


@app.get("/saves/{save_id}/export")
def export_save(request: Request, save_id: str):
    with db() as session:
        save=owned_save(request,session,save_id);raw=backup_service.build_package(session,save);filename=exports.safe_filename(save.name)
        return Response(raw,media_type="application/zip",headers={"Content-Disposition":f'attachment; filename="{filename}.decades-save"'})


@app.post("/saves/import")
async def import_save(request: Request, package: UploadFile):
    raw=await package.read()
    try:
        with db() as session:
            user=signed_in(request,session)
            if not user: raise HTTPException(401)
            membership=session.scalar(select(Membership).where(Membership.user_id==user.id))
            save=backup_service.restore_as_copy(session,membership.workspace_id,raw,"Imported")
            request.session["save_id"]=save.id
    except (KeyError,ValueError,zipfile.BadZipFile,json.JSONDecodeError,OSError) as exc:
        raise HTTPException(400,f"Invalid save package: {exc}") from exc
    return RedirectResponse("/p/saves",status_code=303)


@app.post("/saves/{save_id}/snapshot")
def snapshot_save(request: Request, save_id: str):
    with db() as session:
        save=owned_save(request,session,save_id)
        backup_service.create_snapshot(session,save,"manual",force=True)
        request.session["backup_notice"]="A restorable snapshot was saved."
    return RedirectResponse("/p/saves",status_code=303)


@app.get("/backups/{snapshot_id}/download")
def download_snapshot(request: Request, snapshot_id: str):
    with db() as session:
        snapshot=session.get(BackupSnapshot,snapshot_id)
        if not snapshot: raise HTTPException(404)
        save=owned_save(request,session,snapshot.save_id)
        filename=f"{exports.safe_filename(save.name)}-revision-{snapshot.revision}.decades-save"
        return Response(snapshot.package,media_type="application/zip",headers={"Content-Disposition":f'attachment; filename="{filename}"'})


@app.post("/backups/{snapshot_id}/restore")
def restore_snapshot(request: Request, snapshot_id: str):
    with db() as session:
        snapshot=session.get(BackupSnapshot,snapshot_id)
        if not snapshot: raise HTTPException(404)
        source=owned_save(request,session,snapshot.save_id)
        restored=backup_service.restore_as_copy(session,source.workspace_id,snapshot.package,"Restored snapshot")
        request.session["save_id"]=restored.id
        request.session["backup_notice"]="Snapshot restored as a separate save; the current save was not overwritten."
    return RedirectResponse("/p/saves",status_code=303)


def _download(raw: bytes | str, media_type: str, filename: str) -> Response:
    body=raw.encode("utf-8") if isinstance(raw,str) else raw
    return Response(body,media_type=media_type,headers={"Content-Disposition":f'attachment; filename="{filename}"'})


@app.get("/exports/{save_id}/csv.zip")
def export_csv(request: Request, save_id: str):
    with db() as session:
        save=owned_save(request,session,save_id)
        return _download(exports.csv_archive(session,save),"application/zip",f"{exports.safe_filename(save.name)}-tables.zip")


@app.get("/exports/{save_id}/family.ged")
def export_gedcom(request: Request, save_id: str):
    with db() as session:
        save=owned_save(request,session,save_id)
        return _download(exports.gedcom(session,save),"text/vnd.gedcom; charset=utf-8",f"{exports.safe_filename(save.name)}-family.ged")


@app.get("/exports/{save_id}/calendar.ics")
def export_calendar(request: Request, save_id: str):
    with db() as session:
        save=owned_save(request,session,save_id)
        return _download(exports.calendar_ics(session,save),"text/calendar; charset=utf-8",f"{exports.safe_filename(save.name)}-calendar.ics")


@app.get("/exports/{save_id}/chronicle.pdf")
def export_chronicle_pdf(request: Request, save_id: str):
    with db() as session:
        save=owned_save(request,session,save_id);story=storyline.build(session,save)
        return _download(exports.chronicle_pdf(session,save,story),"application/pdf",f"{exports.safe_filename(save.name)}-chronicle.pdf")


@app.get("/exports/{save_id}/family-tree/print",response_class=HTMLResponse)
def printable_family_tree(request: Request, save_id: str):
    with db() as session:
        save=owned_save(request,session,save_id)
        rows=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind.in_({"sim","relationship"}),Record.deleted.is_(False))))
        sims=sorted((row for row in rows if row.kind=="sim"),key=lambda row:(int_or_none((row.data or {}).get("generation")) or 0,int_or_none((row.data or {}).get("birth_global_day")) or 10**9,row.label.casefold()))
        groups=[]
        for generation in sorted({int_or_none((row.data or {}).get("generation")) for row in sims},key=lambda value:(value is None,value or 0)):
            groups.append({"label":generation if generation is not None else "Unassigned","sims":[row for row in sims if int_or_none((row.data or {}).get("generation"))==generation]})
        return templates.TemplateResponse(request,"print_family_tree.html",{"request":request,"save":save,"family_groups":groups,"relationships":[row for row in rows if row.kind=="relationship"],"historical_period":historical_period})


@app.get("/p/{page}", response_class=HTMLResponse)
def feature_page(request: Request, page: str):
    if page not in FEATURES: raise HTTPException(404)
    with db() as session:
        ctx = context(request, session, page=page, title=FEATURES[page][0], subtitle=FEATURES[page][1])
        if not ctx["user"]: return RedirectResponse("/", status_code=303)
        save = ctx["save"]
        if save:
            ctx["automation_pending"] = session.scalar(select(func.count()).select_from(Record).where(Record.save_id==save.id,Record.kind=="game_candidate",Record.deleted.is_(False),Record.data["status"].as_string()=="pending")) or 0
        kind = KIND_BY_PAGE.get(page)
        records = []
        if save and kind:
            list_page=max(1,int_or_none(request.query_params.get("list_page")) or 1);list_size=48;list_q=request.query_params.get("q","").strip();list_status=request.query_params.get("record_status","all")
            conditions=[Record.save_id==save.id,Record.kind==kind,Record.deleted.is_(False)]
            if list_q: conditions.append(Record.label.ilike(f"%{list_q}%"))
            if page=="automation": conditions.append(Record.data["status"].as_string()=="pending")
            if page=="rolls" and list_status in {"pending","completed"}: conditions.append(Record.data["completed"].as_boolean().is_(list_status=="completed"))
            if page=="rolls":
                hidden_event_ids=hidden_event_ids_for(session,save.id)
                if hidden_event_ids:
                    event_reference=Record.data["event_id"].as_string()
                    conditions.append(or_(event_reference.is_(None),event_reference.notin_(hidden_event_ids)))
            record_count=session.scalar(select(func.count()).select_from(Record).where(*conditions)) or 0;list_pages=max(1,(record_count+list_size-1)//list_size);list_page=min(list_page,list_pages)
            if page=="sims": ordering=(Record.data["sim_number"].as_string().desc().nullslast(),Record.label)
            elif page=="automation": ordering=(Record.created_at.asc(),)
            else: ordering=(Record.global_day.desc().nullslast(),Record.label)
            records=list(session.scalars(select(Record).where(*conditions).order_by(*ordering).offset((list_page-1)*list_size).limit(list_size)))
            ctx.update(list_page=list_page,list_pages=list_pages,list_count=record_count,list_q=list_q,list_status=list_status)
        view_records = None
        view_kinds = {
            "family-tree":{"sim","relationship","household"},
            "statistics":{"sim","household","relationship","pregnancy","illness","event","death","roll"},
            "pregnancies":{"sim","pregnancy","roll"}, "illnesses":{"illness","sim"},
            "households":{"household","sim","game_history","pregnancy","illness"},
            "planner":{"sim","household","play_rotation","family_plan"},
            "challenge":{"sim","campaign","service","era_guidance","era_rule"},
            "events":{"event","event_rule"}, "notes":{"note"},
            "rules":{"sim","roll","roll_rule","occult_rule","death_causes","planner_rule","multiple_birth_rule","era_guidance","era_rule","event_rule","source_archive","detection_candidate","task","roll_rule_era"},
        }.get(page)
        if save and view_kinds:
            view_records = list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind.in_(view_kinds),Record.deleted.is_(False))))
        elif save and page=="timeline":
            view_records = list(session.scalars(select(Record).where(Record.save_id==save.id,Record.deleted.is_(False),Record.global_day.is_not(None)).order_by(Record.global_day.desc()).limit(1500)))
        elif save and page=="health":
            view_records = insights.all_records(session,save.id)
        if save and view_records is not None and page in {"statistics","timeline"}:
            hidden_event_ids=hidden_event_ids_for(session,save.id)
            if hidden_event_ids:
                view_records=[item for item in view_records if item.id not in hidden_event_ids and str((item.data or {}).get("event_id") or "") not in hidden_event_ids]
        if page == "family-tree" and save:
            focus = request.query_params.get("focus")
            mode = request.query_params.get("mode", "direct")
            if mode not in {"direct", "family", "ancestors", "descendants"}: mode = "direct"
            depth = max(1, min(8, int_or_none(request.query_params.get("depth")) or 3))
            tree_photos=request.query_params.get("photos","1")!="0";tree_dates=request.query_params.get("dates","1")!="0"
            ctx.update(tree=insights.family_view(view_records, focus, mode, depth), tree_mode=mode, tree_depth=depth,tree_photos=tree_photos,tree_dates=tree_dates,
                       all_sims=sorted((item for item in view_records if item.kind == "sim" and bool((item.data or {}).get("include_in_family_tree",True))), key=insights.sim_number, reverse=True),
                       photo_record_ids=set(session.scalars(select(Portrait.record_id).where(Portrait.save_id == save.id))))
            records = []
        if page == "statistics" and save:
            ctx["statistics"] = insights.statistics(view_records, save); records = []
        if page == "pregnancies" and save:
            ctx["pregnancy_dashboard"] = insights.pregnancy_dashboard(view_records, save)
        if page == "illnesses" and save:
            ctx["illness_statistics"] = insights.illness_statistics(view_records, save)
            ctx["illness_signatures"] = list(session.scalars(select(Record).where(
                Record.save_id == save.id, Record.kind == "illness_signature", Record.deleted.is_(False),
            ).order_by(Record.label)))
        if page == "households" and save:
            ctx["household_census"] = insights.household_census(view_records, save)
        if page == "timeline" and save:
            requested_kinds = {value for value in request.query_params.getlist("kind") if value}
            start_year = int_or_none(request.query_params.get("start_year")); end_year = int_or_none(request.query_params.get("end_year"))
            ctx.update(timeline_entries=insights.timeline(view_records, save, kinds=requested_kinds or None, start_year=start_year, end_year=end_year, query=request.query_params.get("q", "")),
                       timeline_kinds=sorted({item.kind for item in view_records if item.global_day is not None}), selected_timeline_kinds=requested_kinds,
                       timeline_start=start_year, timeline_end=end_year, timeline_query=request.query_params.get("q", ""),timeline_overview=insights.timeline_overview(view_records,save))
            records = []
        if page == "health" and save:
            ctx.update(
                health_report=insights.health_report(view_records, save),
                duplicate_obligations=domain.duplicate_obligation_summary(view_records),
                duplicate_events=domain.duplicate_event_summary(view_records),
                health_notice=request.session.pop("health_notice", None),
            ); records = []
        if page == "plants" and save:
            ctx["planting"] = insights.planting(save, request.query_params.get("location", ""), request.query_params.get("region", "")); records = []
        if page == "events" and save:
            event_query_value = request.query_params.get("q", "").strip()
            event_query = event_query_value.casefold()
            event_status = request.query_params.get("status", "all")
            event_scope = request.query_params.get("scope", "all").strip()
            event_location = request.query_params.get("location", "").strip()
            event_rolls = request.query_params.get("rolls", "all")
            event_interest = request.query_params.get("interest", "shown")
            event_year = int_or_none(request.query_params.get("year"))
            event_sort = request.query_params.get("sort", "earliest")
            if event_interest not in {"shown","hidden","all"}: event_interest="shown"
            all_events = [item for item in view_records if item.kind == "event"]
            event_hidden_count=sum(domain.event_is_ignored(item) for item in all_events)
            event_scopes = sorted({str((item.data or {}).get("scope") or "Global") for item in all_events}, key=str.casefold)
            event_locations = sorted({str((item.data or {}).get("location") or "").strip() for item in all_events if str((item.data or {}).get("location") or "").strip()}, key=str.casefold)
            selected_year_start = ((event_year - save.start_year) * save.days_per_year + 1) if event_year is not None else None
            selected_year_end = (selected_year_start + save.days_per_year - 1) if selected_year_start is not None else None
            def event_visible(item):
                data=item.data or {}; start=int_or_none(data.get("start_global_day",item.global_day)); end=int_or_none(data.get("end_global_day",start));
                ignored=domain.event_is_ignored(item)
                if event_interest=="shown" and ignored: return False
                if event_interest=="hidden" and not ignored: return False
                if event_query and event_query not in f"{item.label} {data.get('location','')} {data.get('scope','')} {data.get('notes','')}".casefold(): return False
                if event_scope != "all" and str(data.get("scope") or "Global").casefold() != event_scope.casefold(): return False
                if event_location and str(data.get("location") or "").casefold() != event_location.casefold(): return False
                if event_rolls == "required" and not bool(data.get("roll_required")): return False
                if event_rolls == "reference" and bool(data.get("roll_required")): return False
                if selected_year_start is not None and not ((start is None or start <= selected_year_end) and (end is None or end >= selected_year_start)): return False
                if event_status=="active": return (start is None or start<=save.global_day) and (end is None or end>=save.global_day) and bool(data.get("active",True))
                if event_status=="upcoming": return start is not None and start>save.global_day
                if event_status=="past": return end is not None and end<save.global_day
                return True
            filtered=[item for item in all_events if event_visible(item)]
            if event_sort == "latest":
                filtered.sort(key=lambda item:(int_or_none((item.data or {}).get("start_global_day",item.global_day)) or -10**9,item.label.casefold()),reverse=True)
            elif event_sort == "name":
                filtered.sort(key=lambda item:item.label.casefold())
            else:
                filtered.sort(key=lambda item:(int_or_none((item.data or {}).get("start_global_day",item.global_day)) or 10**9,item.label.casefold()))
            event_page=max(1,int_or_none(request.query_params.get("event_page")) or 1); event_pages=max(1,(len(filtered)+49)//50); event_page=min(event_page,event_pages)
            page_events=filtered[(event_page-1)*50:event_page*50]
            imported_rules=domain._event_rule_map(session,save)
            event_filter_values={"q":event_query_value,"status":event_status,"scope":event_scope,"location":event_location,"rolls":event_rolls,"interest":event_interest,"year":event_year if event_year is not None else "","sort":event_sort}
            ctx.update(event_records=page_events,event_specs={item.id:domain.event_roll_configuration(item,imported_rules.get(domain.event_key(item),{})) for item in page_events},event_count=len(all_events),event_hidden_count=event_hidden_count,event_filtered=len(filtered),event_page=event_page,event_pages=event_pages,event_query=event_query_value,event_status=event_status,event_scope=event_scope,event_location=event_location,event_rolls=event_rolls,event_interest=event_interest,event_year=event_year,event_sort=event_sort,event_scopes=event_scopes,event_locations=event_locations,event_filters_active=sum(bool(value and value not in {"all","earliest","shown"}) for value in event_filter_values.values()),event_filter_query=urlencode(event_filter_values),event_notice=request.session.pop("event_notice",None))
            records=[]
        if page == "notes" and save:
            note_query=request.query_params.get("q","").casefold().strip(); category=request.query_params.get("category","")
            notes=[item for item in view_records if item.kind=="note"]
            categories=sorted({str((item.data or {}).get("category") or "General") for item in notes})
            notes=[item for item in notes if (not category or str((item.data or {}).get("category") or "General")==category) and (not note_query or note_query in f"{item.label} {(item.data or {}).get('body','')} {(item.data or {}).get('notes','')}".casefold())]
            ctx.update(note_records=sorted(notes,key=lambda item:(bool((item.data or {}).get("pinned")),item.global_day or 0,item.updated_at),reverse=True),note_categories=categories,note_query=request.query_params.get("q",""),note_category=category);records=[]
        if page == "names" and save:
            name_pool=names.libraries(session,save.id);cultures=sorted(name_pool);culture=request.query_params.get("culture") or (cultures[0] if cultures else "")
            sex=request.query_params.get("sex") or "Female";surname_culture=request.query_params.get("surname_culture") or culture;count=max(1,min(20,int_or_none(request.query_params.get("count")) or 5));no_surname=request.query_params.get("no_surname") in {"1","true","on","yes"}
            ctx.update(name_coverage=names.coverage(name_pool),name_cultures=cultures,name_culture=culture,name_sex=sex,name_surname_culture=surname_culture,name_count=count,name_no_surname=no_surname,name_suggestions=names.generate(name_pool,culture,sex,count,surname_culture=surname_culture,no_surname=no_surname) if request.query_params.get("generate") else [],name_medieval=names.medieval_summary())
            records=[]
        if page == "rules" and save:
            occult_rule_records=sorted((item for item in view_records if item.kind=="occult_rule"),key=lambda item:(str((item.data or {}).get("occult") or ""),str((item.data or {}).get("rule_key") or ""),int_or_none((item.data or {}).get("start_year")) or -9999))
            occult_sims=[item for item in view_records if item.kind=="sim" and occult_rules.sim_occult_types(item.data)]
            ctx.update(roll_rules=sorted((item for item in view_records if item.kind=="roll_rule"),key=lambda item:(int_or_none((item.data or {}).get("age_days")) if int_or_none((item.data or {}).get("age_days")) is not None else 10**9,item.label)),
                       cause_groups=[item for item in view_records if item.kind=="death_causes"],
                       planner_rules=sorted((item for item in view_records if item.kind=="planner_rule"),key=lambda item:(item.label,int_or_none((item.data or {}).get("start_year")) or -9999)),
                       multiple_birth_rules=sorted((item for item in view_records if item.kind=="multiple_birth_rule"),key=lambda item:int_or_none((item.data or {}).get("start_year")) or -9999),
                       era_guidance=sorted((item for item in view_records if item.kind=="era_guidance"),key=lambda item:(int_or_none((item.data or {}).get("start_year")) or -9999,item.label)),
                       imported_era_rules=[item for item in view_records if item.kind=="era_rule"],
                       event_rule_count=sum(item.kind=="event_rule" for item in view_records),
                       compatibility_records=[item for item in view_records if item.kind in {"source_archive","detection_candidate","task","roll_rule_era"}],
                       occult_rules=occult_rule_records,detected_occult_sims=occult_sims,
                       occult_rule_sims=sorted((item for item in view_records if item.kind=="sim"),key=lambda item:item.label.casefold()),
                       occult_pending_count=sum(item.kind=="roll" and bool((item.data or {}).get("occult_roll")) and not bool((item.data or {}).get("completed")) for item in view_records),
                       save_settings=dict(save.settings or {}));records=[]
        if page == "planner" and save:
            sims=[item for item in view_records if item.kind=="sim"];households=[item for item in view_records if item.kind=="household"]
            rotations=[item for item in view_records if item.kind=="play_rotation"];plans=[item for item in view_records if item.kind=="family_plan"]
            last_played={};
            for item in rotations:
                hid=(item.data or {}).get("household_id")
                if hid and (hid not in last_played or (item.global_day or 0)>(last_played[hid].global_day or 0)): last_played[hid]=item
            recommendations=[]
            for home in households:
                living=sum((sim.data or {}).get("current_household_id")==home.id and (int_or_none((sim.data or {}).get("death_global_day")) is None or int((sim.data or {}).get("death_global_day"))>save.global_day) for sim in sims)
                recommendations.append({"household":home,"last":last_played.get(home.id),"living":living})
            recommendations.sort(key=lambda item:((item["last"].global_day if item["last"] else -10**9),item["household"].label.casefold()))
            ctx.update(planner_recommendations=recommendations,rotation_records=sorted(rotations,key=lambda item:item.global_day or 0,reverse=True),family_plans=plans,all_sims=sorted(sims,key=lambda item:item.label.casefold()),all_households=sorted(households,key=lambda item:item.label.casefold()));records=[]
        if page == "challenge" and save:
            year=insights.current_year(save);challenge_location=str((save.settings or {}).get("challenge_location") or "").casefold();guidance_by_key={}
            for item in sorted((item for item in view_records if item.kind in {"era_guidance","era_rule"}),key=lambda item:item.kind=="era_guidance"):
                data=item.data or {};location=str(data.get("location") or "All").casefold()
                if not bool(data.get("active",True)) or not int(data.get("start_year",-9999))<=year<=int(data.get("end_year",9999)): continue
                if location not in {"","all","global","worldwide"} and challenge_location and location not in challenge_location and challenge_location not in location: continue
                key=str(data.get("rule_id") or data.get("legacy_id") or f"{item.label}:{data.get('start_year')}:{data.get('end_year')}")
                guidance_by_key[key]=item
            guidance=sorted(guidance_by_key.values(),key=lambda item:(str((item.data or {}).get("category") or ""),item.label.casefold()))
            sims=[item for item in view_records if item.kind=="sim"];succession=sorted((item for item in sims if int_or_none((item.data or {}).get("death_global_day")) is None or int((item.data or {}).get("death_global_day"))>save.global_day),key=lambda item:(0 if "heir" in str((item.data or {}).get("succession_override") or "").casefold() else 1,int_or_none((item.data or {}).get("birth_global_day")) or 10**9))
            ctx.update(challenge_year=year,era_guidance=guidance,succession=succession,campaigns=[item for item in view_records if item.kind=="campaign"],services=[item for item in view_records if item.kind=="service"],all_sims=sorted(sims,key=lambda item:item.label.casefold()));records=[]
        if page == "today" and save:
            # Scheduling is idempotent. Remember the check in process instead
            # of rewriting the save's large settings JSON on every new day.
            # This keeps an ordinary GET read-only when there are no new rolls.
            schedule_marker=(save.global_day,3)
            if _TODAY_SCHEDULE_CHECKED.get(save.id) != schedule_marker:
                save.revision += domain.retire_prechallenge_rolls(session,save)
                domain.schedule_marriage_rolls(session,save)
                _TODAY_SCHEDULE_CHECKED[save.id]=schedule_marker
            g = save.global_day
            params = request.query_params
            due_scope = params.get("due") or request.session.get("today_due", "due")
            task = params.get("task") or request.session.get("today_task", "rolls")
            density = params.get("density") or request.session.get("today_density", "comfortable")
            roll_kind = params.get("roll_kind") or request.session.get("today_roll_kind", "all")
            if due_scope not in {"due","today","overdue"}: due_scope = "due"
            if task not in {"rolls","pregnancies","events","illnesses","deaths"}: task = "rolls"
            if density not in {"comfortable","compact"}: density = "comfortable"
            if roll_kind not in {"all","event","occult","pregnancy-count","pregnancy","marriage","aging","planner"}: roll_kind = "all"
            request.session.update(today_due=due_scope,today_task=task,today_density=density,today_roll_kind=roll_kind)
            preview_days = max(1, min(80, int_or_none(params.get("preview")) or 7))
            def scoped(day):
                if day is None: return False
                return int(day) == g if due_scope == "today" else int(day) < g if due_scope == "overdue" else int(day) <= g
            all_sims = list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="sim",Record.deleted.is_(False)).order_by(Record.label)))
            all_households = list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="household",Record.deleted.is_(False)).order_by(Record.label)))
            all_rolls = list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.deleted.is_(False)).order_by(Record.global_day,Record.label)))
            occult_history = list(session.scalars(select(Record).where(
                Record.save_id==save.id, Record.kind=="game_history", Record.deleted.is_(False),
                Record.data["category"].as_string()=="occult",
            ).order_by(Record.global_day.desc(),Record.updated_at.desc())))
            raw_rule_definitions=list(session.scalars(select(Record).where(
                Record.save_id==save.id,Record.deleted.is_(False),Record.kind.like(r"%\_rule",escape="\\"),
            )))
            current_rule_year=save.start_year+(g-1)//max(1,save.days_per_year)
            rule_definitions=[]
            for rule in raw_rule_definitions:
                data=rule.data or {}
                if not bool(data.get("active",True)) or not concrete_rule_die(rule): continue
                start=int_or_none(data.get("start_year"));end=int_or_none(data.get("end_year"))
                if start is not None and current_rule_year<start or end is not None and current_rule_year>end: continue
                rule_definitions.append(rule)
            rule_definitions.sort(key=lambda rule:(str((rule.data or {}).get("occult") or (rule.data or {}).get("rule_family") or rule.kind),rule.label.casefold()))
            all_pregnancies = list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="pregnancy",Record.deleted.is_(False)).order_by(Record.global_day,Record.label)))
            all_events = list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="event",Record.deleted.is_(False)).order_by(Record.global_day,Record.label)))
            hidden_event_ids={event.id for event in all_events if domain.event_is_ignored(event)}
            all_events=[event for event in all_events if event.id not in hidden_event_ids]
            if hidden_event_ids: all_rolls=[roll for roll in all_rolls if str((roll.data or {}).get("event_id") or "") not in hidden_event_ids]
            all_illnesses = list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="illness",Record.deleted.is_(False)).order_by(Record.global_day,Record.label)))
            dead_sim_ids={sim.id for sim in all_sims if bool((sim.data or {}).get("game_was_dead")) or (int_or_none(sim.data.get("death_global_day")) is not None and int(sim.data.get("death_global_day"))<=g)}
            living_sims=[sim for sim in all_sims if sim.id not in dead_sim_ids]
            def roll_identity(roll):
                return (str(roll.data.get("sim_id") or ""),str(roll.data.get("roll_type") or "").casefold().strip(),int(roll.global_day or roll.data.get("due_global_day") or 0))
            completed_roll_keys = {roll_identity(r) for r in all_rolls if bool(r.data.get("completed"))}
            pending_rolls = []
            pending_roll_keys = set()
            for candidate in all_rolls:
                key = roll_identity(candidate)
                candidate_due = int_or_none(candidate.global_day) or int_or_none((candidate.data or {}).get("due_global_day"))
                post_death_roll = bool((candidate.data or {}).get("allow_after_death")) or (bool((candidate.data or {}).get("occult_roll")) and (candidate.data or {}).get("occult_rule_key") == "ghost_persistence")
                if candidate_due is None or candidate_due < 1 or bool(candidate.data.get("completed")) or (candidate.data.get("sim_id") in dead_sim_ids and not post_death_roll) or key in completed_roll_keys or key in pending_roll_keys: continue
                pending_rolls.append(candidate); pending_roll_keys.add(key)
            def roll_category(roll):
                if bool((roll.data or {}).get("pregnancy_count_roll")): return "pregnancy-count"
                if bool((roll.data or {}).get("occult_roll")): return "occult"
                text = " ".join(str(roll.data.get(k) or "") for k in ("source","source_id","roll_type")).casefold()
                if "event" in text: return "event"
                if "preg" in text or "maternal" in text or "born" in text or "newborn" in text: return "pregnancy"
                if "marriage" in text: return "marriage"
                if "planner" in text: return "planner"
                return "aging"
            occult_summaries = []
            for occult_roll in (item for item in all_rolls if bool((item.data or {}).get("occult_roll"))):
                data = occult_roll.data or {}
                due = int_or_none(data.get("due_global_day")) or int_or_none(occult_roll.global_day) or g
                completed = bool(data.get("completed"))
                if completed:
                    status = "Resolved"
                    display_day = int_or_none(data.get("completed_global_day")) or due
                    summary = str(data.get("outcome") or "Completed")
                else:
                    display_day = due
                    status = "Overdue" if due < g else "Due today" if due == g else "Upcoming"
                    summary = str(data.get("result_rules") or data.get("notes") or "Awaiting a result")
                occult_summaries.append({
                    "record":occult_roll, "kind":"roll", "title":occult_roll.label,
                    "sim_name":str(data.get("sim_name") or ""), "occult_type":str(data.get("occult_type") or "Occult"),
                    "status":status, "global_day":display_day, "summary":summary,
                    "die":str(data.get("die") or ""), "actual":data.get("actual"),
                    "result_rules":str(data.get("result_rules") or ""), "completed":completed,
                })
            for history in occult_history:
                data = history.data or {}
                transition = ""
                if data.get("from") or data.get("to"):
                    transition = f"{data.get('from') or 'Unknown'} → {data.get('to') or 'Unknown'}"
                occult_summaries.append({
                    "record":history, "kind":"detection", "title":history.label,
                    "sim_name":str(data.get("sim_name") or ""),
                    "occult_type":str(data.get("to") or ", ".join(data.get("occult_types") or []) or "Occult"),
                    "status":"Detected", "global_day":int_or_none(history.global_day) or g,
                    "summary":transition or str(data.get("notes") or history.label),
                    "die":"", "actual":None, "result_rules":"", "completed":True,
                })
            occult_order = {"Overdue":0,"Due today":1,"Upcoming":2,"Resolved":3,"Detected":4}
            occult_summaries.sort(key=lambda item:(occult_order.get(item["status"],9), item["global_day"] if item["status"] in {"Overdue","Due today","Upcoming"} else -item["global_day"], item["title"].casefold()))
            occult_summary_counts = {
                "total":len(occult_summaries),
                "pending":sum(1 for item in occult_summaries if item["status"] in {"Overdue","Due today","Upcoming"}),
                "resolved":sum(1 for item in occult_summaries if item["status"]=="Resolved"),
                "detected":sum(1 for item in occult_summaries if item["status"]=="Detected"),
            }
            followup_children={}
            for child in all_rolls:
                origin_id=str((child.data or {}).get("origin_roll_id") or "")
                if origin_id: followup_children.setdefault(origin_id,[]).append(child)
            rule_action_outcomes=[]
            for origin in all_rolls:
                origin_data=origin.data or {}
                if not bool(origin_data.get("completed")) or not bool(origin_data.get("triggered")) or bool(origin_data.get("rule_followup_reviewed")): continue
                followups=[rule for rule in rule_definitions if rule_can_follow(origin,rule)]
                if not followups: continue
                rule_action_outcomes.append({
                    "origin":origin,"followups":followups,"created":followup_children.get(origin.id,[]),
                    "default_sim_id":str(origin_data.get("sim_id") or ""),
                })
            rule_action_outcomes.sort(key=lambda item:(-(int_or_none((item["origin"].data or {}).get("completed_global_day")) or int_or_none(item["origin"].global_day) or g),item["origin"].label.casefold()))
            due_rolls = [r for r in pending_rolls if scoped(r.global_day) and (roll_kind=="all" or roll_category(r)==roll_kind)]
            today_roll_results = sorted((
                r for r in all_rolls
                if bool(r.data.get("completed"))
                and int_or_none(r.data.get("completed_global_day", r.global_day)) == g
                and (roll_kind=="all" or roll_category(r)==roll_kind)
            ), key=lambda r:r.updated_at, reverse=True)
            roll_priority = {"event":0,"occult":1,"pregnancy-count":2,"pregnancy":3,"marriage":4,"aging":5,"planner":6}
            due_rolls.sort(key=lambda r:(roll_priority.get(roll_category(r),9),int(r.global_day or g),r.label.casefold()))
            due_pregnancies = [p for p in all_pregnancies if scoped(p.data.get("due_global_day",p.global_day)) and str(p.data.get("status") or "active").casefold() not in domain.CLOSED_PREGNANCIES]
            active_events = [e for e in all_events if bool(e.data.get("active",True)) and int(e.data.get("start_global_day",e.global_day) or -10**9)<=g<=int(e.data.get("end_global_day",e.global_day) or 10**9)]
            active_illnesses = [i for i in all_illnesses if str(i.data.get("status") or "active").casefold() not in domain.CLOSED_ILLNESSES and int(i.data.get("onset_global_day",i.global_day) or g)<=g and (i.data.get("end_global_day") in (None,"") or int(i.data.get("end_global_day"))>=g)]
            active_illnesses.sort(key=lambda i: ({"critical":0,"severe":1,"moderate":2,"mild":3}.get(str(i.data.get("severity") or "").casefold(),4),i.global_day or 0))
            due_deaths = [s for s in all_sims if scoped(s.data.get("death_global_day")) and not bool(s.data.get("death_confirmed"))]
            today_deaths = [s for s in all_sims if int_or_none(s.data.get("death_global_day"))==g and not bool(s.data.get("death_confirmed"))]
            upcoming_deaths = sorted([s for s in all_sims if (int_or_none(s.data.get("death_global_day")) or -10**9)>g],key=lambda s:int(s.data.get("death_global_day")))[:10]
            upcoming_rolls = [r for r in pending_rolls if r.global_day is not None and g < int(r.global_day) <= g + preview_days][:20]
            event_context = {r.id:[e.label for e in all_events if int(e.data.get("start_global_day",e.global_day) or -10**9)<=int(r.global_day or g)<=int(e.data.get("end_global_day",e.global_day) or 10**9) and bool(e.data.get("active",True))][:5] for r in due_rolls+upcoming_rolls}
            page_size=50; roll_page=max(1,int_or_none(params.get("roll_page")) or 1); roll_pages=max(1,(len(due_rolls)+page_size-1)//page_size); roll_page=min(roll_page,roll_pages); due_rolls=due_rolls[(roll_page-1)*page_size:roll_page*page_size]
            raw_settings=dict(save.settings or {}); legacy=raw_settings.get("legacy_settings") or {}; id_map=raw_settings.get("legacy_id_map") or {}
            current_heir=raw_settings.get("current_heir_id") or id_map.get(legacy.get("current_heir_id"),legacy.get("current_heir_id"))
            main_household=raw_settings.get("main_household_id") or id_map.get(legacy.get("main_household_id"),legacy.get("main_household_id"))
            digest_records=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="session_journal",Record.deleted.is_(False)).order_by(Record.global_day.desc(),Record.updated_at.desc()).limit(7)))
            ctx.update(all_sims=all_sims,living_sims=living_sims,all_households=all_households,due_scope=due_scope,task=task,density=density,roll_kind=roll_kind,preview_days=preview_days,
                due_rolls=due_rolls,today_roll_results=today_roll_results,due_pregnancies=due_pregnancies,active_events=active_events,active_illnesses=active_illnesses,due_deaths=due_deaths,today_deaths=today_deaths,upcoming_deaths=upcoming_deaths,upcoming_rolls=upcoming_rolls,event_context=event_context,
                today_counts={"rolls":len([r for r in pending_rolls if int(r.global_day or g)<=g]),"pregnancies":len([p for p in all_pregnancies if int(p.data.get("due_global_day",p.global_day) or g)<=g and str(p.data.get("status") or "active").casefold() not in domain.CLOSED_PREGNANCIES]),"events":len(active_events),"illnesses":len(active_illnesses),"deaths":len(today_deaths)},
                roll_page=roll_page,roll_pages=roll_pages,current_heir=current_heir,main_household=main_household,photo_record_ids=set(session.scalars(select(Portrait.record_id).where(Portrait.save_id==save.id))),today_undo=request.session.get("today_undo"),
                daily_digest=digest_records[0] if digest_records else None,recent_digests=digest_records,
                occult_summaries=occult_summaries,occult_summary_counts=occult_summary_counts,
                rule_definitions=rule_definitions,rule_action_outcomes=rule_action_outcomes,
                rule_workbench_notice=request.session.pop("rule_workbench_notice",None))
            records=[]
        if page == "dice-audit":
            report = dice.fairness_report(session, save.id if save else None, request.query_params.get("die", "d20"))
            ctx["fairness"] = report
            ctx["dice_ledger"] = list(session.scalars(select(DiceAudit).where(DiceAudit.save_id == (save.id if save else None)).order_by(DiceAudit.created_at.desc()).limit(100))) if save else []
        if page == "sync" and save:
            ctx["devices"] = list(session.scalars(select(Device).where(Device.save_id == save.id)))
            ctx["sync_conflicts"] = list(session.scalars(select(Conflict).where(Conflict.save_id == save.id, Conflict.status == "open").order_by(Conflict.created_at.desc())))
            ctx["sync_conflict_rows"] = [{"conflict": item, "fields": sync.conflict_fields(item)} for item in ctx["sync_conflicts"]]
            if settings.local_mode:
                from .sync_client import load_config
                configured=load_config();ctx["sync_config"]={key:value for key,value in configured.items() if key!="token"}
            ctx["sync_notice"]=request.session.pop("sync_notice",None)
        if page == "clock" and save:
            link = session.scalar(select(ClockLink).where(ClockLink.save_id == save.id))
            ctx["clock_link"] = link
            ctx["clock_notice"] = request.session.pop("clock_notice", None)
            if link and link.last_game_day is not None and link.game_anchor_day is not None and link.tracker_anchor_day is not None:
                ctx["clock_projected_day"] = int(link.tracker_anchor_day) + max(0, int(link.last_game_day) - int(link.game_anchor_day))
                ctx["clock_drift"] = save.global_day - ctx["clock_projected_day"]
            if settings.local_mode:
                ctx["game_save_files"] = save_scanner.discover_saves()
                ctx["game_save_scan"] = _SAVE_SCAN_CACHE.get(save.id)
                ctx["game_save_notice"] = request.session.pop("game_save_notice", None)
        if page == "today" and save:
            ctx["clock_link"] = session.scalar(select(ClockLink).where(ClockLink.save_id == save.id))
        if page == "storyline" and save:
            story_data = storyline.build(session, save)
            chapter_size = 20
            chapter_pages = max(1, (len(story_data["chapters"]) + chapter_size - 1) // chapter_size)
            chapter_page = min(chapter_pages, max(1, int_or_none(request.query_params.get("chapter_page")) or 1))
            chapter_start = (chapter_page - 1) * chapter_size
            story_data.update(
                chapter_page=chapter_page,
                chapter_pages=chapter_pages,
                chapter_total=len(story_data["chapters"]),
                chapter_page_items=story_data["chapters"][chapter_start:chapter_start + chapter_size],
            )
            ctx["story"] = story_data
            ctx["all_sims"] = list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="sim",Record.deleted.is_(False)).order_by(Record.label)))
            ctx["storyline_notice"] = request.session.pop("storyline_notice", None)
        if page == "saves" and save:
            backup_rows = list(session.scalars(select(BackupSnapshot).where(
                BackupSnapshot.save_id.in_([item.id for item in ctx["saves"]]),
            ).order_by(BackupSnapshot.created_at.desc()).limit(100))) if ctx["saves"] else []
            ctx["backup_rows_by_save"] = {item.id: [row for row in backup_rows if row.save_id == item.id] for item in ctx["saves"]}
            ctx["backup_notice"] = request.session.pop("backup_notice", None)
        if page == "account" and save:
            user, workspace = active_workspace(request, session)
            memberships = list(session.scalars(select(Membership).where(Membership.workspace_id == workspace.id)))
            ctx.update(
                workspace=workspace,
                workspace_role=accounts.role_for(session, user.id, workspace.id),
                workspace_members=[{"membership": row, "user": session.get(User, row.user_id)} for row in memberships],
                workspace_invites=list(session.scalars(select(WorkspaceInvite).where(WorkspaceInvite.workspace_id == workspace.id).order_by(WorkspaceInvite.created_at.desc()).limit(25))),
                legacy_links=list(session.scalars(select(LegacyWorkspaceCode).where(LegacyWorkspaceCode.workspace_id == workspace.id).order_by(LegacyWorkspaceCode.created_at.desc()))),
                legacy_imported_saves=[item for item in ctx["saves"] if item.workspace_id == workspace.id and (item.settings or {}).get("legacy_neon_save_id")],
                notification_preference=notifications.preference(session, user.id, workspace.id),
                notification_categories=notifications.DEFAULT_CATEGORIES,
                account_notice=request.session.pop("account_notice", None),
                invitation_link=request.session.pop("invitation_link", None),
                portrait_config=portraits.effective_config(),
                portrait_notice=request.session.pop("portrait_notice", None),
            )
        if page == "automation" and save:
            ctx["birth_estimates"] = {
                item.id:clock.estimate_new_sim_birth(session,save,item.data.get("payload") or item.data,item.global_day)
                for item in records if item.data.get("action") == "new_sim"
            }
            ctx["journals"] = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "session_journal", Record.deleted.is_(False)).order_by(Record.global_day.desc()).limit(30)))
            ctx["legacy_detections"] = list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="detection_candidate",Record.deleted.is_(False)).order_by(Record.created_at.desc()).limit(50)))
        if save and page in {"sims", "relationships", "households", "pregnancies", "illnesses", "automation", "rolls"}:
            ctx["all_sims"] = sorted(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False))), key=insights.sim_number, reverse=True)
            ctx["all_households"] = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "household", Record.deleted.is_(False)).order_by(Record.label)))
            ctx["photo_record_ids"] = set(session.scalars(select(Portrait.record_id).where(Portrait.save_id == save.id)))
            ctx["archived_count"] = session.scalar(select(func.count()).select_from(Record).where(Record.save_id == save.id, Record.kind == kind, Record.deleted.is_(True))) if kind else 0
            ctx["archived_records"] = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == kind, Record.deleted.is_(True)).order_by(Record.label).limit(100))) if kind else []
            if page=="sims":
                ctx["name_cultures"]=sorted(names.libraries(session,save.id))
        ctx.update(records=records, kind=kind, portrait_status=portraits.provider_status())
        dedicated = {
            "today":"today.html", "sims":"sims.html", "relationships":"relationships.html", "households":"households.html",
            "pregnancies":"pregnancies.html", "illnesses":"illnesses.html", "automation":"automation.html", "storyline":"storyline.html",
            "family-tree":"family_tree.html", "timeline":"timeline.html", "statistics":"statistics.html", "health":"health.html",
            "plants":"plants.html", "events":"events.html", "notes":"notes.html", "rules":"rules.html", "planner":"planner.html",
            "challenge":"challenge.html", "guides":"guides.html", "names":"names.html", "saves":"saves.html", "support":"support.html",
            "clock":"clock.html", "sync":"sync.html", "account":"account.html", "dice-audit":"dice_audit.html", "rolls":"rolls.html",
        }
        return templates.TemplateResponse(request, dedicated.get(page, "feature.html"), ctx)


@app.get("/storyline/export")
def export_storyline(request: Request):
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        story=storyline.build(session,save)
        lines=[f"# The Chronicle of {save.name}","",story["opening"],""]
        if story["authored_entries"]:
            lines.extend(["## Written entries",""])
            for entry in reversed(story["authored_entries"]):
                author=(entry.data or {}).get("narrator_name") or "The chronicler"
                lines.extend([f"### {entry.label}",f"*{historical_period(save,entry.global_day)} · {author}*","",str((entry.data or {}).get("body") or ""),""])
        lines.extend(["## Chapters by year",""])
        for chapter in reversed(story["chapters"]):
            lines.extend([f"### {chapter['year']}",chapter.get("paragraph") or chapter["summary"],""])
            lines.extend(f"- {entry.label} ({entry.kind.replace('_',' ')})" for entry in chapter["entries"])
            lines.append("")
        safe_name=re.sub(r"[^A-Za-z0-9._-]+","-",save.name).strip("-") or "decades-chronicle"
        return Response("\n".join(lines),media_type="text/markdown; charset=utf-8",headers={"Content-Disposition":f'attachment; filename="{safe_name}-story.md"'})


@app.post("/storyline/generate")
def generate_storyline_chapter(request: Request, narrator_sim_id: str = Form(""), tone: str = Form("intimate"), use_ai: str = Form("")):
    if tone not in {"intimate","dramatic","formal","hopeful"}: raise HTTPException(400,"Choose a supported narrative tone.")
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400)
        entry=storyline.generate_chapter(session,save,narrator_sim_id,tone,use_ai in {"1","on","true","yes"})
        request.session["storyline_notice"]=f"Generated “{entry.label}” from the save’s recorded facts."
    return RedirectResponse("/p/storyline",status_code=303)


@app.post("/storyline/settings")
def save_storyline_settings(request: Request, automatic_storyline: str = Form("")):
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400)
        values=dict(save.settings or {});values["automatic_storyline"]=automatic_storyline in {"1","on","true","yes"};save.settings=values;save.revision+=1
        request.session["storyline_notice"]="Automatic storyline setting saved."
    return RedirectResponse("/p/storyline",status_code=303)


@app.post("/names/import")
async def import_name_library(request: Request):
    form=await request.form();culture=str(form.get("culture") or "").strip();sex=str(form.get("sex") or "Any").strip();source=str(form.get("source") or "Player supplied source").strip()
    if not culture: raise HTTPException(400,"Name the culture or source group.")
    def lines(value): return list(dict.fromkeys(line.strip() for line in str(value or "").splitlines() if line.strip()))
    first_names=lines(form.get("first_names"));surnames=lines(form.get("surnames"))
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        existing={(str((item.data or {}).get("culture") or "").casefold(),str((item.data or {}).get("sex") or "Any").casefold(),str((item.data or {}).get("name_kind") or "first"),str((item.data or {}).get("name") or item.label).casefold()) for item in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="name_entry",Record.deleted.is_(False)))}
        made=0
        for kind,entry_sex,values in (("first",sex,first_names),("surname","Any",surnames)):
            for value in values:
                key=(culture.casefold(),entry_sex.casefold(),kind,value.casefold())
                if key in existing: continue
                item=Record(save_id=save.id,kind="name_entry",label=value,data={"name":value,"culture":culture,"sex":entry_sex,"name_kind":kind,"source":source});session.add(item);session.flush();domain.journal(session,item,"upsert",0);existing.add(key);made+=1
        save.revision+=made
    return RedirectResponse("/p/names",status_code=303)


@app.get("/api/names/generate")
def generate_names(request: Request, culture: str = "", sex: str = "Female", surname_culture: str = "", count: int = 5, no_surname: bool = False):
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        pool=names.libraries(session,save.id);chosen=culture or (sorted(pool)[0] if pool else "")
        return {"suggestions":names.generate(pool,chosen,sex,count,surname_culture=surname_culture or chosen,no_surname=no_surname),"cultures":sorted(pool)}


@app.get("/sims/{sim_id}", response_class=HTMLResponse)
def sim_profile(request: Request, sim_id: str):
    with db() as session:
        sim = session.get(Record, sim_id)
        if not sim or sim.kind != "sim" or sim.deleted: raise HTTPException(404)
        save = owned_save(request, session, sim.save_id)
        all_sims = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False)).order_by(Record.label)))
        households = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "household", Record.deleted.is_(False)).order_by(Record.label)))
        relationships = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "relationship", Record.deleted.is_(False)).where((Record.data["partner1_id"].as_string() == sim.id) | (Record.data["partner2_id"].as_string() == sim.id))))
        sim_by_id={item.id:item for item in all_sims};sim_data=sim.data or {}
        parents=[sim_by_id[parent_id] for parent_id in (sim_data.get("mother_id"),sim_data.get("father_id")) if parent_id in sim_by_id]
        children=[item for item in all_sims if sim.id in ((item.data or {}).get("mother_id"),(item.data or {}).get("father_id"))]
        parent_ids={parent_id for parent_id in (sim_data.get("mother_id"),sim_data.get("father_id")) if parent_id}
        siblings=[item for item in all_sims if item.id!=sim.id and parent_ids.intersection({(item.data or {}).get("mother_id"),(item.data or {}).get("father_id")})]
        current_household=next((item for item in households if item.id==sim_data.get("current_household_id")),None)
        related_rolls=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.deleted.is_(False),Record.data["sim_id"].as_string()==sim.id).order_by(Record.global_day.desc()).limit(20)))
        life_history=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="game_history",Record.deleted.is_(False),Record.data["sim_id"].as_string()==sim.id).order_by(Record.global_day.desc(),Record.created_at.desc()).limit(80)))
        illnesses=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="illness",Record.deleted.is_(False),Record.data["sim_id"].as_string()==sim.id).order_by(Record.global_day.desc()).limit(20)))
        pregnancies=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="pregnancy",Record.deleted.is_(False)).where((Record.data["mother_id"].as_string()==sim.id) | (Record.data["father_id"].as_string()==sim.id)).order_by(Record.global_day.desc()).limit(20)))
        relationship_rows=[]
        for relationship in relationships:
            relationship_data=relationship.data or {}
            other_id=relationship_data.get("partner2_id") if relationship_data.get("partner1_id")==sim.id else relationship_data.get("partner1_id")
            relationship_rows.append({"record":relationship,"partner":sim_by_id.get(other_id)})
        birth_day=int_or_none(sim_data.get("birth_global_day",sim.global_day));death_day=int_or_none(sim_data.get("death_global_day"))
        age_end=death_day if death_day is not None and death_day<=save.global_day else save.global_day
        age_days=max(0,age_end-birth_day) if birth_day is not None else None
        life_stage=insights.life_stage(sim,save.global_day)
        stage_index=next((index for index,(label,_) in enumerate(insights.LIFE_STAGES) if label==life_stage),None)
        next_stage=None;stage_progress=None
        if age_days is not None and stage_index is not None:
            stage_start=insights.LIFE_STAGES[stage_index][1]
            if stage_index+1<len(insights.LIFE_STAGES):
                next_label,next_start=insights.LIFE_STAGES[stage_index+1]
                span=max(1,next_start-stage_start)
                stage_progress=max(0,min(100,round((age_days-stage_start)*100/span)))
                next_stage={"label":next_label,"global_day":birth_day+next_start,"days_remaining":max(0,next_start-age_days)}
            else:
                stage_progress=100
        active_illnesses=[item for item in illnesses if str((item.data or {}).get("status") or "Active").casefold() in {"active","chronic","ongoing"}]
        active_pregnancies=[item for item in pregnancies if str((item.data or {}).get("status") or "Active").casefold() in {"active","pregnant","ongoing"}]
        pending_rolls=[item for item in related_rolls if not (item.data or {}).get("completed")]
        completed_rolls=[item for item in related_rolls if (item.data or {}).get("completed")]
        profile_summary={"life_stage":life_stage,"age_days":age_days,"stage_progress":stage_progress,"next_stage":next_stage,"active_illnesses":active_illnesses,"active_pregnancies":active_pregnancies,"pending_rolls":pending_rolls,"completed_rolls":completed_rolls}
        pregnancy_plan=domain.pregnancy_allowance_status(session,save,sim)
        sim_portraits=list(session.scalars(select(Portrait).where(Portrait.record_id==sim.id).order_by(Portrait.created_at)))
        delete_impact=domain.sim_delete_impact(session,sim) if request.query_params.get("delete")=="1" else None
        name_history={"surname_at_birth":domain.surname_at_birth(sim),"married_surname":domain.married_surname(sim)}
        ctx = context(request, session, sim=sim, name_history=name_history, all_sims=all_sims, all_households=households, relationships=relationships, relationship_rows=relationship_rows, parents=parents,children=children,siblings=siblings,current_household=current_household,related_rolls=related_rolls,life_history=life_history,illnesses=illnesses,pregnancies=pregnancies,profile_summary=profile_summary,pregnancy_plan=pregnancy_plan,sim_portraits=sim_portraits,photo_record_ids=set(session.scalars(select(Portrait.record_id).where(Portrait.save_id==save.id))),portrait_notice=request.session.pop("portrait_notice",None),sim_notice=request.session.pop("sim_notice",None), delete_impact=delete_impact, title=sim.label, page="sims")
        return templates.TemplateResponse(request, "sim_profile.html", ctx)


@app.post("/sims")
async def add_sim(request: Request):
    form=await request.form()
    with db() as session:
        ctx = context(request, session); save = ctx["save"]
        if not save: raise HTTPException(400)
        first_name=str(form.get("first_name") or "").strip()
        if not first_name: raise HTTPException(400,"First name is required.")
        title=str(form.get("title") or "").strip();last_name=str(form.get("last_name") or "").strip();suffix=str(form.get("suffix") or "").strip()
        mother_id=str(form.get("mother_id") or "");father_id=str(form.get("father_id") or "");household_id=str(form.get("household_id") or "")
        for record_id,kind in ((mother_id,"sim"),(father_id,"sim"),(household_id,"household")):
            if record_id:
                linked=session.get(Record,record_id)
                if not linked or linked.save_id!=save.id or linked.kind!=kind or linked.deleted: raise HTTPException(400,"A selected family or household record is invalid.")
        sim_number = next_sim_number(session, save.id)
        name = " ".join(part for part in (title,first_name,last_name,suffix) if part)
        birth,birth_fields=resolve_birth_input(save,form.get("birth_global_day"),form.get("birth_year"),form.get("birth_game_hour"),form.get("birth_game_minute"));death=int_or_none(form.get("death_global_day"))
        birth_surname=str(form.get("surname_at_birth") or form.get("maiden_name") or last_name).strip();married_surname=str(form.get("married_surname") or form.get("married_name") or "").strip()
        sim_data={"sim_number":sim_number,"title":title,"first_name":first_name,"last_name":last_name,"suffix":suffix,"surname_at_birth":birth_surname,"maiden_name":birth_surname,"married_surname":married_surname,"married_name":married_surname,"sex":str(form.get("sex") or ""),"generation":int_or_none(form.get("generation")),"birth_global_day":birth,"death_global_day":death,"birth_status":str(form.get("birth_status") or ""),"multiple_birth_status":str(form.get("multiple_birth_status") or ""),"mother_id":mother_id or None,"father_id":father_id or None,"current_household_id":household_id or None,"historical_household":str(form.get("historical_household") or ""),"species_occult":str(form.get("species") or "Human"),"legitimacy":str(form.get("legitimacy") or ""),"fertility_status":str(form.get("fertility_status") or ""),"succession_override":str(form.get("succession_override") or ""),"succession_notes":str(form.get("succession_notes") or ""),"played_through_global_day":int_or_none(form.get("played_through_global_day")),"include_in_family_tree":"include_in_family_tree" not in form or str(form.get("include_in_family_tree") or "").casefold() in {"1","true","on","yes"},"birthplace":str(form.get("birthplace") or ""),"cause_of_death":str(form.get("cause_of_death") or ""),"death_place":str(form.get("death_place") or ""),"notes":str(form.get("notes") or "")}
        if sim_data["generation"] is not None: sim_data["generation_source"]="manual"
        sim_data.update(birth_fields);sim_data.update(death_calendar_fields(save,death,form.get("death_game_hour"),form.get("death_game_minute")))
        record = Record(save_id=save.id, kind="sim", label=name, global_day=birth, data=sim_data)
        session.add(record); session.flush(); session.add(Change(save_id=save.id, device_id="local" if settings.local_mode else "web", record_id=record.id, kind="sim", operation="upsert", base_version=0, new_version=1, payload=sync.serialize(record))); save.revision += 1+domain.sync_generations(session,save); domain.schedule_rolls(session, save)
        return RedirectResponse(f"/sims/{record.id}", status_code=303)


@app.post("/sims/{sim_id}")
async def edit_sim(request: Request, sim_id: str):
    form=await request.form()
    with db() as session:
        record = session.get(Record, sim_id)
        if not record or record.kind != "sim": raise HTTPException(404)
        save = owned_save(request, session, record.save_id); base = record.version
        first_name=str(form.get("first_name") or "").strip()
        if not first_name: raise HTTPException(400,"First name is required.")
        title=str(form.get("title") or "").strip();last_name=str(form.get("last_name") or "").strip();suffix=str(form.get("suffix") or "").strip()
        mother_id=str(form.get("mother_id") or "");father_id=str(form.get("father_id") or "");household_id=str(form.get("household_id") or "")
        if record.id in {mother_id,father_id}: raise HTTPException(400,"A Sim cannot be their own parent.")
        previous_current_surname=str((record.data or {}).get("last_name") or "");previous_married_surname=domain.married_surname(record)
        for record_id,kind in ((mother_id,"sim"),(father_id,"sim"),(household_id,"household")):
            if record_id:
                linked=session.get(Record,record_id)
                if not linked or linked.save_id!=save.id or linked.kind!=kind or linked.deleted: raise HTTPException(400,"A selected family or household record is invalid.")
        birth,birth_fields=resolve_birth_input(save,form.get("birth_global_day"),form.get("birth_year"),form.get("birth_game_hour"),form.get("birth_game_minute"))
        data = dict(record.data or {});previous_generation=data.get("generation");previous_generation_source=str(data.get("generation_source") or "").casefold();submitted_generation=int_or_none(form.get("generation"));birth_surname=str(form.get("surname_at_birth") or form.get("maiden_name") or data.get("surname_at_birth") or data.get("maiden_name") or last_name).strip();married_surname=str(form.get("married_surname") or form.get("married_name") or "").strip();data.update({"title":title,"first_name":first_name,"last_name":last_name,"suffix":suffix,"surname_at_birth":birth_surname,"maiden_name":birth_surname,"married_surname":married_surname,"married_name":married_surname,"sex":str(form.get("sex") or ""),"generation":submitted_generation,"birth_global_day":birth,"death_global_day":int_or_none(form.get("death_global_day")),"birth_status":str(form.get("birth_status") or ""),"multiple_birth_status":str(form.get("multiple_birth_status") or ""),"mother_id":mother_id or None,"father_id":father_id or None,"current_household_id":household_id or None,"historical_household":str(form.get("historical_household") or ""),"species_occult":str(form.get("species") or "Human"),"occult_alignment":str(form.get("occult_alignment") or ""),"dormant_occult_types":detected_form_list(str(form.get("dormant_occult_types") or "")),"occult_water_access":str(form.get("occult_water_access") or "Unknown"),"werewolf_confined":str(form.get("werewolf_confined") or "").casefold() in {"1","true","on","yes"},"occult_notes":str(form.get("occult_notes") or ""),"legitimacy":str(form.get("legitimacy") or ""),"fertility_status":str(form.get("fertility_status") or ""),"succession_override":str(form.get("succession_override") or ""),"succession_notes":str(form.get("succession_notes") or ""),"played_through_global_day":int_or_none(form.get("played_through_global_day")),"include_in_family_tree":str(form.get("include_in_family_tree") or "").casefold() in {"1","true","on","yes"},"cause_of_death":str(form.get("cause_of_death") or ""),"birthplace":str(form.get("birthplace") or ""),"death_place":str(form.get("death_place") or ""),"game_career":str(form.get("game_career") or "").strip(),"game_education":str(form.get("game_education") or "").strip(),"game_traits":detected_form_list(str(form.get("game_traits") or "")),"game_skills":detected_form_list(str(form.get("game_skills") or "")),"game_milestones":detected_form_list(str(form.get("game_milestones") or "")),"notes":str(form.get("notes") or "")})
        if last_name != previous_current_surname or married_surname != previous_married_surname:
            data.pop("married_name_source_relationship_id",None)
        if submitted_generation is None:
            data.pop("generation_source",None);data.pop("generation_source_ids",None)
        elif previous_generation_source not in domain.AUTOMATIC_GENERATION_SOURCES or int_or_none(previous_generation)!=submitted_generation:
            data["generation_source"]="manual";data.pop("generation_source_ids",None)
        for key in ("birth_game_hour","birth_game_minute","birth_time","historical_birth_date","historical_birth_date_range","birth_date_precision","birth_year","birth_year_only","birth_global_day_estimated","birth_estimate_precision","birth_estimate_source","original_birth_estimate_global_day","estimated_birth_global_day_range_start","estimated_birth_global_day_range_end"):
            data.pop(key,None)
        data.update(birth_fields)
        for key in ("death_game_hour","death_game_minute","death_time","historical_death_date","historical_death_date_range","death_date_precision"):
            data.pop(key,None)
        data.update(death_calendar_fields(save,data["death_global_day"],form.get("death_game_hour"),form.get("death_game_minute")))
        record.label = " ".join(part for part in (title,first_name,last_name,suffix) if part); record.global_day = data["birth_global_day"]; record.data = data; record.version += 1
        session.add(Change(save_id=save.id, device_id="local" if settings.local_mode else "web", record_id=record.id, kind="sim", operation="upsert", base_version=base, new_version=record.version, payload=sync.serialize(record))); save.revision += 1+domain.sync_generations(session,save)
        if data["death_global_day"] is not None:
            save.revision += domain.end_illnesses_for_death(session, save, record, data["death_global_day"])
        domain.schedule_rolls(session, save)
    return RedirectResponse(f"/sims/{sim_id}", status_code=303)


@app.post("/sims/{sim_id}/spouse")
def add_sim_spouse(
    request: Request,
    sim_id: str,
    spouse_id: str = Form(...),
    marriage_global_day: str = Form(""),
    marriage_game_hour: str = Form(""),
    marriage_game_minute: str = Form(""),
    location: str = Form(""),
    surname_rule: str = Form("automatic"),
    notes: str = Form(""),
):
    """Connect a spouse from a Sim profile without creating duplicate marriages."""
    if sim_id == spouse_id:
        raise HTTPException(400, "A Sim cannot be their own spouse.")
    with db() as session:
        first = session.get(Record, sim_id)
        if not first or first.kind != "sim" or first.deleted:
            raise HTTPException(404)
        save = owned_save(request, session, first.save_id)
        second = session.get(Record, spouse_id)
        if not second or second.kind != "sim" or second.deleted or second.save_id != save.id:
            raise HTTPException(400, "Choose a valid spouse from this save.")

        pair = {first.id, second.id}
        relationship_records = list(session.scalars(select(Record).where(
            Record.save_id == save.id,
            Record.kind == "relationship",
            Record.deleted.is_(False),
        ).order_by(Record.created_at.desc())))
        pair_records = [
            item for item in relationship_records
            if {
                str((item.data or {}).get("partner1_id") or ""),
                str((item.data or {}).get("partner2_id") or ""),
            } == pair
        ]

        closed_statuses = {"ended", "divorced", "annulled", "widowed", "former", "inactive"}

        def is_active(item: Record) -> bool:
            data = item.data or {}
            return (
                str(data.get("status") or "Active").strip().casefold() not in closed_statuses
                and not str(data.get("type") or "").strip().casefold().startswith("former")
            )

        def is_marriage(item: Record) -> bool:
            data = item.data or {}
            return bool(data.get("legally_married")) or "marriage" in str(data.get("type") or "").casefold()

        active_marriage = next((item for item in pair_records if is_active(item) and is_marriage(item)), None)
        if active_marriage:
            request.session["sim_notice"] = f"{second.label} is already connected as a spouse. The existing marriage was kept."
            return RedirectResponse(f"/sims/{first.id}#family", status_code=303)

        relationship = next((item for item in pair_records if is_active(item)), None)
        start = int_or_none(marriage_global_day)
        if start is None:
            start = save.global_day
        entered_location = str(location or "").strip()
        entered_notes = str(notes or "").strip()
        if relationship:
            base = relationship.version
            data = dict(relationship.data or {})
            data.update({
                "partner1_id": first.id,
                "partner2_id": second.id,
                "partner1_name": first.label,
                "partner2_name": second.label,
                "type": "Marriage",
                "status": "Active",
                "start_global_day": start,
                "marriage_global_day": start,
                "end_global_day": None,
                "location": entered_location or str(data.get("location") or ""),
                "legally_married": True,
                "surname_rule": surname_rule,
                "children_count": int_or_none(data.get("children_count")) or 0,
                "notes": entered_notes or str(data.get("notes") or ""),
            })
            for key in ("marriage_game_hour", "marriage_game_minute", "marriage_time", "historical_marriage_date", "historical_marriage_date_range", "marriage_date_precision"):
                data.pop(key, None)
            data.update(marriage_calendar_fields(save, start, marriage_game_hour, marriage_game_minute))
            relationship.global_day = start
            relationship.data = data
            relationship.version += 1
            action = "converted the existing relationship into a marriage"
        else:
            base = 0
            data = {
                "partner1_id": first.id,
                "partner2_id": second.id,
                "partner1_name": first.label,
                "partner2_name": second.label,
                "type": "Marriage",
                "status": "Active",
                "start_global_day": start,
                "marriage_global_day": start,
                "end_global_day": None,
                "location": entered_location,
                "legally_married": True,
                "surname_rule": surname_rule,
                "children_count": 0,
                "notes": entered_notes,
            }
            data.update(marriage_calendar_fields(save, start, marriage_game_hour, marriage_game_minute))
            relationship = Record(
                save_id=save.id,
                kind="relationship",
                label=f"{first.label} & {second.label}",
                global_day=start,
                data=data,
            )
            session.add(relationship)
            session.flush()
            action = "created a marriage"

        name_changes = domain.apply_married_surnames(session, relationship, first, second, surname_rule)
        relationship.label = f"{first.label} & {second.label}"
        relationship.data = {**relationship.data, "partner1_name": first.label, "partner2_name": second.label}
        session.add(Change(
            save_id=save.id,
            device_id="local" if settings.local_mode else "web",
            record_id=relationship.id,
            kind=relationship.kind,
            operation="upsert",
            base_version=base,
            new_version=relationship.version,
            payload=sync.serialize(relationship),
        ))
        save.revision += 1 + name_changes + domain.sync_generations(session, save)
        domain.schedule_marriage_rolls(session, save)
        request.session["sim_notice"] = f"Added {second.label} as spouse and {action}. Both Sim profiles now share this marriage."
    return RedirectResponse(f"/sims/{sim_id}#family", status_code=303)


@app.post("/sims/{sim_id}/purge")
def purge_sim(request: Request, sim_id: str, confirm_name: str = Form(...)):
    with db() as session:
        record = session.get(Record, sim_id)
        if not record or record.kind != "sim" or record.deleted:
            raise HTTPException(404)
        save = owned_save(request, session, record.save_id)
        if confirm_name.strip() != record.label:
            raise HTTPException(400, "The confirmation name did not match. Nothing was deleted.")
        domain.purge_sim(session, save, record)
    return RedirectResponse("/p/sims", status_code=303)


@app.post("/relationships")
def add_relationship(request: Request, partner1_id: str = Form(...), partner2_id: str = Form(...), relationship_type: str = Form("Marriage"), status: str = Form("Active"), start_global_day: str = Form(""), marriage_game_hour: str = Form(""), marriage_game_minute: str = Form(""), end_global_day: str = Form(""), location: str = Form(""), legally_married: str = Form(""), surname_rule: str = Form("automatic"), children_count: str = Form(""), notes: str = Form("")):
    if partner1_id == partner2_id: raise HTTPException(400, "Choose two different Sims.")
    with db() as session:
        ctx = context(request, session); save = ctx["save"]; first=session.get(Record,partner1_id); second=session.get(Record,partner2_id)
        if not save or not first or not second or first.save_id != save.id or second.save_id != save.id: raise HTTPException(400)
        married=legally_married in {"1","true","on","yes"} or "marriage" in relationship_type.casefold();start=int_or_none(start_global_day)
        data={"partner1_id":first.id,"partner2_id":second.id,"partner1_name":first.label,"partner2_name":second.label,"type":relationship_type,"status":status,"start_global_day":start,"end_global_day":int_or_none(end_global_day),"location":location,"legally_married":married,"surname_rule":surname_rule,"children_count":int_or_none(children_count) or 0,"notes":notes}
        if married:
            data["marriage_global_day"]=start;data.update(marriage_calendar_fields(save,start,marriage_game_hour,marriage_game_minute))
        record=Record(save_id=save.id,kind="relationship",label=f"{first.label} & {second.label}",global_day=data["start_global_day"],data=data);session.add(record);session.flush();name_changes=domain.apply_married_surnames(session,record,first,second,surname_rule);record.label=f"{first.label} & {second.label}";record.data={**record.data,"partner1_name":first.label,"partner2_name":second.label};session.add(Change(save_id=save.id,device_id="local" if settings.local_mode else "web",record_id=record.id,kind=record.kind,operation="upsert",base_version=0,new_version=1,payload=sync.serialize(record)));save.revision+=1+name_changes+domain.sync_generations(session,save);domain.schedule_marriage_rolls(session,save)
    return RedirectResponse("/p/relationships",status_code=303)


@app.get("/relationships/{relationship_id}", response_class=HTMLResponse)
def relationship_profile(request: Request, relationship_id: str):
    with db() as session:
        relationship=session.get(Record,relationship_id)
        if not relationship or relationship.kind!="relationship" or relationship.deleted: raise HTTPException(404)
        save=owned_save(request,session,relationship.save_id)
        sims=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="sim",Record.deleted.is_(False)).order_by(Record.label)))
        sim_by_id={item.id:item for item in sims}
        partner_ids=((relationship.data or {}).get("partner1_id"),(relationship.data or {}).get("partner2_id"))
        partners=[sim_by_id[item_id] for item_id in partner_ids if item_id in sim_by_id]
        relationship_portraits=list(session.scalars(select(Portrait).where(Portrait.record_id==relationship.id).order_by(Portrait.created_at)))
        ctx=context(request,session,relationship=relationship,all_sims=sims,partners=partners,photo_record_ids=set(session.scalars(select(Portrait.record_id).where(Portrait.save_id==save.id))),relationship_portraits=relationship_portraits,portrait_notice=request.session.pop("portrait_notice",None),title=relationship.label,page="relationships")
        return templates.TemplateResponse(request,"relationship_profile.html",ctx)


@app.post("/relationships/{relationship_id}")
def edit_relationship(request: Request, relationship_id: str, partner1_id: str = Form(...), partner2_id: str = Form(...), relationship_type: str = Form("Marriage"), status: str = Form("Active"), start_global_day: str = Form(""), marriage_game_hour: str = Form(""), marriage_game_minute: str = Form(""), end_global_day: str = Form(""), location: str = Form(""), legally_married: str = Form(""), surname_rule: str = Form(""), children_count: str = Form(""), notes: str = Form("")):
    if partner1_id==partner2_id: raise HTTPException(400,"Choose two different Sims.")
    with db() as session:
        relationship=session.get(Record,relationship_id)
        if not relationship or relationship.kind!="relationship": raise HTTPException(404)
        save=owned_save(request,session,relationship.save_id);first=session.get(Record,partner1_id);second=session.get(Record,partner2_id)
        if not first or not second or first.save_id!=save.id or second.save_id!=save.id: raise HTTPException(400)
        base=relationship.version;data=dict(relationship.data or {});surname_rule=surname_rule or str(data.get("surname_rule") or "automatic");start=int_or_none(start_global_day);married=legally_married in {"1","true","on","yes"} or "marriage" in relationship_type.casefold();data.update({"partner1_id":first.id,"partner2_id":second.id,"partner1_name":first.label,"partner2_name":second.label,"type":relationship_type,"status":status,"start_global_day":start,"end_global_day":int_or_none(end_global_day),"location":location,"legally_married":married,"surname_rule":surname_rule,"children_count":int_or_none(children_count) or 0,"notes":notes})
        for key in ("marriage_global_day","marriage_game_hour","marriage_game_minute","marriage_time","historical_marriage_date","historical_marriage_date_range","marriage_date_precision"):
            data.pop(key,None)
        if married:
            data["marriage_global_day"]=start;data.update(marriage_calendar_fields(save,start,marriage_game_hour,marriage_game_minute))
        relationship.global_day=data["start_global_day"];relationship.data=data;name_changes=domain.apply_married_surnames(session,relationship,first,second,surname_rule);relationship.label=f"{first.label} & {second.label}";relationship.data={**relationship.data,"partner1_name":first.label,"partner2_name":second.label};relationship.version+=1
        session.add(Change(save_id=save.id,device_id="local" if settings.local_mode else "web",record_id=relationship.id,kind=relationship.kind,operation="upsert",base_version=base,new_version=relationship.version,payload=sync.serialize(relationship)));save.revision+=1+name_changes+domain.sync_generations(session,save);domain.schedule_marriage_rolls(session,save)
    return RedirectResponse(f"/relationships/{relationship_id}",status_code=303)


@app.post("/households")
async def add_household(request: Request):
    form = await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx["save"]
        if not save: raise HTTPException(400)
        name=str(form.get("name") or "").strip()
        if not name: raise HTTPException(400,"Household name is required.")
        head_id=str(form.get("head_sim_id") or "")
        if head_id:
            head=session.get(Record,head_id)
            if not head or head.kind!="sim" or head.save_id!=save.id: raise HTTPException(400,"Invalid household head.")
        data={"household_name":name,"branch_type":str(form.get("branch_type") or "Main"),"location":str(form.get("location") or ""),"social_class":str(form.get("social_class") or ""),"head_sim_id":head_id or None,"start_global_day":int_or_none(form.get("start_global_day")),"end_global_day":int_or_none(form.get("end_global_day")),"active":form.get("active") in {"1","true","on","yes"},"notes":str(form.get("notes") or "")}
        record=Record(save_id=save.id,kind="household",label=name,global_day=data["start_global_day"],data=data);session.add(record);session.flush();domain.journal(session,record,"upsert",0);save.revision+=1
        assign_household_members(session,save,record,form.getlist("member_ids"))
    return RedirectResponse(f"/households/{record.id}",status_code=303)


@app.get("/households/{household_id}", response_class=HTMLResponse)
def household_profile(request: Request, household_id: str):
    with db() as session:
        household=session.get(Record,household_id)
        if not household or household.kind!="household" or household.deleted: raise HTTPException(404)
        save=owned_save(request,session,household.save_id)
        sims=sorted(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="sim",Record.deleted.is_(False))),key=insights.sim_number,reverse=True)
        members=[sim for sim in sims if (sim.data or {}).get("current_household_id")==household.id]
        census=insights.household_census(insights.all_records(session,save.id),save)["rows"].get(household.id,{})
        ctx=context(request,session,household=household,all_sims=sims,members=members,household_census=census,photo_record_ids=set(session.scalars(select(Portrait.record_id).where(Portrait.save_id==save.id))),title=household.label,page="households")
        return templates.TemplateResponse(request,"household_profile.html",ctx)


@app.post("/households/{household_id}")
async def edit_household(request: Request, household_id: str):
    form=await request.form()
    with db() as session:
        household=session.get(Record,household_id)
        if not household or household.kind!="household": raise HTTPException(404)
        save=owned_save(request,session,household.save_id);name=str(form.get("name") or "").strip()
        if not name: raise HTTPException(400,"Household name is required.")
        head_id=str(form.get("head_sim_id") or "")
        if head_id:
            head=session.get(Record,head_id)
            if not head or head.kind!="sim" or head.save_id!=save.id: raise HTTPException(400,"Invalid household head.")
        base=household.version;data={**(household.data or {}),"household_name":name,"branch_type":str(form.get("branch_type") or "Main"),"location":str(form.get("location") or ""),"social_class":str(form.get("social_class") or ""),"head_sim_id":head_id or None,"start_global_day":int_or_none(form.get("start_global_day")),"end_global_day":int_or_none(form.get("end_global_day")),"active":form.get("active") in {"1","true","on","yes"},"notes":str(form.get("notes") or "")}
        household.label=name;household.global_day=data["start_global_day"];household.data=data;household.version+=1;domain.journal(session,household,"upsert",base);save.revision+=1
        assign_household_members(session,save,household,form.getlist("member_ids"))
    return RedirectResponse(f"/households/{household_id}",status_code=303)


@app.post("/pregnancies")
def add_pregnancy(request: Request, mother_id: str = Form(...), father_id: str = Form(""), conception_global_day: str = Form(...), due_global_day: str = Form(""), babies_expected: str = Form("1"), status: str = Form("Active"), maternal_rolls: str = Form("on"), newborn_rolls: str = Form("on"), notes: str = Form("")):
    with db() as session:
        ctx=context(request,session);save=ctx["save"];mother=session.get(Record,mother_id);father=session.get(Record,father_id) if father_id else None
        if not save or not mother or mother.save_id!=save.id: raise HTTPException(400)
        conception=int_or_none(conception_global_day)
        due=int_or_none(due_global_day) or ((conception or save.global_day)+save.pregnancy_days)
        expected=max(1,int_or_none(babies_expected) or 1)
        try: domain.validate_multiple_birth_count(session,save,due,expected)
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
        data={"mother_id":mother.id,"mother_name":mother.label,"father_id":father.id if father else None,"father_name":father.label if father else "","conception_global_day":conception,"due_global_day":due,"babies_expected":expected,"babies_delivered":0,"status":status,"maternal_rolls_required":maternal_rolls in {"1","true","on","yes"},"birth_newborn_rolls_required":newborn_rolls in {"1","true","on","yes"},"notes":notes}
        record=Record(save_id=save.id,kind="pregnancy",label=f"{mother.label} pregnancy",global_day=due,data=data);session.add(record);session.flush();domain.journal(session,record,"upsert",0);save.revision+=1;domain.schedule_rolls(session,save)
    return RedirectResponse(f"/pregnancies/{record.id}",status_code=303)


@app.get("/pregnancies/{pregnancy_id}", response_class=HTMLResponse)
def pregnancy_profile(request: Request, pregnancy_id: str):
    with db() as session:
        pregnancy=session.get(Record,pregnancy_id)
        if not pregnancy or pregnancy.kind!="pregnancy" or pregnancy.deleted: raise HTTPException(404)
        save=owned_save(request,session,pregnancy.save_id)
        sims=sorted(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="sim",Record.deleted.is_(False))),key=insights.sim_number,reverse=True)
        children=[sim for sim in sims if (sim.data or {}).get("pregnancy_id")==pregnancy.id]
        all_records=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind.in_(("sim","pregnancy","roll","game_history")),Record.deleted.is_(False))))
        progress=insights.pregnancy_dashboard(all_records,save)["rows"].get(pregnancy.id,{})
        progress_history=[item for item in all_records if item.kind=="game_history" and (item.data or {}).get("pregnancy_id")==pregnancy.id]
        progress_history.sort(key=lambda item:(item.global_day or 0,item.created_at),reverse=True)
        ctx=context(request,session,pregnancy=pregnancy,all_sims=sims,children=children,pregnancy_progress=progress,progress_history=progress_history[:30],photo_record_ids=set(session.scalars(select(Portrait.record_id).where(Portrait.save_id==save.id))),title=pregnancy.label,page="pregnancies")
        return templates.TemplateResponse(request,"pregnancy_profile.html",ctx)


@app.post("/pregnancies/{pregnancy_id}")
def edit_pregnancy(request: Request, pregnancy_id: str, mother_id: str = Form(...), father_id: str = Form(""), conception_global_day: str = Form(...), due_global_day: str = Form(""), actual_delivery_global_day: str = Form(""), babies_expected: str = Form("1"), babies_delivered: str = Form("0"), status: str = Form("Active"), outcome: str = Form(""), complication: str = Form(""), maternal_rolls: str = Form(""), newborn_rolls: str = Form(""), notes: str = Form("")):
    with db() as session:
        pregnancy=session.get(Record,pregnancy_id)
        if not pregnancy or pregnancy.kind!="pregnancy": raise HTTPException(404)
        save=owned_save(request,session,pregnancy.save_id);mother=session.get(Record,mother_id);father=session.get(Record,father_id) if father_id else None
        if not mother or mother.kind!="sim" or mother.save_id!=save.id or (father and (father.kind!="sim" or father.save_id!=save.id)): raise HTTPException(400)
        domain.retire_pregnancy_rolls(session,save,pregnancy.id,"Pregnancy details changed")
        conception=int_or_none(conception_global_day);due=int_or_none(due_global_day) or ((conception or save.global_day)+save.pregnancy_days);base=pregnancy.version
        expected=max(1,int_or_none(babies_expected) or 1)
        try: domain.validate_multiple_birth_count(session,save,due,expected)
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
        data={**(pregnancy.data or {}),"mother_id":mother.id,"mother_name":mother.label,"father_id":father.id if father else None,"father_name":father.label if father else "","conception_global_day":conception,"due_global_day":due,"actual_delivery_global_day":int_or_none(actual_delivery_global_day),"babies_expected":expected,"babies_delivered":max(0,int_or_none(babies_delivered) or 0),"status":status,"outcome":outcome,"complication":complication,"maternal_rolls_required":maternal_rolls in {"1","true","on","yes"},"birth_newborn_rolls_required":newborn_rolls in {"1","true","on","yes"},"notes":notes}
        pregnancy.label=f"{mother.label} pregnancy";pregnancy.global_day=due;pregnancy.data=data;pregnancy.version+=1;domain.journal(session,pregnancy,"upsert",base);save.revision+=1
        if str(status).casefold() not in domain.CLOSED_PREGNANCIES and data["maternal_rolls_required"]: domain.schedule_rolls(session,save)
    return RedirectResponse(f"/pregnancies/{pregnancy_id}",status_code=303)


@app.post("/pregnancies/{pregnancy_id}/newborns")
def add_pregnancy_newborn(request: Request, pregnancy_id: str, first_name: str = Form(...), last_name: str = Form(""), sex: str = Form(""), birth_global_day: str = Form(""), notes: str = Form("")):
    with db() as session:
        pregnancy=session.get(Record,pregnancy_id)
        if not pregnancy or pregnancy.kind!="pregnancy" or pregnancy.deleted: raise HTTPException(404)
        save=owned_save(request,session,pregnancy.save_id);data=dict(pregnancy.data or {});mother=session.get(Record,data.get("mother_id"));father=session.get(Record,data.get("father_id")) if data.get("father_id") else None
        if not mother: raise HTTPException(400,"The pregnancy needs a valid mother before a newborn can be added.")
        birth=int_or_none(birth_global_day) or int_or_none(data.get("actual_delivery_global_day")) or int_or_none(data.get("due_global_day")) or save.global_day
        name=" ".join(part.strip() for part in (first_name,last_name) if part.strip());sim_data={"sim_number":next_sim_number(session,save.id),"first_name":first_name.strip(),"last_name":last_name.strip(),"surname_at_birth":last_name.strip(),"maiden_name":last_name.strip(),"sex":sex,"birth_global_day":birth,"death_global_day":None,"mother_id":mother.id,"father_id":father.id if father else None,"current_household_id":mother.data.get("current_household_id"),"species_occult":"Human","pregnancy_id":pregnancy.id,"newborn_rolls_required":bool(data.get("birth_newborn_rolls_required",True)),"notes":notes}
        newborn=Record(save_id=save.id,kind="sim",label=name,global_day=birth,data=sim_data);session.add(newborn);session.flush();domain.journal(session,newborn,"upsert",0)
        base=pregnancy.version;delivered=int(data.get("babies_delivered") or 0)+1;expected=max(1,int(data.get("babies_expected") or 1));data.update({"babies_delivered":delivered,"actual_delivery_global_day":birth,"delivery_global_day":birth,"status":"Delivered" if delivered>=expected else "Active","outcome":data.get("outcome") or "Live birth"});pregnancy.data=data;pregnancy.version+=1;domain.journal(session,pregnancy,"upsert",base);save.revision+=2+domain.sync_generations(session,save)
        if data["status"]=="Delivered": save.revision+=domain.retire_pregnancy_rolls(session,save,pregnancy.id,"Pregnancy delivered")
        domain.schedule_rolls(session,save)
    return RedirectResponse(f"/pregnancies/{pregnancy_id}",status_code=303)


@app.post("/illnesses")
def add_illness(request: Request, sim_id: str = Form(...), illness_name: str = Form(...), onset_global_day: str = Form(""), end_global_day: str = Form(""), status: str = Form("Active"), severity: str = Form("Moderate"), contagious: str = Form(""), treatment: str = Form(""), outcome: str = Form(""), notes: str = Form("")):
    with db() as session:
        ctx=context(request,session);save=ctx["save"];sim=session.get(Record,sim_id)
        if not save or not sim or sim.kind!="sim" or sim.save_id!=save.id: raise HTTPException(400)
        onset=int_or_none(onset_global_day) or save.global_day
        data={"sim_id":sim.id,"sim_name":sim.label,"illness_name":illness_name.strip(),"onset_global_day":onset,"end_global_day":int_or_none(end_global_day),"status":status,"severity":severity,"contagious":contagious in {"1","true","on","yes"},"treatment":treatment,"outcome":outcome,"notes":notes}
        record=Record(save_id=save.id,kind="illness",label=f"{sim.label} — {illness_name.strip()}",global_day=onset,data=data);session.add(record);session.flush();session.add(Change(save_id=save.id,device_id="local" if settings.local_mode else "web",record_id=record.id,kind=record.kind,operation="upsert",base_version=0,new_version=1,payload=sync.serialize(record)));save.revision+=1
    return RedirectResponse("/p/illnesses",status_code=303)


@app.post("/illnesses/{illness_id}")
def edit_illness(request: Request, illness_id: str, sim_id: str = Form(...), illness_name: str = Form(...), onset_global_day: str = Form(""), end_global_day: str = Form(""), status: str = Form("Active"), severity: str = Form("Moderate"), contagious: str = Form(""), treatment: str = Form(""), outcome: str = Form(""), notes: str = Form("")):
    with db() as session:
        record=session.get(Record,illness_id);sim=session.get(Record,sim_id)
        if not record or record.kind!="illness": raise HTTPException(404)
        save=owned_save(request,session,record.save_id)
        if not sim or sim.kind!="sim" or sim.save_id!=save.id: raise HTTPException(400)
        closed=status.lower() in {"recovered","resolved","deceased","ended","closed"};end=int_or_none(end_global_day) or (save.global_day if closed else None);base=record.version
        record.data={"sim_id":sim.id,"sim_name":sim.label,"illness_name":illness_name.strip(),"onset_global_day":int_or_none(onset_global_day),"end_global_day":end,"status":status,"severity":severity,"contagious":contagious in {"1","true","on","yes"},"treatment":treatment,"outcome":outcome,"notes":notes};record.label=f"{sim.label} — {illness_name.strip()}";record.global_day=record.data["onset_global_day"];record.version+=1
        session.add(Change(save_id=save.id,device_id="local" if settings.local_mode else "web",record_id=record.id,kind=record.kind,operation="upsert",base_version=base,new_version=record.version,payload=sync.serialize(record)));save.revision+=1
    return RedirectResponse("/p/illnesses",status_code=303)


@app.post("/illnesses/{illness_id}/resolve")
def resolve_illness(request: Request, illness_id: str):
    with db() as session:
        record=session.get(Record,illness_id)
        if not record or record.kind!="illness": raise HTTPException(404)
        save=owned_save(request,session,record.save_id);base=record.version;data=dict(record.data or {});data.update({"status":"Recovered","outcome":data.get("outcome") or "Recovered","end_global_day":save.global_day});record.data=data;record.version+=1
        session.add(Change(save_id=save.id,device_id="local" if settings.local_mode else "web",record_id=record.id,kind=record.kind,operation="upsert",base_version=base,new_version=record.version,payload=sync.serialize(record)));save.revision+=1
    return RedirectResponse("/p/illnesses",status_code=303)


@app.post("/illness-signatures")
def add_illness_signature(request: Request, illness_name: str = Form(...), pattern: str = Form(...), match_type: str = Form("contains")):
    if match_type not in {"contains","exact","hash"}: raise HTTPException(400,"Choose contains, exact, or hash matching.")
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400)
        clean_pattern=pattern.strip();clean_name=illness_name.strip()
        if not clean_pattern or not clean_name: raise HTTPException(400,"Illness name and detector value are required.")
        row=Record(save_id=save.id,kind="illness_signature",label=clean_name,data={"illness_name":clean_name,"pattern":clean_pattern,"match_type":match_type,"active":True})
        session.add(row);session.flush();domain.journal(session,row,"upsert",0);save.revision+=1
    return RedirectResponse("/p/illnesses",status_code=303)


@app.post("/automation/{candidate_id}/dismiss")
def dismiss_automation(request: Request, candidate_id: str):
    with db() as session:
        item=session.get(Record,candidate_id)
        if not item or item.kind!="game_candidate": raise HTTPException(404)
        save=owned_save(request,session,item.save_id);base=item.version;item.data={**item.data,"status":"dismissed"};item.version+=1;domain.journal(session,item,"upsert",base);save.revision+=1
    return RedirectResponse("/p/automation",status_code=303)


@app.post("/automation/{candidate_id}/accept")
async def accept_automation(request: Request, candidate_id: str):
    form = await request.form()
    with db() as session:
        item=session.get(Record,candidate_id)
        if not item or item.kind!="game_candidate" or item.data.get("status")!="pending": raise HTTPException(404)
        save=owned_save(request,session,item.save_id);action=item.data.get("action");payload=item.data.get("payload") or item.data;sim=session.get(Record,item.data.get("sim_id")) if item.data.get("sim_id") else None;resolved_record=sim
        def value(name, default=""):
            return form.get(name) if name in form else payload.get(name, default)
        def checked(name, default=True):
            return str(form.get(name, "")).casefold() in {"1","true","on","yes"} if form else bool(default)
        def chosen_sim(name):
            record=session.get(Record,str(form.get(name) or "")) if form.get(name) else None
            return record if record and record.kind=="sim" and record.save_id==save.id and not record.deleted else None
        def chosen_household(name="household_id"):
            record=session.get(Record,str(form.get(name) or "")) if form.get(name) else None
            return record if record and record.kind=="household" and record.save_id==save.id and not record.deleted else None
        def payload_sim(name):
            record=session.get(Record,str(payload.get(name) or "")) if payload.get(name) else None
            return record if record and record.kind=="sim" and record.save_id==save.id and not record.deleted else None
        def payload_household(name):
            record=session.get(Record,str(payload.get(name) or "")) if payload.get(name) else None
            return record if record and record.kind=="household" and record.save_id==save.id and not record.deleted else None
        if action=="unknown_illness" and sim:
            illness_name=str(value("illness_name",payload.get("suggested_name") or "Unclassified illness") or "Unclassified illness").strip()
            onset=int_or_none(value("onset_global_day")) or save.global_day
            severity=str(value("severity","Moderate") or "Moderate")
            source_key=str(item.data.get("source_key") or f"reviewed-illness:{sim.id}:{payload.get('raw_trait','')}")
            illness=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="illness",Record.deleted.is_(False),Record.data["source_key"].as_string()==source_key))
            if not illness:
                illness=Record(save_id=save.id,kind="illness",label=f"{sim.label} — {illness_name}",global_day=onset,data={"sim_id":sim.id,"sim_name":sim.label,"illness_name":illness_name,"onset_global_day":onset,"end_global_day":None,"status":"Active","severity":severity,"source":"Reviewed Clock Sync trait","source_key":source_key,"raw_trait":payload.get("raw_trait")})
                session.add(illness);session.flush();domain.journal(session,illness,"upsert",0);save.revision+=1
            resolved_record=illness
            if checked("remember_signature",True):
                raw=str(payload.get("raw_trait") or "").strip()
                if raw:
                    signature=Record(save_id=save.id,kind="illness_signature",label=illness_name,data={"illness_name":illness_name,"pattern":raw,"match_type":"hash" if raw.casefold().startswith("hash:") else "exact","active":True,"learned_from_candidate_id":item.id})
                    session.add(signature);session.flush();domain.journal(session,signature,"upsert",0);save.revision+=1
        elif action in {"illness_detected","illness_recovered"} and sim:
            illness=session.get(Record,str(payload.get("illness_record_id") or "")) if payload.get("illness_record_id") else None
            if illness and illness.kind=="illness" and illness.save_id==save.id and not illness.deleted:
                illness_name=str(value("illness_name",illness.data.get("illness_name") or illness.label) or illness.label).strip()
                base=illness.version;illness_data=dict(illness.data or {})
                if action=="illness_detected":
                    onset=int_or_none(value("onset_global_day",illness_data.get("onset_global_day"))) or save.global_day
                    status=str(value("status",illness_data.get("status") or "Active") or "Active")
                    illness.global_day=onset
                    illness_data.update({"illness_name":illness_name,"onset_global_day":onset,"status":status,
                                         "severity":str(value("severity",illness_data.get("severity") or "Unrated") or "Unrated"),
                                         "contagious":checked("contagious",bool(illness_data.get("contagious"))),
                                         "end_global_day":None if status.casefold() not in domain.CLOSED_ILLNESSES else illness_data.get("end_global_day")})
                else:
                    status=str(value("status","Recovered") or "Recovered")
                    recovery=int_or_none(value("recovery_global_day",illness_data.get("end_global_day"))) or save.global_day
                    illness_data.update({"illness_name":illness_name,"status":status,
                                         "end_global_day":recovery if status.casefold() in domain.CLOSED_ILLNESSES else None,
                                         "outcome":str(value("outcome",illness_data.get("outcome") or "No longer detected in game") or "")})
                illness.label=f"{sim.label} — {illness_name}";illness.data=illness_data;illness.version+=1
                domain.journal(session,illness,"upsert",base);resolved_record=illness;save.revision+=1
        elif action in {"new_sim","new_baby"}:
            first=str(value("first_name") or "").strip();last=str(value("last_name") or "").strip();name=" ".join(x for x in (first,last) if x) or item.label
            birth_estimate=clock.estimate_new_sim_birth(session,save,payload,item.global_day) if action=="new_sim" else {}
            submitted_birth=int_or_none(value("birth_global_day"));submitted_birth_year=int_or_none(value("birth_year"));submitted_age=int_or_none(value("age_days"))
            birth=submitted_birth if submitted_birth is not None else (save.global_day if action=="new_baby" else int(birth_estimate.get("estimated_birth_global_day",save.global_day-(submitted_age or 0))))
            birth_hour=int_or_none(value("birth_game_hour",item.data.get("hour") if action=="new_baby" else ""));birth_minute=int_or_none(value("birth_game_minute",item.data.get("minute") if action=="new_baby" else ""))
            birth,reviewed_birth_fields=resolve_birth_input(save,birth,submitted_birth_year,birth_hour,birth_minute)
            home=chosen_household() if "household_id" in form else payload_household("inferred_household_id")
            mother=chosen_sim("mother_id") if "mother_id" in form else payload_sim("inferred_mother_id")
            father=chosen_sim("father_id") if "father_id" in form else payload_sim("inferred_father_id")
            pregnancy=session.get(Record,str(payload.get("pregnancy_id") or "")) if action=="new_baby" and payload.get("pregnancy_id") else None
            if pregnancy and (pregnancy.kind!="pregnancy" or pregnancy.save_id!=save.id or pregnancy.deleted): pregnancy=None
            accepted_estimate=bool(action=="new_sim" and submitted_birth_year is None and birth_estimate and birth==birth_estimate.get("estimated_birth_global_day"))
            occult=game_metadata.occult_identity(payload);species=str(value("species_occult",occult.get("display") or "Human") or "Human")
            sim_data={"sim_number":next_sim_number(session,save.id),"first_name":first or name,"last_name":last,"sex":str(value("sex") or ""),"birth_global_day":birth,"mother_id":mother.id if mother else None,"father_id":father.id if father else None,"current_household_id":home.id if home else None,"pregnancy_id":pregnancy.id if pregnancy else None,"game_sim_id":str(payload.get("game_sim_id") or ""),"game_household_id":payload.get("household_id"),"game_household_name":payload.get("household_name"),"game_age_stage":str(value("age_stage") or ""),"game_age_days_at_detection":submitted_age if submitted_age is not None else birth_estimate.get("estimated_age_days"),"game_age_progress_percentage":payload.get("age_progress_percentage"),"game_career":str(value("career") or ""),"game_education":str(value("education") or ""),"game_traits":detected_form_list(value("traits")),"game_skills":detected_form_list(value("skills")),"game_milestones":detected_form_list(value("milestones")),"parent_game_sim_ids":[str(v) for v in (payload.get("parent_game_sim_ids") or []) if v],"game_parents":[row for row in (payload.get("parents") or []) if isinstance(row,dict)],"last_game_world":payload.get("world_name"),"last_game_lot":payload.get("lot_name"),"last_household_funds":payload.get("household_funds")}
            sim_data["surname_at_birth"] = last
            sim_data["maiden_name"] = last
            sim_data["species_occult"] = species
            sim_data["game_occult_types"] = occult.get("types") or []
            if occult.get("display"): sim_data["game_occult_source"] = occult.get("source")
            if birth_estimate:
                sim_data.update({key:value for key,value in birth_estimate.items() if key.startswith("birth_estimate_") or key.startswith("estimated_birth_global_day_range_")})
                sim_data.update({"original_birth_estimate_global_day":birth_estimate.get("estimated_birth_global_day"),"birth_global_day_estimated":accepted_estimate and birth_estimate.get("birth_estimate_precision")!="reported-birth-day"})
            sim_data.update(reviewed_birth_fields);sim_data["birth_time_source"]=reviewed_birth_fields.get("birth_estimate_source") if submitted_birth_year is not None else "Clock Sync newborn detection" if action=="new_baby" and birth_hour is not None and birth_minute is not None else birth_estimate.get("birth_estimate_source") if accepted_estimate else "Reviewed manual birth day"
            sim=Record(save_id=save.id,kind="sim",label=name,global_day=birth,data=sim_data);session.add(sim);session.flush();domain.journal(session,sim,"upsert",0);resolved_record=sim
            if pregnancy:
                pregnancy_base=pregnancy.version;linked=list(pregnancy.data.get("linked_newborn_ids") or [])
                if sim.id not in linked: linked.append(sim.id)
                pregnancy.data={**pregnancy.data,"linked_newborn_ids":linked};pregnancy.version+=1;domain.journal(session,pregnancy,"upsert",pregnancy_base)
            # Process the complete first snapshot now.  Clock Sync only reports
            # changes, so waiting for a second report could otherwise lose an
            # existing death, pregnancy, marriage, illness, or parent link.
            clock._game_illnesses(session,save,sim,payload)
            automation.reconcile_sim(session,save,sim,payload)
            automation.resolve_parent_links(session,save)
            save.revision+=domain.sync_generations(session,save)
            domain.schedule_rolls(session,save)
        elif action=="sim_identity_change" and sim:
            first=str(value("first_name",payload.get("first_name")) or "").strip();last=str(value("last_name",payload.get("last_name")) or "").strip();sex=str(value("sex",payload.get("sex")) or "").strip();name=" ".join(part for part in (first,last) if part) or sim.label
            base=sim.version;sim.label=name;sim.data={**sim.data,"first_name":first or name,"last_name":last,"sex":sex or sim.data.get("sex"),"game_sex":sex or sim.data.get("game_sex")};sim.version+=1;domain.journal(session,sim,"upsert",base);resolved_record=sim
        elif action=="sim_death" and sim:
            death_day=int_or_none(value("death_global_day",payload.get("detected_tracker_global_day"))) or save.global_day;death_hour=int_or_none(value("death_game_hour",payload.get("detected_game_hour")));death_minute=int_or_none(value("death_game_minute",payload.get("detected_game_minute")));cause=str(value("cause_of_death",payload.get("death_type") or "Detected in game") or "Detected in game");place=str(value("death_place") or "");calendar=death_calendar_fields(save,death_day,death_hour,death_minute)
            base=sim.version;sim.data={**sim.data,"death_global_day":death_day,"cause_of_death":cause,"death_place":place,"death_confirmed":True,"death_time_source":"Clock Sync death transition" if death_hour is not None and death_minute is not None else "Reviewed detection",**calendar};sim.version+=1;domain.journal(session,sim,"upsert",base)
            death=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="death",Record.deleted.is_(False),Record.data["sim_id"].as_string()==sim.id,Record.global_day==death_day).order_by(Record.created_at.desc()).limit(1))
            if death:
                death_base=death.version;death.data={**death.data,"sim_id":sim.id,"cause":cause,"place":place,"death_global_day":death_day,"completed":True,"confirmed_global_day":death_day,"source_candidate_id":item.id,**calendar};death.version+=1;domain.journal(session,death,"upsert",death_base)
            else:
                death=Record(save_id=save.id,kind="death",label=f"Death of {sim.label}",global_day=death_day,data={"sim_id":sim.id,"cause":cause,"place":place,"death_global_day":death_day,"completed":True,"confirmed_global_day":death_day,"source":"game","source_candidate_id":item.id,**calendar});session.add(death);session.flush();domain.journal(session,death,"upsert",0)
            resolved_record=death;save.revision+=1+domain.end_illnesses_for_death(session,save,sim,death_day);domain.schedule_rolls(session,save)
        elif action=="household_change" and sim:
            home=chosen_household("tracker_household_id");base=sim.version;sim.data={**sim.data,"current_household_id":home.id if home else sim.data.get("current_household_id"),"game_household_id":str(value("game_household_id",payload.get("household_id")) or ""),"game_household_name":str(value("household_name") or ""),"last_game_world":str(value("world_name") or ""),"last_game_lot":str(value("lot_name") or "")};sim.version+=1;domain.journal(session,sim,"upsert",base)
        elif action=="pregnancy_discovered" and sim:
            active=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="pregnancy",Record.deleted.is_(False),Record.data["mother_id"].as_string()==sim.id).order_by(Record.global_day.desc()))
            if not active or str((active.data or {}).get("status") or "active").casefold() in domain.CLOSED_PREGNANCIES:
                conception=int_or_none(value("conception_global_day")) or save.global_day;due=int_or_none(value("due_global_day")) or conception+save.pregnancy_days
                father=chosen_sim("other_parent_id") or (session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="sim",Record.deleted.is_(False),Record.data["game_sim_id"].as_string()==str(payload.get("pregnancy_partner_game_sim_id") or payload.get("other_parent_game_sim_id") or ""))) if (payload.get("pregnancy_partner_game_sim_id") or payload.get("other_parent_game_sim_id")) else None)
                expected=max(1,int_or_none(value("babies_expected",payload.get("pregnancy_offspring_count"))) or int_or_none(payload.get("pregnancy_offspring_count")) or 1)
                try: domain.validate_multiple_birth_count(session,save,due,expected)
                except ValueError as exc: raise HTTPException(400,str(exc)) from exc
                pregnancy=Record(save_id=save.id,kind="pregnancy",label=f"{sim.label} pregnancy",global_day=due,data={"mother_id":sim.id,"mother_name":sim.label,"father_id":father.id if father else None,"father_name":father.label if father else "","conception_global_day":conception,"due_global_day":due,"babies_expected":expected,"babies_delivered":0,"status":"Active","maternal_rolls_required":checked("maternal_rolls_required",True),"birth_newborn_rolls_required":checked("birth_newborn_rolls_required",True),"source":"game","game_pregnancy_sequence":sim.data.get("game_pregnancy_sequence")});session.add(pregnancy);session.flush();domain.journal(session,pregnancy,"upsert",0);domain.schedule_rolls(session,save)
        elif action=="pregnancy_outcome" and sim:
            pregnancy=session.get(Record,str(payload.get("pregnancy_id") or "")) if payload.get("pregnancy_id") else None
            if not pregnancy or pregnancy.kind!="pregnancy" or pregnancy.save_id!=save.id: pregnancy=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="pregnancy",Record.deleted.is_(False),Record.data["mother_id"].as_string()==sim.id).order_by(Record.global_day.desc()))
            if pregnancy:
                status=str(value("status","Delivered") or "Delivered");delivery=int_or_none(value("delivery_global_day")) or save.global_day;detected=int_or_none(value("babies_delivered",payload.get("babies_delivered")));delivered=max(0,detected if detected is not None else (int_or_none(pregnancy.data.get("babies_expected")) or 1))
                if status.casefold() in {"miscarriage","cancelled","canceled"} and "babies_delivered" not in form: delivered=0
                delivery_hour=int_or_none(value("delivery_game_hour",payload.get("detected_game_hour")));delivery_minute=int_or_none(value("delivery_game_minute",payload.get("detected_game_minute")));delivery_exact=calendar_utils.exact_historical_label(delivery,delivery_hour,delivery_minute,save.start_year,save.days_per_year) if delivery_hour is not None and delivery_minute is not None else ""
                delivery_details={"delivery_game_hour":delivery_hour,"delivery_game_minute":delivery_minute,"delivery_time":f"{delivery_hour:02d}:{delivery_minute:02d}" if delivery_hour is not None and delivery_minute is not None else None,"historical_delivery_date":delivery_exact or None}
                base=pregnancy.version;pregnancy.data={**pregnancy.data,"status":status,"babies_delivered":delivered,"actual_delivery_global_day":delivery,"delivery_global_day":delivery,"outcome":str(value("outcome",status) or status),"complication":str(value("complication") or "") or None,**delivery_details};pregnancy.version+=1;domain.journal(session,pregnancy,"upsert",base);save.revision+=domain.retire_pregnancy_rolls(session,save,pregnancy.id,f"Pregnancy reviewed as {status}")
                resolved_record=pregnancy
        elif action=="relationship_change" and sim:
            other=chosen_sim("other_sim_id") or session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="sim",Record.data["game_sim_id"].as_string()==str(payload.get("other_game_sim_id") or "")))
            if other:
                category=str(value("relationship_type",payload.get("category") or "Relationship")).title();start=int_or_none(value("start_global_day",payload.get("detected_tracker_global_day"))) or save.global_day;married=checked("legally_married",category.casefold()=="marriage") or "marriage" in category.casefold();marriage_hour=int_or_none(value("marriage_game_hour",payload.get("detected_game_hour"))) if married else None;marriage_minute=int_or_none(value("marriage_game_minute",payload.get("detected_game_minute"))) if married else None
                existing_relationships=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="relationship",Record.deleted.is_(False))))
                rel=next((record for record in existing_relationships if {str((record.data or {}).get("partner1_id") or ""),str((record.data or {}).get("partner2_id") or "")}=={sim.id,other.id} and (("marriage" in str((record.data or {}).get("type") or "").casefold() or bool((record.data or {}).get("legally_married"))) if married else str((record.data or {}).get("type") or "").casefold()==category.casefold())),None)
                calendar=marriage_calendar_fields(save,start,marriage_hour,marriage_minute) if married else {}
                surname_rule=str(value("surname_rule","automatic") or "automatic")
                rel_data={"partner1_id":sim.id,"partner2_id":other.id,"partner1_name":sim.label,"partner2_name":other.label,"type":category,"status":str(value("relationship_status","Active") or "Active"),"start_global_day":start,"legally_married":married,"surname_rule":surname_rule,"game_detected":True,"source_candidate_id":item.id,**calendar}
                if married: rel_data["marriage_global_day"]=start;rel_data["marriage_time_source"]="Clock Sync marriage transition" if marriage_hour is not None and marriage_minute is not None else "Reviewed detection"
                if rel:
                    rel_base=rel.version;rel.global_day=start;rel.data={**rel.data,**rel_data}
                else:
                    rel_base=0;rel=Record(save_id=save.id,kind="relationship",label=f"{sim.label} & {other.label}",global_day=start,data=rel_data);session.add(rel);session.flush()
                name_changes=domain.apply_married_surnames(session,rel,sim,other,surname_rule);rel.label=f"{sim.label} & {other.label}";rel.data={**rel.data,"partner1_name":sim.label,"partner2_name":other.label}
                if rel_base: rel.version+=1
                domain.journal(session,rel,"upsert",rel_base)
                resolved_record=rel;save.revision+=1+name_changes+domain.sync_generations(session,save);domain.schedule_marriage_rolls(session,save)
        elif action=="relationship_end" and sim:
            other=chosen_sim("other_sim_id") or payload_sim("other_sim_id") or session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="sim",Record.deleted.is_(False),Record.data["game_sim_id"].as_string()==str(payload.get("other_game_sim_id") or "")))
            if other:
                category=str(value("relationship_type",payload.get("category") or "Relationship")).casefold()
                relationships=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="relationship",Record.deleted.is_(False))))
                matches=[record for record in relationships if {str((record.data or {}).get("partner1_id") or ""),str((record.data or {}).get("partner2_id") or "")}=={sim.id,other.id}]
                rel=next((record for record in matches if category in str((record.data or {}).get("type") or "relationship").casefold()),None) or (matches[0] if len(matches)==1 else None)
                if rel:
                    end_day=int_or_none(value("end_global_day",payload.get("detected_tracker_global_day"))) or save.global_day
                    end_hour=int_or_none(value("end_game_hour",payload.get("detected_game_hour")));end_minute=int_or_none(value("end_game_minute",payload.get("detected_game_minute")))
                    end_status=str(value("relationship_status","Ended") or "Ended")
                    historical_end=calendar_utils.exact_historical_label(end_day,end_hour,end_minute,save.start_year,save.days_per_year) if end_hour is not None and end_minute is not None else None
                    rel_base=rel.version;rel.data={**rel.data,"status":end_status,"end_global_day":end_day,"end_game_hour":end_hour,"end_game_minute":end_minute,"end_time":f"{end_hour:02d}:{end_minute:02d}" if end_hour is not None and end_minute is not None else None,"historical_end_date":historical_end,"legally_married":bool((rel.data or {}).get("legally_married")) and end_status.casefold()=="widowed","end_source":"Clock Sync relationship transition","source_candidate_id":item.id};rel.version+=1;domain.journal(session,rel,"upsert",rel_base);resolved_record=rel;save.revision+=1+domain.sync_generations(session,save);domain.schedule_marriage_rolls(session,save)
        automation.resolve_parent_links(session,save)
        reviewed={key:str(value) for key,value in form.multi_items() if key not in {"confirm"}}
        base=item.version;item.data={**item.data,"status":"accepted","reviewed_details":reviewed,"resolved_record_id":resolved_record.id if resolved_record else None};item.version+=1;domain.journal(session,item,"upsert",base);save.revision+=2
    return RedirectResponse("/p/automation",status_code=303)


@app.post("/records/{kind}")
def add_record(request: Request, kind: str, label: str = Form(...), global_day: int | None = Form(None), details: str = Form("")):
    if kind not in sync.SYNC_KINDS: raise HTTPException(400, "Unsupported record type")
    with db() as session:
        ctx = context(request, session)
        if not ctx["save"]: raise HTTPException(400, "Create a save first")
        try: data = json.loads(details) if details.strip() else {}
        except json.JSONDecodeError: data = {"notes": details}
        record = Record(save_id=ctx["save"].id, kind=kind, label=label.strip(), global_day=global_day, data=data)
        session.add(record); session.flush()
        session.add(Change(save_id=record.save_id, device_id="local" if settings.local_mode else "web", record_id=record.id, kind=record.kind, operation="upsert", base_version=0, new_version=record.version, payload=sync.serialize(record)))
        ctx["save"].revision += 1
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


@app.post("/records/{kind}/structured")
async def add_structured_record(request: Request, kind: str):
    allowed={"event","note","story_entry","roll_rule","death_causes","planner_rule","multiple_birth_rule","era_guidance","occult_rule","play_rotation","family_plan","campaign","service"}
    if kind not in allowed: raise HTTPException(400,"Unsupported record type")
    form=await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx["save"]
        if not save: raise HTTPException(400,"Choose a save first")
        label=str(form.get("label") or form.get("title") or form.get("name") or kind.replace("_"," ").title()).strip()
        data=structured_form_data(form)
        if kind=="event":
            data.setdefault("start_global_day",int_or_none(form.get("global_day")) or save.global_day);data.setdefault("end_global_day",data.get("start_global_day"));data.setdefault("active",True)
            if "die" in form: data["configured_die"]=str(form.get("die") or "").strip()
            if "bad_results" in form: data["configured_bad_results"]=str(form.get("bad_results") or "").strip()
        if kind in {"note","story_entry"}: data.setdefault("body",str(form.get("body") or form.get("notes") or "").strip())
        if kind=="story_entry" and data.get("narrator_sim_id"):
            narrator=session.get(Record,str(data["narrator_sim_id"]))
            data["narrator_name"]=narrator.label if narrator and narrator.save_id==save.id and narrator.kind=="sim" and not narrator.deleted else "Household chronicler"
        if kind in {"roll_rule","planner_rule","era_guidance","occult_rule"}: data.setdefault("active",True)
        day=int_or_none(form.get("global_day"))
        if day is None: day=int_or_none(data.get("start_global_day")) or int_or_none(data.get("conception_global_day"))
        record=Record(save_id=save.id,kind=kind,label=label,global_day=day,data=data);session.add(record);session.flush();domain.journal(session,record,"upsert",0);save.revision+=1
        if kind in {"roll_rule","occult_rule"}: domain.schedule_rolls(session,save)
        elif kind=="planner_rule": domain.schedule_marriage_rolls(session,save)
    return RedirectResponse(str(form.get("return_to") or request.headers.get("referer") or "/"),status_code=303)


@app.post("/records/{record_id}/edit")
async def edit_structured_record(request: Request, record_id: str):
    form=await request.form()
    with db() as session:
        record=session.get(Record,record_id)
        if not record: raise HTTPException(404)
        save=owned_save(request,session,record.save_id);base=record.version;data={**(record.data or {}),**structured_form_data(form)}
        if record.kind=="event":
            if "die" in form: data["configured_die"]=str(form.get("die") or "").strip()
            if "bad_results" in form: data["configured_bad_results"]=str(form.get("bad_results") or "").strip()
        record.label=str(form.get("label") or form.get("title") or form.get("name") or record.label).strip()
        day=int_or_none(form.get("global_day"))
        if day is None: day=int_or_none(data.get("start_global_day")) or int_or_none(data.get("conception_global_day")) or record.global_day
        record.global_day=day;record.data=data;record.version+=1;domain.journal(session,record,"upsert",base);save.revision+=1
        if record.kind in {"roll_rule","occult_rule"}: domain.schedule_rolls(session,save)
        elif record.kind=="planner_rule": domain.schedule_marriage_rolls(session,save)
    return RedirectResponse(str(form.get("return_to") or request.headers.get("referer") or "/"),status_code=303)


@app.post("/api/events/{event_id}/interest")
def update_event_interest(request: Request, event_id: str, hidden: str = Form("true"), return_to: str = Form("/p/events")):
    hide=hidden.casefold() in {"1","true","on","yes"}
    with db() as session:
        event=session.get(Record,event_id)
        if not event or event.kind!="event" or event.deleted: raise HTTPException(404)
        save=owned_save(request,session,event.save_id);stable_key=domain.event_key(event);changed=0
        related=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="event",Record.deleted.is_(False))))
        for item in related:
            if domain.event_key(item)!=stable_key or domain.event_is_ignored(item)==hide: continue
            base=item.version;data=dict(item.data or {})
            if hide:
                data.update({"ignored":True,"ignored_global_day":save.global_day})
            else:
                data.pop("ignored",None);data.pop("ignored_global_day",None)
            item.data=data;item.version+=1;domain.journal(session,item,"upsert",base);changed+=1
        save.revision+=changed
        if changed and not hide: domain.schedule_rolls(session,save)
        request.session["event_notice"]=(
            f"{event.label} is hidden. Its event rolls and Today entries will stay out of the way."
            if hide else f"{event.label} is visible again and can generate applicable event rolls."
        )
    destination=return_to.strip()
    if not destination.startswith("/") or destination.startswith("//"): destination="/p/events"
    return RedirectResponse(destination,status_code=303)


@app.post("/settings")
async def update_settings(request: Request):
    form=await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx["save"]
        if not save: raise HTTPException(400)
        save.name=str(form.get("name") or save.name).strip();save.start_year=max(-9999,min(9999,int_or_none(form.get("start_year")) or save.start_year))
        save.days_per_year=max(1,min(365,int_or_none(form.get("days_per_year")) or save.days_per_year));save.pregnancy_days=max(1,min(100,int_or_none(form.get("pregnancy_days")) or save.pregnancy_days))
        settings_data=dict(save.settings or {})
        for key in ("challenge_location","default_species","succession_system"):
            if key in form: settings_data[key]=str(form.get(key) or "").strip()
        for key in ("roll_tracking_start_day","try_for_baby_daily_limit","delivery_day_limit","elder_min_age_days","elder_max_age_days","marriage_min_age_days","inheritance_rule_cutoff_year","free_save_a_sims","full_moon_anchor_global_day","full_moon_interval_days"):
            if key in form and int_or_none(form.get(key)) is not None: settings_data[key]=int_or_none(form.get(key))
        for key in ("maternal_rolls_enabled","automatic_death_causes"):
            settings_data[key]=key in form
        save.settings=settings_data;save.revision+=1;domain.schedule_rolls(session,save)
    return RedirectResponse(str(form.get("return_to") or "/p/rules"),status_code=303)


@app.post("/defaults/install")
def install_defaults(request: Request):
    with db() as session:
        ctx=context(request,session);save=ctx["save"]
        if not save: raise HTTPException(400)
        domain.seed_defaults(session,save);domain.schedule_rolls(session,save)
    return RedirectResponse(request.headers.get("referer") or "/p/health",status_code=303)


@app.post("/api/health/repair-duplicate-obligations")
def repair_duplicate_obligations(request: Request):
    with db() as session:
        ctx=context(request,session);save=ctx["save"]
        if not save: raise HTTPException(400)
        result=domain.repair_duplicate_obligations(session,save)
        if result["archived"]:
            request.session["health_notice"]=(
                f"Archived {result['archived']} redundant pending obligation"
                f"{'s' if result['archived'] != 1 else ''}. Completed roll results were preserved."
            )
        elif result["protected_completed"]:
            request.session["health_notice"]=(
                "No pending duplicates needed repair. Duplicate completed results were left intact for audit history."
            )
        else:
            request.session["health_notice"]="No duplicate obligations need repair."
    return RedirectResponse("/p/health",status_code=303)


@app.post("/api/health/repair-duplicate-events")
def repair_duplicate_events(request: Request):
    with db() as session:
        ctx=context(request,session);save=ctx["save"]
        if not save: raise HTTPException(400)
        result=domain.repair_duplicate_events(session,save)
        if result["archived"]:
            request.session["health_notice"]=(
                f"Archived {result['archived']} duplicate event cop"
                f"{'ies' if result['archived'] != 1 else 'y'}, repointed {result['repointed']} linked record"
                f"{'s' if result['repointed'] != 1 else ''}, and archived {result['rolls_archived']} duplicate pending event roll"
                f"{'s' if result['rolls_archived'] != 1 else ''}."
            )
        else:
            request.session["health_notice"]="No duplicate events need repair."
    return RedirectResponse("/p/health",status_code=303)


@app.post("/api/occult-rolls/toggle")
def toggle_occult_rolls(request: Request, enabled: str = Form(""), return_to: str = Form("")):
    turn_on = enabled.casefold() in {"1", "true", "on", "yes"}
    with db() as session:
        ctx=context(request,session);save=ctx["save"]
        if not save: raise HTTPException(400)
        settings_data=dict(save.settings or {})
        was_on=bool(settings_data.get("automatic_occult_rolls",False))
        settings_data["automatic_occult_rolls"]=turn_on
        if turn_on and not was_on:
            settings_data["occult_rolls_enabled_from_global_day"]=save.global_day
        settings_data.setdefault("full_moon_anchor_global_day",1)
        settings_data.setdefault("full_moon_interval_days",8)
        save.settings=settings_data
        seeded=domain.seed_occult_rules(session,save)
        save.revision+=seeded+1
        created=domain.schedule_occult_rolls(session,save) if turn_on else 0
        save.revision+=created
        request.session["occult_notice"]=(
            f"Occult roll auto-generation is on. {created} eligible roll{'s' if created!=1 else ''} scheduled from {len([item for item in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='sim',Record.deleted.is_(False))) if occult_rules.sim_occult_types(item.data)])} detected occult Sims."
            if turn_on else "Occult roll auto-generation is off. Existing pending rolls were kept for review; no new occult rolls will be created."
        )
    destination = return_to.strip()
    if not destination.startswith("/") or destination.startswith("//"):
        destination = "/p/rules#occult-rules"
    return RedirectResponse(destination,status_code=303)


@app.post("/api/occult-rolls/create")
def create_occult_followup(request: Request, rule_id: str = Form(...), sim_id: str = Form(...), global_day: int | None = Form(None)):
    with db() as session:
        ctx=context(request,session);save=ctx["save"]
        if not save: raise HTTPException(400)
        rule=session.get(Record,rule_id);sim=session.get(Record,sim_id)
        if not rule or rule.save_id!=save.id or rule.kind!="occult_rule" or rule.deleted: raise HTTPException(400,"Choose a valid occult rule.")
        if not sim or sim.save_id!=save.id or sim.kind!="sim" or sim.deleted: raise HTTPException(400,"Choose a valid Sim.")
        due=max(1,int(global_day or save.global_day))
        try: roll,created=create_rule_roll_record(session,save,rule,sim,due)
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
        save.revision+=int(created)
        request.session["occult_notice"]=f"Created {rule.label} for {sim.label} on Global Day {due}."
    return RedirectResponse("/p/rules#occult-rules",status_code=303)


@app.post("/api/rule-rolls/create")
def create_rule_workbench_roll(request: Request, rule_id: str = Form(...), sim_id: str = Form(...),
                               global_day: int | None = Form(None), origin_roll_id: str = Form(""),
                               context_note: str = Form(""), return_to: str = Form("/p/today#rule-workbench")):
    with db() as session:
        ctx=context(request,session);save=ctx["save"]
        if not save: raise HTTPException(400)
        rule=session.get(Record,rule_id);sim=session.get(Record,sim_id)
        if not rule or rule.save_id!=save.id or rule.deleted or not rule.kind.endswith("_rule") or not bool((rule.data or {}).get("active",True)):
            raise HTTPException(400,"Choose an active rule with a configured die.")
        if not sim or sim.save_id!=save.id or sim.kind!="sim" or sim.deleted: raise HTTPException(400,"Choose a valid Sim.")
        origin=None
        if origin_roll_id:
            origin=session.get(Record,origin_roll_id)
            if not origin or origin.save_id!=save.id or origin.kind!="roll" or origin.deleted or not bool((origin.data or {}).get("completed")) or not bool((origin.data or {}).get("triggered")):
                raise HTTPException(400,"That triggering outcome is no longer available.")
            if not rule_can_follow(origin,rule): raise HTTPException(400,"That rule is not an applicable follow-up for this outcome.")
        due=max(1,int(global_day or save.global_day))
        try: roll,created=create_rule_roll_record(session,save,rule,sim,due,origin=origin,context_note=context_note)
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
        if created and origin:
            base=origin.version;followups=list((origin.data or {}).get("rule_followup_ids") or [])
            if roll.id not in followups: followups.append(roll.id)
            origin.data={**(origin.data or {}),"rule_followup_ids":followups,"rule_followup_last_created_global_day":save.global_day}
            origin.version+=1;domain.journal(session,origin,"upsert",base)
        save.revision+=int(created)+int(bool(created and origin))
        request.session["rule_workbench_notice"]=(
            f"Added {rule.label} for {sim.label} on Global Day {due}." if created
            else f"That follow-up already exists for {sim.label}."
        )
    return RedirectResponse(return_to or "/p/today#rule-workbench",status_code=303)


@app.post("/api/rule-actions/{roll_id}/reviewed")
def finish_rule_action_review(request: Request, roll_id: str):
    with db() as session:
        roll=session.get(Record,roll_id)
        if not roll or roll.kind!="roll" or roll.deleted: raise HTTPException(404)
        save=owned_save(request,session,roll.save_id);base=roll.version
        roll.data={**(roll.data or {}),"rule_followup_reviewed":True,"rule_followup_reviewed_global_day":save.global_day}
        roll.version+=1;domain.journal(session,roll,"upsert",base);save.revision+=1
        request.session["rule_workbench_notice"]=f"Finished reviewing follow-ups for {roll.label}."
    return RedirectResponse("/p/today#rule-workbench",status_code=303)


@app.post("/records/{record_id}/delete")
def delete_record(request: Request, record_id: str):
    with db() as session:
        record = session.get(Record, record_id)
        if not record: raise HTTPException(404)
        save=owned_save(request, session, record.save_id)
        if record.kind=="household":
            member_ids=[sim.id for sim in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="sim",Record.deleted.is_(False))) if (sim.data or {}).get("current_household_id")==record.id]
            record.data={**(record.data or {}),"archived_member_ids":member_ids}
            assign_household_members(session,save,record,[],include_head=False)
        if record.kind=="pregnancy":
            save.revision+=domain.retire_pregnancy_rolls(session,save,record.id,"Pregnancy archived")
        base = record.version
        record.deleted = True; record.version += 1
        session.add(Change(save_id=record.save_id, device_id="local" if settings.local_mode else "web", record_id=record.id, kind=record.kind, operation="delete", base_version=base, new_version=record.version, payload=sync.serialize(record)))
        save.revision+=1
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


@app.post("/records/{record_id}/restore")
def restore_record(request: Request, record_id: str):
    with db() as session:
        record=session.get(Record,record_id)
        if not record: raise HTTPException(404)
        save=owned_save(request,session,record.save_id);base=record.version;record.deleted=False;record.version+=1
        session.add(Change(save_id=save.id,device_id="local" if settings.local_mode else "web",record_id=record.id,kind=record.kind,operation="upsert",base_version=base,new_version=record.version,payload=sync.serialize(record)));save.revision+=1
        if record.kind=="household": assign_household_members(session,save,record,list((record.data or {}).get("archived_member_ids") or []))
        if record.kind=="pregnancy" and str((record.data or {}).get("status") or "active").casefold() not in domain.CLOSED_PREGNANCIES: domain.schedule_rolls(session,save)
    return RedirectResponse(request.headers.get("referer") or "/",status_code=303)


@app.post("/api/saves/{save_id}/advance")
def advance_day(request: Request, save_id: str, days: int = Form(1)):
    with db() as session:
        save = owned_save(request, session, save_id)
        step = max(-365, min(365, days))
        label = "Skipped 7 days" if step == 7 else "Changed Global Day"
        set_today_undo(request, label, save_global_day=save.global_day)
        save.global_day = max(1, min(20000, save.global_day + step))
        domain.schedule_rolls(session, save)
    return RedirectResponse("/p/today", status_code=303)


@app.post("/api/saves/{save_id}/set-day")
def set_global_day(request: Request, save_id: str, global_day: int = Form(...)):
    with db() as session:
        save=owned_save(request,session,save_id);set_today_undo(request,"Changed Global Day",save_global_day=save.global_day)
        save.global_day=max(1,min(20000,global_day));domain.schedule_rolls(session,save)
    return RedirectResponse("/p/today",status_code=303)


@app.post("/api/saves/{save_id}/today-focus")
def save_today_focus(request: Request, save_id: str, current_heir_id: str = Form(""), main_household_id: str = Form("")):
    with db() as session:
        save=owned_save(request,session,save_id);settings_data=dict(save.settings or {});settings_data.update({"current_heir_id":current_heir_id or None,"main_household_id":main_household_id or None});save.settings=settings_data;save.revision+=1
    return RedirectResponse("/p/today",status_code=303)


@app.post("/api/today/pregnancy-count-rolls")
def add_pregnancy_count_roll(request: Request, sim_id: str = Form(...)):
    with db() as session:
        ctx=context(request,session);save=ctx["save"];sim=session.get(Record,sim_id)
        if not save or not sim or sim.save_id!=save.id:
            raise HTTPException(400,"Choose a Sim from the active save.")
        try:
            roll,created=domain.create_pregnancy_count_roll(session,save,sim)
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc
        if created:
            set_today_undo(request,f"Added pregnancy-count roll for {sim.label}",delete_ids=[roll.id])
    return RedirectResponse("/p/today?task=rolls&roll_kind=pregnancy-count",status_code=303)


@app.post("/api/today/undo")
def undo_today(request: Request):
    undo=request.session.pop("today_undo",None)
    if not undo: return RedirectResponse("/p/today",status_code=303)
    with db() as session:
        save=None
        for snapshot in undo.get("records",[]):
            record=session.get(Record,snapshot["id"])
            if not record: continue
            save=owned_save(request,session,record.save_id);base=record.version;record.label=snapshot["label"];record.global_day=snapshot["global_day"];record.data=snapshot["data"];record.deleted=bool(snapshot.get("deleted"));record.version+=1;domain.journal(session,record,"delete" if record.deleted else "upsert",base)
        for record_id in undo.get("delete_ids",[]):
            record=session.get(Record,record_id)
            if record and not record.deleted:
                save=owned_save(request,session,record.save_id);base=record.version;record.deleted=True;record.version+=1;domain.journal(session,record,"delete",base)
        if undo.get("save_global_day") is not None:
            ctx=context(request,session);save=ctx["save"]
            if save: save.global_day=int(undo["save_global_day"])
        if save: save.revision+=1
    return RedirectResponse("/p/today",status_code=303)


@app.post("/api/today/pregnancies/{pregnancy_id}/resolve")
def resolve_today_pregnancy(request: Request, pregnancy_id: str, status: str = Form(...), babies_delivered: int = Form(0), outcome: str = Form(""), complication: str = Form("")):
    with db() as session:
        record=session.get(Record,pregnancy_id)
        if not record or record.kind!="pregnancy": raise HTTPException(404)
        save=owned_save(request,session,record.save_id);set_today_undo(request,f"Updated {record.label}",[record]);base=record.version;data=dict(record.data or {});data.update({"status":status,"babies_delivered":max(0,babies_delivered),"actual_delivery_global_day":save.global_day,"delivery_global_day":save.global_day,"outcome":outcome or status,"complication":complication or None});record.data=data;record.version+=1;domain.journal(session,record,"upsert",base);save.revision+=1
        if str(status).casefold() in domain.CLOSED_PREGNANCIES: save.revision+=domain.retire_pregnancy_rolls(session,save,record.id,f"Pregnancy resolved as {status}")
        domain.schedule_rolls(session,save)
    return RedirectResponse("/p/today?task=pregnancies",status_code=303)


@app.post("/api/today/illnesses/{illness_id}/recover")
def recover_today_illness(request: Request, illness_id: str):
    with db() as session:
        record=session.get(Record,illness_id)
        if not record or record.kind!="illness": raise HTTPException(404)
        save=owned_save(request,session,record.save_id);set_today_undo(request,f"Recovered {record.label}",[record]);base=record.version;record.data={**record.data,"status":"Recovered","outcome":record.data.get("outcome") or "Recovered","end_global_day":save.global_day};record.version+=1;domain.journal(session,record,"upsert",base);save.revision+=1
    return RedirectResponse("/p/today?task=illnesses",status_code=303)


@app.post("/api/today/illnesses")
def add_today_illness(request: Request, sim_id: str = Form(...), illness_name: str = Form(...), severity: str = Form("Moderate"), status: str = Form("Active"), contagious: str = Form(""), treatment: str = Form(""), notes: str = Form("")):
    with db() as session:
        ctx=context(request,session);save=ctx["save"];sim=session.get(Record,sim_id)
        if not save or not sim or sim.save_id!=save.id: raise HTTPException(400)
        data={"sim_id":sim.id,"sim_name":sim.label,"illness_name":illness_name.strip(),"onset_global_day":save.global_day,"end_global_day":None,"status":status,"severity":severity,"contagious":contagious in {"1","on","true","yes"},"treatment":treatment,"outcome":"","notes":notes};record=Record(save_id=save.id,kind="illness",label=f"{sim.label} — {illness_name.strip()}",global_day=save.global_day,data=data);session.add(record);session.flush();domain.journal(session,record,"upsert",0);save.revision+=1;set_today_undo(request,f"Added {record.label}",delete_ids=[record.id])
    return RedirectResponse("/p/today?task=illnesses",status_code=303)


@app.post("/api/today/deaths/{sim_id}/confirm")
def confirm_today_death(request: Request, sim_id: str, cause_of_death: str = Form(""), death_place: str = Form(""), death_game_hour: str = Form(""), death_game_minute: str = Form("")):
    with db() as session:
        sim=session.get(Record,sim_id)
        if not sim or sim.kind!="sim": raise HTTPException(404)
        save=owned_save(request,session,sim.save_id)
        data=dict(sim.data or {});death_day=int_or_none(data.get("death_global_day")) or save.global_day
        death=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="death",Record.deleted.is_(False),Record.data["sim_id"].as_string()==sim.id,Record.global_day==death_day).order_by(Record.created_at.desc()).limit(1))
        set_today_undo(request,f"Updated death details for {sim.label}",[sim] + ([death] if death else []));base=sim.version
        existing_time=str(data.get("death_time") or "");time_parts=existing_time.split(":",1)
        saved_hour=data.get("death_game_hour");saved_minute=data.get("death_game_minute")
        if saved_hour in (None,"") and len(time_parts)==2: saved_hour=int_or_none(time_parts[0])
        if saved_minute in (None,"") and len(time_parts)==2: saved_minute=int_or_none(time_parts[1])
        final_hour=saved_hour if saved_hour not in (None,"") else death_game_hour
        final_minute=saved_minute if saved_minute not in (None,"") else death_game_minute
        data.update({"death_global_day":death_day,"cause_of_death":cause_of_death or data.get("cause_of_death"),"death_place":death_place or data.get("death_place"),"death_confirmed":True})
        if data.get("death_date") in (None,""): data["death_date"]=challenge_date_label(save,death_day)
        for key,value in death_calendar_fields(save,death_day,final_hour,final_minute).items():
            if data.get(key) in (None,""): data[key]=value
        sim.data=data;sim.version+=1;domain.journal(session,sim,"upsert",base);save.revision+=1+domain.end_illnesses_for_death(session,save,sim,death_day)
        if death:
            death_base=death.version;death_data={**death.data,"sim_id":sim.id,"death_global_day":death_day,"completed":True,"confirmed_global_day":save.global_day,"cause":data.get("cause_of_death"),"place":data.get("death_place")}
            for key,value in death_calendar_fields(save,death_day,final_hour,final_minute).items():
                if death_data.get(key) in (None,""): death_data[key]=value
            death.data=death_data;death.version+=1;domain.journal(session,death,"upsert",death_base);save.revision+=1
        else:
            death_data={"sim_id":sim.id,"death_global_day":death_day,"completed":True,"confirmed_global_day":save.global_day,"cause":data.get("cause_of_death"),"place":data.get("death_place"),"source":"today"}
            death_data.update(death_calendar_fields(save,death_day,final_hour,final_minute))
            death=Record(save_id=save.id,kind="death",label=f"Death of {sim.label}",global_day=death_day,data=death_data)
            session.add(death);session.flush();domain.journal(session,death,"upsert",0);save.revision+=1
        domain.schedule_rolls(session,save)
    return RedirectResponse("/p/today?task=deaths",status_code=303)


@app.post("/api/rolls/{roll_id}/complete")
def complete_roll(request: Request, roll_id: str, actual: int = Form(...), outcome: str = Form("")):
    with db() as session:
        roll = session.get(Record, roll_id)
        if not roll: raise HTTPException(404)
        save = owned_save(request, session, roll.save_id)
        related=[roll];sim=session.get(Record,roll.data.get("sim_id")) if roll.data.get("sim_id") else None
        if sim:
            related.append(sim)
            related.extend(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="illness",Record.deleted.is_(False),Record.data["sim_id"].as_string()==sim.id)))
            related.extend(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="death",Record.deleted.is_(False),Record.data["sim_id"].as_string()==sim.id)))
        set_today_undo(request,f"Completed {roll.label}",related);result=domain.complete_roll(session,save,roll,actual,outcome)
        if result.get("death_created"): request.session["today_undo"]["delete_ids"]=[result["death"]["id"]]
        request.session["last_roll"] = {
            "number": actual, "die": roll.data.get("die") or "die", "roll": roll.label,
            "outcome": result["outcome"], "failed": result["outcome"] == "Failed",
        }
    return RedirectResponse(request.headers.get("referer") or "/p/today", status_code=303)


@app.post("/api/rolls/{roll_id}/roll")
def roll_and_complete(request: Request, roll_id: str):
    """Roll the record's configured die, audit it, and save its outcome."""
    with db() as session:
        roll = session.get(Record, roll_id)
        if not roll or roll.kind != "roll" or roll.deleted: raise HTTPException(404)
        save = owned_save(request, session, roll.save_id)
        if bool((roll.data or {}).get("completed")): raise HTTPException(409, "That roll is already complete.")
        notation, die_label = dice.notation_for_roll(roll.data.get("die"), roll.data.get("bad_results"))
        related=[roll];sim=session.get(Record,roll.data.get("sim_id")) if roll.data.get("sim_id") else None
        if sim:
            related.append(sim)
            related.extend(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="illness",Record.deleted.is_(False),Record.data["sim_id"].as_string()==sim.id)))
            related.extend(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="death",Record.deleted.is_(False),Record.data["sim_id"].as_string()==sim.id)))
        audit = dice.audited_roll(session, notation, save.id, "roll", roll.id)
        set_today_undo(request,f"Completed {roll.label}",related);result=domain.complete_roll(session, save, roll, audit.total)
        if result.get("death_created"): request.session["today_undo"]["delete_ids"]=[result["death"]["id"]]
        request.session["last_roll"] = {
            "number": audit.total, "die": die_label, "roll": roll.label,
            "outcome": roll.data.get("outcome", "Completed"),
            "failed": roll.data.get("outcome") == "Failed",
        }
    return RedirectResponse(request.headers.get("referer") or "/p/today", status_code=303)


@app.post("/api/rolls/{roll_id}/reopen")
def reopen_roll(request: Request, roll_id: str):
    """Return a completed roll to review and safely reverse its automatic effects."""
    with db() as session:
        roll=session.get(Record,roll_id)
        if not roll or roll.kind!="roll" or roll.deleted: raise HTTPException(404)
        save=owned_save(request,session,roll.save_id)
        if not bool((roll.data or {}).get("completed")): return RedirectResponse(request.headers.get("referer") or "/p/rolls",status_code=303)
        sim=session.get(Record,str((roll.data or {}).get("sim_id") or "")) if (roll.data or {}).get("sim_id") else None
        if sim and bool((sim.data or {}).get("death_confirmed")) and (sim.data or {}).get("death_source_roll_id")==roll.id:
            raise HTTPException(409,"This death has already been confirmed. Correct the Sim profile instead of reopening the roll.")
        related=[roll]
        auto_deaths=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="death",Record.deleted.is_(False),Record.data["source_roll_id"].as_string()==roll.id)))
        related.extend(auto_deaths)
        if sim: related.append(sim)
        set_today_undo(request,f"Reopened {roll.label}",related)
        for death in auto_deaths:
            base=death.version;data=dict(death.data or {});prior=int_or_none(data.get("rescheduled_from_global_day"));prior_cause=data.get("rescheduled_from_cause")
            if prior is None:
                death.deleted=True;death.data={**data,"correction_note":"Automatic death withdrawn when its roll was reopened"};death.version+=1;domain.journal(session,death,"delete",base)
            else:
                for key in ("source_roll_id","rescheduled_from_global_day","rescheduled_from_cause","historical_death_date","death_game_hour","death_game_minute","death_time"): data.pop(key,None)
                data.update({"cause":prior_cause or "Player choice","historical_death_date_range":calendar_utils.date_range_label(prior,save.start_year,save.days_per_year),"death_date_precision":"challenge-day-only"});death.global_day=prior;death.data=data;death.version+=1;domain.journal(session,death,"upsert",base)
        if sim and (sim.data or {}).get("death_source_roll_id")==roll.id:
            base=sim.version;data=dict(sim.data or {});automatic_day=int_or_none(data.get("death_global_day"));prior=int_or_none(data.get("rescheduled_from_global_day"));prior_cause=data.get("rescheduled_from_cause")
            for key in ("death_source_roll_id","rescheduled_from_global_day","rescheduled_from_cause","historical_death_date","historical_death_date_range","death_date_precision","death_game_hour","death_game_minute","death_time"): data.pop(key,None)
            if prior is None:
                data.pop("death_global_day",None);data.pop("cause_of_death",None);data["death_confirmed"]=False
            else:
                data.update({"death_global_day":prior,"cause_of_death":prior_cause or "Player choice","death_confirmed":False,"historical_death_date_range":calendar_utils.date_range_label(prior,save.start_year,save.days_per_year),"death_date_precision":"challenge-day-only"})
            sim.data=data;sim.version+=1;domain.journal(session,sim,"upsert",base)
            for illness in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="illness",Record.deleted.is_(False),Record.data["sim_id"].as_string()==sim.id)):
                illness_data=dict(illness.data or {})
                if str(illness_data.get("outcome") or "").casefold()=="ended by death" and int_or_none(illness_data.get("end_global_day"))==automatic_day:
                    illness_base=illness.version;illness_data.update({"status":"Active","end_global_day":None,"outcome":""});illness.data=illness_data;illness.version+=1;domain.journal(session,illness,"upsert",illness_base)
        if bool((roll.data or {}).get("pregnancy_count_roll")) and sim:
            base=sim.version;data=dict(sim.data or {});allowances=dict(data.get("pregnancy_allowances") or {})
            allowances={year:value for year,value in allowances.items() if str((value or {}).get("roll_id") or "")!=roll.id};data["pregnancy_allowances"]=allowances
            if data.get("pregnancy_allowance_roll_id")==roll.id:
                for key in ("pregnancy_allowance_count","pregnancy_allowance_year","pregnancy_allowance_roll_id","pregnancy_allowance_recorded_global_day"): data.pop(key,None)
            sim.data=data;sim.version+=1;domain.journal(session,sim,"upsert",base)
        for retired in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.deleted.is_(True),Record.data["retired_by_death_roll_id"].as_string()==roll.id)):
            base=retired.version;data=dict(retired.data or {});data.pop("retired_reason",None);data.pop("retired_global_day",None);data.pop("retired_by_death_roll_id",None);retired.data=data;retired.deleted=False;retired.version+=1;domain.journal(session,retired,"upsert",base)
        base=roll.version;data=dict(roll.data or {})
        for key in ("actual","outcome","completed","completed_global_day","pregnancy_count","nonlethal"): data.pop(key,None)
        data["correction_note"]="Reopened for correction";roll.data=data;roll.version+=1;domain.journal(session,roll,"upsert",base);save.revision+=1
    return RedirectResponse(request.headers.get("referer") or "/p/rolls",status_code=303)


@app.post("/api/dice")
async def roll_die(request: Request):
    body = await request.json()
    with db() as session:
        audit = dice.audited_roll(session, str(body.get("notation", "d20")), body.get("save_id"), str(body.get("context", "practice")), str(body.get("context_id", "")))
        return {"audit_id": audit.id, "faces": audit.faces, "total": audit.total, "commitment": audit.commitment, "reveal": audit.reveal, "verified": dice.verify(audit)}


def token_device(session, authorization: str | None) -> Device:
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401)
    digest = hash_secret(authorization[7:].strip())
    device = session.scalar(select(Device).where(Device.token_hash == digest, Device.enabled.is_(True)))
    if not device: raise HTTPException(401, "Invalid or revoked device token")
    device.last_seen_at=datetime.now(timezone.utc)
    return device


@app.post("/api/sync/devices")
def create_device(request: Request, name: str = Form("My computer")):
    with db() as session:
        ctx = context(request, session)
        if not ctx["save"]: raise HTTPException(400)
        raw = token(32)
        device = Device(save_id=ctx["save"].id, name=name, token_hash=hash_secret(raw))
        session.add(device); session.flush()
        return JSONResponse({"device_id": device.id, "token": raw, "save_id": device.save_id})


@app.post("/api/sync/devices/{device_id}/revoke")
def revoke_device(request: Request, device_id: str):
    with db() as session:
        device=session.get(Device,device_id)
        if not device: raise HTTPException(404)
        owned_save(request,session,device.save_id);device.enabled=False
    return RedirectResponse("/p/sync",status_code=303)


@app.post("/api/sync/conflicts/{conflict_id}/resolve")
async def resolve_sync_conflict(request: Request, conflict_id: str):
    form=await request.form();keep=str(form.get("keep") or "")
    if keep not in {"server","desktop","merge"}: raise HTTPException(400,"Choose the hosted copy, desktop copy, or a field-by-field merge.")
    with db() as session:
        conflict=session.get(Conflict,conflict_id)
        if not conflict or conflict.status!="open": raise HTTPException(404)
        save=owned_save(request,session,conflict.save_id)
        incoming=dict(conflict.local_change or {});record=session.get(Record,conflict.record_id)
        if keep=="desktop": payload=dict(incoming.get("payload") or {});operation=str(incoming.get("operation") or "upsert")
        elif keep=="merge": payload=sync.merged_conflict_payload(conflict,set(str(value) for value in form.getlist("desktop_field")));operation="delete" if payload.get("deleted") else "upsert"
        else:
            if not record: raise HTTPException(409,"The hosted record no longer exists; choose desktop or merge.")
            payload=sync.serialize(record);operation="delete" if record.deleted else "upsert"
        label,day,data,payload_deleted=sync.unpack_payload(payload)
        if record is None:
            record=Record(id=conflict.record_id,save_id=save.id,kind=str(incoming.get("kind") or payload.get("kind") or "note"));session.add(record);session.flush();base=0
        else:
            base=record.version
        record.kind=str(incoming.get("kind") or payload.get("kind") or record.kind);record.label=label;record.global_day=day;record.data=data;record.deleted=operation=="delete" or payload_deleted;record.version=base+1;record.updated_by_device="conflict-resolution";domain.journal(session,record,"delete" if record.deleted else "upsert",base);save.revision+=1
        conflict.status=f"resolved_{keep}"
    return RedirectResponse("/p/sync",status_code=303)


@app.post("/api/sync/run")
def run_sync_now(request: Request):
    if not settings.local_mode: raise HTTPException(400,"Manual sync runs from the desktop edition.")
    try:
        from .sync_client import cycle
        result=cycle();request.session["sync_notice"]=f"Sync complete: {result.get('pushed',0)} sent, {result.get('pulled',0)} received, {result.get('conflicts',0)} conflicts."
    except Exception as exc:
        request.session["sync_notice"]=f"Sync could not connect: {str(exc)[:180]}"
    return RedirectResponse("/p/sync",status_code=303)


@app.post("/api/sync/configure-local")
def configure_local_sync(request: Request, remote_url: str = Form(...), device_token: str = Form(...), remote_save_id: str = Form(...)):
    if not settings.local_mode: raise HTTPException(400, "This endpoint belongs to the desktop edition.")
    with db() as session:
        ctx = context(request, session)
        if not ctx["save"]: raise HTTPException(400)
        from .sync_client import save_config
        save_config(remote_url, device_token, remote_save_id, ctx["save"].id)
    return RedirectResponse("/p/sync", status_code=303)


@app.get("/api/sync/pull")
def sync_pull(after: int = 0, limit: int = 500, authorization: str | None = Header(None)):
    with db() as session:
        device = token_device(session, authorization)
        return sync.pull(session, device.save_id, after, limit)


@app.post("/api/sync/push")
async def sync_push(request: Request, authorization: str | None = Header(None)):
    body = await request.json()
    with db() as session:
        device = token_device(session, authorization)
        save = session.get(ChronicleSave, device.save_id)
        results = [sync.apply_change(session, save, device, item) for item in body.get("changes", [])]
        return {"results": results, **sync.pull(session, save.id, int(body.get("after", 0)))}


def rotate_clock_link(session, save_id: str) -> str:
    """Create a fresh one-time Clock Sync credential for a save."""
    raw = token(32)
    existing = session.scalar(select(ClockLink).where(ClockLink.save_id == save_id))
    if existing:
        existing.token_hash = hash_secret(raw)
        existing.enabled = True
    else:
        session.add(ClockLink(save_id=save_id, token_hash=hash_secret(raw)))
    session.flush()
    return raw


@app.post("/api/clock/links")
def create_clock_link(request: Request):
    with db() as session:
        ctx = context(request, session)
        if not ctx["save"]: raise HTTPException(400)
        raw = rotate_clock_link(session, ctx["save"].id)
        base_url = str(request.base_url).rstrip("/") if settings.local_mode else settings.public_url
        return {"token": raw, "endpoint": f"{base_url}/api/clock/report", "works_offline": settings.local_mode}


@app.post("/api/clock/reanchor")
def reanchor_clock_link(request: Request):
    """Pair the last reported game day with the current tracker day without rotating the token."""
    with db() as session:
        ctx = context(request, session)
        save = ctx.get("save")
        if not save:
            raise HTTPException(400, "Open a save first.")
        link = session.scalar(select(ClockLink).where(ClockLink.save_id == save.id, ClockLink.enabled.is_(True)))
        if not link or link.last_game_day is None:
            raise HTTPException(400, "A game report is required before the clock can be re-anchored.")
        link.game_anchor_day = int(link.last_game_day)
        link.tracker_anchor_day = int(save.global_day)
        sync.sync_clock_state(session,save,link)
        request.session["clock_notice"] = f"Clock repaired: game day {link.last_game_day} now matches tracker Global Day {save.global_day}."
    return RedirectResponse("/p/clock", status_code=303)


@app.post("/api/game-save/scan")
async def scan_game_save(request: Request):
    if not settings.local_mode:
        raise HTTPException(400, "Direct save scanning is available in the desktop edition.")
    form = await request.form()
    file_name = str(form.get("file_name") or "")
    available = {item.path.name: item.path for item in save_scanner.discover_saves()}
    if file_name not in available:
        raise HTTPException(400, "Choose one of the detected primary Sims 4 saves.")
    with db() as session:
        ctx = context(request, session)
        save = ctx.get("save")
        if not save:
            raise HTTPException(400, "Open a tracker save first.")
        try:
            scan = save_scanner.inspect_save(available[file_name])
        except save_scanner.SaveScanError as exc:
            request.session["game_save_notice"] = str(exc)
        else:
            households, sims = save_scanner.relevant_population(scan)
            tracked = {str(item.data.get("game_sim_id") or ""):item for item in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="sim",Record.deleted.is_(False))) if item.data.get("game_sim_id")}
            for item in sims:
                match = tracked.get(str(item.get("game_sim_id") or ""))
                item["tracker_record_id"] = match.id if match else None
                item["tracker_record_label"] = match.label if match else None
            scan["relevant_households"], scan["relevant_sims"] = households, sims
            _SAVE_SCAN_CACHE[save.id] = scan
            request.session["game_save_notice"] = f"Read {len(sims)} Sims from {len(households)} active or player-owned households. Nothing has been imported yet."
    return RedirectResponse("/p/clock#save-scan", status_code=303)


@app.post("/api/game-save/apply")
async def apply_game_save_scan(request: Request):
    if not settings.local_mode:
        raise HTTPException(400, "Direct save scanning is available in the desktop edition.")
    form = await request.form()
    selected = {str(value) for value in form.getlist("game_sim_id") if value}
    with db() as session:
        ctx = context(request, session)
        save = ctx.get("save")
        if not save:
            raise HTTPException(400, "Open a tracker save first.")
        scan = _SAVE_SCAN_CACHE.get(save.id)
        if not scan:
            raise HTTPException(409, "Scan the game save again before importing.")
        allowed = {str(item.get("game_sim_id") or "") for item in scan.get("relevant_sims") or ()}
        selected &= allowed
        if not selected:
            raise HTTPException(400, "Select at least one Sim to reconcile.")
        result = save_scanner.reconcile_scan(session, save, scan, selected, str(form.get("advance_clock") or "").casefold() in {"1","true","on","yes"})
        request.session["game_save_notice"] = f"Save scan applied: {result['updated']} linked Sim(s) refreshed, {result['linked']} imported match(es) linked, {result['candidates']} review item(s) created, tracker advanced {result['advanced']} day(s)."
    return RedirectResponse("/p/automation", status_code=303)


@app.get("/downloads/clock-sync")
def download_clock_sync(request: Request):
    with db() as session:
        if not signed_in(request, session): raise HTTPException(401)
    try:
        package = clock_bundle.build_bundle()
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(package, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="SeveralUDO-Clock-Sync-{clock_bundle.CLOCK_SYNC_VERSION}-Complete.zip"',
        "Cache-Control": "no-store",
    })


@app.post("/downloads/clock-sync/configured")
def download_configured_clock_sync(request: Request):
    """Rotate the active save's token and return a private ready-to-install kit."""
    with db() as session:
        ctx = context(request, session)
        save = ctx["save"]
        if not save:
            raise HTTPException(400, "Open a tracker save before creating a private Clock Sync kit.")
        raw = rotate_clock_link(session, save.id)
        base_url = str(request.base_url).rstrip("/") if settings.local_mode else settings.public_url
        try:
            package = clock_bundle.build_bundle(f"{base_url}/api/clock/report", raw)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
    return Response(package, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="SeveralUDO-Clock-Sync-{clock_bundle.CLOCK_SYNC_VERSION}-Private.zip"',
        "Cache-Control": "no-store, private",
    })


@app.get("/downloads/clock-sync/{component}")
def download_clock_sync_component(request: Request, component: str):
    with db() as session:
        if not signed_in(request, session): raise HTTPException(401)
    if component == "config-template":
        return Response(clock_bundle.config_document(), media_type="application/json", headers={
            "Content-Disposition": 'attachment; filename="config-template.json"',
            "Cache-Control": "no-store",
        })
    try:
        source = clock_bundle.bridge_file(component)
    except KeyError as exc:
        raise HTTPException(404, "That Clock Sync file is not available.") from exc
    if not source.is_file():
        raise HTTPException(404, "That Clock Sync file is unavailable in this build.")
    media_type = "application/zip" if component == "script" else "text/plain; charset=utf-8"
    return Response(source.read_bytes(), media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{source.name}"',
        "Cache-Control": "no-store",
    })


@app.get("/downloads/windows-installer")
def download_windows_installer(request: Request):
    with db() as session:
        if not signed_in(request, session): raise HTTPException(401)
    package=ROOT / "release" / "Decades-Tracker-4.2.3-Setup.exe"
    if not package.exists():
        return RedirectResponse(settings.desktop_installer_url, status_code=302)
    return StreamingResponse(package.open("rb"),media_type="application/vnd.microsoft.portable-executable",headers={
        "Content-Disposition":'attachment; filename="Decades-Tracker-4.2.3-Setup.exe"',"Cache-Control":"no-store",
    })


@app.post("/api/clock/report")
async def clock_report(request: Request, authorization: str | None = Header(None)):
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401)
    digest = hash_secret(authorization[7:].strip())
    with db() as session:
        link = session.scalar(select(ClockLink).where(ClockLink.token_hash == digest, ClockLink.enabled.is_(True)))
        if not link: raise HTTPException(401, "Invalid clock token")
        return clock.receive(session, link, await request.json())


@app.get("/portraits/{record_id}/{stage}")
def portrait(request: Request, record_id: str, stage: str):
    with db() as session:
        record = session.get(Record, record_id)
        if not record: raise HTTPException(404)
        save=owned_save(request, session, record.save_id)
        if stage == "current" and record.kind == "sim":
            raw=str((record.data or {}).get("game_age_stage") or "").replace("Age.","").replace("_","").replace(" ","").casefold()
            stage_map={"baby":"Newborn","newborn":"Newborn","infant":"Infant","toddler":"Toddler","child":"Child","preteen":"Preteen","teen":"Teen","youngadult":"Young Adult","adult":"Adult","elder":"Elder"}
            stage=stage_map.get(raw,insights.life_stage(record,save.global_day))
        item = session.scalar(select(Portrait).where(Portrait.record_id == record_id, Portrait.stage == stage))
        if not item and stage != "default":
            item = session.scalar(select(Portrait).where(Portrait.record_id == record_id, Portrait.stage == "default"))
        if not item:
            item = session.scalar(select(Portrait).where(Portrait.record_id == record_id).order_by(Portrait.created_at.desc()).limit(1))
        if not item: raise HTTPException(404)
        return Response(item.image, media_type=item.mime_type, headers={"Cache-Control": "public,max-age=86400"})


@app.post("/portraits/{record_id}")
async def upload_portrait(request: Request, record_id: str, stage: str = Form("default"), image: UploadFile = None):
    data = await image.read()
    normalized, mime = portraits.normalize_image(data)
    with db() as session:
        record = session.get(Record, record_id)
        if not record: raise HTTPException(404)
        owned_save(request, session, record.save_id)
        item = session.scalar(select(Portrait).where(Portrait.record_id == record_id, Portrait.stage == stage))
        if item: item.image, item.mime_type, item.source = normalized, mime, "upload"
        else: item=Portrait(save_id=record.save_id, record_id=record_id, stage=stage, image=normalized, mime_type=mime);session.add(item)
        session.flush();sync.sync_portrait(session,session.get(ChronicleSave,record.save_id),item,record_id,stage)
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


@app.post("/portraits/{record_id}/{stage}/delete")
def delete_portrait(request: Request, record_id: str, stage: str):
    with db() as session:
        record=session.get(Record,record_id)
        if not record: raise HTTPException(404)
        owned_save(request,session,record.save_id)
        item=session.scalar(select(Portrait).where(Portrait.record_id==record_id,Portrait.stage==stage))
        if not item: raise HTTPException(404)
        sync.sync_portrait(session,session.get(ChronicleSave,record.save_id),item,record_id,stage,deleted=True)
        session.delete(item)
        request.session["portrait_notice"]=f"{stage} portrait removed."
    return RedirectResponse(request.headers.get("referer") or "/",status_code=303)


@app.post("/portraits/marriage/{relationship_id}/generate")
def generate_marriage_portrait(request: Request, relationship_id: str, first_sim_id: str = Form(...), second_sim_id: str = Form(...), marriage_year: int = Form(...)):
    with db() as session:
        relationship = session.get(Record, relationship_id)
        first = session.get(Record, first_sim_id); second = session.get(Record, second_sim_id)
        if not relationship or not first or not second: raise HTTPException(404)
        owned_save(request, session, relationship.save_id)
        if first.save_id != relationship.save_id or second.save_id != relationship.save_id: raise HTTPException(400)
        first_photo = session.scalar(select(Portrait).where(Portrait.record_id == first.id, Portrait.stage == "default"))
        second_photo = session.scalar(select(Portrait).where(Portrait.record_id == second.id, Portrait.stage == "default"))
        if not first_photo or not second_photo: raise HTTPException(400, "Both Sims need portraits first.")
        try:
            generated = portraits.generate(first_photo.image, second_photo.image, first.label, second.label, marriage_year)
            normalized, mime = portraits.normalize_image(generated)
        except Exception as exc:
            request.session["portrait_notice"]=f"Marriage portrait was not generated: {str(exc)[:220]}"
            return RedirectResponse(f"/relationships/{relationship_id}",status_code=303)
        item = session.scalar(select(Portrait).where(Portrait.record_id == relationship.id, Portrait.stage == "marriage"))
        if item: item.image, item.mime_type, item.source = normalized, mime, settings.portrait_provider
        else: item=Portrait(save_id=relationship.save_id, record_id=relationship.id, stage="marriage", image=normalized, mime_type=mime, source=settings.portrait_provider);session.add(item)
        session.flush();sync.sync_portrait(session,session.get(ChronicleSave,relationship.save_id),item,relationship.id,"marriage")
        request.session["portrait_notice"]="Marriage portrait generated."
    return RedirectResponse(request.headers.get("referer") or "/p/relationships", status_code=303)


@app.get("/healthz")
def health():
    payload = {"status": "ok", "version": app.version, "storage": "local" if settings.local_mode else "hosted", "google": settings.google_enabled, "portrait": portraits.provider_status(), "clock_sync_ready": not clock_bundle.missing_files()}
    try:
        with engine.connect() as connection:
            connection.execute(select(User.id).limit(1))
    except SQLAlchemyError:
        payload["status"] = "database-unavailable"
        return JSONResponse(payload, status_code=503)
    return payload
