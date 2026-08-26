from __future__ import annotations

import json
import random
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select

from .models import Record


MEDIEVAL_PREFIX = "Medieval — "
MEDIEVAL_LIBRARY = Path(__file__).with_name("medieval_names.json")


@lru_cache(maxsize=1)
def _medieval_payload() -> dict:
    """Load the bundled source workbook once per app process."""
    try:
        return json.loads(MEDIEVAL_LIBRARY.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


@lru_cache(maxsize=1)
def medieval_libraries() -> dict:
    """Return immutable pools built from the bundled medieval-name worksheet."""
    cultures = (_medieval_payload().get("cultures") or {})
    return {
        f"{MEDIEVAL_PREFIX}{culture}": {
            "Male": {"first": tuple(groups.get("Male") or ())},
            "Female": {"first": tuple(groups.get("Female") or ())},
            "Any": {"surname": tuple(groups.get("Surname") or ())},
        }
        for culture, groups in cultures.items()
        if isinstance(groups, dict)
    }


def medieval_summary() -> dict:
    payload = _medieval_payload()
    cultures = payload.get("cultures") or {}
    first_names = sum(
        len(groups.get("Male") or ()) + len(groups.get("Female") or ())
        for groups in cultures.values()
    )
    surnames = sum(len(groups.get("Surname") or ()) for groups in cultures.values())
    source = payload.get("source") or {}
    return {
        "cultures": len(cultures),
        "first_names": first_names,
        "surnames": surnames,
        "total_names": first_names + surnames,
        "total_names_display": f"{first_names + surnames:,}",
        "source_title": str(source.get("title") or "Decades Names"),
        "source_sheet": str(source.get("sheet") or "medieval names"),
        "source_url": str(source.get("url") or ""),
    }


def libraries(session, save_id: str) -> dict:
    """Return bundled, explicit-source, and current-save name libraries."""
    rows = list(session.scalars(select(Record).where(
        Record.save_id == save_id, Record.kind == "name_entry", Record.deleted.is_(False),
    )))
    custom: dict[str, dict[str, dict[str, list[str]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in rows:
        data = row.data or {}
        culture = str(data.get("culture") or "Uncategorized").strip()
        sex = str(data.get("sex") or "Any").strip()
        kind = str(data.get("name_kind") or "first").strip().casefold()
        if kind not in {"first", "surname"}:
            continue
        value = str(data.get("name") or row.label or "").strip()
        if value and value not in custom[culture][sex][kind]:
            custom[culture][sex][kind].append(value)
    sims = session.scalars(select(Record).where(
        Record.save_id == save_id, Record.kind == "sim", Record.deleted.is_(False),
    ))
    for sim in sims:
        data = sim.data or {}; sex=str(data.get("sex") or "Any").strip()
        first=str(data.get("first_name") or "").strip();last=str(data.get("last_name") or "").strip()
        if first and first not in custom["Names already recorded in this save"][sex]["first"]:
            custom["Names already recorded in this save"][sex]["first"].append(first)
        if last and last not in custom["Names already recorded in this save"]["Any"]["surname"]:
            custom["Names already recorded in this save"]["Any"]["surname"].append(last)

    # Keep the large built-in lists immutable and shared; only overlapping custom
    # collections allocate merged lists. This avoids rebuilding 49k names on each page.
    pool = {
        culture: {sex: dict(kinds) for sex, kinds in by_sex.items()}
        for culture, by_sex in medieval_libraries().items()
    }
    for culture, by_sex in custom.items():
        target = pool.setdefault(culture, {})
        for sex, kinds in by_sex.items():
            target_kinds = target.setdefault(sex, {})
            for kind, values in kinds.items():
                existing = target_kinds.get(kind, ())
                seen = {name.casefold() for name in existing}
                merged = list(existing)
                for name in values:
                    key = name.casefold()
                    if key not in seen:
                        seen.add(key)
                        merged.append(name)
                target_kinds[kind] = tuple(merged)
    return pool


def library_names(session, save_id: str, *, include_recorded: bool = True) -> list[str]:
    """Return dropdown labels without copying the 49k-name bundled library."""
    cultures = {
        f"{MEDIEVAL_PREFIX}{culture}"
        for culture, groups in ((_medieval_payload().get("cultures") or {}).items())
        if isinstance(groups, dict)
    }
    custom = session.scalars(select(Record.data["culture"].as_string()).where(
        Record.save_id == save_id,
        Record.kind == "name_entry",
        Record.deleted.is_(False),
    ).distinct())
    for value in custom:
        cultures.add(str(value or "Uncategorized").strip() or "Uncategorized")
    if include_recorded:
        cultures.add("Names already recorded in this save")
    return sorted(cultures, key=str.casefold)


def coverage(pool: dict) -> list[dict]:
    result=[]
    for culture, by_sex in sorted(pool.items()):
        first=sum(len(values.get("first",())) for values in by_sex.values())
        surnames=len({name for values in by_sex.values() for name in values.get("surname",())})
        result.append({"culture":culture,"first":first,"surnames":surnames,"sexes":sorted(by_sex),"built_in":culture.startswith(MEDIEVAL_PREFIX)})
    return result


def generate(pool: dict, culture: str, sex: str, count: int = 5, *,
             surname_culture: str = "", no_surname: bool = False) -> list[dict]:
    rng=random.SystemRandom();count=max(1,min(20,int(count or 5)))
    chosen=pool.get(culture) or {}
    sex_groups=list(chosen) if sex == "Any" else [sex,"Any"]
    first=list(dict.fromkeys(name for group in sex_groups for name in (chosen.get(group) or {}).get("first",())))
    surname_source=pool.get(surname_culture or culture) or {}
    surnames=list(dict.fromkeys(name for values in surname_source.values() for name in values.get("surname",())))
    if not first:
        return []
    suggestions=[];seen=set()
    attempts=max(30,count*12)
    for _ in range(attempts):
        given=rng.choice(first);surname="" if no_surname or not surnames else rng.choice(surnames)
        key=(given,surname)
        if key in seen: continue
        seen.add(key);suggestions.append({"first_name":given,"last_name":surname,"full_name":" ".join(value for value in (given,surname) if value),"sex":sex,"culture":culture,"surname_culture":surname_culture or culture})
        if len(suggestions)>=count: break
    return suggestions
