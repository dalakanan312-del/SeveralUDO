from __future__ import annotations

import random
import re
import base64
import gzip
import json
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Change, ChronicleSave, Portrait, Record
from . import advanced, calendar_utils, core_rulesets, decade_portraits, occult_rules, sync
from .event_catalog_data import EVENT_LIBRARY_GZIP_BASE64
from .early_event_catalog_data import EARLY_EVENT_LIBRARY_GZIP_BASE64


DEFAULTS_SCHEMA_VERSION = "4.5.4-occult-alignment"

# Authoritative pre-1700 SeveralUDO mortality table recovered from the
# original Rules Config. The age offsets remain challenge-day milestones;
# Elder is a 60–120 historical-year draw rather than an ordinary failure roll.
DEFAULT_STAGES = [
    ("Being Born", 0, "d20", "5 10 15"), ("Newborn", 0, "d20", "2 6"),
    ("Infant", 1, "d20", "17"), ("Toddler", 4, "d20", "5 10 15"),
    ("Child", 20, "d20", "15 20"), ("Preteen", 40, "d20", "13 18"),
    ("Teen", 52, "d20", "7"), ("Young Adult", 72, "d20", "14 16"),
    ("Adult", 160, "d20", "3 9 20"), ("Elder Death-Age RNG", 240, "RNG", "60–120"),
]

# Values accidentally introduced by the first 4.x rebuild. They are retained
# only as a migration fingerprint so player-edited rules are never overwritten.
LEGACY_INCORRECT_STAGES = {
    "being born": ("d20", "1-3"), "newborn": ("d20", "1"),
    "infant": ("d20", "1"), "toddler": ("d20", "1"),
    "child": ("d20", "1"), "preteen": ("d20", "1"),
    "teen": ("d20", "1"), "young adult": ("d20", "1"),
    "adult": ("d20", "1"), "elder death-age rng": ("d100", "1-20"),
}

AGING_STAGE_OFFSETS = {stage.casefold(): age for stage, age, _die, _bad in DEFAULT_STAGES}


def automation_enabled(save: ChronicleSave) -> bool:
    """Return the save-wide master automation state; existing saves default on."""
    value = (save.settings or {}).get("automation_enabled", True)
    if isinstance(value, str):
        return value.strip().casefold() not in {"0", "false", "off", "no", "paused"}
    return value is not False

DEFAULT_DEATH_CAUSES = {
    "birth": ["Complications of childbirth", "Childbed fever", "Hemorrhage"],
    "infant": ["Infant fever", "Respiratory infection", "Unknown childhood illness"],
    "child": ["Fever", "Accident", "Infectious disease"],
    "adult": ["Pneumonia", "Infectious disease", "Accident", "Sudden illness"],
    "elder": ["Old age", "Pneumonia", "Stroke", "Heart failure"],
}

DEFAULT_MATERNAL_RULES = [
    ("Maternal — Preteen", "d20", "1-8"),
    ("Maternal — Teen", "d20", "1-6"),
    ("Maternal — Young Adult", "d20", "1-4"),
    ("Maternal — Adult", "d20", "1-5"),
    ("Maternal — Elder", "d20", "1-8"),
]

DEFAULT_MULTIPLE_BIRTH_ERAS = (
    (1200, 1749), (1750, 1865), (1866, 1895), (1896, 9999),
)

# Location-neutral starter guidance. Every entry remains editable per save.
DEFAULT_ERA_GUIDANCE = [
    ("Household-based livelihoods", "Careers & education", -9999, 1499, "Most work is tied to household, land, craft, trade, or local service. Formal schooling depends on class, faith, sex, and location."),
    ("Marriage as a household alliance", "Marriage & family", -9999, 1499, "Treat marriage as a family, property, labor, or political alliance as well as a personal relationship."),
    ("Customary inheritance", "Inheritance", -9999, 1499, "Use the selected succession system, legitimacy, birth order, and local custom."),
    ("Limited medical care", "Health", -9999, 1499, "Care is domestic or local. Illness, childbirth, infection, and injury carry serious consequences."),
    ("Early modern households", "Marriage & family", 1500, 1699, "Household formation, inheritance, religion, trade, migration, and reputation remain central."),
    ("Print, trade, and expanding literacy", "Careers & education", 1500, 1699, "Literacy and specialized work expand unevenly through schools, apprenticeships, clerical work, and trade."),
    ("Agrarian and commercial life", "Economy", 1700, 1799, "Land and household production remain important while commerce and wage work grow."),
    ("Industrial transition", "Economy", 1800, 1849, "Introduce factories, wage labor, urban crowding, and faster transport only where industrialization has reached the location."),
    ("Rail, steam, and telegraph transition", "Building & technology", 1850, 1913, "Introduce rail, steam, telegraphy, photography, sanitation, and electricity by location and class."),
    ("Total-war disruption", "Military", 1914, 1945, "Major wars may affect soldiers and civilians through service, evacuation, rationing, displacement, injury, and bereavement."),
    ("Postwar household change", "Marriage & family", 1946, 1969, "Let local law and culture govern marriage, divorce, adoption, legitimacy, gender roles, and reproductive choices."),
    ("Rights and opportunities in transition", "Careers & education", 1970, 1999, "Education, employment, family law, and civil rights broaden at different rates by location."),
    ("Contemporary local rules", "Other", 2000, 9999, "Use current-year rules for the location while retaining differences in law, culture, cost, healthcare, and opportunity."),
]

DEFAULT_PLANNER_RULES = [
    ("Side Household Pregnancy", -9999, 1299, "d20", "1-14: Schedule that many pregnancies; 15-20: No pregnancy", "Annual pregnancy-count roll"),
    ("Side Household Pregnancy", 1300, 1399, "d20", "1-13: Schedule that many pregnancies; 14-20: No pregnancy", "Annual pregnancy-count roll"),
    ("Side Household Pregnancy", 1400, 1499, "d20", "1-11: Schedule that many pregnancies; 12-15: One pregnancy; 16-20: No pregnancy", "Annual pregnancy-count roll"),
    ("Side Household Pregnancy", 1500, 1699, "d12", "1-10: Schedule that many pregnancies; 11-12: No pregnancy", "Annual pregnancy-count roll"),
    ("Side Household Pregnancy", 1700, 1799, "d10", "1-8: Schedule that many pregnancies; 9-10: No pregnancy", "Annual pregnancy-count roll"),
    ("Side Household Pregnancy", 1800, 1899, "d10", "1-8: Schedule that many pregnancies; 9-10: No pregnancy", "Annual pregnancy-count roll"),
    ("Side Household Pregnancy", 1900, 9999, "d6", "1-5: Schedule that many pregnancies; 6: No pregnancy", "Annual pregnancy-count roll"),
    ("Non-Heir Marriage Eligibility", -9999, 1299, "d12", "1", "Marriage eligibility for a non-heir"),
    ("Non-Heir Marriage Eligibility", 1300, 1499, "d10", "1", "Marriage eligibility for a non-heir"),
    ("Non-Heir Marriage Eligibility", 1500, 1799, "d8", "1", "Marriage eligibility for a non-heir"),
    ("Non-Heir Marriage Eligibility", 1800, 9999, "d6", "1", "Marriage eligibility for a non-heir"),
    ("Remarriage Eligibility", -9999, 9999, "d6", "1: May remarry; 2-6: Does not remarry", "One remarriage roll after a marriage ends; add era ranges to change the odds through history"),
]

CLOSED_PREGNANCIES = {"delivered", "complete", "completed", "miscarriage", "stillbirth", "cancelled", "canceled", "closed"}
DELIVERY_PREGNANCIES = {"delivered", "complete", "completed", "stillbirth", "closed"}
MATERNAL_ROLL_RETIRE_PREGNANCIES = {"miscarriage", "cancelled", "canceled"}
CLOSED_ILLNESSES = {"recovered", "resolved", "deceased", "ended", "closed"}
EVENT_CATALOG_VERSION = "approved-source-1200-865-v3"

# These catalog rows change money, MCCC limits, or available technology.  An
# early import marked them as rolls even though their source instructions do
# not ask the player to roll anything.
NON_ROLL_CATALOG_IDS = {
    "EVT-0169", "EVT-0506", "EVT-0518", "EVT-0520", "EVT-0621", "EVT-0646",
}

# A handful of recovered catalog rows kept only a "see the source page" note.
# Their rolls therefore cannot be inferred from the compressed catalog itself.
# These values are transcribed from the linked SeveralUDO era pages so fresh and
# existing saves receive the same editable rules.
ORIGINAL_EVENT_ROLL_OVERRIDES: dict[str, dict] = {
    "EVT-0143": {
        "location": "Spain",
        "affected_class": "Male Sims age 16+ in Spain",
        "configured_die": "d12",
        "configured_bad_results": "6: Enlisted in the Siege of Seville",
        "followup_enabled": True,
        "followup_trigger_results": "6",
        "followup_delay_days": 0,
        "followup_label": "Siege of Seville casualty roll",
        "followup_die": "d12",
        "followup_bad_results": "5: Dies in the Siege of Seville",
        "followup_failure_is_lethal": True,
        "original_roll_instructions": "Spanish men age 16+ roll D12; 6 means enlisted. Enlisted Sims roll D12 and die on 5.",
    },
    "EVT-0157": {
        "location": "Bulgaria, Italy, Latin Empire",
        "affected_class": "Male Sims age 16+ in the participating regions",
        "configured_die": "d12",
        "configured_bad_results": "6: Enlisted in the Battle of Adrianople",
        "followup_enabled": True,
        "followup_trigger_results": "6",
        "followup_delay_days": 0,
        "followup_label": "Battle of Adrianople casualty roll",
        "followup_die": "d8",
        "followup_bad_results": "2: Dies in the Battle of Adrianople",
        "followup_failure_is_lethal": True,
        "original_roll_instructions": "Eligible men roll D12; 6 means enlisted. Enlisted Sims roll D8 and die on 2.",
    },
    "EVT-0264": {
        "location": "France",
        "affected_class": "All Sims in France, including side households",
        "configured_die": "d12",
        "configured_bad_results": "3: Dies of famine",
        "original_roll_instructions": "Roll a D12 for all Sims in France; 3 means they die of famine.",
    },
    "EVT-0484": {
        "location": "United States, Massachusetts",
        "affected_class": "Women and men in colonial Massachusetts",
        "configured_die": "d4",
        "configured_bad_results": "1: Accused of witchcraft; 4: Accused of witchcraft",
        "die_by_sex": {"female": "d4", "male": "d6"},
        "result_rules_by_sex": {
            "female": "1: Accused of witchcraft; 4: Accused of witchcraft",
            "male": "1: Accused of witchcraft; 6: Accused of witchcraft",
        },
        "followup_enabled": True,
        "followup_delay_days": 0,
        "followup_label": "Witch-trial verdict",
        "followup_die": "d2",
        "followup_bad_results": "1: Dies after the witch trial",
        "followup_failure_is_lethal": True,
        "original_roll_instructions": (
            "Women roll D4 and are accused on 1 or 4; men roll D6 and are accused on 1 or 6. "
            "Each accused Sim flips a coin: Heads means death and Tails means they walk away."
        ),
    },
    "EVT-0485": {
        "location": "The Americas",
        "affected_class": "All Sims living in the Americas",
        "configured_die": "d20",
        "configured_bad_results": "1: Dies of influenza",
        "original_roll_instructions": "Roll a D20 for every Sim living in America; 1 means the Sim dies.",
    },
    "EVT-0486": {
        "location": "North America",
        "affected_class": "Male Sims living in North America",
        "configured_die": "d2",
        "configured_bad_results": "2: Tails — enlisted",
        "followup_enabled": True,
        "followup_trigger_results": "2",
        "followup_delay_days": 0,
        "followup_label": "Queen Anne's War casualty roll",
        "followup_die": "d10",
        "followup_bad_results": "3: Dies in Queen Anne's War; 5: Dies in Queen Anne's War",
        "followup_failure_is_lethal": True,
        "original_roll_instructions": (
            "Male Sims in North America flip a coin; Tails means enlisted. "
            "Enlisted Sims roll D10 and die on 3 or 5."
        ),
    },
    "EVT-0488": {
        "location": "Europe",
        "affected_class": "All Sims in Europe",
        "configured_die": "d2",
        "configured_bad_results": "1: Heads — no fireplace heat; 2: Tails — heat available",
        "followup_enabled": True,
        "followup_delay_days": 0,
        "followup_label": "Great Frost survival roll",
        "followup_branches": {
            "1": {
                "label": "Great Frost — no heat",
                "die": "d10",
                "result_rules": "7: Starves during the Great Frost; 10: Freezes to death",
                "failure_is_lethal": True,
            },
            "2": {
                "label": "Great Frost — heat available",
                "die": "d20",
                "result_rules": "2: Starves during the Great Frost; 3: Freezes to death",
                "failure_is_lethal": True,
            },
        },
        "original_roll_instructions": (
            "Flip a coin for fireplace heat. Heads means no heat: roll D10, where 7 starves and 10 freezes. "
            "Otherwise roll D20, where 2 starves and 3 freezes."
        ),
    },
    "EVT-0494": {
        "location": "North America, Spain",
        "affected_class": "Male Sims age 16+ in North America or Spain",
        "configured_die": "d20",
        "configured_bad_results": "4: Enlisted in the Villasur Expedition",
        "followup_enabled": True,
        "followup_trigger_results": "4",
        "followup_delay_days": 0,
        "followup_label": "Villasur Expedition casualty roll",
        "followup_die": "d6",
        "followup_bad_results": "3: Dies in the Villasur Expedition",
        "followup_failure_is_lethal": True,
        "original_roll_instructions": "Eligible men roll D20; 4 means enlisted. Enlisted Sims roll D6 and die on 3.",
    },
    "EVT-0501": {
        "location": "United States, Massachusetts",
        "affected_class": "Babies, toddlers, children, and pregnant Sims in Boston",
        "eligible_life_stages": ["newborn", "infant", "baby", "toddler", "child"],
        "include_pregnant": True,
        "configured_die": "d10",
        "configured_bad_results": "10: Dies of measles",
        "original_roll_instructions": "Babies, toddlers, children, and pregnant Sims roll D10; 10 means death.",
    },
    "EVT-0529": {
        "location": "Spain, Morocco, England, Scotland, Wales",
        "affected_class": "Male Sims age 16+ in the participating regions",
        "configured_die": "d12",
        "configured_bad_results": "4: Enlisted in the Siege of Melilla",
        "followup_enabled": True,
        "followup_trigger_results": "4",
        "followup_delay_days": 0,
        "followup_label": "Siege of Melilla casualty roll",
        "followup_die": "d20",
        "followup_bad_results": "3: Dies in the Siege of Melilla",
        "followup_failure_is_lethal": True,
        "original_roll_instructions": "Eligible men roll D12; 4 means enlisted. Enlisted Sims roll D20 and die on 3.",
    },
    "EVT-0269": {
        "affected_class": "Male Teen–Adult Sims in all households",
        "configured_die": "d4",
        "configured_bad_results": "4: Enlisted in the Hundred Years' War",
        "followup_enabled": True,
        "followup_trigger_results": "4",
        "followup_delay_years": 5,
        "followup_label": "Hundred Years' War casualty and return roll",
        "followup_die": "d20",
        "followup_bad_results": "20: Dies in the Hundred Years' War",
        "followup_failure_is_lethal": True,
        "original_roll_instructions": "Draft phases use D4 and enlist on 4; the later casualty/return roll uses D20 and kills on 20.",
    },
    "EVT-0491": {
        "location": "United States, North Carolina, South Carolina",
        "affected_class": "Male Sims age 16+ in the Carolinas, including Native Sims",
        "configured_die": "d20",
        "configured_bad_results": "4: Enlisted in the Tuscarora War",
        "followup_enabled": True,
        "followup_trigger_results": "4",
        "followup_delay_days": 0,
        "followup_label": "Tuscarora War casualty roll",
        "followup_die": "d12",
        "followup_bad_results": "8: Dies in the Tuscarora War",
        "followup_failure_is_lethal": True,
        "original_roll_instructions": "Eligible men roll D20; 4 enlists. Enlisted Sims roll D12; 8 means death.",
    },
    "EVT-0492": {
        "location": "North America",
        "affected_class": "Babies through children in main and side households",
        "eligible_life_stages": ["newborn", "infant", "baby", "toddler", "child"],
        "configured_die": "d10",
        "configured_bad_results": "5: Dies of measles; 7: Dies of measles; 10: Dies of measles",
        "original_roll_instructions": "Every baby through child rolls D10; 5, 7, or 10 means death.",
    },
    "EVT-0493": {
        "location": "United States, South Carolina",
        "affected_class": "Traders and unemployed Sims in South Carolina",
        "configured_die": "d10",
        "configured_bad_results": "1-8: Dies in the Yamasee War; 10: Dies in the Yamasee War",
        "original_roll_instructions": "Eligible Sims roll D10; only 9 survives.",
    },
    "EVT-0496": {
        "location": "United States, Massachusetts, Boston",
        "affected_class": "Babies, toddlers, children, and pregnant Sims in Boston",
        "eligible_life_stages": ["newborn", "infant", "baby", "toddler", "child"],
        "include_pregnant": True,
        "configured_die": "d10",
        "configured_bad_results": "10: Dies of measles",
        "original_roll_instructions": "Babies, toddlers, children, and pregnant Sims roll D10; 10 means death.",
    },
    "EVT-0497": {
        "location": "United States, Thirteen Colonies",
        "affected_class": "Every Sim in main and side households in the Thirteen Colonies",
        "configured_die": "d8",
        "configured_bad_results": "3: Dies of influenza",
        "original_roll_instructions": "Every Sim in main and side households rolls D8; 3 means death.",
    },
    "EVT-0499": {
        "location": "United States, New England, New Hampshire",
        "affected_class": "All Sims in New England, using an age-specific table",
        "configured_die": "d20",
        "configured_bad_results": "3: Dies of diphtheria",
        "source_roll_plan": [
            {"index": 0, "die": "d20", "bad_results": "3", "result_rules": "3: Dies of diphtheria", "context": "Teen through Adult Sims", "parent_index": None, "trigger_results": "", "eligible_life_stages": ["teen", "young adult", "adult"]},
            {"index": 1, "die": "d8", "bad_results": "4", "result_rules": "4: Dies of diphtheria", "context": "Infant through Child and Elder Sims", "parent_index": None, "trigger_results": "", "eligible_life_stages": ["newborn", "infant", "baby", "toddler", "child", "elder"]},
        ],
        "original_roll_instructions": "Teen through Adult Sims roll D20 and die on 3; Infant through Child and Elder Sims roll D8 and die on 4.",
    },
    "EVT-0500": {
        "location": "United States, England, Spain, Caribbean",
        "affected_class": "Eligible male Sims in participating regions",
        "configured_die": "d4",
        "configured_bad_results": "3: Enlisted in the War of Jenkins' Ear",
        "followup_enabled": True,
        "followup_trigger_results": "3",
        "followup_delay_days": 0,
        "followup_label": "War of Jenkins' Ear casualty roll",
        "followup_die": "d10",
        "followup_bad_results": "1: Dies in the War of Jenkins' Ear; 3: Dies in the War of Jenkins' Ear; 4: Dies in the War of Jenkins' Ear; 5: Dies in the War of Jenkins' Ear",
        "followup_failure_is_lethal": True,
        "original_roll_instructions": "Eligible men roll D4; 3 enlists. Enlisted soldiers roll D10 and die on 1, 3, 4, or 5.",
    },
    "EVT-0522": {
        "location": "United States, Thirteen Colonies",
        "affected_class": "Main household in the Thirteen Colonies",
        "configured_die": "d4",
        "configured_bad_results": "1-4: House that many soldiers",
        "original_roll_instructions": "The main household rolls D4 and must house, feed, and support that many soldiers.",
    },
    "EVT-0629": {
        "location": "United States, Alaska, St. Lawrence Island",
        "affected_class": "All households and Sims on St. Lawrence Island",
        "configured_die": "d2",
        "configured_bad_results": "1: Heads — crops fail",
        "source_roll_plan": [
            {"index": 0, "die": "d2", "bad_results": "1", "result_rules": "1: Heads — crops fail; 2: Tails — crops survive", "context": "Household crop-loss check", "parent_index": None, "trigger_results": ""},
            {"index": 1, "die": "d4", "bad_results": "3", "result_rules": "3: Dies of famine", "context": "Every Sim in main and side households", "parent_index": None, "trigger_results": ""},
        ],
        "original_roll_instructions": "Flip for household crop loss, then every Sim rolls D4; 3 means famine death.",
    },
}

# These five source entries use result wording such as “land on 7 and
# survives” or list outcomes after “if” rather than “means”.  Keeping the
# transcribed tables here prevents a generic d20 fallback and preserves the
# source's actual dice and outcomes.
EARLY_EVENT_ROLL_OVERRIDES_BY_NAME: dict[str, dict] = {
    "2200 BCE The settlements in what today is northern Israel have been abandoned.": {
        "configured_die": "d4",
        "configured_bad_results": "1,2,3,4",
        "source_roll_plan": [{
            "index": 0, "die": "d4", "bad_results": "1,2,3,4",
            "result_rules": "1: Migrate to Asia; 2: Migrate elsewhere in the Middle East; 3: Migrate to Europe; 4: Migrate to Africa",
            "context": "Israeli Sims relocate after the settlements are abandoned",
            "parent_index": None, "parent_indices": [], "trigger_results": "",
        }],
    },
    "441 BCE The first famine recorded in ancient Rome.": {
        "configured_die": "d10",
        "configured_bad_results": "1-6,8-10",
        "source_roll_plan": [{
            "index": 0, "die": "d10", "bad_results": "1-6,8-10",
            "result_rules": "7: Survives; 1-6,8-10: Dies of famine or plague",
            "context": "All Sims living in Rome",
            "parent_index": None, "parent_indices": [], "trigger_results": "",
        }],
    },
    "Peloponnesian War (431–404 BCE)": {
        "configured_die": "d6",
        "configured_bad_results": "1,2,4,6",
        "source_roll_plan": [{
            "index": 0, "die": "d6", "bad_results": "1,2,4,6",
            "result_rules": "3,5: Survives; 1,2,4,6: Dies during the war or plague",
            "context": "All Sims living in Greece",
            "parent_index": None, "parent_indices": [], "trigger_results": "",
        }],
    },
    "Lamian War (323–322 BCE)": {
        "configured_die": "d10",
        "configured_bad_results": "2,3,5,6,8,10",
        "source_roll_plan": [{
            "index": 0, "die": "d10", "bad_results": "2,3,5,6,8,10",
            "result_rules": "1,4,7,9: Remains, becomes noble; 2,3,5,6,8,10: Exiled and loses money",
            "context": "Greek Sims not already exiled",
            "parent_index": None, "parent_indices": [], "trigger_results": "",
        }],
    },
    "Third Punic War (149–146 BCE)": {
        # This war has two independent, region-specific tables. Leave the
        # event global enough to reach both groups, then let each source-plan
        # step restrict itself to the affected region.
        "location": "Global",
        "configured_die": "d2",
        "configured_bad_results": "1",
        "source_roll_plan": [
            {
                "index": 0, "die": "d2", "bad_results": "1",
                "result_rules": "1: Does not survive the Third Punic War; 2: Survives",
                "context": "Sims in or near Carthage or North Africa",
                "location": "Carthage, North Africa",
                "parent_index": None, "parent_indices": [], "trigger_results": "",
            },
            {
                "index": 1, "die": "d6", "bad_results": "5",
                "result_rules": "5: Dies in the Third Punic War",
                "context": "Sims from Rome",
                "location": "Rome, Roman Empire",
                "parent_index": None, "parent_indices": [], "trigger_results": "",
            },
        ],
    },
}

# The source prose for these events interleaves several regional or household
# tables.  A plain "next die is the follow-up" rule cannot represent that
# topology, so keep the small amount of source-defined branching explicit.
SOURCE_PLAN_RELATION_OVERRIDES: dict[str, dict[int, dict]] = {
    "EVT-0070": {1:{"parent_indices":[0], "trigger_results":"1-3"}, 2:{"parent_indices":[], "trigger_results":""}},
    "EVT-0228": {
        1:{"parent_indices":[0], "trigger_results":"1"},
        2:{"parent_indices":[1], "trigger_results":"39-45"},
        3:{"parent_indices":[0], "trigger_results":"1"},
        4:{"parent_indices":[0], "trigger_results":"2"},
        5:{"parent_indices":[4], "trigger_results":"1", "bad_results":"1", "result_rules":"1: One Sim is injured; 2-20: No injury"},
    },
    "EVT-0230": {
        1:{"parent_indices":[], "trigger_results":""},
        2:{"parent_indices":[0], "trigger_results":"1-3"},
        3:{"parent_indices":[1], "trigger_results":"1"},
        4:{"parent_indices":[], "trigger_results":""},
    },
    "EVT-0261": {1:{"parent_indices":[], "trigger_results":""}, 2:{"parent_indices":[0,1], "trigger_results":""}},
    "EVT-0336": {1:{"parent_indices":[], "trigger_results":""}, 2:{"parent_indices":[0,1], "trigger_results":""}},
    "EVT-0452": {
        1:{"parent_indices":[0], "trigger_results":"3"},
        2:{"parent_indices":[], "trigger_results":""},
        3:{"parent_indices":[2], "trigger_results":"7"},
        4:{"parent_indices":[], "trigger_results":""},
    },
    "EVT-0539": {1:{"parent_indices":[0], "trigger_results":"4"}, 2:{"parent_indices":[], "trigger_results":""}},
    "EVT-0614": {1:{"parent_indices":[0], "trigger_results":"7"}, 2:{"parent_indices":[], "trigger_results":""}},
    "EVT-0625": {
        1:{"parent_indices":[0], "trigger_results":"1-11"},
        2:{"parent_indices":[], "trigger_results":""},
        3:{"parent_indices":[0], "trigger_results":"1-11"},
    },
    "EVT-0653": {1:{"parent_indices":[0], "trigger_results":"1-12"}, 2:{"parent_indices":[], "trigger_results":""}},
    "EVT-0654": {
        1:{"parent_indices":[0], "trigger_results":"1-8"},
        2:{"parent_indices":[], "trigger_results":""},
        3:{"parent_indices":[2], "trigger_results":"1-6"},
    },
}


def source_event_requires_roll(notes: object) -> bool:
    """Recognize explicit dice instructions in the approved SeveralUDO source.

    A late section of the recovered catalog retained its original dice prose but
    lost the separate ``roll_required`` flag. Keep this deliberately narrow:
    ordinary historical prose is not actionable unless it actually says to roll
    or names one of the dice used by the ruleset.
    """
    text = str(notes or "")
    return bool(
        re.search(r"\broll(?:s|ed|ing)?\b", text, re.I)
        or re.search(r"\b(?:\d+\s*)?d\s*(?:4|6|8|10|12|20|100)\b", text, re.I)
        or re.search(r"\b(?:flip|toss)(?:\s+\w+){0,3}\s+coin\b|\bflip\s+(?:for|per)\b", text, re.I)
    )


_SOURCE_ROLL_MARKER = re.compile(
    r"(?P<coin>\b(?:flip|toss)(?:\s+(?:a|the))?\s+coin\b|\bcoin\s+(?:flip|toss|assigns|for)\b|\bflip\s+(?:for|per)\b)"
    r"|(?P<coin_result>\bcoin\b(?=[^.;]{0,80}\b(?:heads?|tails?)\b))"
    r"|(?P<die>(?<!\w)(?:1\s*)?d\s*(?P<sides>2|4|6|8|10|12|20|100)\b)",
    re.I,
)
_SOURCE_SUCCESS = re.compile(
    r"\b(?:avoid|unaffected|unharmed|unscathed|spared|surviv(?:e|es|ed|or)|"
    r"return(?:s|ed)?\s+(?:home\s+)?safely|survival|no\s+(?:major\s+)?effect|not\s+involved|"
    r"do(?:es)?\s+not\s+participate|thriv(?:e|es|ed)|remain(?:s)?\s+intact|"
    r"(?:claim|dispute)\s+is\s+settled|ordinary\s+year|"
    r"(?:route|trade)\s+(?:prospers?|flourishes?)|"
    r"gains?\s+(?:a\s+)?(?:local\s+)?(?:favor|reward))\b",
    re.I,
)
_SOURCE_DEPENDENT = re.compile(
    r"\b(?:enlisted|conscripted|drafted|arrested|accused|infected|affected|involved|"
    r"participants?|soldiers?|those\s+who|whose\s+(?:house|home)|missing|remaining|"
    r"directly\s+affected|house\s+burns|house\s+was\s+destroyed)\b",
    re.I,
)


def _source_result_left(value: str) -> str:
    normalized = str(value or "").replace("–", "-").replace("—", "-")
    normalized = re.sub(r"\b(?:or|and)\b|[&/]", ",", normalized, flags=re.I)
    pieces = re.findall(r"\d+\s*-\s*\d+|\d+", normalized)
    return ",".join(dict.fromkeys(piece.replace(" ", "") for piece in pieces))


def _source_complement(sides: int, safe_values: str) -> str:
    safe: set[int] = set()
    for token in re.findall(r"\d+\s*-\s*\d+|\d+", safe_values.replace("–", "-").replace("—", "-")):
        if "-" in token:
            first, last = (int(value.strip()) for value in token.split("-", 1))
            safe.update(range(min(first, last), max(first, last) + 1))
        else:
            safe.add(int(token))
    bad = [value for value in range(1, sides + 1) if value not in safe]
    if not bad:
        return ""
    ranges: list[str] = []
    start = previous = bad[0]
    for value in bad[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


def _source_outcome_is_success(outcome: object) -> bool:
    text = str(outcome or "")
    if re.search(r"\b(?:does?|did|will)?\s*not\s+(?:survive|return|participate|remain)|\bfails?\s+to\s+survive", text, re.I):
        return False
    return bool(_SOURCE_SUCCESS.search(text))


def _source_step_rules(segment: str, sides: int, *, coin: bool = False) -> tuple[str, str]:
    """Parse the result table belonging to one source die/coin instruction."""
    cleaned = str(segment or "").replace("�", " ").replace("•", " ").replace("*", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .;:")
    clauses: list[tuple[str, str]] = []
    if coin:
        for label, outcome in re.findall(
            r"\b(heads?|tails?)\b\s*(?:means?|=|:)?\s*([^;.]+)", cleaned, re.I
        ):
            clauses.append(("1" if label.casefold().startswith("head") else "2", outcome.strip()))

    numeric_left = (
        r"\d+(?:\s*[-–—]\s*\d+)?"
        r"(?:\s*(?:(?:,|/|&)\s*(?:(?:or|and)\s+)?|(?:or|and)\s+)"
        r"\d+(?:\s*[-–—]\s*\d+)?)*"
    )
    numeric = re.compile(
        rf"(?<!\d)({numeric_left})"
        r"\s*(?:means?|=|:|[–—])\s*([^;.]+)",
        re.I,
    )
    for left, outcome in numeric.findall(cleaned):
        clauses.append((_source_result_left(left), outcome.strip()))

    # Compact recovered summaries often read "D12: 3 thirst, 7 hunger, 10
    # pestilence death" without repeating a colon after every number.
    if not clauses:
        compact = cleaned.split(":", 1)[-1]
        for part in re.split(r",\s*(?=\d)|;", compact):
            match = re.match(rf"\s*({numeric_left})\s+(.+?)\s*$", part, re.I)
            if match:
                clauses.append((_source_result_left(match.group(1)), match.group(2).strip()))

    only_survives = re.search(
        r"\bonly\s+([\d\s,orand/&\-–—]+?)\s+surviv(?:e|es)\b", cleaned, re.I
    )
    if only_survives:
        bad = _source_complement(sides, only_survives.group(1))
        if bad:
            clauses.append((bad, "Dies"))
    other_death = re.search(r"\ball\s+(?:other|remaining)\s+results?\s+(?:mean\s+)?(?:die|death)", cleaned, re.I)
    if other_death:
        named = ",".join(left for left, outcome in clauses if _source_outcome_is_success(outcome))
        bad = _source_complement(sides, named)
        if bad:
            clauses.append((bad, "Dies"))

    # Some compact source entries name the sole safe result (for example,
    # "D12 for enlisted; 3 means survival") instead of spelling out the
    # losing values.  Store the complement so the follow-up remains playable.
    if clauses and all(_source_outcome_is_success(outcome) for _left, outcome in clauses):
        safe_values = ",".join(left for left, _outcome in clauses)
        complement = _source_complement(sides, safe_values)
        if complement:
            clauses.append((complement, "Dies"))

    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for left, outcome in clauses:
        key = (left, outcome.casefold())
        if left and outcome and key not in seen:
            seen.add(key); deduped.append((left, outcome))
    result_rules = "; ".join(f"{left}: {outcome}" for left, outcome in deduped)
    adverse = [left for left, outcome in deduped if not _source_outcome_is_success(outcome)]
    if not adverse and deduped:
        # Some source tables are choices rather than pass/fail checks.  They
        # still need actionable result values in the tracker.
        adverse = [left for left, _outcome in deduped]
    return result_rules, " ".join(dict.fromkeys(adverse))


_REPEAT_YEAR_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
}


def source_roll_repeat_interval_years(text: object) -> int:
    """Return the historical-year interval stated by one source instruction.

    Repeating checks are anchored to the event's first day.  Keeping this as a
    numeric interval also supports less common source rules such as "every ten
    years" without adding event-specific scheduler branches.
    """
    value = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
    if not value:
        return 0
    match = re.search(
        r"\bevery\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty)\s+(?:historical\s+|in[ -]game\s+)?years?\b",
        value,
    )
    if match:
        token = match.group(1)
        return max(1, int(token) if token.isdigit() else _REPEAT_YEAR_WORDS.get(token, 1))
    if re.search(
        r"\b(?:each|every)\s+(?:historical\s+|in[ -]game\s+)?year\b|\b(?:annually|yearly)\b|\b(?:once\s+)?per\s+(?:historical\s+|in[ -]game\s+)?year\b",
        value,
    ):
        return 1
    return 0


def _normalize_source_plan_cadence(plan: list[dict], notes: object = "") -> list[dict]:
    """Attach repeat cadence to the exact root instruction that states it."""
    normalized = [dict(step) for step in plan if isinstance(step, dict)]
    root_steps = [
        step for step in normalized
        if not (step.get("parent_indices") or []) and step.get("parent_index") is None
    ]
    marked = False
    for step in normalized:
        explicit = step.get("repeat_interval_years")
        try:
            interval = max(0, int(explicit or 0))
        except (TypeError, ValueError):
            interval = 0
        interval = interval or source_roll_repeat_interval_years(step.get("context"))
        if interval:
            step["repeat_interval_years"] = interval
            step["cadence"] = "annual" if interval == 1 else f"every {interval} years"
            marked = True
    # A custom/override plan may contain only one root and omit the original
    # prose from its context.  In that unambiguous case the event-level wording
    # can safely supply the cadence without repeating unrelated root tables.
    if not marked and len(root_steps) == 1:
        interval = source_roll_repeat_interval_years(notes)
        if interval:
            root_steps[0]["repeat_interval_years"] = interval
            root_steps[0]["cadence"] = "annual" if interval == 1 else f"every {interval} years"
    return normalized


def source_event_roll_plan(notes: object) -> list[dict]:
    """Return every actionable die/coin table recoverable from source prose.

    Each entry has a concrete die and bad/trigger result.  Dependent tables
    (casualty after enlistment, death after infection, etc.) point to their
    parent so completing the first roll can create the correct follow-up.
    """
    text = str(notes or "").replace("\r", " ").replace("\n", "; ")
    candidates = list(_SOURCE_ROLL_MARKER.finditer(text))
    usable = []
    for marker in candidates:
        tail = text[marker.end():marker.end() + 24]
        prefix = text[max(0, marker.start() - 18):marker.start()]
        if marker.group("die") and re.match(
            r"\s*(?:days?|weeks?|months?|years?|objects?|simoleons?|goods?|food|"
            r"livestock|animals?|crops?|supplies|money|coins?|soldiers?)\b",
            tail,
            re.I,
        ) and not re.search(r"\broll\b", prefix, re.I):
            continue
        usable.append(marker)
    plan: list[dict] = []
    for index, marker in enumerate(usable):
        end = usable[index + 1].start() if index + 1 < len(usable) else len(text)
        segment = text[marker.end():end]
        context_start = max(text.rfind(";", 0, marker.start()), text.rfind(".", 0, marker.start())) + 1
        context = re.sub(r"\s+", " ", text[context_start:end]).strip(" ;.")
        coin = bool(marker.group("coin") or marker.group("coin_result")); sides = 2 if coin else int(marker.group("sides"))
        result_rules, bad_results = _source_step_rules(segment, sides, coin=coin)
        if not bad_results:
            # A compact coin instruction can put Heads/Tails just before the
            # next punctuation boundary included in the context.
            result_rules, bad_results = _source_step_rules(context, sides, coin=coin)
        if not bad_results:
            continue
        prefix_start = max(text.rfind(";", 0, marker.start()), text.rfind(".", 0, marker.start())) + 1
        prefix = re.sub(r"\s+", " ", text[prefix_start:marker.start()]).strip()
        subject = segment[:120]
        dependent_terms = "enlisted|conscripted|drafted|arrested|accused|infected|affected|involved|participants?|soldiers?|missing|convicts?|directly affected|indirect|aftershock injury"
        selector = re.sub(r"\s+", " ", f"{prefix} {subject.split(':', 1)[0]}").strip()[:220]
        dependent = bool(plan and (
            re.search(rf"\b(?:{dependent_terms})\b", prefix, re.I)
            or re.search(rf"\b(?:for|per)\b[^:;.]{{0,90}}\b(?:{dependent_terms})\b", subject, re.I)
            or re.search(rf"^\s*(?:for\s+)?(?:{dependent_terms})\b", subject, re.I)
            or (
                coin and len(prefix.split()) <= 2
                and re.search(r"\b(?:enlist|conscript|draft|join(?:s|ed)?|involved)\b", str(plan[-1].get("result_rules") or ""), re.I)
            )
        ))
        parent_indices: list[int] = []
        if dependent:
            activation_words = {
                value.casefold() for value in re.findall(
                    rf"\b(?:{dependent_terms})\b", f"{prefix} {subject[:90]}", re.I
                )
            }
            group_stop = {
                "roll", "for", "per", "each", "every", "all", "only", "the", "a", "an", "of", "in",
                "sims", "sim", "men", "women", "male", "female", "eligible", "household", "households",
                "enlisted", "conscripted", "drafted", "arrested", "accused", "infected", "affected",
                "involved", "participant", "participants", "soldier", "soldiers", "missing", "directly",
                "indirect", "aftershock", "injury",
            }
            group_words = {
                value.casefold() for value in re.findall(r"\b[A-Za-z][A-Za-z'-]{2,}\b", prefix)
                if value.casefold() not in group_stop
            }
            candidates = []
            for prior in plan:
                prior_rules = str(prior.get("result_rules") or "")
                prior_text = f"{prior.get('selector','')} {prior_rules}".casefold()
                activation_match = not activation_words or any(word in prior_text for word in activation_words)
                shared = sum(word in prior_text for word in group_words)
                if activation_match:
                    candidates.append((shared, int(prior.get("index") or 0)))
            if candidates:
                best_shared = max(shared for shared, _index in candidates)
                if best_shared:
                    parent_indices = [index for shared, index in candidates if shared == best_shared]
                else:
                    # A shared follow-up such as "Enlisted D20" applies to
                    # every preceding enlistment root, while a chained state
                    # such as "missing" should follow only the nearest match.
                    roots = [index for _shared, index in candidates if not plan[index].get("parent_indices")]
                    parent_indices = roots or [candidates[-1][1]]
                    if activation_words - {"enlisted", "conscripted", "drafted", "soldiers", "soldier"}:
                        parent_indices = [candidates[-1][1]]
            if not parent_indices:
                parent_indices = [len(plan) - 1]
        parent_index = parent_indices[0] if parent_indices else None
        step = {
            "index": len(plan), "die": f"d{sides}", "bad_results": bad_results,
            "result_rules": result_rules or f"{bad_results}: Source-defined adverse result",
            "context": context[:500], "parent_index": parent_index,
            "parent_indices": parent_indices,
            "selector": selector,
            "trigger_results": plan[parent_index]["bad_results"] if parent_index is not None else "",
        }
        interval = source_roll_repeat_interval_years(context)
        if interval:
            step["repeat_interval_years"] = interval
            step["cadence"] = "annual" if interval == 1 else f"every {interval} years"
        plan.append(step)
    return _normalize_source_plan_cadence(plan, notes)


def _source_numbered_table_roll_plan(notes: object) -> list[dict]:
    """Recover a die when a source says “roll once” then lists its results.

    The revised early-era document uses table-style rules such as “1 — One Sim
    dies” rather than repeating “roll D6” in every entry.
    """
    text = re.sub(r"\s+", " ", str(notes or "")).strip()
    if not re.search(r"\broll\s+(?:once|for\s+(?:the|this|each))\b", text, re.IGNORECASE):
        return []
    matches = list(re.finditer(
        r"(?<!\d)(\d+)(?:\s*[-–—]\s*(\d+))?\s*[–—]\s*(.+?)(?=\s+(?<!\d)\d+(?:\s*[-–—]\s*\d+)?\s*[–—]\s+|$)",
        text,
        re.IGNORECASE,
    ))
    if len(matches) < 2:
        return []
    values: list[tuple[int, int, str]] = []
    for match in matches:
        low, high = int(match.group(1)), int(match.group(2) or match.group(1))
        outcome = match.group(3).strip(" .;")
        if not outcome:
            continue
        values.append((low, high, outcome))
    if len(values) < 2:
        return []
    sides = max(high for _low, high, _outcome in values)
    if sides not in {2, 4, 6, 8, 10, 12, 20, 100}:
        return []
    result_rules = "; ".join(
        f"{low if low == high else f'{low}-{high}'}: {outcome}"
        for low, high, outcome in values
    )
    adverse = [
        str(low) if low == high else f"{low}-{high}"
        for low, high, outcome in values
        if not _source_outcome_is_success(outcome)
    ]
    return [{
        "index": 0, "die": f"d{sides}", "bad_results": ",".join(adverse),
        "result_rules": result_rules, "context": text[:500],
        "parent_index": None, "parent_indices": [], "trigger_results": "",
    }]


def event_source_roll_plan(notes: object, override: dict | None = None, catalog_id: str = "") -> list[dict]:
    """Return the authoritative editable plan for one catalog event."""
    override = override or {}
    if isinstance(override.get("source_roll_plan"), list):
        return _normalize_source_plan_cadence(override["source_roll_plan"], notes)
    if override.get("configured_die") and override.get("configured_bad_results"):
        primary_rules = str(override["configured_bad_results"])
        primary = {
            "index": 0, "die": str(override["configured_die"]),
            "bad_results": _result_numbers(primary_rules) if ":" in primary_rules else primary_rules,
            "result_rules": primary_rules, "context": str(override.get("affected_class") or "Applicable Sims"),
            "parent_index": None, "trigger_results": "",
        }
        plan = [primary]
        if override.get("followup_enabled") and override.get("followup_die") and override.get("followup_bad_results"):
            followup_rules = str(override["followup_bad_results"])
            plan.append({
                "index": 1, "die": str(override["followup_die"]),
                "bad_results": _result_numbers(followup_rules) if ":" in followup_rules else followup_rules,
                "result_rules": followup_rules, "context": str(override.get("followup_label") or "Source follow-up"),
                "parent_index": 0,
                "trigger_results": str(override.get("followup_trigger_results") or primary["bad_results"]),
                "delay_days": int(override.get("followup_delay_days") or 0),
                "delay_years": int(override.get("followup_delay_years") or 0),
                "failure_is_lethal": bool(override.get("followup_failure_is_lethal")),
            })
        return _normalize_source_plan_cadence(plan, notes)
    plan = source_event_roll_plan(notes) or _source_numbered_table_roll_plan(notes)
    for index, changes in SOURCE_PLAN_RELATION_OVERRIDES.get(catalog_id, {}).items():
        if not (0 <= index < len(plan)):
            continue
        plan[index].update(changes)
        parents = [int(value) for value in plan[index].get("parent_indices") or []]
        plan[index]["parent_indices"] = parents
        plan[index]["parent_index"] = parents[0] if parents else None
    return _normalize_source_plan_cadence(plan, notes)


def due_on_today(record: Record, global_day: int) -> bool:
    """Return whether a record belongs in Today's actionable queue."""
    if record.deleted or record.global_day is None or int(record.global_day) > global_day:
        return False
    data = record.data or {}
    if record.kind == "roll":
        due_day = record.global_day if record.global_day is not None else data.get("due_global_day")
        try:
            return int(due_day) >= 1 and not bool(data.get("completed"))
        except (TypeError, ValueError):
            return False
    if record.kind == "pregnancy":
        return str(data.get("status") or "active").strip().casefold() not in CLOSED_PREGNANCIES
    if record.kind == "event":
        if event_is_ignored(record) or not bool(data.get("active", True)) or bool(data.get("completed")):
            return False
        end_day = data.get("end_global_day")
        return end_day is None or int(end_day) >= global_day
    if record.kind == "illness":
        if str(data.get("status") or "active").strip().casefold() in CLOSED_ILLNESSES:
            return False
        end_day = data.get("end_global_day")
        return end_day in (None, "") or int(end_day) >= global_day
    if record.kind == "death":
        return not bool(data.get("completed"))
    return False


def event_is_ignored(record: Record) -> bool:
    """Return whether an event has been intentionally hidden for this save."""
    return record.kind == "event" and bool((record.data or {}).get("ignored"))


def journal(session: Session, record: Record, operation: str, base_version: int) -> None:
    session.add(Change(
        save_id=record.save_id, device_id="automation", record_id=record.id,
        kind=record.kind, operation=operation, base_version=base_version,
        new_version=record.version, payload=sync.serialize(record),
    ))


def roll_obligation_identity(record: Record) -> tuple[str, str, int] | None:
    """Return the conservative identity used to find duplicate roll obligations.

    A repairable duplicate must point to the same Sim, have the same named roll
    type, and be due on the same Global Day.  Incomplete legacy rows are left
    alone because grouping them without those three facts could merge unrelated
    obligations.
    """
    if record.kind != "roll" or record.deleted:
        return None
    data = record.data or {}
    sim_id = str(data.get("sim_id") or "").strip()
    roll_type = re.sub(r"\s+", " ", str(data.get("roll_type") or "").strip().casefold())
    raw_day = record.global_day if record.global_day is not None else data.get("due_global_day")
    try:
        due_day = int(raw_day)
    except (TypeError, ValueError):
        return None
    if not sim_id or not roll_type or due_day < 1:
        return None
    return sim_id, roll_type, due_day


def _obligation_keeper(record: Record) -> tuple[int, int, str, str]:
    """Prefer the richest and most recently maintained pending obligation."""
    data = record.data or {}
    useful = (
        "die", "bad_results", "result_rules", "failure_outcome", "success_outcome",
        "source", "source_id", "event_id", "event_rule_id", "pregnancy_id",
    )
    richness = sum(value not in (None, "", [], {}) for value in (data.get(key) for key in useful))
    return richness, int(record.version or 0), str(record.updated_at or record.created_at or ""), record.id


def duplicate_obligation_groups(records: list[Record]) -> list[dict]:
    """Describe active duplicate obligations and which pending rows are repairable."""
    identities: dict[tuple[str, str, int], list[Record]] = defaultdict(list)
    for record in records:
        identity = roll_obligation_identity(record)
        if identity is not None:
            identities[identity].append(record)

    groups = []
    for identity, matches in identities.items():
        if len(matches) < 2:
            continue
        completed = [item for item in matches if bool((item.data or {}).get("completed"))]
        pending = [item for item in matches if not bool((item.data or {}).get("completed"))]
        keeper = max(pending, key=_obligation_keeper) if pending and not completed else None
        redundant = list(pending) if completed else [item for item in pending if item is not keeper]
        example = completed[0] if completed else keeper or matches[0]
        groups.append({
            "identity": identity,
            "label": example.label,
            "matches": matches,
            "pending": pending,
            "completed": completed,
            "keeper": completed[0] if completed else keeper,
            "redundant": redundant,
        })
    return sorted(groups, key=lambda group: (group["identity"][2], group["label"].casefold()))


def duplicate_obligation_summary(records: list[Record]) -> dict:
    groups = duplicate_obligation_groups(records)
    return {
        "groups": len(groups),
        "repairable": sum(len(group["redundant"]) for group in groups),
        "protected_completed": sum(max(0, len(group["completed"]) - 1) for group in groups),
        "preview": [{
            "label": group["label"],
            "global_day": group["identity"][2],
            "copies": len(group["matches"]),
            "repairable": len(group["redundant"]),
            "completed": len(group["completed"]),
        } for group in groups[:12]],
    }


def repair_duplicate_obligations(session: Session, save: ChronicleSave) -> dict:
    """Archive redundant pending obligations without touching completed results."""
    rolls = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "roll",
        Record.deleted.is_(False),
    )))
    groups = duplicate_obligation_groups(rolls)
    archived = 0
    for group in groups:
        keeper = group["keeper"]
        for record in group["redundant"]:
            base = record.version
            record.deleted = True
            record.data = {
                **(record.data or {}),
                "duplicate_repair": True,
                "duplicate_of": keeper.id if keeper else "",
                "retired_reason": "Duplicate obligation",
                "retired_global_day": save.global_day,
            }
            record.version += 1
            journal(session, record, "delete", base)
            archived += 1
    save.revision += archived
    return {
        "groups": len(groups),
        "archived": archived,
        "protected_completed": sum(max(0, len(group["completed"]) - 1) for group in groups),
    }


AUTOMATIC_GENERATION_SOURCES = {"parents", "spouse"}


def surname_at_birth(sim: Record) -> str:
    """Return the preserved family surname a Sim had at birth."""
    data = sim.data or {}
    return str(data.get("surname_at_birth") or data.get("maiden_name") or data.get("last_name") or "").strip()


def married_surname(sim: Record) -> str:
    data = sim.data or {}
    return str(data.get("married_surname") or data.get("married_name") or "").strip()


def _sim_display_name(data: dict) -> str:
    return " ".join(
        str(data.get(key) or "").strip()
        for key in ("title", "first_name", "last_name", "suffix")
        if str(data.get(key) or "").strip()
    )


def apply_married_surnames(
    session: Session,
    relationship: Record,
    first: Record,
    second: Record,
    surname_rule: str = "automatic",
    respect_existing: bool = False,
) -> int:
    """Preserve birth surnames and apply a marriage naming convention."""
    normalized = str(surname_rule or "automatic").strip().casefold().replace("-", "_")
    allowed = {"automatic", "partner1_takes_partner2", "partner2_takes_partner1", "keep", "hyphenate"}
    if normalized not in allowed:
        normalized = "automatic"

    sims = (first, second)
    proposed: dict[str, dict] = {sim.id: dict(sim.data or {}) for sim in sims}
    for sim in sims:
        data = proposed[sim.id]
        birth = surname_at_birth(sim)
        if birth:
            data["surname_at_birth"] = birth
            data["maiden_name"] = birth  # legacy export/import compatibility

    relation_data = relationship.data or {}
    relation_type = str(relation_data.get("type") or "").casefold()
    status = str(relation_data.get("status") or "Active").casefold()
    is_marriage = bool(relation_data.get("legally_married")) or "marriage" in relation_type
    if is_marriage and status not in {"ended", "divorced", "annulled"} and normalized != "keep":
        first_current = str(proposed[first.id].get("last_name") or surname_at_birth(first)).strip()
        second_current = str(proposed[second.id].get("last_name") or surname_at_birth(second)).strip()
        targets: list[tuple[Record, str]] = []
        if normalized == "hyphenate":
            parts = [surname_at_birth(first) or first_current, surname_at_birth(second) or second_current]
            unique_parts = [part for index, part in enumerate(parts) if part and part not in parts[:index]]
            combined = "-".join(unique_parts)
            targets = [(first, combined), (second, combined)] if combined else []
        else:
            if normalized == "automatic":
                first_female = str(proposed[first.id].get("sex") or "").casefold() in {"female", "woman", "f"}
                second_female = str(proposed[second.id].get("sex") or "").casefold() in {"female", "woman", "f"}
                normalized = "partner1_takes_partner2" if first_female and not second_female else "partner2_takes_partner1"
            targets = [(first, second_current)] if normalized == "partner1_takes_partner2" else [(second, first_current)]
        for target, new_surname in targets:
            if not new_surname:
                continue
            data = proposed[target.id]
            if respect_existing and married_surname(target) and str(data.get("married_name_source_relationship_id") or "") != relationship.id:
                continue
            data["last_name"] = new_surname
            data["married_surname"] = new_surname
            data["married_name"] = new_surname  # legacy export/import compatibility
            data["married_name_source_relationship_id"] = relationship.id

    changed = 0
    for sim in sims:
        data = proposed[sim.id]
        if data == (sim.data or {}):
            continue
        base = sim.version
        sim.data = data
        sim.label = _sim_display_name(data) or sim.label
        sim.version += 1
        journal(session, sim, "upsert", base)
        changed += 1
    return changed


def backfill_married_surnames(session: Session, save: ChronicleSave) -> int:
    """Bring existing active marriages into the automatic name-history model."""
    sims = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False)
    )))
    by_id = {sim.id: sim for sim in sims}
    relationships = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "relationship", Record.deleted.is_(False)
    ).order_by(Record.global_day, Record.created_at)))
    changed = 0
    for relationship in relationships:
        data = relationship.data or {}
        first = by_id.get(str(data.get("partner1_id") or ""))
        second = by_id.get(str(data.get("partner2_id") or ""))
        if first and second:
            changed += apply_married_surnames(
                session, relationship, first, second, str(data.get("surname_rule") or "automatic"), respect_existing=True
            )
    return changed


def sync_generations(session: Session, save: ChronicleSave) -> int:
    """Fill and maintain automatic generations from parents, then spouses.

    A known parent's generation always wins and places the child one generation
    later.  A spouse is used only when both parent links are unknown, and spouses
    share the same generation.  Existing generations without an automatic source
    are treated as intentional manual values and are never overwritten.
    """
    sims = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "sim",
        Record.deleted.is_(False),
    )))
    if not sims:
        return 0
    by_id = {sim.id: sim for sim in sims}
    relationships = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "relationship",
        Record.deleted.is_(False),
    ).order_by(Record.global_day.desc(), Record.created_at.desc())))
    spouses: dict[str, list[str]] = {sim.id: [] for sim in sims}
    for relationship in relationships:
        data = relationship.data or {}
        relation_type = str(data.get("type") or "").casefold()
        status = str(data.get("status") or "active").casefold()
        married = bool(data.get("legally_married")) or "marriage" in relation_type or "spouse" in relation_type
        if not married or status in {"ended", "divorced", "annulled"}:
            continue
        first, second = str(data.get("partner1_id") or ""), str(data.get("partner2_id") or "")
        if first in by_id and second in by_id and first != second:
            spouses[first].append(second)
            spouses[second].append(first)

    changed = 0
    # Several passes allow a parent's or spouse's newly inferred value to feed
    # the next relationship without recursion or expensive graph rebuilding.
    for _ in range(max(1, len(sims))):
        pass_changed = False
        for sim in sims:
            data = dict(sim.data or {})
            current_generation = data.get("generation")
            current_source = str(data.get("generation_source") or "").casefold()
            if current_generation not in (None, "") and current_source not in AUTOMATIC_GENERATION_SOURCES:
                continue

            parent_ids = [str(data.get(field) or "") for field in ("mother_id", "father_id")]
            parent_ids = [parent_id for parent_id in parent_ids if parent_id in by_id]
            parent_generations = [
                int((by_id[parent_id].data or {}).get("generation"))
                for parent_id in parent_ids
                if (by_id[parent_id].data or {}).get("generation") not in (None, "")
            ]
            inferred = max(parent_generations) + 1 if parent_generations else None
            source = "parents" if inferred is not None else None
            source_ids = parent_ids if inferred is not None else []

            # A spouse is a fallback only when both parents are genuinely
            # unknown, not when a linked parent's generation is merely missing.
            if inferred is None and not parent_ids:
                for spouse_id in spouses.get(sim.id, []):
                    spouse_generation = (by_id[spouse_id].data or {}).get("generation")
                    if spouse_generation not in (None, ""):
                        inferred = int(spouse_generation)
                        source = "spouse"
                        source_ids = [spouse_id]
                        break

            normalized_current = int(current_generation) if current_generation not in (None, "") else None
            existing_ids = list(data.get("generation_source_ids") or [])
            if normalized_current == inferred and current_source == (source or "") and existing_ids == source_ids:
                continue
            base = sim.version
            data["generation"] = inferred
            if source:
                data["generation_source"] = source
                data["generation_source_ids"] = source_ids
            else:
                data.pop("generation_source", None)
                data.pop("generation_source_ids", None)
            sim.data = data
            sim.version += 1
            journal(session, sim, "upsert", base)
            changed += 1
            pass_changed = True
        if not pass_changed:
            break
    return changed


SIM_SCALAR_REFERENCES = {
    "sim_id", "mother_id", "father_id", "partner1_id", "partner2_id",
    "other_sim_id", "head_id", "head_sim_id", "head_of_household_id", "heir_id",
    "current_heir_id", "first_recorded_sim_id", "inferred_mother_id",
    "inferred_father_id", "inferred_other_parent_id",
}
SIM_LIST_REFERENCES = {"sim_ids", "member_ids", "parent_ids", "children_ids", "affected_sim_ids"}
SIM_DEPENDENT_KINDS = {
    "relationship", "pregnancy", "roll", "event_result", "illness", "death",
    "game_candidate", "game_history", "detection_candidate", "task",
}


def sim_delete_impact(session: Session, sim: Record) -> dict:
    """Describe every tracker row that a permanent Sim deletion would touch."""
    from collections import Counter

    records = list(session.scalars(select(Record).where(
        Record.save_id == sim.save_id, Record.id != sim.id, Record.deleted.is_(False),
    )))
    dependent, detached = [], []
    for record in records:
        data = record.data or {}
        scalar_hit = any(str(data.get(key) or "") == sim.id for key in SIM_SCALAR_REFERENCES)
        list_hit = any(sim.id in {str(value) for value in (data.get(key) or [])}
                       for key in SIM_LIST_REFERENCES if isinstance(data.get(key), list))
        if not scalar_hit and not list_hit:
            continue
        (dependent if record.kind in SIM_DEPENDENT_KINDS else detached).append(record)
    portraits = list(session.scalars(select(Portrait).where(Portrait.record_id == sim.id)))
    return {"dependent":dependent, "detached":detached, "portraits":portraits,
            "dependent_counts":dict(Counter(item.kind for item in dependent)),
            "detached_counts":dict(Counter(item.kind for item in detached))}


def purge_sim(session: Session, save: ChronicleSave, sim: Record) -> dict:
    """Permanently remove an accidental Sim while preserving referential integrity."""
    if sim.kind != "sim" or sim.save_id != save.id:
        raise ValueError("Only a Sim in the open save can be deleted.")
    impact = sim_delete_impact(session, sim)
    for record in impact["dependent"]:
        base = record.version
        record.deleted = True
        record.data = {**(record.data or {}), "archived_reason":"Related Sim was permanently deleted", "deleted_sim_id":sim.id}
        record.version += 1
        journal(session, record, "delete", base)
    for record in impact["detached"]:
        data = dict(record.data or {})
        changed = False
        for key in SIM_SCALAR_REFERENCES:
            if str(data.get(key) or "") == sim.id:
                data[key] = None; changed = True
        for key in SIM_LIST_REFERENCES:
            values = data.get(key)
            if isinstance(values, list) and sim.id in {str(value) for value in values}:
                data[key] = [value for value in values if str(value) != sim.id]; changed = True
        if changed:
            base = record.version; record.data = data; record.version += 1; journal(session, record, "upsert", base)
    for portrait in impact["portraits"]:
        sync.sync_portrait(session, save, portrait, sim.id, portrait.stage, deleted=True)
        session.delete(portrait)
    settings = dict(save.settings or {})
    for key in ("current_heir_id", "main_sim_id", "founder_sim_id"):
        if str(settings.get(key) or "") == sim.id:
            settings[key] = None
    save.settings = settings
    base = sim.version
    sim.deleted = True; sim.version += 1
    journal(session, sim, "delete", base)
    session.flush()
    session.delete(sim)
    save.revision += 1 + len(impact["dependent"]) + len(impact["detached"]) + len(impact["portraits"])
    return {"archived":len(impact["dependent"]), "detached":len(impact["detached"]), "portraits":len(impact["portraits"])}


def end_illnesses_for_death(session: Session, save: ChronicleSave, sim: Record, death_day: int) -> int:
    """Close every still-active illness for a Sim on their recorded death day."""
    if int(death_day) > int(save.global_day) and not bool((sim.data or {}).get("death_confirmed")):
        return 0
    illnesses = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "illness",
        Record.deleted.is_(False),
        Record.data["sim_id"].as_string() == sim.id,
    )))
    changed = 0
    for illness in illnesses:
        data = dict(illness.data or {})
        closed = str(data.get("status") or "active").strip().casefold() in CLOSED_ILLNESSES
        ended_by_death = str(data.get("outcome") or "").strip().casefold() == "ended by death"
        # If a failed roll moves an already scheduled death earlier, move the
        # associated illness end date too instead of leaving it on the old day.
        if closed and not (ended_by_death and int(data.get("end_global_day") or death_day) != int(death_day)):
            continue
        base = illness.version
        data.update({"status": "Deceased", "end_global_day": int(death_day), "outcome": "Ended by death"})
        illness.data = data
        illness.version += 1
        journal(session, illness, "upsert", base)
        changed += 1
    return changed


def retire_pregnancy_rolls(session: Session, save: ChronicleSave, pregnancy_id: str, reason: str = "Pregnancy closed") -> int:
    """Archive unfinished maternal rolls after a loss, cancellation, or deletion.

    A completed delivery still needs its maternal roll.  Delivery handlers use
    ``preserve_delivery_maternal_rolls`` instead so accepting the delivery does
    not make the obligation disappear before the player rolls it.
    """
    rolls = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "roll",
        Record.deleted.is_(False),
    )))
    changed = 0
    source_prefix = f"maternal:{pregnancy_id}:"
    for roll in rolls:
        data = dict(roll.data or {})
        if data.get("completed"):
            continue
        if data.get("source_id") != pregnancy_id and not str(data.get("source") or "").startswith(source_prefix):
            continue
        base = roll.version
        roll.deleted = True
        roll.data = {**data, "retired_reason": reason, "retired_global_day": save.global_day}
        roll.version += 1
        journal(session, roll, "delete", base)
        changed += 1
    return changed


def pregnancy_keeps_maternal_roll(status: object) -> bool:
    return str(status or "").strip().casefold() in DELIVERY_PREGNANCIES


def pregnancy_retires_maternal_roll(status: object) -> bool:
    return str(status or "").strip().casefold() in MATERNAL_ROLL_RETIRE_PREGNANCIES


def _delivery_retirement_reason(reason: object) -> bool:
    text = str(reason or "").strip().casefold()
    return any(token in text for token in (
        "deliver", "stillbirth", "reviewed as complete", "resolved as complete",
    ))


def maternal_rule_for_day(
    save: ChronicleSave,
    rules: list[Record],
    mother: Record | None,
    day: int,
) -> Record | None:
    """Return the maternal table that applies to one delivery day.

    Both the normal scheduler and delivery recovery use this same selection
    rule.  Keeping it in one place prevents a completed pregnancy from being
    unable to recover an obligation merely because it arrived before the
    periodic scheduler had created it.
    """
    if not mother:
        return None
    birth = (mother.data or {}).get("birth_global_day", mother.global_day)
    if birth is None:
        return None
    try:
        age = int(day) - int(birth)
    except (TypeError, ValueError):
        return None
    stage = (
        "preteen" if age < 52 else "teen" if age < 72 else
        "young adult" if age < 160 else "adult" if age < 240 else "elder"
    )
    due_year = save.start_year + (int(day) - 1) // max(1, save.days_per_year)
    eligible = [
        rule for rule in rules
        if int((rule.data or {}).get("start_year", -9999)) <= due_year
        <= int((rule.data or {}).get("end_year", 9999))
    ]
    return (
        next((rule for rule in eligible if stage in rule.label.casefold()), None)
        or next((
            rule for rule in eligible
            if "all ages" in rule.label.casefold() or "birth" in rule.label.casefold()
        ), None)
    )


def preserve_delivery_maternal_rolls(
    session: Session,
    save: ChronicleSave,
    pregnancy: Record,
    delivery_day: int | None = None,
    *,
    restore_retired: bool = False,
    create_missing: bool = True,
) -> int:
    """Keep unfinished maternal obligations visible after delivery acceptance.

    Existing pending rolls are re-anchored to the confirmed delivery day.  If
    a delivery is received before periodic scheduling had created its roll, a
    current delivery flow creates the one missing obligation.  The optional
    repair mode revives only rolls that older versions explicitly retired as a
    delivery; rolls retired for a miscarriage, cancellation, archive, or
    ruleset change remain retired.
    """
    pregnancy_data = dict(pregnancy.data or {})
    if not pregnancy_data.get("maternal_rolls_required", True):
        return 0
    day = int(
        delivery_day
        or pregnancy_data.get("actual_delivery_global_day")
        or pregnancy_data.get("delivery_global_day")
        or pregnancy_data.get("due_global_day")
        or pregnancy.global_day
        or save.global_day
    )
    mother = session.get(Record, str(pregnancy_data.get("mother_id") or ""))
    rolls = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "roll",
    )))
    source_prefix = f"maternal:{pregnancy.id}:"
    all_matching_rolls = [
        roll for roll in rolls
        if (
            (roll.data or {}).get("source_id") == pregnancy.id
            or str((roll.data or {}).get("source") or "").startswith(source_prefix)
        )
    ]
    matching_rolls = [roll for roll in all_matching_rolls if not (roll.data or {}).get("completed")]
    active_rolls = [roll for roll in matching_rolls if not roll.deleted]
    restorable_id = None
    if restore_retired and not active_rolls:
        retired_by_delivery = [
            roll for roll in matching_rolls
            if roll.deleted and _delivery_retirement_reason((roll.data or {}).get("retired_reason"))
        ]
        if retired_by_delivery:
            restorable_id = max(
                retired_by_delivery,
                key=lambda item: (item.updated_at, item.version, item.id),
            ).id
    changed = 0
    for roll in matching_rolls:
        data = dict(roll.data or {})
        if roll.deleted and roll.id != restorable_id:
            continue
        updates = {
            "source_id": pregnancy.id,
            "due_global_day": day,
            "delivery_global_day": day,
            "delivery_confirmed": True,
            "maternal_roll_preserved_after_delivery": True,
        }
        if mother:
            updates.update({"sim_id": mother.id, "sim_name": mother.label})
        new_data = {**data, **updates}
        new_data.pop("retired_reason", None)
        new_data.pop("retired_global_day", None)
        new_label = f"{mother.label} — {data.get('roll_type')}" if mother and data.get("roll_type") else roll.label
        if not roll.deleted and roll.global_day == day and roll.label == new_label and new_data == data:
            continue
        base = roll.version
        roll.deleted = False
        roll.global_day = day
        roll.label = new_label
        roll.data = new_data
        roll.version += 1
        journal(session, roll, "upsert", base)
        changed += 1
    # When there is already a completed or retired history record, preserve
    # that history rather than creating a second pending maternal roll.  A
    # genuinely missing record is safe to create only in the live delivery
    # workflow; broad historic repair remains revive-only.
    if create_missing and not all_matching_rolls and mother and not mother.deleted:
        mother_data = mother.data or {}
        mother_death = mother_data.get("death_global_day")
        try:
            mother_death_has_arrived = mother_death is not None and int(mother_death) <= int(save.global_day)
        except (TypeError, ValueError):
            mother_death_has_arrived = False
        mother_is_unavailable = (
            bool(mother_data.get("game_was_dead"))
            or "Servo" in occult_rules.sim_occult_types(mother_data)
            or mother_death_has_arrived
        )
        if not mother_is_unavailable and (save.settings or {}).get("maternal_rolls_enabled", True):
            rules = [
                item for item in session.scalars(select(Record).where(
                    Record.save_id == save.id,
                    Record.kind == "roll_rule",
                    Record.deleted.is_(False),
                ))
                if core_rulesets.applies_to_selected_core(save, item)
                and "maternal" in item.label.casefold()
                and (item.data or {}).get("active", True)
            ]
            rule = maternal_rule_for_day(save, rules, mother, day)
            if rule:
                roll = Record(
                    save_id=save.id,
                    kind="roll",
                    label=f"{mother.label} — {rule.label}",
                    global_day=day,
                    data={
                        "sim_id": mother.id,
                        "sim_name": mother.label,
                        "source_id": pregnancy.id,
                        "roll_type": rule.label,
                        "die": (rule.data or {}).get("die"),
                        "bad_results": (rule.data or {}).get("bad_results"),
                        "source": f"maternal:{pregnancy.id}:{rule.id}",
                        "due_global_day": day,
                        "delivery_global_day": day,
                        "delivery_confirmed": True,
                        "maternal_roll_preserved_after_delivery": True,
                        "completed": False,
                        "core_ruleset_id": (rule.data or {}).get("core_ruleset_id"),
                        "core_source_rule_id": (rule.data or {}).get("source_rule_id"),
                    },
                )
                session.add(roll)
                session.flush()
                journal(session, roll, "upsert", 0)
                changed += 1
    return changed


def restore_delivery_maternal_rolls(session: Session, save: ChronicleSave) -> int:
    """Repair maternal rolls hidden by pre-4.4.7 delivery acceptance."""
    pregnancies = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "pregnancy",
        Record.deleted.is_(False),
    )))
    return sum(
        preserve_delivery_maternal_rolls(
            session, save, pregnancy, restore_retired=True, create_missing=False,
        )
        for pregnancy in pregnancies
        if pregnancy_keeps_maternal_roll((pregnancy.data or {}).get("status"))
    )


def retire_dead_sim_rolls(session: Session, save: ChronicleSave, sims: list[Record] | None = None) -> int:
    """Archive every unfinished roll once its Sim's death day has arrived."""
    sims = sims if sims is not None else list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False)
    )))
    dead_ids = {
        sim.id for sim in sims
        if bool((sim.data or {}).get("game_was_dead")) or (
            sim.data.get("death_global_day") is not None
            and int(sim.data["death_global_day"]) <= save.global_day
        )
    }
    if not dead_ids:
        return 0
    rolls = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False)
    )))
    changed = 0
    for roll in rolls:
        data = dict(roll.data or {})
        post_death_roll = bool(data.get("allow_after_death")) or (bool(data.get("occult_roll")) and data.get("occult_rule_key") == "ghost_persistence")
        if data.get("completed") or data.get("sim_id") not in dead_ids or post_death_roll:
            continue
        base = roll.version
        roll.deleted = True
        roll.data = {**data, "retired_reason": "Sim is deceased", "retired_global_day": save.global_day}
        roll.version += 1
        journal(session, roll, "delete", base)
        changed += 1
    return changed


def retire_prechallenge_rolls(session: Session, save: ChronicleSave) -> int:
    """Archive unfinished obligations dated before Global Day 1.

    Events and Sims may legitimately retain pre-challenge dates for historical
    context.  Those dates must never become actionable rolls, while completed
    imported results remain part of the chronicle.
    """
    rolls = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False)
    )))
    changed = 0
    for roll in rolls:
        data = dict(roll.data or {})
        raw_day = roll.global_day if roll.global_day is not None else data.get("due_global_day")
        try:
            due_day = int(raw_day)
        except (TypeError, ValueError):
            continue
        if data.get("completed") or due_day >= 1:
            continue
        base = roll.version
        roll.deleted = True
        roll.data = {
            **data,
            "retired_reason": "Obligation predates challenge start",
            "retired_global_day": save.global_day,
        }
        roll.version += 1
        journal(session, roll, "delete", base)
        changed += 1
    return changed


def seed_defaults(session: Session, save: ChronicleSave) -> int:
    created = 0
    existing_rule_records = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll_rule", Record.deleted.is_(False)
    )))
    existing_rules = {item.label.casefold() for item in existing_rule_records}
    existing_aging_rules = {
        item.label.casefold() for item in existing_rule_records
        if (item.data or {}).get("age_days") not in (None, "")
    }
    for stage, age, die, bad in DEFAULT_STAGES:
        if stage.casefold() in existing_aging_rules:
            continue
        record = Record(save_id=save.id, kind="roll_rule", label=stage, data={"age_days": age, "die": die, "bad_results": bad, "active": True, "source":"built-in SeveralUDO baseline", "core_ruleset_id":core_rulesets.SEVERALUDO, "death_age_rng":"elder" in stage.casefold()})
        session.add(record); session.flush(); journal(session, record, "upsert", 0); created += 1
    for label, die, bad in DEFAULT_MATERNAL_RULES:
        if label.casefold() in existing_rules:
            continue
        record = Record(save_id=save.id, kind="roll_rule", label=label, data={"age_days": None, "die": die, "bad_results": bad, "active": True, "source": "built-in maternal baseline", "core_ruleset_id":core_rulesets.SEVERALUDO})
        session.add(record); session.flush(); journal(session, record, "upsert", 0); created += 1
    existing_causes = {item.label.casefold() for item in session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "death_causes", Record.deleted.is_(False)))}
    for group, causes in DEFAULT_DEATH_CAUSES.items():
        if group.casefold() in existing_causes:
            continue
        record = Record(save_id=save.id, kind="death_causes", label=group.title(), data={"causes": causes, "active": True})
        session.add(record); session.flush(); journal(session, record, "upsert", 0); created += 1
    existing_guidance = {item.label.casefold() for item in session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "era_guidance", Record.deleted.is_(False)))}
    for title, category, start_year, end_year, text in DEFAULT_ERA_GUIDANCE:
        if title.casefold() in existing_guidance:
            continue
        record = Record(save_id=save.id, kind="era_guidance", label=title, data={"category": category, "start_year": start_year, "end_year": end_year, "location": "All", "rule_text": text, "active": True, "source": "Built-in editable baseline", "core_ruleset_id":core_rulesets.SEVERALUDO})
        session.add(record); session.flush(); journal(session, record, "upsert", 0); created += 1
    existing_planner = {(item.label.casefold(), int(item.data.get("start_year", -9999))) for item in session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "planner_rule", Record.deleted.is_(False)))}
    for label, start_year, end_year, die, bad, notes in DEFAULT_PLANNER_RULES:
        if (label.casefold(), start_year) in existing_planner:
            continue
        record = Record(save_id=save.id, kind="planner_rule", label=label, data={"start_year": start_year, "end_year": end_year, "die": die, "bad_results": bad, "notes": notes, "active": True, "core_ruleset_id":None if label=="Remarriage Eligibility" else core_rulesets.SEVERALUDO})
        session.add(record); session.flush(); journal(session, record, "upsert", 0); created += 1
    existing_multiple={(int(item.data.get("start_year",-9999)),int(item.data.get("end_year",9999))) for item in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="multiple_birth_rule",Record.deleted.is_(False)))}
    for start_year,end_year in DEFAULT_MULTIPLE_BIRTH_ERAS:
        if (start_year,end_year) in existing_multiple: continue
        record=Record(save_id=save.id,kind="multiple_birth_rule",label=f"Multiple births · {start_year}–{end_year}",data={"start_year":start_year,"end_year":end_year,"max_babies":None,"quintuplet_policy":"","active":True,"notes":"Historical range supplied; enter only sourced limits. Blank means no enforced limit."})
        session.add(record);session.flush();journal(session,record,"upsert",0);created+=1
    created += repair_default_aging_tables(session, save)
    created += seed_occult_rules(session, save)
    created += core_rulesets.sync_rules(session, save)
    save.revision += created
    event_created=seed_event_catalog(session,save)
    generation_updates=sync_generations(session,save)
    save.revision+=generation_updates
    save_settings=dict(save.settings or {});save_settings["defaults_schema_version"]=DEFAULTS_SCHEMA_VERSION;save.settings=save_settings
    return created+event_created+generation_updates


def seed_occult_rules(session: Session, save: ChronicleSave) -> int:
    """Install and version the supplied occult rules without duplicating them."""
    repaired = repair_duplicate_occult_rules(session, save)
    existing_records = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "occult_rule", Record.deleted.is_(False)
    )))
    existing = {_occult_rule_identity(item.data): item for item in existing_records}
    created = 0
    for definition in occult_rules.DEFAULT_OCCULT_RULES:
        data = {key:value for key,value in definition.items() if key != "label"}
        default_id = f"{data['rule_key']}:{data.get('start_year',-9999)}:{data.get('end_year',9999)}"
        data["default_id"] = default_id
        identity = _occult_rule_identity(data)
        if identity in existing:
            # Alignment was originally shipped as a manual guidance rule.  A
            # version marker upgrades it once, so a player's later choice to
            # turn this individual rule off is respected on future startups.
            current = existing[identity]
            current_data = dict(current.data or {})
            if (
                data.get("rule_key") == "alignment_inheritance"
                and int(current_data.get("alignment_automation_version") or 0) < 1
            ):
                base = current.version
                current_data.update({"auto_schedule": True, "alignment_automation_version": 1})
                current.data = current_data
                current.version += 1
                journal(session, current, "upsert", base)
                created += 1
            continue
        record = Record(save_id=save.id, kind="occult_rule", label=definition["label"], data=data)
        session.add(record); session.flush(); journal(session, record, "upsert", 0)
        existing[identity] = record; created += 1
    return repaired + created


def _occult_rule_identity(data: dict | None) -> tuple[str, int, int] | None:
    """Return the unique era-specific identity of an occult rule definition."""
    values = data or {}
    key = str(values.get("rule_key") or values.get("key") or "").strip()
    if not key:
        return None
    try:
        return key, int(values.get("start_year", -9999)), int(values.get("end_year", 9999))
    except (TypeError, ValueError):
        return None


def repair_duplicate_occult_rules(session: Session, save: ChronicleSave) -> int:
    """Archive repeated era rules while preserving the oldest, editable copy.

    Early 4.x saves could receive a second built-in set because the original
    rows predated ``default_id``. Keeping both rows caused household and moon
    schedulers to create the same obligation twice. Completed rolls are never
    changed by this repair.
    """
    grouped: dict[tuple[str, int, int], list[Record]] = defaultdict(list)
    for rule in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "occult_rule", Record.deleted.is_(False)
    )):
        identity = _occult_rule_identity(rule.data)
        if identity is not None:
            grouped[identity].append(rule)

    changed = 0
    for (key, start, end), matches in grouped.items():
        if len(matches) < 2:
            continue
        matches.sort(key=lambda item: (str(item.created_at or ""), item.id))
        keeper = matches[0]
        default_id = f"{key}:{start}:{end}"
        if str((keeper.data or {}).get("default_id") or "") != default_id:
            base = keeper.version
            keeper.data = {**(keeper.data or {}), "default_id": default_id}
            keeper.version += 1; journal(session, keeper, "upsert", base); changed += 1
        for duplicate in matches[1:]:
            base = duplicate.version
            duplicate.deleted = True
            duplicate.data = {
                **(duplicate.data or {}),
                "archived_reason": "Duplicate occult rule definition",
                "canonical_occult_rule_id": keeper.id,
            }
            duplicate.version += 1; journal(session, duplicate, "delete", base); changed += 1
    return changed


def multiple_birth_limit(session: Session, save: ChronicleSave, global_day: int | None) -> dict | None:
    day=int(global_day if global_day is not None else save.global_day)
    year=save.start_year+(day-1)//max(1,save.days_per_year)
    matches=[]
    for item in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="multiple_birth_rule",Record.deleted.is_(False))):
        data=item.data or {}
        if not bool(data.get("active",True)) or (data.get("max_babies") in (None,"") and not data.get("quintuplet_policy")): continue
        start=int(data.get("start_year",-9999));end=int(data.get("end_year",9999))
        if start<=year<=end: matches.append((end-start,item))
    if not matches: return None
    item=min(matches,key=lambda pair:pair[0])[1]
    raw_max=item.data.get("max_babies")
    return {"record":item,"year":year,"max_babies":max(1,int(raw_max)) if raw_max not in (None,"") else None,"quintuplet_policy":str(item.data.get("quintuplet_policy") or "")}


def validate_multiple_birth_count(session: Session, save: ChronicleSave, global_day: int | None, count: int) -> None:
    rule=multiple_birth_limit(session,save,global_day)
    if rule and rule["max_babies"] is not None and int(count)>rule["max_babies"]:
        raise ValueError(f"The editable multiple-birth rule for {rule['year']} allows at most {rule['max_babies']} babies. Correct the count or edit that rule first.")
    policy=str((rule or {}).get("quintuplet_policy") or "").casefold()
    if rule and int(count)==5 and any(marker in policy for marker in ("reroll","not allowed","disallow")):
        raise ValueError(f"The editable multiple-birth rule for {rule['year']} says quintuplets must be rerolled. Correct the count or edit that rule first.")


def repair_default_aging_tables(session: Session, save: ChronicleSave) -> int:
    """Repair only the known incorrect 4.x lifecycle defaults and pending rolls.

    The migration fingerprint is intentionally strict: a player-edited table is
    preserved. Completed obligations are also preserved as historical facts.
    """
    desired = {
        label.casefold(): {"label": label, "age_days": age, "die": die, "bad_results": bad}
        for label, age, die, bad in DEFAULT_STAGES
    }
    rules = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "roll_rule",
        Record.deleted.is_(False),
    )))
    changed = 0
    canonical_rules: dict[str, Record] = {}
    for rule in rules:
        key = rule.label.strip().casefold()
        target = desired.get(key)
        if not target:
            continue
        data = dict(rule.data or {})
        if str(data.get("core_ruleset_id") or core_rulesets.SEVERALUDO) != core_rulesets.SEVERALUDO:
            continue
        if data.get("age_days") in (None, "") or int(data.get("age_days")) != int(target["age_days"]):
            continue
        canonical_rules[rule.id] = rule
        current = (str(data.get("die") or ""), str(data.get("bad_results") or ""))
        target_values = (target["die"], target["bad_results"])
        migration_values = LEGACY_INCORRECT_STAGES.get(key)
        updates = {}
        if current == migration_values:
            updates.update(die=target["die"], bad_results=target["bad_results"])
        elif current != target_values:
            # The player has edited this table; keep their version authoritative.
            continue
        updates.update({
            "source": data.get("source") or "built-in SeveralUDO baseline",
            "core_ruleset_id": core_rulesets.SEVERALUDO,
            "death_age_rng": key == "elder death-age rng",
        })
        if all(data.get(field) == value for field, value in updates.items()):
            continue
        base = rule.version
        rule.data = {**data, **updates}
        rule.version += 1
        journal(session, rule, "upsert", base)
        changed += 1

    if canonical_rules:
        pending = session.scalars(select(Record).where(
            Record.save_id == save.id,
            Record.kind == "roll",
            Record.deleted.is_(False),
        ))
        for roll in pending:
            data = dict(roll.data or {})
            if bool(data.get("completed")):
                continue
            source = str(data.get("source") or "")
            if not source.startswith("aging:"):
                continue
            rule_id = source.rsplit(":", 1)[-1]
            rule = canonical_rules.get(rule_id)
            if not rule:
                continue
            rule_data = rule.data or {}
            updates = {
                "die": rule_data.get("die"),
                "bad_results": rule_data.get("bad_results"),
                "death_age_rng": bool(rule_data.get("death_age_rng")),
                "core_ruleset_id": rule_data.get("core_ruleset_id"),
            }
            if all(data.get(field) == value for field, value in updates.items()):
                continue
            base = roll.version
            roll.data = {**data, **updates, "aging_table_refreshed": True}
            roll.version += 1
            journal(session, roll, "upsert", base)
            changed += 1

    settings_data = dict(save.settings or {})
    settings_data["defaults_schema_version"] = DEFAULTS_SCHEMA_VERSION
    save.settings = settings_data
    return changed


def seed_event_catalog(session: Session, save: ChronicleSave, *, force: bool = False) -> int:
    """Install and repair the approved historical-event catalog without duplicates.

    Earlier builds treated any large partial import as complete.  The integrity
    pass checks stable catalog IDs so every new or migrated save receives every
    approved row while preserving custom events and intentional archives.
    """
    marker = str((save.settings or {}).get("event_catalog_version") or "")
    source_rows = json.loads(gzip.decompress(base64.b64decode(EARLY_EVENT_LIBRARY_GZIP_BASE64)).decode("utf-8"))
    refreshed_sources = {str(row.get("source") or "") for row in source_rows}
    rows = [
        row for row in json.loads(gzip.decompress(base64.b64decode(EVENT_LIBRARY_GZIP_BASE64)).decode("utf-8"))
        # The revised 1200s document is now authoritative.  Its refreshed
        # rows below retain legacy IDs where possible and safely retire rows
        # removed from the source instead of leaving duplicate events.
        if str(row.get("source") or "") not in refreshed_sources
    ]
    rows.extend(source_rows)
    existing_by_id: dict[str, list[Record]] = defaultdict(list)
    for item in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "event",
    )):
        stable_id = str(item.data.get("catalog_id") or item.data.get("event_id") or "")
        if stable_id:
            existing_by_id[stable_id].append(item)
    existing_ids = set(existing_by_id)
    approved_ids={str(row.get("event_id") or "") for row in rows}
    if marker == EVENT_CATALOG_VERSION and approved_ids.issubset(existing_ids) and not force:
        return 0
    def rebase(value):
        if value is None:
            return None
        value = int(value)
        absolute_year = 1200 + (value - 1) // 4
        challenge_day = ((value - 1) % 4) + 1
        return (absolute_year - save.start_year) * save.days_per_year + min(challenge_day, save.days_per_year)

    changed = 0
    for row in rows:
        catalog_id = str(row.get("event_id") or "")
        notes = str(row.get("notes") or "")
        updated_source_event = catalog_id.startswith(("EVT-PRE1000-", "EVT-1000S-", "EVT-1100S-", "EVT-1200S-")) or str(row.get("source") or "") in refreshed_sources
        override = {
            **EARLY_EVENT_ROLL_OVERRIDES_BY_NAME.get(str(row.get("event_name") or ""), {}),
            **ORIGINAL_EVENT_ROLL_OVERRIDES.get(catalog_id, {}),
        }
        roll_plan = event_source_roll_plan(notes, override, catalog_id)
        original_roll_required = catalog_id not in NON_ROLL_CATALOG_IDS and bool(
            roll_plan or source_event_requires_roll(notes) or override
        )
        primary = next((step for step in roll_plan if step.get("parent_index") is None), roll_plan[0] if roll_plan else {})
        first_followup = next((step for step in roll_plan if step.get("parent_index") == primary.get("index", 0)), None)
        source_defaults = {
            "source_roll_plan": roll_plan,
            "source_roll_plan_version": 3,
            "configured_die": primary.get("die") or "",
            "configured_bad_results": primary.get("bad_results") or "",
            "configured_result_rules": primary.get("result_rules") or "",
            "die": primary.get("die") or "",
            "bad_results": primary.get("bad_results") or "",
        }
        if first_followup:
            source_defaults.update({
                "followup_enabled": True,
                "followup_trigger_results": first_followup.get("trigger_results") or primary.get("bad_results") or "",
                "followup_delay_days": int(first_followup.get("delay_days") or 0),
                "followup_delay_years": int(first_followup.get("delay_years") or 0),
                "followup_label": first_followup.get("label") or f"{row.get('event_name') or catalog_id} follow-up",
                "followup_die": first_followup.get("die") or "",
                "followup_bad_results": first_followup.get("result_rules") or first_followup.get("bad_results") or "",
                "followup_failure_is_lethal": bool(first_followup.get("failure_is_lethal")) or _lethal_outcome(first_followup.get("result_rules") or ""),
            })
        if catalog_id in existing_ids:
            # Repair only during this catalog-version migration. Once the marker
            # is current, a player may still turn an individual event roll off.
            for record in existing_by_id[catalog_id]:
                if record.deleted:
                    continue
                base = record.version
                data = dict(record.data or {})
                before = dict(data)
                record_changed = False
                if updated_source_event:
                    source_start, source_end = rebase(row.get("start_global_day")), rebase(row.get("end_global_day"))
                    if record.label != str(row.get("event_name") or catalog_id):
                        record.label = str(row.get("event_name") or catalog_id)
                        record_changed = True
                    if record.global_day != source_start:
                        record.global_day = source_start
                        record_changed = True
                    # This is an explicit source refresh requested for the
                    # revised source documents. Keep a player's hide choice,
                    # but replace the catalogue-owned facts and roll table.
                    source_refresh = {
                        "start_global_day": source_start,
                        "end_global_day": source_end,
                        "scope": row.get("scope") or "Historical event",
                        "location": row.get("location") or "Global",
                        "affected_class": row.get("affected_class") or "All applicable Sims",
                        "source": row.get("source") or "Recovered approved catalog",
                        "notes": notes,
                        "original_roll_required": original_roll_required,
                        "roll_required_source": "Original SeveralUDO event rules" if original_roll_required else "",
                        "source_catalog_revision": EVENT_CATALOG_VERSION,
                    }
                    if original_roll_required:
                        source_refresh.update({
                            **source_defaults,
                            "source_roll_plan": roll_plan,
                            "source_roll_plan_version": 4,
                        })
                    data.update(source_refresh)
                    data.update(override)
                if (
                    catalog_id in NON_ROLL_CATALOG_IDS
                    and bool(data.get("original_roll_required"))
                    and not data.get("configured_bad_results")
                    and not data.get("source_roll_plan")
                ):
                    data.update({
                        "roll_required": False,
                        "original_roll_required": False,
                        "roll_required_source": "",
                        "die": "",
                        "bad_results": "",
                    })
                if original_roll_required and not bool(data.get("roll_required")):
                    data.update({
                        "roll_required": True,
                        "original_roll_required": True,
                        "roll_required_source": "Original SeveralUDO event rules",
                    })
                if original_roll_required:
                    if int(data.get("source_roll_plan_version") or 0) < 3:
                        data["source_roll_plan"] = roll_plan
                        data["source_roll_plan_version"] = 3
                    for key, value in source_defaults.items():
                        if value not in (None, "", [], {}) and data.get(key) in (None, "", [], {}):
                            data[key] = value
                for key, value in override.items():
                    # Replace only untouched recovered placeholders. Explicit
                    # player edits and imported 3.x configurations still win.
                    if key in {"location", "affected_class"}:
                        old_catalog_value = row.get(key)
                        if data.get(key) in (None, "", old_catalog_value):
                            data[key] = value
                    elif data.get(key) in (None, "", [], {}):
                        data[key] = value
                if data != before or record_changed:
                    record.data = data
                    record.version += 1
                    journal(session, record, "upsert", base)
                    changed += 1
            continue
        start = rebase(row.get("start_global_day"))
        end = rebase(row.get("end_global_day"))
        data = {
            "catalog_id": catalog_id, "start_global_day": start, "end_global_day": end,
            "scope": row.get("scope") or "Historical event", "location": row.get("location") or "Global",
            "roll_required": original_roll_required, "affected_class": row.get("affected_class") or "All applicable Sims",
            "active": bool(row.get("active", 1)), "source": row.get("source") or "Recovered approved catalog",
            "notes": notes,
            "original_roll_required": original_roll_required,
            "roll_required_source": "Original SeveralUDO event rules" if original_roll_required else "",
        }
        if original_roll_required:
            data.update(source_defaults)
        if updated_source_event:
            data.update({
                "source_catalog_revision": EVENT_CATALOG_VERSION,
                "source_roll_plan_version": 4,
            })
        data.update(override)
        record = Record(save_id=save.id, kind="event", label=str(row.get("event_name") or catalog_id), global_day=start, data=data)
        session.add(record); session.flush(); journal(session, record, "upsert", 0); changed += 1
    # A revised source may deliberately remove a catalogue row. Deactivate
    # (rather than delete) only source entries covered by the revised documents so history,
    # custom notes and completed rolls remain recoverable.
    for stale_id in existing_ids - approved_ids:
        for record in existing_by_id[stale_id]:
            if record.deleted:
                continue
            data = dict(record.data or {})
            if str(data.get("source") or "") not in refreshed_sources:
                continue
            if data.get("catalog_removed_from_source") and not data.get("active", True):
                continue
            base = record.version
            record.data = {
                **data, "active": False, "catalog_removed_from_source": True,
                "source_catalog_revision": EVENT_CATALOG_VERSION,
            }
            record.version += 1
            journal(session, record, "upsert", base)
            changed += 1
    settings_data = dict(save.settings or {}); settings_data["event_catalog_version"] = EVENT_CATALOG_VERSION
    save.settings = settings_data; save.revision += changed
    return changed


def event_roll_spec(notes: str) -> dict:
    """Extract a lethal event roll from prose without treating enlistment as death."""
    text = str(notes or "").replace(";", ". ")
    rolls = list(re.finditer(r"(?:\broll\s+(?:an?\s+)?(?:the\s+)?)?\bd(\d+)\b", text, re.I))
    if not rolls:
        return {"die": "d20", "bad_results": ""}
    lethal = re.compile(r"\b(?:die|dies|died|death|dead|killed|fatal|executed|hanged|slain|perish(?:es|ed)?)\b", re.I)
    for index, match in enumerate(rolls):
        tail = text[match.end():rolls[index + 1].start() if index + 1 < len(rolls) else len(text)]
        numbers = []
        for clause in re.split(r"[.;\n]+", tail):
            if lethal.search(clause):
                leading = re.match(r"\s*([\d\s,orand\-–—]+)\s*(?:means|:|=)", clause, re.I)
                if leading:
                    ranges = re.findall(r"\d+\s*[-–—]\s*\d+|\d+", leading.group(1))
                    numbers.extend(ranges)
        if numbers:
            return {"die": f"d{match.group(1)}", "bad_results": " ".join(dict.fromkeys(numbers))}
    return {"die": f"d{rolls[0].group(1)}", "bad_results": ""}


def event_key(event: Record) -> str:
    """Return the stable catalog identity used by both 3.x imports and 4.x seeds."""
    data = event.data or {}
    return str(data.get("catalog_id") or data.get("event_id") or data.get("legacy_id") or event.id)


def _event_rule_map(session: Session, save: ChronicleSave) -> dict[str, dict]:
    """Index imported editable event rules without copying or discarding them."""
    result: dict[str, dict] = {}
    rules = session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "event_rule", Record.deleted.is_(False),
    ).order_by(Record.updated_at))
    for record in rules:
        data = dict(record.data or {})
        key = str(data.get("event_id") or data.get("catalog_id") or data.get("legacy_id") or "").strip()
        if key:
            data["record_id"] = record.id
            result[key] = data
    return result


def _result_numbers(text: str) -> str:
    """Extract the die values on the left side of an editable result table."""
    found: list[str] = []
    normalized = str(text or "").replace("–", "-").replace("—", "-")
    for clause in re.split(r"[;\n]+", normalized):
        left = clause.split(":", 1)[0] if ":" in clause else clause
        found.extend(re.findall(r"\d+\s*-\s*\d+|(?<!\d)\d+(?!\d)", left))
    return " ".join(dict.fromkeys(value.replace(" ", "") for value in found))


def _mapped_roll_outcome(actual: int, result_rules: str) -> str:
    """Return the prose attached to a matching numeric result or range."""
    normalized = str(result_rules or "").replace("–", "-").replace("—", "-")
    fallback = ""
    for clause in (part.strip() for part in re.split(r"[;\n]+", normalized) if part.strip()):
        if ":" not in clause:
            continue
        left, outcome = clause.split(":", 1)
        if re.search(r"\b(?:all\s+others?|otherwise|else|any\s+other\s+results?)\b", left, re.I):
            fallback = outcome.strip()
            continue
        if failed(actual, left):
            return outcome.strip()
    return fallback


def _lethal_outcome(text: str) -> bool:
    return bool(re.search(
        r"\b(?:die|dies|died|death|dead|killed|fatal|executed|hanged|slain|perish(?:es|ed)?|"
        r"drown(?:s|ed)?|starv(?:e|es|ed|ation)|murder(?:s|ed)?|succumb(?:s|ed)?)\b",
        str(text or ""), re.I,
    ))


def event_roll_configuration(event: Record, rule_data: dict | None = None) -> dict:
    """Merge native event fields, recovered prose and the imported rule table."""
    data = event.data or {}
    rule = rule_data or {}
    prose = event_roll_spec(data.get("notes") or "")
    configured_bad = str(data.get("configured_bad_results") or "").strip()
    native_bad = str(data.get("bad_results") or "").strip()
    result_rules = str(
        data.get("configured_result_rules") or rule.get("result_rules") or rule.get("bad_results")
        or data.get("result_rules") or (configured_bad if ":" in configured_bad else "")
        or (native_bad if ":" in native_bad else "")
        or ""
    ).strip()
    bad_results = configured_bad if configured_bad and ":" not in configured_bad else ""
    if not bad_results and native_bad and ":" not in native_bad:
        bad_results = native_bad
    if not bad_results:
        bad_results = _result_numbers(configured_bad or result_rules) if ":" in (configured_bad or result_rules) else (configured_bad or result_rules)
    if not bad_results:
        bad_results = str(prose.get("bad_results") or "")
    outcome_text = " ".join(part.split(":", 1)[-1] for part in re.split(r"[;\n]+", result_rules))
    lethal = _lethal_outcome(outcome_text) or (not result_rules and bool(prose.get("bad_results")))
    return {
        "die": str(data.get("configured_die") or rule.get("die") or data.get("die") or prose.get("die") or "d20"),
        "bad_results": bad_results,
        "result_rules": result_rules,
        "failure_outcome": _mapped_roll_outcome(int(re.findall(r"\d+", bad_results)[0]), result_rules) if bad_results and result_rules else "",
        "failure_is_lethal": lethal,
        "event_rule_id": rule.get("record_id"),
    }


def repair_pending_event_rolls(session: Session, save: ChronicleSave) -> int:
    """Refresh unfinished event obligations from their authoritative event table.

    Event rules are editable, and catalog recovery has become more accurate over
    time. Older obligations used to keep the die and blank result table copied
    when they were first scheduled. Only unfinished rolls are repaired here, so
    historical results remain an immutable account of what the player rolled.
    """
    events = {
        item.id: item for item in session.scalars(select(Record).where(
            Record.save_id == save.id,
            Record.kind == "event",
            Record.deleted.is_(False),
        ))
    }
    if not events:
        return 0
    rule_map = _event_rule_map(session, save)
    changed = 0
    rolls = session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "roll",
        Record.deleted.is_(False),
        Record.data["event_id"].as_string().is_not(None),
    ))
    for roll in rolls:
        data = dict(roll.data or {})
        if bool(data.get("completed")) or not data.get("event_id"):
            continue
        event = events.get(str(data.get("event_id") or ""))
        if not event:
            continue
        event_data = event.data or {}
        spec = event_roll_configuration(event, rule_map.get(event_key(event), {}))
        plan_index = data.get("source_roll_plan_index")
        step = None
        if plan_index not in (None, ""):
            try:
                wanted_index = int(plan_index)
            except (TypeError, ValueError):
                wanted_index = -1
            step = next((
                dict(candidate) for candidate in (event_data.get("source_roll_plan") or [])
                if isinstance(candidate, dict) and int(candidate.get("index") or 0) == wanted_index
            ), None)
        if step:
            result_rules = str(step.get("result_rules") or "")
            bad_results = str(step.get("bad_results") or "")
            spec.update({
                "die": str(step.get("die") or spec["die"]),
                "bad_results": bad_results,
                "result_rules": result_rules,
                "failure_outcome": (
                    _mapped_roll_outcome(int(re.findall(r"\d+", bad_results)[0]), result_rules)
                    if bad_results and result_rules else ""
                ),
                "failure_is_lethal": bool(step.get("failure_is_lethal")) or _lethal_outcome(result_rules),
            })
        context_label = str((step or {}).get("context") or "").split(";")[0].strip()
        source = str(data.get("source") or "")
        show_context = bool(context_label and (":step:" in source or source.startswith("conditional-followup:")))
        roll_type = f"Event — {event.label}" + (f" — {context_label[:80]}" if show_context else "")
        updates = {
            "die": spec["die"],
            "bad_results": spec["bad_results"],
            "result_rules": spec["result_rules"],
            "failure_outcome": spec["failure_outcome"],
            "failure_is_lethal": spec["failure_is_lethal"],
            "nonlethal": not spec["failure_is_lethal"],
            "event_rule_id": spec["event_rule_id"],
            "roll_type": roll_type,
        }
        if all(data.get(key) == value for key, value in updates.items()):
            continue
        base = roll.version
        roll.data = {**data, **updates, "event_table_refreshed": True}
        roll.version += 1
        journal(session, roll, "upsert", base)
        changed += 1
    return changed


def refresh_pending_rolls(session: Session, save: ChronicleSave) -> dict[str, int]:
    """Re-read editable rule tables for unfinished rolls only.

    This is the explicit maintenance action behind the Today-page refresh
    button.  Completed rolls are historical facts and are never rewritten.
    """
    counts = {
        "event": repair_pending_event_rolls(session, save),
        "aging": repair_default_aging_tables(session, save),
        "maternal": restore_delivery_maternal_rolls(session, save),
        "occult": 0,
        "planner": 0,
        "campaign": 0,
    }
    pending = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "roll",
        Record.deleted.is_(False),
    )))
    for roll in pending:
        data = dict(roll.data or {})
        if bool(data.get("completed")) or data.get("event_id"):
            continue
        updates: dict = {}
        category = ""

        source = str(data.get("source") or "")
        if source.startswith(("aging:", "maternal:")):
            rule = session.get(Record, source.rsplit(":", 1)[-1])
            if rule and rule.save_id == save.id and rule.kind == "roll_rule" and not rule.deleted:
                rule_data = rule.data or {}
                updates = {
                    "die": rule_data.get("die") or "d20",
                    "bad_results": str(rule_data.get("bad_results") or ""),
                    "death_age_rng": bool(rule_data.get("death_age_rng")),
                    "core_ruleset_id": rule_data.get("core_ruleset_id"),
                    "core_source_rule_id": rule_data.get("source_rule_id"),
                }
                category = "maternal" if source.startswith("maternal:") else "aging"

        occult_rule_id = str(data.get("occult_rule_id") or "")
        if occult_rule_id:
            rule = session.get(Record, occult_rule_id)
            if rule and rule.save_id == save.id and rule.kind == "occult_rule" and not rule.deleted:
                rule_data = rule.data or {}
                rule_key = str(rule_data.get("rule_key") or data.get("occult_rule_key") or "")
                lethal = occult_rules.lethal_results(rule_key)
                updates = {
                    "die": rule_data.get("die") or "d20",
                    "trigger_results": str(rule_data.get("trigger_results") or ""),
                    "result_rules": str(rule_data.get("result_rules") or ""),
                    "bad_results": lethal,
                    "nonlethal": not bool(lethal),
                    "failure_is_lethal": bool(lethal),
                    "occult_rule_key": rule_key,
                    "occult_type": rule_data.get("occult"),
                    "notes": str(rule_data.get("notes") or ""),
                }
                category = "occult"

        planner_rule_id = str(data.get("planner_rule_id") or "")
        if planner_rule_id:
            rule = session.get(Record, planner_rule_id)
            if rule and rule.save_id == save.id and rule.kind == "planner_rule" and not rule.deleted:
                rule_data = rule.data or {}
                table = str(rule_data.get("bad_results") or rule_data.get("result_rules") or "")
                updates = {"die": rule_data.get("die") or "d20"}
                if data.get("pregnancy_count_roll"):
                    result_rules = table if ":" in table else f"{table}: No pregnancy; all other results: Schedule that many pregnancies"
                    updates.update({"bad_results":"", "zero_results":table, "result_rules":result_rules, "nonlethal":True})
                elif data.get("remarriage_roll"):
                    failure = "; ".join(
                        clause.split(":", 1)[0].strip() for clause in re.split(r"[;\n]+", table)
                        if ":" in clause and "does not remarry" in clause.casefold()
                    )
                    updates.update({"bad_results":failure, "result_rules":table, "nonlethal":True})
                elif _marriage_roll(roll):
                    failure = "; ".join(
                        clause.split(":", 1)[0].strip() for clause in re.split(r"[;\n]+", table)
                        if ":" in clause and "does not marry" in clause.casefold()
                    )
                    updates.update({"bad_results":failure or ("1" if "does not marry" in table.casefold() else table), "result_rules":table, "nonlethal":True})
                category = "planner"

        campaign_id = str(data.get("campaign_id") or "")
        if campaign_id:
            campaign = session.get(Record, campaign_id)
            if campaign and campaign.save_id == save.id and campaign.kind == "campaign" and not campaign.deleted:
                spec = event_roll_configuration(campaign)
                updates = {
                    "die": spec["die"], "bad_results": spec["bad_results"],
                    "result_rules": spec["result_rules"], "failure_outcome": spec["failure_outcome"],
                    "failure_is_lethal": spec["failure_is_lethal"],
                    "nonlethal": not spec["failure_is_lethal"],
                }
                category = "campaign"

        if not updates or all(data.get(key) == value for key, value in updates.items()):
            continue
        base = roll.version
        roll.data = {**data, **updates, "rule_table_refreshed": True}
        roll.version += 1
        journal(session, roll, "upsert", base)
        counts[category] += 1
    counts["updated"] = sum(counts.values())
    return counts


def _event_is_global(event: Record) -> bool:
    data = event.data or {}
    scope = str(data.get("scope") or "").strip().casefold()
    location = str(data.get("location") or "").strip().casefold()
    return scope.startswith("global") or location.startswith("global") or scope in {"world", "worldwide", "all", "everyone", "all sims"}


_EVENT_LOCATION_GROUPS = {
    "britain": {
        "britain", "great britain", "united kingdom", "uk", "british isles",
        "england", "scotland", "wales",
    },
    "low countries": {
        "low countries", "netherlands", "belgium", "luxembourg", "holland",
    },
    "europe": {
        "europe", "britain", "great britain", "united kingdom", "uk", "british isles",
        "england", "scotland", "wales", "ireland", "france", "germany", "italy",
        "spain", "portugal", "netherlands", "belgium", "luxembourg", "holland",
        "austria", "switzerland", "poland", "denmark", "norway", "sweden", "finland",
        "iceland", "greece", "hungary", "bohemia", "czechia", "slovakia", "romania",
        "bulgaria", "serbia", "croatia", "slovenia", "bosnia", "albania", "ukraine",
        "belarus", "lithuania", "latvia", "estonia", "moldova", "russia", "persia",
        "holy roman empire", "ottoman empire",
    },
    "north america": {
        "north america", "canada", "united states", "united states of america", "usa", "us",
        "mexico", "greenland", "bermuda", "saint pierre and miquelon",
    },
    "the americas": {
        "the americas", "americas", "america", "north america", "south america", "central america",
        "canada", "united states", "united states of america", "usa", "us", "mexico",
        "belize", "costa rica", "el salvador", "guatemala", "honduras", "nicaragua", "panama",
        "argentina", "bolivia", "brazil", "chile", "colombia", "ecuador", "guyana", "paraguay",
        "peru", "suriname", "uruguay", "venezuela", "caribbean", "west indies",
    },
}

_EVENT_LOCATION_ALIASES = {
    "pan european": "europe",
    "pan-european": "europe",
    "great britain": "britain",
    "united kingdom": "britain",
    "uk": "britain",
    "british isles": "britain",
    "holland": "netherlands",
    "america": "the americas",
    "americas": "the americas",
    "united states of america": "united states",
    "usa": "united states",
    "us": "united states",
}


def _normalized_location(value: object) -> str:
    text = re.sub(r"[^a-z0-9 -]+", " ", str(value or "").casefold())
    return re.sub(r"\s+", " ", text).strip()


def _location_parts(value: object) -> list[str]:
    parts = []
    for raw in re.split(r"[,/]", str(value or "")):
        normalized = _normalized_location(raw)
        if normalized and normalized not in {"see notes", "affected areas"}:
            parts.append(_EVENT_LOCATION_ALIASES.get(normalized, normalized))
    return parts


def _event_location_matches(target: object, places: object) -> bool:
    """Match historical regions without broadening country-specific events.

    England is part of both Britain and Europe, but an event explicitly limited
    to France must not become applicable merely because both are European.
    Therefore only a *target* region expands to its member countries.
    """
    targets, recorded_places = _location_parts(target), _location_parts(places)
    for target_part in targets:
        members = _EVENT_LOCATION_GROUPS.get(target_part, {target_part})
        for place in recorded_places:
            place_alias = _EVENT_LOCATION_ALIASES.get(place, place)
            if any(
                member == place_alias or member in place_alias or place_alias in member
                for member in members
            ):
                return True
    return False


def _event_occurrence_key(event: Record) -> tuple[str, int, int, str]:
    data = event.data or {}
    start = int(data.get("start_global_day", event.global_day) or 0)
    end = int(data.get("end_global_day", start) or start)
    return (
        re.sub(r"\s+", " ", event.label.casefold()).strip(),
        start,
        end,
        _normalized_location(data.get("location")),
    )


def _event_external_ids(event: Record) -> set[str]:
    data = event.data or {}
    values = {
        str(data.get(key) or "").strip()
        for key in ("catalog_id", "event_id", "legacy_id")
    }
    values.update(str(value).strip() for value in (data.get("duplicate_event_aliases") or []))
    return {value for value in values if value}


def duplicate_event_groups(records: list[Record]) -> list[dict]:
    """Find exact duplicate event occurrences and select the safest keeper."""
    active = [item for item in records if not item.deleted]
    references: dict[str, int] = defaultdict(int)
    event_ids = {item.id for item in active if item.kind == "event"}
    for item in active:
        data = item.data or {}
        for key in ("event_id", "source_id", "source_event_id"):
            value = str(data.get(key) or "")
            if value in event_ids:
                references[value] += 1
        source = str(data.get("source") or "")
        if source.startswith("event:"):
            source_id = source.split(":", 2)[1]
            if source_id in event_ids:
                references[source_id] += 1

    occurrences: dict[tuple[str, int, int, str], list[Record]] = defaultdict(list)
    for event in (item for item in active if item.kind == "event"):
        occurrences[_event_occurrence_key(event)].append(event)

    def keeper_score(event: Record) -> tuple[int, int, int, int, int, float, str]:
        data = event.data or {}
        protected = sum(bool(data.get(key)) for key in (
            "ignored", "completed", "configured_die", "configured_bad_results", "result_rules",
        ))
        meaningful = sum(value not in (None, "", [], {}) for value in data.values())
        legacy = int(bool(data.get("legacy_table") or data.get("legacy_id")))
        created = event.created_at.timestamp() if event.created_at else 0.0
        return references[event.id], protected, meaningful, legacy, int(event.version or 0), -created, event.id

    groups = []
    for identity, matches in occurrences.items():
        if len(matches) < 2:
            continue
        keeper = max(matches, key=keeper_score)
        groups.append({
            "identity": identity,
            "label": keeper.label,
            "keeper": keeper,
            "redundant": [item for item in matches if item is not keeper],
            "matches": matches,
            "references": sum(references[item.id] for item in matches),
        })
    return sorted(groups, key=lambda group: (group["identity"][1], group["label"].casefold()))


def duplicate_event_summary(records: list[Record]) -> dict:
    groups = duplicate_event_groups(records)
    return {
        "groups": len(groups),
        "repairable": sum(len(group["redundant"]) for group in groups),
        "preview": [{
            "label": group["label"],
            "global_day": group["identity"][1],
            "copies": len(group["matches"]),
            "references": group["references"],
        } for group in groups[:12]],
    }


def repair_duplicate_events(session: Session, save: ChronicleSave) -> dict:
    """Merge exact duplicate events, repoint dependents, and archive extra copies."""
    records = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.deleted.is_(False),
    )))
    groups = duplicate_event_groups(records)
    if not groups:
        return {"groups": 0, "archived": 0, "repointed": 0, "rolls_archived": 0}

    record_id_map: dict[str, str] = {}
    external_id_map: dict[str, str] = {}
    keeper_aliases: dict[str, set[str]] = defaultdict(set)
    for group in groups:
        keeper = group["keeper"]
        canonical_external = event_key(keeper)
        for redundant in group["redundant"]:
            record_id_map[redundant.id] = keeper.id
            for alias in _event_external_ids(redundant):
                if alias != canonical_external:
                    external_id_map[alias] = canonical_external
                    keeper_aliases[keeper.id].add(alias)

    repointed = 0
    grouped_event_ids = {item.id for group in groups for item in group["matches"]}
    for item in records:
        if item.id in grouped_event_ids:
            continue
        data = dict(item.data or {})
        changed = False
        for key in ("event_id", "source_id", "source_event_id"):
            value = str(data.get(key) or "")
            if value in record_id_map:
                data[key] = record_id_map[value]
                changed = True
        if item.kind == "event_rule":
            for key in ("event_id", "catalog_id", "legacy_id"):
                value = str(data.get(key) or "")
                if value in external_id_map:
                    data[key] = external_id_map[value]
                    changed = True
        if isinstance(data.get("event_ids"), list):
            mapped = [record_id_map.get(str(value), str(value)) for value in data["event_ids"]]
            if mapped != data["event_ids"]:
                data["event_ids"] = list(dict.fromkeys(mapped))
                changed = True
        source = str(data.get("source") or "")
        if source.startswith("event:"):
            parts = source.split(":", 2)
            if len(parts) == 3 and parts[1] in record_id_map:
                data["source"] = f"event:{record_id_map[parts[1]]}:{parts[2]}"
                changed = True
        if not changed:
            continue
        base = item.version
        item.data = data
        item.version += 1
        journal(session, item, "upsert", base)
        repointed += 1

    archived = 0
    keepers_updated = 0
    for group in groups:
        keeper = group["keeper"]
        merged = dict(keeper.data or {})
        aliases = set(merged.get("duplicate_event_aliases") or []) | keeper_aliases.get(keeper.id, set())
        for redundant in group["redundant"]:
            other = redundant.data or {}
            for key, value in other.items():
                if key in {"catalog_id", "event_id", "legacy_id", "duplicate_event_aliases"}:
                    continue
                if key in {"roll_required", "completed", "ignored"}:
                    merged[key] = bool(merged.get(key)) or bool(value)
                elif key == "notes" and len(str(value or "")) > len(str(merged.get(key) or "")):
                    merged[key] = value
                elif merged.get(key) in (None, "", [], {}) and value not in (None, "", [], {}):
                    merged[key] = value
            base = redundant.version
            redundant.deleted = True
            redundant.data = {
                **other,
                "duplicate_event_repair": True,
                "duplicate_of": keeper.id,
                "retired_reason": "Duplicate event",
                "retired_global_day": save.global_day,
            }
            redundant.version += 1
            journal(session, redundant, "delete", base)
            archived += 1
        if aliases:
            merged["duplicate_event_aliases"] = sorted(aliases)
        if merged != (keeper.data or {}):
            base = keeper.version
            keeper.data = merged
            keeper.version += 1
            journal(session, keeper, "upsert", base)
            keepers_updated += 1

    save.revision += archived + repointed + keepers_updated
    session.flush()
    roll_result = repair_duplicate_obligations(session, save)
    return {
        "groups": len(groups),
        "archived": archived,
        "repointed": repointed,
        "rolls_archived": roll_result["archived"],
    }


def _event_applies(event: Record, sim: Record, due: int, rule_data: dict | None = None,
                   household: Record | None = None, save: ChronicleSave | None = None,
                   fallback_location: str = "", pregnancies: list[Record] | None = None) -> bool:
    data, original = event.data or {}, sim.data or {}
    household_data = household.data if household else {}
    # Household and challenge defaults fill gaps but never replace explicit Sim data.
    sim_data = {**(household_data or {}), **original}
    rule = rule_data or {}
    birth = sim_data.get("birth_global_day", sim.global_day)
    death = sim_data.get("death_global_day")
    if birth is not None and int(birth) > due:
        return False
    if death is not None and int(death) <= due:
        return False
    text = f"{rule.get('eligibility','')} {data.get('affected_class','')} {data.get('notes','')}".casefold()
    sex = str(sim_data.get("sex") or "").casefold()
    explicit_sexes = rule.get("eligible_sexes")
    sex_rule = " ".join(map(str, explicit_sexes)) if isinstance(explicit_sexes, (list, tuple, set)) else str(explicit_sexes or "")
    sex_rule = sex_rule.casefold()
    sex_text = f"{sex_rule} {text}"
    mentions_male = bool(re.search(r"\b(?:male|males|men|man|boys?)\b", sex_text))
    mentions_female = bool(re.search(r"\b(?:female|females|women|woman|girls?)\b", sex_text))
    male_only = mentions_male and not mentions_female
    female_only = mentions_female and not mentions_male
    sim_is_male = bool(re.search(r"\b(?:male|man|boy)\b", sex)) and not bool(re.search(r"\bfemale\b", sex))
    sim_is_female = bool(re.search(r"\b(?:female|woman|girl)\b", sex))
    if male_only and not sim_is_male:
        return False
    if female_only and not sim_is_female:
        return False
    if birth is not None:
        age = due - int(birth)
        try:
            if rule.get("min_age_days") not in (None, "") and age < int(rule["min_age_days"]):
                return False
            if rule.get("max_age_days") not in (None, "") and age > int(rule["max_age_days"]):
                return False
        except (TypeError, ValueError):
            pass
        age_match = re.search(r"\b(\d+)\s*\+", text)
        if age_match and age < int(age_match.group(1)):
            return False
        eligible_stages = {
            str(value or "").strip().casefold()
            for value in (data.get("eligible_life_stages") or rule.get("eligible_life_stages") or [])
            if str(value or "").strip()
        }
        if eligible_stages:
            inferred_stage = DEFAULT_STAGES[0][0].casefold()
            for stage_name, minimum, _die, _bad in DEFAULT_STAGES:
                if age >= minimum:
                    inferred_stage = stage_name.casefold()
            stage_matches = inferred_stage in eligible_stages or (
                inferred_stage in {"being born", "newborn", "infant"} and "baby" in eligible_stages
            )
            pregnant_at_event = False
            if data.get("include_pregnant"):
                for pregnancy in pregnancies or []:
                    pregnancy_data = pregnancy.data or {}
                    if str(pregnancy_data.get("mother_id") or "") != sim.id:
                        continue
                    try:
                        conception = int(pregnancy_data.get("conception_global_day", pregnancy.global_day))
                        finished = int(
                            pregnancy_data.get("actual_delivery_global_day")
                            or pregnancy_data.get("due_global_day") or due
                        )
                    except (TypeError, ValueError):
                        continue
                    if conception <= due <= finished:
                        pregnant_at_event = True
                        break
            if not stage_matches and not pregnant_at_event:
                return False
    if _event_is_global(event):
        return True
    target = str(data.get("location") or "").casefold()
    challenge = save.settings if save else {}
    places = " ".join(str(value or "") for value in (
        sim_data.get("country"), sim_data.get("last_game_world"), sim_data.get("birthplace"),
        sim_data.get("location"), sim_data.get("world"), household_data.get("country"),
        household_data.get("location"), household_data.get("world"),
        (challenge or {}).get("challenge_location"), (challenge or {}).get("location"),
        (challenge or {}).get("country"),
    )).casefold()
    if not places.strip():
        places = fallback_location.casefold()
    target_parts = _location_parts(target)
    if target_parts and not _event_location_matches(target, places):
        return False
    social = str(sim_data.get("social_class") or household_data.get("social_class") or "").casefold()
    affected = str(data.get("affected_class") or "").casefold()
    class_words = ("nobility", "noble", "royal", "peasant", "working", "middle", "upper", "lower")
    requested = [word for word in class_words if word in affected]
    return not requested or any(word in social for word in requested)


def _source_roll_step_applies(step: dict, sim: Record, household: Record | None,
                              save: ChronicleSave, fallback_location: str) -> bool:
    """Apply a source table's narrower location without widening its event.

    Some historical entries contain separate dice tables for different places.
    The event itself must reach all of those Sims, while a table should only be
    scheduled for its own region.
    """
    target = str(step.get("location") or step.get("eligible_location") or "").strip()
    if not target or _normalized_location(target) in {"all", "global", "world", "worldwide"}:
        return True
    sim_data = sim.data or {}
    household_data = household.data if household else {}
    challenge = save.settings or {}
    places = " ".join(str(value or "") for value in (
        sim_data.get("country"), sim_data.get("last_game_world"), sim_data.get("birthplace"),
        sim_data.get("location"), sim_data.get("world"), household_data.get("country"),
        household_data.get("location"), household_data.get("world"),
        challenge.get("challenge_location"), challenge.get("location"), challenge.get("country"),
    ))
    return _event_location_matches(target, places or fallback_location)


def _remarriage_rule(record: Record) -> bool:
    data = record.data or {}
    text = " ".join(str(value or "") for value in (data.get("rule_key"), record.label, data.get("notes"))).casefold()
    return "remarriage" in text or "remarry" in text


def seed_remarriage_rule(session: Session, save: ChronicleSave) -> int:
    """Install the cross-ruleset remarriage baseline into older saves once."""
    existing = next((record for record in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "planner_rule", Record.deleted.is_(False)
    )) if _remarriage_rule(record)), None)
    if existing:
        return 0
    record = Record(save_id=save.id, kind="planner_rule", label="Remarriage Eligibility", data={
        "rule_key":"remarriage", "start_year":-9999, "end_year":9999, "die":"d6",
        "bad_results":"1: May remarry; 2-6: Does not remarry", "active":True,
        "notes":"One remarriage roll after a marriage ends; add era ranges to change the odds through history",
    })
    session.add(record); session.flush(); journal(session, record, "upsert", 0); save.revision += 1
    return 1


def _marriage_rule(record: Record) -> bool:
    data = record.data or {}
    text = " ".join(str(value or "") for value in (data.get("rule_key"), record.label, data.get("notes"))).casefold()
    return not _remarriage_rule(record) and ("non_heir_marriage" in text or "non-heir marriage" in text or "marriage eligibility" in text)


def _marriage_roll(record: Record) -> bool:
    data = record.data or {}
    text = " ".join(str(value or "") for value in (data.get("source"), data.get("source_id"), data.get("roll_type"))).casefold()
    return "planner:marriage:" in text or "planner-marriage-" in text or "marriage eligibility" in text or "marriage roll" in text


def marriage_roll_result(actual: int, result_rules: str, bad_results: str = "") -> str:
    """Interpret full marriage tables while keeping every marriage outcome nonfatal."""
    text = str(result_rules or bad_results or "").replace("–", "-").replace("—", "-")
    remarriage = "remarri" in text.casefold() or "remarry" in text.casefold()
    positive = "May remarry" if remarriage else "May marry"
    negative = "Does not remarry" if remarriage else "Does not marry"
    default_action = ""
    for clause in (part.strip() for part in re.split(r"[;\n]+", text) if part.strip()):
        if ":" in clause and any(marker in clause.casefold() for marker in ("all other", "otherwise", "remaining")):
            default_action = clause.split(":", 1)[1].strip()
        match = re.search(r"(?<!\d)(\d+)(?:\s*-\s*(\d+))?(?!\d)\s*:", clause)
        if not match:
            continue
        low = int(match.group(1)); high = int(match.group(2) or low)
        if min(low, high) <= actual <= max(low, high):
            action = clause.split(":", 1)[1].strip()
            if "does not remarry" in action.casefold() or "does not marry" in action.casefold():
                return negative
            if "may remarry" in action.casefold() or "remarries" in action.casefold() or "may marry" in action.casefold() or "marries" in action.casefold():
                return positive
            return action or positive
    if default_action:
        if "does not remarry" in default_action.casefold() or "does not marry" in default_action.casefold():
            return negative
        if "may remarry" in default_action.casefold() or "remarries" in default_action.casefold() or "may marry" in default_action.casefold() or "marries" in default_action.casefold():
            return positive
        return default_action
    failure_values = str(bad_results or "")
    if "does not marry" in text.casefold() or "does not remarry" in text.casefold():
        failure_clause = next((part for part in re.split(r"[;\n]+", text) if "does not marry" in part.casefold() or "does not remarry" in part.casefold()), "")
        failure_values = failure_clause.split(":", 1)[0]
    return negative if failed(actual, failure_values) else positive


def _pregnancy_count_rule(record: Record) -> bool:
    data = record.data or {}
    text = " ".join(str(value or "") for value in (data.get("rule_key"), record.label, data.get("notes"))).casefold()
    return "side_pregnancy" in text or "side household pregnancy" in text or "pregnancy-count" in text or "pregnancy count" in text


def pregnancy_count_result(actual: int, result_rules: str, zero_results: str = "") -> tuple[int, str]:
    """Translate a pregnancy-count die result without treating it as a fatal failure."""
    text = str(result_rules or "").replace("–", "-").replace("—", "-")
    default_action = ""
    for clause in (part.strip() for part in re.split(r"[;\n]+", text) if part.strip()):
        action = clause.split(":", 1)[1].strip() if ":" in clause else ""
        if "all other" in clause.casefold():
            default_action = action
            continue
        match = re.search(r"(?<!\d)(\d+)(?:\s*-\s*(\d+))?(?!\d)\s*:", clause)
        if not match:
            continue
        low = int(match.group(1)); high = int(match.group(2) or low)
        if min(low, high) <= actual <= max(low, high):
            break
    else:
        action = default_action
    lowered = action.casefold()
    if "no pregn" in lowered or (not action and zero_results and failed(actual, zero_results)):
        count = 0
    elif "one pregn" in lowered:
        count = 1
    else:
        explicit = re.search(r"\b(\d+)\s+pregnan", lowered)
        count = int(explicit.group(1)) if explicit else max(0, int(actual))
    outcome = "No pregnancies" if count == 0 else "1 pregnancy" if count == 1 else f"{count} pregnancies"
    return count, outcome


def create_pregnancy_count_roll(session: Session, save: ChronicleSave, sim: Record) -> tuple[Record, bool]:
    """Create one editable-rule pregnancy allowance for a Sim in the current historical year."""
    if sim.kind != "sim" or sim.deleted or sim.save_id != save.id:
        raise ValueError("Choose a Sim from the active save.")
    death_day = (sim.data or {}).get("death_global_day")
    if bool((sim.data or {}).get("game_was_dead")) or (death_day not in (None, "") and int(death_day) <= save.global_day):
        raise ValueError("Pregnancy-count rolls are only available for living Sims.")
    year = save.start_year + (save.global_day - 1) // max(1, save.days_per_year)
    rules = [record for record in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "planner_rule", Record.deleted.is_(False)
    )) if _pregnancy_count_rule(record) and bool((record.data or {}).get("active", True)) and core_rulesets.applies_to_selected_core(save, record)]
    rule = next((record for record in sorted(rules, key=lambda item:int((item.data or {}).get("start_year", -9999)), reverse=True)
                 if int((record.data or {}).get("start_year", -9999)) <= year <= int((record.data or {}).get("end_year", 9999))), None)
    if not rule:
        raise ValueError(f"No active pregnancy-count rule covers {year}. Add or enable one under Roll Tables.")
    source = f"planner:pregnancy-count:{sim.id}:{year}"
    existing = session.scalar(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["source"].as_string() == source,
    ).order_by(Record.created_at.desc()).limit(1))
    if existing:
        return existing, False
    rule_data = rule.data or {}
    stored_rules = str(rule_data.get("bad_results") or "")
    if ":" not in stored_rules:
        stored_rules = f"{stored_rules}: No pregnancy; all other results: Schedule that many pregnancies"
    roll = Record(save_id=save.id, kind="roll", label=f"{sim.label} — Pregnancy Count", global_day=save.global_day, data={
        "sim_id":sim.id, "sim_name":sim.label, "source_id":source, "source":source,
        "roll_type":"Pregnancy Count", "die":rule_data.get("die") or "d20", "bad_results":"",
        "result_rules":stored_rules, "zero_results":str(rule_data.get("bad_results") or ""),
        "planner_rule_id":rule.id, "planner_year":year, "due_global_day":save.global_day,
        "core_ruleset_id":rule_data.get("core_ruleset_id"),
        "core_source_rule_id":rule_data.get("source_rule_id"),
        "completed":False, "nonlethal":True, "pregnancy_count_roll":True,
        "notes":f"Pregnancy allowance for {year}; uses the editable era planner rule",
    })
    session.add(roll); session.flush(); journal(session, roll, "upsert", 0); save.revision += 1
    return roll, True


def pregnancy_allowance_status(session: Session, save: ChronicleSave, sim: Record) -> dict:
    """Return each recorded annual allowance with live used and remaining counts."""
    stored = (sim.data or {}).get("pregnancy_allowances") or {}
    allowances = {str(key):dict(value) for key,value in stored.items() if isinstance(value, dict)} if isinstance(stored, dict) else {}
    if (sim.data or {}).get("pregnancy_allowance_count") is not None:
        year = str((sim.data or {}).get("pregnancy_allowance_year") or save.start_year)
        allowances.setdefault(year, {
            "allowed":int((sim.data or {}).get("pregnancy_allowance_count") or 0),
            "roll_id":(sim.data or {}).get("pregnancy_allowance_roll_id"),
            "recorded_global_day":(sim.data or {}).get("pregnancy_allowance_recorded_global_day"),
        })
    completed_rolls = session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["sim_id"].as_string() == sim.id,
    ).order_by(Record.updated_at.desc()))
    for roll in completed_rolls:
        data = roll.data or {}
        if not data.get("pregnancy_count_roll") or not data.get("completed") or data.get("pregnancy_count") is None:
            continue
        year = str(data.get("planner_year") or (save.start_year + (int(roll.global_day or save.global_day) - 1) // max(1, save.days_per_year)))
        allowances.setdefault(year, {"allowed":int(data.get("pregnancy_count") or 0), "roll_id":roll.id, "recorded_global_day":data.get("completed_global_day")})
    used_by_year: dict[str, int] = {}
    pregnancies = session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "pregnancy", Record.deleted.is_(False),
        Record.data["mother_id"].as_string() == sim.id,
    ))
    for pregnancy in pregnancies:
        data = pregnancy.data or {}
        if str(data.get("status") or "").strip().casefold() in {"cancelled", "canceled"}:
            continue
        day = data.get("conception_global_day")
        if day in (None, ""):
            due = data.get("due_global_day", pregnancy.global_day)
            day = int(due) - save.pregnancy_days if due not in (None, "") else pregnancy.global_day
        if day is None:
            continue
        year = str(save.start_year + (int(day) - 1) // max(1, save.days_per_year))
        used_by_year[year] = used_by_year.get(year, 0) + 1
    rows = []
    for year,value in allowances.items():
        allowed = max(0, int(value.get("allowed") or 0)); used = used_by_year.get(str(year), 0)
        rows.append({"year":int(year), "allowed":allowed, "used":used, "remaining":max(0, allowed - used), **value})
    rows.sort(key=lambda row:row["year"], reverse=True)
    current_year = save.start_year + (save.global_day - 1) // max(1, save.days_per_year)
    return {"rows":rows, "current":next((row for row in rows if row["year"] == current_year), rows[0] if rows else None)}


def sync_family_plan_from_pregnancy_roll(session: Session, save: ChronicleSave, roll: Record,
                                         sim: Record, pregnancy_count: int) -> tuple[Record, bool, bool]:
    """Create or update the annual family plan represented by a count roll."""
    data = roll.data or {}
    year = int(data.get("planner_year") or (save.start_year + (int(roll.global_day or save.global_day) - 1) // max(1, save.days_per_year)))
    source = f"pregnancy-count:{roll.id}"
    plan = session.scalar(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "family_plan",
        Record.deleted.is_(False),
        Record.data["source_pregnancy_roll_id"].as_string() == roll.id,
    ).limit(1))
    payload = {
        "sim_id": sim.id,
        "sim_name": sim.label,
        "target_pregnancies": max(0, int(pregnancy_count)),
        # The existing planner forecasts children, so one child per allotted
        # pregnancy is the transparent baseline until actual births replace it.
        "target_children": max(0, int(pregnancy_count)),
        "min_birth_spacing_days": max(0, int(save.pregnancy_days)),
        "planner_year": year,
        "source": source,
        "source_pregnancy_roll_id": roll.id,
        "automatic": True,
        "active": True,
        "notes": f"Created automatically from the {year} pregnancy-count roll; adjust the targets if a multiple birth changes the family goal.",
    }
    label = f"{sim.label} family plan · {year}"
    if plan:
        current = plan.data or {}
        if plan.label == label and all(current.get(key) == value for key, value in payload.items()):
            return plan, False, False
        base = plan.version; plan.label = label; plan.global_day = int(roll.global_day or save.global_day)
        plan.data = {**current, **payload}; plan.version += 1; journal(session, plan, "upsert", base)
        return plan, True, False
    plan = Record(save_id=save.id, kind="family_plan", label=label,
                  global_day=int(roll.global_day or save.global_day), data=payload)
    session.add(plan); session.flush(); journal(session, plan, "upsert", 0)
    return plan, True, True


def backfill_pregnancy_allowances(session: Session, save: ChronicleSave) -> int:
    """Copy completed pre-feature pregnancy-count rolls onto their Sim profiles once."""
    rolls = session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["pregnancy_count_roll"].as_boolean().is_(True),
        Record.data["completed"].as_boolean().is_(True),
    ).order_by(Record.updated_at))
    changed = 0
    for roll in rolls:
        data = roll.data or {}
        if not data.get("pregnancy_count_roll") or not data.get("completed") or data.get("pregnancy_count") is None:
            continue
        sim = session.get(Record, data.get("sim_id")) if data.get("sim_id") else None
        if not sim or sim.kind != "sim" or sim.deleted:
            continue
        year = int(data.get("planner_year") or (save.start_year + (int(roll.global_day or save.global_day) - 1) // max(1, save.days_per_year)))
        sim_data = dict(sim.data or {}); allowances = dict(sim_data.get("pregnancy_allowances") or {})
        entry = {"allowed":int(data.get("pregnancy_count") or 0), "roll_id":roll.id, "recorded_global_day":data.get("completed_global_day"), "actual":data.get("actual")}
        if allowances.get(str(year)) != entry:
            allowances[str(year)] = entry
            sim_data["pregnancy_allowances"] = allowances
            if int(sim_data.get("pregnancy_allowance_year") or -9999) <= year:
                sim_data.update({
                    "pregnancy_allowance_count":entry["allowed"], "pregnancy_allowance_year":year,
                    "pregnancy_allowance_roll_id":roll.id, "pregnancy_allowance_recorded_global_day":entry["recorded_global_day"],
                })
            base = sim.version; sim.data = sim_data; sim.version += 1; journal(session, sim, "upsert", base); changed += 1
        _, plan_changed, _ = sync_family_plan_from_pregnancy_roll(session, save, roll, sim, entry["allowed"])
        changed += int(plan_changed)
    save.revision += changed
    return changed


def backfill_generated_marriage_dates(session: Session, save: ChronicleSave) -> int:
    """Give older successful marriage rolls one stable suggested date."""
    changed = 0
    rolls = session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["completed"].as_boolean().is_(True),
    ))
    for roll in rolls:
        data = dict(roll.data or {})
        outcome = str(data.get("outcome") or "").casefold()
        if not _marriage_roll(roll) or not any(value in outcome for value in ("may marry", "may remarry")):
            continue
        if data.get("suggested_marriage_global_day") not in (None, ""):
            continue
        first_day = max(save.global_day, int(roll.global_day or save.global_day)) + 1
        last_day = first_day + max(1, int(save.days_per_year)) - 1
        suggested = random.SystemRandom().randint(first_day, last_day)
        base = roll.version
        roll.data = {
            **data,
            "suggested_marriage_global_day": suggested,
            "suggested_marriage_date_range": calendar_utils.date_range_label(suggested, save.start_year, save.days_per_year),
            "suggested_marriage_date_source": "Generated after successful marriage eligibility roll",
        }
        roll.version += 1; journal(session, roll, "upsert", base); changed += 1
    return changed


def _setting_int(save: ChronicleSave, key: str, default: int) -> int:
    settings = save.settings or {}
    value = settings.get(key)
    if value in (None, ""):
        value = (settings.get("legacy_settings") or {}).get(key, default)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _schedule_marriage_rolls(session: Session, save: ChronicleSave, sims: list[Record] | None = None) -> tuple[int, int]:
    """Restore the one-time non-heir marriage obligation used by the 3.x planner."""
    rules = [record for record in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "planner_rule", Record.deleted.is_(False)
    )) if _marriage_rule(record) and bool((record.data or {}).get("active", True)) and core_rulesets.applies_to_selected_core(save, record)]
    if not rules:
        return 0, 0
    sims = sims if sims is not None else list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False)
    )))
    relationships = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "relationship", Record.deleted.is_(False)
    )))
    married_ids = set()
    for relationship in relationships:
        data = relationship.data or {}
        if bool(data.get("legally_married")) or "marriage" in str(data.get("type") or "").casefold():
            married_ids.update(str(data.get(key) or "") for key in ("partner1_id", "partner2_id"))
    existing_rolls = [record for record in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False)
    )) if _marriage_roll(record)]
    existing_sim_ids = {str((record.data or {}).get("sim_id") or "") for record in existing_rolls}
    tracking_start = max(1, _setting_int(save, "roll_tracking_start_day", _setting_int(save, "roll_tracking_start", 1)))
    retired = 0
    for roll in existing_rolls:
        if bool((roll.data or {}).get("completed")):
            continue
        sim_is_married = str((roll.data or {}).get("sim_id") or "") in married_ids
        before_tracking = roll.global_day is not None and int(roll.global_day) < tracking_start
        if not sim_is_married and not before_tracking:
            continue
        base = roll.version
        roll.deleted = True
        reason = "Sim is already married" if sim_is_married else "Eligibility predates roll tracking"
        roll.data = {**(roll.data or {}), "retired_reason":reason, "retired_global_day":save.global_day}
        roll.version += 1; journal(session, roll, "delete", base); retired += 1
    settings = save.settings or {}
    legacy = settings.get("legacy_settings") or {}
    legacy_map = settings.get("legacy_id_map") or {}
    heir_id = str(settings.get("current_heir_id") or legacy_map.get(legacy.get("current_heir_id"), legacy.get("current_heir_id")) or "")
    marriage_age = max(0, _setting_int(save, "marriage_min_age_days", 72))
    created = 0
    for sim in sims:
        data = sim.data or {}
        birth = data.get("birth_global_day", sim.global_day)
        if bool(data.get("game_was_dead")) or birth is None or sim.id == heir_id or sim.id in married_ids or sim.id in existing_sim_ids:
            continue
        due = int(birth) + marriage_age
        death = data.get("death_global_day")
        if due < tracking_start or due > save.global_day or (death is not None and (int(death) <= save.global_day or int(death) <= due)):
            continue
        due_year = save.start_year + (due - 1) // max(1, save.days_per_year)
        rule = next((record for record in sorted(rules, key=lambda item:int((item.data or {}).get("start_year", -9999)), reverse=True)
                     if int((record.data or {}).get("start_year", -9999)) <= due_year <= int((record.data or {}).get("end_year", 9999))), None)
        if not rule:
            continue
        rule_results = str((rule.data or {}).get("bad_results") or "")
        failure_results = "1" if "does not marry" in rule_results.casefold() else rule_results
        source = f"planner:marriage:{sim.id}"
        roll = Record(save_id=save.id, kind="roll", label=f"{sim.label} — Non-Heir Marriage Eligibility", global_day=due, data={
            "sim_id":sim.id, "sim_name":sim.label, "source_id":source, "roll_type":"Non-Heir Marriage Eligibility",
            "die":(rule.data or {}).get("die") or "d20", "bad_results":failure_results, "result_rules":rule_results,
            "source":source, "planner_rule_id":rule.id, "due_global_day":due, "completed":False,
            "nonlethal":True, "failure_outcome":"Does not marry", "success_outcome":"May marry",
            "notes":"Auto-generated when this non-heir reached marriage eligibility",
            "core_ruleset_id":(rule.data or {}).get("core_ruleset_id"),
            "core_source_rule_id":(rule.data or {}).get("source_rule_id"),
        })
        session.add(roll);session.flush();journal(session,roll,"upsert",0);existing_sim_ids.add(sim.id);created += 1
    return created, retired


def _schedule_remarriage_rolls(session: Session, save: ChronicleSave, sims: list[Record] | None = None) -> tuple[int, int]:
    """Schedule one era-aware, nonfatal remarriage decision after each ended marriage."""
    seed_remarriage_rule(session, save)
    rules = [record for record in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "planner_rule", Record.deleted.is_(False)
    )) if _remarriage_rule(record) and bool((record.data or {}).get("active", True)) and core_rulesets.applies_to_selected_core(save, record)]
    if not rules:
        return 0, 0
    sims = sims if sims is not None else list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False)
    )))
    sim_by_id = {sim.id: sim for sim in sims}
    relationships = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "relationship", Record.deleted.is_(False)
    )))
    ended_statuses = {"ended", "divorced", "annulled", "widowed", "separated", "inactive", "closed"}
    active_married_ids: set[str] = set()
    ended_marriages: list[tuple[Record, int]] = []
    for relationship in relationships:
        data = relationship.data or {}
        relation_type = str(data.get("type") or "").casefold()
        married = bool(data.get("legally_married")) or "marriage" in relation_type or "spouse" in relation_type
        if not married:
            continue
        status = str(data.get("status") or "Active").strip().casefold()
        raw_end = data.get("end_global_day")
        ended = status in ended_statuses or raw_end not in (None, "")
        if not ended:
            active_married_ids.update(str(data.get(key) or "") for key in ("partner1_id", "partner2_id"))
            continue
        try:
            ended_day = int(raw_end)
        except (TypeError, ValueError):
            ended_day = save.global_day
        ended_marriages.append((relationship, max(1, ended_day)))

    all_remarriage_rolls = [record for record in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll"
    )) if "planner:remarriage:" in str((record.data or {}).get("source") or "").casefold()]
    existing_sources = {str((record.data or {}).get("source") or "") for record in all_remarriage_rolls}
    tracking_start = max(1, _setting_int(save, "roll_tracking_start_day", _setting_int(save, "roll_tracking_start", 1)))
    retired = 0
    for roll in all_remarriage_rolls:
        if roll.deleted or bool((roll.data or {}).get("completed")):
            continue
        sim = sim_by_id.get(str((roll.data or {}).get("sim_id") or ""))
        death_day = (sim.data or {}).get("death_global_day") if sim else None
        unavailable = not sim or sim.id in active_married_ids or bool((sim.data or {}).get("game_was_dead")) or (death_day not in (None, "") and int(death_day) <= save.global_day)
        before_tracking = roll.global_day is not None and int(roll.global_day) < tracking_start
        if not unavailable and not before_tracking:
            continue
        base = roll.version
        roll.deleted = True
        roll.data = {**(roll.data or {}), "retired_reason":"Sim is already remarried or no longer eligible" if unavailable else "Eligibility predates roll tracking", "retired_global_day":save.global_day}
        roll.version += 1; journal(session, roll, "delete", base); retired += 1

    created = 0
    for relationship, due in ended_marriages:
        if due < tracking_start:
            continue
        relationship_data = relationship.data or {}
        partner_ids = [str(relationship_data.get(key) or "") for key in ("partner1_id", "partner2_id")]
        for index, sim_id in enumerate(partner_ids):
            sim = sim_by_id.get(sim_id)
            if not sim or sim.id in active_married_ids:
                continue
            sim_data = sim.data or {}
            death_day = sim_data.get("death_global_day")
            if bool(sim_data.get("game_was_dead")) or (death_day not in (None, "") and int(death_day) <= save.global_day):
                continue
            source = f"planner:remarriage:{relationship.id}:{sim.id}"
            if source in existing_sources:
                continue
            year = save.start_year + (due - 1) // max(1, save.days_per_year)
            rule = next((record for record in sorted(rules, key=lambda item:int((item.data or {}).get("start_year", -9999)), reverse=True)
                         if int((record.data or {}).get("start_year", -9999)) <= year <= int((record.data or {}).get("end_year", 9999))), None)
            if not rule:
                continue
            rule_data = rule.data or {}
            die = str(rule_data.get("die") or "d6")
            result_rules = str(rule_data.get("bad_results") or rule_data.get("result_rules") or "1: May remarry; 2-6: Does not remarry").strip()
            if ":" not in result_rules:
                sides_match = re.search(r"d(\d+)", die.casefold())
                sides = max(2, int(sides_match.group(1))) if sides_match else 6
                result_rules = f"{result_rules or '1'}: May remarry; 2-{sides}: Does not remarry"
            failure_values = "; ".join(
                clause.split(":", 1)[0].strip() for clause in re.split(r"[;\n]+", result_rules)
                if ":" in clause and "does not remarry" in clause.casefold()
            )
            former_partner_id = partner_ids[1-index] if len(partner_ids) > 1 else ""
            former_partner = sim_by_id.get(former_partner_id)
            roll = Record(save_id=save.id, kind="roll", label=f"{sim.label} — Remarriage Eligibility", global_day=due, data={
                "sim_id":sim.id, "sim_name":sim.label, "former_partner_id":former_partner_id,
                "former_partner_name":former_partner.label if former_partner else relationship_data.get("partner2_name" if index == 0 else "partner1_name"),
                "relationship_id":relationship.id, "source_id":relationship.id, "source":source,
                "roll_type":"Remarriage Eligibility", "die":die, "bad_results":failure_values,
                "result_rules":result_rules, "planner_rule_id":rule.id, "planner_year":year,
                "due_global_day":due, "completed":False, "nonlethal":True, "remarriage_roll":True,
                "failure_outcome":"Does not remarry", "success_outcome":"May remarry",
                "notes":"Auto-generated when the prior marriage ended; completing it records eligibility only and never creates a spouse automatically",
                "core_ruleset_id":rule_data.get("core_ruleset_id"), "core_source_rule_id":rule_data.get("source_rule_id"),
            })
            session.add(roll); session.flush(); journal(session, roll, "upsert", 0)
            existing_sources.add(source); created += 1
    return created, retired


def schedule_marriage_rolls(session: Session, save: ChronicleSave) -> int:
    created, retired = _schedule_marriage_rolls(session, save)
    remarriage_created, remarriage_retired = _schedule_remarriage_rolls(session, save)
    save.revision += created + retired + remarriage_created + remarriage_retired
    return created + remarriage_created


def _occult_year(save: ChronicleSave, day: int) -> int:
    return save.start_year + (max(1, int(day)) - 1) // max(1, save.days_per_year)


def _occult_rule_matches(rule: Record, year: int) -> bool:
    data = rule.data or {}
    return (
        bool(data.get("active", True)) and bool(data.get("auto_schedule", False))
        and int(data.get("start_year", -9999)) <= year <= int(data.get("end_year", 9999))
    )


def _add_occult_roll(session: Session, save: ChronicleSave, rule: Record, sim: Record,
                     due: int, source: str, *, label: str | None = None,
                     overrides: dict | None = None) -> bool:
    if int(due) < 1:
        return False
    exists = session.scalar(select(Record.id).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["source"].as_string() == source,
    ).limit(1))
    if exists:
        return False
    data = rule.data or {}
    payload = {
        "sim_id":sim.id, "sim_name":sim.label, "source_id":rule.id,
        "roll_type":rule.label, "die":data.get("die") or "d20", "bad_results":"",
        "trigger_results":data.get("trigger_results") or "",
        "result_rules":data.get("result_rules") or "",
        "failure_outcome":"", "success_outcome":"", "nonlethal":True,
        "occult_roll":True, "occult_rule_id":rule.id,
        "occult_rule_key":data.get("rule_key"), "occult_type":data.get("occult"),
        "source":source, "due_global_day":int(due), "completed":False,
        "notes":data.get("notes") or "",
        "allow_after_death":str(data.get("occult") or "") == "Ghost" and str(data.get("rule_key") or "") != "ghost_haunting_death",
    }
    payload.update(overrides or {})
    roll = Record(save_id=save.id, kind="roll", label=label or f"{sim.label} — {rule.label}",
                  global_day=int(due), data=payload)
    session.add(roll); session.flush(); journal(session, roll, "upsert", 0)
    return True


def _occult_inheritance_rolls(session: Session, save: ChronicleSave, sims: list[Record],
                              rules: list[Record], enabled_from: int) -> int:
    rule = next((item for item in rules if (item.data or {}).get("rule_key") == "general_inheritance"
                 and bool((item.data or {}).get("active", True)) and bool((item.data or {}).get("auto_schedule", False))), None)
    if not rule:
        return 0
    by_id = {sim.id:sim for sim in sims}
    created = 0
    for child in sims:
        data = child.data or {}
        birth = data.get("birth_global_day", child.global_day)
        if birth is None or int(birth) < enabled_from or int(birth) > save.global_day:
            continue
        if occult_rules.sim_occult_types(data):
            continue
        mother = by_id.get(str(data.get("mother_id") or ""))
        father = by_id.get(str(data.get("father_id") or ""))
        if not mother or not father:
            continue
        mother_types = occult_rules.sim_occult_types(mother.data)
        father_types = occult_rules.sim_occult_types(father.data)
        mother_dormant = occult_rules.dormant_occult_types(mother.data)
        father_dormant = occult_rules.dormant_occult_types(father.data)
        die = trigger = results = effect = ""
        candidates: list[str] = []
        if mother_types and father_types and mother_types[0] != father_types[0]:
            candidates = [mother_types[0], father_types[0]]
            die, trigger, effect = "d2", "1-2", "inherit_occult_choice"
            results = f"1: Inherits {candidates[0]}; 2: Inherits {candidates[1]}"
        elif bool(mother_types) != bool(father_types):
            candidates = list(mother_types or father_types)
            die, trigger, effect = "d4", "1", "add_dormant_occult"
            results = f"1: Carries dormant {candidates[0]} blood; 2-4: Human without dormant blood"
        elif mother_dormant and father_dormant:
            candidates = list(dict.fromkeys(mother_dormant + father_dormant))
            die, trigger, effect = "d4", "1", "manifest_dormant_occult"
            results = f"1: Manifests {candidates[0]}; 2-4: Remains human"
        elif bool(mother_dormant) != bool(father_dormant):
            candidates = list(mother_dormant or father_dormant)
            die, trigger, effect = "d10", "1", "manifest_dormant_occult"
            results = f"1: Manifests {candidates[0]}; 2-10: Remains human"
        if not candidates:
            continue
        source = f"occult:inheritance:{child.id}"
        created += int(_add_occult_roll(
            session, save, rule, child, max(enabled_from, int(birth)), source,
            label=f"{child.label} — Occult inheritance",
            overrides={"die":die, "trigger_results":trigger, "result_rules":results,
                       "occult_effect":effect, "occult_candidates":candidates},
        ))
    return created


def _occult_alignment_rolls(session: Session, save: ChronicleSave, sims: list[Record],
                            rules: list[Record]) -> int:
    """Create the one-time alignment roll required by the supplied rules."""
    rule = next((item for item in rules if (item.data or {}).get("rule_key") == "alignment_inheritance"
                 and bool((item.data or {}).get("active", True))
                 and bool((item.data or {}).get("auto_schedule", False))), None)
    if not rule:
        return 0
    by_id = {sim.id: sim for sim in sims}
    created = 0
    missing_values = {"", "unknown", "undetermined", "none", "not set"}
    for sim in sims:
        sim_data = sim.data or {}
        occult = occult_rules.alignment_occult(sim_data)
        current_alignment = str(sim_data.get("occult_alignment") or "").strip()
        if not occult or current_alignment.casefold() not in missing_values:
            continue
        if not occult_rules.living(sim_data, save.global_day):
            continue

        aligned_parents: list[tuple[Record, str]] = []
        for parent_key in ("mother_id", "father_id"):
            parent = by_id.get(str(sim_data.get(parent_key) or ""))
            if not parent or parent.deleted:
                continue
            side = occult_rules.alignment_side((parent.data or {}).get("occult_alignment"))
            if side:
                aligned_parents.append((parent, side))
        same_occult = [item for item in aligned_parents if occult in occult_rules.sim_occult_types(item[0].data)]
        parents = same_occult or aligned_parents

        good = occult_rules.alignment_label(occult, "good")
        bad = occult_rules.alignment_label(occult, "bad")
        parent_ids = [parent.id for parent, _side in parents]
        if parents and len({side for _parent, side in parents}) == 1:
            inherited_side = parents[0][1]
            inherited = occult_rules.alignment_label(occult, inherited_side)
            opposite = occult_rules.alignment_label(occult, "bad" if inherited_side == "good" else "good")
            die = "d10"
            results = f"1: {opposite}; 2-10: {inherited}"
            result_map = {"1": opposite, **{str(value): inherited for value in range(2, 11)}}
            basis = "Inherits an occult parent's alignment; 1 on D10 gives the opposite alignment."
        else:
            # Opposing aligned parents and founders both use an even D2.  For
            # founders this establishes alignment without inventing ancestry.
            die = "d2"
            results = f"1: {good}; 2: {bad}"
            result_map = {"1": good, "2": bad}
            basis = (
                "Opposing occult parents — coin flip between alignments."
                if parents else "No known aligned occult parent — establish alignment by coin flip."
            )
        created += int(_add_occult_roll(
            session, save, rule, sim, int(save.global_day), f"occult:alignment:{sim.id}",
            label=f"{sim.label} — {occult} alignment",
            overrides={
                "die": die, "trigger_results": "", "result_rules": results,
                "occult_type": occult, "occult_effect": "set_occult_alignment",
                "occult_alignment_result_map": result_map,
                "occult_alignment_parent_ids": parent_ids,
                "occult_alignment_basis": basis,
                "notes": basis,
            },
        ))
    return created


def _ghost_persistence_rolls(session: Session, save: ChronicleSave, sims: list[Record],
                             rules: list[Record], enabled_from: int) -> int:
    rule = next((item for item in rules if (item.data or {}).get("rule_key") == "ghost_persistence"
                 and bool((item.data or {}).get("active", True)) and bool((item.data or {}).get("auto_schedule", False))), None)
    if not rule:
        return 0
    created = 0
    for sim in sims:
        data = sim.data or {}
        death = data.get("death_global_day")
        birth = data.get("birth_global_day", sim.global_day)
        if death in (None, "") or birth in (None, "") or int(death) > save.global_day:
            continue
        age_days = int(death) - int(birth)
        minimum_age_days = 10 * max(1, int(save.days_per_year))
        if age_days < minimum_age_days:
            continue
        cause = str(data.get("cause_of_death") or "").casefold()
        if any(word in cause for word in ("murder", "execut", "assassin")):
            die, trigger, description = "d2", "1", "1: Heads — ghost remains; 2: Tails — spirit moves on"
        elif any(word in cause for word in ("betray", "revenge", "vengeance")):
            die, trigger, description = "d4", "1-2", "1-2: Ghost remains; 3-4: Spirit moves on"
        elif any(word in cause for word in ("accident", "fire", "drown", "fall", "lightning")):
            die, trigger, description = "d4", "1", "1: Ghost remains; 2-4: Spirit moves on"
        elif any(word in cause for word in ("old age", "peaceful")):
            die, trigger, description = "d8", "1", "1: Ghost remains; 2-8: Spirit moves on"
        else:
            die, trigger, description = "d6", "1", "1: Ghost remains; 2-6: Spirit moves on"
        created += int(_add_occult_roll(
            session, save, rule, sim, int(death), f"occult:ghost-persistence:{sim.id}:{death}",
            overrides={"die":die, "trigger_results":trigger, "result_rules":description,
                       "occult_effect":"persistent_ghost", "age_at_death_days":age_days,
                       "age_at_death_years":age_days // max(1, int(save.days_per_year)),
                       "minimum_ghost_age_years":10},
        ))
    return created


def schedule_occult_rolls(session: Session, save: ChronicleSave,
                          sims: list[Record] | None = None) -> int:
    """Schedule only occult obligations whose eligibility can be derived safely."""
    settings = save.settings or {}
    if not bool(settings.get("automatic_occult_rolls", False)):
        return 0
    # Repair duplicate built-in rows from early 4.x saves before they fan out
    # into duplicate annual hunts or full-moon obligations.
    save.revision += repair_duplicate_occult_rules(session, save)
    enabled_from = max(1, int(settings.get("occult_rolls_enabled_from_global_day") or save.global_day))
    sims = sims if sims is not None else list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False)
    )))
    rules = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "occult_rule", Record.deleted.is_(False)
    )))
    created = _occult_inheritance_rolls(session, save, sims, rules, enabled_from)
    created += _occult_alignment_rolls(session, save, sims, rules)
    created += _ghost_persistence_rolls(session, save, sims, rules, enabled_from)
    living_sims = [sim for sim in sims if occult_rules.living(sim.data, save.global_day)]
    current_year = _occult_year(save, save.global_day)
    year_start = max(1, (current_year - save.start_year) * max(1, save.days_per_year) + 1)

    def condition_ok(rule: Record, sim: Record) -> bool:
        condition = str((rule.data or {}).get("condition") or "")
        if condition in {"coastal", "inland"}:
            return occult_rules.water_access(sim.data, settings) == condition
        if condition == "loose":
            return not bool((sim.data or {}).get("werewolf_confined", False))
        if condition in {"good", "bad"}:
            return occult_rules.aligned(sim.data, condition)
        return True

    def eligible(rule: Record) -> list[Record]:
        occult = str((rule.data or {}).get("occult") or "")
        pool = sims if occult == "Ghost" else living_sims
        result = [sim for sim in pool if occult in occult_rules.sim_occult_types(sim.data) and condition_ok(rule, sim)]
        if occult == "Ghost" and str((rule.data or {}).get("cadence") or "") == "annual":
            # A game ghost is not automatically a persistent challenge ghost.
            # Annual haunting/move-on checks begin only after the death roll
            # says that the spirit remains, and stop after release/moving on.
            result = [sim for sim in result if (
                str((sim.data or {}).get("persistent_ghost_roll") or "") == "Spirit remains"
                and not bool((sim.data or {}).get("ghost_moved_on"))
            )]
            if str((rule.data or {}).get("rule_key") or "") == "ghost_move_on":
                result = [sim for sim in result if not bool((sim.data or {}).get("ghost_bound"))]
        return result

    # One current-year obligation is created per eligible Sim or household. This
    # avoids flooding a newly enabled save with rolls from already-played years.
    existing_occult_rolls = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["occult_roll"].as_boolean().is_(True),
    )))
    annual_identities = {
        (str((item.data or {}).get("occult_rule_key") or ""), str((item.data or {}).get("sim_id") or ""),
         _occult_year(save, int(item.global_day or save.global_day)))
        for item in existing_occult_rolls
    }
    sims_by_id = {sim.id: sim for sim in sims}
    household_identities = set()
    moon_identities = set()
    for item in existing_occult_rolls:
        item_data = item.data or {}
        key = str(item_data.get("occult_rule_key") or "")
        sim_id = str(item_data.get("sim_id") or "")
        household_id = str(item_data.get("occult_household_id") or "")
        if not household_id and sim_id in sims_by_id:
            household_id = str((sims_by_id[sim_id].data or {}).get("current_household_id") or f"unhoused-{sim_id}")
        if household_id:
            household_identities.add((key, household_id, _occult_year(save, int(item.global_day or save.global_day))))
        if ":moon:" in str(item_data.get("source") or ""):
            moon_identities.add((key, sim_id, int(item.global_day or save.global_day)))
    annual_rules = [rule for rule in rules if (rule.data or {}).get("cadence") == "annual"
                    and _occult_rule_matches(rule, current_year)]
    for rule in annual_rules:
        due = max(year_start, enabled_from)
        targets = eligible(rule)
        if str((rule.data or {}).get("scope") or "sim") == "household":
            groups: dict[str, list[Record]] = {}
            for sim in targets:
                key = str((sim.data or {}).get("current_household_id") or f"unhoused-{sim.id}")
                groups.setdefault(key, []).append(sim)
            for group, members in groups.items():
                representative = sorted(members, key=lambda item:item.label.casefold())[0]
                identity = (str((rule.data or {}).get("rule_key") or ""), group, current_year)
                if identity in household_identities:
                    continue
                source = f"occult:{rule.id}:household:{group}:{current_year}"
                added = _add_occult_roll(
                    session, save, rule, representative, due, source,
                    label=f"{rule.label} — {(representative.data or {}).get('game_household_name') or representative.label}",
                    overrides={"occult_household_id":group, "eligible_occult_sim_ids":[item.id for item in members]},
                )
                created += int(added)
                if added:
                    household_identities.add(identity)
        else:
            for sim in targets:
                identity = (str((rule.data or {}).get("rule_key") or ""), sim.id, current_year)
                if identity in annual_identities:
                    continue
                source = f"occult:{rule.id}:{sim.id}:{current_year}"
                added = _add_occult_roll(session, save, rule, sim, due, source)
                created += int(added)
                if added:
                    annual_identities.add(identity)

    # Full-moon obligations use an editable anchor and interval. Every moon crossed
    # since enabling is retained, even when the player skips several days at once.
    interval = max(1, int(settings.get("full_moon_interval_days") or 8))
    anchor = max(1, int(settings.get("full_moon_anchor_global_day") or 1))
    first = anchor
    if first < enabled_from:
        first += ((enabled_from - first + interval - 1) // interval) * interval
    moon_day = first
    while moon_day <= save.global_day:
        moon_year = _occult_year(save, moon_day)
        for rule in rules:
            if (rule.data or {}).get("cadence") != "full_moon" or not _occult_rule_matches(rule, moon_year):
                continue
            for sim in eligible(rule):
                identity = (str((rule.data or {}).get("rule_key") or ""), sim.id, moon_day)
                if identity in moon_identities:
                    continue
                source = f"occult:{rule.id}:{sim.id}:moon:{moon_day}"
                added = _add_occult_roll(session, save, rule, sim, moon_day, source)
                created += int(added)
                if added:
                    moon_identities.add(identity)
        moon_day += interval

    # Reconcile completed rule outcomes from before the shared follow-up engine
    # existed. Non-triggered outcomes are included because branches such as an
    # innocent witch-trial verdict or a surviving werewolf victim also create a
    # required next roll. Each origin is upgraded once, keeping later page loads
    # cheap even in a long-running save.
    automatic_parent_keys = set(occult_rules.AUTOMATIC_OCCULT_FOLLOW_UP_SPECS)
    for definition in list(rules) + list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "future_rule", Record.deleted.is_(False),
    ))):
        automatic_parent_keys.update(_declared_followup_parents((definition.data or {}).get("triggered_by")))
    completed_triggers = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "roll",
        Record.deleted.is_(False),
        Record.data["completed"].as_boolean().is_(True),
    )))
    for origin in completed_triggers:
        origin_data = origin.data or {}
        parent_key = str(origin_data.get("source_rule_key") or origin_data.get("occult_rule_key") or "")
        if not parent_key or int(origin_data.get("rule_followup_automation_version") or 0) >= 2:
            continue
        if parent_key not in automatic_parent_keys:
            continue
        if not (origin_data.get("occult_roll") or origin_data.get("rule_generated")):
            continue
        before = dict(origin_data)
        base = origin.version
        followups_created = _schedule_automatic_occult_followup(session, save, origin)
        created += followups_created
        if origin.data != before:
            origin.version += 1
            journal(session, origin, "upsert", base)
            if not followups_created: created += 1
    return created


def apply_occult_roll_result(session: Session, roll: Record, actual: int) -> int:
    """Persist safe tracker-side inheritance effects without changing the game."""
    data = roll.data or {}
    if not bool(data.get("occult_roll")):
        return 0
    triggered = failed(actual, str(data.get("trigger_results") or ""))
    effect = str(data.get("occult_effect") or "")
    sim = session.get(Record, data.get("sim_id")) if data.get("sim_id") else None
    if not sim or sim.deleted:
        return 0
    sim_data = dict(sim.data or {})
    rule_key = str(data.get("source_rule_key") or data.get("occult_rule_key") or "")
    candidates = [str(value) for value in (data.get("occult_candidates") or []) if value]
    if effect == "set_occult_alignment":
        result_map = data.get("occult_alignment_result_map") or {}
        selected = str(result_map.get(str(actual)) or "").strip()
        if not selected:
            return 0
        sim_data.update({
            "occult_alignment": selected,
            "occult_alignment_source_roll_id": roll.id,
            "occult_alignment_global_day": int(roll.global_day or 1),
        })
    elif effect == "add_dormant_occult" and candidates and triggered:
        existing = occult_rules.dormant_occult_types(sim_data)
        sim_data["dormant_occult_types"] = list(dict.fromkeys(existing + candidates))
    elif effect == "manifest_dormant_occult" and candidates and triggered:
        sim_data["challenge_manifested_occult"] = candidates[0]
        sim_data["dormant_occult_types"] = [value for value in occult_rules.dormant_occult_types(sim_data) if value != candidates[0]]
    elif effect == "inherit_occult_choice" and candidates and triggered:
        index = min(max(1, int(actual)), len(candidates)) - 1
        sim_data["challenge_inherited_occult"] = candidates[index]
    elif effect == "persistent_ghost":
        sim_data["persistent_ghost_roll"] = "Spirit remains" if triggered else "Spirit moves on"
        sim_data["ghost_moved_on"] = not triggered
        if triggered:
            sim_data["challenge_manifested_occult"] = "Ghost"
        elif sim_data.get("challenge_manifested_occult") == "Ghost":
            sim_data.pop("challenge_manifested_occult", None)
    elif rule_key in {"werewolf_turn_adult", "werewolf_turn_child"} and triggered:
        sim_data["challenge_manifested_occult"] = "Werewolf"
        sim_data["occult_transformation_source_roll_id"] = roll.id
    elif rule_key == "fairy_changeling_truth" and triggered:
        sim_data["challenge_manifested_occult"] = "Fairy"
        sim_data["occult_transformation_source_roll_id"] = roll.id
    elif rule_key == "fairy_discovery" and triggered:
        sim_data.update({
            "fairy_discovered": True,
            "fairy_discovery_status": "Discovered — awaiting community response",
            "fairy_discovery_global_day": int(roll.global_day or 1),
            "fairy_discovery_source_roll_id": roll.id,
        })
    elif rule_key == "fairy_discovery_response":
        save = session.get(ChronicleSave, roll.save_id)
        year_days = max(1, int(save.days_per_year if save else 4))
        if actual <= 2:
            sim_data.update({
                "fairy_discovered": False,
                "fairy_discovery_status": "Dismissed as folklore; secrecy restored",
                "fairy_relocation_required": False,
                "fairy_hunt_active": False,
            })
        elif actual <= 4:
            sim_data.update({
                "fairy_discovered": True,
                "fairy_discovery_status": "Must leave or hide for one historical year",
                "fairy_relocation_required": True,
                "fairy_concealment_until_global_day": int(roll.global_day or 1) + year_days,
                "fairy_hunt_active": False,
            })
        else:
            sim_data.update({
                "fairy_discovered": True,
                "fairy_discovery_status": "Community persecution or fairy hunt",
                "fairy_relocation_required": True,
                "fairy_hunt_active": True,
            })
    elif rule_key == "fairy_discovery_danger":
        if triggered:
            sim_data["fairy_discovery_status"] = "Killed during fairy persecution"
        elif actual <= 4:
            sim_data.update({
                "fairy_discovery_status": "Escaped persecution; must leave the settlement",
                "fairy_relocation_required": True,
                "fairy_hunt_active": False,
            })
        else:
            sim_data.update({
                "fairy_discovered": False,
                "fairy_discovery_status": "Identity concealed after persecution",
                "fairy_relocation_required": False,
                "fairy_hunt_active": False,
            })
    elif rule_key == "vampire_feeding_suspicion" and triggered:
        sim_data["vampire_suspicion_raised"] = True
        sim_data["vampire_suspicion_source_roll_id"] = roll.id
    elif rule_key in {"ghost_move_on", "ghost_exorcism"} and triggered:
        sim_data["persistent_ghost_roll"] = "Spirit moves on"
        sim_data["ghost_moved_on"] = True
        sim_data["ghost_bound"] = False
        if sim_data.get("challenge_manifested_occult") == "Ghost":
            sim_data.pop("challenge_manifested_occult", None)
    elif rule_key == "ghost_binding" and triggered:
        sim_data["ghost_bound"] = True
        sim_data["ghost_moved_on"] = False
    else:
        return 0
    base = sim.version; sim.data = sim_data; sim.version += 1; journal(session, sim, "upsert", base)
    return 1


def _declared_followup_parents(value) -> set[str]:
    if isinstance(value, str):
        value = re.split(r"[,;|]+", value)
    return {str(item).strip() for item in (value or []) if str(item).strip()}


def _close_relation_ids(session: Session, save: ChronicleSave, actor: Record, sims: list[Record]) -> set[str]:
    """Return family, spouse and explicitly close-friend targets for an actor."""
    by_id = {sim.id:sim for sim in sims}
    data = actor.data or {}
    close = {str(data.get(key) or "") for key in ("mother_id", "father_id")}
    close.update(sim.id for sim in sims if actor.id in {
        str((sim.data or {}).get("mother_id") or ""), str((sim.data or {}).get("father_id") or "")
    })
    actor_parents = {str(data.get("mother_id") or ""), str(data.get("father_id") or "")} - {""}
    if actor_parents:
        close.update(sim.id for sim in sims if sim.id != actor.id and actor_parents.intersection({
            str((sim.data or {}).get("mother_id") or ""), str((sim.data or {}).get("father_id") or "")
        }))
    for relationship in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "relationship", Record.deleted.is_(False),
    )):
        rel = relationship.data or {}
        first, second = str(rel.get("partner1_id") or ""), str(rel.get("partner2_id") or "")
        if actor.id not in {first, second}:
            continue
        status = str(rel.get("status") or "Active").casefold()
        relation_type = str(rel.get("type") or "").casefold()
        is_close = (
            status not in {"ended", "divorced", "annulled", "separated", "inactive"}
            and (bool(rel.get("legally_married")) or "marriage" in relation_type or "spouse" in relation_type)
        ) or "close friend" in relation_type or "best friend" in relation_type
        if is_close:
            close.add(second if first == actor.id else first)
    return {sim_id for sim_id in close if sim_id in by_id and sim_id != actor.id}


def _followup_target(session: Session, save: ChronicleSave, origin: Record, strategy: str,
                     sims: list[Record]) -> Record | None:
    data = origin.data or {}
    origin_sim = session.get(Record, data.get("sim_id")) if data.get("sim_id") else None
    actor_id = str(data.get("rule_actor_sim_id") or (origin_sim.id if origin_sim else ""))
    actor = session.get(Record, actor_id) if actor_id else origin_sim
    if strategy == "origin":
        return origin_sim if origin_sim and not origin_sim.deleted and origin_sim.save_id == save.id else None

    living = [sim for sim in sims if sim.id != actor_id and occult_rules.living(sim.data, save.global_day)]
    if not living:
        return None
    actor_household = str(((actor.data or {}) if actor else {}).get("current_household_id") or "")

    def preferred(pool: list[Record]) -> list[Record]:
        same_home = [sim for sim in pool if actor_household and str((sim.data or {}).get("current_household_id") or "") == actor_household]
        return same_home or pool

    if strategy == "living_human_other":
        living = [sim for sim in living if not occult_rules.sim_occult_types(sim.data)]
    elif strategy == "living_non_spellcaster":
        living = [sim for sim in living if "Spellcaster" not in occult_rules.sim_occult_types(sim.data)]
    elif strategy in {"werewolf_close_relation", "unrelated_victim"} and actor:
        close = _close_relation_ids(session, save, actor, sims)
        living = [sim for sim in living if (sim.id in close) == (strategy == "werewolf_close_relation")]
    if not living:
        return None
    return random.SystemRandom().choice(preferred(living))


def _followup_rule(session: Session, save: ChronicleSave, key: str, year: int,
                   explicit_id: str = "") -> Record | None:
    candidates = []
    for rule in session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind.in_(("occult_rule", "future_rule")),
        Record.deleted.is_(False),
    )):
        rule_data = rule.data or {}
        if explicit_id and rule.id != explicit_id:
            continue
        if not explicit_id and str(rule_data.get("rule_key") or rule_data.get("key") or rule.id) != key:
            continue
        if not bool(rule_data.get("active", True)) or rule_data.get("auto_followup_enabled") is False:
            continue
        if int(rule_data.get("start_year", -9999)) <= year <= int(rule_data.get("end_year", 9999)):
            candidates.append(rule)
    if not candidates:
        return None
    return min(candidates, key=lambda item: int((item.data or {}).get("end_year", 9999)) - int((item.data or {}).get("start_year", -9999)))


def _create_automatic_followup(session: Session, save: ChronicleSave, origin: Record,
                               rule: Record, sim: Record, due: int, actor_id: str,
                               sequence: int = 0, overrides: dict | None = None) -> tuple[Record | None, bool]:
    origin_data = origin.data or {}; rule_data = rule.data or {}
    child_key = str(rule_data.get("rule_key") or rule_data.get("key") or rule.id)
    existing = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["origin_roll_id"].as_string() == origin.id,
    )))
    followup = next((item for item in existing if str(
        (item.data or {}).get("source_rule_key") or (item.data or {}).get("occult_rule_key") or ""
    ) == child_key and str((item.data or {}).get("sim_id") or "") == sim.id
        and int((item.data or {}).get("followup_sequence") or 0) == int(sequence)), None)
    if followup:
        return followup, False

    lethal = str(rule_data.get("lethal_results") or (occult_rules.lethal_results(child_key) if rule.kind == "occult_rule" else "")).strip()
    source = f"rule:auto-followup:{origin.id}:{rule.id}:{sim.id}:{int(sequence)}"
    context = f"Automatically scheduled after {origin.label}: {origin_data.get('outcome') or 'triggered'}"
    common = {
        "origin_roll_id":origin.id, "source_rule_id":rule.id, "source_rule_kind":rule.kind,
        "source_rule_key":child_key, "rule_generated":True, "automatic_followup":True,
        "followup_sequence":int(sequence),
        "rule_actor_sim_id":actor_id, "rule_context":context,
        "bad_results":lethal, "nonlethal":not bool(lethal), "failure_is_lethal":bool(lethal),
        "allow_after_death":bool(rule_data.get("allow_after_death")) or (rule.kind == "occult_rule" and str(rule_data.get("occult") or "") == "Ghost" and child_key != "ghost_haunting_death"),
    }
    common.update(overrides or {})
    if rule.kind == "occult_rule":
        created = _add_occult_roll(session, save, rule, sim, due, source, overrides=common)
    else:
        payload = {
            "sim_id":sim.id, "sim_name":sim.label, "source_id":rule.id,
            "roll_type":rule.label, "die":rule_data.get("die") or "d20",
            "trigger_results":str(rule_data.get("trigger_results") or ""),
            "result_rules":str(rule_data.get("result_rules") or rule_data.get("rules") or ""),
            "source":source, "due_global_day":int(due), "completed":False,
            "notes":str(rule_data.get("notes") or ""), **common,
        }
        followup = Record(save_id=save.id, kind="roll", label=f"{sim.label} — {rule.label}", global_day=int(due), data=payload)
        session.add(followup); session.flush(); journal(session, followup, "upsert", 0)
        return followup, True
    followup = session.scalar(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["source"].as_string() == source,
    ).limit(1))
    return followup, bool(created)


def _schedule_automatic_occult_followup(session: Session, save: ChronicleSave, roll: Record) -> int:
    """Create every required built-in or declared follow-up without duplicates."""
    data = roll.data or {}
    if not bool(data.get("completed")):
        return 0
    parent_key = str(data.get("source_rule_key") or data.get("occult_rule_key") or "")
    if not parent_key:
        return 0

    specs = [dict(spec) for spec in occult_rules.automatic_follow_up_specs(parent_key)]
    built_in_keys = {str(spec.get("rule_key") or "") for spec in specs}
    for rule in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind.in_(("occult_rule", "future_rule")), Record.deleted.is_(False),
    )):
        rule_data = rule.data or {}
        child_key = str(rule_data.get("rule_key") or rule_data.get("key") or rule.id)
        if parent_key in _declared_followup_parents(rule_data.get("triggered_by")) and child_key not in built_in_keys:
            specs.append({"rule_key":child_key, "rule_id":rule.id, "target":str(rule_data.get("followup_target") or "origin")})
    if not specs:
        return 0

    sims = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
    )))
    parent_year = _occult_year(save, int(roll.global_day or save.global_day))
    actor_id = str(data.get("rule_actor_sim_id") or data.get("sim_id") or "")
    followup_ids = list(data.get("rule_followup_ids") or [])
    processed = set(str(item) for item in (data.get("automatic_followup_processed") or []))
    skipped = list(data.get("automatic_followup_skipped") or [])
    created = 0; missing_rule = False

    for index, spec in enumerate(specs):
        token = str(spec.get("rule_id") or f"{parent_key}:{index}:{spec.get('rule_key')}:{spec.get('when','triggered')}")
        if token in processed:
            continue
        when = str(spec.get("when") or "triggered")
        triggered = bool(data.get("triggered"))
        if (when == "triggered" and not triggered) or (when == "not_triggered" and triggered):
            processed.add(token); continue
        if spec.get("minimum_year") is not None and parent_year < int(spec["minimum_year"]):
            processed.add(token); continue
        wanted_alignment = str(spec.get("actor_alignment") or "")
        actor = session.get(Record, actor_id) if actor_id else None
        if wanted_alignment and (not actor or not occult_rules.aligned(actor.data, wanted_alignment)):
            processed.add(token); continue

        target_strategy = str(spec.get("target") or "origin")
        targets: list[Record] = []
        if target_strategy == "eligible_occult_members":
            wanted = str(spec.get("occult") or "")
            eligible_ids = {str(item) for item in (data.get("eligible_occult_sim_ids") or []) if item}
            targets = [sim for sim in sims if sim.id in eligible_ids and occult_rules.living(sim.data, save.global_day)
                       and wanted in occult_rules.sim_occult_types(sim.data)]
            if not targets:
                fallback = _followup_target(session, save, roll, "origin", sims)
                targets = [fallback] if fallback else []
        else:
            selected = _followup_target(session, save, roll, target_strategy, sims)
            targets = [selected] if selected else []
        child_key = str(spec.get("rule_key") or "")
        if not targets and spec.get("fallback_rule_key"):
            child_key = str(spec["fallback_rule_key"])
            target_strategy = str(spec.get("fallback_target") or "living_victim")
            selected = _followup_target(session, save, roll, target_strategy, sims)
            targets = [selected] if selected else []
        if not targets:
            skipped.append({"rule_key":child_key, "reason":"No eligible Sim was available", "global_day":save.global_day})
            processed.add(token); continue

        # The original werewolf rule selects a random victim before asking
        # whether kinship changes the attack. Do not preferentially select kin.
        if spec.get("close_relation_rule_key"):
            actor = session.get(Record, actor_id) if actor_id else None
            close_ids = _close_relation_ids(session, save, actor, sims) if actor else set()
            if targets[0].id in close_ids:
                child_key = str(spec["close_relation_rule_key"])

        due = save.global_day
        if str(spec.get("due") or "") == "child_stage" and targets:
            try: due = max(save.global_day, int((targets[0].data or {}).get("birth_global_day", targets[0].global_day)) + 20)
            except (TypeError, ValueError): due = save.global_day
        rule_year = _occult_year(save, due)
        rule = _followup_rule(session, save, child_key, rule_year, str(spec.get("rule_id") or ""))
        if not rule:
            missing_rule = True
            continue
        matched_target = False
        for target_index, target in enumerate(targets):
            target_data = target.data or {}
            age_group = str(spec.get("age_group") or "")
            if age_group:
                stage = str(target_data.get("game_age_stage") or "").casefold()
                birth = target_data.get("birth_global_day", target.global_day)
                try: age_days = max(0, int(roll.global_day or save.global_day) - int(birth))
                except (TypeError, ValueError): age_days = 52 if stage in {"teen", "young adult", "adult", "elder"} else 20
                teen_plus = stage in {"teen", "young adult", "adult", "elder"} if stage else age_days >= 52
                if (age_group == "child" and teen_plus) or (age_group == "teen_plus" and not teen_plus):
                    continue
            repeats = 1
            forced_accusation = False
            if child_key == "vampire_accused":
                exposure = str(target_data.get("vampire_hunt_exposure") or "secret").strip().casefold()
                if exposure in {"witnessed powers", "public feeding", "attack", "exposed"}:
                    repeats = 2
                forced_accusation = bool(target_data.get("vampire_suspicion_raised"))
                if forced_accusation:
                    repeats = 1
            for repeat_index in range(repeats):
                sequence = target_index * 10 + repeat_index
                followup, was_created = _create_automatic_followup(
                    session, save, roll, rule, target, due,
                    target.id if child_key == "vampire_accused" else actor_id,
                    sequence=sequence,
                    overrides={
                        "vampire_accusation_number":repeat_index + 1,
                        "vampire_accusation_count":repeats,
                        "automatic_accusation_from_suspicion":forced_accusation,
                    } if child_key == "vampire_accused" else None,
                )
                if followup:
                    matched_target = True
                    if followup.id not in followup_ids: followup_ids.append(followup.id)
                    created += int(was_created)
                    if was_created and forced_accusation:
                        nested = complete_roll(
                            session, save, followup, 9,
                            "Accused automatically because prior feeding raised suspicion",
                        )
                        created += int(nested.get("automatic_followups") or 0)
                        target_base = target.version
                        target.data = {**(target.data or {}), "vampire_suspicion_raised":False,
                                       "vampire_suspicion_consumed_global_day":save.global_day}
                        target.version += 1; journal(session, target, "upsert", target_base)
        if matched_target:
            processed.add(token)

    complete = not missing_rule
    roll.data = {
        **data,
        "rule_followup_ids":followup_ids,
        "automatic_followup_processed":sorted(processed),
        "automatic_followup_skipped":skipped,
        "rule_followup_last_created_global_day":save.global_day,
        "rule_followup_reviewed":complete,
        "rule_followup_reviewed_global_day":save.global_day if complete else data.get("rule_followup_reviewed_global_day"),
        "rule_followup_automatic":True,
        "rule_followup_automation_version":2 if complete else int(data.get("rule_followup_automation_version") or 0),
    }
    return created


def schedule_event_rolls(session: Session, save: ChronicleSave, sims: list[Record] | None = None) -> int:
    """Backfill reached historical-event rolls without running every scheduler.

    Today uses this focused pass after imports and same-day clock reconnects.  It
    deliberately reads the editable start day from event data instead of trusting
    the indexed record day, because older imports can leave those two values out
    of sync.
    """
    repaired = repair_pending_event_rolls(session, save)
    if sims is None:
        sims = list(session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
        )))
    event_candidates = []
    for event in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "event", Record.deleted.is_(False),
    )):
        data = event.data or {}
        try:
            due = int(data.get("start_global_day", event.global_day))
        except (TypeError, ValueError):
            continue
        if (
            1 <= due <= save.global_day
            and not event_is_ignored(event)
            and data.get("active", True)
            and data.get("roll_required")
        ):
            event_candidates.append(event)

    event_groups: dict[tuple[str, int, int, str], list[Record]] = {}
    for event in event_candidates:
        event_groups.setdefault(_event_occurrence_key(event), []).append(event)
    events = [
        next((item for item in group if (item.data or {}).get("catalog_id")), group[0])
        for group in event_groups.values()
    ]
    event_rules = _event_rule_map(session, save)
    households = {
        record.id: record for record in session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "household", Record.deleted.is_(False),
        ))
    }
    pregnancies = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "pregnancy", Record.deleted.is_(False),
    )))
    household_locations = {
        _normalized_location(value)
        for household in households.values()
        for value in (
            (household.data or {}).get("country"),
            (household.data or {}).get("location"),
            (household.data or {}).get("world"),
        )
        if _normalized_location(value)
    }
    fallback_location = next(iter(household_locations)) if len(household_locations) == 1 else ""
    existing_event_sources = set(session.scalars(select(Record.data["source"].as_string()).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["source"].as_string().like("event:%"),
    )))
    created = repaired
    for event in events:
        event_data = event.data or {}
        event_start = int(event_data.get("start_global_day", event.global_day))
        rule_data = event_rules.get(event_key(event), {})
        spec = event_roll_configuration(event, rule_data)
        source_plan = [dict(step) for step in (event_data.get("source_roll_plan") or []) if isinstance(step, dict)]
        root_steps = [step for step in source_plan if not (step.get("parent_indices") or []) and step.get("parent_index") is None]
        event_words = f"{event.label} {event_data.get('scope','')} {event_data.get('notes','')}".casefold()
        classic_war = core_rulesets.selected_core(save) == core_rulesets.CLASSIC_2023 and any(
            word in event_words for word in ("war", "battle", "military", "draft")
        )
        if classic_war:
            spec = {**spec, "die":"d6", "bad_results":"1,3,5",
                    "result_rules":"1,3,5: Dies in the war; 2,4,6: Returns home",
                    "failure_outcome":"Dies in the war", "failure_is_lethal":True}
            root_steps = []
        equivalent_event_ids = {
            equivalent.id for equivalent in event_groups[_event_occurrence_key(event)]
        }
        for sim in sims:
            death = (sim.data or {}).get("death_global_day")
            household = households.get(str((sim.data or {}).get("current_household_id") or ""))
            event_text = f"{event.label} {event_data.get('scope','')} {event_data.get('notes','')}".casefold()
            servo_exempt = "Servo" in occult_rules.sim_occult_types(sim.data) and any(
                word in event_text for word in ("disease", "illness", "plague", "epidemic", "pandemic", "famine", "starvation", "drown")
            )
            if (
                bool((sim.data or {}).get("game_was_dead"))
                or (death is not None and int(death) <= save.global_day)
                or servo_exempt
            ):
                continue
            steps = root_steps or [None]
            for root_position, step in enumerate(steps):
                step = dict(step or {})
                step_index = int(step.get("index") or 0)
                explicit_interval = event_data.get("roll_repeat_interval_years")
                if explicit_interval not in (None, ""):
                    try:
                        repeat_interval = max(0, int(explicit_interval))
                    except (TypeError, ValueError):
                        repeat_interval = 0
                else:
                    try:
                        repeat_interval = max(0, int(step.get("repeat_interval_years") or 0))
                    except (TypeError, ValueError):
                        repeat_interval = 0
                    repeat_interval = repeat_interval or source_roll_repeat_interval_years(step.get("context"))
                    if not repeat_interval and (not step or len(root_steps) == 1):
                        repeat_interval = source_roll_repeat_interval_years(event_data.get("notes"))
                if repeat_interval:
                    raw_end = event_data.get("end_global_day")
                    try:
                        event_end = int(raw_end) if raw_end not in (None, "") else save.global_day
                    except (TypeError, ValueError):
                        event_end = save.global_day
                    reached_end = min(event_end, save.global_day)
                    interval_days = max(1, save.days_per_year * repeat_interval)
                    occurrence_days = list(range(event_start, reached_end + 1, interval_days))
                else:
                    interval_days = 0
                    occurrence_days = [event_start]
                sim_spec = dict(spec)
                if step:
                    result_rules = str(step.get("result_rules") or "")
                    bad_results = str(step.get("bad_results") or "")
                    sim_spec.update({
                        "die": str(step.get("die") or sim_spec["die"]), "bad_results": bad_results,
                        "result_rules": result_rules,
                        "failure_outcome": _mapped_roll_outcome(int(re.findall(r"\d+", bad_results)[0]), result_rules) if bad_results and result_rules else "",
                        "failure_is_lethal": _lethal_outcome(result_rules),
                    })
                sex = str((sim.data or {}).get("sex") or "").casefold()
                sex_key = "female" if re.search(r"\b(?:female|woman|girl)\b", sex) else "male" if re.search(r"\b(?:male|man|boy)\b", sex) else ""
                die_by_sex = event_data.get("die_by_sex") or {}
                rules_by_sex = event_data.get("result_rules_by_sex") or {}
                if root_position == 0 and sex_key and (sex_key in die_by_sex or sex_key in rules_by_sex):
                    result_rules = str(rules_by_sex.get(sex_key) or sim_spec["result_rules"] or "")
                    bad_results = _result_numbers(result_rules) if ":" in result_rules else result_rules
                    sim_spec.update({
                        "die": str(die_by_sex.get(sex_key) or sim_spec["die"]), "bad_results": bad_results,
                        "result_rules": result_rules,
                        "failure_outcome": _mapped_roll_outcome(int(re.findall(r"\d+", bad_results)[0]), result_rules) if bad_results and result_rules else "",
                        "failure_is_lethal": _lethal_outcome(result_rules),
                    })
                context_label = str(step.get("context") or "").split(";")[0].strip()
                roll_type = f"Event — {event.label}" + (f" — {context_label[:80]}" if root_position else "")
                for occurrence_number, due in enumerate(occurrence_days, start=1):
                    if not _event_applies(event, sim, due, rule_data, household, save, fallback_location, pregnancies):
                        continue
                    if step and not _source_roll_step_applies(step, sim, household, save, fallback_location):
                        continue
                    eligible_stages = {str(value).strip().casefold() for value in step.get("eligible_life_stages") or []}
                    if eligible_stages:
                        birth = (sim.data or {}).get("birth_global_day", sim.global_day)
                        if birth is None:
                            continue
                        age = due - int(birth); inferred_stage = DEFAULT_STAGES[0][0].casefold()
                        for stage_name, minimum, _die, _bad in DEFAULT_STAGES:
                            if age >= minimum: inferred_stage = stage_name.casefold()
                        if inferred_stage not in eligible_stages and not (inferred_stage in {"being born", "newborn", "infant"} and "baby" in eligible_stages):
                            continue
                    base_source = f"event:{event.id}:{sim.id}" if root_position == 0 else f"event:{event.id}:{sim.id}:step:{step_index}"
                    source = f"{base_source}:occurrence:{due}" if repeat_interval else base_source
                    equivalent_source_exists = False
                    for event_id in equivalent_event_ids:
                        equivalent_base = f"event:{event_id}:{sim.id}" if root_position == 0 else f"event:{event_id}:{sim.id}:step:{step_index}"
                        candidates = {f"{equivalent_base}:occurrence:{due}"} if repeat_interval else {equivalent_base}
                        # Pre-4.4.8 builds created the first occurrence without
                        # an occurrence suffix. It represents year one and must
                        # protect both completed history and pending work.
                        if repeat_interval and due == event_start:
                            candidates.add(equivalent_base)
                        if candidates & existing_event_sources:
                            equivalent_source_exists = True
                            break
                    if equivalent_source_exists:
                        continue
                    historical_year = save.start_year + ((due - 1) // max(1, save.days_per_year))
                    roll = Record(save_id=save.id, kind="roll", label=f"{event.label} — {sim.label}", global_day=due, data={
                        "event_id": event.id, "source_id": event.id, "sim_id": sim.id, "sim_name": sim.label,
                        "roll_type": roll_type, "die": sim_spec["die"], "bad_results": sim_spec["bad_results"],
                        "result_rules": sim_spec["result_rules"], "failure_outcome": sim_spec["failure_outcome"],
                        "failure_is_lethal": sim_spec["failure_is_lethal"], "nonlethal": not sim_spec["failure_is_lethal"],
                        "event_rule_id": sim_spec["event_rule_id"], "source": source, "due_global_day": due, "completed": False,
                        "source_roll_plan_index": step_index if step else None,
                        "source_roll_plan_root": bool(step),
                        "event_repeat_interval_years": repeat_interval or None,
                        "event_occurrence_number": occurrence_number if repeat_interval else None,
                        "event_occurrence_global_day": due if repeat_interval else None,
                        "event_occurrence_year": historical_year if repeat_interval else None,
                        "core_ruleset_id":core_rulesets.CLASSIC_2023 if classic_war else None,
                    })
                    session.add(roll); session.flush(); journal(session, roll, "upsert", 0)
                    existing_event_sources.add(source); created += 1
    return created


def schedule_campaign_rolls(session: Session, save: ChronicleSave, sims: list[Record] | None = None) -> int:
    """Generate one war/campaign obligation for each living eligible Sim."""
    sims = sims if sims is not None else list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
    )))
    campaigns = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "campaign", Record.deleted.is_(False),
    )))
    households = {item.id:item for item in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "household", Record.deleted.is_(False),
    ))}
    existing = set(session.scalars(select(Record.data["source"].as_string()).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["source"].as_string().like("campaign:%"),
    )))
    created = 0
    for campaign in campaigns:
        data = campaign.data or {}
        try: due = int(data.get("start_global_day", campaign.global_day))
        except (TypeError, ValueError): continue
        if not (1 <= due <= save.global_day and bool(data.get("active", True)) and bool(data.get("roll_required"))): continue
        spec = event_roll_configuration(campaign)
        campaign_words=f"{campaign.label} {data.get('scope','')} {data.get('notes','')}".casefold()
        classic_war=core_rulesets.selected_core(save)==core_rulesets.CLASSIC_2023 and any(word in campaign_words for word in ("war","battle","military","draft"))
        if classic_war:
            spec={**spec,"die":"d6","bad_results":"1,3,5","result_rules":"1,3,5: Dies in the war; 2,4,6: Returns home","failure_outcome":"Dies in the war","failure_is_lethal":True}
        allowed_sexes = {value.strip().casefold() for value in str(data.get("eligible_sexes") or "All").split(",") if value.strip()}
        allowed_classes = {value.strip().casefold() for value in str(data.get("eligible_classes") or "All").split(",") if value.strip()}
        minimum = int(data.get("min_age_days") or 0); maximum = int(data.get("max_age_days") or 100000)
        for sim in sims:
            sim_data = sim.data or {}; birth = sim_data.get("birth_global_day", sim.global_day); death = sim_data.get("death_global_day")
            source = f"campaign:{campaign.id}:{sim.id}"
            if source in existing or birth is None or bool(sim_data.get("game_was_dead")) or (death is not None and int(death) <= due): continue
            age = due - int(birth)
            if age < minimum or age > maximum: continue
            sex = str(sim_data.get("sex") or "").casefold()
            if allowed_sexes and "all" not in allowed_sexes and sex not in allowed_sexes: continue
            household = households.get(str(sim_data.get("current_household_id") or "")); household_data = household.data if household else {}
            social = str(sim_data.get("social_class") or household_data.get("social_class") or "").casefold()
            if allowed_classes and "all" not in allowed_classes and social not in allowed_classes: continue
            target = data.get("location") or "All"
            places = " ".join(str(value or "") for value in (sim_data.get("country"), sim_data.get("location"), sim_data.get("last_game_world"), household_data.get("country"), household_data.get("location"), (save.settings or {}).get("challenge_location")))
            if _normalized_location(target) not in {"", "all", "global", "worldwide"} and not _event_location_matches(target, places): continue
            payload = {
                "campaign_id":campaign.id,"source_id":campaign.id,"sim_id":sim.id,"sim_name":sim.label,
                "roll_type":f"War — {campaign.label}","die":spec["die"],"bad_results":spec["bad_results"],
                "result_rules":spec["result_rules"],"failure_outcome":spec["failure_outcome"],
                "failure_is_lethal":spec["failure_is_lethal"],"nonlethal":not spec["failure_is_lethal"],
                "source":source,"due_global_day":due,"completed":False,"campaign_role":data.get("role") or "Conscript",
                "core_ruleset_id":core_rulesets.CLASSIC_2023 if classic_war else None,
            }
            roll = Record(save_id=save.id,kind="roll",label=f"{campaign.label} — {sim.label}",global_day=due,data=payload)
            session.add(roll);session.flush();journal(session,roll,"upsert",0);existing.add(source);created += 1
    return created


def _schedule_event_followup(session: Session, save: ChronicleSave, origin: Record, actual: int) -> int:
    """Schedule the configured event/war follow-up only when its trigger matches."""
    data = origin.data or {}; source_id = data.get("campaign_id") or data.get("event_id")
    source_record = session.get(Record, source_id) if source_id else None
    if not source_record or source_record.deleted: return 0
    config = source_record.data or {}
    source_plan = [dict(step) for step in (config.get("source_roll_plan") or []) if isinstance(step, dict)]
    plan_index = data.get("source_roll_plan_index")
    if source_plan and plan_index is not None:
        try: plan_index = int(plan_index)
        except (TypeError, ValueError): plan_index = None
        children = [
            step for step in source_plan
            if plan_index in [int(value) for value in (step.get("parent_indices") or [])]
            or (not step.get("parent_indices") and step.get("parent_index") == plan_index)
        ]
        created = 0
        for child in children:
            trigger = str(child.get("trigger_results") or next(
                (step.get("bad_results") for step in source_plan if int(step.get("index") or 0) == plan_index),
                data.get("bad_results") or "",
            ))
            if trigger and not failed(actual, trigger):
                continue
            child_index = int(child.get("index") or 0)
            source = f"conditional-followup:{origin.id}:step:{child_index}"
            if session.scalar(select(Record.id).where(
                Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
                Record.data["source"].as_string() == source,
            ).limit(1)):
                continue
            sim_id = str(data.get("sim_id") or ""); sim = session.get(Record, sim_id) if sim_id else None
            if not sim or sim.deleted:
                continue
            result_rules = str(child.get("result_rules") or "")
            bad_results = str(child.get("bad_results") or "")
            delay = max(0, int(child.get("delay_days") or 0))
            delay += max(0, int(child.get("delay_years") or 0)) * max(1, int(save.days_per_year or 1))
            due = save.global_day + delay
            label = str(child.get("label") or child.get("context") or f"{source_record.label} follow-up")[:160]
            lethal = bool(child.get("failure_is_lethal")) or _lethal_outcome(result_rules)
            followup = Record(save_id=save.id, kind="roll", label=f"{sim.label} — {label}", global_day=due, data={
                "sim_id":sim.id, "sim_name":sim.label, "event_id":data.get("event_id"),
                "origin_roll_id":origin.id, "roll_type":label, "die":str(child.get("die") or "d20"),
                "bad_results":bad_results, "result_rules":result_rules,
                "failure_is_lethal":lethal, "nonlethal":not lethal,
                "source":source, "due_global_day":due, "completed":False, "automatic_followup":True,
                "source_roll_plan_index":child_index,
            })
            session.add(followup); session.flush(); journal(session, followup, "upsert", 0); created += 1
        if created or children:
            origin.data = {**origin.data, "event_followup_processed":True}
            return created
        if data.get("automatic_followup"):
            return 0
    if not bool(config.get("followup_enabled")): return 0
    branches = config.get("followup_branches") if isinstance(config.get("followup_branches"), dict) else {}
    branch = next(
        (dict(payload) for trigger, payload in branches.items()
         if isinstance(payload, dict) and failed(actual, str(trigger))),
        None,
    )
    if branches and branch is None:
        return 0
    if not branches:
        trigger = str(config.get("followup_trigger_results") or data.get("bad_results") or "")
        if trigger and not failed(actual, trigger): return 0
    branch = branch or {}
    sim_id = str(data.get("sim_id") or ""); sim = session.get(Record, sim_id) if sim_id else None
    if not sim or sim.deleted: return 0
    source = f"conditional-followup:{origin.id}"
    exists = session.scalar(select(Record.id).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["source"].as_string() == source,
    ).limit(1))
    if exists: return 0
    delay = max(0, int(branch.get("delay_days", config.get("followup_delay_days")) or 0))
    delay += max(0, int(branch.get("delay_years", config.get("followup_delay_years")) or 0)) * max(1, int(save.days_per_year or 1))
    due = save.global_day + delay
    label = str(branch.get("label") or config.get("followup_label") or f"{source_record.label} follow-up")
    result_rules = str(
        branch.get("result_rules") or branch.get("bad_results")
        or config.get("followup_result_rules") or config.get("followup_bad_results") or ""
    )
    bad_results = _result_numbers(result_rules) if ":" in result_rules else result_rules
    lethal = bool(branch.get("failure_is_lethal", config.get("followup_failure_is_lethal"))) or _lethal_outcome(result_rules)
    followup = Record(save_id=save.id,kind="roll",label=f"{sim.label} — {label}",global_day=due,data={
        "sim_id":sim.id,"sim_name":sim.label,"event_id":data.get("event_id"),"campaign_id":data.get("campaign_id"),
        "origin_roll_id":origin.id,"roll_type":label,"die":branch.get("die") or config.get("followup_die") or "d20",
        "bad_results":bad_results,"result_rules":result_rules,"failure_is_lethal":lethal,"nonlethal":not lethal,
        "source":source,"due_global_day":due,"completed":False,"automatic_followup":True,
        "followup_branch_result":actual if branches else None,
    })
    session.add(followup);session.flush();journal(session,followup,"upsert",0)
    origin.data={**origin.data,"event_followup_roll_id":followup.id,"event_followup_processed":True}
    return 1


def _record_campaign_service(session: Session, save: ChronicleSave, roll: Record) -> bool:
    data = roll.data or {}; campaign_id = str(data.get("campaign_id") or ""); sim_id = str(data.get("sim_id") or "")
    if not campaign_id or not sim_id: return False
    campaign=session.get(Record,campaign_id);sim=session.get(Record,sim_id)
    if not campaign or not sim: return False
    outcome=str(data.get("outcome") or "Completed");folded=outcome.casefold()
    status="Killed" if _lethal_outcome(outcome) else "Injured" if any(word in folded for word in ("injur","wound","maim")) else "Missing" if any(word in folded for word in ("missing","captur","imprison")) else "Returned" if any(word in folded for word in ("return","surviv","complete","passed")) else "Active"
    existing=session.scalar(select(Record).where(
        Record.save_id==save.id,Record.kind=="service",Record.deleted.is_(False),
        Record.data["campaign_id"].as_string()==campaign_id,Record.data["sim_id"].as_string()==sim_id,
    ).limit(1))
    payload={"campaign_id":campaign_id,"sim_id":sim_id,"sim_name":sim.label,"role":data.get("campaign_role") or "Conscript","status":status,"enlisted_global_day":int((campaign.data or {}).get("start_global_day",campaign.global_day) or save.global_day),"return_global_day":None if status=="Active" else save.global_day,"outcome":outcome,"source_roll_id":roll.id}
    if existing:
        base=existing.version;existing.label=f"{sim.label} — {campaign.label}";existing.global_day=payload["enlisted_global_day"];existing.data={**(existing.data or {}),**payload};existing.version+=1;journal(session,existing,"upsert",base)
    else:
        existing=Record(save_id=save.id,kind="service",label=f"{sim.label} — {campaign.label}",global_day=payload["enlisted_global_day"],data=payload);session.add(existing);session.flush();journal(session,existing,"upsert",0)
    return True


def apply_due_migrations(session: Session, save: ChronicleSave) -> int:
    """Advance current countries from the dated migration ledger exactly once."""
    moves=list(session.scalars(select(Record).where(
        Record.save_id==save.id,Record.kind=="migration",Record.deleted.is_(False),
    )))
    if not moves: return 0
    sim_ids={str((item.data or {}).get("sim_id") or "") for item in moves};sim_ids.discard("")
    if not sim_ids: return 0
    sims=list(session.scalars(select(Record).where(
        Record.save_id==save.id,Record.kind=="sim",Record.id.in_(sim_ids),Record.deleted.is_(False),
    )))
    changed=0
    for sim in sims:
        data=dict(sim.data or {});country=advanced.location_at(sim,save.global_day,moves);birth=advanced.birth_country(sim)
        updates={}
        if not data.get("birth_country") and birth!="Unknown": updates["birth_country"]=birth
        if country!="Unknown" and (str(data.get("country") or "")!=country or str(data.get("current_country") or "")!=country):
            updates.update({"country":country,"current_country":country})
        if not updates: continue
        base=sim.version;sim.data={**data,**updates};sim.version+=1;journal(session,sim,"upsert",base);changed+=1
    return changed


def retire_inactive_core_rolls(session: Session, save: ChronicleSave) -> int:
    """Retire pending obligations from a core ruleset that is no longer selected."""
    active = core_rulesets.selected_core(save)
    changed = 0
    rule_packs = {
        item.id:str((item.data or {}).get("core_ruleset_id") or "")
        for item in session.scalars(select(Record).where(
            Record.save_id==save.id,Record.deleted.is_(False),
            Record.kind.in_(("roll_rule","planner_rule")),
        ))
    }
    rolls = session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
    ))
    for roll in rolls:
        data = dict(roll.data or {})
        pack = str(data.get("core_ruleset_id") or "")
        if not pack:
            source=str(data.get("source") or "");parts=source.split(":")
            inferred_rule_id=parts[-1] if parts and parts[0] in {"aging","maternal"} else str(data.get("planner_rule_id") or "")
            pack=rule_packs.get(inferred_rule_id,"")
        if not pack or pack == active or data.get("completed"):
            continue
        base = roll.version; roll.deleted = True
        roll.data = {**data, "retired_reason":"Core ruleset changed", "retired_global_day":save.global_day}
        roll.version += 1; journal(session, roll, "delete", base); changed += 1
    return changed


def _schedule_sim_lifecycle_rolls(session: Session, save: ChronicleSave, sim: Record,
                                  rules: list[Record]) -> int:
    """Create only one Sim's aging obligations, without unrelated automation."""
    birth = sim.data.get("birth_global_day", sim.global_day)
    death = sim.data.get("death_global_day")
    if (bool((sim.data or {}).get("game_was_dead"))
            or (death is not None and int(death) <= save.global_day)
            or "Servo" in occult_rules.sim_occult_types(sim.data)
            or birth is None):
        return 0
    created = 0
    for rule in rules:
        if not rule.data.get("active", True):
            continue
        configured_age = rule.data.get("age_days")
        if configured_age in (None, ""):
            configured_age = AGING_STAGE_OFFSETS.get(rule.label.strip().casefold())
        if configured_age is None:
            continue
        if int(configured_age) == 0 and sim.data.get("newborn_rolls_required") is False:
            continue
        due = int(birth) + int(configured_age)
        due_year = save.start_year + (due - 1) // max(1, save.days_per_year)
        if not int(rule.data.get("start_year", -9999)) <= due_year <= int(rule.data.get("end_year", 9999)):
            continue
        if due < 1 or (death is not None and due >= int(death)):
            continue
        source = f"aging:{sim.id}:{rule.id}"
        exists = session.scalar(select(Record.id).where(
            Record.save_id == save.id,
            Record.kind == "roll",
            Record.deleted.is_(False),
            (Record.data["source"].as_string() == source) |
            (
                (Record.data["sim_id"].as_string() == sim.id) &
                (Record.data["roll_type"].as_string() == rule.label) &
                (Record.global_day == due)
            ),
        ).limit(1))
        if exists:
            continue
        later_ages = sorted(
            int(item.data.get("age_days")) for item in rules
            if item.id != rule.id and item.data.get("age_days") not in (None, "")
            and int(item.data.get("age_days")) > int(configured_age)
            and str(item.data.get("core_ruleset_id") or "") == str(rule.data.get("core_ruleset_id") or "")
            and int(item.data.get("start_year", -9999)) <= due_year <= int(item.data.get("end_year", 9999))
        )
        payload={
            "sim_id": sim.id, "roll_type": rule.label, "die": rule.data.get("die"),
            "bad_results": rule.data.get("bad_results"), "source": source,
            "due_global_day": due, "completed": False,
            "core_ruleset_id":rule.data.get("core_ruleset_id"),
            "core_source_rule_id":rule.data.get("source_rule_id"),
            "death_age_rng":bool(rule.data.get("death_age_rng")),
        }
        if later_ages:
            payload["death_window_end"]=int(birth)+later_ages[0]-1
        roll = Record(save_id=save.id, kind="roll", label=f"{sim.label} — {rule.label}", global_day=due, data=payload)
        session.add(roll); session.flush(); journal(session, roll, "upsert", 0); created += 1
    return created


_HOGWARTS_FOUNDED_YEAR = 990
_HOGWARTS_HOUSES_BY_D4 = {1: "Hufflepuff", 2: "Ravenclaw", 3: "Slytherin", 4: "Gryffindor"}


def _is_hogwarts_sorting_roll(roll: Record) -> bool:
    data = roll.data or {}
    return bool(data.get("hp_hogwarts_sorting")) or str(data.get("source_rule_key") or "").casefold() == "hp_13"


def _schedule_hogwarts_sorting_rolls(session: Session, save: ChronicleSave,
                                     sims: list[Record]) -> int:
    """Schedule one first-year Hogwarts sorting roll for each eligible Spellcaster.

    Hogwarts is founded in 990. House assignment remains an optional Harry
    Potter module, but once it is enabled the tracker owns the age-11
    obligation and carries the resulting House onto the Sim profile.
    """
    selected = set((save.settings or {}).get("selected_rule_packs") or [])
    if "harry_potter_decades" not in selected:
        return 0
    rule = session.scalar(select(Record).where(
        Record.save_id == save.id, Record.kind == "addon_rule", Record.deleted.is_(False),
        Record.data["rule_pack_id"].as_string() == "harry_potter_decades",
        Record.data["code"].as_string() == "HP-13",
    ).limit(1))
    if not rule or not bool((rule.data or {}).get("active")):
        return 0

    existing = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        (Record.data["hp_hogwarts_sorting"].as_boolean().is_(True))
        | (Record.data["source_rule_key"].as_string() == "hp_13"),
    )))
    existing_by_sim: dict[str, list[Record]] = defaultdict(list)
    for item in existing:
        sim_id = str((item.data or {}).get("sim_id") or "")
        if sim_id:
            existing_by_sim[sim_id].append(item)

    days_per_year = max(1, int(save.days_per_year))
    created = repaired = 0
    for sim in sims:
        data = sim.data or {}
        birth = data.get("birth_global_day", sim.global_day)
        try:
            due = int(birth) + 11 * days_per_year
        except (TypeError, ValueError):
            continue
        due_year = save.start_year + (due - 1) // days_per_year
        ability = str(data.get("hp_magical_ability") or "").casefold()
        eligible = (
            due >= 1
            and due_year >= _HOGWARTS_FOUNDED_YEAR
            and "Spellcaster" in occult_rules.sim_occult_types(data)
            and not bool(data.get("hp_hidden_squib"))
            and ability not in {"squib", "muggle"}
            and not str(data.get("hp_hogwarts_house") or "").strip()
        )
        death = data.get("death_global_day")
        try:
            eligible = eligible and (death in (None, "") or int(death) > due)
        except (TypeError, ValueError):
            pass

        matches = existing_by_sim.get(sim.id, [])
        completed = next((item for item in matches if bool((item.data or {}).get("completed"))), None)
        automatic_pending = next((item for item in matches if not bool((item.data or {}).get("completed"))
                                  and bool((item.data or {}).get("hp_hogwarts_sorting"))), None)
        if completed:
            continue
        if not eligible:
            if automatic_pending:
                base = automatic_pending.version
                automatic_pending.deleted = True
                automatic_pending.data = {
                    **(automatic_pending.data or {}),
                    "retired_reason": "No longer eligible for Hogwarts sorting",
                    "retired_global_day": save.global_day,
                }
                automatic_pending.version += 1
                journal(session, automatic_pending, "delete", base)
                repaired += 1
            continue
        if matches:
            if automatic_pending and int(automatic_pending.global_day or due) != due:
                base = automatic_pending.version
                automatic_pending.global_day = due
                automatic_pending.data = {**(automatic_pending.data or {}), "due_global_day": due}
                automatic_pending.version += 1
                journal(session, automatic_pending, "upsert", base)
                repaired += 1
            continue

        source = f"harry-potter:hogwarts-sorting:{sim.id}"
        payload = {
            "sim_id": sim.id, "sim_name": sim.label, "source_id": rule.id,
            "source_rule_id": rule.id, "source_rule_kind": "addon_rule",
            "source_rule_key": "hp_13", "rule_generated": True,
            "rule_family": "Harry Potter Decades", "roll_type": "Hogwarts Sorting",
            "die": str((rule.data or {}).get("die") or "d4"), "bad_results": "",
            "result_rules": str((rule.data or {}).get("result_rules") or "1: Hufflepuff; 2: Ravenclaw; 3: Slytherin; 4: Gryffindor"),
            "nonlethal": True, "failure_is_lethal": False, "source": source,
            "due_global_day": due, "completed": False, "hp_hogwarts_sorting": True,
            "notes": "Automatically scheduled at age 11 for a Spellcaster after Hogwarts was founded.",
        }
        roll = Record(save_id=save.id, kind="roll", label=f"{sim.label} — Hogwarts Sorting", global_day=due, data=payload)
        session.add(roll); session.flush(); journal(session, roll, "upsert", 0)
        created += 1
    if repaired:
        save.revision += repaired
    return created


def _hp_magical(sim: Record | None) -> bool:
    """Use the most reliable magical identity available without changing game data."""
    if not sim or sim.deleted:
        return False
    data = sim.data or {}
    ability = str(data.get("hp_magical_ability") or "").casefold()
    if ability in {"witch", "wizard", "magical", "muggle-born"}:
        return True
    return "Spellcaster" in occult_rules.sim_occult_types(data)


def _hp_witch_or_wizard(data: dict) -> str:
    sex = str(data.get("sex") or data.get("game_sex") or "").casefold()
    return "Wizard" if "male" in sex or sex in {"man", "boy"} else "Witch"


def _hp_country(data: dict) -> str:
    return " ".join(str(data.get(key) or "") for key in (
        "birth_country", "birthplace", "country", "location", "last_game_world",
    )).casefold()


def _hp_rule_roll(session: Session, save: ChronicleSave, rule: Record, sim: Record, due: int,
                  source: str, *, die: str, result_rules: str, trigger_results: str = "",
                  label: str = "", extra: dict | None = None) -> bool:
    """Create one idempotent automatic Harry Potter obligation."""
    if due < 1 or due > save.global_day:
        return False
    exists = session.scalar(select(Record.id).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["source"].as_string() == source,
    ).limit(1))
    if exists:
        return False
    rule_data = rule.data or {}
    code = str(rule_data.get("code") or "").upper()
    payload = {
        "sim_id": sim.id, "sim_name": sim.label, "source_id": rule.id,
        "source_rule_id": rule.id, "source_rule_kind": "addon_rule",
        "source_rule_key": code.casefold().replace("-", "_"), "rule_generated": True,
        "rule_family": "Harry Potter Decades", "hp_auto_roll": True, "hp_rule_code": code,
        "roll_type": str(rule_data.get("name") or rule.label), "die": die,
        "bad_results": trigger_results, "trigger_results": trigger_results,
        "result_rules": result_rules, "nonlethal": True, "failure_is_lethal": False,
        "source": source, "due_global_day": due, "completed": False,
        "notes": str(rule_data.get("rule_text") or ""),
    }
    if extra:
        payload.update(extra)
    roll = Record(
        save_id=save.id, kind="roll", label=label or f"{sim.label} — {payload['roll_type']}",
        global_day=due, data=payload,
    )
    session.add(roll); session.flush(); journal(session, roll, "upsert", 0)
    return True


def _schedule_harry_potter_rolls(session: Session, save: ChronicleSave,
                                 sims: list[Record]) -> int:
    """Schedule every enabled HP rule whose trigger is already known to the tracker.

    Scenario-only canon entries still wait for a player to nominate a participant;
    this avoids silently putting a non-participant into a tournament, battle, or
    dangerous-object search.  All birth, age, household, pregnancy and recorded
    exposure triggers are automatic and idempotent.
    """
    from . import harry_potter_rules

    if harry_potter_rules.PACK_ID not in set((save.settings or {}).get("selected_rule_packs") or []):
        return 0
    rules = {
        str((item.data or {}).get("code") or "").upper(): item
        for item in session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "addon_rule", Record.deleted.is_(False),
            Record.data["rule_pack_id"].as_string() == harry_potter_rules.PACK_ID,
        ))
        if bool((item.data or {}).get("active"))
    }
    if not rules:
        return 0
    days = max(1, int(save.days_per_year))
    current_year = save.start_year + (save.global_day - 1) // days
    year_start = max(1, (current_year - save.start_year) * days + 1)
    by_id = {sim.id: sim for sim in sims}
    households = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "household", Record.deleted.is_(False),
    )))
    household_by_id = {item.id: item for item in households}
    created = 0

    def living(sim: Record) -> bool:
        data = sim.data or {}; death = data.get("death_global_day")
        try:
            return not bool(data.get("game_was_dead") or data.get("death_confirmed")) and (death in (None, "") or int(death) > save.global_day)
        except (TypeError, ValueError):
            return not bool(data.get("game_was_dead") or data.get("death_confirmed"))

    # HP-05: every in-challenge birth, with the applicable branch captured in
    # the roll so completing it never asks the player to reconstruct parentage.
    rule = rules.get("HP-05")
    if rule:
        for sim in sims:
            data = sim.data or {}; birth = data.get("birth_global_day", sim.global_day)
            try:
                birth = int(birth)
            except (TypeError, ValueError):
                continue
            if not (1 <= birth <= save.global_day) or not living(sim):
                continue
            parents = [by_id.get(str(data.get(key) or "")) for key in ("mother_id", "father_id")]
            magical_parent = any(_hp_magical(parent) for parent in parents)
            branch = "magical-parent" if magical_parent else "muggle-parent"
            rules_text = (
                "1: Squib; 2-20: Witch or Wizard" if magical_parent
                else "1: Muggle-Born Witch or Wizard; 2-20: Muggle"
            )
            created += int(_hp_rule_roll(
                session, save, rule, sim, birth, f"harry-potter:HP-05:{sim.id}",
                die="d20", result_rules=rules_text, trigger_results="1",
                extra={"hp_birth_branch": branch, "hp_parent_ids": [parent.id for parent in parents if parent]},
            ))

    # HP-06: a recorded squib becomes publicly known at age seven.  It is a
    # confirmation prompt (D1) rather than an invented random chance.
    rule = rules.get("HP-06")
    if rule:
        for sim in sims:
            data = sim.data or {}; birth = data.get("birth_global_day", sim.global_day)
            try:
                due = int(birth) + 7 * days
            except (TypeError, ValueError):
                continue
            if (str(data.get("hp_magical_ability") or "").casefold() != "squib"
                    or not bool(data.get("hp_hidden_squib")) or not living(sim)):
                continue
            created += int(_hp_rule_roll(
                session, save, rule, sim, due, f"harry-potter:HP-06:{sim.id}",
                die="d1", result_rules="1: Squib status is discovered and recorded publicly",
                extra={"hp_squib_discovery": True},
            ))

    pregnancies = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "pregnancy", Record.deleted.is_(False),
    )))
    # HP-11: only an already-recorded triplet-or-larger magical pregnancy gets
    # an upgrade roll; it never overwrites an actual game pregnancy silently.
    rule = rules.get("HP-11")
    if rule:
        for pregnancy in pregnancies:
            pdata = pregnancy.data or {}; mother = by_id.get(str(pdata.get("mother_id") or ""))
            expected = pdata.get("babies_delivered", pdata.get("babies_expected"))
            try:
                expected = int(expected)
            except (TypeError, ValueError):
                continue
            if not mother or not living(mother) or not _hp_magical(mother) or expected < 3 or expected >= 6:
                continue
            due = int(pdata.get("actual_delivery_global_day") or pdata.get("due_global_day") or pregnancy.global_day or save.global_day)
            next_count = expected + 1
            created += int(_hp_rule_roll(
                session, save, rule, mother, due, f"harry-potter:HP-11:{pregnancy.id}:{expected}",
                die="d20", result_rules=f"1: Pregnancy becomes {next_count} babies; 2-20: Remains {expected} babies",
                trigger_results="1", extra={"hp_pregnancy_id": pregnancy.id, "hp_multiples_current": expected, "hp_multiples_next": next_count},
                label=f"{mother.label} — Magical higher-order multiples",
            ))

    # HP-14: a yearly childhood check for magical children between three and
    # ten.  We schedule each reached anniversary once, without pre-challenge
    # backfill or dead-Sim work.
    rule = rules.get("HP-14")
    if rule:
        for sim in sims:
            data = sim.data or {}; birth = data.get("birth_global_day", sim.global_day)
            try:
                birth = int(birth)
            except (TypeError, ValueError):
                continue
            if not living(sim) or not _hp_magical(sim) or str(data.get("hp_magical_ability") or "").casefold() == "squib":
                continue
            for age in range(3, 11):
                due = birth + age * days
                if due > save.global_day:
                    break
                created += int(_hp_rule_roll(
                    session, save, rule, sim, due, f"harry-potter:HP-14:{sim.id}:{age}",
                    die="d6", result_rules="1: Noticeable accidental magic; 2-6: No major accidental magic",
                    trigger_results="1", extra={"hp_age_years": age, "hp_accidental_magic": True},
                ))

    # HP-15: the response table begins once a Muggle-Born magical child is
    # known to be in the United States.  A configurable profile value can later
    # refine the family response; the default is the source's supportive case.
    rule = rules.get("HP-15")
    if rule:
        for sim in sims:
            data = sim.data or {}
            if (not living(sim) or not _hp_magical(sim)
                    or str(data.get("hp_blood_status") or "").casefold() != "muggle-born"
                    or not any(value in _hp_country(data) for value in ("united states", "america", "usa"))):
                continue
            due = max(1, int(data.get("birth_global_day", sim.global_day) or sim.global_day))
            response = str(data.get("hp_obscurial_home_response") or "supportive").casefold()
            bad = {"supportive": "1", "fearful": "1-2", "anti-magic": "1-3", "violent suppression": "1-4"}.get(response, "1")
            created += int(_hp_rule_roll(
                session, save, rule, sim, due, f"harry-potter:HP-15:{sim.id}", die="d6",
                result_rules=f"{bad}: Magic is dangerously suppressed; remaining results: Safe magical support",
                trigger_results=bad, extra={"hp_obscurial_response": response, "hp_obscurial_risk": True},
            ))

    # HP-19: one per exposed magical household after the Statute era begins.
    rule = rules.get("HP-19")
    if rule and current_year >= 1692:
        members: dict[str, list[Record]] = defaultdict(list)
        for sim in sims:
            if living(sim) and _hp_magical(sim):
                members[str((sim.data or {}).get("current_household_id") or f"unhoused-{sim.id}")].append(sim)
        for household_id, group in members.items():
            household = household_by_id.get(household_id)
            hdata = household.data if household else {}
            if not bool(hdata.get("hp_repeated_public_exposure")):
                continue
            representative = sorted(group, key=lambda item: item.label.casefold())[0]
            created += int(_hp_rule_roll(
                session, save, rule, representative, save.global_day,
                f"harry-potter:HP-19:{household_id}:{current_year}", die="d6",
                result_rules="1-2: Ministry intervention; 3-6: Secrecy preserved",
                trigger_results="1-2", label=f"International Statute of Secrecy — {hdata.get('name') or representative.label}",
                extra={"hp_household_id": household_id, "eligible_magical_sim_ids": [item.id for item in group]},
            ))

    # Enabled HP event tables with deterministic triggers are now automatic.
    magical_households: dict[str, list[Record]] = defaultdict(list)
    for sim in sims:
        if living(sim) and _hp_magical(sim):
            magical_households[str((sim.data or {}).get("current_household_id") or f"unhoused-{sim.id}")].append(sim)
    for code, die, text, years in (
        ("HP-T01", "d20", "1: Accidental magic witnessed; 2: Magical illness; 3: Dangerous object; 4: Creature incident; 5: Ministry investigation; 6: Blood-status dispute; 7: School event; 8: Quidditch; 9: Unusual birth; 10: Family secret; 11: Forbidden experiment; 12: Muggle relative discovers magic; 13: Business; 14: Visitor; 15: Heir dispute; 16: Dark Wizard; 17: Helpful discovery; 18: Friendship, courtship, or betrothal; 19-20: No major event", None),
        ("HP-T04", "d12", "1: Death; 2: Imprisonment or capture; 3: Disappearance; 4: Property attack; 5: Ordered collaboration; 6: Resistance; 7: Hiding; 8: Betrayal; 9: Protects target; 10: Intelligence; 11: Avoids major harm; 12: Gains influence", {(1970, 1981), (1995, 1998)}),
    ):
        rule = rules.get(code)
        if not rule or (years and not any(start <= current_year <= end for start, end in years)):
            continue
        for household_id, group in magical_households.items():
            representative = sorted(group, key=lambda item: item.label.casefold())[0]
            household = household_by_id.get(household_id)
            created += int(_hp_rule_roll(
                session, save, rule, representative, year_start, f"harry-potter:{code}:{household_id}:{current_year}",
                die=die, result_rules=text, label=f"{(rule.data or {}).get('name') or rule.label} — {(household.data or {}).get('name') if household else representative.label}",
                extra={"hp_household_id": household_id, "eligible_magical_sim_ids": [item.id for item in group], "hp_annual_year": current_year},
            ))

    rule = rules.get("HP-T05")
    if rule:
        for sim in sims:
            data = sim.data or {}; birth = data.get("birth_global_day", sim.global_day)
            try:
                age = (save.global_day - int(birth)) // days
            except (TypeError, ValueError):
                continue
            if not (living(sim) and _hp_magical(sim) and str(data.get("hp_magical_school") or "").casefold() == "hogwarts" and 11 <= age <= 17):
                continue
            created += int(_hp_rule_roll(
                session, save, rule, sim, year_start, f"harry-potter:HP-T05:{sim.id}:{current_year}", die="d12",
                result_rules="1: Serious accident; 2: Discipline; 3: House rivalry; 4: Forbidden Forest; 5: Secret passage or object; 6: Quidditch; 7: Academic distinction; 8: Friendship or romance; 9: Professor conflict; 10: Creature encounter; 11: Family interruption; 12: Uneventful", extra={"hp_annual_year": current_year},
            ))
    return created


def _hp_roll_outcome(roll: Record, actual: int) -> str | None:
    """Return the concrete outcome text for automatic HP modules."""
    data = roll.data or {}; code = str(data.get("hp_rule_code") or "").upper()
    if code == "HP-05":
        if data.get("hp_birth_branch") == "magical-parent":
            return "Squib" if actual == 1 else "Witch or Wizard"
        return "Muggle-Born Witch or Wizard" if actual == 1 else "Muggle"
    if code == "HP-06":
        return "Squib status is discovered and recorded publicly"
    if code == "HP-11":
        return f"Pregnancy becomes {data.get('hp_multiples_next')} babies" if actual == 1 else f"Remains {data.get('hp_multiples_current')} babies"
    if code == "HP-14":
        return "Noticeable accidental magic" if actual == 1 else "No major accidental magic"
    if code == "HP-15":
        return "Magic is dangerously suppressed" if failed(actual, str(data.get("trigger_results") or "")) else "Safe magical support"
    if code == "HP-19":
        return "Ministry intervention" if actual in {1, 2} else "Secrecy preserved"
    return None


def _apply_hp_roll_result(session: Session, save: ChronicleSave, roll: Record, actual: int) -> int:
    """Persist only deterministic tracker-side consequences from an HP roll."""
    data = roll.data or {}; code = str(data.get("hp_rule_code") or "").upper()
    sim_id = str(data.get("sim_id") or ""); sim = session.get(Record, sim_id) if sim_id else None
    changed = 0
    if code == "HP-05" and sim and not sim.deleted:
        sim_data = dict(sim.data or {}); magical_parent = data.get("hp_birth_branch") == "magical-parent"
        if magical_parent and actual == 1:
            parents = [session.get(Record, parent_id) for parent_id in data.get("hp_parent_ids") or []]
            magical_count = sum(_hp_magical(parent) for parent in parents)
            updates = {"hp_magical_ability": "Squib", "hp_hidden_squib": True,
                       "hp_blood_status": "Pureblood" if magical_count >= 2 else "Half-Blood" if magical_count else "Magical ancestry",
                       "hp_birth_roll_id": roll.id, "hp_birth_roll_global_day": int(roll.global_day or save.global_day)}
        elif magical_parent:
            parents = [session.get(Record, parent_id) for parent_id in data.get("hp_parent_ids") or []]
            magical_count = sum(_hp_magical(parent) for parent in parents)
            updates = {"hp_magical_ability": _hp_witch_or_wizard(sim_data), "hp_hidden_squib": False,
                       "hp_blood_status": "Pureblood" if magical_count >= 2 else "Half-Blood",
                       "hp_birth_roll_id": roll.id, "hp_birth_roll_global_day": int(roll.global_day or save.global_day)}
        elif actual == 1:
            updates = {"hp_magical_ability": _hp_witch_or_wizard(sim_data), "hp_hidden_squib": False,
                       "hp_blood_status": "Muggle-Born", "hp_birth_roll_id": roll.id,
                       "hp_birth_roll_global_day": int(roll.global_day or save.global_day)}
        else:
            updates = {"hp_magical_ability": "Muggle", "hp_hidden_squib": False,
                       "hp_blood_status": "Muggle", "hp_birth_roll_id": roll.id,
                       "hp_birth_roll_global_day": int(roll.global_day or save.global_day)}
        if any(sim_data.get(key) != value for key, value in updates.items()):
            base = sim.version; sim.data = {**sim_data, **updates}; sim.version += 1; journal(session, sim, "upsert", base); changed += 1
    elif code == "HP-06" and sim and not sim.deleted:
        sim_data = dict(sim.data or {}); updates = {"hp_hidden_squib": False, "hp_public_magical_status": "Squib",
                                                     "hp_squib_discovery_roll_id": roll.id, "hp_squib_discovered_global_day": int(roll.global_day or save.global_day)}
        if any(sim_data.get(key) != value for key, value in updates.items()):
            base = sim.version; sim.data = {**sim_data, **updates}; sim.version += 1; journal(session, sim, "upsert", base); changed += 1
    elif code == "HP-11" and actual == 1:
        pregnancy_id = str(data.get("hp_pregnancy_id") or ""); pregnancy = session.get(Record, pregnancy_id) if pregnancy_id else None
        if pregnancy and not pregnancy.deleted:
            pdata = dict(pregnancy.data or {}); next_count = int(data.get("hp_multiples_next") or 0)
            if next_count > int(pdata.get("babies_expected") or 0):
                base = pregnancy.version; pregnancy.data = {**pdata, "babies_expected": next_count,
                    "hp_magical_multiples_roll_id": roll.id, "hp_magical_multiples_level": next_count}; pregnancy.version += 1
                journal(session, pregnancy, "upsert", base); changed += 1
    elif code == "HP-14" and sim and not sim.deleted and actual == 1:
        sim_data = dict(sim.data or {}); updates = {"hp_accidental_magic_count": int(sim_data.get("hp_accidental_magic_count") or 0) + 1,
                                                     "hp_last_accidental_magic_global_day": int(roll.global_day or save.global_day),
                                                     "hp_last_accidental_magic_roll_id": roll.id}
        base = sim.version; sim.data = {**sim_data, **updates}; sim.version += 1; journal(session, sim, "upsert", base); changed += 1
    elif code == "HP-15" and sim and not sim.deleted and failed(actual, str(data.get("trigger_results") or "")):
        sim_data = dict(sim.data or {}); updates = {"hp_obscurial_status": "At risk", "hp_obscurial_risk_roll_id": roll.id}
        base = sim.version; sim.data = {**sim_data, **updates}; sim.version += 1; journal(session, sim, "upsert", base); changed += 1
    elif code == "HP-19":
        household_id = str(data.get("hp_household_id") or ""); household = session.get(Record, household_id) if household_id else None
        if household and not household.deleted and actual in {1, 2}:
            hdata = dict(household.data or {}); updates = {"hp_secrecy_violation_count": int(hdata.get("hp_secrecy_violation_count") or 0) + 1,
                                                             "hp_last_secrecy_roll_id": roll.id, "hp_last_secrecy_global_day": int(roll.global_day or save.global_day)}
            base = household.version; household.data = {**hdata, **updates}; household.version += 1; journal(session, household, "upsert", base); changed += 1
    return changed


def schedule_rolls(session: Session, save: ChronicleSave) -> int:
    if not automation_enabled(save):
        return 0
    save.revision += apply_due_migrations(session,save)
    rules = [item for item in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll_rule", Record.deleted.is_(False)
    )) if core_rulesets.applies_to_selected_core(save, item)]
    sims = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False))))
    save.revision += retire_prechallenge_rolls(session, save)
    save.revision += retire_dead_sim_rolls(session, save, sims)
    created = 0
    for sim in sims:
        created += _schedule_sim_lifecycle_rolls(session, save, sim, rules)
    created += _schedule_hogwarts_sorting_rolls(session, save, sims)
    created += _schedule_harry_potter_rolls(session, save, sims)
    if (save.settings or {}).get("maternal_rolls_enabled", True):
        maternal_rules = [rule for rule in rules if "maternal" in rule.label.casefold() and rule.data.get("active", True)]
        pregnancies = list(session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "pregnancy", Record.deleted.is_(False),
        )))
        for pregnancy in pregnancies:
            status = str(pregnancy.data.get("status") or "active").casefold()
            if pregnancy.data.get("maternal_rolls_required") is False or status in {"miscarriage", "cancelled", "canceled"}:
                continue
            due = pregnancy.data.get("due_global_day", pregnancy.global_day)
            mother = session.get(Record, pregnancy.data.get("mother_id"))
            birth = mother.data.get("birth_global_day", mother.global_day) if mother else None
            mother_death = mother.data.get("death_global_day") if mother else None
            if due is None or int(due) < 1 or birth is None or (mother and (bool((mother.data or {}).get("game_was_dead")) or "Servo" in occult_rules.sim_occult_types(mother.data))) or (mother_death is not None and int(mother_death) <= save.global_day) or (status in CLOSED_PREGNANCIES and int(due) < save.global_day):
                continue
            rule = maternal_rule_for_day(save, maternal_rules, mother, int(due))
            if not rule:
                continue
            source = f"maternal:{pregnancy.id}:{rule.id}"
            exists = session.scalar(select(Record.id).where(
                Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
                (Record.data["source"].as_string() == source) |
                (
                    (Record.data["source_id"].as_string() == pregnancy.id) &
                    (Record.data["roll_type"].as_string() == rule.label) &
                    (Record.global_day == int(due))
                ),
            ).limit(1))
            if exists:
                continue
            roll = Record(save_id=save.id,kind="roll",label=f"{mother.label} — {rule.label}",global_day=int(due),data={
                "sim_id":mother.id,"sim_name":mother.label,"source_id":pregnancy.id,"roll_type":rule.label,
                "die":rule.data.get("die"),"bad_results":rule.data.get("bad_results"),"source":source,
                "due_global_day":int(due),"completed":False,
                "core_ruleset_id":rule.data.get("core_ruleset_id"),
                "core_source_rule_id":rule.data.get("source_rule_id"),
            })
            session.add(roll);session.flush();journal(session,roll,"upsert",0);created += 1
    marriage_created, marriage_retired = _schedule_marriage_rolls(session, save, sims)
    created += marriage_created
    remarriage_created, remarriage_retired = _schedule_remarriage_rolls(session, save, sims)
    created += remarriage_created
    created += schedule_occult_rolls(session, save, sims)
    created += schedule_event_rolls(session, save, sims)
    created += schedule_campaign_rolls(session, save, sims)
    portrait_prompt_created = decade_portraits.schedule_prompt(session, save)
    save.revision += created + marriage_retired + remarriage_retired + portrait_prompt_created
    return created


def failed(actual: int, bad_results: str) -> bool:
    text = str(bad_results or "").replace("–", "-").replace("—", "-").replace("�", "-")
    range_pattern = r"(?<!\d)(-?\d+)\s*-\s*(-?\d+)(?!\d)"
    for match in re.finditer(range_pattern, text):
        low, high = map(int, match.groups())
        if min(low, high) <= actual <= max(low, high): return True
    singles = re.sub(range_pattern, " ", text)
    if actual in (int(value) for value in re.findall(r"-?\d+", singles)): return True
    return False


def prior_lifecycle_rolls(session: Session, save: ChronicleSave, sim: Record) -> list[Record]:
    """Return unfinished aging checks the Sim has already lived through."""
    if sim.kind != "sim" or sim.deleted:
        return []
    birth = sim.data.get("birth_global_day", sim.global_day)
    if birth is None:
        return []
    death = sim.data.get("death_global_day")
    lived_through = min(save.global_day, int(death)) if death is not None else save.global_day
    rows = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["sim_id"].as_string() == sim.id,
        Record.data["completed"].as_boolean().is_not(True),
        Record.global_day <= lived_through,
    ).order_by(Record.global_day, Record.created_at)))
    return [
        roll for roll in rows
        if str((roll.data or {}).get("source") or "").startswith("aging:")
        and not bool((roll.data or {}).get("death_age_rng"))
    ]


def pass_prior_lifecycle_rolls(session: Session, save: ChronicleSave, sim: Record) -> dict:
    """Record safe passing results for every earlier life-stage check."""
    # This is an explicit player action, so it remains useful even while the
    # save-wide automatic scheduling switch is paused.
    rules = [item for item in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll_rule", Record.deleted.is_(False)
    )) if core_rulesets.applies_to_selected_core(save, item)]
    created = _schedule_sim_lifecycle_rolls(session, save, sim, rules)
    save.revision += created
    passed = skipped = 0
    for roll in prior_lifecycle_rolls(session, save, sim):
        data = dict(roll.data or {})
        match = re.search(r"(?:^|\b)d\s*(\d+)(?:\b|$)", str(data.get("die") or ""), re.IGNORECASE)
        faces = int(match.group(1)) if match else 20
        safe = next((actual for actual in range(1, max(1, faces) + 1)
                     if not failed(actual, str(data.get("bad_results") or ""))), None)
        if safe is None:
            skipped += 1
            continue
        roll.data = {
            **data, "age_stage_catch_up": True,
            "age_stage_catch_up_global_day": save.global_day,
            "age_stage_catch_up_reason": "Sim was added after this life stage",
        }
        complete_roll(session, save, roll, safe, "Passed automatically — prior life stage")
        passed += 1
    return {"passed": passed, "skipped": skipped}


def _event_death_cause(session: Session, roll: Record) -> str | None:
    event_id = (roll.data or {}).get("event_id") or (roll.data or {}).get("campaign_id")
    event = session.get(Record, event_id) if event_id else None
    if not event:
        return None
    data = event.data or {}
    explicit = str(data.get("death_cause") or data.get("cause_of_death") or "").strip()
    if explicit:
        return explicit
    name = event.label
    text = f"{name} {data.get('scope','')} {data.get('notes','')}".casefold()
    if any(word in text for word in ("war", "battle", "siege", "invasion", "revolt", "massacre")):
        return f"Killed during {name}"
    if any(word in text for word in ("plague", "pandemic", "epidemic", "cholera", "influenza", "pox", "fever")):
        return name
    if "famine" in text or "starvation" in text:
        return f"Famine during {name}"
    if "fire" in text:
        return f"Fire during {name}"
    if any(word in text for word in ("flood", "storm", "earthquake", "eruption", "disaster")):
        return f"Disaster during {name}"
    return f"Death during {name}"


def _death_window(session: Session, save: ChronicleSave, roll: Record, sim: Record) -> tuple[int, int]:
    data = roll.data or {}
    start = max(1, int(data.get("death_window_start") or roll.global_day or save.global_day))
    end = int(data.get("death_window_end") or start)
    event_id = data.get("event_id")
    event = session.get(Record, event_id) if event_id else None
    if event:
        start = max(start, int(event.data.get("start_global_day", event.global_day) or start))
        end = max(start, int(event.data.get("end_global_day", event.global_day) or start))
    else:
        roll_type = str(data.get("roll_type") or "").casefold()
        if "maternal" not in roll_type:
            birth = sim.data.get("birth_global_day", sim.global_day)
            current_offset = AGING_STAGE_OFFSETS.get(roll_type)
            if birth is not None and current_offset is not None:
                later = sorted(offset for offset in AGING_STAGE_OFFSETS.values() if offset > current_offset)
                if later:
                    end = max(start, int(birth) + later[0] - 1)
                elif "elder" in roll_type:
                    end = max(start, int(birth) + int((save.settings or {}).get("elder_max_age_days", 320)))
    # Never assign a newly discovered death before the day on which the player
    # resolved the roll. It may be scheduled later within the applicable range.
    start = max(start, save.global_day)
    return start, max(start, end)


def _retire_rolls_after_death(session: Session, save: ChronicleSave, sim_id: str, death_day: int, source_roll_id: str) -> int:
    pending = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "roll", Record.deleted.is_(False),
        Record.data["sim_id"].as_string() == sim_id,
    )))
    retired = 0
    for item in pending:
        if item.id == source_roll_id or item.data.get("completed") or item.global_day is None:
            continue
        if death_day > save.global_day and int(item.global_day) < death_day:
            continue
        base = item.version
        item.deleted = True
        item.data = {**item.data, "retired_reason": "Sim dies before this obligation", "retired_global_day": save.global_day,
                     "retired_by_death_roll_id": source_roll_id}
        item.version += 1; journal(session, item, "delete", base); retired += 1
    return retired


def complete_roll(session: Session, save: ChronicleSave, roll: Record, actual: int, outcome_override: str = "") -> dict:
    if roll.kind != "roll" or roll.deleted: raise ValueError("That roll is unavailable.")
    automate = automation_enabled(save)
    base = roll.version
    pregnancy_count = None
    death_age_rng = bool(roll.data.get("death_age_rng"))
    if death_age_rng:
        is_bad = False
        automatic_outcome = f"Old-age death scheduled during historical age {actual}"
    elif bool(roll.data.get("pregnancy_count_roll")):
        pregnancy_count, automatic_outcome = pregnancy_count_result(actual, str(roll.data.get("result_rules") or ""), str(roll.data.get("zero_results") or ""))
        is_bad = False
    elif _marriage_roll(roll):
        automatic_outcome = marriage_roll_result(actual, str(roll.data.get("result_rules") or ""), str(roll.data.get("bad_results") or ""))
        is_bad = automatic_outcome in {"Does not marry", "Does not remarry"}
    else:
        is_bad = failed(actual, str(roll.data.get("bad_results") or ""))
        mapped_outcome = _mapped_roll_outcome(actual, str(roll.data.get("result_rules") or ""))
        automatic_outcome = mapped_outcome or (roll.data.get("failure_outcome") if is_bad else roll.data.get("success_outcome"))
        if _is_hogwarts_sorting_roll(roll):
            automatic_outcome = _HOGWARTS_HOUSES_BY_D4.get(int(actual), automatic_outcome)
        hp_outcome = _hp_roll_outcome(roll, actual)
        if hp_outcome:
            automatic_outcome = hp_outcome
        if roll.data.get("event_id") and is_bad:
            # Mixed event tables can contain both lethal and nonlethal failures.
            # The actual result controls death automation, not the event as a whole.
            roll.data = {**roll.data, "nonlethal": not _lethal_outcome(mapped_outcome) if mapped_outcome else not bool(roll.data.get("failure_is_lethal"))}
    rule_trigger_results=str(roll.data.get("trigger_results") or "")
    rule_triggered = bool(rule_trigger_results) and bool(roll.data.get("occult_roll") or roll.data.get("rule_generated")) and failed(actual,rule_trigger_results)
    roll.data = {**roll.data, "actual": actual, "outcome": outcome_override.strip() or automatic_outcome or ("Failed" if is_bad else "Passed"), "completed": True, "completed_global_day": save.global_day,
                 "triggered":rule_triggered if (roll.data.get("occult_roll") or roll.data.get("rule_generated")) and rule_trigger_results else roll.data.get("triggered")}
    allowance_changed = False
    hogwarts_house_changed = False
    if _is_hogwarts_sorting_roll(roll):
        sim_id = str(roll.data.get("sim_id") or "")
        sim = session.get(Record, sim_id) if sim_id else None
        house = _HOGWARTS_HOUSES_BY_D4.get(int(actual))
        if sim and not sim.deleted and house and not str((sim.data or {}).get("hp_hogwarts_house") or "").strip():
            sim_base = sim.version
            sim.data = {
                **(sim.data or {}), "hp_hogwarts_house": house,
                "hp_magical_school": str((sim.data or {}).get("hp_magical_school") or "Hogwarts"),
                "hp_hogwarts_sorting_roll_id": roll.id,
                "hp_hogwarts_sorted_global_day": int(roll.global_day or save.global_day),
            }
            sim.version += 1
            journal(session, sim, "upsert", sim_base)
            hogwarts_house_changed = True
    hp_changed = _apply_hp_roll_result(session, save, roll, actual) if automate else 0
    family_plan = None
    family_plan_changed = False
    family_plan_created = False
    if pregnancy_count is not None:
        roll.data = {**roll.data, "pregnancy_count":pregnancy_count}
        sim_id = roll.data.get("sim_id")
        allowance_sim = session.get(Record, sim_id) if sim_id else None
        if automate and allowance_sim and allowance_sim.kind == "sim" and not allowance_sim.deleted:
            year = int(roll.data.get("planner_year") or (save.start_year + (save.global_day - 1) // max(1, save.days_per_year)))
            sim_data = dict(allowance_sim.data or {}); allowances = dict(sim_data.get("pregnancy_allowances") or {})
            allowances[str(year)] = {"allowed":pregnancy_count, "roll_id":roll.id, "recorded_global_day":save.global_day, "actual":actual}
            sim_data.update({
                "pregnancy_allowances":allowances, "pregnancy_allowance_count":pregnancy_count,
                "pregnancy_allowance_year":year, "pregnancy_allowance_roll_id":roll.id,
                "pregnancy_allowance_recorded_global_day":save.global_day,
            })
            sim_base = allowance_sim.version; allowance_sim.data = sim_data; allowance_sim.version += 1
            journal(session, allowance_sim, "upsert", sim_base); allowance_changed = True
            family_plan, family_plan_changed, family_plan_created = sync_family_plan_from_pregnancy_roll(
                session, save, roll, allowance_sim, pregnancy_count,
            )
    if automate and _marriage_roll(roll):
        marriage_updates = {"nonlethal":True}
        outcome_text = str(roll.data.get("outcome") or "").casefold()
        if ("may marry" in outcome_text or "may remarry" in outcome_text) and roll.data.get("suggested_marriage_global_day") in (None, ""):
            first_day = max(save.global_day, int(roll.global_day or save.global_day)) + 1
            last_day = first_day + max(1, int(save.days_per_year)) - 1
            suggested = random.SystemRandom().randint(first_day, last_day)
            marriage_updates.update({
                "suggested_marriage_global_day": suggested,
                "suggested_marriage_date_range": calendar_utils.date_range_label(suggested, save.start_year, save.days_per_year),
                "suggested_marriage_date_source": "Generated after successful marriage eligibility roll",
            })
        roll.data = {**roll.data, **marriage_updates}
    occult_changed = apply_occult_roll_result(session, roll, actual) if automate else 0
    automatic_followups = _schedule_automatic_occult_followup(session, save, roll) if automate else 0
    occult_scheduled = (
        schedule_occult_rolls(session, save)
        if occult_changed and str(roll.data.get("occult_effect") or "") == "set_occult_alignment"
        else 0
    )
    hp_scheduled = _schedule_harry_potter_rolls(
        session, save, list(session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
        )))
    ) if hp_changed else 0
    if automate:
        automatic_followups += _schedule_event_followup(session, save, roll, actual)
    service_changed = _record_campaign_service(session, save, roll) if automate else False
    roll.version += 1; journal(session, roll, "upsert", base)
    death = None
    death_created = False
    death_changed = False
    if automate and death_age_rng:
        sim_id = roll.data.get("sim_id"); sim = session.get(Record, sim_id) if sim_id else None
        if sim and not sim.deleted and not bool((sim.data or {}).get("death_confirmed")):
            birth_day = (sim.data or {}).get("birth_global_day", sim.global_day)
            age_year_start = int(birth_day if birth_day is not None else roll.global_day or save.global_day) + max(1, int(actual)) * max(1, save.days_per_year)
            age_year_end = age_year_start + max(1, save.days_per_year) - 1
            proposed = (
                save.global_day if save.global_day > age_year_end
                else random.SystemRandom().randint(max(save.global_day, age_year_start), age_year_end)
            )
            try: existing_day = int((sim.data or {}).get("death_global_day"))
            except (TypeError, ValueError): existing_day = None
            death_day = min(proposed, existing_day) if existing_day is not None else proposed
            if existing_day is None or proposed < existing_day:
                scheduled = session.scalar(select(Record).where(
                    Record.save_id == save.id, Record.kind == "death", Record.deleted.is_(False),
                    Record.data["sim_id"].as_string() == sim.id,
                ).order_by(Record.global_day.asc()).limit(1))
                cause = "Old age"
                fields={"historical_death_date_range":calendar_utils.date_range_label(death_day,save.start_year,save.days_per_year),"death_date_precision":"challenge-day-only"}
                if scheduled and not bool((scheduled.data or {}).get("completed")):
                    death=scheduled;base_death=death.version;death.global_day=death_day
                    death.data={**(death.data or {}),"sim_id":sim.id,"cause":cause,"source_roll_id":roll.id,"completed":False,**fields}
                    death.version+=1;journal(session,death,"upsert",base_death)
                else:
                    death=Record(save_id=save.id,kind="death",label=f"Death of {sim.label}",global_day=death_day,data={"sim_id":sim.id,"cause":cause,"source_roll_id":roll.id,"completed":False,**fields})
                    session.add(death);session.flush();journal(session,death,"upsert",0);death_created=True
                sim_base=sim.version;sim.data={**(sim.data or {}),"death_global_day":death_day,"cause_of_death":cause,"death_confirmed":False,"death_source_roll_id":roll.id,**fields};sim.version+=1;journal(session,sim,"upsert",sim_base)
                death_changed=True
            save.revision += _retire_rolls_after_death(session,save,sim.id,death_day,roll.id)
    if automate and is_bad and not bool(roll.data.get("nonlethal")):
        sim_id = roll.data.get("sim_id")
        sim = session.get(Record, sim_id) if sim_id else None
        if sim and not sim.deleted and not bool((sim.data or {}).get("death_confirmed")):
            window_start, window_end = _death_window(session, save, roll, sim)
            failed_roll_death_day = random.SystemRandom().randint(window_start, window_end)
            try:
                existing_death_day = int((sim.data or {}).get("death_global_day"))
            except (TypeError, ValueError):
                existing_death_day = None
            death_day = min(failed_roll_death_day, existing_death_day) if existing_death_day is not None else failed_roll_death_day
            roll_type = str(roll.data.get("roll_type") or "").casefold()
            group = "birth" if "maternal" in roll_type or "being born" in roll_type else "elder" if "elder" in roll_type else "infant" if any(x in roll_type for x in ("newborn", "infant")) else "child" if any(x in roll_type for x in ("toddler", "child", "preteen", "teen")) else "adult"
            event_cause = _event_death_cause(session, roll)
            pool = session.scalar(select(Record).where(Record.save_id == save.id, Record.kind == "death_causes", Record.label == group.title()))
            causes = (pool.data.get("causes") if pool else DEFAULT_DEATH_CAUSES[group])
            if isinstance(causes, str):
                causes = [value.strip() for value in re.split(r"[;\n]+", causes) if value.strip()]
            causes = causes or DEFAULT_DEATH_CAUSES[group]
            cause = event_cause or (random.SystemRandom().choice(causes) if (save.settings or {}).get("automatic_death_causes", True) else "Player choice")
            # Only rewrite the schedule when the failed-roll date is earlier.
            # If the Sim was already due to die sooner, that earlier date wins.
            if existing_death_day is None or failed_roll_death_day < existing_death_day:
                previous_cause = (sim.data or {}).get("cause_of_death")
                scheduled = session.scalar(select(Record).where(
                    Record.save_id == save.id, Record.kind == "death", Record.deleted.is_(False),
                    Record.data["sim_id"].as_string() == sim.id,
                ).order_by(Record.global_day.asc()).limit(1))
                date_fields = {
                    "historical_death_date_range": calendar_utils.date_range_label(death_day, save.start_year, save.days_per_year),
                    "death_date_precision": "challenge-day-only",
                }
                if scheduled and not bool((scheduled.data or {}).get("completed")):
                    death = scheduled
                    death_base = death.version
                    death.global_day = death_day
                    death.data = {
                        **death.data, "sim_id": sim.id, "cause": cause,
                        "source_roll_id": roll.id, "completed": False,
                        "rescheduled_from_global_day": existing_death_day,
                        "rescheduled_from_cause": (death.data or {}).get("cause") or previous_cause,
                        **date_fields,
                    }
                    death.version += 1; journal(session, death, "upsert", death_base)
                else:
                    death = Record(save_id=save.id, kind="death", label=f"Death of {sim.label}", global_day=death_day, data={
                        "sim_id": sim.id, "cause": cause, "source_roll_id": roll.id,
                        "completed": False, "rescheduled_from_global_day": existing_death_day,
                        "rescheduled_from_cause": previous_cause,
                        **date_fields,
                    })
                    session.add(death); session.flush(); journal(session, death, "upsert", 0)
                    death_created = True
                sim_data = dict(sim.data or {})
                for key in ("death_date", "historical_death_date", "death_game_hour", "death_game_minute", "death_time"):
                    sim_data.pop(key, None)
                sim_base = sim.version
                sim.data = {
                    **sim_data, "death_global_day": death_day, "cause_of_death": cause,
                    "death_confirmed": False, "rescheduled_from_global_day": existing_death_day,
                    "rescheduled_from_cause": previous_cause,
                    "death_source_roll_id": roll.id, **date_fields,
                }
                sim.version += 1; journal(session, sim, "upsert", sim_base)
                save.revision += end_illnesses_for_death(session, save, sim, death_day)
                death_changed = True
            save.revision += _retire_rolls_after_death(session, save, sim.id, death_day, roll.id)
    save.revision += 1 + int(death_changed) + int(allowance_changed) + int(hogwarts_house_changed) + int(family_plan_changed) + int(service_changed) + occult_changed + occult_scheduled + hp_changed + hp_scheduled + automatic_followups
    return {
        "outcome": roll.data["outcome"], "death": sync.serialize(death) if death else None,
        "death_created": death_created, "death_changed": death_changed, "pregnancy_count":pregnancy_count,
        "family_plan": sync.serialize(family_plan) if family_plan else None,
        "family_plan_changed": family_plan_changed,
        "family_plan_created": family_plan_created,
        "hogwarts_house": roll.data.get("outcome") if _is_hogwarts_sorting_roll(roll) else None,
        "harry_potter_updates": hp_changed,
        "suggested_marriage_global_day": roll.data.get("suggested_marriage_global_day"),
        "automatic_followups": automatic_followups,
    }
