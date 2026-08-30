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

from . import accounts, advanced, auth, automation, avatar_rules, backup_service, calendar_utils, clock, clock_bundle, core_rulesets, decade_portraits, dice, exports, game_metadata, game_of_thrones_rules, harry_potter_rules, historical_life, life_records, names, notifications, occult_rules, portraits, save_scanner, themes, tray_scanner, sync, storyline, telemetry, insights
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
    "historical-life": ("Historical Life", "Era preparation, estates, education, reputation, service, memorials and family strategy"),
    "life-records": ("Life Records", "Dowries, guardians, milestones, law, wellbeing and chronicle reliability"),
    "challenge": ("Challenge Management", "Succession, matchmaking and campaigns"),
    "world": ("World & Migration", "Birth countries, moves, historical locations and migration routes"),
    "legacy-lab": ("Legacy Lab", "Progress, biographies, compatibility, consistency and safe rule experiments"),
    "avatar": ("Avatar Add-on", "Bending, nations, Spirits, the Avatar Cycle and the BG/AG timeline"),
    "harry-potter": ("Harry Potter Add-on", "Magical families, Hogwarts, secrecy and Wizarding history"),
    "game-of-thrones": ("Game of Thrones Add-on", "Houses, succession, courts, wars, dragons and BC/AC history"),
    "statistics": ("Statistics", "Population, survival, fertility and records"),
    "tutorial": ("Tutorial", "A step-by-step guide to setup, daily play and automation"),
    "notes": ("Notes", "A private notebook for this save"),
    "plants": ("Planting Reference", "Historical crops by year, season and location"),
    "names": ("Name Generator", "Offline names from your own sourced historical libraries"),
    "guides": ("Challenge Guides", "SeveralUDO, Morbid, and Classic 2023 references"),
    "rules": ("Rule Setup", "Calendar, automation, save-wide defaults and display preferences"),
    "roll-tables": ("Roll Tables", "Aging, pregnancy, marriage, remarriage and multiple-birth rules by era"),
    "occult-rules": ("Occult Rules", "Occult detection, automatic obligations and follow-up rule library"),
    "historical-guidance": ("Historical Guidance", "Era guidance, death causes and recovered reference data"),
    "health": ("Rules Health", "Coverage, duplicates and maintenance checks"),
    "clock": ("Game Clock", "Local or hosted Sims 4 time and population receiver"),
    "sync": ("Sync", "Desktop/cloud status, devices and conflict review"),
    "dice-audit": ("Dice Audit", "Verifiable history and distribution reports"),
    "storyline": ("Storyline", "A living narrative generated from the changing save"),
    "saves": ("Saves & Backups", "Create, rename, duplicate, export and restore chronicles"),
    "account": ("Account & Sharing", "Google sign-in, shared workspaces and notifications"),
    "appearance": ("Appearance", "Colors, type, spacing and motion for this save"),
    "support": ("About & Support", "Version, help, privacy and project support"),
}

# Navigation follows the player's workflow instead of the underlying record
# types.  Keep this as the single source of truth for the sidebar and overview
# so every feature remains discoverable without presenting one very long list.
NAVIGATION_GROUPS = (
    {
        "id": "play",
        "label": "Play",
        "description": "What needs attention now",
        "icon": "▶",
        "pages": ("today", "automation", "clock", "planner", "rolls"),
    },
    {
        "id": "family",
        "label": "Family & Life",
        "description": "People, homes and life events",
        "icon": "♟",
        "pages": ("sims", "family-tree", "relationships", "households", "pregnancies", "illnesses", "historical-life", "life-records"),
    },
    {
        "id": "history",
        "label": "History & Story",
        "description": "The chronicle, world and memories",
        "icon": "✒",
        "pages": ("events", "world", "timeline", "storyline", "notes"),
    },
    {
        "id": "challenge",
        "label": "Challenge & Rules",
        "description": "Rulesets, succession and play aids",
        "icon": "⚖",
        "pages": ("challenge", "rules", "roll-tables", "historical-guidance", "occult-rules", "guides", "plants", "names", "avatar", "harry-potter", "game-of-thrones"),
    },
    {
        "id": "insights",
        "label": "Insights",
        "description": "Progress, statistics and checks",
        "icon": "⌁",
        "pages": ("statistics", "legacy-lab", "dice-audit", "health"),
    },
    {
        "id": "setup",
        "label": "Settings & Help",
        "description": "Saves, devices, access and guidance",
        "icon": "⚙",
        "pages": ("saves", "sync", "account", "appearance", "tutorial", "support"),
    },
)


def navigation_group_for(page: str | None):
    return next((group for group in NAVIGATION_GROUPS if page in group["pages"]), None)

KIND_BY_PAGE = {
    "sims": "sim", "households": "household", "relationships": "relationship",
    "pregnancies": "pregnancy", "rolls": "roll", "events": "event",
    "illnesses": "illness", "notes": "note", "planner": "play_rotation", "automation": "game_candidate",
    "challenge": "campaign", "plants": "plant",
}

class CachedStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response=await super().get_response(path,scope)
        response.headers["Cache-Control"]="public, max-age=604800, immutable"
        return response


def static_version() -> str:
    digest=hashlib.sha256()
    static_root=ROOT / "app" / "static"
    for path in sorted(static_root.glob("*")):
        if not path.is_file(): continue
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


app = FastAPI(title="Decades Tracker", version="4.5.1")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, max_age=REMEMBER_DEVICE_SECONDS, same_site="lax", https_only=not settings.local_mode)
app.add_middleware(StaySignedInMiddleware, persistent_max_age=REMEMBER_DEVICE_SECONDS)
app.mount("/static", CachedStaticFiles(directory=ROOT / "app" / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "app" / "templates")
_SAVE_SCAN_CACHE: dict[str, dict] = {}
_TODAY_SCHEDULE_CHECKED: dict[str, tuple[int, int]] = {}
_STATIC_VERSION=static_version()


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


def event_calendar_fields(save: ChronicleSave, event: str, global_day: int | None, hour, minute, second=None) -> dict:
    day = int_or_none(global_day)
    if day is None:
        return {}
    result = {f"historical_{event}_date_range": calendar_utils.date_range_label(day, save.start_year, save.days_per_year)}
    parsed_hour, parsed_minute = int_or_none(hour), int_or_none(minute)
    if parsed_hour is None or parsed_minute is None:
        result[f"{event}_date_precision"] = "challenge-day-only"
        return result
    parsed_hour=max(0,min(23,parsed_hour));parsed_minute=max(0,min(59,parsed_minute))
    parsed_second=int_or_none(second)
    result.update({f"{event}_game_hour":parsed_hour,f"{event}_game_minute":parsed_minute,
                   f"{event}_time":f"{parsed_hour:02d}:{parsed_minute:02d}"})
    if parsed_second is not None:
        parsed_second=max(0,min(59,parsed_second))
        result.update({f"{event}_game_second":parsed_second,
                       f"{event}_time":f"{parsed_hour:02d}:{parsed_minute:02d}:{parsed_second:02d}"})
    exact=calendar_utils.exact_historical_label(day,parsed_hour,parsed_minute,save.start_year,save.days_per_year)
    if exact:
        result.update({f"historical_{event}_date":exact,f"{event}_date_precision":"exact"})
    else:
        result[f"{event}_date_precision"]="clock-time-no-calendar-map"
    return result


def birth_circumstance_suggestion(session, save: ChronicleSave, pregnancy: Record | None, mother: Record | None,
                                  birth_day: int | None, birthplace: str = "", events: list[Record] | None = None) -> dict:
    """Build an explainable, editable birth summary only from facts the tracker knows."""
    if (not save or not domain.automation_enabled(save)
            or not bool((save.settings or {}).get("automatic_birth_circumstances", True))):
        return {"summary": "", "tags": [], "birth_status": "", "multiple_birth_status": ""}
    day = int_or_none(birth_day) or save.global_day
    pregnancy_data = dict((pregnancy.data if pregnancy else {}) or {})
    tags = ["Live birth"]
    expected = max(1, int_or_none(pregnancy_data.get("babies_delivered")) or int_or_none(pregnancy_data.get("babies_expected")) or 1)
    multiple_names = {1: "Singleton", 2: "Twin", 3: "Triplet", 4: "Quadruplet", 5: "Quintuplet"}
    multiple_status = multiple_names.get(expected, f"{expected}-baby multiple")
    tags.append(f"{multiple_status} birth")
    due = int_or_none(pregnancy_data.get("actual_due_global_day")) or int_or_none(pregnancy_data.get("due_global_day"))
    conception = int_or_none(pregnancy_data.get("conception_global_day"))
    if due is not None:
        tags.append("Premature birth" if day < due else "Late birth" if day > due else "On-time birth")
    elif conception is not None:
        gestation = day - conception
        tags.append("Premature birth" if gestation < save.pregnancy_days else "Late birth" if gestation > save.pregnancy_days else "On-time birth")
    place = str(birthplace or "").strip()
    if place:
        tags.append(f"Born at {place}")
    complication = str(pregnancy_data.get("complication") or "").strip()
    outcome = str(pregnancy_data.get("outcome") or "").strip()
    if complication:
        tags.append(f"Maternal complication: {complication}")
    if outcome and outcome.casefold() not in {"live birth", "delivered", "complete", "completed"}:
        tags.append(f"Pregnancy outcome: {outcome}")
    mother_death = int_or_none((mother.data or {}).get("death_global_day")) if mother else None
    if mother_death is not None and mother_death == day:
        cause = str((mother.data or {}).get("cause_of_death") or "").strip()
        tags.append("Mother died during or shortly after childbirth" + (f" ({cause})" if cause else ""))
    event_rows = events if events is not None else list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "event", Record.deleted.is_(False),
    )))
    active_events = []
    for event in event_rows:
        data = event.data or {}
        start = int_or_none(data.get("start_global_day", event.global_day))
        end = int_or_none(data.get("end_global_day", start))
        if domain.event_is_ignored(event) or not bool(data.get("active", True)):
            continue
        if (start is None or start <= day) and (end is None or end >= day):
            active_events.append(event.label)
    if active_events:
        shown = sorted(set(active_events), key=str.casefold)[:3]
        tags.append("Born during " + ", ".join(shown) + (f" and {len(set(active_events)) - 3} other active events" if len(set(active_events)) > 3 else ""))
    return {
        "summary": "; ".join(tags) + ".",
        "tags": tags,
        "birth_status": "Live birth",
        "multiple_birth_status": multiple_status,
    }


def historical_sim_location(session, save: ChronicleSave, sim: Record | None, day_value: int | None) -> dict:
    """Resolve where a Sim lived on a historical day without applying later moves."""
    if not sim:
        return {"place": "", "country": "", "source": "No Sim selected"}
    day=int_or_none(day_value) or save.global_day
    data=dict(sim.data or {})
    country=str(data.get("birth_country") or data.get("country") or "").strip()
    place=str(data.get("birthplace") or country).strip()
    moves=list(session.scalars(select(Record).where(
        Record.save_id==save.id,Record.kind=="migration",Record.deleted.is_(False),
        Record.data["sim_id"].as_string()==sim.id,
    )))
    moves.sort(key=lambda move:(int_or_none((move.data or {}).get("move_global_day",move.global_day)) or 10**9,move.created_at))
    if moves and not str(data.get("birth_country") or "").strip():
        earliest_origin=str((moves[0].data or {}).get("from_country") or "").strip()
        if earliest_origin:
            country=earliest_origin
            if not str(data.get("birthplace") or "").strip(): place=earliest_origin
    applied=None
    for move in moves:
        move_data=move.data or {};move_day=int_or_none(move_data.get("move_global_day",move.global_day))
        if move_day is None or move_day>day: continue
        country=str(move_data.get("to_country") or country).strip()
        place=str(move_data.get("to_location") or country or place).strip()
        applied=move
    if applied:
        return {"place":place or country,"country":country,"source":f"{sim.label}'s migration on GD {int_or_none((applied.data or {}).get('move_global_day',applied.global_day))}"}
    # If no dated route exists at all, the current tracker location is the best
    # available evidence.  Never use it when a later migration would rewrite history.
    if not moves:
        current_place=str(data.get("current_location") or "").strip()
        current_country=str(data.get("current_country") or data.get("country") or "").strip()
        if current_place or current_country:
            return {"place":current_place or current_country,"country":current_country or country,"source":f"{sim.label}'s current tracker location"}
        household_id=str(data.get("current_household_id") or "")
        household=session.get(Record,household_id) if household_id else None
        if household and household.kind=="household" and household.save_id==save.id and not household.deleted:
            household_place=str((household.data or {}).get("location") or "").strip()
            if household_place:
                return {"place":household_place,"country":country,"source":f"{sim.label}'s household location"}
    return {"place":place or country,"country":country,"source":f"{sim.label}'s recorded birth location" if (place or country) else f"{sim.label}'s location is not recorded"}


def birth_calendar_fields(save: ChronicleSave, global_day: int | None, hour, minute, second=None) -> dict:
    return event_calendar_fields(save, "birth", global_day, hour, minute, second)


def resolve_birth_input(save: ChronicleSave, global_day, birth_year, hour=None, minute=None, second=None) -> tuple[int | None, dict]:
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
    return day, birth_calendar_fields(save, day, hour, minute, second)


def death_calendar_fields(save: ChronicleSave, global_day: int | None, hour, minute, second=None) -> dict:
    return event_calendar_fields(save, "death", global_day, hour, minute, second)


def marriage_calendar_fields(save: ChronicleSave, global_day: int | None, hour, minute, second=None) -> dict:
    return event_calendar_fields(save, "marriage", global_day, hour, minute, second)


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


def detected_labels(value, details=None, kind: str = "") -> list[str]:
    """Render old and new Clock Sync collection formats consistently."""
    return game_metadata.readable_named_labels(value, details, kind=kind)


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


BOOL_FIELDS = {"active", "roll_required", "legally_married", "contagious", "maternal_rolls_required", "newborn_rolls_required", "pinned", "include_in_tree", "auto_schedule", "followup_enabled", "followup_failure_is_lethal", "completed", "temporary", "resolved"}
INT_FIELDS = {
    "start_global_day", "end_global_day", "conception_global_day", "due_global_day", "delivery_global_day",
    "birth_global_day", "death_global_day", "global_day", "start_year", "end_year", "age_days", "min_age_days",
    "max_age_days", "max_babies", "target_children", "min_birth_spacing_days", "children_count", "babies_expected", "babies_delivered", "followup_delay_days",
    "roll_repeat_interval_years", "amount", "impact", "planned_global_day", "decade", "year_acquired",
    "ward_start_global_day", "ceremony_global_day", "end_global_day", "sentence_end_global_day",
    "return_global_day", "restriction_end_global_day", "suggested_amount", "year", "birth_order",
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


def sorted_sims(records, save: ChronicleSave) -> list[Record]:
    """Apply the save's menu preference everywhere a Sim chooser is built."""
    order = str((save.settings or {}).get("sim_menu_order") or "highest_id").casefold()
    sims = list(records)
    if order == "recent":
        return sorted(sims, key=lambda item: (item.updated_at, insights.sim_number(item)), reverse=True)
    if order == "alphabetical":
        return sorted(sims, key=lambda item: (item.label.casefold(), -insights.sim_number(item)))
    return sorted(sims, key=lambda item: (insights.sim_number(item), item.label.casefold()), reverse=True)


def navigation_counts(session, save: ChronicleSave) -> dict[str, int]:
    """Small indexed count queries keep the sidebar useful without loading ledgers."""
    base = (Record.save_id == save.id, Record.deleted.is_(False))
    pending = Record.data["completed"].as_boolean().is_not(True)
    counts = {
        "automation": session.scalar(select(func.count()).select_from(Record).where(*base, Record.kind == "game_candidate", Record.data["status"].as_string() == "pending")) or 0,
        "rolls": session.scalar(select(func.count()).select_from(Record).where(*base, Record.kind == "roll", pending, Record.global_day <= save.global_day)) or 0,
        "pregnancies": session.scalar(select(func.count()).select_from(Record).where(*base, Record.kind == "pregnancy", func.lower(func.coalesce(Record.data["status"].as_string(), "active")).notin_(["delivered", "complete", "miscarriage", "stillbirth", "cancelled", "canceled"]))) or 0,
        "illnesses": session.scalar(select(func.count()).select_from(Record).where(*base, Record.kind == "illness", func.lower(func.coalesce(Record.data["status"].as_string(), "active")).notin_(["recovered", "fatal", "resolved", "complete"]))) or 0,
        "events": session.scalar(select(func.count()).select_from(Record).where(*base, Record.kind == "event", Record.global_day <= save.global_day, or_(Record.data["end_global_day"].as_integer().is_(None), Record.data["end_global_day"].as_integer() >= save.global_day), Record.data["ignored"].as_boolean().is_not(True), Record.data["active"].as_boolean().is_not(False))) or 0,
        "deaths": session.scalar(select(func.count()).select_from(Record).where(*base, Record.kind == "sim", Record.data["death_global_day"].as_integer() <= save.global_day, Record.data["death_confirmed"].as_boolean().is_not(True))) or 0,
    }
    counts["today"] = counts["rolls"] + counts["pregnancies"] + counts["illnesses"] + counts["events"] + counts["deaths"]
    return {key: int(value) for key, value in counts.items()}


def _living_sim(sim: Record, save: ChronicleSave) -> bool:
    data = sim.data or {}; birth = int_or_none(data.get("birth_global_day", sim.global_day)); death = int_or_none(data.get("death_global_day"))
    return not bool(data.get("game_was_dead")) and (birth is None or birth <= save.global_day) and (death is None or death > save.global_day)


def _ancestor_ids(sim_id: str, by_id: dict[str, Record], limit: int = 3) -> dict[str, int]:
    found: dict[str, int] = {}; frontier = [(sim_id, 0)]
    while frontier:
        current, depth = frontier.pop(0)
        if depth >= limit or current not in by_id: continue
        for parent in ((by_id[current].data or {}).get("mother_id"), (by_id[current].data or {}).get("father_id")):
            parent = str(parent or "")
            if parent and (parent not in found or depth + 1 < found[parent]):
                found[parent] = depth + 1; frontier.append((parent, depth + 1))
    return found


def kinship_warning(first_id: str, second_id: str, sims: list[Record], limit: int = 3) -> str:
    if first_id == second_id: return "Same Sim"
    by_id = {item.id:item for item in sims}; first = _ancestor_ids(first_id, by_id, limit); second = _ancestor_ids(second_id, by_id, limit)
    if second_id in first or first_id in second: return "Direct ancestor or descendant"
    shared = set(first) & set(second)
    if not shared: return ""
    a, b = min((first[key], second[key]) for key in shared)
    if a == b == 1: return "Sibling or half-sibling"
    if sorted((a, b)) == [1, 2]: return "Aunt/uncle and niece/nephew"
    if a == b == 2: return "First cousins"
    return "Shared close ancestor"


def succession_ranking(sims: list[Record], save: ChronicleSave) -> list[dict]:
    settings_data = save.settings or {}; system = str(settings_data.get("succession_system") or "Absolute primogeniture")
    require_legitimate = bool(settings_data.get("succession_require_legitimate")); root_id = str(settings_data.get("succession_root_id") or "")
    eligible = [item for item in sims if _living_sim(item, save) and bool((item.data or {}).get("include_in_family_tree", True))]
    if root_id:
        descendants = {root_id}; changed = True
        while changed:
            before = len(descendants)
            descendants.update(item.id for item in eligible if str((item.data or {}).get("mother_id") or "") in descendants or str((item.data or {}).get("father_id") or "") in descendants)
            changed = len(descendants) != before
        eligible = [item for item in eligible if item.id in descendants and item.id != root_id]
    rows = []
    for sim in eligible:
        data = sim.data or {}; override = str(data.get("succession_override") or "Auto"); legitimacy = str(data.get("legitimacy") or data.get("legitimate") or "").casefold()
        if any(word in override.casefold() for word in ("exclude", "disinherit")): continue
        if require_legitimate and legitimacy not in {"1", "true", "yes", "legitimate"}: continue
        sex = str(data.get("sex") or "").casefold(); sex_priority = 0
        if "male-preference" in system.casefold(): sex_priority = 0 if sex.startswith("m") else 1
        elif "female-preference" in system.casefold(): sex_priority = 0 if sex.startswith("f") else 1
        birth = int_or_none(data.get("birth_global_day"))
        rows.append({"sim":sim,"override":override,"priority":0 if any(word in override.casefold() for word in ("heir", "priority", "include")) else 1,"sex_priority":sex_priority,"birth":birth if birth is not None else 10**9})
    rows.sort(key=lambda row:(row["priority"], row["sex_priority"], row["birth"], insights.sim_number(row["sim"])))
    for index, row in enumerate(rows, 1): row["rank"] = index
    return rows


def planner_analysis(sims: list[Record], plans: list[Record], save: ChronicleSave) -> tuple[list[dict], list[dict]]:
    by_id = {item.id:item for item in sims}; adulthood = int((save.settings or {}).get("adulthood_age_days") or 72)
    plan_rows = []
    for plan in plans:
        data = plan.data or {}; sim = by_id.get(str(data.get("sim_id") or "")); children = []
        if sim: children = [item for item in sims if str((item.data or {}).get("mother_id") or "") == sim.id or str((item.data or {}).get("father_id") or "") == sim.id]
        survived = sum(int_or_none((child.data or {}).get("birth_global_day")) is not None and ((int_or_none((child.data or {}).get("death_global_day")) or 10**9) >= int((child.data or {}).get("birth_global_day")) + adulthood) and (int_or_none((child.data or {}).get("death_global_day")) is not None or save.global_day >= int((child.data or {}).get("birth_global_day")) + adulthood) for child in children)
        died_young = sum(int_or_none((child.data or {}).get("death_global_day")) is not None and int((child.data or {}).get("death_global_day")) < int((child.data or {}).get("birth_global_day") or 0) + adulthood for child in children)
        pending = max(0, len(children) - survived - died_young); spacing = int_or_none(data.get("min_birth_spacing_days")) or 0
        births = [int((child.data or {}).get("birth_global_day")) for child in children if int_or_none((child.data or {}).get("birth_global_day")) is not None]
        target = int_or_none(data.get("target_children")) or 0
        forecast = []
        if sim:
            birth = int_or_none((sim.data or {}).get("birth_global_day"))
            if birth is not None:
                for label, offset in sorted(domain.AGING_STAGE_OFFSETS.items(), key=lambda item:item[1]):
                    due = birth + int(offset)
                    if due >= save.global_day: forecast.append({"label":label.title(),"global_day":due})
                marriage_due = birth + int((save.settings or {}).get("marriage_min_age_days") or 72)
                if marriage_due >= save.global_day: forecast.append({"label":"Marriage eligibility","global_day":marriage_due})
        plan_rows.append({"record":plan,"sim":sim,"children":len(children),"survived":survived,"died_young":died_young,"pending":pending,"remaining":max(0,target-len(children)),"next_conception":max(births)+spacing if births and spacing else None,"forecast":sorted(forecast,key=lambda item:item["global_day"])[:12]})
    dynasties: dict[str, dict] = {}
    for sim in sims:
        surname = str((sim.data or {}).get("surname_at_birth") or (sim.data or {}).get("maiden_name") or (sim.data or {}).get("last_name") or sim.label.split()[-1] or "Unknown")
        row = dynasties.setdefault(surname,{"name":surname,"total":0,"living":0,"first":None}); row["total"] += 1; row["living"] += int(_living_sim(sim,save))
        birth = int_or_none((sim.data or {}).get("birth_global_day")); row["first"] = birth if row["first"] is None else min(row["first"],birth) if birth is not None else row["first"]
    dynasty_rows = sorted(({**row,"state":"Extinct" if row["living"]==0 else "Endangered" if row["living"]<=2 else "Surviving"} for row in dynasties.values()),key=lambda row:(row["living"],row["total"],row["name"].casefold()),reverse=True)
    return plan_rows, dynasty_rows


templates.env.globals.update(
    sim_status=sim_status,
    detected_labels=detected_labels,
    trait_labels=game_metadata.readable_trait_labels,
    game_labels=game_metadata.readable_named_labels,
    relationship_is_partner=insights.relationship_is_partner,
)


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
                if not settings.skip_startup_migrations and str((existing_save.settings or {}).get("defaults_schema_version") or "")!=domain.DEFAULTS_SCHEMA_VERSION:
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
            if not settings.skip_startup_migrations and str((active.settings or {}).get("defaults_schema_version") or "") != domain.DEFAULTS_SCHEMA_VERSION:
                domain.seed_defaults(session, active)
            if str((active.settings or {}).get("event_catalog_version") or "") != domain.EVENT_CATALOG_VERSION:
                domain.seed_event_catalog(session, active)
    last_roll = request.session.pop("last_roll", None)
    current_page = extra.get("page")
    visual_theme = themes.resolve((active.settings or {}).get("visual_theme") if active else None)
    return {"request": request, "user": user, "saves": saves, "save": active,
            "save_settings": dict(active.settings or {}) if active else {},
            "visual_theme": visual_theme,
            "features": FEATURES, "navigation_groups": NAVIGATION_GROUPS,
            "navigation_group": navigation_group_for(current_page),
            "local_mode": settings.local_mode, "google_enabled": settings.google_enabled, "last_roll": last_roll,
            "occult_notice": request.session.pop("occult_notice", None),
            "master_automation_notice": request.session.pop("master_automation_notice", None),
            "manual_roll_notice": request.session.pop("manual_roll_notice", None),
            "theme_notice": request.session.pop("theme_notice", None),
            "app_version": app.version, "static_version":_STATIC_VERSION,
            "notification_cursor": datetime.now(timezone.utc).isoformat(), **extra}


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
            ctx["navigation_counts"] = navigation_counts(session, save)
        if save:
            ctx["automation_pending"] = session.scalar(select(func.count()).select_from(Record).where(Record.save_id==save.id,Record.kind=="game_candidate",Record.deleted.is_(False),Record.data["status"].as_string()=="pending")) or 0
        counts = {}
        if save:
            counts = dict(session.execute(select(Record.kind, func.count()).where(Record.save_id == save.id, Record.deleted.is_(False)).group_by(Record.kind)).all())
        selected_rule_packs=list((save.settings or {}).get("selected_rule_packs") or []) if save else []
        decade_snapshots=list(session.scalars(select(Record).where(
            Record.save_id==save.id,Record.kind=="decade_snapshot",Record.deleted.is_(False),
        ).order_by(Record.data["portrait_year"].as_integer().desc()).limit(12))) if save else []
        return templates.TemplateResponse(request, "dashboard.html", {
            **ctx, "counts": counts, "rule_packs":advanced.RULE_PACKS,
            "core_rulesets":core_rulesets.CORE_RULESETS,
            "selected_core_ruleset":core_rulesets.selected_core(save) if save else core_rulesets.SEVERALUDO,
            "selected_rule_packs":selected_rule_packs,"decade_snapshots":decade_snapshots,
        })


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


@app.get("/exports/{save_id}/biographies.md")
def export_biographies(request: Request, save_id: str):
    with db() as session:
        save=owned_save(request,session,save_id)
        rows=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.deleted.is_(False))))
        lines=[f"# The People of {save.name}","",f"Through historical year {advanced.year_for(save,save.global_day)}.",""]
        for bio in advanced.biographies(rows,save):
            lines.extend([f"## {bio['sim'].label}","",bio["text"],""])
        return _download("\n".join(lines),"text/markdown; charset=utf-8",f"{exports.safe_filename(save.name)}-biographies.md")


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
            hash_name_repair = automation.repair_hashed_sim_metadata(session, save)
            if hash_name_repair["sims"] or hash_name_repair["labels"]:
                session.flush()
                ctx["hash_name_repair"] = hash_name_repair
            relationship_classification_repair = automation.repair_relationship_classifications(session, save)
            if relationship_classification_repair["records"]:
                session.flush()
                ctx["relationship_classification_repair"] = relationship_classification_repair
            if page == "roll-tables":
                domain.seed_remarriage_rule(session, save)
        if save and page == "automation":
            relationship_repair = automation.repair_relationship_inbox(session, save)
            repaired_count = relationship_repair["classified"] + relationship_repair["dismissed"]
            if repaired_count:
                save.revision += repaired_count
                session.flush()
                ctx["relationship_inbox_repair"] = relationship_repair
        if save and page == "challenge":
            domain.schedule_marriage_rolls(session, save)
            save.revision += domain.schedule_campaign_rolls(session, save)
        if save:
            added_portrait_prompt=decade_portraits.schedule_prompt(session,save)
            if added_portrait_prompt:
                save.revision += added_portrait_prompt
        if save and page != "automation":
            ctx["automation_pending"] = session.scalar(select(func.count()).select_from(Record).where(Record.save_id==save.id,Record.kind=="game_candidate",Record.deleted.is_(False),Record.data["status"].as_string()=="pending")) or 0
        kind = KIND_BY_PAGE.get(page)
        records = []
        support_rows_cache = None
        if save and kind:
            list_page=max(1,int_or_none(request.query_params.get("list_page")) or 1);list_size=48;list_q=request.query_params.get("q","").strip();list_status=request.query_params.get("record_status","all")
            if page=="sims":
                # The create/edit controls need every active Sim and household
                # anyway. Reuse that one result for the list and archive tray.
                support_rows_cache=list(session.scalars(select(Record).where(
                    Record.save_id==save.id,Record.kind.in_({"sim","household"}),
                )))
                listed=[item for item in support_rows_cache if item.kind=="sim" and not item.deleted]
                if list_q: listed=[item for item in listed if list_q.casefold() in item.label.casefold()]
                listed = sorted_sims(listed, save)
                record_count=len(listed);list_pages=max(1,(record_count+list_size-1)//list_size);list_page=min(list_page,list_pages)
                records=listed[(list_page-1)*list_size:list_page*list_size]
            else:
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
                if page=="automation": ordering=(Record.created_at.asc(),)
                else: ordering=(Record.global_day.desc().nullslast(),Record.label)
                records=list(session.scalars(select(Record).where(*conditions).order_by(*ordering).offset((list_page-1)*list_size).limit(list_size)))
            ctx.update(list_page=list_page,list_pages=list_pages,list_count=record_count,list_q=list_q,list_status=list_status)
            if page=="automation": ctx["automation_pending"]=record_count
        view_records = None
        view_kinds = {
            "family-tree":{"sim","relationship","household"},
            "statistics":{"sim","household","relationship","pregnancy","illness","event","death","roll"},
            "pregnancies":{"sim","pregnancy","roll"}, "illnesses":{"illness","sim"},
            "households":{"household","sim","game_history","pregnancy","illness"},
            "planner":{"sim","household","play_rotation","family_plan","roll"},
            "historical-life":{"sim","household","relationship","event","campaign","service","migration","era_guidance","era_rule","era_check","estate_plan","economy_entry","education_plan","reputation_event","migration_plan","memorial","heirloom","correspondence"},
            "life-records":{"sim","household","relationship","pregnancy","illness","roll","event","death","migration","family_plan","dowry_plan","guardianship","birth_privilege","coming_of_age","dispersal_plan","social_mobility","legal_case","absence","disability","mourning","wellbeing","medical_treatment","recovery_restriction","saved_view","newspaper"},
            "challenge":{"sim","household","relationship","roll","planner_rule","campaign","service","era_guidance","era_rule"},
            "world":{"sim","migration","household"},
            "legacy-lab":{"sim","household","relationship","pregnancy","illness","roll","event","death","migration","game_history","clock_diagnostic"},
            "events":{"event","event_rule"}, "notes":{"note"},
            "roll-tables":{"roll_rule","planner_rule","multiple_birth_rule"},
            "occult-rules":{"sim","roll","occult_rule"},
            "historical-guidance":{"death_causes","era_guidance","era_rule","event_rule","source_archive","detection_candidate","task","roll_rule_era"},
            "avatar":{"sim","addon_rule"},
            "harry-potter":{"sim","household","addon_rule"},
            "game-of-thrones":{"sim","household","addon_rule"},
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
                       all_sims=sorted_sims((item for item in view_records if item.kind == "sim" and bool((item.data or {}).get("include_in_family_tree",True))), save),
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
        if page in {"rules", "roll-tables", "occult-rules", "historical-guidance"} and save:
            selected_core=core_rulesets.selected_core(save)
            visible_core=lambda item: not (item.data or {}).get("core_ruleset_id") or (item.data or {}).get("core_ruleset_id")==selected_core
            rule_records=view_records or []
            ctx.update(save_settings=dict(save.settings or {}),core_ruleset=core_rulesets.current_catalog_entry(save))
            if page == "roll-tables":
                ctx.update(roll_rules=sorted((item for item in rule_records if item.kind=="roll_rule" and visible_core(item)),key=lambda item:(int_or_none((item.data or {}).get("start_year")) or -9999,int_or_none((item.data or {}).get("age_days")) if int_or_none((item.data or {}).get("age_days")) is not None else 10**9,item.label)),
                           planner_rules=sorted((item for item in rule_records if item.kind=="planner_rule" and visible_core(item)),key=lambda item:(item.label,int_or_none((item.data or {}).get("start_year")) or -9999)),
                           multiple_birth_rules=sorted((item for item in rule_records if item.kind=="multiple_birth_rule"),key=lambda item:int_or_none((item.data or {}).get("start_year")) or -9999))
            elif page == "occult-rules":
                occult_rule_records=sorted((item for item in rule_records if item.kind=="occult_rule"),key=lambda item:(str((item.data or {}).get("occult") or ""),str((item.data or {}).get("rule_key") or ""),int_or_none((item.data or {}).get("start_year")) or -9999))
                occult_sims=[item for item in rule_records if item.kind=="sim" and occult_rules.sim_occult_types(item.data)]
                ctx.update(occult_rules=occult_rule_records,detected_occult_sims=occult_sims,
                           occult_rule_sims=sorted((item for item in rule_records if item.kind=="sim"),key=lambda item:item.label.casefold()),
                           occult_pending_count=sum(item.kind=="roll" and bool((item.data or {}).get("occult_roll")) and not bool((item.data or {}).get("completed")) for item in rule_records))
            elif page == "historical-guidance":
                ctx.update(cause_groups=[item for item in rule_records if item.kind=="death_causes"],
                           era_guidance=sorted((item for item in rule_records if item.kind=="era_guidance" and visible_core(item)),key=lambda item:(int_or_none((item.data or {}).get("start_year")) or -9999,item.label)),
                           imported_era_rules=[item for item in rule_records if item.kind=="era_rule"],
                           event_rule_count=sum(item.kind=="event_rule" for item in rule_records),
                           compatibility_records=[item for item in rule_records if item.kind in {"source_archive","detection_candidate","task","roll_rule_era"}])
            records=[]
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
            plan_analysis,dynasty_analysis=planner_analysis(sims,plans,save)
            ctx.update(planner_recommendations=recommendations,rotation_records=sorted(rotations,key=lambda item:item.global_day or 0,reverse=True),family_plans=plans,family_plan_analysis=plan_analysis,dynasty_analysis=dynasty_analysis,all_sims=sorted_sims(sims,save),all_households=sorted(households,key=lambda item:item.label.casefold()));records=[]
        if page == "historical-life" and save:
            ctx.update(historical_life=historical_life.build(view_records or [], save),
                       historical_life_notice=request.session.pop("historical_life_notice", None))
            records=[]
        if page == "life-records" and save:
            ctx.update(life_records=life_records.build(view_records or [], save),
                       life_records_notice=request.session.pop("life_records_notice", None),
                       automation_undo_rows=[item for item in session.scalars(select(Record).where(
                           Record.save_id==save.id,Record.kind=="game_candidate",Record.deleted.is_(False),
                           Record.data["status"].as_string()=="accepted",
                       ).order_by(Record.updated_at.desc()).limit(30)) if (item.data or {}).get("undo_targets")])
            records=[]
        if page == "appearance" and save:
            ctx.update(theme_presets=themes.presets_for_ui())
            records=[]
        if page == "challenge" and save:
            year=insights.current_year(save);challenge_location=str((save.settings or {}).get("challenge_location") or "").casefold();guidance_by_key={}
            for item in sorted((item for item in view_records if item.kind in {"era_guidance","era_rule"}),key=lambda item:item.kind=="era_guidance"):
                data=item.data or {};location=str(data.get("location") or "All").casefold()
                if not bool(data.get("active",True)) or not int(data.get("start_year",-9999))<=year<=int(data.get("end_year",9999)): continue
                if location not in {"","all","global","worldwide"} and challenge_location and location not in challenge_location and challenge_location not in location: continue
                key=str(data.get("rule_id") or data.get("legacy_id") or f"{item.label}:{data.get('start_year')}:{data.get('end_year')}")
                guidance_by_key[key]=item
            guidance=sorted(guidance_by_key.values(),key=lambda item:(str((item.data or {}).get("category") or ""),item.label.casefold()))
            sims=[item for item in view_records if item.kind=="sim"];relationships=[item for item in view_records if item.kind=="relationship"]
            succession=succession_ranking(sims,save);married_ids=set()
            for relationship in relationships:
                data=relationship.data or {}
                if bool(data.get("legally_married")) or "marriage" in str(data.get("type") or "").casefold(): married_ids.update((str(data.get("partner1_id") or ""),str(data.get("partner2_id") or "")))
            minimum_age=int((save.settings or {}).get("marriage_min_age_days") or 72)
            match_eligible=[item for item in sims if _living_sim(item,save) and item.id not in married_ids and int_or_none((item.data or {}).get("birth_global_day")) is not None and save.global_day-int((item.data or {}).get("birth_global_day"))>=minimum_age]
            kinship_depth=max(1,min(8,int((save.settings or {}).get("kinship_detection_generations") or 3)))
            selected_match=request.query_params.get("match_sim") or (match_eligible[0].id if match_eligible else "");match_candidates=[]
            for candidate in match_eligible:
                if candidate.id==selected_match: continue
                warning=kinship_warning(selected_match,candidate.id,sims,kinship_depth);first=next((item for item in match_eligible if item.id==selected_match),None)
                age_gap=abs(int((first.data or {}).get("birth_global_day"))-int((candidate.data or {}).get("birth_global_day"))) if first else 0
                match_candidates.append({"sim":candidate,"warning":warning,"score":max(0,100-age_gap)-(100 if warning else 0)})
            match_candidates.sort(key=lambda item:(item["score"],item["sim"].label.casefold()),reverse=True)
            marriage_rolls=[item for item in view_records if item.kind=="roll" and domain._marriage_roll(item)]
            campaigns=[item for item in view_records if item.kind=="campaign"]
            ctx.update(challenge_year=year,era_guidance=guidance,succession=succession,campaigns=campaigns,services=[item for item in view_records if item.kind=="service"],all_sims=sorted_sims(sims,save),match_eligible=sorted_sims(match_eligible,save),selected_match=selected_match,match_candidates=match_candidates,kinship_depth=kinship_depth,marriage_rolls=sorted(marriage_rolls,key=lambda item:item.global_day or 0),marriage_roll_counts={"pending":sum(not bool((item.data or {}).get("completed")) for item in marriage_rolls),"may":sum(any(text in str((item.data or {}).get("outcome") or "").casefold() for text in ("may marry","may remarry")) for item in marriage_rolls),"no":sum(any(text in str((item.data or {}).get("outcome") or "").casefold() for text in ("does not marry","does not remarry")) for item in marriage_rolls)});records=[]
        if page == "world" and save:
            sims=[item for item in view_records if item.kind=="sim"]
            selected_year=int_or_none(request.query_params.get("year"))
            ctx.update(world=advanced.world_snapshot(view_records,save,selected_year),all_sims=sorted_sims(sims,save),world_notice=request.session.pop("world_notice",None));records=[]
        if page == "legacy-lab" and save:
            simulation=advanced.simulate_rules(view_records,save,request.query_params.get("pregnancy_days"),request.query_params.get("kinship_depth"),request.query_params.get("mortality_multiplier"))
            ctx.update(progress=advanced.progress_dashboard(view_records,save),consistency=advanced.consistency_report(view_records,save),biographies=advanced.biographies(view_records,save),compatibility=advanced.mod_compatibility(view_records),simulation=simulation,rule_packs=advanced.RULE_PACKS);records=[]
        if page == "avatar" and save:
            settings_data=dict(save.settings or {});selected=set(settings_data.get("selected_rule_packs") or [])
            modules=sorted((item for item in view_records if item.kind=="addon_rule" and (item.data or {}).get("rule_pack_id")==avatar_rules.PACK_ID),key=lambda item:str((item.data or {}).get("code") or item.label))
            current_year=insights.current_year(save)
            ctx.update(avatar_pack_enabled=avatar_rules.PACK_ID in selected,avatar_modules=modules,avatar_timeline=[{"start":start,"end":end,"label":label,"text":text,"range":avatar_rules.range_label(start,end)} for start,end,label,text in avatar_rules.TIMELINE],avatar_current_year=current_year,avatar_current_label=avatar_rules.date_label(current_year),avatar_canon_mode=bool(settings_data.get("avatar_canon_timeline_mode")),all_sims=sorted_sims((item for item in view_records if item.kind=="sim"),save),avatar_notice=request.session.pop("avatar_notice",None));records=[]
        if page == "harry-potter" and save:
            settings_data=dict(save.settings or {});selected=set(settings_data.get("selected_rule_packs") or [])
            rules=sorted((item for item in view_records if item.kind=="addon_rule" and (item.data or {}).get("rule_pack_id")==harry_potter_rules.PACK_ID),key=lambda item:str((item.data or {}).get("code") or item.label))
            ctx.update(hp_pack_enabled=harry_potter_rules.PACK_ID in selected,hp_modules=[item for item in rules if str((item.data or {}).get("code") or "").startswith("HP-") and not str((item.data or {}).get("code") or "").startswith("HP-T")],hp_event_tables=[item for item in rules if str((item.data or {}).get("code") or "").startswith("HP-T")],hp_timeline=[{"start":start,"end":end,"label":label,"text":text,"range":harry_potter_rules.range_label(start,end)} for start,end,label,text in harry_potter_rules.TIMELINE],hp_timeline_modes=harry_potter_rules.TIMELINE_MODES,hp_timeline_mode=str(settings_data.get("harry_potter_timeline_mode") or "alternate"),hp_current_year=insights.current_year(save),hp_current_label=harry_potter_rules.year_label(insights.current_year(save)),all_sims=sorted_sims((item for item in view_records if item.kind=="sim"),save),all_households=sorted((item for item in view_records if item.kind=="household"),key=lambda item:item.label.casefold()),hp_notice=request.session.pop("hp_notice",None));records=[]
        if page == "game-of-thrones" and save:
            settings_data=dict(save.settings or {});selected=set(settings_data.get("selected_rule_packs") or []);rules=sorted((item for item in view_records if item.kind=="addon_rule" and (item.data or {}).get("rule_pack_id")==game_of_thrones_rules.PACK_ID),key=lambda item:str((item.data or {}).get("code") or item.label));current_year=game_of_thrones_rules.lore_year(save)
            ctx.update(got_pack_enabled=game_of_thrones_rules.PACK_ID in selected,got_modules=[item for item in rules if not str((item.data or {}).get("code") or "").startswith("GOT-T")],got_event_tables=[item for item in rules if str((item.data or {}).get("code") or "").startswith("GOT-T")],got_timeline=[{"start":start,"end":end,"label":label,"text":text,"range":game_of_thrones_rules.range_label(start,end)} for start,end,label,text in game_of_thrones_rules.TIMELINE],got_timeline_modes=game_of_thrones_rules.TIMELINE_MODES,got_timeline_mode=str(settings_data.get("game_of_thrones_timeline_mode") or "original"),got_current_year=current_year,got_current_label=game_of_thrones_rules.year_label(current_year),all_sims=sorted_sims((item for item in view_records if item.kind=="sim"),save),all_households=sorted((item for item in view_records if item.kind=="household"),key=lambda item:item.label.casefold()),got_notice=request.session.pop("got_notice",None));records=[]
        if page == "today" and save:
            # Scheduling is idempotent. Remember the check in process instead
            # of rewriting the save's large settings JSON on every new day.
            # This keeps an ordinary GET read-only when there are no new rolls.
            schedule_marker=(save.global_day,6)
            if _TODAY_SCHEDULE_CHECKED.get(save.id) != schedule_marker:
                save.revision += domain.retire_prechallenge_rolls(session,save)
                domain.schedule_marriage_rolls(session,save)
                save.revision += domain.schedule_occult_rolls(session,save)
                save.revision += domain.schedule_event_rolls(session,save)
                save.revision += domain.schedule_campaign_rolls(session,save)
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
            # Neon latency is paid per round trip. Load the Today workspace in
            # one query and partition it in memory instead of issuing a query
            # for each card family. This returns the same records while making
            # button-driven rerenders substantially faster.
            today_kinds={"sim","household","roll","pregnancy","event","illness"}
            today_rows=list(session.scalars(select(Record).where(
                Record.save_id==save.id,
                Record.deleted.is_(False),
                or_(
                    Record.kind.in_(today_kinds),
                    Record.kind.like(r"%\_rule",escape="\\"),
                    (Record.kind=="game_history") & (Record.data["category"].as_string()=="occult"),
                ),
            )))
            rows_by_kind={kind:[] for kind in today_kinds}
            raw_rule_definitions=[];occult_history=[]
            for row in today_rows:
                if row.kind in rows_by_kind: rows_by_kind[row.kind].append(row)
                elif row.kind=="game_history": occult_history.append(row)
                elif row.kind.endswith("_rule"): raw_rule_definitions.append(row)
            by_day_label=lambda item: (int_or_none(item.global_day) if int_or_none(item.global_day) is not None else 10**9,item.label.casefold())
            all_sims=sorted_sims(rows_by_kind["sim"],save)
            all_households=sorted(rows_by_kind["household"],key=lambda item:item.label.casefold())
            all_rolls=sorted(rows_by_kind["roll"],key=by_day_label)
            all_pregnancies=sorted(rows_by_kind["pregnancy"],key=by_day_label)
            all_events=sorted(rows_by_kind["event"],key=by_day_label)
            all_illnesses=sorted(rows_by_kind["illness"],key=by_day_label)
            occult_history.sort(key=lambda item:(int_or_none(item.global_day) or -10**9,item.updated_at),reverse=True)
            current_rule_year=save.start_year+(g-1)//max(1,save.days_per_year)
            rule_definitions=[]
            for rule in raw_rule_definitions:
                data=rule.data or {}
                if not bool(data.get("active",True)) or not concrete_rule_die(rule): continue
                start=int_or_none(data.get("start_year"));end=int_or_none(data.get("end_year"))
                if start is not None and current_rule_year<start or end is not None and current_rule_year>end: continue
                rule_definitions.append(rule)
            rule_definitions.sort(key=lambda rule:(str((rule.data or {}).get("occult") or (rule.data or {}).get("rule_family") or rule.kind),rule.label.casefold()))
            hidden_event_ids={event.id for event in all_events if domain.event_is_ignored(event)}
            all_events=[event for event in all_events if event.id not in hidden_event_ids]
            if hidden_event_ids: all_rolls=[roll for roll in all_rolls if str((roll.data or {}).get("event_id") or "") not in hidden_event_ids]
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
            page_size=50; roll_page=max(1,int_or_none(params.get("roll_page")) or 1); roll_pages=max(1,(len(due_rolls)+page_size-1)//page_size); roll_page=min(roll_page,roll_pages); due_rolls=due_rolls[(roll_page-1)*page_size:roll_page*page_size]
            upcoming_rolls = [r for r in pending_rolls if r.global_day is not None and g < int(r.global_day) <= g + preview_days][:20]
            event_windows=[(
                int_or_none((event.data or {}).get("start_global_day",event.global_day)) or -10**9,
                int_or_none((event.data or {}).get("end_global_day",event.global_day)) or 10**9,
                event.label,
            ) for event in all_events if bool((event.data or {}).get("active",True))]
            event_context={roll.id:[label for start,end,label in event_windows if start<=int(roll.global_day or g)<=end][:5] for roll in due_rolls+upcoming_rolls}
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
                rule_workbench_notice=request.session.pop("rule_workbench_notice",None),
                roll_refresh_notice=request.session.pop("roll_refresh_notice",None))
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
            ctx["clock_sync_version"] = clock_bundle.CLOCK_SYNC_VERSION
            ctx["clock_protocol"] = session.scalar(select(Record).where(
                Record.save_id == save.id, Record.kind == "clock_protocol_state", Record.deleted.is_(False),
            ).limit(1))
            ctx["clock_diagnostic"] = session.scalar(select(Record).where(
                Record.save_id == save.id, Record.kind == "clock_diagnostic", Record.deleted.is_(False),
            ).order_by(Record.updated_at.desc()).limit(1))
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
            ctx["all_sims"] = sorted(story_data.get("all_sims") or [],key=lambda item:item.label.casefold())
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
            birth_events=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="event",Record.deleted.is_(False))))
            birth_suggestions={};location_suggestions={}
            for candidate in (item for item in records if (item.data or {}).get("action")=="new_baby"):
                payload=(candidate.data or {}).get("payload") or candidate.data or {}
                pregnancy=session.get(Record,str(payload.get("pregnancy_id") or "")) if payload.get("pregnancy_id") else None
                if pregnancy and (pregnancy.kind!="pregnancy" or pregnancy.save_id!=save.id or pregnancy.deleted): pregnancy=None
                mother=session.get(Record,str(payload.get("inferred_mother_id") or "")) if payload.get("inferred_mother_id") else None
                maternal_location=historical_sim_location(session,save,mother,candidate.global_day)
                birthplace=payload.get("birthplace") or payload.get("lot_name") or payload.get("world_name") or maternal_location["place"]
                location_suggestions[candidate.id]={**maternal_location,"place":birthplace}
                birth_suggestions[candidate.id]=birth_circumstance_suggestion(session,save,pregnancy,mother,candidate.global_day,birthplace,birth_events)
            for candidate in (item for item in records if (item.data or {}).get("action") in {"relationship_change","household_change"}):
                primary=session.get(Record,str((candidate.data or {}).get("sim_id") or ""))
                payload=(candidate.data or {}).get("payload") or candidate.data or {}
                known_place=payload.get("lot_name") or payload.get("world_name") or ""
                historical=historical_sim_location(session,save,primary,candidate.global_day)
                location_suggestions[candidate.id]={**historical,"place":known_place or historical["place"],"source":"Clock Sync detected lot/world" if known_place else historical["source"]}
            ctx["birth_circumstance_suggestions"]=birth_suggestions
            ctx["historical_location_suggestions"]=location_suggestions
            ctx["journals"] = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "session_journal", Record.deleted.is_(False)).order_by(Record.global_day.desc()).limit(30)))
            ctx["legacy_detections"] = list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="detection_candidate",Record.deleted.is_(False)).order_by(Record.created_at.desc()).limit(50)))
            digest_rows=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="game_candidate",Record.deleted.is_(False),Record.data["status"].as_string()=="pending").order_by(Record.created_at.asc())))
            ctx["automation_digest"]=advanced.automation_digest(digest_rows)
            ctx["automation_notice"]=request.session.pop("automation_notice",None)
        if page == "relationships" and save:
            ctx["relationship_notice"] = request.session.pop("relationship_notice", None)
            domain.schedule_marriage_rolls(session, save)
            date_updates = domain.backfill_generated_marriage_dates(session, save)
            save.revision += date_updates
            relationship_sims = list(session.scalars(select(Record).where(
                Record.save_id==save.id,Record.kind=="sim",Record.deleted.is_(False),
            )))
            relationship_rows = list(session.scalars(select(Record).where(
                Record.save_id==save.id,Record.kind=="relationship",Record.deleted.is_(False),
            )))
            married_ids=set()
            for relationship in relationship_rows:
                rel_data=relationship.data or {}
                if bool(rel_data.get("legally_married")) or "marriage" in str(rel_data.get("type") or "").casefold():
                    married_ids.update((str(rel_data.get("partner1_id") or ""),str(rel_data.get("partner2_id") or "")))
            minimum_age=int((save.settings or {}).get("marriage_min_age_days") or 72)
            match_eligible=[item for item in relationship_sims if _living_sim(item,save) and item.id not in married_ids and int_or_none((item.data or {}).get("birth_global_day")) is not None and save.global_day-int((item.data or {}).get("birth_global_day"))>=minimum_age]
            kinship_depth=max(1,min(8,int((save.settings or {}).get("kinship_detection_generations") or 3)))
            selected_match=request.query_params.get("match_sim") or (match_eligible[0].id if match_eligible else "")
            selected_record=next((item for item in match_eligible if item.id==selected_match),None)
            match_candidates=[]
            for candidate in match_eligible:
                if candidate.id==selected_match: continue
                warning=kinship_warning(selected_match,candidate.id,relationship_sims,kinship_depth)
                age_gap=abs(int((selected_record.data or {}).get("birth_global_day"))-int((candidate.data or {}).get("birth_global_day"))) if selected_record else 0
                match_candidates.append({"sim":candidate,"warning":warning,"score":max(0,100-age_gap)-(100 if warning else 0)})
            match_candidates.sort(key=lambda item:(item["score"],item["sim"].label.casefold()),reverse=True)
            sim_by_id={item.id:item for item in relationship_sims}
            generated_marriage_rolls=[]
            for roll in session.scalars(select(Record).where(
                Record.save_id==save.id,Record.kind=="roll",Record.deleted.is_(False),
                Record.data["completed"].as_boolean().is_(True),
            ).order_by(Record.updated_at.desc())):
                roll_data=roll.data or {};suggested=int_or_none(roll_data.get("suggested_marriage_global_day"))
                if domain._marriage_roll(roll) and suggested is not None:
                    generated_marriage_rolls.append({"roll":roll,"sim":sim_by_id.get(str(roll_data.get("sim_id") or "")),"global_day":suggested})
            selected_match_roll=next((row for row in generated_marriage_rolls if row["sim"] and row["sim"].id==selected_match),None)
            ctx.update(match_eligible=sorted_sims(match_eligible,save),selected_match=selected_match,
                       selected_match_roll=selected_match_roll,match_candidates=match_candidates,
                       kinship_depth=kinship_depth,generated_marriage_rolls=generated_marriage_rolls)
        if save and page in {"sims", "relationships", "households", "pregnancies", "illnesses", "automation", "rolls"}:
            support_rows=support_rows_cache if support_rows_cache is not None else list(session.scalars(select(Record).where(
                Record.save_id==save.id,Record.kind.in_({"sim","household"}),Record.deleted.is_(False),
            )))
            ctx["all_sims"] = sorted_sims((item for item in support_rows if item.kind=="sim" and not item.deleted), save)
            ctx["all_households"] = sorted((item for item in support_rows if item.kind=="household" and not item.deleted),key=lambda item:item.label.casefold())
            ctx["deceased_sim_ids"] = {item.id for item in ctx["all_sims"] if sim_status(item,save)=="Deceased"}
            ctx["photo_record_ids"] = set(session.scalars(select(Portrait.record_id).where(Portrait.save_id == save.id)))
            archived_probe=sorted((item for item in support_rows if item.kind==kind and item.deleted),key=lambda item:item.label.casefold())[:101] if support_rows_cache is not None else list(session.scalars(select(Record).where(
                Record.save_id==save.id,Record.kind==kind,Record.deleted.is_(True),
            ).order_by(Record.label).limit(101))) if kind else []
            ctx["archived_records"]=archived_probe[:100]
            ctx["archived_count"]=(len(archived_probe) if len(archived_probe)<=100 else session.scalar(
                select(func.count()).select_from(Record).where(Record.save_id==save.id,Record.kind==kind,Record.deleted.is_(True))
            )) if kind else 0
            if page=="sims":
                ctx["name_cultures"]=names.library_names(session,save.id,include_recorded=bool(ctx["all_sims"]))
        ctx.update(records=records, kind=kind, portrait_status=portraits.provider_status())
        dedicated = {
            "today":"today.html", "sims":"sims.html", "relationships":"relationships.html", "households":"households.html",
            "pregnancies":"pregnancies.html", "illnesses":"illnesses.html", "automation":"automation.html", "storyline":"storyline.html",
            "family-tree":"family_tree.html", "timeline":"timeline.html", "statistics":"statistics.html", "health":"health.html",
            "plants":"plants.html", "events":"events.html", "notes":"notes.html", "rules":"rules.html", "roll-tables":"roll_tables.html", "occult-rules":"occult_rules.html", "historical-guidance":"historical_guidance.html", "planner":"planner.html", "avatar":"avatar.html", "harry-potter":"harry_potter.html", "game-of-thrones":"game_of_thrones.html",
            "challenge":"challenge.html", "tutorial":"tutorial.html", "guides":"guides.html", "names":"names.html", "saves":"saves.html", "support":"support.html",
            "world":"world.html", "legacy-lab":"legacy_lab.html", "historical-life":"historical_life.html", "life-records":"life-records.html",
            "clock":"clock.html", "sync":"sync.html", "account":"account.html", "appearance":"appearance.html", "dice-audit":"dice_audit.html", "rolls":"rolls.html",
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
            relationship_rows.append({"record":relationship,"partner":sim_by_id.get(other_id),"is_partner":insights.relationship_is_partner(relationship)})
        partner_relationship_rows=[row for row in relationship_rows if row["is_partner"]]
        other_relationship_rows=[row for row in relationship_rows if not row["is_partner"]]
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
        catchup_roll_count=len(domain.prior_lifecycle_rolls(session,save,sim)) if sim_status(sim,save)!="Deceased" else 0
        sim_portraits=list(session.scalars(select(Portrait).where(Portrait.record_id==sim.id).order_by(Portrait.created_at)))
        delete_impact=domain.sim_delete_impact(session,sim) if request.query_params.get("delete")=="1" else None
        name_history={"surname_at_birth":domain.surname_at_birth(sim),"married_surname":domain.married_surname(sim)}
        ctx = context(request, session, sim=sim, name_history=name_history, all_sims=all_sims, all_households=households, relationships=relationships, relationship_rows=relationship_rows, partner_relationship_rows=partner_relationship_rows, other_relationship_rows=other_relationship_rows, parents=parents,children=children,siblings=siblings,current_household=current_household,related_rolls=related_rolls,life_history=life_history,illnesses=illnesses,pregnancies=pregnancies,profile_summary=profile_summary,pregnancy_plan=pregnancy_plan,catchup_roll_count=catchup_roll_count,sim_portraits=sim_portraits,photo_record_ids=set(session.scalars(select(Portrait.record_id).where(Portrait.save_id==save.id))),portrait_notice=request.session.pop("portrait_notice",None),sim_notice=request.session.pop("sim_notice",None), delete_impact=delete_impact, title=sim.label, page="sims")
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
        sim_data={"sim_number":sim_number,"title":title,"first_name":first_name,"last_name":last_name,"suffix":suffix,"surname_at_birth":birth_surname,"maiden_name":birth_surname,"married_surname":married_surname,"married_name":married_surname,"sex":str(form.get("sex") or ""),"generation":int_or_none(form.get("generation")),"birth_global_day":birth,"death_global_day":death,"birth_status":str(form.get("birth_status") or ""),"multiple_birth_status":str(form.get("multiple_birth_status") or ""),"birth_circumstances":str(form.get("birth_circumstances") or ""),"mother_id":mother_id or None,"father_id":father_id or None,"current_household_id":household_id or None,"historical_household":str(form.get("historical_household") or ""),"species_occult":str(form.get("species") or "Human"),"legitimacy":str(form.get("legitimacy") or ""),"fertility_status":str(form.get("fertility_status") or ""),"succession_override":str(form.get("succession_override") or ""),"succession_notes":str(form.get("succession_notes") or ""),"played_through_global_day":int_or_none(form.get("played_through_global_day")),"include_in_family_tree":"include_in_family_tree" not in form or str(form.get("include_in_family_tree") or "").casefold() in {"1","true","on","yes"},"birthplace":str(form.get("birthplace") or ""),"cause_of_death":str(form.get("cause_of_death") or ""),"death_place":str(form.get("death_place") or ""),"notes":str(form.get("notes") or "")}
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
        data = dict(record.data or {});previous_generation=data.get("generation");previous_generation_source=str(data.get("generation_source") or "").casefold();submitted_generation=int_or_none(form.get("generation"));birth_surname=str(form.get("surname_at_birth") or form.get("maiden_name") or data.get("surname_at_birth") or data.get("maiden_name") or last_name).strip();married_surname=str(form.get("married_surname") or form.get("married_name") or "").strip();data.update({"title":title,"first_name":first_name,"last_name":last_name,"suffix":suffix,"surname_at_birth":birth_surname,"maiden_name":birth_surname,"married_surname":married_surname,"married_name":married_surname,"sex":str(form.get("sex") or ""),"generation":submitted_generation,"birth_global_day":birth,"death_global_day":int_or_none(form.get("death_global_day")),"birth_status":str(form.get("birth_status") or ""),"multiple_birth_status":str(form.get("multiple_birth_status") or ""),"birth_circumstances":str(form.get("birth_circumstances") or ""),"mother_id":mother_id or None,"father_id":father_id or None,"current_household_id":household_id or None,"historical_household":str(form.get("historical_household") or ""),"species_occult":str(form.get("species") or "Human"),"occult_alignment":str(form.get("occult_alignment") or ""),"dormant_occult_types":detected_form_list(str(form.get("dormant_occult_types") or "")),"occult_water_access":str(form.get("occult_water_access") or "Unknown"),"werewolf_confined":str(form.get("werewolf_confined") or "").casefold() in {"1","true","on","yes"},"vampire_hunt_exposure":str(form.get("vampire_hunt_exposure") or "Secret identity"),"vampire_suspicion_raised":str(form.get("vampire_suspicion_raised") or "").casefold() in {"1","true","on","yes"},"occult_notes":str(form.get("occult_notes") or ""),"legitimacy":str(form.get("legitimacy") or ""),"fertility_status":str(form.get("fertility_status") or ""),"succession_override":str(form.get("succession_override") or ""),"succession_notes":str(form.get("succession_notes") or ""),"played_through_global_day":int_or_none(form.get("played_through_global_day")),"include_in_family_tree":str(form.get("include_in_family_tree") or "").casefold() in {"1","true","on","yes"},"cause_of_death":str(form.get("cause_of_death") or ""),"birthplace":str(form.get("birthplace") or ""),"death_place":str(form.get("death_place") or ""),"game_career":str(form.get("game_career") or "").strip(),"game_education":str(form.get("game_education") or "").strip(),"game_traits":detected_form_list(str(form.get("game_traits") or "")),"game_skills":detected_form_list(str(form.get("game_skills") or "")),"game_milestones":detected_form_list(str(form.get("game_milestones") or "")),"notes":str(form.get("notes") or "")})
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


@app.post("/sims/{sim_id}/pass-prior-rolls")
def pass_sim_prior_rolls(request: Request, sim_id: str):
    with db() as session:
        sim=session.get(Record,sim_id)
        if not sim or sim.kind!="sim" or sim.deleted: raise HTTPException(404)
        save=owned_save(request,session,sim.save_id)
        result=domain.pass_prior_lifecycle_rolls(session,save,sim)
        if result["passed"]:
            request.session["sim_notice"]=(f"Recorded {result['passed']} earlier life-stage roll"
                f"{'s' if result['passed']!=1 else ''} as passed for {sim.label}.")
        else:
            request.session["sim_notice"]="No unfinished earlier life-stage rolls needed catch-up."
        if result["skipped"]:
            request.session["sim_notice"]+=f" {result['skipped']} roll(s) had no possible passing number and were left unchanged."
    return RedirectResponse(f"/sims/{sim_id}",status_code=303)


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
        first_location=historical_sim_location(session,save,first,start);second_location=historical_sim_location(session,save,second,start)
        automatic_location=first_location["place"] or second_location["place"]
        submitted_location=str(location or "").strip();entered_location=submitted_location or automatic_location
        location_source="Reviewed manual location" if submitted_location else first_location["source"] if first_location["place"] else second_location["source"]
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
                "location": submitted_location or str(data.get("location") or "") or automatic_location,
                "location_source": "Reviewed manual location" if submitted_location else str(data.get("location_source") or "") or location_source,
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
                "location_source": location_source,
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
        married=legally_married in {"1","true","on","yes"} or "marriage" in relationship_type.casefold();start=int_or_none(start_global_day);location_day=start or save.global_day
        first_location=historical_sim_location(session,save,first,location_day);second_location=historical_sim_location(session,save,second,location_day);submitted_location=location.strip();resolved_location=submitted_location or first_location["place"] or second_location["place"];location_source="Reviewed manual location" if submitted_location else first_location["source"] if first_location["place"] else second_location["source"]
        type_folded=relationship_type.strip().casefold();tags=["Family"] if type_folded in {"family","relative","kin"} else ["Friendship"] if type_folded in {"friend","friendship","acquaintance"} else ["Romantic"] if insights.relationship_is_partner({"type":relationship_type,"legally_married":married}) else []
        data={"partner1_id":first.id,"partner2_id":second.id,"partner1_name":first.label,"partner2_name":second.label,"type":relationship_type,"status":status,"start_global_day":start,"end_global_day":int_or_none(end_global_day),"location":resolved_location,"location_source":location_source,"legally_married":married,"surname_rule":surname_rule,"children_count":int_or_none(children_count) or 0,"notes":notes,"relationship_tags":tags,"relationship_classification_source":"manual"}
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
        ctx=context(request,session,relationship=relationship,all_sims=sims,partners=partners,is_partner_relationship=insights.relationship_is_partner(relationship),photo_record_ids=set(session.scalars(select(Portrait.record_id).where(Portrait.save_id==save.id))),relationship_portraits=relationship_portraits,portrait_status=portraits.provider_status(),portrait_notice=request.session.pop("portrait_notice",None),relationship_notice=request.session.pop("relationship_notice",None),title=relationship.label,page="relationships")
        return templates.TemplateResponse(request,"relationship_profile.html",ctx)


@app.post("/relationships/{relationship_id}")
def edit_relationship(request: Request, relationship_id: str, partner1_id: str = Form(...), partner2_id: str = Form(...), relationship_type: str = Form("Marriage"), status: str = Form("Active"), start_global_day: str = Form(""), marriage_game_hour: str = Form(""), marriage_game_minute: str = Form(""), end_global_day: str = Form(""), location: str = Form(""), legally_married: str = Form(""), surname_rule: str = Form(""), children_count: str = Form(""), notes: str = Form("")):
    if partner1_id==partner2_id: raise HTTPException(400,"Choose two different Sims.")
    with db() as session:
        relationship=session.get(Record,relationship_id)
        if not relationship or relationship.kind!="relationship": raise HTTPException(404)
        save=owned_save(request,session,relationship.save_id);first=session.get(Record,partner1_id);second=session.get(Record,partner2_id)
        if not first or not second or first.save_id!=save.id or second.save_id!=save.id: raise HTTPException(400)
        base=relationship.version;data=dict(relationship.data or {});surname_rule=surname_rule or str(data.get("surname_rule") or "automatic");start=int_or_none(start_global_day);married=legally_married in {"1","true","on","yes"} or "marriage" in relationship_type.casefold();type_folded=relationship_type.strip().casefold();tags=["Family"] if type_folded in {"family","relative","kin"} else ["Friendship"] if type_folded in {"friend","friendship","acquaintance"} else ["Romantic"] if insights.relationship_is_partner({"type":relationship_type,"legally_married":married}) else [];submitted_location=location.strip();prior_location=str(data.get("location") or "");auto_location=historical_sim_location(session,save,first,start or save.global_day);resolved_location=submitted_location or prior_location or auto_location["place"];data.update({"partner1_id":first.id,"partner2_id":second.id,"partner1_name":first.label,"partner2_name":second.label,"type":relationship_type,"status":status,"start_global_day":start,"end_global_day":int_or_none(end_global_day),"location":resolved_location,"location_source":"Reviewed manual location" if submitted_location else auto_location["source"] if not prior_location and resolved_location else str(data.get("location_source") or ""),"legally_married":married,"surname_rule":surname_rule,"children_count":int_or_none(children_count) or 0,"notes":notes,"relationship_tags":tags,"relationship_classification_source":"manual"})
        for key in ("marriage_global_day","marriage_game_hour","marriage_game_minute","marriage_time","historical_marriage_date","historical_marriage_date_range","marriage_date_precision"):
            data.pop(key,None)
        if married:
            data["marriage_global_day"]=start;data.update(marriage_calendar_fields(save,start,marriage_game_hour,marriage_game_minute))
        relationship.global_day=data["start_global_day"];relationship.data=data;name_changes=domain.apply_married_surnames(session,relationship,first,second,surname_rule);relationship.label=f"{first.label} & {second.label}";relationship.data={**relationship.data,"partner1_name":first.label,"partner2_name":second.label};relationship.version+=1
        session.add(Change(save_id=save.id,device_id="local" if settings.local_mode else "web",record_id=relationship.id,kind=relationship.kind,operation="upsert",base_version=base,new_version=relationship.version,payload=sync.serialize(relationship)));save.revision+=1+name_changes+domain.sync_generations(session,save);domain.schedule_marriage_rolls(session,save)
        request.session["relationship_notice"]="Relationship updated. Family and friendship records will no longer appear as romantic partners."
    return RedirectResponse(f"/relationships/{relationship_id}",status_code=303)


@app.post("/households")
async def add_household(request: Request):
    form = await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx["save"]
        if not save: raise HTTPException(400)
        name=str(form.get("name") or "").strip()
        if not name: raise HTTPException(400,"Household name is required.")
        head_id=str(form.get("head_sim_id") or "");head=None
        if head_id:
            head=session.get(Record,head_id)
            if not head or head.kind!="sim" or head.save_id!=save.id: raise HTTPException(400,"Invalid household head.")
        start_day=int_or_none(form.get("start_global_day"));submitted_location=str(form.get("location") or "").strip();head_location=historical_sim_location(session,save,head,start_day or save.global_day);resolved_location=submitted_location or head_location["place"]
        data={"household_name":name,"branch_type":str(form.get("branch_type") or "Main"),"location":resolved_location,"location_source":"Reviewed manual location" if submitted_location else head_location["source"],"social_class":str(form.get("social_class") or ""),"head_sim_id":head_id or None,"start_global_day":start_day,"end_global_day":int_or_none(form.get("end_global_day")),"active":form.get("active") in {"1","true","on","yes"},"notes":str(form.get("notes") or "")}
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
        household_portraits=list(session.scalars(select(Record).where(
            Record.save_id==save.id,Record.kind=="household_portrait",Record.deleted.is_(False),
            Record.data["household_id"].as_string()==household.id,
        ).order_by(Record.data["portrait_year"].as_integer().desc())))
        ctx=context(request,session,household=household,all_sims=sims,members=members,household_census=census,household_portraits=household_portraits,photo_record_ids=set(session.scalars(select(Portrait.record_id).where(Portrait.save_id==save.id))),title=household.label,page="households")
        return templates.TemplateResponse(request,"household_profile.html",ctx)


@app.post("/households/{household_id}")
async def edit_household(request: Request, household_id: str):
    form=await request.form()
    with db() as session:
        household=session.get(Record,household_id)
        if not household or household.kind!="household": raise HTTPException(404)
        save=owned_save(request,session,household.save_id);name=str(form.get("name") or "").strip()
        if not name: raise HTTPException(400,"Household name is required.")
        head_id=str(form.get("head_sim_id") or "");head=None
        if head_id:
            head=session.get(Record,head_id)
            if not head or head.kind!="sim" or head.save_id!=save.id: raise HTTPException(400,"Invalid household head.")
        prior=dict(household.data or {});start_day=int_or_none(form.get("start_global_day"));submitted_location=str(form.get("location") or "").strip();prior_location=str(prior.get("location") or "");head_location=historical_sim_location(session,save,head,start_day or save.global_day);resolved_location=submitted_location or prior_location or head_location["place"]
        base=household.version;data={**prior,"household_name":name,"branch_type":str(form.get("branch_type") or "Main"),"location":resolved_location,"location_source":"Reviewed manual location" if submitted_location else head_location["source"] if not prior_location and resolved_location else str(prior.get("location_source") or ""),"social_class":str(form.get("social_class") or ""),"head_sim_id":head_id or None,"start_global_day":start_day,"end_global_day":int_or_none(form.get("end_global_day")),"active":form.get("active") in {"1","true","on","yes"},"notes":str(form.get("notes") or "")}
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
        mother=next((sim for sim in sims if sim.id==(pregnancy.data or {}).get("mother_id")),None)
        suggested_birth_day=int_or_none((pregnancy.data or {}).get("actual_delivery_global_day")) or int_or_none((pregnancy.data or {}).get("due_global_day")) or save.global_day
        suggested_birth_location=historical_sim_location(session,save,mother,suggested_birth_day)
        birth_circumstance=birth_circumstance_suggestion(session,save,pregnancy,mother,suggested_birth_day,suggested_birth_location["place"])
        ctx=context(request,session,pregnancy=pregnancy,all_sims=sims,children=children,pregnancy_progress=progress,progress_history=progress_history[:30],birth_circumstance=birth_circumstance,suggested_birth_location=suggested_birth_location,photo_record_ids=set(session.scalars(select(Portrait.record_id).where(Portrait.save_id==save.id))),title=pregnancy.label,page="pregnancies")
        return templates.TemplateResponse(request,"pregnancy_profile.html",ctx)


@app.post("/pregnancies/{pregnancy_id}")
def edit_pregnancy(request: Request, pregnancy_id: str, mother_id: str = Form(...), father_id: str = Form(""), conception_global_day: str = Form(...), due_global_day: str = Form(""), actual_delivery_global_day: str = Form(""), babies_expected: str = Form("1"), babies_delivered: str = Form("0"), status: str = Form("Active"), outcome: str = Form(""), complication: str = Form(""), maternal_rolls: str = Form(""), newborn_rolls: str = Form(""), notes: str = Form("")):
    with db() as session:
        pregnancy=session.get(Record,pregnancy_id)
        if not pregnancy or pregnancy.kind!="pregnancy": raise HTTPException(404)
        save=owned_save(request,session,pregnancy.save_id);mother=session.get(Record,mother_id);father=session.get(Record,father_id) if father_id else None
        if not mother or mother.kind!="sim" or mother.save_id!=save.id or (father and (father.kind!="sim" or father.save_id!=save.id)): raise HTTPException(400)
        conception=int_or_none(conception_global_day);due=int_or_none(due_global_day) or ((conception or save.global_day)+save.pregnancy_days);base=pregnancy.version
        expected=max(1,int_or_none(babies_expected) or 1)
        try: domain.validate_multiple_birth_count(session,save,due,expected)
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
        data={**(pregnancy.data or {}),"mother_id":mother.id,"mother_name":mother.label,"father_id":father.id if father else None,"father_name":father.label if father else "","conception_global_day":conception,"due_global_day":due,"actual_delivery_global_day":int_or_none(actual_delivery_global_day),"babies_expected":expected,"babies_delivered":max(0,int_or_none(babies_delivered) or 0),"status":status,"outcome":outcome,"complication":complication,"maternal_rolls_required":maternal_rolls in {"1","true","on","yes"},"birth_newborn_rolls_required":newborn_rolls in {"1","true","on","yes"},"notes":notes}
        keeps_maternal=domain.pregnancy_keeps_maternal_roll(status) and data["maternal_rolls_required"]
        if keeps_maternal:
            domain.schedule_rolls(session,save)
        else:
            save.revision+=domain.retire_pregnancy_rolls(session,save,pregnancy.id,"Pregnancy details changed")
        pregnancy.label=f"{mother.label} pregnancy";pregnancy.global_day=due;pregnancy.data=data;pregnancy.version+=1;domain.journal(session,pregnancy,"upsert",base);save.revision+=1
        if keeps_maternal:
            save.revision+=domain.preserve_delivery_maternal_rolls(session,save,pregnancy,data.get("actual_delivery_global_day") or due)
        elif str(status).casefold() not in domain.CLOSED_PREGNANCIES and data["maternal_rolls_required"]:
            domain.schedule_rolls(session,save)
    return RedirectResponse(f"/pregnancies/{pregnancy_id}",status_code=303)


@app.post("/pregnancies/{pregnancy_id}/newborns")
def add_pregnancy_newborn(request: Request, pregnancy_id: str, first_name: str = Form(...), last_name: str = Form(""), sex: str = Form(""), birth_global_day: str = Form(""), birthplace: str = Form(""), legitimacy: str = Form(""), birth_circumstances: str = Form(""), birth_circumstances_reviewed: str = Form(""), notes: str = Form("")):
    with db() as session:
        pregnancy=session.get(Record,pregnancy_id)
        if not pregnancy or pregnancy.kind!="pregnancy" or pregnancy.deleted: raise HTTPException(404)
        save=owned_save(request,session,pregnancy.save_id);data=dict(pregnancy.data or {});mother=session.get(Record,data.get("mother_id"));father=session.get(Record,data.get("father_id")) if data.get("father_id") else None
        if not mother: raise HTTPException(400,"The pregnancy needs a valid mother before a newborn can be added.")
        birth=int_or_none(birth_global_day) or int_or_none(data.get("actual_delivery_global_day")) or int_or_none(data.get("due_global_day")) or save.global_day
        maternal_location=historical_sim_location(session,save,mother,birth);resolved_birthplace=birthplace.strip() or maternal_location["place"]
        suggested=birth_circumstance_suggestion(session,save,pregnancy,mother,birth,resolved_birthplace)
        reviewed=birth_circumstances_reviewed in {"1","true","on","yes"};circumstances=birth_circumstances.strip() if reviewed else suggested["summary"]
        name=" ".join(part.strip() for part in (first_name,last_name) if part.strip());sim_data={"sim_number":next_sim_number(session,save.id),"first_name":first_name.strip(),"last_name":last_name.strip(),"surname_at_birth":last_name.strip(),"maiden_name":last_name.strip(),"sex":sex,"birth_global_day":birth,"birthplace":resolved_birthplace,"birth_country":maternal_location["country"],"birth_location_source":maternal_location["source"] if not birthplace.strip() else "Reviewed manual birthplace","legitimacy":legitimacy.strip(),"birth_status":suggested["birth_status"] or "Live birth","multiple_birth_status":suggested["multiple_birth_status"],"birth_circumstances":circumstances,"birth_circumstance_tags":suggested["tags"] if circumstances==suggested["summary"] else [],"birth_circumstances_source":"Reviewed suggestion" if reviewed else "Automatic tracker inference","death_global_day":None,"mother_id":mother.id,"father_id":father.id if father else None,"current_household_id":mother.data.get("current_household_id"),"species_occult":"Human","pregnancy_id":pregnancy.id,"newborn_rolls_required":bool(data.get("birth_newborn_rolls_required",True)),"notes":notes}
        newborn=Record(save_id=save.id,kind="sim",label=name,global_day=birth,data=sim_data);session.add(newborn);session.flush();domain.journal(session,newborn,"upsert",0)
        base=pregnancy.version;delivered=int(data.get("babies_delivered") or 0)+1;expected=max(1,int(data.get("babies_expected") or 1));will_deliver=delivered>=expected
        if will_deliver and data.get("maternal_rolls_required",True): domain.schedule_rolls(session,save)
        data.update({"babies_delivered":delivered,"actual_delivery_global_day":birth,"delivery_global_day":birth,"status":"Delivered" if will_deliver else "Active","outcome":data.get("outcome") or "Live birth"});pregnancy.data=data;pregnancy.version+=1;domain.journal(session,pregnancy,"upsert",base);save.revision+=2+domain.sync_generations(session,save)
        if data["status"]=="Delivered": save.revision+=domain.preserve_delivery_maternal_rolls(session,save,pregnancy,birth)
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


def _dismiss_automation_candidate(session, save: ChronicleSave, item: Record) -> int:
    """Dismiss one finding and reverse any legacy eager illness mutation."""
    item_data=dict(item.data or {});action=str(item_data.get("action") or "");payload=dict(item_data.get("payload") or {})
    changed=0
    illness=session.get(Record,str(payload.get("illness_record_id") or "")) if payload.get("illness_record_id") else None
    if action in {"illness_detected","unknown_illness"}:
        illness_fallback=(illness.data or {}).get("illness_name") if illness else ""
        illness_name=str(payload.get("illness_name") or payload.get("suggested_name") or illness_fallback or "").strip()
        identity=clock.illness_detection_identity(str(payload.get("detection_identity") or illness_name))
        source_keys=list(dict.fromkeys(str(value).casefold().strip() for value in (
            list(payload.get("game_source_keys") or payload.get("source_keys") or [])
            + ([str(payload.get("source_key"))] if payload.get("source_key") else [])
        ) if value))
        if identity:
            suppression=session.scalar(select(Record).where(
                Record.save_id==save.id,Record.kind=="illness_suppression",Record.deleted.is_(False),
                Record.data["sim_id"].as_string()==str(item_data.get("sim_id") or payload.get("sim_id") or ""),
                Record.data["illness_identity"].as_string()==identity,
            ).limit(1))
            if suppression:
                suppression_base=suppression.version;suppression.data={**(suppression.data or {}),"active":True,"source_keys":source_keys,"dismissed_candidate_id":item.id};suppression.version+=1;domain.journal(session,suppression,"upsert",suppression_base)
            else:
                suppression=Record(save_id=save.id,kind="illness_suppression",label=f"Suppressed illness detection — {illness_name or identity}",global_day=save.global_day,data={"sim_id":str(item_data.get("sim_id") or payload.get("sim_id") or ""),"sim_name":payload.get("sim_name"),"illness_name":illness_name,"illness_identity":identity,"source_keys":source_keys,"active":True,"dismissed_candidate_id":item.id})
                session.add(suppression);session.flush();domain.journal(session,suppression,"upsert",0)
            changed+=1
        if illness and illness.kind=="illness" and illness.save_id==save.id and not illness.deleted and bool((illness.data or {}).get("automatic_detection")):
            illness_base=illness.version;illness.deleted=True;illness.data={**(illness.data or {}),"dismissed_as_detection":True,"dismissed_candidate_id":item.id,"retired_global_day":save.global_day};illness.version+=1;domain.journal(session,illness,"delete",illness_base);changed+=1
    elif action=="illness_recovered" and illness and illness.kind=="illness" and illness.save_id==save.id and not illness.deleted:
        illness_base=illness.version;illness_data=dict(illness.data or {})
        illness_data.update({"status":"Active","end_global_day":None,"recovery_pending":False,"recovery_review_pending":False,"recovery_review_suppressed":True,"auto_recovery_confirmed":False})
        if str(illness_data.get("outcome") or "").casefold()=="no longer detected in game": illness_data["outcome"]=""
        illness.data=illness_data;illness.version+=1;domain.journal(session,illness,"upsert",illness_base);changed+=1
    base=item.version;item.data={**item_data,"status":"dismissed","dismissed_global_day":save.global_day};item.version+=1;domain.journal(session,item,"upsert",base)
    save.revision+=changed+1
    return changed+1


@app.post("/automation/{candidate_id}/dismiss")
def dismiss_automation(request: Request, candidate_id: str):
    with db() as session:
        item=session.get(Record,candidate_id)
        if not item or item.kind!="game_candidate" or str((item.data or {}).get("status") or "pending")!="pending": raise HTTPException(404)
        save=owned_save(request,session,item.save_id);action=str((item.data or {}).get("action") or "")
        _dismiss_automation_candidate(session,save,item)
        request.session["automation_notice"]=(
            "Dismissed and remembered. No illness record was added."
            if action in {"illness_detected","unknown_illness"}
            else "Dismissed. The tracker left your records unchanged."
        )
    return RedirectResponse("/p/automation",status_code=303)


@app.post("/api/automation/{candidate_id}/undo")
def undo_accepted_automation(request: Request, candidate_id: str):
    """Undo one accepted inbox item when none of its affected records changed later."""
    with db() as session:
        item=session.get(Record,candidate_id)
        if not item or item.kind!="game_candidate": raise HTTPException(404)
        save=owned_save(request,session,item.save_id);item_data=dict(item.data or {})
        if str(item_data.get("status") or "")!="accepted": raise HTTPException(400,"Only accepted automation can be undone.")
        targets=list(item_data.get("undo_targets") or [])
        if not targets: raise HTTPException(400,"This older acceptance has no safe undo snapshot.")
        blocked=[];restored=[]
        for target in reversed(targets):
            record=session.get(Record,str(target.get("record_id") or ""))
            expected=int_or_none(target.get("after_version"))
            if not record or record.save_id!=save.id or expected is None or record.version!=expected:
                blocked.append(str(target.get("kind") or "record"));continue
            base=record.version
            if bool(target.get("created")):
                record.deleted=True;record.version+=1;domain.journal(session,record,"delete",base)
            else:
                before=target.get("before_payload")
                if not isinstance(before,dict):
                    blocked.append(record.kind);continue
                label,day,data,deleted=sync.unpack_payload(before)
                record.label=label;record.global_day=day;record.data=data;record.deleted=deleted
                record.version+=1;domain.journal(session,record,"delete" if deleted else "upsert",base)
            restored.append(record.kind)
        if not restored:
            raise HTTPException(409,"Undo was blocked because the affected records were edited afterward.")
        base=item.version;item_data.update({"status":"undone","undone_global_day":save.global_day,
                                           "undo_result":{"restored":restored,"blocked":blocked}})
        item.data=item_data;item.version+=1;domain.journal(session,item,"upsert",base);save.revision+=len(restored)+1
        request.session["life_records_notice"]=(
            f"Undid {len(restored)} automation change{'s' if len(restored)!=1 else ''}."
            + (f" {len(blocked)} later-edited record{'s were' if len(blocked)!=1 else ' was'} left unchanged." if blocked else "")
        )
    return RedirectResponse("/p/life-records#tools",status_code=303)


@app.post("/api/life-records/bulk-sims")
async def bulk_correct_sims(request: Request):
    form=await request.form();sim_ids=list(dict.fromkeys(str(value) for value in form.getlist("sim_id") if value))
    field=str(form.get("field") or "").strip();raw_value=str(form.get("value") or "").strip()
    allowed={"social_class","current_location","generation","dynasty_name","legitimacy","surname_at_birth","current_household_id"}
    if field not in allowed or not sim_ids: raise HTTPException(400,"Choose Sims and one supported field.")
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        value: object=raw_value
        if field=="generation":
            value=int_or_none(raw_value)
            if value is None or value<1: raise HTTPException(400,"Generation must be a positive number.")
        if field=="current_household_id" and raw_value:
            household=session.get(Record,raw_value)
            if not household or household.save_id!=save.id or household.kind!="household" or household.deleted:
                raise HTTPException(400,"Choose a household in this save.")
        rows=list(session.scalars(select(Record).where(
            Record.save_id==save.id,Record.kind=="sim",Record.deleted.is_(False),Record.id.in_(sim_ids))))
        changed=0
        for sim in rows:
            if (sim.data or {}).get(field)==value: continue
            base=sim.version;sim.data={**(sim.data or {}),field:value};sim.version+=1
            domain.journal(session,sim,"upsert",base);changed+=1
        save.revision+=changed;request.session["life_records_notice"]=f"Updated {changed} Sim profile{'s' if changed!=1 else ''}."
    return RedirectResponse("/p/life-records#tools",status_code=303)


@app.post("/api/life-records/newspaper")
async def create_annual_newspaper(request: Request):
    form=await request.form();year=int_or_none(form.get("year"))
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        current_year=insights.current_year(save)
        year=current_year if year is None else year
        if year>current_year: raise HTTPException(400,"The newspaper cannot report a future year.")
        records=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.deleted.is_(False))))
        issue=life_records.annual_newspaper(save,records,year)
        existing=session.scalar(select(Record).where(
            Record.save_id==save.id,Record.kind=="newspaper",Record.deleted.is_(False),
            Record.data["year"].as_integer()==year).limit(1))
        if existing:
            base=existing.version;existing.label=issue["label"];existing.global_day=issue["global_day"]
            existing.data=issue;existing.version+=1;domain.journal(session,existing,"upsert",base);paper=existing
        else:
            paper=Record(save_id=save.id,kind="newspaper",label=issue["label"],global_day=issue["global_day"],data=issue)
            session.add(paper);session.flush();domain.journal(session,paper,"upsert",0)
        save.revision+=1;request.session["life_records_notice"]=f"Generated {paper.label}."
    return RedirectResponse("/p/life-records#newspaper",status_code=303)


@app.post("/api/automation/batch-dismiss")
async def batch_dismiss_automation(request: Request):
    form=await request.form();candidate_ids=list(dict.fromkeys(str(value) for value in form.getlist("candidate_id") if value))
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        rows=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.id.in_(candidate_ids),Record.kind=="game_candidate",Record.deleted.is_(False)))) if candidate_ids else []
        for item in rows:
            if str((item.data or {}).get("status") or "pending")!="pending": continue
            _dismiss_automation_candidate(session,save,item)
            base=item.version;item.data={**(item.data or {}),"batch_reviewed":True};item.version+=1
            domain.journal(session,item,"upsert",base);save.revision+=1
    return RedirectResponse("/p/automation",status_code=303)


@app.post("/automation/{candidate_id}/accept")
async def accept_automation(request: Request, candidate_id: str):
    form = await request.form()
    with db() as session:
        item=session.get(Record,candidate_id)
        if not item or item.kind!="game_candidate" or item.data.get("status")!="pending": raise HTTPException(404)
        save=owned_save(request,session,item.save_id);action=item.data.get("action");payload=item.data.get("payload") or item.data;sim=session.get(Record,item.data.get("sim_id")) if item.data.get("sim_id") else None;resolved_record=sim
        accept_change_floor=session.scalar(select(func.max(Change.sequence))) or 0
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
        if action=="save_portrait":
            portrait_year=int_or_none(value("portrait_year",payload.get("portrait_year"))) or historical_year(save,save.global_day)
            try: portrait_year=int(portrait_year)
            except (TypeError,ValueError): portrait_year=save.start_year+(save.global_day-1)//max(1,save.days_per_year)
            result=decade_portraits.save_from_tray(session,save,portrait_year,str(value("background_color",payload.get("background_color")) or decade_portraits.DEFAULT_BACKGROUND))
            if not result["records"]:
                request.session["automation_notice"]=(
                    "No current household portraits were found yet. In The Sims 4, save each active household to My Library, "
                    "then return here and press Save portrait again. The reminder is still waiting."
                )
                return RedirectResponse("/p/automation",status_code=303)
            resolved_record=result["records"][0]
            payload={**payload,"scan_result":{
                "portraits_saved":len(result["records"]),"tray_portraits_available":result["available"],
                "missing_names":result["missing"],"ambiguous_names":result["ambiguous"],
                "background_color":result["background_color"],
            }}
            item.data={**item.data,"payload":payload}
            request.session["automation_notice"]=(
                f"Saved {len(result['records'])} household portrait{'s' if len(result['records'])!=1 else ''} and one combined Decade Snapshot for {portrait_year}. "
                f"{len(result['missing'])} current household member{'s' if len(result['missing'])!=1 else ''} had no unambiguous Tray portrait."
            )
        elif action=="unknown_illness" and sim:
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
            if action=="illness_detected" and not illness:
                onset=int_or_none(value("onset_global_day",payload.get("onset_global_day"))) or save.global_day
                illness_name=str(value("illness_name",payload.get("illness_name") or "Detected illness") or "Detected illness").strip()
                illness_data={
                    "sim_id":sim.id,"sim_name":sim.label,"illness_name":illness_name,
                    "onset_global_day":onset,"end_global_day":None,"status":"Active",
                    "severity":str(value("severity",payload.get("severity") or "Unrated") or "Unrated"),
                    "contagious":checked("contagious",bool(payload.get("contagious"))),
                    "treatment":"","outcome":"","notes":"Accepted from a reviewed Clock Sync detection.",
                    "source":"game","source_key":str(payload.get("source_key") or payload.get("detection_identity") or ""),
                    "provider":payload.get("provider") or "game","automatic_detection":True,
                    "game_source_keys":list(payload.get("game_source_keys") or payload.get("source_keys") or []),
                    "last_detected_global_day":save.global_day,
                    "symptoms":payload.get("symptoms") or [],"health_buffs":payload.get("health_buffs") or [],
                    "onset_game_hour":int_or_none(value("onset_game_hour",payload.get("detected_game_hour"))),
                    "onset_game_minute":int_or_none(value("onset_game_minute",payload.get("detected_game_minute"))),
                    "onset_game_second":int_or_none(value("onset_game_second",payload.get("detected_game_second"))) or 0,
                    "accepted_candidate_id":item.id,
                }
                illness=Record(save_id=save.id,kind="illness",label=f"{sim.label} — {illness_name}",global_day=onset,data=illness_data)
                session.add(illness);session.flush();domain.journal(session,illness,"upsert",0);save.revision+=1
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
                                         "end_global_day":None if status.casefold() not in domain.CLOSED_ILLNESSES else illness_data.get("end_global_day"),
                                         "onset_game_hour":int_or_none(value("onset_game_hour",payload.get("detected_game_hour"))),
                                         "onset_game_minute":int_or_none(value("onset_game_minute",payload.get("detected_game_minute"))),
                                         "onset_game_second":int_or_none(value("onset_game_second",payload.get("detected_game_second"))) or 0})
                else:
                    status=str(value("status","Recovered") or "Recovered")
                    recovery=int_or_none(value("recovery_global_day",illness_data.get("end_global_day"))) or save.global_day
                    illness_data.update({"illness_name":illness_name,"status":status,
                                         "end_global_day":recovery if status.casefold() in domain.CLOSED_ILLNESSES else None,
                                         "outcome":str(value("outcome",illness_data.get("outcome") or "No longer detected in game") or ""),
                                         "recovery_review_pending":False,"recovery_review_suppressed":False,
                                         "auto_recovery_confirmed":status.casefold() in domain.CLOSED_ILLNESSES,
                                         "recovery_game_hour":int_or_none(value("recovery_game_hour",payload.get("detected_game_hour"))),
                                         "recovery_game_minute":int_or_none(value("recovery_game_minute",payload.get("detected_game_minute"))),
                                         "recovery_game_second":int_or_none(value("recovery_game_second",payload.get("detected_game_second"))) or 0})
                    if status.casefold() not in domain.CLOSED_ILLNESSES:
                        illness_data.update({"missing_scan_global_days":[], "recovery_pending":False,
                                             "auto_recovery_confirmed":False})
                illness.label=f"{sim.label} — {illness_name}";illness.data=illness_data;illness.version+=1
                domain.journal(session,illness,"upsert",base);resolved_record=illness;save.revision+=1
        elif action=="scandal_detected" and sim:
            other=chosen_sim("other_sim_id") or payload_sim("other_sim_id")
            title=str(value("scandal_title",payload.get("label") or "Possible scandal") or "Possible scandal").strip()
            impact=int_or_none(value("impact",-10));impact=-10 if impact is None else max(-100,min(100,impact))
            scandal_day=int_or_none(value("global_day",payload.get("detected_tracker_global_day"))) or save.global_day
            event=Record(save_id=save.id,kind="reputation_event",label=f"{sim.label} — {title}",global_day=scandal_day,
                         data={"sim_id":sim.id,"sim_name":sim.label,"other_sim_id":other.id if other else None,
                               "other_sim_name":other.label if other else str(payload.get("other_sim_name") or ""),
                               "reputation_kind":"Scandal","scandal_type":str(value("scandal_type",payload.get("type") or "possible_scandal")),
                               "impact":impact,"consequence":str(value("consequence","") or ""),
                               "evidence":payload.get("evidence"),"relationship_bits":payload.get("relationship_bits") or [],
                               "source":"Reviewed Clock Sync signal","source_candidate_id":item.id})
            session.add(event);session.flush();domain.journal(session,event,"upsert",0);save.revision+=1;resolved_record=event
        elif action=="legal_signal" and sim:
            case_day=int_or_none(value("global_day",payload.get("detected_tracker_global_day"))) or save.global_day
            offense=str(value("offense",payload.get("signal_label") or "Possible legal matter") or "Possible legal matter").strip()
            case=Record(save_id=save.id,kind="legal_case",label=f"{sim.label} — {offense}",global_day=case_day,
                        data={"sim_id":sim.id,"sim_name":sim.label,"offense":offense,
                              "case_status":str(value("case_status","Under review") or "Under review"),
                              "punishment":str(value("punishment","") or ""),
                              "sentence_end_global_day":int_or_none(value("sentence_end_global_day")),
                              "notes":str(value("notes","") or ""),"source":"Reviewed Clock Sync legal signal",
                              "source_mod":payload.get("source_mod") or "Law and Disorder compatible telemetry",
                              "evidence":payload.get("signal_label"),"source_candidate_id":item.id})
            session.add(case);session.flush();domain.journal(session,case,"upsert",0);save.revision+=1;resolved_record=case
        elif action=="grief_detected" and sim:
            start=int_or_none(value("start_global_day",payload.get("start_global_day"))) or save.global_day
            end=int_or_none(value("end_global_day",payload.get("suggested_end_global_day")))
            deceased=session.get(Record,str(payload.get("deceased_sim_id") or "")) if payload.get("deceased_sim_id") else None
            label=str(value("mourning_label",f"Mourning for {payload.get('deceased_name') or 'a loved one'}") or "Mourning").strip()
            mourning=Record(save_id=save.id,kind="mourning",label=f"{sim.label} — {label}",global_day=start,
                            data={"sim_id":sim.id,"sim_name":sim.label,"deceased_sim_id":deceased.id if deceased else payload.get("deceased_sim_id"),
                                  "deceased_name":deceased.label if deceased else payload.get("deceased_name"),
                                  "relationship":str(value("relationship",payload.get("relationship") or "Loved one") or "Loved one"),
                                  "start_global_day":start,"end_global_day":end,"status":str(value("status","Active") or "Active"),
                                  "customs":str(value("customs","") or ""),"source":"Reviewed grief automation","source_candidate_id":item.id})
            session.add(mourning);session.flush();domain.journal(session,mourning,"upsert",0);save.revision+=1;resolved_record=mourning
            if checked("create_wellbeing",True):
                wellbeing=Record(save_id=save.id,kind="wellbeing",label=f"{sim.label} — Grieving",global_day=start,
                                 data={"sim_id":sim.id,"sim_name":sim.label,"state":"Grieving","start_global_day":start,
                                       "end_global_day":end,"support":str(value("support","") or ""),
                                       "notes":str(value("wellbeing_notes","") or ""),"source":"Reviewed grief automation",
                                       "mourning_record_id":mourning.id,"source_candidate_id":item.id})
                session.add(wellbeing);session.flush();domain.journal(session,wellbeing,"upsert",0);save.revision+=1
        elif action in {"new_sim","new_baby"}:
            first=str(value("first_name") or "").strip();last=str(value("last_name") or "").strip();name=" ".join(x for x in (first,last) if x) or item.label
            birth_estimate=clock.estimate_new_sim_birth(session,save,payload,item.global_day) if action=="new_sim" else {}
            submitted_birth=int_or_none(value("birth_global_day"));submitted_birth_year=int_or_none(value("birth_year"));submitted_age=int_or_none(value("age_days"))
            birth=submitted_birth if submitted_birth is not None else (save.global_day if action=="new_baby" else int(birth_estimate.get("estimated_birth_global_day",save.global_day-(submitted_age or 0))))
            birth_hour=int_or_none(value("birth_game_hour",item.data.get("hour") if action=="new_baby" else ""));birth_minute=int_or_none(value("birth_game_minute",item.data.get("minute") if action=="new_baby" else ""));birth_second=int_or_none(value("birth_game_second",payload.get("detected_game_second") if action=="new_baby" else ""))
            birth,reviewed_birth_fields=resolve_birth_input(save,birth,submitted_birth_year,birth_hour,birth_minute,birth_second)
            home=chosen_household() if "household_id" in form else payload_household("inferred_household_id")
            mother=chosen_sim("mother_id") if "mother_id" in form else payload_sim("inferred_mother_id")
            father=chosen_sim("father_id") if "father_id" in form else payload_sim("inferred_father_id")
            pregnancy=session.get(Record,str(payload.get("pregnancy_id") or "")) if action=="new_baby" and payload.get("pregnancy_id") else None
            if pregnancy and (pregnancy.kind!="pregnancy" or pregnancy.save_id!=save.id or pregnancy.deleted): pregnancy=None
            accepted_estimate=bool(action=="new_sim" and submitted_birth_year is None and birth_estimate and birth==birth_estimate.get("estimated_birth_global_day"))
            occult=game_metadata.occult_identity(payload);species=str(value("species_occult",occult.get("display") or "Human") or "Human")
            maternal_location=historical_sim_location(session,save,mother,birth)
            detected_birthplace=payload.get("birthplace") or payload.get("lot_name") or payload.get("world_name") or maternal_location["place"]
            resolved_birthplace=str(value("birthplace",detected_birthplace) or "").strip()
            suggested=birth_circumstance_suggestion(session,save,pregnancy,mother,birth,resolved_birthplace) if action=="new_baby" else {"summary":"","tags":[],"birth_status":"","multiple_birth_status":""}
            circumstances=str(value("birth_circumstances",suggested["summary"]) or "").strip()
            sim_data={"sim_number":next_sim_number(session,save.id),"first_name":first or name,"last_name":last,"sex":str(value("sex") or ""),"birth_global_day":birth,"birthplace":resolved_birthplace,"birth_country":maternal_location["country"] if action=="new_baby" else str(payload.get("birth_country") or ""),"birth_location_source":("Clock Sync detected lot/world" if (payload.get("birthplace") or payload.get("lot_name") or payload.get("world_name")) else maternal_location["source"]) if action=="new_baby" else "","legitimacy":str(value("legitimacy",payload.get("legitimacy") or "") or "").strip(),"birth_status":suggested["birth_status"] or ("Live birth" if action=="new_baby" else ""),"multiple_birth_status":suggested["multiple_birth_status"],"birth_circumstances":circumstances,"birth_circumstance_tags":suggested["tags"] if circumstances==suggested["summary"] else [],"birth_circumstances_source":"Reviewed Clock Sync suggestion" if action=="new_baby" else "","mother_id":mother.id if mother else None,"father_id":father.id if father else None,"current_household_id":home.id if home else None,"pregnancy_id":pregnancy.id if pregnancy else None,"game_sim_id":str(payload.get("game_sim_id") or ""),"game_household_id":payload.get("household_id"),"game_household_name":payload.get("household_name"),"game_age_stage":str(value("age_stage") or ""),"game_age_days_at_detection":submitted_age if submitted_age is not None else birth_estimate.get("estimated_age_days"),"game_age_progress_percentage":payload.get("age_progress_percentage"),"game_career":str(value("career") or ""),"game_education":str(value("education") or ""),"game_traits":detected_form_list(value("traits")),"game_skills":detected_form_list(value("skills")),"game_milestones":detected_form_list(value("milestones")),"parent_game_sim_ids":[str(v) for v in (payload.get("parent_game_sim_ids") or []) if v],"game_parents":[row for row in (payload.get("parents") or []) if isinstance(row,dict)],"last_game_world":payload.get("world_name"),"last_game_lot":payload.get("lot_name"),"last_household_funds":payload.get("household_funds")}
            sim_data.update({
                "child_game_sim_ids":[str(v) for v in (payload.get("child_game_sim_ids") or []) if v],
                "sibling_game_sim_ids":[str(v) for v in (payload.get("sibling_game_sim_ids") or []) if v],
                "grandparent_game_sim_ids":[str(v) for v in (payload.get("grandparent_game_sim_ids") or []) if v],
                "grandchild_game_sim_ids":[str(v) for v in (payload.get("grandchild_game_sim_ids") or []) if v],
                "game_relationships":[row for row in (payload.get("relationships") or []) if isinstance(row,dict)],
                "game_careers":[row for row in (payload.get("careers") or []) if isinstance(row,dict)],
                "game_degrees":game_metadata.readable_named_labels(payload.get("degrees"),payload.get("degree_details"),kind="degree"),"game_school":payload.get("school"),
                "game_health_buffs":[row for row in (payload.get("health_buffs") or []) if isinstance(row,dict)],
                "game_symptoms":detected_form_list(payload.get("symptoms")),"game_occult_progress":payload.get("occult_progress") or {},
                "game_aspirations":game_metadata.readable_named_labels(payload.get("aspirations"),payload.get("aspiration_details"),kind="aspiration"),"game_active_aspiration":next(iter(game_metadata.readable_named_labels(payload.get("active_aspiration"),kind="aspiration")),None),
                "game_completed_aspirations":game_metadata.readable_named_labels(payload.get("completed_aspirations"),payload.get("aspiration_details"),kind="aspiration"),"game_lifestyles":game_metadata.readable_named_labels(payload.get("lifestyles"),payload.get("trait_details"),kind="lifestyle"),
                "game_fears":game_metadata.readable_named_labels(payload.get("fears"),payload.get("trait_details"),kind="fear"),"game_character_values":game_metadata.readable_named_labels(payload.get("character_values"),payload.get("trait_details"),kind="trait"),
                "game_preferences":game_metadata.readable_named_labels(payload.get("preferences"),kind="preference"),"game_portrait":payload.get("game_portrait") or {},
                "clock_sync_version":payload.get("clock_sync_version"),"game_build":payload.get("game_build"),
                "game_installed_packs":detected_form_list(payload.get("installed_packs")),"game_detected_optional_mods":detected_form_list(payload.get("detected_optional_mods")),
                "game_telemetry_capabilities":payload.get("telemetry_capabilities") or {},"game_clock_diagnostics":payload.get("clock_sync_diagnostics") or {},
                "game_skill_details":[row for row in (payload.get("skill_details") or []) if isinstance(row,dict)],
                "game_milestone_details":[row for row in (payload.get("milestone_details") or []) if isinstance(row,dict)],
                "game_trait_details":[row for row in (payload.get("trait_details") or []) if isinstance(row,dict)],
                "game_degree_details":[row for row in (payload.get("degree_details") or []) if isinstance(row,dict)],
                "game_aspiration_details":[row for row in (payload.get("aspiration_details") or []) if isinstance(row,dict)],
                "game_stable_tuning_ids":payload.get("stable_tuning_ids") or {},
            })
            sim_data["surname_at_birth"] = last
            sim_data["maiden_name"] = last
            sim_data["species_occult"] = species
            sim_data["game_occult_types"] = occult.get("types") or []
            if occult.get("display"): sim_data["game_occult_source"] = occult.get("source")
            if birth_estimate:
                sim_data.update({key:value for key,value in birth_estimate.items() if key.startswith("birth_estimate_") or key.startswith("estimated_birth_global_day_range_")})
                sim_data.update({"original_birth_estimate_global_day":birth_estimate.get("estimated_birth_global_day"),"birth_global_day_estimated":accepted_estimate and birth_estimate.get("birth_estimate_precision")!="reported-birth-day"})
            sim_data.update(reviewed_birth_fields);sim_data["birth_time_source"]=reviewed_birth_fields.get("birth_estimate_source") if submitted_birth_year is not None else "Clock Sync newborn detection" if action=="new_baby" and birth_hour is not None and birth_minute is not None else birth_estimate.get("birth_estimate_source") if accepted_estimate else "Reviewed manual birth day"
            sim=Record(save_id=save.id,kind="sim",label=name,global_day=birth,data=sim_data);session.add(sim);session.flush();domain.journal(session,sim,"upsert",0);clock._store_game_portrait(session,save,sim,payload);resolved_record=sim
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
            death_details=payload.get("death_details") or {};death_day=int_or_none(value("death_global_day",payload.get("detected_tracker_global_day"))) or save.global_day;death_hour=int_or_none(value("death_game_hour",payload.get("detected_game_hour")));death_minute=int_or_none(value("death_game_minute",payload.get("detected_game_minute")));death_second=int_or_none(value("death_game_second",payload.get("detected_game_second")));cause=str(value("cause_of_death",payload.get("death_type") or death_details.get("death_type") or "Detected in game") or "Detected in game");place=str(value("death_place",payload.get("lot_name") or death_details.get("place") or "") or "");calendar=death_calendar_fields(save,death_day,death_hour,death_minute,death_second)
            base=sim.version;sim.data={**sim.data,"death_global_day":death_day,"cause_of_death":cause,"death_place":place,"death_confirmed":True,"death_time_source":"Clock Sync death transition" if death_hour is not None and death_minute is not None else "Reviewed detection",**calendar};sim.version+=1;domain.journal(session,sim,"upsert",base)
            death=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="death",Record.deleted.is_(False),Record.data["sim_id"].as_string()==sim.id,Record.global_day==death_day).order_by(Record.created_at.desc()).limit(1))
            if death:
                death_base=death.version;death.data={**death.data,"sim_id":sim.id,"cause":cause,"place":place,"death_global_day":death_day,"completed":True,"confirmed_global_day":death_day,"source_candidate_id":item.id,**calendar};death.version+=1;domain.journal(session,death,"upsert",death_base)
            else:
                death=Record(save_id=save.id,kind="death",label=f"Death of {sim.label}",global_day=death_day,data={"sim_id":sim.id,"cause":cause,"place":place,"death_global_day":death_day,"completed":True,"confirmed_global_day":death_day,"source":"game","source_candidate_id":item.id,**calendar});session.add(death);session.flush();domain.journal(session,death,"upsert",0)
            grief_reviews=life_records.schedule_grief_candidates(session,save,sim,death_day)
            resolved_record=death;save.revision+=1+len(grief_reviews)+domain.end_illnesses_for_death(session,save,sim,death_day);domain.schedule_rolls(session,save)
        elif action=="sim_resurrection" and sim:
            resurrection_day=int_or_none(value("resurrection_global_day",payload.get("detected_tracker_global_day"))) or save.global_day
            resurrection_hour=int_or_none(value("resurrection_game_hour",payload.get("detected_game_hour")))
            resurrection_minute=int_or_none(value("resurrection_game_minute",payload.get("detected_game_minute")))
            prior_death_day=sim.data.get("death_global_day")
            prior_cause=sim.data.get("cause_of_death")
            base=sim.version
            sim.data={**sim.data,"death_global_day":None,"cause_of_death":"","death_place":"","death_confirmed":False,
                      "death_game_hour":None,"death_game_minute":None,"death_time":None,"historical_death_date":None,
                      "game_was_dead":False,"resurrection_global_day":resurrection_day,
                      "resurrection_game_hour":resurrection_hour,"resurrection_game_minute":resurrection_minute,
                      "resurrection_notes":str(value("resurrection_notes","Detected alive in game") or "Detected alive in game")}
            sim.version+=1;domain.journal(session,sim,"upsert",base)
            deaths=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="death",Record.deleted.is_(False),Record.data["sim_id"].as_string()==sim.id).order_by(Record.global_day.desc()).limit(1)))
            if deaths:
                death=deaths[0];death_base=death.version;death.data={**death.data,"resurrected":True,"resurrection_global_day":resurrection_day};death.version+=1;domain.journal(session,death,"upsert",death_base)
            resolved_record=telemetry.history_event(session,save,category="resurrection",label=f"{sim.label} returned to life.",snapshot={"detected_game_day":payload.get("detected_game_day"),"detected_game_hour":resurrection_hour,"detected_game_minute":resurrection_minute},sim=sim,details={"prior_death_global_day":prior_death_day,"prior_cause_of_death":prior_cause,"resurrection_global_day":resurrection_day})
            save.revision+=1;domain.schedule_rolls(session,save)
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
                discovered_hour=int_or_none(payload.get("detected_game_hour"));discovered_minute=int_or_none(payload.get("detected_game_minute"));discovered_second=int_or_none(payload.get("detected_game_second"))
                pregnancy_data={"mother_id":sim.id,"mother_name":sim.label,"father_id":father.id if father else None,"father_name":father.label if father else "","conception_global_day":conception,"due_global_day":due,"babies_expected":expected,"babies_delivered":0,"status":"Active","maternal_rolls_required":checked("maternal_rolls_required",True),"birth_newborn_rolls_required":checked("birth_newborn_rolls_required",True),"source":"game","game_pregnancy_sequence":sim.data.get("game_pregnancy_sequence"),"discovered_global_day":int_or_none(payload.get("detected_tracker_global_day")) or save.global_day,"discovered_game_hour":discovered_hour,"discovered_game_minute":discovered_minute,"discovered_game_second":discovered_second}
                if discovered_hour is not None and discovered_minute is not None: pregnancy_data["discovered_game_time"]=(f"{discovered_hour:02d}:{discovered_minute:02d}:{discovered_second:02d}" if discovered_second is not None else f"{discovered_hour:02d}:{discovered_minute:02d}")
                pregnancy=Record(save_id=save.id,kind="pregnancy",label=f"{sim.label} pregnancy",global_day=due,data=pregnancy_data);session.add(pregnancy);session.flush();domain.journal(session,pregnancy,"upsert",0);domain.schedule_rolls(session,save)
        elif action=="pregnancy_outcome" and sim:
            pregnancy=session.get(Record,str(payload.get("pregnancy_id") or "")) if payload.get("pregnancy_id") else None
            if not pregnancy or pregnancy.kind!="pregnancy" or pregnancy.save_id!=save.id: pregnancy=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="pregnancy",Record.deleted.is_(False),Record.data["mother_id"].as_string()==sim.id).order_by(Record.global_day.desc()))
            if pregnancy:
                status=str(value("status","Delivered") or "Delivered");delivery=int_or_none(value("delivery_global_day")) or save.global_day;detected=int_or_none(value("babies_delivered",payload.get("babies_delivered")));delivered=max(0,detected if detected is not None else (int_or_none(pregnancy.data.get("babies_expected")) or 1))
                if status.casefold() in {"miscarriage","cancelled","canceled"} and "babies_delivered" not in form: delivered=0
                delivery_hour=int_or_none(value("delivery_game_hour",payload.get("detected_game_hour")));delivery_minute=int_or_none(value("delivery_game_minute",payload.get("detected_game_minute")));delivery_second=int_or_none(value("delivery_game_second",payload.get("detected_game_second")));delivery_exact=calendar_utils.exact_historical_label(delivery,delivery_hour,delivery_minute,save.start_year,save.days_per_year) if delivery_hour is not None and delivery_minute is not None else ""
                delivery_details={"delivery_game_hour":delivery_hour,"delivery_game_minute":delivery_minute,"delivery_time":(f"{delivery_hour:02d}:{delivery_minute:02d}:{delivery_second:02d}" if delivery_second is not None else f"{delivery_hour:02d}:{delivery_minute:02d}") if delivery_hour is not None and delivery_minute is not None else None,"historical_delivery_date":delivery_exact or None}
                if delivery_second is not None: delivery_details["delivery_game_second"]=delivery_second
                keeps_maternal=domain.pregnancy_keeps_maternal_roll(status) and (pregnancy.data or {}).get("maternal_rolls_required",True)
                if keeps_maternal: domain.schedule_rolls(session,save)
                base=pregnancy.version;pregnancy.data={**pregnancy.data,"status":status,"babies_delivered":delivered,"actual_delivery_global_day":delivery,"delivery_global_day":delivery,"outcome":str(value("outcome",status) or status),"complication":str(value("complication") or "") or None,**delivery_details};pregnancy.version+=1;domain.journal(session,pregnancy,"upsert",base)
                if keeps_maternal:
                    save.revision+=domain.preserve_delivery_maternal_rolls(session,save,pregnancy,delivery)
                elif domain.pregnancy_retires_maternal_roll(status):
                    save.revision+=domain.retire_pregnancy_rolls(session,save,pregnancy.id,f"Pregnancy reviewed as {status}")
                resolved_record=pregnancy
        elif action=="relationship_change" and sim:
            other=chosen_sim("other_sim_id") or session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="sim",Record.data["game_sim_id"].as_string()==str(payload.get("other_game_sim_id") or "")))
            if other:
                category=str(value("relationship_type",payload.get("category") or "Relationship")).title();start=int_or_none(value("start_global_day",payload.get("detected_tracker_global_day"))) or save.global_day;married=checked("legally_married",category.casefold()=="marriage") or "marriage" in category.casefold();marriage_hour=int_or_none(value("marriage_game_hour",payload.get("detected_game_hour"))) if married else None;marriage_minute=int_or_none(value("marriage_game_minute",payload.get("detected_game_minute"))) if married else None;marriage_second=int_or_none(value("marriage_game_second",payload.get("detected_game_second"))) if married else None
                primary_location=historical_sim_location(session,save,sim,start);secondary_location=historical_sim_location(session,save,other,start);detected_location=payload.get("lot_name") or payload.get("world_name") or primary_location["place"] or secondary_location["place"];relationship_location=str(value("location",detected_location) or "").strip();relationship_location_source="Clock Sync detected lot/world" if (payload.get("lot_name") or payload.get("world_name")) else primary_location["source"] if primary_location["place"] else secondary_location["source"]
                existing_relationships=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="relationship",Record.deleted.is_(False))))
                rel=next((record for record in existing_relationships if {str((record.data or {}).get("partner1_id") or ""),str((record.data or {}).get("partner2_id") or "")}=={sim.id,other.id} and (("marriage" in str((record.data or {}).get("type") or "").casefold() or bool((record.data or {}).get("legally_married"))) if married else str((record.data or {}).get("type") or "").casefold()==category.casefold())),None)
                calendar=marriage_calendar_fields(save,start,marriage_hour,marriage_minute,marriage_second) if married else {}
                surname_rule=str(value("surname_rule","automatic") or "automatic")
                rel_data={"partner1_id":sim.id,"partner2_id":other.id,"partner1_name":sim.label,"partner2_name":other.label,"type":category,"status":str(value("relationship_status","Active") or "Active"),"start_global_day":start,"location":relationship_location,"location_source":relationship_location_source,"legally_married":married,"surname_rule":surname_rule,"game_detected":True,"source_candidate_id":item.id,"relationship_tags":payload.get("relationship_tags") or [],"relationship_bits":payload.get("relationship_bits") or [],"friendship_score":payload.get("friendship_score"),"romance_score":payload.get("romance_score"),"relationship_classification_source":payload.get("relationship_classification_source"),**calendar}
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
                    end_hour=int_or_none(value("end_game_hour",payload.get("detected_game_hour")));end_minute=int_or_none(value("end_game_minute",payload.get("detected_game_minute")));end_second=int_or_none(value("end_game_second",payload.get("detected_game_second")))
                    end_status=str(value("relationship_status","Ended") or "Ended")
                    historical_end=calendar_utils.exact_historical_label(end_day,end_hour,end_minute,save.start_year,save.days_per_year) if end_hour is not None and end_minute is not None else None
                    end_fields={"end_game_hour":end_hour,"end_game_minute":end_minute,"end_time":(f"{end_hour:02d}:{end_minute:02d}:{end_second:02d}" if end_second is not None else f"{end_hour:02d}:{end_minute:02d}") if end_hour is not None and end_minute is not None else None}
                    if end_second is not None: end_fields["end_game_second"]=end_second
                    rel_base=rel.version;rel.data={**rel.data,"status":end_status,"end_global_day":end_day,**end_fields,"historical_end_date":historical_end,"legally_married":bool((rel.data or {}).get("legally_married")) and end_status.casefold()=="widowed","end_source":"Clock Sync relationship transition","source_candidate_id":item.id};rel.version+=1;domain.journal(session,rel,"upsert",rel_base);resolved_record=rel;save.revision+=1+domain.sync_generations(session,save);domain.schedule_marriage_rolls(session,save)
        automation.resolve_parent_links(session,save)
        reviewed={key:str(value) for key,value in form.multi_items() if key not in {"confirm"}}
        acceptance_changes=list(session.scalars(select(Change).where(
            Change.save_id==save.id,Change.sequence>accept_change_floor,Change.record_id!=item.id,
        ).order_by(Change.sequence)))
        grouped_changes={}
        for change in acceptance_changes:
            grouped_changes.setdefault(change.record_id,[]).append(change)
        undo_targets=[]
        for record_id,changes in grouped_changes.items():
            first,last=changes[0],changes[-1]
            before=None
            if first.base_version:
                previous=session.scalar(select(Change).where(
                    Change.save_id==save.id,Change.record_id==record_id,Change.new_version==first.base_version,
                ).order_by(Change.sequence.desc()).limit(1))
                before=dict(previous.payload or {}) if previous else None
            if first.base_version==0 or before:
                undo_targets.append({"record_id":record_id,"kind":last.kind,"before_version":first.base_version,
                                     "after_version":last.new_version,"before_payload":before,"created":first.base_version==0})
        base=item.version;item.data={**item.data,"status":"accepted","accepted_global_day":save.global_day,
                                    "reviewed_details":reviewed,"resolved_record_id":resolved_record.id if resolved_record else None,
                                    "undo_targets":undo_targets};item.version+=1;domain.journal(session,item,"upsert",base);save.revision+=2
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


@app.post("/api/challenge/matchmaking", include_in_schema=False)
@app.post("/api/relationships/courtship")
def create_matchmaking_courtship(request: Request, first_id: str = Form(...), second_id: str = Form(...),
                                 suggested_marriage_global_day: str = Form(""), source_roll_id: str = Form("")):
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Choose a save first")
        sims=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="sim",Record.deleted.is_(False))))
        first=next((item for item in sims if item.id==first_id),None);second=next((item for item in sims if item.id==second_id),None)
        if not first or not second: raise HTTPException(404,"Sim not found")
        depth=max(1,min(8,int((save.settings or {}).get("kinship_detection_generations") or 3)))
        warning=kinship_warning(first.id,second.id,sims,depth)
        if warning: raise HTTPException(409,f"Courtship blocked: {warning}")
        relationships=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="relationship",Record.deleted.is_(False))))
        existing=next((item for item in relationships if {str((item.data or {}).get("partner1_id") or ""),str((item.data or {}).get("partner2_id") or "")}=={first.id,second.id} and str((item.data or {}).get("status") or "Active").casefold() not in {"ended","divorced","annulled"}),None)
        if not existing:
            suggested=int_or_none(suggested_marriage_global_day)
            record=Record(save_id=save.id,kind="relationship",label=f"{first.label} & {second.label}",global_day=save.global_day,data={"partner1_id":first.id,"partner2_id":second.id,"partner1_name":first.label,"partner2_name":second.label,"type":"Courtship","status":"Active","legally_married":False,"start_global_day":save.global_day,"suggested_marriage_global_day":suggested,"suggested_marriage_date_range":calendar_utils.date_range_label(suggested,save.start_year,save.days_per_year) if suggested is not None else None,"source_marriage_roll_id":source_roll_id or None,"source":"Relationships matchmaking"})
            session.add(record);session.flush();domain.journal(session,record,"upsert",0);save.revision+=1
            request.session["relationship_notice"] = f"Courtship created for {first.label} and {second.label}." + (f" Suggested marriage: Global Day {suggested}." if suggested is not None else "")
        else:
            request.session["relationship_notice"] = "Those Sims already have an active relationship, so no duplicate courtship was created."
    return RedirectResponse("/p/relationships?match_sim="+first_id,status_code=303)


@app.post("/api/historical-life/profile")
def update_historical_accuracy_profile(request: Request, profile: str = Form(...)):
    if profile not in historical_life.ACCURACY_PROFILES:
        raise HTTPException(400, "Choose a supported historical accuracy profile.")
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        values=dict(save.settings or {});values["historical_accuracy_profile"]=profile
        save.settings=values;save.revision+=1
        request.session["historical_life_notice"]="Historical accuracy profile updated. Existing facts and rolls were not changed."
    return RedirectResponse("/p/historical-life#profile",status_code=303)


@app.post("/api/historical-life/checklist/{task_key}")
def toggle_era_checklist(request: Request, task_key: str, completed: str = Form("true")):
    valid={key for key,_,_ in historical_life.ERA_TASKS}
    if task_key not in valid: raise HTTPException(404,"Unknown era checklist item.")
    wanted=completed.casefold() in {"1","true","yes","on"}
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        year=advanced.year_for(save,save.global_day) or save.start_year;decade=year-year%10
        item=session.scalar(select(Record).where(
            Record.save_id==save.id,Record.kind=="era_check",Record.deleted.is_(False),
            Record.data["task_key"].as_string()==task_key,Record.data["decade"].as_integer()==decade,
        ))
        if item:
            base=item.version;item.data={**(item.data or {}),"completed":wanted,"completed_global_day":save.global_day if wanted else None};item.version+=1
            domain.journal(session,item,"upsert",base)
        else:
            item=Record(save_id=save.id,kind="era_check",label=f"{decade}s — {task_key}",global_day=save.global_day,
                        data={"task_key":task_key,"decade":decade,"completed":wanted,"completed_global_day":save.global_day if wanted else None})
            session.add(item);session.flush();domain.journal(session,item,"upsert",0)
        save.revision+=1
    return RedirectResponse("/p/historical-life#era",status_code=303)


@app.post("/api/historical-life/correspondence")
def generate_historical_correspondence(request: Request, writing_kind: str = Form("letter"),
                                       author_sim_id: str = Form(""), recipient_sim_id: str = Form(""),
                                       subject: str = Form(""), notes: str = Form("")):
    if writing_kind not in {"letter","diary"}: raise HTTPException(400,"Choose letter or diary.")
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        rows=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.deleted.is_(False))))
        author=next((item for item in rows if item.id==author_sim_id and item.kind=="sim"),None)
        recipient=next((item for item in rows if item.id==recipient_sim_id and item.kind=="sim"),None)
        label,body=historical_life.compose_correspondence(writing_kind,author,recipient,subject,notes,save,rows)
        item=Record(save_id=save.id,kind="correspondence",label=label,global_day=save.global_day,
                    data={"writing_kind":writing_kind,"author_sim_id":author.id if author else None,
                          "author_name":author.label if author else "Household chronicler",
                          "recipient_sim_id":recipient.id if recipient else None,
                          "recipient_name":recipient.label if recipient else "","subject":subject.strip(),"body":body})
        session.add(item);session.flush();domain.journal(session,item,"upsert",0);save.revision+=1
        request.session["historical_life_notice"]=f"Created {writing_kind}: {label}."
    return RedirectResponse("/p/historical-life#letters",status_code=303)


@app.post("/records/{kind}/structured")
async def add_structured_record(request: Request, kind: str):
    allowed={"event","note","story_entry","roll_rule","death_causes","planner_rule","multiple_birth_rule","era_guidance","occult_rule","play_rotation","family_plan","campaign","service","estate_plan","economy_entry","education_plan","reputation_event","migration_plan","memorial","heirloom","correspondence","dowry_plan","guardianship","birth_privilege","coming_of_age","dispersal_plan","social_mobility","legal_case","absence","disability","mourning","wellbeing","medical_treatment","recovery_restriction","saved_view","newspaper"}
    if kind not in allowed: raise HTTPException(400,"Unsupported record type")
    form=await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx["save"]
        if not save: raise HTTPException(400,"Choose a save first")
        label=str(form.get("label") or form.get("title") or form.get("name") or kind.replace("_"," ").title()).strip()
        data=structured_form_data(form)
        if kind in {"event","campaign"}:
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
        elif kind=="event": domain.schedule_event_rolls(session,save)
        elif kind=="campaign": domain.schedule_campaign_rolls(session,save)
    return RedirectResponse(str(form.get("return_to") or request.headers.get("referer") or "/"),status_code=303)


@app.post("/records/{record_id}/edit")
async def edit_structured_record(request: Request, record_id: str):
    form=await request.form()
    with db() as session:
        record=session.get(Record,record_id)
        if not record: raise HTTPException(404)
        save=owned_save(request,session,record.save_id);base=record.version;data={**(record.data or {}),**structured_form_data(form)}
        if record.kind in {"event","campaign"}:
            if "die" in form: data["configured_die"]=str(form.get("die") or "").strip()
            if "bad_results" in form: data["configured_bad_results"]=str(form.get("bad_results") or "").strip()
        record.label=str(form.get("label") or form.get("title") or form.get("name") or record.label).strip()
        day=int_or_none(form.get("global_day"))
        if day is None: day=int_or_none(data.get("start_global_day")) or int_or_none(data.get("conception_global_day")) or record.global_day
        record.global_day=day;record.data=data;record.version+=1;domain.journal(session,record,"upsert",base);save.revision+=1
        if record.kind in {"roll_rule","occult_rule"}: domain.schedule_rolls(session,save)
        elif record.kind=="planner_rule": domain.schedule_marriage_rolls(session,save)
        elif record.kind=="event": domain.schedule_event_rolls(session,save)
        elif record.kind=="campaign": domain.schedule_campaign_rolls(session,save)
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


@app.post("/appearance")
async def update_appearance(request: Request):
    form = await request.form()
    with db() as session:
        ctx = context(request, session)
        save = ctx.get("save")
        if not save: raise HTTPException(400, "Open a save first.")
        theme = themes.from_form(form)
        values = dict(save.settings or {})
        values["visual_theme"] = theme
        save.settings = values
        save.revision += 1
        resolved = themes.resolve(theme)
        request.session["theme_notice"] = (
            f"{resolved['name']} is now applied to {save.name}."
            if theme["preset"] != "custom" else
            "Your custom theme is now applied to this save."
        )
    return RedirectResponse("/p/appearance", status_code=303)


@app.post("/appearance/reset")
def reset_appearance(request: Request):
    with db() as session:
        ctx = context(request, session)
        save = ctx.get("save")
        if not save: raise HTTPException(400, "Open a save first.")
        values = dict(save.settings or {})
        values.pop("visual_theme", None)
        save.settings = values
        save.revision += 1
        request.session["theme_notice"] = "The original Heirloom Gold theme has been restored."
    return RedirectResponse("/p/appearance", status_code=303)


@app.post("/settings")
async def update_settings(request: Request):
    form=await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx["save"]
        if not save: raise HTTPException(400)
        save.name=str(form.get("name") or save.name).strip();save.start_year=max(-9999,min(9999,int_or_none(form.get("start_year")) or save.start_year))
        save.days_per_year=max(1,min(365,int_or_none(form.get("days_per_year")) or save.days_per_year));save.pregnancy_days=max(1,min(100,int_or_none(form.get("pregnancy_days")) or save.pregnancy_days))
        settings_data=dict(save.settings or {})
        for key in ("challenge_location","default_species","succession_system","succession_root_id","sim_menu_order"):
            if key in form: settings_data[key]=str(form.get(key) or "").strip()
        for key in ("roll_tracking_start_day","try_for_baby_daily_limit","delivery_day_limit","elder_min_age_days","elder_max_age_days","marriage_min_age_days","inheritance_rule_cutoff_year","free_save_a_sims","full_moon_anchor_global_day","full_moon_interval_days","kinship_detection_generations"):
            if key in form and int_or_none(form.get(key)) is not None: settings_data[key]=int_or_none(form.get(key))
        settings_scope=str(form.get("settings_scope") or ("succession" if str(form.get("return_to") or "").startswith("/p/challenge") else "rules"))
        if settings_scope=="succession":
            settings_data["succession_require_legitimate"]="succession_require_legitimate" in form
        elif settings_scope=="rules" and "sim_menu_order" not in form:
            for key in ("maternal_rolls_enabled","automatic_death_causes","automatic_birth_circumstances"):
                settings_data[key]=key in form
        save.settings=settings_data;save.revision+=1;domain.schedule_rolls(session,save)
    return RedirectResponse(str(form.get("return_to") or "/p/rules"),status_code=303)


@app.post("/api/rule-packs")
async def update_rule_packs(request: Request):
    form=await request.form();allowed={pack["id"] for pack in advanced.RULE_PACKS};raw_selected=list(form.getlist("rule_pack"));selected=[value for value in raw_selected if value in allowed]
    legacy_core=core_rulesets.MORBID if "morbidgamer" in raw_selected else core_rulesets.SEVERALUDO if "severaludo" in raw_selected else ""
    chosen_core=str(form.get("core_ruleset") or legacy_core or core_rulesets.SEVERALUDO)
    if chosen_core not in core_rulesets.CORE_IDS: chosen_core=core_rulesets.SEVERALUDO
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        values=dict(save.settings or {});values["selected_rule_packs"]=(raw_selected if legacy_core else selected);values["core_ruleset_id"]=chosen_core;values["rule_pack_selection_version"]=3;save.settings=values
        save.revision+=1+core_rulesets.sync_rules(session,save)+avatar_rules.sync_pack(session,save,selected)+harry_potter_rules.sync_pack(session,save,selected)+game_of_thrones_rules.sync_pack(session,save,selected)
        save.revision+=domain.retire_inactive_core_rolls(session,save)
        domain.schedule_rolls(session,save)
        if avatar_rules.PACK_ID in selected: request.session["avatar_notice"]="Avatar Decades is installed. Recommended modules are on; optional and canon-only modules remain off until you enable them."
        if harry_potter_rules.PACK_ID in selected: request.session["hp_notice"]="Harry Potter Decades is installed. Recommended modules are on; optional modules and event tables remain off until you enable them."
        if game_of_thrones_rules.PACK_ID in selected: request.session["got_notice"]="Game of Thrones Decades is installed. Recommended modules are on; optional, supernatural, and timeline tables remain off until you enable them."
    return RedirectResponse("/",status_code=303)


@app.post("/api/avatar/modules/{code}")
async def update_avatar_module(code: str, request: Request):
    form=await request.form();enabled=str(form.get("enabled") or "").casefold() in {"1","true","on","yes"}
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        record=avatar_rules.set_module(session,save,code.upper(),enabled)
        if not record: raise HTTPException(404,"Avatar module not found. Enable the Avatar add-on first.")
        domain.journal(session,record,"upsert",record.version-1);save.revision+=1
        request.session["avatar_notice"]=f'{record.label} is now {"enabled" if enabled else "paused"}.'
    return RedirectResponse("/p/avatar#modules",status_code=303)


@app.post("/api/avatar/modules/{code}/edit")
async def edit_avatar_module(code: str, request: Request):
    form=await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        record=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="addon_rule",Record.deleted.is_(False),Record.data["code"].as_string()==code.upper()))
        if not record or (record.data or {}).get("rule_pack_id")!=avatar_rules.PACK_ID: raise HTTPException(404,"Avatar module not found.")
        data=dict(record.data or {});base=record.version
        data.update({"category":str(form.get("category") or "").strip(),"die":str(form.get("die") or "").strip(),"trigger":str(form.get("trigger") or "").strip(),"rule_text":str(form.get("rule_text") or "").strip()})
        data["result_rules"]=data["rule_text"];record.data=data;record.version+=1;domain.journal(session,record,"upsert",base);save.revision+=1
        request.session["avatar_notice"]=f"Saved edits to {record.label}."
    return RedirectResponse("/p/avatar#modules",status_code=303)


@app.post("/api/avatar/settings")
async def update_avatar_settings(request: Request):
    form=await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        values=dict(save.settings or {});values["avatar_canon_timeline_mode"]=str(form.get("canon_timeline_mode") or "").casefold() in {"1","true","on","yes"};save.settings=values;save.revision+=1
        request.session["avatar_notice"]="Avatar timeline preference saved. Canon mode fixes the supplied anchor years; alternate-history mode treats them as reference."
    return RedirectResponse("/p/avatar#timeline",status_code=303)


@app.post("/api/avatar/rolls")
async def create_avatar_roll(request: Request):
    form=await request.form();rule_id=str(form.get("rule_id") or "");sim_id=str(form.get("sim_id") or "");due=int_or_none(form.get("global_day"))
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save or due is None or due<1: raise HTTPException(400,"Choose a valid Sim, rule, and Global Day.")
        rule=session.get(Record,rule_id);sim=session.get(Record,sim_id)
        if not rule or rule.save_id!=save.id or rule.kind!="addon_rule" or not bool((rule.data or {}).get("active")): raise HTTPException(404,"Active Avatar rule not found.")
        if not sim or sim.save_id!=save.id or sim.kind!="sim" or sim.deleted: raise HTTPException(404,"Sim not found.")
        try: roll,created=create_rule_roll_record(session,save,rule,sim,due,context_note=str(form.get("notes") or ""))
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
        if created: save.revision+=1
        request.session["avatar_notice"]=f'{"Added" if created else "Already scheduled"}: {roll.label} on GD {due}.'
    return RedirectResponse("/p/avatar#roll-workbench",status_code=303)


@app.post("/api/avatar/sims/{sim_id}")
async def update_avatar_sim(sim_id: str, request: Request):
    form=await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx.get("save");sim=session.get(Record,sim_id)
        if not save or not sim or sim.save_id!=save.id or sim.kind!="sim" or sim.deleted: raise HTTPException(404,"Sim not found.")
        data=dict(sim.data or {});base=sim.version
        techniques=[part.strip() for part in re.split(r"[;|,]+",str(form.get("advanced_techniques") or "")) if part.strip()]
        data.update({
            "avatar_birth_nation":str(form.get("birth_nation") or "").strip(),"avatar_cultural_nation":str(form.get("cultural_nation") or "").strip(),
            "avatar_elemental_ancestry":str(form.get("elemental_ancestry") or "").strip(),"avatar_bender_status":str(form.get("bender_status") or "").strip(),
            "avatar_bending_element":str(form.get("bending_element") or "").strip(),"avatar_natural_strength":str(form.get("natural_strength") or "").strip(),
            "avatar_mastery_rank":str(form.get("mastery_rank") or "").strip(),"avatar_status":str(form.get("avatar_status") or "").strip(),
            "avatar_spiritually_gifted":str(form.get("spiritually_gifted") or "").casefold() in {"1","true","on","yes"},"avatar_advanced_techniques":techniques,
        })
        sim.data=data;sim.version+=1;domain.journal(session,sim,"upsert",base);save.revision+=1;request.session["avatar_notice"]=f"Saved Avatar fields for {sim.label}."
    return RedirectResponse("/p/avatar#people",status_code=303)


@app.post("/api/harry-potter/modules/{code}")
async def update_hp_module(code: str, request: Request):
    form=await request.form();enabled=str(form.get("enabled") or "").casefold() in {"1","true","on","yes"}
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        record=harry_potter_rules.set_module(session,save,code.upper(),enabled)
        if not record: raise HTTPException(404,"Harry Potter module not found. Enable the add-on first.")
        domain.journal(session,record,"upsert",record.version-1);save.revision+=1;request.session["hp_notice"]=f'{record.label} is now {"enabled" if enabled else "paused"}.'
    return RedirectResponse("/p/harry-potter#modules",status_code=303)


@app.post("/api/harry-potter/modules/{code}/edit")
async def edit_hp_module(code: str, request: Request):
    form=await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        record=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="addon_rule",Record.deleted.is_(False),Record.data["code"].as_string()==code.upper()))
        if not record or (record.data or {}).get("rule_pack_id")!=harry_potter_rules.PACK_ID: raise HTTPException(404,"Harry Potter module not found.")
        data=dict(record.data or {});base=record.version;data.update({"category":str(form.get("category") or "").strip(),"die":str(form.get("die") or "").strip(),"trigger":str(form.get("trigger") or "").strip(),"rule_text":str(form.get("rule_text") or "").strip()});data["result_rules"]=data["rule_text"];record.data=data;record.version+=1;domain.journal(session,record,"upsert",base);save.revision+=1;request.session["hp_notice"]=f"Saved edits to {record.label}."
    return RedirectResponse("/p/harry-potter#modules",status_code=303)


@app.post("/api/harry-potter/settings")
async def update_hp_settings(request: Request):
    form=await request.form();allowed={mode[0] for mode in harry_potter_rules.TIMELINE_MODES};mode=str(form.get("timeline_mode") or "alternate")
    if mode not in allowed: raise HTTPException(400,"Choose a valid timeline mode.")
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        values=dict(save.settings or {});values["harry_potter_timeline_mode"]=mode;save.settings=values;save.revision+=1;request.session["hp_notice"]="Harry Potter timeline mode saved."
    return RedirectResponse("/p/harry-potter#timeline",status_code=303)


@app.post("/api/harry-potter/rolls")
async def create_hp_roll(request: Request):
    form=await request.form();rule_id=str(form.get("rule_id") or "");sim_id=str(form.get("sim_id") or "");due=int_or_none(form.get("global_day"))
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save or due is None or due<1: raise HTTPException(400,"Choose a valid Sim, rule, and Global Day.")
        rule=session.get(Record,rule_id);sim=session.get(Record,sim_id)
        if not rule or rule.save_id!=save.id or rule.kind!="addon_rule" or (rule.data or {}).get("rule_pack_id")!=harry_potter_rules.PACK_ID or not bool((rule.data or {}).get("active")): raise HTTPException(404,"Active Harry Potter rule not found.")
        if not sim or sim.save_id!=save.id or sim.kind!="sim" or sim.deleted: raise HTTPException(404,"Sim not found.")
        try: roll,created=create_rule_roll_record(session,save,rule,sim,due,context_note=str(form.get("notes") or ""))
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
        if created: save.revision+=1
        request.session["hp_notice"]=f'{"Added" if created else "Already scheduled"}: {roll.label} on GD {due}.'
    return RedirectResponse("/p/harry-potter#roll-workbench",status_code=303)


@app.post("/api/harry-potter/sims/{sim_id}")
async def update_hp_sim(sim_id: str, request: Request):
    form=await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx.get("save");sim=session.get(Record,sim_id)
        if not save or not sim or sim.save_id!=save.id or sim.kind!="sim" or sim.deleted: raise HTTPException(404,"Sim not found.")
        data=dict(sim.data or {});base=sim.version;data.update({"hp_magical_ability":str(form.get("magical_ability") or "").strip(),"hp_blood_status":str(form.get("blood_status") or "").strip(),"hp_hidden_squib":str(form.get("hidden_squib") or "").casefold() in {"1","true","on","yes"},"hp_public_magical_status":str(form.get("public_magical_status") or "").strip(),"hp_magical_school":str(form.get("magical_school") or "").strip(),"hp_hogwarts_house":str(form.get("hogwarts_house") or "").strip(),"hp_obscurial_status":str(form.get("obscurial_status") or "").strip(),"hp_quidditch_status":str(form.get("quidditch_status") or "").strip(),"hp_war_allegiance":str(form.get("war_allegiance") or "").strip(),"hp_death_eater_status":str(form.get("death_eater_status") or "").strip(),"hp_resistance_status":str(form.get("resistance_status") or "").strip(),"hp_prisoner_missing_status":str(form.get("prisoner_missing_status") or "").strip(),"hp_secrecy_violation_count":max(0,int_or_none(form.get("secrecy_violation_count")) or 0)});sim.data=data;sim.version+=1;domain.journal(session,sim,"upsert",base);save.revision+=1;request.session["hp_notice"]=f"Saved Wizarding fields for {sim.label}."
    return RedirectResponse("/p/harry-potter#people",status_code=303)


@app.post("/api/harry-potter/households/{household_id}")
async def update_hp_household(household_id: str, request: Request):
    form=await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx.get("save");house=session.get(Record,household_id)
        if not save or not house or house.save_id!=save.id or house.kind!="household" or house.deleted: raise HTTPException(404,"Household not found.")
        data=dict(house.data or {});base=house.version;data.update({"hp_blood_purity_level":str(form.get("blood_purity_level") or "").strip(),"hp_inheritance_custom":str(form.get("inheritance_custom") or "").strip(),"hp_family_exception":str(form.get("family_exception") or "").strip(),"hp_living_grandchildren":max(0,int_or_none(form.get("living_grandchildren")) or 0),"hp_fashion_reference_decade":int_or_none(form.get("fashion_reference_decade")),"hp_magic_exposure_level":str(form.get("magic_exposure_level") or "").strip(),"hp_repeated_public_exposure":str(form.get("repeated_public_exposure") or "").casefold() in {"1","true","on","yes"},"hp_political_reputation":str(form.get("political_reputation") or "").strip(),"hp_ministry_connections":str(form.get("ministry_connections") or "").strip(),"hp_hidden_dark_objects":str(form.get("hidden_dark_objects") or "").strip(),"hp_known_war_crimes":str(form.get("known_war_crimes") or "").strip()});house.data=data;house.version+=1;domain.journal(session,house,"upsert",base);save.revision+=1;request.session["hp_notice"]=f"Saved Wizarding fields for {house.label}."
    return RedirectResponse("/p/harry-potter#families",status_code=303)


@app.post("/api/game-of-thrones/modules/{code}")
async def update_got_module(code: str, request: Request):
    form=await request.form();enabled=str(form.get("enabled") or "").casefold() in {"1","true","on","yes"}
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save:raise HTTPException(400,"Open a save first.")
        record=game_of_thrones_rules.set_module(session,save,code.upper(),enabled)
        if not record:raise HTTPException(404,"Game of Thrones module not found. Enable the add-on first.")
        domain.journal(session,record,"upsert",record.version-1);save.revision+=1;request.session["got_notice"]=f'{record.label} is now {"enabled" if enabled else "paused"}.'
    return RedirectResponse("/p/game-of-thrones#modules",status_code=303)


@app.post("/api/game-of-thrones/modules/{code}/edit")
async def edit_got_module(code: str, request: Request):
    form=await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save:raise HTTPException(400,"Open a save first.")
        record=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="addon_rule",Record.deleted.is_(False),Record.data["code"].as_string()==code.upper()))
        if not record or (record.data or {}).get("rule_pack_id")!=game_of_thrones_rules.PACK_ID:raise HTTPException(404,"Game of Thrones module not found.")
        data=dict(record.data or {});base=record.version;data.update({"category":str(form.get("category") or "").strip(),"die":str(form.get("die") or "").strip(),"trigger":str(form.get("trigger") or "").strip(),"rule_text":str(form.get("rule_text") or "").strip()});data["result_rules"]=data["rule_text"];record.data=data;record.version+=1;domain.journal(session,record,"upsert",base);save.revision+=1;request.session["got_notice"]=f"Saved edits to {record.label}."
    return RedirectResponse("/p/game-of-thrones#modules",status_code=303)


@app.post("/api/game-of-thrones/settings")
async def update_got_settings(request: Request):
    form=await request.form();allowed={mode[0] for mode in game_of_thrones_rules.TIMELINE_MODES};mode=str(form.get("timeline_mode") or "original")
    if mode not in allowed:raise HTTPException(400,"Choose a valid timeline mode.")
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save:raise HTTPException(400,"Open a save first.")
        values=dict(save.settings or {});values.update({"game_of_thrones_timeline_mode":mode,"got_monarch":str(form.get("monarch") or "").strip(),"got_royal_house":str(form.get("royal_house") or "").strip(),"got_current_season":str(form.get("current_season") or "").strip(),"got_season_length":max(0,int_or_none(form.get("season_length")) or 0),"got_war_status":str(form.get("war_status") or "").strip(),"got_active_claimants":str(form.get("active_claimants") or "").strip(),"got_long_night_stage":str(form.get("long_night_stage") or "").strip(),"got_dragons_present":str(form.get("dragons_present") or "").casefold() in {"1","true","on","yes"},"got_others_active":str(form.get("others_active") or "").casefold() in {"1","true","on","yes"}});save.settings=values;save.revision+=1;request.session["got_notice"]="Realm and timeline settings saved."
    return RedirectResponse("/p/game-of-thrones#realm",status_code=303)


@app.post("/api/game-of-thrones/rolls")
async def create_got_roll(request:Request):
    form=await request.form();rule_id=str(form.get("rule_id") or "");sim_id=str(form.get("sim_id") or "");due=int_or_none(form.get("global_day"))
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save or due is None or due<1:raise HTTPException(400,"Choose a valid Sim, rule, and Global Day.")
        rule=session.get(Record,rule_id);sim=session.get(Record,sim_id)
        if not rule or rule.save_id!=save.id or rule.kind!="addon_rule" or (rule.data or {}).get("rule_pack_id")!=game_of_thrones_rules.PACK_ID or not bool((rule.data or {}).get("active")):raise HTTPException(404,"Active Game of Thrones rule not found.")
        if not sim or sim.save_id!=save.id or sim.kind!="sim" or sim.deleted:raise HTTPException(404,"Sim not found.")
        try:roll,created=create_rule_roll_record(session,save,rule,sim,due,context_note=str(form.get("notes") or ""))
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        if created:save.revision+=1
        request.session["got_notice"]=f'{"Added" if created else "Already scheduled"}: {roll.label} on GD {due}.'
    return RedirectResponse("/p/game-of-thrones#roll-workbench",status_code=303)


@app.post("/api/game-of-thrones/sims/{sim_id}")
async def update_got_sim(sim_id:str,request:Request):
    form=await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx.get("save");sim=session.get(Record,sim_id)
        if not save or not sim or sim.save_id!=save.id or sim.kind!="sim" or sim.deleted:raise HTTPException(404,"Sim not found.")
        data=dict(sim.data or {});base=sim.version;data.update({"got_house":str(form.get("house") or "").strip(),"got_birth_house":str(form.get("birth_house") or "").strip(),"got_legitimacy":str(form.get("legitimacy") or "").strip(),"got_recognized_bastard":str(form.get("recognized_bastard") or "").casefold() in {"1","true","on","yes"},"got_bastard_surname":str(form.get("bastard_surname") or "").strip(),"got_claim":str(form.get("claim") or "").strip(),"got_martial_ability":str(form.get("martial_ability") or "").strip(),"got_knight_status":str(form.get("knight_status") or "").strip(),"got_court_office":str(form.get("court_office") or "").strip(),"got_vow_order":str(form.get("vow_order") or "").strip(),"got_prisoner_status":str(form.get("prisoner_status") or "").strip(),"got_missing_status":str(form.get("missing_status") or "").strip(),"got_regent_status":str(form.get("regent_status") or "").strip(),"got_dragonrider":str(form.get("dragonrider") or "").casefold() in {"1","true","on","yes"},"got_greensight":str(form.get("greensight") or "").casefold() in {"1","true","on","yes"},"got_skinchanger":str(form.get("skinchanger") or "").casefold() in {"1","true","on","yes"},"got_reputation_conditions":str(form.get("reputation_conditions") or "").strip()});sim.data=data;sim.version+=1;domain.journal(session,sim,"upsert",base);save.revision+=1;request.session["got_notice"]=f"Saved Westerosi fields for {sim.label}."
    return RedirectResponse("/p/game-of-thrones#people",status_code=303)


@app.post("/api/game-of-thrones/households/{household_id}")
async def update_got_household(household_id:str,request:Request):
    form=await request.form()
    with db() as session:
        ctx=context(request,session);save=ctx.get("save");house=session.get(Record,household_id)
        if not save or not house or house.save_id!=save.id or house.kind!="household" or house.deleted:raise HTTPException(404,"Household not found.")
        data=dict(house.data or {});base=house.version;data.update({"got_house_name":str(form.get("house_name") or "").strip(),"got_region":str(form.get("region") or "").strip(),"got_rank":str(form.get("rank") or "").strip(),"got_wealth":str(form.get("wealth") or "").strip(),"got_ancestral_seat":str(form.get("ancestral_seat") or "").strip(),"got_house_words":str(form.get("house_words") or "").strip(),"got_sigil":str(form.get("sigil") or "").strip(),"got_religion":str(form.get("religion") or "").strip(),"got_succession_custom":str(form.get("succession_custom") or "").strip(),"got_current_ruler":str(form.get("current_ruler") or "").strip(),"got_current_heir":str(form.get("current_heir") or "").strip(),"got_cadet_branch":str(form.get("cadet_branch") or "").strip(),"got_alliances":str(form.get("alliances") or "").strip(),"got_feuds":str(form.get("feuds") or "").strip(),"got_reputation":str(form.get("reputation") or "").strip(),"got_house_objective":str(form.get("house_objective") or "").strip(),"got_dragon_ownership":str(form.get("dragon_ownership") or "").strip(),"got_valyrian_steel":str(form.get("valyrian_steel") or "").strip()});house.data=data;house.version+=1;domain.journal(session,house,"upsert",base);save.revision+=1;request.session["got_notice"]=f"Saved House fields for {house.label}."
    return RedirectResponse("/p/game-of-thrones#houses",status_code=303)


@app.post("/api/migrations")
async def add_migration(request: Request):
    form=await request.form();sim_id=str(form.get("sim_id") or "");move_day=int_or_none(form.get("move_global_day"));to_country=str(form.get("to_country") or "").strip()
    if move_day is None or move_day<1 or not to_country: raise HTTPException(400,"Choose a Sim, destination country, and valid move day.")
    with db() as session:
        ctx=context(request,session);save=ctx.get("save")
        if not save: raise HTTPException(400,"Open a save first.")
        sim=session.get(Record,sim_id)
        if not sim or sim.save_id!=save.id or sim.kind!="sim" or sim.deleted: raise HTTPException(404)
        existing=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="migration",Record.deleted.is_(False))))
        from_country=advanced.location_at(sim,max(1,move_day-1),existing)
        sim_data=dict(sim.data or {})
        if not sim_data.get("birth_country"): sim_data["birth_country"]=advanced.birth_country(sim)
        payload={"sim_id":sim.id,"sim_name":sim.label,"move_global_day":move_day,"from_country":from_country,"to_country":to_country,"to_location":str(form.get("to_location") or "").strip(),"reason":str(form.get("reason") or "Migration").strip(),"notes":str(form.get("notes") or "").strip()}
        move=Record(save_id=save.id,kind="migration",label=f"{sim.label}: {from_country} → {to_country}",global_day=move_day,data=payload);session.add(move);session.flush();domain.journal(session,move,"upsert",0)
        if move_day<=save.global_day:
            base=sim.version;sim_data.update({"country":to_country,"current_country":to_country,"current_location":payload["to_location"] or to_country});sim.data=sim_data;sim.version+=1;domain.journal(session,sim,"upsert",base)
        elif sim.data!=sim_data:
            base=sim.version;sim.data=sim_data;sim.version+=1;domain.journal(session,sim,"upsert",base)
        save.revision+=2;request.session["world_notice"]=f"Recorded {sim.label}'s move from {from_country} to {to_country} on Global Day {move_day}."
    return RedirectResponse("/p/world",status_code=303)


@app.post("/api/migrations/{migration_id}/delete")
def delete_migration(request: Request, migration_id: str):
    with db() as session:
        move=session.get(Record,migration_id)
        if not move or move.kind!="migration" or move.deleted: raise HTTPException(404)
        save=owned_save(request,session,move.save_id);sim=session.get(Record,str((move.data or {}).get("sim_id") or ""))
        base=move.version;move.deleted=True;move.version+=1;domain.journal(session,move,"delete",base);save.revision+=1
        if sim and sim.save_id==save.id and not sim.deleted:
            remaining=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="migration",Record.deleted.is_(False),Record.id!=move.id)))
            country=advanced.location_at(sim,save.global_day,remaining);data=dict(sim.data or {});sim_base=sim.version;data.update({"country":country,"current_country":country});sim.data=data;sim.version+=1;domain.journal(session,sim,"upsert",sim_base);save.revision+=1
        request.session["world_notice"]="The migration was removed and the Sim's current country was recalculated from the remaining route."
    return RedirectResponse("/p/world",status_code=303)


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
        destination = "/p/occult-rules"
    return RedirectResponse(destination,status_code=303)


@app.post("/api/automation/toggle")
def toggle_master_automation(request: Request, enabled: str = Form(""), return_to: str = Form("")):
    """Pause or resume every save-level automatic mutation from one control."""
    turn_on = enabled.casefold() in {"1", "true", "on", "yes"}
    with db() as session:
        ctx = context(request, session); save = ctx["save"]
        if not save:
            raise HTTPException(400, "Open a save first.")
        values = dict(save.settings or {})
        values["automation_enabled"] = turn_on
        values["automation_toggled_global_day"] = save.global_day
        link = session.scalar(select(ClockLink).where(ClockLink.save_id == save.id))
        if link and link.last_game_day is not None:
            link.game_anchor_day = int(link.last_game_day)
            link.tracker_anchor_day = int(save.global_day)
            values["clock_game_day_high_watermark"] = max(
                int(values.get("clock_game_day_high_watermark") or link.last_game_day),
                int(link.last_game_day),
            )
        save.settings = values
        save.revision += 1
        created = domain.schedule_rolls(session, save) if turn_on else 0
        request.session["master_automation_notice"] = (
            f"Automation is on. Clock Sync, detections, scheduled obligations, automatic outcomes and storyline updates can run again. {created} missing roll{'s were' if created != 1 else ' was'} added."
            if turn_on else
            "Automation is paused for this save. Clock reports may still show the live connection, but Global Day, detections, scheduled obligations and automatic outcomes will not change until you resume. Existing records were kept."
        )
    destination = return_to.strip()
    if not destination.startswith("/") or destination.startswith("//"):
        destination = "/p/today"
    return RedirectResponse(destination, status_code=303)


@app.post("/api/rolls/refresh")
def refresh_scheduled_rolls(request: Request, return_to: str = Form("/p/today?task=rolls")):
    with db() as session:
        ctx = context(request, session); save = ctx["save"]
        if not save:
            raise HTTPException(400, "Open a save first.")
        repaired = domain.refresh_pending_rolls(session, save)
        save.revision += repaired["updated"]
        created = domain.schedule_rolls(session, save)
        categories = ", ".join(
            f"{count} {name}" for name, count in repaired.items()
            if name != "updated" and count
        )
        if repaired["updated"] or created:
            detail = f" Refreshed: {categories}." if categories else ""
            request.session["roll_refresh_notice"] = (
                f"Roll refresh complete: corrected {repaired['updated']} unfinished roll"
                f"{'s' if repaired['updated'] != 1 else ''} and added {created} missing roll"
                f"{'s' if created != 1 else ''}.{detail} Completed history was not changed."
            )
        else:
            request.session["roll_refresh_notice"] = "All pending rolls already match the current rule tables. Completed history was not changed."
    destination = return_to.strip()
    if not destination.startswith("/") or destination.startswith("//"):
        destination = "/p/today?task=rolls"
    return RedirectResponse(destination, status_code=303)


@app.post("/rolls/manual")
async def create_manual_roll(request: Request):
    form = await request.form()
    with db() as session:
        ctx = context(request, session); save = ctx["save"]
        if not save:
            raise HTTPException(400, "Open a save first.")
        roll_type = str(form.get("roll_type") or "Custom roll").strip()[:160] or "Custom roll"
        due = max(1, min(20000, int_or_none(form.get("global_day")) or save.global_day))
        sim_id = str(form.get("sim_id") or "").strip()
        sim = session.get(Record, sim_id) if sim_id else None
        if sim_id and (not sim or sim.save_id != save.id or sim.kind != "sim" or sim.deleted):
            raise HTTPException(400, "Choose a valid Sim from this save.")
        lethal = str(form.get("failure_is_lethal") or "").casefold() in {"1", "true", "on", "yes"}
        data = {
            "roll_type":roll_type, "sim_id":sim.id if sim else None,
            "sim_name":sim.label if sim else "", "die":str(form.get("die") or "d20").strip()[:30] or "d20",
            "bad_results":str(form.get("bad_results") or "").strip()[:500],
            "result_rules":str(form.get("result_rules") or "").strip()[:1000],
            "notes":str(form.get("notes") or "").strip()[:4000],
            "failure_is_lethal":lethal, "nonlethal":not lethal,
            "manual_roll":True, "completed":False, "due_global_day":due,
            "created_global_day":save.global_day, "source":"manual",
        }
        label = f"{sim.label} — {roll_type}" if sim else roll_type
        roll = Record(save_id=save.id, kind="roll", label=label, global_day=due, data=data)
        session.add(roll); session.flush()
        roll.data = {**roll.data, "source":f"manual:{roll.id}"}
        domain.journal(session, roll, "upsert", 0)
        save.revision += 1
        request.session["manual_roll_notice"] = f"Added {label} for Global Day {due}."
    return RedirectResponse("/p/rolls?record_status=pending", status_code=303)


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
    return RedirectResponse("/p/occult-rules",status_code=303)


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
        save=owned_save(request,session,record.save_id);set_today_undo(request,f"Updated {record.label}",[record]);keeps_maternal=domain.pregnancy_keeps_maternal_roll(status) and (record.data or {}).get("maternal_rolls_required",True)
        if keeps_maternal: domain.schedule_rolls(session,save)
        base=record.version;data=dict(record.data or {});data.update({"status":status,"babies_delivered":max(0,babies_delivered),"actual_delivery_global_day":save.global_day,"delivery_global_day":save.global_day,"outcome":outcome or status,"complication":complication or None});record.data=data;record.version+=1;domain.journal(session,record,"upsert",base);save.revision+=1
        if keeps_maternal:
            save.revision+=domain.preserve_delivery_maternal_rolls(session,save,record,save.global_day)
        elif domain.pregnancy_retires_maternal_roll(status):
            save.revision+=domain.retire_pregnancy_rolls(session,save,record.id,f"Pregnancy resolved as {status}")
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
        grief_reviews=life_records.schedule_grief_candidates(session,save,sim,death_day)
        save.revision+=len(grief_reviews)
        if grief_reviews and request.session.get("today_undo"):
            request.session["today_undo"].setdefault("delete_ids",[])
            request.session["today_undo"]["delete_ids"].extend(item.id for item in grief_reviews)
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
            if bool((roll.data or {}).get("pregnancy_count_roll")):
                related.extend(session.scalars(select(Record).where(
                    Record.save_id==save.id,Record.kind=="family_plan",Record.deleted.is_(False),
                    Record.data["source_pregnancy_roll_id"].as_string()==roll.id,
                )))
        set_today_undo(request,f"Completed {roll.label}",related);result=domain.complete_roll(session,save,roll,actual,outcome)
        delete_ids=[]
        if result.get("death_created"): delete_ids.append(result["death"]["id"])
        if result.get("family_plan_created") and result.get("family_plan"): delete_ids.append(result["family_plan"]["id"])
        if delete_ids: request.session["today_undo"]["delete_ids"]=delete_ids
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
        conditional_followups=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.deleted.is_(False),Record.data["origin_roll_id"].as_string()==roll.id,Record.data["automatic_followup"].as_boolean().is_(True))))
        related.extend(conditional_followups)
        if sim: related.append(sim)
        set_today_undo(request,f"Reopened {roll.label}",related)
        for death in auto_deaths:
            base=death.version;data=dict(death.data or {});prior=int_or_none(data.get("rescheduled_from_global_day"));prior_cause=data.get("rescheduled_from_cause")
            if prior is None:
                death.deleted=True;death.data={**data,"correction_note":"Automatic death withdrawn when its roll was reopened"};death.version+=1;domain.journal(session,death,"delete",base)
            else:
                for key in ("source_roll_id","rescheduled_from_global_day","rescheduled_from_cause","historical_death_date","death_game_hour","death_game_minute","death_time"): data.pop(key,None)
                data.update({"cause":prior_cause or "Player choice","historical_death_date_range":calendar_utils.date_range_label(prior,save.start_year,save.days_per_year),"death_date_precision":"challenge-day-only"});death.global_day=prior;death.data=data;death.version+=1;domain.journal(session,death,"upsert",base)
        for followup in conditional_followups:
            followup_base=followup.version;followup.deleted=True;followup.data={**(followup.data or {}),"retired_reason":"Origin roll reopened","retired_global_day":save.global_day};followup.version+=1;domain.journal(session,followup,"delete",followup_base)
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
        for key in ("actual","outcome","completed","completed_global_day","pregnancy_count","nonlethal","event_followup_roll_id","event_followup_processed"): data.pop(key,None)
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


@app.post("/api/clock/trust-next-save")
def trust_next_clock_save(request: Request):
    """Clear only the Sims save-slot binding after explicit owner confirmation."""
    with db() as session:
        ctx = context(request, session)
        save = ctx.get("save")
        if not save:
            raise HTTPException(400, "Open a tracker save first.")
        state = session.scalar(select(Record).where(
            Record.save_id == save.id, Record.kind == "clock_protocol_state", Record.deleted.is_(False),
        ).limit(1))
        if state:
            base = state.version
            data = dict(state.data or {})
            data.update(save_identity=None, save_slot_id=None, save_slot_name=None,
                        last_report_sequence=0, last_report_checksum="", last_report_id=None,
                        binding_cleared_at=datetime.now(timezone.utc).isoformat())
            state.data = data
            state.version += 1
            domain.journal(session, state, "upsert", base)
        request.session["clock_notice"] = "The old game-save binding was cleared. Enter Live Mode and let time move; the next valid report will pair this tracker save with the currently loaded Sims save."
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
            comparison = save_scanner.compare_scan(session, save, scan)
            scan["relevant_households"], scan["relevant_sims"] = households, comparison["rows"]
            scan["comparison"] = comparison
            _SAVE_SCAN_CACHE[save.id] = scan
            request.session["game_save_notice"] = (
                f"Read {len(sims)} Sims from {len(households)} player households: "
                f"{comparison['counts']['changed']} changed, {comparison['counts']['new']} new, "
                f"and {comparison['counts']['missing']} linked tracker Sims absent from this played-population snapshot. "
                f"{sum(bool(item.get('has_embedded_portrait')) for item in comparison['rows'])} individual portrait(s) are available. "
                "Nothing has been imported yet."
            )
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
        backup_service.create_snapshot(session, save, "before read-only game-save reconciliation", force=True)
        result = save_scanner.reconcile_scan(session, save, scan, selected, str(form.get("advance_clock") or "").casefold() in {"1","true","on","yes"})
        request.session["game_save_notice"] = f"Save scan applied: {result['updated']} linked Sim(s) refreshed, {result['linked']} imported match(es) linked, {result['portrait_updates']} portrait(s) added or refreshed, {result['candidates']} review item(s) created, tracker advanced {result['advanced']} day(s)."
    return RedirectResponse("/p/automation", status_code=303)


@app.post("/api/game-save/portraits")
async def import_game_save_portraits(request: Request):
    """Import every safely matched embedded portrait without changing Sim data."""
    if not settings.local_mode:
        raise HTTPException(400, "Direct save scanning is available in the desktop edition.")
    form = await request.form()
    available = {item.path.name: item.path for item in save_scanner.discover_saves()}
    file_name = str(form.get("file_name") or "")
    if not file_name and available:
        file_name = next(iter(available))
    if file_name not in available:
        raise HTTPException(400, "Choose one of the detected primary Sims 4 saves.")
    with db() as session:
        ctx = context(request, session); save = ctx.get("save")
        if not save:
            raise HTTPException(400, "Open a tracker save first.")
        try:
            scan = save_scanner.inspect_save(available[file_name])
        except save_scanner.SaveScanError as exc:
            request.session["game_save_notice"] = str(exc)
        else:
            result = save_scanner.import_portraits(session, save, scan)
            request.session["game_save_notice"] = (
                f"Portrait scan finished: {result['updated']} portrait(s) added or refreshed, "
                f"{result['unchanged']} already current, {result['protected']} manual upload(s) preserved, "
                f"and {result['unmatched']} embedded portrait(s) could not be matched. "
                "No clock or Sim details were changed."
            )
    return RedirectResponse("/p/clock#save-scan", status_code=303)


@app.post("/sims/{sim_id}/scan-portrait")
def scan_sim_portrait(request: Request, sim_id: str):
    """Find one Sim's portrait in the Clock-Sync-linked or newest save file."""
    if not settings.local_mode:
        raise HTTPException(400, "Direct save scanning is available in the desktop edition.")
    files = save_scanner.discover_saves()
    with db() as session:
        sim = session.get(Record, sim_id)
        if not sim or sim.kind != "sim" or sim.deleted:
            raise HTTPException(404)
        save = owned_save(request, session, sim.save_id)
        if not files:
            request.session["portrait_notice"] = "No primary Sims 4 save files were found. Save the game, then try again."
            return RedirectResponse(f"/sims/{sim.id}#portraits", status_code=303)
        protocol=session.scalar(select(Record).where(
            Record.save_id==save.id,Record.kind=="clock_protocol_state",Record.deleted.is_(False),
        ).limit(1))
        bound_slot=str((protocol.data or {}).get("save_slot_id") or "") if protocol else ""
        def slot_key(value) -> str:
            return str(value or "").casefold().removesuffix(".save").removeprefix("slot_")
        selected_file=next((item for item in files if bound_slot and slot_key(item.path.name)==slot_key(bound_slot)),files[0])
        try:
            scan = save_scanner.inspect_save(selected_file.path)
        except save_scanner.SaveScanError as exc:
            request.session["portrait_notice"] = str(exc)
        else:
            result = save_scanner.import_portraits(session, save, scan, target_record_id=sim.id)
            if result["updated"]:
                request.session["portrait_notice"] = f"Imported {sim.label}’s current life-stage portrait from {selected_file.path.name}."
            elif result["protected"]:
                request.session["portrait_notice"] = "A manual portrait already fills this life stage, so it was preserved."
            elif result["unchanged"]:
                request.session["portrait_notice"] = "The portrait in the newest game save is already current."
            elif result["identity_matches"]:
                request.session["portrait_notice"] = (
                    "This Sim was found, but the newest save does not contain an embedded portrait. "
                    "Load or edit the Sim in game, save again, then retry."
                )
            else:
                request.session["portrait_notice"] = (
                    "This tracker Sim could not be matched in the newest game save. "
                    "Run Clock Sync with that Sim loaded, or make sure the tracker name matches the game name."
                )
    return RedirectResponse(f"/sims/{sim_id}#portraits", status_code=303)


@app.post("/api/tray/portraits")
def import_tray_portraits(request: Request):
    """Import exact-name portraits from the desktop user's Sims 4 Tray library."""
    if not settings.local_mode:
        raise HTTPException(400, "Tray scanning is available in the desktop edition.")
    with db() as session:
        ctx = context(request, session); save = ctx.get("save")
        if not save:
            raise HTTPException(400, "Open a tracker save first.")
        result = tray_scanner.import_portraits(session, save)
        request.session["game_save_notice"] = (
            f"Tray scan finished: {result['updated']} portrait(s) added or refreshed, "
            f"{result['unchanged']} already current, {result['protected']} manual portrait(s) preserved, "
            f"{result['ambiguous']} duplicate-name match(es) skipped, and {result['invalid']} invalid image(s) skipped. "
            f"The Tray library contained {result['available']} linked individual portrait file(s)."
        )
    return RedirectResponse("/p/clock#save-scan", status_code=303)


@app.post("/sims/{sim_id}/scan-tray-portrait")
def scan_sim_tray_portrait(request: Request, sim_id: str):
    if not settings.local_mode:
        raise HTTPException(400, "Tray scanning is available in the desktop edition.")
    with db() as session:
        sim = session.get(Record, sim_id)
        if not sim or sim.kind != "sim" or sim.deleted:
            raise HTTPException(404)
        save = owned_save(request, session, sim.save_id)
        result = tray_scanner.import_portraits(session, save, target_record_id=sim.id)
        if result["updated"]:
            request.session["portrait_notice"] = f"Imported {sim.label}’s current life-stage portrait from the Sims 4 Tray library."
        elif result["protected"]:
            request.session["portrait_notice"] = "A manual portrait already fills this life stage, so it was preserved."
        elif result["unchanged"]:
            request.session["portrait_notice"] = "The newest matching Tray portrait is already current."
        elif result["ambiguous"]:
            request.session["portrait_notice"] = "The Tray match was ambiguous, so no portrait was assigned automatically."
        elif not result["available"]:
            request.session["portrait_notice"] = "No individual Sim portraits were found in the Sims 4 Tray folder. Save this household to My Library in game, then retry."
        else:
            request.session["portrait_notice"] = "No exact name match was found in the Tray library. Save this Sim or household to My Library with the same name, then retry."
    return RedirectResponse(f"/sims/{sim_id}#portraits", status_code=303)


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
def download_configured_clock_sync(request: Request, capture_portraits: str = Form("true")):
    """Rotate the active save's token and return a private ready-to-install kit."""
    with db() as session:
        ctx = context(request, session)
        save = ctx["save"]
        if not save:
            raise HTTPException(400, "Open a tracker save before creating a private Clock Sync kit.")
        raw = rotate_clock_link(session, save.id)
        base_url = str(request.base_url).rstrip("/") if settings.local_mode else settings.public_url
        try:
            package = clock_bundle.build_bundle(
                f"{base_url}/api/clock/report", raw,
                str(capture_portraits).casefold() in {"1", "true", "on", "yes"},
            )
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
    package=ROOT / "release" / "Decades-Tracker-4.5.1-Setup.exe"
    if not package.exists():
        return RedirectResponse(settings.desktop_installer_url, status_code=302)
    return StreamingResponse(package.open("rb"),media_type="application/vnd.microsoft.portable-executable",headers={
        "Content-Disposition":'attachment; filename="Decades-Tracker-4.5.1-Setup.exe"',"Cache-Control":"no-store",
    })


@app.post("/api/clock/report")
async def clock_report(request: Request, authorization: str | None = Header(None)):
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401)
    digest = hash_secret(authorization[7:].strip())
    with db() as session:
        link = session.scalar(select(ClockLink).where(ClockLink.token_hash == digest, ClockLink.enabled.is_(True)))
        if not link: raise HTTPException(401, "Invalid clock token")
        return clock.receive(session, link, await request.json())


@app.get("/api/clock/ping")
def clock_ping(authorization: str | None = Header(None)):
    """Validate a private relay link without advancing time or changing records."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401)
    digest = hash_secret(authorization[7:].strip())
    with db() as session:
        link = session.scalar(select(ClockLink).where(
            ClockLink.token_hash == digest, ClockLink.enabled.is_(True),
        ))
        if not link:
            raise HTTPException(401, "Invalid clock token")
        save = session.get(ChronicleSave, link.save_id)
        return {
            "ok": True, "clock_sync_version": clock_bundle.CLOCK_SYNC_VERSION,
            "save_id": save.id, "save_name": save.name,
            "tracker_global_day": save.global_day, "last_game_day": link.last_game_day,
        }


@app.get("/portraits/{record_id}/{stage}")
def portrait(request: Request, record_id: str, stage: str):
    with db() as session:
        record = session.get(Record, record_id)
        if not record: raise HTTPException(404)
        save=owned_save(request, session, record.save_id)
        if stage == "current" and record.kind == "sim":
            raw=str((record.data or {}).get("game_age_stage") or "").replace("Age.","").replace("_","").replace(" ","").casefold()
            stage_map={"baby":"newborn","newborn":"newborn","infant":"infant","toddler":"toddler","child":"child","preteen":"preteen","teen":"teen","youngadult":"youngadult","adult":"adult","elder":"elder"}
            stage=stage_map.get(raw,insights.life_stage(record,save.global_day))
        stage_key="".join(character for character in str(stage).casefold() if character.isalpha()) or "default"
        stage_items=list(session.scalars(select(Portrait).where(
            Portrait.record_id == record_id, func.lower(func.replace(Portrait.stage," ","")) == stage_key,
        )))
        item=next((value for value in stage_items if value.source not in {"clock-sync-game","save-file-game"}),stage_items[0] if stage_items else None)
        if not item and stage_key != "default":
            item = session.scalar(select(Portrait).where(Portrait.record_id == record_id, func.lower(Portrait.stage) == "default").limit(1))
        if not item:
            item = session.scalar(select(Portrait).where(Portrait.record_id == record_id).order_by(Portrait.created_at.desc()).limit(1))
        if not item: raise HTTPException(404)
        etag=f'"portrait-{hashlib.sha256(item.image).hexdigest()[:20]}"'
        headers={"Cache-Control":"private,max-age=0,must-revalidate","ETag":etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304,headers=headers)
        return Response(item.image, media_type=item.mime_type, headers=headers)


@app.post("/portraits/{record_id}")
async def upload_portrait(request: Request, record_id: str, stage: str = Form("default"), image: UploadFile = None):
    data = await image.read()
    normalized, mime = portraits.normalize_image(data)
    with db() as session:
        record = session.get(Record, record_id)
        if not record: raise HTTPException(404)
        owned_save(request, session, record.save_id)
        stage_key="".join(character for character in str(stage).casefold() if character.isalpha()) or "default"
        stage_items=list(session.scalars(select(Portrait).where(
            Portrait.record_id==record_id,
            func.lower(func.replace(Portrait.stage," ",""))==stage_key,
        )))
        item=next((value for value in stage_items if value.source not in {"clock-sync-game","save-file-game"}),stage_items[0] if stage_items else None)
        if item: item.image, item.mime_type, item.source = normalized, mime, "upload"
        else: item=Portrait(save_id=record.save_id, record_id=record_id, stage=stage, image=normalized, mime_type=mime);session.add(item)
        session.flush();sync.sync_portrait(session,session.get(ChronicleSave,record.save_id),item,record_id,item.stage)
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
