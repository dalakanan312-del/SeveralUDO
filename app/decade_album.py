"""One growing, save-wide group portrait per year, independent of branch clocks."""
from __future__ import annotations

import io
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, ImageStat
from sqlalchemy import or_, select

from . import portraits, sync
from .models import Portrait, Record
from .tray_scanner import decode_sgi, discover_portraits, match_portraits

PAGE = "decade-snapshots"
DEFAULT_BACKGROUND = "#ffffff"
SHARED_KINDS = {"decade_snapshot", "household_portrait"}


def _branch(save):
    from .infinite_dynasty import state
    data = state(save) or {}
    return str(data.get("active_branch_id") or "main"), str(data.get("branch_name") or "Main save")


def _integer(value, default=None):
    try: return int(value)
    except (ValueError, TypeError): return default


def _year(save, day):
    return int(save.start_year) + (int(day) - 1) // max(1, int(save.days_per_year))


def eligible_in_year(sim, save, year):
    """Offer this branch's people who lived during the selected historical year."""
    if sim.deleted or (sim.data or {}).get("infinite_frozen"):
        return False
    data = sim.data or {}
    birth = _integer(data.get("birth_global_day"), sim.global_day)
    death = _integer(data.get("death_global_day"))
    if birth is None or _year(save, birth) > year:
        return False
    if death is not None:
        return _year(save, death) >= year
    return not data.get("game_was_dead")


def archives(session, save):
    if not save: return []
    rows = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "decade_snapshot",
        or_(Record.deleted.is_(False), Record.data["infinite_frozen"].as_boolean().is_(True)),
    ).order_by(Record.created_at, Record.id)))
    by_year = {}
    for row in rows:
        year = _integer((row.data or {}).get("portrait_year"))
        if year is not None and not (row.data or {}).get("merged_into"):
            by_year.setdefault(year, row)
    return [by_year[year] for year in sorted(by_year, reverse=True)]


def _touch(session, row, data):
    from .domain import journal
    base = row.version or 0
    row.data = {k:v for k,v in data.items() if k not in {"infinite_branch_id", "infinite_frozen", "infinite_frozen_global_day"}}
    row.deleted = False
    row.version = base + 1
    session.flush()
    journal(session, row, "upsert", base)


def _image(session, record_id, stage="default"):
    return session.scalar(select(Portrait).where(Portrait.record_id == record_id, Portrait.stage == stage))


def _store(session, save, record, stage, raw, mime="image/png", source="decade-member"):
    row = _image(session, record.id, stage)
    if row is not None and row.image == raw and row.mime_type == mime:
        return row
    if row is None:
        row = Portrait(save_id=save.id, record_id=record.id, stage=stage, image=raw, mime_type=mime, source=source)
        session.add(row)
    else:
        row.image, row.mime_type, row.source = raw, mime, source
    session.flush()
    sync.sync_portrait(session, save, row, record.id, stage)
    return row


def _source_photos(session, save, sims, root=None, requested_ids=None):
    candidates = discover_portraits(root)
    matched, ambiguous = match_portraits(candidates, sims)
    photos = {}
    invalid = 0
    # Load metadata without loading every image into memory.
    stored = defaultdict(list)
    for rid, pid, stage, source in session.execute(select(Portrait.record_id, Portrait.id, Portrait.stage, Portrait.source).where(
            Portrait.save_id == save.id, Portrait.record_id.in_(list(requested_ids) if requested_ids is not None else [sim.id for sim in sims]))):
        stored[rid].append((pid, stage, source))
    for sim in sims:
        if requested_ids is not None and sim.id not in requested_ids: continue
        candidate = matched.get(sim.id)
        if candidate:
            try:
                raw = decode_sgi(candidate.image_path.read_bytes())
                photos[sim.id] = (raw, candidate.age_stage, "Tray Library")
                continue
            except (OSError, ValueError):
                invalid += 1
        from .clock import _stage_key
        stage = _stage_key((sim.data or {}).get("game_age_stage") or (sim.data or {}).get("life_stage"))
        options = sorted(stored[sim.id], key=lambda p:(p[1] != stage, p[1] != "default", p[2] != "upload"))
        if options:
            pid, photo_stage, source = options[0]
            row = session.get(Portrait, pid)
            try:
                portraits.open_image(row.image).load()
                photos[sim.id] = (row.image, stage if photo_stage == "default" else photo_stage, "Imported/profile portrait")
            except (OSError, ValueError):
                invalid += 1
    return photos, len(candidates), ambiguous, invalid


def _freeze_photo(raw):
    image = portraits.open_image(raw).convert("RGBA")
    bounds = image.getchannel("A").getbbox()
    if bounds is None: raise ValueError("The selected portrait is completely transparent.")
    image = image.crop(bounds)
    image.thumbnail((700, 1050), Image.Resampling.LANCZOS)
    stream = io.BytesIO(); image.save(stream, "PNG")
    return stream.getvalue()


def _entry(sim, stage, source, day, branch_id, branch_name, homes):
    data = sim.data or {}
    hid = str(data.get("current_household_id") or "")
    return {"sim_id":sim.id, "name":sim.label, "portrait_stage":f"sim-{sim.id}",
            "photo_age_stage":stage, "photo_source":source, "capture_global_day":day,
            "branch_id":branch_id, "branch_name":branch_name, "household_id":hid,
            "household_name":homes.get(hid, "Other household")}


def _font(size):
    # Use installed fonts, not redistributed proprietary font files.
    for name in ("DejaVuSerif.ttf", "times.ttf"):
        try: return ImageFont.truetype(name, size)
        except OSError: pass
    return ImageFont.load_default(size=size)


def compose(year, people, background=DEFAULT_BACKGROUND, show_names=False):
    """Cutout group portrait. Keep full bodies and transparent space, not tiles."""
    if not people: raise ValueError("Add at least one portrait first.")
    heights = {"newborn":140, "infant":160, "toddler":205, "child":295, "preteen":330, "teen":395}
    grouped = defaultdict(list)
    for entry, raw in people:
        grouped[(entry.get("household_id") or "", entry.get("household_name") or "")].append((entry, raw))
    actors = []
    for group in grouped.values():
        # Adults are behind children, with a small stagger rather than large gaps.
        group.sort(key=lambda pair:-heights.get(pair[0].get("photo_age_stage"), 440))
        for entry, raw in group:
            actor = portraits.open_image(raw).convert("RGBA")
            bounds = actor.getchannel("A").getbbox()
            if bounds: actor = actor.crop(bounds)
            height = heights.get(entry.get("photo_age_stage"), 440)
            actor = ImageOps.contain(actor, (300, height), Image.Resampling.LANCZOS)
            actors.append((entry, actor))
    # Bound dimensions and memory without dropping people from large dynasties.
    bands = [actors[i:i+60] for i in range(0,len(actors),60)]
    widths = [sum(max(72,int(actor.width*.86)) for _,actor in band)+100 for band in bands]
    width = max(900, max(widths)); band_height = 535 if show_names else 490
    height = 155 + len(bands)*band_height + 30
    if width * height > 60_000_000 or height > 16000:
        raise ValueError("This album is too large to render safely. No changes were saved.")
    canvas = Image.new("RGB", (width,height), background)
    rgb = canvas.getpixel((0,0)); ink = "#171717" if sum(rgb)>380 else "#f7f3e8"
    draw = ImageDraw.Draw(canvas); font = _font(110)
    label = str(year); bounds = draw.textbbox((0,0),label,font=font)
    draw.text(((width-(bounds[2]-bounds[0]))/2,0),label,font=font,fill=ink)
    for band_index, band in enumerate(bands):
        x = (width-widths[band_index])/2+50; positioned=[]
        floor = 155+(band_index+1)*band_height-(55 if show_names else 8)
        for index,(entry,actor) in enumerate(band):
            y = floor-actor.height-(8 if index%2==0 and actor.height>330 else 0)
            positioned.append((entry,actor,int(x),y)); x += max(72,int(actor.width*.86))
        for entry,actor,x,y in sorted(positioned,key=lambda p:-p[1].height):
            canvas.paste(actor,(x,y),actor)
        if show_names:
            for entry,actor,x,y in positioned:
                # Names remain fully available in the HTML roster; small image
                # labels use two lines so long names do not cross whole families.
                words=str(entry.get("name") or "").split(); lines=[words[0] if words else "", " ".join(words[1:])]
                for offset,line in enumerate(lines): draw.text((x,floor+10+offset*18),line[:24],font=_font(14),fill=ink)
    output=io.BytesIO();canvas.save(output,"WEBP",quality=94,method=4)
    return output.getvalue()


def _legacy_members(session, record):
    people = {}
    for pid in (record.data or {}).get("household_portrait_ids", []):
        plate = session.get(Record, pid)
        if plate and plate.save_id == record.save_id:
            for sim_id in (plate.data or {}).get("member_ids", []):
                people.setdefault(sim_id, plate)
    return people


def _legacy_photos(session, record, root=None):
    """Recover the photo actually used, not today's same-named/aged-up photo.

    Old plates have fixed-size tiles. Compare each tile against historical Tray
    sources rendered by that exact compositor. If the source is gone, preserve
    the original tile instead of quietly substituting a newer appearance.
    """
    from .decade_portraits import _portrait_tile
    candidates = discover_portraits(root)
    found = {}
    decoded = {}
    for plate_id in (record.data or {}).get('household_portrait_ids', []):
        plate=session.get(Record,plate_id)
        if not plate or plate.save_id!=record.save_id: continue
        photo=_image(session,plate.id)
        if not photo: continue
        original=portraits.open_image(photo.image).convert('RGB')
        ids=plate.data.get('member_ids',[]);names=plate.data.get('member_names',[])
        columns=min(4,max(1,len(ids)));rows=max(1,(len(ids)+columns-1)//columns)
        if original.size!=(24+columns*274,94+rows*408): continue
        background=plate.data.get('background_color') or '#2b2118'
        for i,sid in enumerate(ids):
            x,y=24+(i%columns)*274,94+(i//columns)*408
            tile=original.crop((x,y,x+250,y+330))
            name=names[i].casefold().strip() if i<len(names) else ''
            options=[]
            for candidate in candidates:
                full=(candidate.first_name+' '+candidate.last_name).casefold().strip()
                if name and full!=name: continue
                key=str(candidate.image_path)
                try:
                    if key not in decoded: decoded[key]=decode_sgi(candidate.image_path.read_bytes())
                    raw=decoded[key]
                    rendered=_portrait_tile(raw,(250,330),background)
                    error=sum(ImageStat.Stat(ImageChops.difference(tile,rendered)).rms)/3
                    if error<5: options.append((error,raw,candidate.age_stage))
                except (OSError,ValueError): continue
            if options:
                _,raw,stage=min(options,key=lambda item:item[0])
                found[sid]=(raw,stage,'Original Tray photo matched to archived snapshot')
            else:
                raw=io.BytesIO();tile.save(raw,'PNG')
                found[sid]=(raw.getvalue(),'','Preserved original snapshot crop')
    return found


def update(session, save, year, *, selected_ids=None, background=DEFAULT_BACKGROUND,
           show_names=False, root=None, capture_day=None):
    """Append by stable Sim identity. Existing member photos are never replaced."""
    from .decade_portraits import _hex_color
    from .infinite_dynasty import frozen as branch_frozen
    if branch_frozen(save): raise ValueError("Continue an active branch before adding portraits.")
    if session.get_bind().dialect.name != 'sqlite':
        from .models import ChronicleSave
        session.execute(select(ChronicleSave.id).where(ChronicleSave.id==save.id).with_for_update())
    year = int(year)
    if year > _year(save,save.global_day): raise ValueError("Choose the current year or an earlier year.")
    background = _hex_color(background)
    branch_id, branch_name = _branch(save)
    all_sims = list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="sim")))
    homes = {r.id:r.label for r in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="household"))}
    by_id = {sim.id:sim for sim in all_sims}
    candidates = [sim for sim in all_sims if eligible_in_year(sim,save,year)]
    requested = set(selected_ids) if selected_ids is not None else {sim.id for sim in candidates}
    if requested - {sim.id for sim in candidates}: raise ValueError("Only this branch's Sims who lived in that year can be added.")
    records = [r for r in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="decade_snapshot"))
               if _integer((r.data or {}).get("portrait_year"))==year and not (r.data or {}).get("merged_into")
               and (not r.deleted or (r.data or {}).get("infinite_frozen"))]
    records.sort(key=lambda r:(r.created_at,r.id))
    album = records[0] if records else None
    stored_ids={entry['sim_id'] for record in records for entry in (record.data or {}).get('members',[])}
    legacy_ids={sim_id for record in records if not record.data.get('album_version') for sim_id in _legacy_members(session,record)}
    photos, available, ambiguous, invalid = _source_photos(session,save,all_sims,root,requested-stored_ids-legacy_ids)
    members = {}; frozen = {}; contributions = {}; legacy_images = []
    for record in records:
        data = record.data or {}
        contributions.update(data.get("contributions") or {})
        for entry in data.get("members", []):
            image = _image(session,record.id,entry["portrait_stage"])
            if not image: raise ValueError(f"The saved photo for {entry['name']} is missing. Restore a backup before updating this album.")
            members.setdefault(entry["sim_id"],dict(entry));frozen.setdefault(entry["sim_id"],image.image)
        if not data.get("album_version"):
            legacy_photos=_legacy_photos(session,record,root)
            missing=[]
            for sim_id,plate in _legacy_members(session,record).items():
                if sim_id in members: continue
                sim=by_id.get(sim_id);photo=legacy_photos.get(sim_id)
                if not sim or not photo: missing.append(sim.label if sim else sim_id);continue
                raw,stage,source=photo
                entry=_entry(sim,stage,source,record.global_day,data.get("infinite_branch_id") or branch_id,branch_name,homes)
                entry.update(household_id=plate.data.get("household_id"),household_name=plate.data.get("household_name"),legacy_photo_recovered=True)
                members[sim_id]=entry;frozen[sim_id]=_freeze_photo(raw)
            if missing: raise ValueError("Original snapshot preserved. Photos are needed before restyling: "+", ".join(missing))
            old=_image(session,record.id)
            if old: legacy_images.append((record,old.image,old.mime_type))
    existing_ids=set(members);added=[];missing=[]
    for sim in candidates:
        if sim.id not in requested or sim.id in members: continue
        photo=photos.get(sim.id)
        if not photo: missing.append(sim.label);continue
        raw,stage,source=photo
        members[sim.id]=_entry(sim,stage,source,capture_day if capture_day is not None else save.global_day,branch_id,branch_name,homes)
        frozen[sim.id]=_freeze_photo(raw);added.append(sim.id)
    if not members: raise ValueError("No matching photos yet. Save the household to My Library or upload portraits to the Sim profiles first.")
    image=compose(year,[(entry,frozen[sid]) for sid,entry in members.items()],background,show_names)
    if album is None:
        album=Record(save_id=save.id,kind="decade_snapshot",label=f"{save.name} — {year} Decade Snapshot",
                     global_day=capture_day if capture_day is not None else save.global_day,data={},version=0)
        session.add(album);session.flush()
    # Preserve the first old composite privately, without adding gallery cards.
    for record,raw,mime in legacy_images:
        if _image(session,record.id,"original") is None: _store(session,save,record,"original",raw,mime,"decade-original")
    for sid,entry in members.items(): _store(session,save,album,entry["portrait_stage"],frozen[sid])
    contributions[branch_id]={"branch_name":branch_name,"last_added_global_day":save.global_day,
                              "member_ids":sorted(set(contributions.get(branch_id,{}).get("member_ids",[]))|{sid for sid,entry in members.items() if entry.get('branch_id')==branch_id})}
    data={**(album.data or {}),"album_version":1,"source_key":f"decade-snapshot:{year}","portrait_year":year,
          "original_preserved":bool(legacy_images or (album.data or {}).get("original_preserved")),
          "members":list(members.values()),"member_count":len(members),"member_ids":list(members),
          "household_names":list(dict.fromkeys(entry["household_name"] for entry in members.values())),
          "contributions":contributions,"background_color":background,"show_names":bool(show_names),
          "missing_member_names":missing,"source":"Shared decade group portrait","layout":"group"}
    _touch(session,album,data);_store(session,save,album,"default",image,"image/webp","decade-group")
    for extra in records[1:]:
        _touch(session,extra,{**extra.data,"merged_into":album.id})
    save.revision+=1
    return {"snapshot":album,"records":[album],"added":len(added),"kept":len(existing_ids),"missing":missing,
            "available":available,"ambiguous":ambiguous,"invalid":invalid,"background_color":background,
            "created":int(not records),"updated":int(bool(records)),"individual":{}}


def page_context(session,save,year=None):
    rows=archives(session,save)
    current=_year(save,save.global_day)
    year=_integer(year, rows[0].data["portrait_year"] if rows else current)
    active=next((r for r in rows if r.data.get("portrait_year")==year),None)
    existing=set((active.data or {}).get("member_ids",[])) if active else set()
    if active and not active.data.get("album_version"):existing.update(_legacy_members(session,active))
    sims=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="sim",Record.deleted.is_(False))))
    candidates=sorted([sim for sim in sims if eligible_in_year(sim,save,year) and sim.id not in existing],key=lambda s:s.label.casefold())
    from .dynasty_tools import snapshot_coverage
    return {"album_coverage":snapshot_coverage(session,save,year,active),
            "albums":rows,"album_year":year,"album_selected":active,"album_candidates":candidates,
            "album_current_year":current,"album_branch_name":_branch(save)[1]}
