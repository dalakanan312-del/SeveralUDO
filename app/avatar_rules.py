from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ChronicleSave, Record


PACK_ID = "avatar_decades"
PACK_NAME = "Avatar: The Last Airbender Decades"

# code, title, category, default, die, trigger, rule text
_MODULE_ROWS = (
    ("ATLA-01", "Starting Nation", "Identity and ancestry", "Recommended", "", "Every founding household", "Assign Water Tribe, Earth Kingdom, Fire Nation, or Air Nomads. Cultural nation follows upbringing; potential element follows biological ancestry."),
    ("ATLA-02", "Bender Birth Roll", "Birth outcome", "Recommended", "d4", "Birth with bending ancestry", "One Bender parent: 1-2 Bender. Same-element Bender parents: 1-3 Bender. Two Non-Benders with bending ancestry: 1 Bender. Two traditional Air Nomads use ATLA-25."),
    ("ATLA-03", "Bending Element", "Birth outcome", "Recommended", "d2", "Child receives Bender result", "A natural Bender receives only an ancestral element. Same-nation parents assign it automatically; mixed elements use 1 maternal, 2 paternal. The Avatar is the exception."),
    ("ATLA-04", "Bending Discovery", "Childhood development", "Recommended", "", "Ages 4-7", "Bending may manifest from age 4 and must be revealed by age 7. A Non-Bender does not develop ordinary bending later."),
    ("ATLA-05", "Natural Bending Strength", "Bending development", "Recommended", "d6", "Bending first manifests", "1 Weak; 2-3 Average; 4-5 Strong; 6 Prodigy. Strength is aptitude, not mastery. A Prodigy gets one final reroll on an advanced-technique test."),
    ("ATLA-06", "Avatar Cycle", "Avatar succession", "Recommended", "", "Avatar death", "Only one living Avatar. Succession follows Water, Earth, Fire, Air and requires a birth after the prior Avatar dies. A selected child is automatically a Bender."),
    ("ATLA-07", "Avatar Identification", "Avatar development", "Optional", "", "Current Avatar", "Identity may remain secret at birth and normally becomes public at 16; second-element use, spiritual activity, war, authority recognition, or another extraordinary event may reveal it earlier."),
    ("ATLA-08", "Avatar Element Training", "Education", "Recommended", "", "Current Avatar", "Train the three non-native elements, preferably in cycle order, with an appropriate teacher and at least one Sim year per element. Fully Realized requires all four."),
    ("ATLA-09", "Bending Mastery", "Bending development", "Recommended", "", "All Benders", "Ranks are Untrained, Novice, Trained, Advanced, and Master. Training, teachers, skill, experience, and achievements raise rank; natural strength does not replace training."),
    ("ATLA-10", "Avatar State Protection", "Death prevention", "Optional", "d6", "Certain combat, attack, accident, or disaster death", "Unmastered: 1-2 cancels death. Mastered: 1-3 cancels death. One attempt per event; not normally available for age or ordinary disease."),
    ("ATLA-11", "Death in the Avatar State", "Avatar succession", "Optional", "d4", "Failed Avatar State protection", "1 dies in the Avatar State and permanently breaks the cycle; 2-4 dies normally and the cycle continues."),
    ("ATLA-12", "Waterbending Environment", "Elemental modifier", "Optional", "", "Waterbender activity", "Waterbenders need accessible or carried water. Dry locations hinder use; water travel, fishing, floods, collection, and trained healing may receive an advantage."),
    ("ATLA-13", "Moon and Waterbending", "Temporary modifier", "Optional", "", "Full moon or lunar eclipse", "A full moon grants one final reroll on a failed bending roll. During a lunar eclipse Waterbending is unavailable, while ordinary actions remain available."),
    ("ATLA-14", "Waterbending Healing Aptitude", "Advanced ability", "Optional", "d6", "Waterbending manifests", "1-2 grants natural aptitude; with a trained healer parent or grandparent 1-3 succeeds. Without natural aptitude a Sim may learn basics but not become a Master Healer."),
    ("ATLA-15", "Waterbending Death Intervention", "Death prevention", "Optional", "d6", "Injury or illness death", "A Trained Healer saves on 1; a Master Healer saves on 1-2. One attempt per dying Sim per event; never prevents old-age death."),
    ("ATLA-16", "Healing During Childbirth", "Maternal mortality", "Optional", "d6", "Maternal death with healer present", "A Trained Healer saves on 1; a Master Healer on 1-2. Infant death is rerolled only when reasonably treatable as injury or illness."),
    ("ATLA-17", "Bloodbending", "Rare advanced ability", "Optional", "d20", "Waterbender reaches Master", "Standard aptitude succeeds on 1; Bloodbender parent or grandparent succeeds on 1-2. Aptitude still requires discovery or training and normally a full moon."),
    ("ATLA-18", "Earthbending Benefits", "Elemental modifier", "Optional", "", "Earthbender activity", "May advantage construction, mining, farming, defense, manual labor, warfare, stonework, or earthworks without automatically preventing injury."),
    ("ATLA-19", "Metalbending", "Rare advanced ability", "Optional", "d10", "Advanced or Master Earthbender after discovery", "Standard aptitude succeeds on 1-2; Metalbender parent or grandparent on 1-3. Training and a living teacher are still required."),
    ("ATLA-20", "Lavabending and Seismic Sense", "Rare advanced abilities", "Optional", "dynamic", "Advanced or Master Earthbender", "Lavabending: 1 on d20, or 1-2 with family ancestry. Seismic Sense: Master succeeds on 1 on d12; blindness and a skilled parent or teacher improve the range."),
    ("ATLA-21", "Firebending Benefits and Accidental Fire", "Elemental modifier", "Optional", "d6", "Serious loss of control", "Firebenders need no external flame and may gain contextual advantages. For a young or inexperienced Firebender, 1 causes an accidental fire; 2-6 does not."),
    ("ATLA-22", "Comet and Solar Eclipse", "Temporary modifier", "Optional", "", "Comet or solar eclipse", "Sozin's Comet grants one final reroll on failed combat rolls and increases fire severity. A solar eclipse disables Firebending, but not ordinary combat."),
    ("ATLA-23", "Lightning Generation and Redirection", "Advanced Firebending", "Optional", "dynamic", "Advanced or Master Firebender", "Generation succeeds on 1 on d10, or 1-2 with ancestry. Redirection after training succeeds on 1-2 on d6, or 1-3 with a Master; retry after another full year."),
    ("ATLA-24", "Combustionbending", "Extremely rare ability", "Optional", "dynamic", "Advanced or Master Firebender", "Aptitude needs two 1s on d20, or one 1 with ancestry. Each training year, 1 on d10 causes an accident resolved by d6: death, disability, scarring, or recovery."),
    ("ATLA-25", "Air Nomad Bending", "Birth outcome", "Recommended", "", "Child with Air Nomad parents", "Two traditional Air Nomad parents produce an Airbender automatically. With only one Air Nomad parent, use the mixed-nation rules."),
    ("ATLA-26", "Air Nomad Lifestyle and Tattoos", "Culture and mastery", "Optional", "", "Traditional Air Nomads", "Communal care, travel, few possessions, and non-parent caregivers are normal. Only Master Airbenders earn tattoos."),
    ("ATLA-27", "Flight and Spiritual Projection", "Rare Airbending abilities", "Optional", "dynamic", "Eligible Airbender", "Unaided Flight: a Master who releases major attachments gets one d20 attempt, succeeding on 1. Spiritual Projection succeeds on 1 on d12, or 1-2 with a teacher."),
    ("ATLA-28", "Bending Teachers and Lost Techniques", "Education dependency", "Recommended", "", "Advanced technique", "An appropriate practitioner or institution must teach an unfamiliar technique. If every living practitioner dies without teaching it, mark it Lost until rediscovered."),
    ("ATLA-29", "Advanced Ability Ancestry", "Advanced ability modifier", "Optional", "d20", "Heritable bending aptitude", "When an aptitude normally succeeds only on 1 on d20, a parent or grandparent with it expands success to 1-2 unless its module says otherwise. Training remains required."),
    ("ATLA-30", "Spiritual Sensitivity", "Birth outcome", "Optional", "d12", "Every birth", "Standard child: 1 is Spiritually Gifted. Air Nomad child: 1-3. Sensitivity supports spiritual experiences and techniques but does not grant bending."),
    ("ATLA-31", "Spirit Encounters and Possession", "Supernatural event", "Optional", "dynamic", "Major Spirit encounter", "Disposition on d6: 1-2 benevolent, 3-4 neutral, 5-6 hostile. After hostility, 1 on d10 causes possession; annual d4 or trained spiritual removal can end it."),
    ("ATLA-32", "Entering the Spirit World", "Supernatural travel", "Optional", "dynamic", "Spirit World travel", "Ordinary Sims become trapped on 1 on d10; Spiritually Gifted Sims on 1 on d20. The Avatar is exempt during ordinary spiritual meditation."),
    ("ATLA-33", "Spirit-Touched Children", "Birth outcome", "Optional", "d6", "Child of human and human-form Spirit", "1-3 ordinary human; 4-5 Spiritually Gifted; 6 Spirit-Touched. Spirit ancestry may affect appearance, lifespan, or spiritual ability but not bending."),
    ("ATLA-34", "Animal Companions", "Childhood event", "Optional", "d10", "Sim becomes Child", "1 grants a lifelong companion. At the bonded Sim's death, 1 on d4 transfers the bond to family; 2-4 allows no new permanent bond."),
    ("ATLA-35", "Sky Bison and Dragons", "Special animal bond", "Optional", "dynamic", "Eligible Air Nomad or Master Firebender", "Traditional Air Nomad children bond with a Sky Bison on 1-3 on d4. A Master Firebender is accepted by an available dragon on 1 on d12."),
    ("ATLA-36", "Bending and Inheritance", "Inheritance", "Recommended", "", "Family succession", "Bending does not normally determine inheritance. A recorded Bender-preference custom may do so, but must be applied consistently across generations."),
    ("ATLA-37", "Fire Nation Heirs and Honor", "Fire Nation society", "Optional", "d4", "Noble or military succession conflict", "When a first heir is a Non-Bender and a younger sibling a Firebender, 1 replaces the heir. Track honor from Highly Honored through Disgraced and apply social consequences."),
    ("ATLA-38", "Agni Kai", "Duel", "Optional", "d6", "Firebender loses duel", "1 death; 2 permanent severe burns/scarring; 3-4 serious injury; 5-6 defeat without permanent injury. Surrender may be allowed."),
    ("ATLA-39", "Earth Kingdom Family Rules", "Earth Kingdom society", "Optional", "", "Earth Kingdom family", "Inheritance follows regional culture, class, and custom; Non-Benders are not automatically disqualified. Track land and distinct regional traditions."),
    ("ATLA-40", "Water Tribe Community Rules", "Water Tribe society", "Optional", "", "Water Tribe household", "Communal households place orphans with grandparents, aunts/uncles, adult siblings, then Tribe members. Healing families use ATLA-14's improved roll."),
    ("ATLA-41", "Bending in Combat", "Combat modifier", "Optional", "d6", "Bender receives death result against ordinary Non-Bender", "1-2 prevents death. No advantage against another Bender, an Exceptional Fighter, or while bending is unavailable. One attempt per event."),
    ("ATLA-42", "Bending Injuries and Disability", "Injury", "Optional", "d10", "Serious bending accident", "1 causes permanent injury; 2-10 eventual recovery. Disability does not remove bending, and technique should adapt to the Sim."),
    ("ATLA-43", "Non-Bender Opportunities", "Social and career rules", "Recommended", "", "Non-Bender", "Non-Benders retain full career, leadership, inheritance, family, and spiritual possibilities. An Exceptional Fighter removes the ordinary Bender combat advantage."),
    ("ATLA-44", "General War Outcomes", "War", "Optional", "d6", "Named military participant after major event", "1-2 death; 3 captured; 4 seriously wounded; 5 missing; 6 returns safely. Resolve injuries and disability normally."),
    ("ATLA-45", "Experienced Bender War Reroll", "War modifier", "Optional", "", "Experienced Bender receives war death", "May reroll once; the second result is final. Bending does not grant war immunity."),
    ("ATLA-46", "Prisoners and Missing Sims", "War aftermath", "Optional", "d6", "Annual while captured or missing", "Captured: 1 dies, 2 escapes, 3 released, 4-6 remains. Missing: 1 confirmed dead, 2 returns, 3-6 remains missing."),
    ("ATLA-47", "Occupation and Resistance", "Wartime civilian event", "Optional", "d10", "Annual under occupation", "Household outcomes cover confiscation, arrest, forced labor, shortage, resistance, or collaboration. Resistance joining and danger use separate annual d10 checks."),
    ("ATLA-48", "Refugees", "Displacement", "Optional", "", "Settlement destroyed or forced flight", "Record destination, portable possessions, property left behind, and losses. Refugees may flee within their nation or to allied, neutral, or hidden settlements."),
    ("ATLA-49", "Hundred Year War", "Historical event", "Optional", "", "Canon or canon-inspired timeline", "Apply ATLA-44 through ATLA-48 to affected nations, participants, occupied households, resistance members, captives, missing Sims, and refugees."),
    ("ATLA-50", "Air Nomad Genocide", "Canon historical event", "Off", "d20", "0 AG at Air Temple during Sozin's Comet", "Roll each affected Air Nomad: 1 survives and flees; 2-20 dies. The Avatar uses Avatar-specific rules. Keep disabled outside a canon-compatible timeline."),
)

DEPENDENCIES = {
    "ATLA-03": ("ATLA-02",), "ATLA-04": ("ATLA-02",), "ATLA-05": ("ATLA-04",),
    "ATLA-07": ("ATLA-06",), "ATLA-08": ("ATLA-06",), "ATLA-09": ("ATLA-06",),
    "ATLA-10": ("ATLA-06",), "ATLA-11": ("ATLA-06",), "ATLA-15": ("ATLA-14",),
    "ATLA-16": ("ATLA-14",), "ATLA-19": ("ATLA-09", "ATLA-28"),
    "ATLA-31": ("ATLA-30",), "ATLA-32": ("ATLA-30",), "ATLA-33": ("ATLA-30",),
    "ATLA-49": ("ATLA-44", "ATLA-45", "ATLA-46", "ATLA-47", "ATLA-48"),
    "ATLA-50": ("ATLA-49",),
}

MODULES = tuple({
    "code": code, "name": name, "category": category, "default": default, "die": die,
    "trigger": trigger, "rule_text": text, "dependencies": DEPENDENCIES.get(code, ()),
} for code, name, category, default, die, trigger, text in _MODULE_ROWS)

# Signed years are used internally: negative is BG, zero and positive are AG.
TIMELINE = (
    (-999999, -9830, "Lion Turtle Era", "Temporary elemental power, common Spirits, no inherited Bender Birth Rolls, and no Avatar Cycle."),
    (-9829, -9829, "First Avatar and Harmonic Convergence", "Avatar Wan bonds with Raava; enable the Avatar Cycle, permanent bending, and Spirit World rules."),
    (-9828, -4001, "Early Avatar World", "Settlements and elemental cultures develop; advanced techniques remain rare."),
    (-4000, -1001, "Formation of the Four Nations", "Nation ancestry, regional inheritance, and communal Air Nomad and Water Tribe structures become appropriate."),
    (-1000, -501, "Early Kingdoms and Clans", "Kingdoms, clans, temples, teachers, schools, and occasionally lost techniques grow."),
    (-500, -401, "Era of Avatar Yangchen", "Trade, diplomacy, Spirit disputes, and international mediation expand."),
    (-400, -345, "Era of Avatar Kuruk", "Public stability conceals dangerous spiritual conflict."),
    (-312, -312, "Birth of Avatar Kyoshi", "Avatar Kuruk dies and Kyoshi is born in the Earth Kingdom."),
    (-296, -296, "Kyoshi's Avatar Identity Revealed", "Enable full Avatar training and major Kyoshi-era political events."),
    (-82, -82, "Death of Kyoshi and Birth of Roku", "The cycle moves to Fire; Roku and Prince Sozin are born."),
    (-66, -66, "Roku Identified", "Begin Roku's Water, Earth, and Air training."),
    (-12, -12, "Death of Roku and Birth of Aang", "Roku dies during a volcanic disaster and Aang is born among the Air Nomads."),
    (-4, -4, "Aang Earns Master Tattoos", "Aang becomes an Airbending Master unusually young."),
    (0, 0, "Air Nomad Genocide", "Sozin's Comet, the Air Temple attacks, and the beginning of the Hundred Year War."),
    (1, 20, "Sozin's Conquest", "Rapid expansion, colonies, displacement, recruitment, and lost traditions."),
    (21, 75, "Fire Nation Consolidation and Middle War", "Long occupation, resistance, refugees, shortages, conscription, and industrial growth."),
    (76, 98, "Late War Period", "Military dominance, Water Tribe raids, territorial collapse, resistance danger, and war fatigue."),
    (99, 99, "Aang Returns", "The Avatar returns and begins the end-of-war journey and element training."),
    (100, 100, "End of the Hundred Year War", "Metalbending and modern Bloodbending appear; Ozai falls, Zuko becomes Fire Lord, and the war ends."),
    (101, 119, "Reconstruction and Early United Republic", "Refugees and prisoners return, colonies are disputed, and multicultural institutions emerge."),
    (120, 152, "Republic City Growth and Late Aang Era", "Modern industry, policing, pro-bending, Bloodbending restrictions, and political change expand."),
    (153, 153, "Death of Aang and Birth of Korra", "The Avatar Cycle moves to Water."),
    (154, 169, "Korra's Childhood and Training", "Republic City industrializes while Bender and Non-Bender tensions rise."),
    (170, 170, "Equalist Revolution", "Enable Equalists, anti-bending politics, pro-bending, occupation, and bending removal."),
    (171, 171, "Harmonic Convergence and New Air Nation", "Open Spirit Portals, Dark Spirits, and new Airbenders from previously Non-Bender families."),
    (172, 173, "Earth Kingdom Instability", "Political collapse, banditry, refugees, military rule, and Air Nation relief."),
    (174, 174, "Kuvira and the Earth Empire", "Forced reunification, occupation, labor camps, spirit-vine technology, war, and evacuation."),
    (175, 199, "Post-Korra Reconstruction", "Original history may explore Spirit-human communities, the restored Air Nation, migration, and political reform."),
    (200, 999999, "Future Avatar Era", "Use original history unless later canon is deliberately imported; the next Avatar follows Korra's eventual death."),
)


def date_label(year: int) -> str:
    year = int(year)
    return f"{abs(year):,} BG" if year < 0 else f"{year:,} AG"


def range_label(start: int, end: int) -> str:
    if start <= -999999:
        return f"Before {date_label(end + 1)}"
    if end >= 999999:
        return f"{date_label(start)} and later"
    return date_label(start) if start == end else f"{date_label(start)}–{date_label(end)}"


def _module_payload(module: dict, enabled: bool, pack_enabled: bool) -> dict:
    return {
        **module, "rule_key": module["code"].lower().replace("-", "_"),
        "rule_pack_id": PACK_ID, "rule_family": "Avatar Decades", "source": "Avatar Decades optional rules",
        "module_enabled": enabled, "pack_enabled": pack_enabled, "active": enabled and pack_enabled,
        "result_rules": module["rule_text"], "auto_schedule": False,
    }


def sync_pack(session: Session, save: ChronicleSave, selected: list[str]) -> int:
    """Install or pause the editable add-on without overwriting user choices."""
    from .domain import journal

    pack_enabled = PACK_ID in selected
    existing = {
        str((item.data or {}).get("code") or ""): item
        for item in session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "addon_rule", Record.deleted.is_(False),
        )) if (item.data or {}).get("rule_pack_id") == PACK_ID
    }
    changed = 0
    touched: list[tuple[Record, int]] = []
    for module in MODULES:
        record = existing.get(module["code"])
        if record is None:
            if not pack_enabled:
                continue
            enabled = module["default"] == "Recommended"
            created = Record(save_id=save.id, kind="addon_rule", label=f'{module["code"]} — {module["name"]}', data=_module_payload(module, enabled, True))
            session.add(created); touched.append((created, 0))
            changed += 1
            continue
        data = dict(record.data or {})
        enabled = bool(data.get("module_enabled", module["default"] == "Recommended"))
        desired = {**data, "pack_enabled": pack_enabled, "active": pack_enabled and enabled}
        if desired != data:
            base = record.version; record.data = desired; record.version += 1; touched.append((record, base)); changed += 1
    if touched:
        session.flush()
        for record, base in touched:
            journal(session, record, "upsert", base)
    return changed


def set_module(session: Session, save: ChronicleSave, code: str, enabled: bool) -> Record | None:
    record = session.scalar(select(Record).where(
        Record.save_id == save.id, Record.kind == "addon_rule", Record.deleted.is_(False),
        Record.data["code"].as_string() == code,
    ))
    if not record or (record.data or {}).get("rule_pack_id") != PACK_ID:
        return None
    data = dict(record.data or {}); data["module_enabled"] = enabled
    data["active"] = enabled and bool(data.get("pack_enabled")); record.data = data; record.version += 1
    return record
