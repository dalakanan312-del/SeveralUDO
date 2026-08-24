from __future__ import annotations

import csv
import io
import json
import re
import textwrap
import zipfile
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ChronicleSave, Record


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "decades-chronicle"


def records_for(session: Session, save: ChronicleSave, kinds: set[str] | None = None) -> list[Record]:
    query = select(Record).where(Record.save_id == save.id, Record.deleted.is_(False))
    if kinds:
        query = query.where(Record.kind.in_(kinds))
    return list(session.scalars(query.order_by(Record.kind, Record.global_day, Record.label)))


def csv_archive(session: Session, save: ChronicleSave) -> bytes:
    grouped: dict[str, list[Record]] = defaultdict(list)
    for item in records_for(session, save):
        grouped[item.kind].append(item)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", (
            f"Decades Tracker export for {save.name}\n"
            "Each CSV contains common columns plus every structured field used by that record type.\n"
            "Lists and nested values are retained as JSON.\n"
        ))
        for kind, rows in grouped.items():
            data_keys = sorted({str(key) for row in rows for key in (row.data or {})})
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=["id", "label", "global_day", "version"] + data_keys)
            writer.writeheader()
            for row in rows:
                values = {"id": row.id, "label": row.label, "global_day": row.global_day, "version": row.version}
                for key in data_keys:
                    value = (row.data or {}).get(key)
                    values[key] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                writer.writerow(values)
            archive.writestr(f"{safe_filename(kind)}.csv", stream.getvalue().encode("utf-8-sig"))
    return output.getvalue()


def _gedcom_text(value) -> str:
    return " ".join(str(value or "").replace("@", "").split())


def gedcom(session: Session, save: ChronicleSave) -> str:
    sims = records_for(session, save, {"sim"})
    relationships = records_for(session, save, {"relationship"})
    index = {sim.id: number for number, sim in enumerate(sims, 1)}
    family_rows: list[tuple[str, str, list[str], str]] = []
    family_by_pair: dict[frozenset[str], int] = {}
    for relationship in relationships:
        data = relationship.data or {}
        first, second = str(data.get("partner1_id") or ""), str(data.get("partner2_id") or "")
        if first in index and second in index:
            key = frozenset((first, second))
            if key not in family_by_pair:
                family_by_pair[key] = len(family_rows) + 1
                family_rows.append((first, second, [], str(data.get("type") or "Relationship")))
    for child in sims:
        data = child.data or {}
        parents = [str(data.get("mother_id") or ""), str(data.get("father_id") or "")]
        parents = [value for value in parents if value in index]
        if not parents:
            continue
        key = frozenset(parents)
        family_number = family_by_pair.get(key)
        if not family_number:
            family_number = len(family_rows) + 1
            family_by_pair[key] = family_number
            family_rows.append((parents[0], parents[1] if len(parents) > 1 else "", [], "Parents"))
        family_rows[family_number - 1][2].append(child.id)
    lines = ["0 HEAD", "1 SOUR DEC-TRACKER", "1 GEDC", "2 VERS 5.5.1", "1 CHAR UTF-8"]
    child_family: dict[str, int] = {}
    spouse_families: dict[str, list[int]] = defaultdict(list)
    for number, (first, second, children, _kind) in enumerate(family_rows, 1):
        for person in (first, second):
            if person:
                spouse_families[person].append(number)
        for child in children:
            child_family[child] = number
    for sim in sims:
        data = sim.data or {}
        number = index[sim.id]
        first = _gedcom_text(data.get("first_name") or sim.label)
        last = _gedcom_text(data.get("last_name"))
        lines.extend([f"0 @I{number}@ INDI", f"1 NAME {first} /{last}/"])
        sex = str(data.get("sex") or "").casefold()
        lines.append(f"1 SEX {'F' if sex.startswith('f') else 'M' if sex.startswith('m') else 'U'}")
        birth = data.get("birth_global_day")
        death = data.get("death_global_day")
        if birth is not None:
            year = save.start_year + (int(birth) - 1) // save.days_per_year
            lines.extend(["1 BIRT", f"2 DATE {year}", f"2 NOTE Decades Tracker Global Day {birth}"])
        if death is not None:
            year = save.start_year + (int(death) - 1) // save.days_per_year
            lines.extend(["1 DEAT", f"2 DATE {year}", f"2 NOTE Decades Tracker Global Day {death}"])
        if sim.id in child_family:
            lines.append(f"1 FAMC @F{child_family[sim.id]}@")
        for family in spouse_families.get(sim.id, []):
            lines.append(f"1 FAMS @F{family}@")
    for number, (first, second, children, kind) in enumerate(family_rows, 1):
        lines.append(f"0 @F{number}@ FAM")
        if first:
            lines.append(f"1 HUSB @I{index[first]}@")
        if second:
            lines.append(f"1 WIFE @I{index[second]}@")
        for child in children:
            lines.append(f"1 CHIL @I{index[child]}@")
        lines.append(f"1 NOTE {_gedcom_text(kind)}")
    lines.append("0 TRLR")
    return "\n".join(lines) + "\n"


def calendar_ics(session: Session, save: ChronicleSave) -> str:
    kinds = {"roll", "event", "death", "pregnancy", "illness", "relationship"}
    rows = records_for(session, save, kinds)
    base = date(2000, 1, 1)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SeveralUDO//Decades Tracker//EN",
             "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{save.name} · Challenge Calendar"]
    for item in rows:
        day = item.global_day
        if day is None:
            day = (item.data or {}).get("due_global_day") or (item.data or {}).get("death_global_day")
        if day is None:
            continue
        day = int(day)
        synthetic = base + timedelta(days=max(0, day - 1))
        historical_year = save.start_year + (day - 1) // save.days_per_year
        summary = f"{item.label} · Year {historical_year} · GD {day}"
        escaped = summary.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{item.id}@decades-tracker",
            f"DTSTART;VALUE=DATE:{synthetic:%Y%m%d}",
            f"SUMMARY:{escaped}",
            f"DESCRIPTION:Decades Tracker {item.kind}; historical year {historical_year}; Global Day {day}.",
            f"X-DECADES-GLOBAL-DAY:{day}",
            f"X-DECADES-HISTORICAL-YEAR:{historical_year}",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _minimal_pdf(lines: list[str]) -> bytes:
    """Small dependency-free PDF fallback used before desktop packages update."""
    def esc(value: str) -> str:
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    pages = [lines[index:index + 45] for index in range(0, len(lines), 45)] or [[]]
    objects: list[bytes] = []
    font_id = 3 + len(pages) * 2
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    for index, page in enumerate(pages):
        page_id = 3 + index * 2
        content_id = page_id + 1
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode())
        commands = ["BT", "/F1 10 Tf", "48 748 Td", "14 TL"]
        for line in page:
            commands.append(f"({esc(line[:110])}) Tj T*")
        commands.append("ET")
        content = "\n".join(commands).encode("latin-1", errors="replace")
        objects.append(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman >>")
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(result)


def chronicle_pdf(session: Session, save: ChronicleSave, story: dict) -> bytes:
    lines = [f"THE CHRONICLE OF {save.name}", f"Through historical year {story['year']}", "", story["opening"], ""]
    for entry in reversed(story.get("authored_entries") or []):
        lines.extend([entry.label, str((entry.data or {}).get("body") or ""), ""])
    if story.get("occult_outcomes"):
        lines.extend(["OCCULT OUTCOMES", ""])
        for entry in reversed(story["occult_outcomes"]):
            data=entry.data or {}
            lines.append(f"- GD {data.get('completed_global_day',entry.global_day)}: {entry.label} — {data.get('die','die')} {data.get('actual')} — {data.get('outcome','Resolved')}")
        lines.append("")
    for chapter in reversed(story.get("chapters") or []):
        lines.extend([f"Year {chapter['year']}", chapter["summary"]])
        lines.extend(f"- {entry.label}" for entry in chapter["entries"])
        lines.append("")
    wrapped = [part for line in lines for part in (textwrap.wrap(str(line), 100) or [""])]
    return _minimal_pdf(wrapped)
