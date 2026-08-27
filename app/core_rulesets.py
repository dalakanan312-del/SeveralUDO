from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ChronicleSave, Record


SEVERALUDO = "severaludo"
MORBID = "morbid_ultimate"
CLASSIC_2023 = "classic_decades_2023"

CORE_RULESETS = (
    {
        "id": SEVERALUDO,
        "name": "SeveralUDO Ultimate Decades Overhaul",
        "description": "The tracker’s existing core mortality, family, event, and household rules.",
        "start_year": 1200,
        "source": "https://sites.google.com/view/severaludo/home",
    },
    {
        "id": MORBID,
        "name": "Morbid’s Ultimate Decades Challenge",
        "description": "Four-days-per-year play beginning in 1300, with century-specific mortality, marriage, pregnancy, and event tables.",
        "start_year": 1300,
        "source": "https://docs.google.com/document/d/1VKVvnDblpT2ngUs9JE9VcFyfhK9ZKXyqrYYaSun1pl4/edit",
    },
    {
        "id": CLASSIC_2023,
        "name": "Classic Decades Challenge — 2023 rules",
        "description": "The 1890s–2010s CuteCoffeeGal rules: manual historical aging and sourced war rolls, without SeveralUDO mortality rolls.",
        "start_year": 1890,
        "source": "DecadesChallengeRules06.13.2023.pdf",
    },
)

CORE_IDS = {item["id"] for item in CORE_RULESETS}


def selected_core(save: ChronicleSave) -> str:
    settings = save.settings or {}
    configured = str(settings.get("core_ruleset_id") or "").strip()
    if configured in CORE_IDS:
        return configured
    # Preserve the old checkbox selector when opening a save created before the
    # one-core-ruleset model existed.
    if "morbidgamer" in (settings.get("selected_rule_packs") or []):
        return MORBID
    return SEVERALUDO


def applies_to_selected_core(save: ChronicleSave, record: Record) -> bool:
    pack = str((record.data or {}).get("core_ruleset_id") or "").strip()
    return not pack or pack == selected_core(save)


# Morbid's linked era documents use fixed Sim-day ages. Each tuple is:
# (stage label, age offset in Sim days, die, fatal results).
MORBID_MORTALITY_ERAS = (
    (1300, 1399, "1300s — The Middle Ages", (
        ("Babies", 0, "d20", "5,10,15,20"), ("Infant", 1, "d20", "12,16,18"),
        ("Toddler", 6, "d20", "4,8,12"), ("Child", 24, "d20", "9,19"),
        ("Teen", 52, "d20", "7"), ("Young Adult", 80, "d20", "2,6,11,13,14"),
        ("Adult", 120, "d20", "1-8"), ("Adult End of Life", 160, "d10", ""),
    )),
    (1400, 1499, "1400s — End of the Middle Ages", (
        ("Babies", 0, "d20", "1,5,10,15"), ("Infant", 1, "d20", "18,20"),
        ("Toddler", 6, "d20", "4,8,12"), ("Child", 24, "d20", "9,19"),
        ("Teen", 52, "d20", "7"), ("Young Adult", 80, "d20", "2,6,11,13,14"),
        ("Adult", 120, "d20", "1-8"), ("Adult Pt. 2", 160, "d20", "1-9"),
        ("Elder End of Life", 200, "d8", ""),
    )),
    (1500, 1619, "1500–1619 — Tudor Renaissance", (
        ("Babies", 0, "d20", "5,10,15,20"), ("Infant", 1, "d20", "8,16"),
        ("Toddler", 6, "d20", "7,14,19"), ("Child", 24, "d20", "2,4,6"),
        ("Teen", 52, "d20", "7,13"), ("Young Adult", 80, "d20", "3,18"),
        ("Adult", 120, "d20", "1-6"), ("Adult Pt. 2", 160, "d20", "1-7"),
        ("Elder End of Life", 200, "d20", ""),
    )),
    (1620, 1699, "1620–1699 — Early Colonial Period", (
        ("Babies", 0, "d20", "5,10,15,20"), ("Infant", 1, "d20", "12,16,18"),
        ("Toddler", 6, "d20", "4,8,17"), ("Child", 20, "d20", "13,19"),
        ("Teen", 52, "d20", "7,14"), ("Young Adult", 80, "d20", "3,9,20"),
        ("Adult", 120, "d20", "2,6,11,15"), ("Adult Pt. 2", 160, "d20", "1-8"),
        ("Elder End of Life", 200, "d20", ""),
    )),
    (1700, 1762, "1700–1762 — Late Colonial America", (
        ("Babies", 0, "d20", "5,10,15"), ("Infant", 1, "d20", "12,16,20"),
        ("Toddler", 6, "d20", "7,13,17"), ("Child", 24, "d20", "8,19"),
        ("Teen", 52, "d20", "3"), ("Young Adult", 80, "d20", "6,14"),
        ("Adult", 120, "d20", "4,16"), ("Adult Pt. 2", 160, "d20", "1-7"),
        ("Elder End of Life", 200, "d20", ""),
    )),
    (1763, 1815, "1763–1815 — Revolutionary America", (
        ("Babies", 0, "d20", "5,10,15"), ("Infant", 6, "d20", "2,8"),
        ("Toddler", 11, "d20", "6,14,17"), ("Child", 29, "d20", "3,7,10"),
        ("Teen", 57, "d20", "11,16"), ("Young Adult", 85, "d20", "9,19"),
        ("Adult", 125, "d20", "4,14"), ("Adult Pt. 2", 165, "d20", "1-7"),
        ("End of Life", 205, "d20", ""),
    )),
)

MORBID_PLANNER_ERAS = (
    # start, end, marriage failure, pregnancy die, no-child results
    (1300, 1399, "1-3", "d12", "1"),
    (1400, 1499, "1-4", "d12", "1"),
    (1500, 1619, "1-5", "d10", "10"),
    (1620, 1699, "1-6", "d10", "10"),
    (1700, 1815, "1-7", "d10", "10"),
)

CLASSIC_DECADE_GUIDANCE = (
    (1890, 1899, "1890s — Eve of the 20th Century", "Male-heir succession; strict marriage, education, career, household, technology, medical, travel, and Off-the-Grid limits. Births occur at home."),
    (1900, 1909, "1900s — Edwardian Era", "Electric lamps, indoor plumbing, upholstery, wallpaper, phonographs, vacations, and an expanded career list become available."),
    (1910, 1919, "1910s — World War I and suffrage", "Eligible male Teens through Adults are drafted. Use the odd/even war-survival roll; survivors receive a trauma trait. Suffrage, school, career, and building rules change."),
    (1920, 1929, "1920s — Roaring Twenties and Prohibition", "Women may inherit; additional entertainment, school, work, holiday, grooming, and building options open, while alcohol is prohibited."),
    (1930, 1939, "1930s — Great Depression", "Apply unemployment, hardship traits, reduced household funds at added difficulty, delayed bills, food and painting limits, pensions, and revised work options."),
    (1940, 1949, "1940s — World War II", "Young Adult men at the decade start are drafted and use odd/even survival. Victory gardens, radio use, ration-style meals, war work, medicine, hospital births, veterinary care, and modern appliances expand."),
    (1950, 1959, "1950s — Red Scare and Korean War", "The oldest son and daughter serve in the Korean War and use odd/even survival. Divorce, adoption, compulsory school, television, showers, formula, nannies, and broader careers become available."),
    (1960, 1969, "1960s — Civil rights and counterculture", "The first two children reaching Young Adult serve in Vietnam and use odd/even survival. Interracial marriage, same-sex relationships, non-procreative WooHoo, loans, broader careers, and new technology are allowed."),
    (1970, 1979, "1970s — Feminism and environmentalism", "Complete any remaining Vietnam draft, apply survivor trauma and environmental traits, and introduce Eco rules, solar power, modern appliances, festivals, Pride items, and Earth Day."),
    (1980, 1989, "1980s — The Yuppies", "Apply decade birth traits; computers, consoles, expanded television hours, fitness equipment, additional careers, hospital births, science babies, and modern holidays become available."),
    (1990, 1999, "1990s — Globalization", "Lift most household restrictions, expand computers and media, allow paternity leave, prepare and shelter for Y2K, and add texting, play dates, tablets, and other modern conveniences."),
    (2000, 2009, "2000s — New Millennium", "Lift computer, build/buy, music, and television restrictions; broaden technology and birth locations while retaining the source's remaining career limits."),
    (2010, 2019, "2010s — Where do we go from here?", "Allow same-sex marriage, nearly all careers and part-time work, drones, Trendi, and other contemporary technology."),
    (2020, 9999, "2020s — Bonus Round", "Allow women in Covert Operator and optionally play the source's one-year-or-longer pandemic, lockdown, remote work, online school, isolation, and illness rules."),
)


GUIDANCE = (
    (MORBID, "Morbid — Time and aging", 1300, 1815,
     "Use four Sim days per historical year and the fixed Sim-day life-stage tables. The linked source becomes incomplete after 1815; no missing roll values are invented."),
    (MORBID, "Morbid — Main and side households", 1300, 1815,
     "Heirs use the source pregnancy percentages. Non-heirs use the era pregnancy-attempt die and the side-household marriage table."),
    (MORBID, "Morbid — Birth mortality", 1300, 1815,
     "Roll once for the birthing Sim and once for every baby; repeat the birthing roll for each additional twin or triplet."),
    (MORBID, "Morbid — Event rules", 1300, 1815,
     "Use the source's dated famine, plague, war, epidemic, tax, migration, and technology instructions. Historical Events remains a separately selectable add-on."),
    (CLASSIC_2023, "Classic 2023 — Time and aging", 1890, 2019,
     "Each Sim day equals six months and every two Sim days advance one year. Manually age Sims using the source's pre-1950 and post-1950 lifespan schedules."),
    (CLASSIC_2023, "Classic 2023 — Core scope", 1890, 2019,
     "Follow the decade-by-decade career, education, finance, relationship, holiday, technology, and household restrictions in the June 13, 2023 rules."),
    (CLASSIC_2023, "Classic 2023 — War rolls", 1890, 2019,
     "For sourced war service, eligible Sims eat the Cowplant cake once, then roll: odd results die and even results return home. Era-specific draft eligibility still comes from the decade rules."),
    (CLASSIC_2023, "Classic 2023 — Mortality model", 1890, 2019,
     "This source does not define SeveralUDO-style routine mortality rolls. Selecting it therefore pauses SeveralUDO lifecycle and maternal tables unless you add custom rules."),
) + tuple(
    (CLASSIC_2023, label, start, end, text)
    for start, end, label, text in CLASSIC_DECADE_GUIDANCE
)


def _identity(record: Record) -> str:
    return str((record.data or {}).get("source_rule_id") or "")


def sync_rules(session: Session, save: ChronicleSave) -> int:
    """Install editable alternatives without deleting or overwriting user rules."""
    created = 0
    records = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.deleted.is_(False),
        Record.kind.in_(("roll_rule", "planner_rule", "era_guidance")),
    )))
    existing = {_identity(item) for item in records if _identity(item)}

    # Tag only unmistakable built-in SeveralUDO defaults. Untagged imported and
    # custom records continue to apply under every core ruleset.
    builtin_stage_names = {
        "being born", "newborn", "infant", "toddler", "child", "preteen",
        "teen", "young adult", "adult", "elder death-age rng",
    }
    for item in records:
        data = dict(item.data or {})
        if data.get("core_ruleset_id"):
            continue
        built_in_roll = item.kind == "roll_rule" and (
            item.label.casefold() in builtin_stage_names
            or str(data.get("source") or "").casefold() == "built-in maternal baseline"
        )
        built_in_planner = item.kind == "planner_rule" and str(data.get("notes") or "") in {
            "Annual pregnancy-count roll", "Marriage eligibility for a non-heir",
        }
        built_in_guidance = item.kind == "era_guidance" and str(data.get("source") or "") == "Built-in editable baseline"
        if built_in_roll or built_in_planner or built_in_guidance:
            item.data = {**data, "core_ruleset_id": SEVERALUDO}

    for start, end, era, rows in MORBID_MORTALITY_ERAS:
        for stage, age_days, die, bad in rows:
            source_id = f"morbid:mortality:{start}:{stage.casefold()}"
            if source_id in existing:
                continue
            data = {
                "core_ruleset_id": MORBID, "source_rule_id": source_id,
                "source_name": era, "start_year": start, "end_year": end,
                "stage_key": stage.casefold(), "age_days": age_days,
                "die": die, "bad_results": bad, "active": True,
                "death_age_rng": "end of life" in stage.casefold(),
                "notes": "Editable transcription of Morbid’s linked era table.",
            }
            item = Record(save_id=save.id, kind="roll_rule", label=f"{stage} · {era}", data=data)
            session.add(item); session.flush(); created += 1; existing.add(source_id)
        maternal_id = f"morbid:maternal:{start}"
        if maternal_id not in existing:
            item = Record(save_id=save.id, kind="roll_rule", label=f"Maternal — All Ages · {era}", data={
                "core_ruleset_id": MORBID, "source_rule_id": maternal_id,
                "source_name": era, "start_year": start, "end_year": end,
                "age_days": None, "die": "d20", "bad_results": "1", "active": True,
                "notes": "Birth roll for the birthing Sim; repeat for each additional baby.",
            })
            session.add(item); session.flush(); created += 1; existing.add(maternal_id)

    for start, end, marriage_bad, pregnancy_die, no_children in MORBID_PLANNER_ERAS:
        definitions = (
            (f"morbid:marriage:{start}", "Non-Heir Marriage Eligibility", "d20",
             f"{marriage_bad}: Does not marry; all other results: May marry", "non_heir_marriage"),
            (f"morbid:pregnancy:{start}", "Side Household Pregnancy", pregnancy_die,
             f"{no_children}: No pregnancy; all other results: Schedule that many pregnancies", "side_pregnancy"),
        )
        for source_id, label, die, result_rules, rule_key in definitions:
            if source_id in existing:
                continue
            item = Record(save_id=save.id, kind="planner_rule", label=f"{label} · {start}–{end}", data={
                "core_ruleset_id": MORBID, "source_rule_id": source_id, "rule_key": rule_key,
                "start_year": start, "end_year": end, "die": die,
                "bad_results": result_rules, "active": True,
                "notes": "Editable transcription of Morbid’s side-household table.",
            })
            session.add(item); session.flush(); created += 1; existing.add(source_id)

    for pack, label, start, end, text in GUIDANCE:
        source_id = f"{pack}:guidance:{label.casefold()}"
        if source_id in existing:
            continue
        item = Record(save_id=save.id, kind="era_guidance", label=label, data={
            "core_ruleset_id": pack, "source_rule_id": source_id,
            "start_year": start, "end_year": end, "category": "Core ruleset",
            "location": "Source-defined", "rule_text": text, "active": True,
        })
        session.add(item); session.flush(); created += 1; existing.add(source_id)
    return created


def current_catalog_entry(save: ChronicleSave) -> dict:
    core = selected_core(save)
    return next(item for item in CORE_RULESETS if item["id"] == core)
