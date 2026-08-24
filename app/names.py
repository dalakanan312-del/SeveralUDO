from __future__ import annotations

import random
from collections import Counter, defaultdict

from sqlalchemy import select

from .models import Record


def libraries(session, save_id: str) -> dict:
    """Return explicit source entries plus clearly labelled names from this save."""
    rows = list(session.scalars(select(Record).where(
        Record.save_id == save_id, Record.kind == "name_entry", Record.deleted.is_(False),
    )))
    pool: dict[str, dict[str, dict[str, list[str]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in rows:
        data = row.data or {}
        culture = str(data.get("culture") or "Uncategorized").strip()
        sex = str(data.get("sex") or "Any").strip()
        kind = str(data.get("name_kind") or "first").strip().casefold()
        if kind not in {"first", "surname"}:
            continue
        value = str(data.get("name") or row.label or "").strip()
        if value and value not in pool[culture][sex][kind]:
            pool[culture][sex][kind].append(value)
    sims = session.scalars(select(Record).where(
        Record.save_id == save_id, Record.kind == "sim", Record.deleted.is_(False),
    ))
    for sim in sims:
        data = sim.data or {}; sex=str(data.get("sex") or "Any").strip()
        first=str(data.get("first_name") or "").strip();last=str(data.get("last_name") or "").strip()
        if first and first not in pool["Names already recorded in this save"][sex]["first"]:
            pool["Names already recorded in this save"][sex]["first"].append(first)
        if last and last not in pool["Names already recorded in this save"]["Any"]["surname"]:
            pool["Names already recorded in this save"]["Any"]["surname"].append(last)
    return pool


def coverage(pool: dict) -> list[dict]:
    result=[]
    for culture, by_sex in sorted(pool.items()):
        first=sum(len(values.get("first",())) for values in by_sex.values())
        surnames=len({name for values in by_sex.values() for name in values.get("surname",())})
        result.append({"culture":culture,"first":first,"surnames":surnames,"sexes":sorted(by_sex)})
    return result


def generate(pool: dict, culture: str, sex: str, count: int = 5, *,
             surname_culture: str = "", no_surname: bool = False) -> list[dict]:
    rng=random.SystemRandom();count=max(1,min(20,int(count or 5)))
    chosen=pool.get(culture) or {}
    first=list(dict.fromkeys(list((chosen.get(sex) or {}).get("first",())) + list((chosen.get("Any") or {}).get("first",()))))
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
