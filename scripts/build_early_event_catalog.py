"""Build the offline source-refreshed event catalogue from approved exports.

This maintenance script is intentionally separate from the app: it turns the
read-only Google Docs exports into compact, versioned seed data so a desktop
tracker never needs an internet connection to install these events.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORY = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
OUTPUT = ROOT / "app" / "early_event_catalog_data.py"
STATIC_CATALOGUE = ROOT / "app" / "event_catalog_data.py"

SOURCES = (
    (
        "pre-1000.txt",
        "pre1000",
        "https://docs.google.com/document/d/1AqeBSk36n5SC9QqCfbrDIno3D-wiK1SnXfNU9UVa6MI/edit",
    ),
    (
        "1000s.txt",
        "1000s",
        "https://docs.google.com/document/d/1VJC9ItPeMR4hxVUuh0bYy__b77Lf5C8ikrOgJJo7lqc/edit",
    ),
    (
        "1100s.txt",
        "1100s",
        "https://docs.google.com/document/d/1QtQFOMXQIj3vyoIl-igRshiuuYcJt8sfLBRs9rMUZhU/edit",
    ),
    (
        "1200s.txt",
        "1200s",
        "https://docs.google.com/document/d/19NzcWvSdq2MEzv3SKFZmX-JoOcSiNjwqtmrLZYSR-s0/edit",
    ),
)

YEAR_FIRST = re.compile(
    r"^(?P<start>\d{1,5})(?:\s*(?:[-–—]|to)\s*(?P<end>\d{1,5}))?"
    r"\s*(?P<era>BCE|BC|CE|AD)?\s+(?P<name>.+)$",
    re.IGNORECASE,
)
YEAR_IN_PARENS = re.compile(
    r"^(?P<name>.+?)\s*\((?:c\.\s*)?(?P<start>\d{1,5})"
    r"(?:\s*(?:[-–—]|to)\s*(?P<end>\d{1,5}))?"
    r"\s*(?P<era>BCE|BC|CE|AD)?[^)]*\)$",
    re.IGNORECASE,
)
YEAR_SUFFIX = re.compile(
    r"^(?P<name>.+?)\s+(?P<start>\d{1,5})"
    r"(?:\s*(?:[-–—]|to)\s*(?P<end>\d{1,5}))?"
    r"\s*(?P<era>BCE|BC|CE|AD)?\s*(?:[-–—]\s*(?P<suffix>Global))?\s*$",
    re.IGNORECASE,
)
DECADE_FIRST = re.compile(r"^(?P<start>\d{3,4})s\s+(?P<name>.+)$", re.IGNORECASE)

LOCATION_WORDS = (
    ("british isles", "British Isles"),
    ("britain", "Britain"),
    ("north america", "North America"),
    ("north africa", "North Africa"),
    ("near east", "Near East"),
    ("central europe", "Central Europe"),
    ("south america", "South America"),
    ("middle east", "Middle East"),
    ("south east asia", "Southeast Asia"),
    ("mesopotamia", "Mesopotamia"),
    ("fertile crescent", "Fertile Crescent"),
    ("scandinavia", "Scandinavia"),
    ("francia", "Francia"),
    ("aquitaine", "Aquitaine"),
    ("phrygia", "Phrygia"),
    ("edessa", "Edessa"),
    ("carthage", "Carthage"),
    ("babylonia", "Babylonia"),
    ("babylon", "Babylon"),
    ("assyria", "Assyria"),
    ("egypt", "Egypt"),
    ("england", "England"),
    ("ireland", "Ireland"),
    ("scotland", "Scotland"),
    ("wales", "Wales"),
    ("france", "France"),
    ("gaul", "Gaul"),
    ("germany", "Germany"),
    ("italy", "Italy"),
    ("spain", "Spain"),
    ("portugal", "Portugal"),
    ("poland", "Poland"),
    ("russia", "Russia"),
    ("india", "India"),
    ("japan", "Japan"),
    ("turkey", "Turkey"),
    ("china", "China"),
    ("korea", "Korea"),
    ("vietnam", "Vietnam"),
    ("israel", "Israel"),
    ("arabia", "Arabia"),
    ("mexico", "Mexico"),
    ("maya", "Mesoamerica"),
    ("greece", "Greece"),
    ("rom[ean]*", "Rome / Roman Empire"),
    ("europe", "Europe"),
    ("global", "Global"),
)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u000b", " ")).strip()


def parse_heading(raw: str, *, allow_indented_short_date: bool = False) -> tuple[int, int, str] | None:
    """Return absolute start/end year and title for a source heading."""
    # A handful of true 1000s headings carry one stray tab in the document
    # export.  Admit only indented four-or-five digit years; this still keeps
    # indented narrative/statistics lines out of the catalogue.
    if raw[:1].isspace() and not allow_indented_short_date and not re.match(r"^\s*\d{4,5}\b", raw):
        return None
    line = normalize(raw)
    line = re.sub(r"^[•*]\s*", "", line)
    line = re.sub(r"\b(BCE|BC|CE|AD)\s*[-–—]\s*", r"\1 ", line, flags=re.IGNORECASE)
    if line.casefold().startswith("if starting"):
        return None
    # Section labels and result-table rows share the same visual convention as
    # dated event headings in the Google Doc.  They are instructions, not
    # calendar entries.
    if re.match(r"^\d{3,4}s\s+note\b", line, re.IGNORECASE):
        return None
    match = YEAR_FIRST.match(line) or YEAR_IN_PARENS.match(line) or YEAR_SUFFIX.match(line)
    decade = DECADE_FIRST.match(line) if not match else None
    if decade:
        start = int(decade.group("start"))
        return start, start + 9, line
    if not match:
        return None
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    era = (match.group("era") or "").upper()
    name = normalize(match.group("name"))
    if not name or name.casefold().endswith("s") and name.casefold() in {"s"}:
        return None
    if name.casefold().startswith(("when:", "who rolls:")) or name.casefold() in {"when", "directly affected"}:
        return None
    # Result-table rows (for example “1 — One Sim dies”) are not dated
    # historical events, even though they begin with a number.
    if re.match(r"^(?:[-–—=]|nd\b|(?:and|or)\s+\d+\s*(?::|(?:means?|dies?|is)\b)|(?:means?|dies?|is)\b)", name, re.IGNORECASE):
        return None
    # A bare suffix date is useful for titles such as “Antonine Plague 165”
    # but values like “Set to 2” are rule prose, not years.
    if YEAR_SUFFIX.match(line) and not era and start < 100:
        return None
    if era in {"BCE", "BC"}:
        start, end = -start, -end
        if start > end:
            start, end = end, start
    elif end < start and len(str(end)) < len(str(start)):
        # Sources commonly abbreviate an AD range as “1180–85”.  Restore the
        # omitted leading century rather than treating it as the year 85.
        end = int(f"{str(start)[:len(str(start)) - len(str(end))]}{end}")
    # The 1200s source includes a few long-running events (for example the
    # Mongol invasion of India) whose approved end date reaches into the next
    # century, so do not accidentally drop them at an arbitrary 1300 cutoff.
    if not (-12000 <= start <= 1400 and -12000 <= end <= 1400):
        return None
    return start, end, line


def scope_for(text: str) -> str:
    value = text.casefold()
    if re.search(r"\b(?:plague|smallpox|pestilence|epidemic)\b", value):
        return "Disease / Epidemic"
    if re.search(r"\b(?:famine|drought|winter|thirst)\b", value):
        return "Famine / Disaster"
    if re.search(r"\b(?:battle|war|crusade|raid|invasion|siege|conquest|massacre|attack|campaign|revolt)\b", value):
        return "War / Conflict"
    if re.search(r"\b(?:earthquake|eruption|volcan|deluge|flood|fire|storm|meteorite)\b", value):
        return "Disaster"
    if re.search(r"\b(?:migrat|settlement|coloniz)\b", value):
        return "Migration / Settlement"
    return "Society / Technology"


def location_for(text: str) -> str:
    value = text.casefold()
    if "yōwa famine" in value:
        return "Japan"
    for needle, label in LOCATION_WORDS:
        if re.search(rf"\b{needle}\b", value):
            return label
    return "Global / See Notes"


def affected_for(text: str) -> str:
    value = text.casefold()
    age = re.search(r"\b(\d{1,2}\s*\+?)\s*(?:year[- ]old\s*)?(?:male|men|man)\b", value)
    if age:
        return f"Male Sims age {age.group(1).replace(' ', '')}"
    if re.search(r"\b(?:all|each)\s+(?:\w+\s+){0,3}(?:sims?|households?)\b", value):
        return "All Sims / Households"
    if re.search(r"\b(?:soldier|enlisted|conscripted|drafted)\b", value):
        return "Eligible Sims / Soldiers"
    return "Affected Local Sims"


def _catalogue_identity(source: str, start: int, end: int, title: str) -> str:
    normalized_title = re.sub(r"[^a-z0-9]+", "", title.casefold())
    return f"{source}|{start}|{end}|{normalized_title}"


def _source_catalogue_identity(source: str, start: int, end: int, title: str) -> str:
    """Match source headings to the older 1200s catalogue title convention."""
    source_title = re.sub(
        r"^\d{3,4}(?:\s*(?:[-–—]|to)\s*\d{1,4})?\s+",
        "",
        title,
        flags=re.IGNORECASE,
    )
    return _catalogue_identity(source, start, end, source_title)


def _source_title_identity(source: str, title: str) -> str:
    """Fallback identity for an event whose approved date range changed."""
    source_title = re.sub(
        r"^\d{3,4}(?:\s*(?:[-–—]|to)\s*\d{1,4})?\s+",
        "",
        title,
        flags=re.IGNORECASE,
    )
    normalized_title = re.sub(r"[^a-z0-9]+", "", source_title.casefold())
    return f"{source}|title|{normalized_title}"


def _previous_catalogue_ids() -> dict[str, str]:
    """Keep stable IDs when a source gains events between existing entries."""
    if not OUTPUT.exists():
        return {}
    try:
        encoded = re.search(
            r'EARLY_EVENT_LIBRARY_GZIP_BASE64\s*=\s*"([^"]+)"',
            OUTPUT.read_text(encoding="utf-8"),
        ).group(1)
        existing = json.loads(gzip.decompress(base64.b64decode(encoded)).decode("utf-8"))
    except (AttributeError, OSError, ValueError, json.JSONDecodeError):
        return {}
    return {
        _catalogue_identity(
            str(row.get("source") or ""),
            int((row.get("start_global_day") or 1) // 4 + 1200),
            int(((row.get("end_global_day") or 4) - 1) // 4 + 1200),
            str(row.get("event_name") or ""),
        ): str(row.get("event_id") or "")
        for row in existing
        if row.get("event_id")
    }


def _static_catalogue_ids() -> dict[str, str]:
    """Keep existing 1200s IDs when its source document is refreshed."""
    if not STATIC_CATALOGUE.exists():
        return {}
    try:
        encoded = re.search(
            r'EVENT_LIBRARY_GZIP_BASE64\s*=\s*"([^"]+)"',
            STATIC_CATALOGUE.read_text(encoding="utf-8"),
        ).group(1)
        existing = json.loads(gzip.decompress(base64.b64decode(encoded)).decode("utf-8"))
    except (AttributeError, OSError, ValueError, json.JSONDecodeError):
        return {}
    ids: dict[str, str] = {}
    for row in existing:
        if not row.get("event_id") or row.get("source") != SOURCES[-1][2]:
            continue
        source = str(row.get("source") or "")
        start = int((row.get("start_global_day") or 1) // 4 + 1200)
        end = int(((row.get("end_global_day") or 4) - 1) // 4 + 1200)
        title = str(row.get("event_name") or "")
        event_id = str(row["event_id"])
        ids[_source_catalogue_identity(source, start, end, title)] = event_id
        ids[_source_title_identity(source, title)] = event_id
    return ids


def _event_id(era: str, identity: str, previous: dict[str, str], used: set[str]) -> str:
    prior = previous.get(identity)
    if prior and prior not in used:
        used.add(prior)
        return prior
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12].upper()
    event_id = f"EVT-{era.upper()}-{digest}"
    used.add(event_id)
    return event_id


def _pre1000_event_start(lines: list[str]) -> int:
    """Skip setup/calendar prose and begin at the first dated BCE event."""
    for index, line in enumerate(lines):
        if re.match(r"^\s*\d{4,5}\s*(?:BCE|BC)\b", line, re.IGNORECASE):
            return index
    return 0


def _source_says_roll(notes: str) -> bool:
    return bool(re.search(
        r"\b(?:flip\s+(?:a\s+)?coin|roll\s+(?:an?\s+)?(?:d\d+|once))\b",
        notes,
        re.IGNORECASE,
    ))


def catalogue_rows() -> list[dict]:
    rows: list[dict] = []
    previous_ids = {**_static_catalogue_ids(), **_previous_catalogue_ids()}
    used_ids: set[str] = set()
    for filename, era, source in SOURCES:
        raw_lines = (SOURCE_DIRECTORY / filename).read_text(encoding="utf-8-sig").splitlines()
        start_index = _pre1000_event_start(raw_lines) if era == "pre1000" else 0
        headings = [
            (index, parsed)
            for index, line in enumerate(raw_lines)
            if index >= start_index and (parsed := parse_heading(
                line, allow_indented_short_date=(era == "pre1000"),
            ))
        ]
        for position, (index, (start, end, title)) in enumerate(headings):
            next_index = headings[position + 1][0] if position + 1 < len(headings) else len(raw_lines)
            notes = normalize(" ".join(normalize(line) for line in raw_lines[index + 1:next_index] if normalize(line)))
            detail = f"{title} {notes}"
            identity = _source_catalogue_identity(source, start, end, title) if era == "1200s" else _catalogue_identity(source, start, end, title)
            if era == "1200s" and identity not in previous_ids:
                legacy_id = previous_ids.get(_source_title_identity(source, title))
                if legacy_id:
                    previous_ids[identity] = legacy_id
            rows.append({
                "event_id": _event_id(era, identity, previous_ids, used_ids),
                "start_global_day": (start - 1200) * 4 + 1,
                "end_global_day": (end - 1200) * 4 + 4,
                "event_name": title,
                "scope": scope_for(detail),
                "location": location_for(detail),
                "roll_required": int(_source_says_roll(notes)),
                "affected_class": affected_for(detail),
                "active": 1,
                "source": source,
                "notes": notes or None,
            })
    return rows


def write() -> None:
    rows = catalogue_rows()
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = base64.b64encode(gzip.compress(payload)).decode("ascii")
    OUTPUT.write_text(
        '"""Compressed source-refreshed historical-event catalogue from approved documents."""\n\n'
        f'EARLY_EVENT_LIBRARY_GZIP_BASE64 = "{compressed}"\n',
        encoding="utf-8",
    )
    print(f"Wrote {len(rows)} events to {OUTPUT}")


if __name__ == "__main__":
    write()
