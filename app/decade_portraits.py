from __future__ import annotations

"""Ten-year household portrait reminders and Tray-backed archive plates."""

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import sync
from .models import ChronicleSave, Portrait, Record
from .tray_scanner import _name_key, _sim_names, decode_sgi, discover_portraits, import_portraits


DEFAULT_BACKGROUND = "#2b2118"


def _living(sim: Record, day: int) -> bool:
    data = sim.data or {}
    try:
        birth = int(data.get("birth_global_day", sim.global_day or 1))
    except (TypeError, ValueError):
        birth = 1
    try:
        death = int(data["death_global_day"]) if data.get("death_global_day") not in (None, "") else None
    except (TypeError, ValueError):
        death = None
    return not bool(data.get("game_was_dead")) and birth <= day and (death is None or death > day)


def _milestone(save: ChronicleSave) -> tuple[int, int] | None:
    per_year = max(1, int(save.days_per_year))
    current_year = int(save.start_year) + (max(1, int(save.global_day)) - 1) // per_year
    elapsed = current_year - int(save.start_year)
    completed = (elapsed // 10) * 10
    if completed < 10:
        return None
    year = int(save.start_year) + completed
    return year, completed * per_year + 1


def schedule_prompt(session: Session, save: ChronicleSave) -> int:
    """Create at most one durable inbox reminder for the latest ten-year mark."""
    from .domain import journal

    milestone = _milestone(save)
    if not milestone:
        return 0
    year, due_day = milestone
    source_key = f"save-portrait:{year}"
    exists = session.scalar(select(Record.id).where(
        Record.save_id == save.id,
        Record.kind == "game_candidate",
        Record.data["source_key"].as_string() == source_key,
    ).limit(1))
    if exists:
        return 0
    households = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "household", Record.deleted.is_(False),
    )))
    active = [home for home in households if bool((home.data or {}).get("active", True))]
    item = Record(
        save_id=save.id,
        kind="game_candidate",
        label=f"Save the {year} household portrait",
        global_day=min(int(save.global_day), due_day),
        data={
            "action":"save_portrait", "status":"pending", "source_key":source_key,
            "payload":{
                "portrait_year":year, "milestone_global_day":due_day,
                "background_color":DEFAULT_BACKGROUND,
                "household_ids":[home.id for home in active],
                "household_names":[home.label for home in active],
            },
        },
    )
    session.add(item); session.flush(); journal(session,item,"upsert",0)
    return 1


def _hex_color(value: object) -> str:
    text = str(value or "").strip()
    if len(text) == 7 and text.startswith("#"):
        try:
            int(text[1:], 16)
            return text
        except ValueError:
            pass
    return DEFAULT_BACKGROUND


def _portrait_tile(raw: bytes, size: tuple[int, int], background: str) -> Image.Image:
    """Place a centered Tray portrait on a flat matte, never a blurred fill."""
    width, height = size
    source = Image.open(io.BytesIO(raw)).convert("RGB")
    fitted = ImageOps.fit(source, (width - 22, height - 22), method=Image.Resampling.LANCZOS, centering=(.5, .38))
    matte = Image.new("RGB", size, background)
    mask = Image.new("L", fitted.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, fitted.width - 1, fitted.height - 1), radius=max(18, width // 7), fill=255)
    matte.paste(fitted, (11, 11), mask)
    return matte


def _compose(title: str, year: int, people: list[tuple[str, bytes]], background: str) -> bytes:
    columns = min(4, max(1, len(people)))
    rows = max(1, math.ceil(len(people) / columns))
    tile_w, tile_h, gap, header, footer = 250, 330, 24, 94, 54
    width = gap + columns * (tile_w + gap)
    height = header + rows * (tile_h + footer + gap)
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=28)
    label_font = ImageFont.load_default(size=18)
    draw.text((gap, 20), f"{title} · {year}", fill="#f3ead8", font=title_font)
    for index, (name, raw) in enumerate(people):
        column, row = index % columns, index // columns
        x, y = gap + column * (tile_w + gap), header + row * (tile_h + footer + gap)
        image.paste(_portrait_tile(raw, (tile_w, tile_h), background), (x, y))
        draw.text((x + 8, y + tile_h + 10), name, fill="#f3ead8", font=label_font)
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=90, method=6)
    return output.getvalue()


def _combine_households(save_name: str, year: int, plates: list[tuple[str, bytes]], background: str) -> bytes:
    """Combine all household plates into one dated, save-wide decade snapshot."""
    columns = 1 if len(plates) == 1 else 2
    rows = max(1, math.ceil(len(plates) / columns))
    gap, header, panel_w, panel_h = 30, 110, 720, 540
    width = gap + columns * (panel_w + gap)
    height = header + rows * (panel_h + 70 + gap)
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    draw.text((gap, 20), f"{save_name} · Decade Snapshot · {year}", fill="#f3ead8", font=ImageFont.load_default(size=32))
    label_font = ImageFont.load_default(size=20)
    for index, (household_name, raw) in enumerate(plates):
        x = gap + (index % columns) * (panel_w + gap)
        y = header + (index // columns) * (panel_h + 70 + gap)
        source = Image.open(io.BytesIO(raw)).convert("RGB")
        contained = ImageOps.contain(source, (panel_w, panel_h), method=Image.Resampling.LANCZOS)
        panel = Image.new("RGB", (panel_w, panel_h), background)
        panel.paste(contained, ((panel_w-contained.width)//2, (panel_h-contained.height)//2))
        image.paste(panel, (x, y))
        draw.text((x + 8, y + panel_h + 14), household_name, fill="#f3ead8", font=label_font)
    output = io.BytesIO()
    image.save(output, format="WEBP", quality=90, method=6)
    return output.getvalue()


def save_from_tray(session: Session, save: ChronicleSave, year: int, background: str,
                   root: Path | None = None) -> dict:
    """Build one archive plate for each active household from newest exact Tray matches."""
    from .domain import journal

    background = _hex_color(background)
    candidates = discover_portraits(root)
    sims = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
    )))
    homes = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "household", Record.deleted.is_(False),
    )))
    homes = [home for home in homes if bool((home.data or {}).get("active", True))]

    tracker_names: dict[str, list[Record]] = {}
    for sim in sims:
        for key in _sim_names(sim):
            tracker_names.setdefault(key, []).append(sim)
    newest = {}
    for candidate in candidates:
        newest.setdefault(_name_key(candidate.name), candidate)
    matched: dict[str, tuple[str, bytes]] = {}
    invalid = ambiguous = 0
    for key, candidate in newest.items():
        matches = {sim.id:sim for sim in tracker_names.get(key, ())}
        if len(matches) > 1:
            ambiguous += 1; continue
        if not matches:
            continue
        sim = next(iter(matches.values()))
        if not _living(sim, save.global_day):
            continue
        try:
            matched[sim.id] = (sim.label, decode_sgi(candidate.image_path.read_bytes()))
        except Exception:
            invalid += 1

    created = updated = 0
    records: list[Record] = []
    household_plates: list[tuple[str, bytes]] = []
    missing: list[str] = []
    for home in homes:
        members = [sim for sim in sims if (sim.data or {}).get("current_household_id") == home.id and _living(sim, save.global_day)]
        people = [matched[sim.id] for sim in members if sim.id in matched]
        missing.extend(sim.label for sim in members if sim.id not in matched)
        if not people:
            continue
        source_key = f"household-portrait:{home.id}:{int(year)}"
        record = session.scalar(select(Record).where(
            Record.save_id == save.id, Record.kind == "household_portrait", Record.deleted.is_(False),
            Record.data["source_key"].as_string() == source_key,
        ).limit(1))
        payload = {
            "source_key":source_key, "household_id":home.id, "household_name":home.label,
            "portrait_year":int(year), "member_ids":[sim.id for sim in members if sim.id in matched],
            "member_names":[sim.label for sim in members if sim.id in matched],
            "background_color":background, "source":"Sims 4 Tray Library",
        }
        if record:
            base=record.version; record.label=f"{home.label} — {year} portrait"; record.global_day=save.global_day
            record.data={**(record.data or {}), **payload}; record.version += 1; journal(session,record,"upsert",base); updated += 1
        else:
            record=Record(save_id=save.id,kind="household_portrait",label=f"{home.label} — {year} portrait",global_day=save.global_day,data=payload)
            session.add(record); session.flush(); journal(session,record,"upsert",0); created += 1
        image = _compose(home.label, int(year), people, background)
        portrait = session.scalar(select(Portrait).where(Portrait.record_id == record.id, Portrait.stage == "default"))
        if portrait:
            portrait.image=image; portrait.mime_type="image/webp"; portrait.source="tray-household-decade"
        else:
            portrait=Portrait(save_id=save.id,record_id=record.id,stage="default",mime_type="image/webp",image=image,source="tray-household-decade")
            session.add(portrait)
        session.flush(); sync.sync_portrait(session, save, portrait, record.id, "default")
        records.append(record)
        household_plates.append((home.label,image))

    snapshot = None
    if household_plates:
        snapshot_key=f"decade-snapshot:{int(year)}"
        snapshot=session.scalar(select(Record).where(
            Record.save_id==save.id,Record.kind=="decade_snapshot",Record.deleted.is_(False),
            Record.data["source_key"].as_string()==snapshot_key,
        ).limit(1))
        snapshot_payload={
            "source_key":snapshot_key,"portrait_year":int(year),
            "household_portrait_ids":[record.id for record in records],
            "household_names":[name for name,_image in household_plates],
            "background_color":background,"source":"Combined Sims 4 Tray household portraits",
        }
        if snapshot:
            base=snapshot.version;snapshot.label=f"{save.name} — {year} Decade Snapshot";snapshot.global_day=save.global_day
            snapshot.data={**(snapshot.data or {}),**snapshot_payload};snapshot.version+=1;journal(session,snapshot,"upsert",base)
        else:
            snapshot=Record(save_id=save.id,kind="decade_snapshot",label=f"{save.name} — {year} Decade Snapshot",global_day=save.global_day,data=snapshot_payload)
            session.add(snapshot);session.flush();journal(session,snapshot,"upsert",0)
        combined=_combine_households(save.name,int(year),household_plates,background)
        snapshot_portrait=session.scalar(select(Portrait).where(Portrait.record_id==snapshot.id,Portrait.stage=="default"))
        if snapshot_portrait:
            snapshot_portrait.image=combined;snapshot_portrait.mime_type="image/webp";snapshot_portrait.source="tray-decade-snapshot"
        else:
            snapshot_portrait=Portrait(save_id=save.id,record_id=snapshot.id,stage="default",mime_type="image/webp",image=combined,source="tray-decade-snapshot")
            session.add(snapshot_portrait)
        session.flush();sync.sync_portrait(session,save,snapshot_portrait,snapshot.id,"default")

    individual = import_portraits(session, save, root=root)
    return {
        "available":len(candidates), "created":created, "updated":updated,
        "records":records, "missing":sorted(set(missing)), "ambiguous":ambiguous,
        "invalid":invalid, "individual":individual, "background_color":background,
        "snapshot":snapshot,
    }
