"""Editable occult challenge rule library and small normalization helpers."""

from __future__ import annotations

import re


OCCULT_TYPES = (
    "Alien", "Vampire", "Mermaid", "Spellcaster", "Werewolf", "Fairy",
    "Ghost", "Servo", "PlantSim", "Skeleton",
)


# Follow-up relationships are kept separately from the editable rule rows so
# saves created before the workbench can use them immediately. Future rule
# records may instead declare `triggered_by` themselves.
OCCULT_FOLLOW_UPS = {
    "vampire_hunt": ("vampire_accused", "vampire_false_accusation"),
    "vampire_accused": ("vampire_accused_death",),
    "vampire_feeding_suspicion": ("vampire_accused",),
    "alien_discovery": ("alien_discovery_type", "alien_discovery_death"),
    "spellcaster_witch_trial": ("spellcaster_accused", "spellcaster_false_accusation"),
    "spellcaster_accused": ("spellcaster_verdict",),
    "spellcaster_false_accusation": ("spellcaster_verdict",),
    "mermaid_discovery": ("mermaid_discovery_death",),
    "mermaid_sailor": ("mermaid_murder_suspicion",),
    "mermaid_murder_suspicion": ("mermaid_discovery",),
    "werewolf_attack": ("werewolf_close_relation", "werewolf_attack_death", "werewolf_turn_adult", "werewolf_turn_child"),
    "werewolf_discovery": ("werewolf_hunt_response",),
    "werewolf_hunt_response": ("werewolf_hunt_death",),
    "fairy_mischief": ("fairy_mischief_result",),
    "fairy_changeling": ("fairy_changeling_truth",),
    "ghost_persistence": ("ghost_haunting", "ghost_move_on", "ghost_exorcism", "ghost_binding"),
    "ghost_haunting": ("ghost_haunting_death",),
    "servo_malfunction": ("servo_breakdown",),
    "servo_breakdown": ("servo_catastrophic_repair",),
}


OCCULT_LETHAL_RESULTS = {
    "vampire_accused_death": "3",
    "alien_discovery_death": "1,6",
    "spellcaster_verdict": "1",
    "spellcaster_innocent_drowning": "1",
    "mermaid_discovery_death": "1,6",
    "werewolf_attack_death": "1",
    "werewolf_hunt_death": "1-3",
    "ghost_haunting_death": "1",
    "servo_catastrophic_repair": "2-4",
}


def follow_up_keys(rule_key: str | None) -> tuple[str, ...]:
    return OCCULT_FOLLOW_UPS.get(str(rule_key or ""), ())


def lethal_results(rule_key: str | None) -> str:
    return OCCULT_LETHAL_RESULTS.get(str(rule_key or ""), "")


def _rule(key, label, occult, cadence, die, trigger, results, notes,
          *, start=-9999, end=9999, scope="sim", condition="", auto=True):
    return {
        "rule_key": key, "occult": occult, "cadence": cadence, "die": die,
        "trigger_results": trigger, "result_rules": results, "notes": notes,
        "start_year": start, "end_year": end, "scope": scope,
        "condition": condition, "auto_schedule": auto, "active": True,
        "source": "SeveralUDO occult rules supplied by the player",
        "label": label,
    }


# Recurring rules are scheduled automatically when the save-level switch is on.
# Follow-up and situational rules remain editable and visible, but are deliberately
# manual until their prerequisite is known.
DEFAULT_OCCULT_RULES = [
    _rule("general_inheritance", "General occult inheritance", "General", "birth", "dynamic", "dynamic", "Varies by the parents' detected occult and dormant blood", "Normal game genetics apply first. A human child may carry or manifest dormant occult blood; two different occults use a coin flip."),
    _rule("alignment_inheritance", "Occult alignment inheritance", "General", "birth", "d10", "1", "1: Opposite alignment; 2-10: Inherit occult parent's alignment", "Opposing parents flip a coin instead.", auto=False),

    _rule("vampire_hunt", "Vampire hunt occurrence", "Vampire", "annual", "d2", "1", "1: Heads — vampire hunt occurs; 2: Tails — no hunt", "Roll once per household or settlement while a vampire lives there.", scope="household"),
    _rule("vampire_accused", "Vampire accused during a hunt", "Vampire", "follow-up", "d10", "9", "9: Accused; all others: Not accused", "Two accusation rolls after witnessed powers, public feeding, or an attack; otherwise one.", auto=False),
    _rule("vampire_false_accusation", "Human falsely accused during a vampire hunt", "Vampire", "follow-up", "d20", "18", "18: Human falsely accused; all others: No false accusation", "Choose an eligible human in the affected household or settlement.", auto=False),
    _rule("vampire_accused_death", "Accused Sim dies in a vampire hunt", "Vampire", "follow-up", "d4", "3", "3: Accused Sim dies; all others: Survives", "This is the lethal follow-up after an accusation.", auto=False),
    _rule("vampire_feeding_suspicion", "Unwilling vampire feeding suspicion", "Vampire", "follow-up", "d6", "1", "1: Suspicion raised; 2-6: Secret preserved", "Suspicion makes the next hunt accusation automatically succeed.", auto=False),
    _rule("vampire_alignment_guidance", "Good and bad vampire modifiers", "Vampire", "guidance", "", "", "", "Good vampires ignore ordinary illness but roll event dangers twice. Bad vampires roll illness twice and require two consecutive war deaths. Alignment is editable on the Sim profile.", auto=False),

    _rule("alien_discovery", "Alien discovery", "Alien", "annual", "d20", "1", "1: Discovered; 2-20: Hidden", "Before 1900 discovery is interpreted through the period's culture.", end=1899),
    _rule("alien_discovery", "Alien discovery", "Alien", "annual", "d20", "1-2", "1-2: Discovered; 3-20: Hidden", "Scientific and government investigation becomes more capable.", start=1900, end=1945),
    _rule("alien_discovery", "Alien discovery", "Alien", "annual", "d20", "1-3", "1-3: Discovered; 4-20: Hidden", "After 1946, a discovery also needs a public-versus-government coin flip.", start=1946),
    _rule("alien_discovery_death", "Alien death after discovery", "Alien", "follow-up", "d10", "1,6", "1 or 6: Dies; all others: Survives and must conceal or flee", "A surviving government discovery requires one year away from normal society.", auto=False),
    _rule("alien_discovery_type", "Post-1946 alien discovery type", "Alien", "follow-up", "d2", "1", "1: Heads — public/local; 2: Tails — government", "Only after a successful discovery from 1946 onward.", start=1946, auto=False),

    _rule("spellcaster_resurrection_standard", "Spellcaster resurrection — standard", "Spellcaster", "follow-up", "d6", "1", "1: Resurrection succeeds; 2-6: Fails", "Requires all Practical spells, two spells from every other school, Sage, and ten False Morels.", auto=False),
    _rule("spellcaster_resurrection_highest", "Spellcaster resurrection — highest rank", "Spellcaster", "follow-up", "d4", "2", "2: Resurrection succeeds; all others: Fails", "Ingredients are consumed either way.", auto=False),
    _rule("spellcaster_resurrection_close", "Spellcaster resurrection — close non-family", "Spellcaster", "follow-up", "d10", "1", "1: Resurrection succeeds; 2-10: Fails", "For a personally close Sim who is not family or a loved one.", auto=False),
    _rule("spellcaster_resurrection_stranger", "Spellcaster resurrection — stranger", "Spellcaster", "follow-up", "d12", "1", "1: Resurrection succeeds; 2-12: Fails", "One attempt per death event.", auto=False),
    _rule("spellcaster_witch_trial", "Witch trial occurrence", "Spellcaster", "annual", "d4", "3", "3: Witch trial occurs; all others: No trial", "Roll once per household containing a spellcaster through 1878.", end=1878, scope="household"),
    _rule("spellcaster_accused", "Actual spellcaster accused", "Spellcaster", "follow-up", "d8", "1", "1: Spellcaster accused; 2-8: Not accused", "Follow-up to a witch trial.", end=1878, auto=False),
    _rule("spellcaster_false_accusation", "Non-spellcaster accused", "Spellcaster", "follow-up", "d12", "4", "4: Non-spellcaster accused; all others: Not accused", "Follow-up to a witch trial.", end=1878, auto=False),
    _rule("spellcaster_verdict", "Witch-trial verdict", "Spellcaster", "follow-up", "d2", "1", "1: Heads — guilty and executed; 2: Tails — innocent", "A saved spellcaster must leave because the settlement believes they died or escaped.", end=1878, auto=False),
    _rule("spellcaster_innocent_drowning", "Innocent Sim drowned anyway", "Spellcaster", "follow-up", "d4", "1", "1: Drowned; 2-4: Survives", "Only after an innocent witch-trial verdict.", end=1878, auto=False),
    _rule("spellcaster_selfish_resurrection", "Bad spellcaster selfish resurrection", "Spellcaster", "follow-up", "d12", "1", "1: Resurrection succeeds; 2-12: Fails", "Bad spellcasters may only resurrect for explicitly selfish purposes, regardless of rank.", auto=False),

    _rule("mermaid_discovery", "Mermaid discovery", "Mermaid", "annual", "d20", "1", "1: Discovered; 2-20: Hidden", "Mermaids may exist from the beginning of history."),
    _rule("mermaid_discovery_death", "Mermaid death after discovery", "Mermaid", "follow-up", "d12", "1,6", "1 or 6: Dies; all others: Survives", "Follow-up after discovery.", auto=False),
    _rule("mermaid_sailor", "Mermaid sailor encounter", "Mermaid", "annual", "d12", "4", "4: Bad mermaid kills a sailor / good mermaid rescues one; all others: No encounter", "Only with meaningful ocean or coastal access.", condition="coastal"),
    _rule("mermaid_murder_suspicion", "Mermaid murder suspicion", "Mermaid", "follow-up", "d6", "1", "1: Witnesses or suspicion; 2-6: Unseen", "Suspicion causes an immediate discovery roll.", auto=False),
    _rule("mermaid_dehydration", "Inland mermaid dehydration", "Mermaid", "annual", "d6", "1", "1: Severe dehydration; 2-6: Stable", "On dehydration, make the normal illness/death roll twice.", condition="inland"),

    _rule("werewolf_attack", "Loose werewolf attacks on a full moon", "Werewolf", "full_moon", "d6", "1", "1: Attacks someone; 2-6: No attack", "Not generated while the Sim is marked securely confined.", condition="loose"),
    _rule("werewolf_discovery", "Loose werewolf discovered on a full moon", "Werewolf", "full_moon", "d8", "1", "1: Discovered; 2-8: Hidden", "Not generated while the Sim is marked securely confined.", condition="loose"),
    _rule("werewolf_attack_death", "Werewolf kills during attack", "Werewolf", "follow-up", "d10", "1", "1: Victim dies; 2-10: Victim survives", "Choose an eligible nearby or household victim.", auto=False),
    _rule("werewolf_close_relation", "Werewolf attacks a close relation", "Werewolf", "follow-up", "d4", "1", "1: Attacks the close relation; 2-4: Reroll an unrelated victim", "Use for spouse, child, parent, sibling, or extremely close friend.", auto=False),
    _rule("werewolf_turn_adult", "Werewolf attack survivor turns", "Werewolf", "follow-up", "d6", "1", "1: Infected/turned; 2-6: Remains unchanged", "For Teen and older survivors.", auto=False),
    _rule("werewolf_turn_child", "Child werewolf attack survivor turns", "Werewolf", "follow-up", "d10", "1", "1: Infected/turned; 2-10: Remains unchanged", "For children who survive an attack.", auto=False),
    _rule("werewolf_hunt_response", "Community response to discovered werewolf", "Werewolf", "follow-up", "d6", "5-6", "1-2: Ignored/disbelieved; 3-4: Must leave; 5-6: Werewolf hunt", "Follow-up after discovery.", auto=False),
    _rule("werewolf_hunt_death", "Werewolf killed during a hunt", "Werewolf", "follow-up", "d6", "1-3", "1-3: Killed; 4-6: Survives", "Lethal hunt follow-up.", auto=False),

    _rule("fairy_discovery", "Fairy discovery", "Fairy", "annual", "d20", "1", "1: Discovered; 2-20: Hidden", "Fairies are exceptionally good at hiding before 1500.", end=1499),
    _rule("fairy_discovery", "Fairy discovery", "Fairy", "annual", "d12", "1", "1: Discovered; 2-12: Hidden", "Discovery becomes more likely with human expansion.", start=1500, end=1799),
    _rule("fairy_discovery", "Fairy discovery", "Fairy", "annual", "d10", "1", "1: Discovered; 2-10: Hidden", "Industrial-era discovery chance.", start=1800, end=1899),
    _rule("fairy_discovery", "Fairy discovery", "Fairy", "annual", "d8", "1", "1: Discovered; 2-8: Hidden", "Modern records and shrinking wilderness increase discovery.", start=1900),
    _rule("fairy_intervention", "Benevolent fairy intervention", "Fairy", "annual", "d6", "1", "1: Intervention succeeds; 2-6: Fails", "May cancel one crop failure, animal death, famine, childhood illness, or pregnancy complication; never old age.", condition="good"),
    _rule("fairy_mischief", "Unseelie fairy mischief", "Fairy", "annual", "d8", "1", "1: Serious mischief event; 2-8: No serious event", "A triggered event uses the editable D6 consequence table.", condition="bad"),
    _rule("fairy_mischief_result", "Unseelie fairy mischief consequence", "Fairy", "follow-up", "d6", "1-6", "1: Crop failure; 2: Livestock death; 3: Serious illness; 4: Child disappears 1-7 days; 5: Resource loss; 6: Accident", "Apply normal death rolls after the result where appropriate.", auto=False),
    _rule("fairy_changeling", "Human newborn suspected changeling", "Fairy", "birth", "d20", "1", "1: Suspected changeling; 2-20: Not suspected", "Optional when fairy and human populations coexist. Do not reveal the truth until Child stage.", auto=False),
    _rule("fairy_changeling_truth", "Suspected changeling truth", "Fairy", "follow-up", "d2", "1", "1: Heads — genuinely fairy; 2: Tails — completely human", "Keep the result hidden until the Child life stage.", auto=False),

    _rule("ghost_persistence", "Spirit remains after death", "Ghost", "death", "dynamic", "dynamic", "Chance depends on peaceful death, premature death, accident, murder, or betrayal", "Generated once for every Sim who dies at age 10 or older. Historical age is calculated from birth and death Global Days using the save's days-per-year setting."),
    _rule("ghost_haunting", "Persistent ghost haunting", "Ghost", "annual", "d6", "6", "1: No manifestation; 2-3: Minor manifestation; 4: Major haunting; 5: Appears to family; 6: Violent supernatural event", "A result of 6 requires the haunting-death follow-up."),
    _rule("ghost_haunting_death", "Living Sim dies from a haunting", "Ghost", "follow-up", "d10", "1", "1: Living Sim dies; 2-10: Survives", "Only after a violent supernatural haunting.", auto=False),
    _rule("ghost_move_on", "Persistent ghost moves on", "Ghost", "annual", "d10", "1", "1: Spirit moves on; 2-10: Remains", "Add one successful number per resolved major issue; 1-4 after all unfinished business is resolved."),
    _rule("ghost_exorcism", "Good spellcaster releases a ghost", "Ghost", "follow-up", "d4", "1", "1: Released; 2-4: Remains", "Requires a qualified good spellcaster.", auto=False),
    _rule("ghost_binding", "Bad spellcaster binds a ghost", "Ghost", "follow-up", "d6", "1", "1: Bound; 2-6: Remains free", "A bound ghost cannot move on until released or the spellcaster dies.", auto=False),

    _rule("servo_construction", "Experimental Automaton construction", "Servo", "manual", "d20", "1", "1: Construction succeeds; 2-20: Fails and materials are consumed", "Requires max Handiness, mechanical/inventing skill, wealth, and electricity.", start=1898, end=1929, auto=False),
    _rule("servo_construction", "Electromechanical Servo construction", "Servo", "manual", "d12", "1", "1: Construction succeeds; 2-12: Fails", "Requirements remain max Handiness plus high mechanical/inventing ability.", start=1930, end=1959, auto=False),
    _rule("servo_construction", "Early Robotics Servo construction", "Servo", "manual", "d8", "1", "1: Construction succeeds; 2-8: Fails", "Servos may perform household, industrial, scientific, or military work.", start=1960, end=1989, auto=False),
    _rule("servo_construction", "Modern Servo construction", "Servo", "manual", "d4", "1", "1: Construction succeeds; 2-4: Fails", "Normal Servo gameplay becomes increasingly acceptable.", start=1990, end=2019, auto=False),
    _rule("servo_malfunction", "Experimental Servo mechanical failure", "Servo", "annual", "d4", "1", "1: Mechanical failure; 2-4: Operates normally", "Servos replace disease, famine, drowning, aging, and pregnancy rolls with this check.", start=1898, end=1929),
    _rule("servo_malfunction", "Electromechanical Servo mechanical failure", "Servo", "annual", "d6", "1", "1: Mechanical failure; 2-6: Operates normally", "Failure requires the breakdown follow-up.", start=1930, end=1959),
    _rule("servo_malfunction", "Early Robotics Servo mechanical failure", "Servo", "annual", "d8", "1", "1: Mechanical failure; 2-8: Operates normally", "Failure requires the breakdown follow-up.", start=1960, end=1989),
    _rule("servo_malfunction", "Modern Servo mechanical failure", "Servo", "annual", "d12", "1", "1: Mechanical failure; 2-12: Operates normally", "Failure requires the breakdown follow-up.", start=1990, end=2019),
    _rule("servo_malfunction", "Advanced Servo mechanical failure", "Servo", "annual", "d20", "1", "1: Mechanical failure; 2-20: Operates normally", "Construction no longer needs a roll if requirements are met.", start=2020),
    _rule("servo_breakdown", "Servo failure severity", "Servo", "follow-up", "d6", "6", "1-3: Repairable malfunction; 4-5: Major breakdown; 6: Catastrophic failure", "A catastrophic result requires a qualified repair attempt.", start=1898, auto=False),
    _rule("servo_catastrophic_repair", "Catastrophic Servo repair", "Servo", "follow-up", "d4", "1", "1: Servo saved; 2-4: Servo dies permanently", "A qualified Sim may make one repair attempt.", start=1898, auto=False),
]


def sim_occult_types(data: dict | None) -> list[str]:
    data = data or {}
    values = data.get("game_occult_types") or []
    if isinstance(values, str):
        values = re.split(r"[,/;|]+", values)
    text = " ".join(str(value) for value in values) + " " + str(data.get("species_occult") or "")
    compact = text.casefold()
    found = [name for name in OCCULT_TYPES if name.casefold() in compact]
    return found


def dormant_occult_types(data: dict | None) -> list[str]:
    value = (data or {}).get("dormant_occult_types") or []
    if isinstance(value, str):
        value = re.split(r"[,/;|]+", value)
    return [name for name in OCCULT_TYPES if any(name.casefold() == str(item).strip().casefold() for item in value)]


def living(data: dict | None, global_day: int) -> bool:
    data = data or {}
    if bool(data.get("game_was_dead")):
        return False
    try:
        return data.get("death_global_day") in (None, "") or int(data["death_global_day"]) > global_day
    except (TypeError, ValueError):
        return True


def water_access(data: dict | None, save_settings: dict | None) -> str:
    data = data or {}
    explicit = str(data.get("occult_water_access") or "").strip().casefold()
    if explicit in {"coastal", "open water", "coastal/open water", "ocean"}:
        return "coastal"
    if explicit == "inland":
        return "inland"
    context = " ".join(str(value or "") for value in (
        data.get("last_game_world"), data.get("last_game_lot"), data.get("birthplace"),
        (save_settings or {}).get("challenge_location"),
    )).casefold()
    coastal_words = ("coast", "ocean", "sea", "island", "beach", "sulani", "brindleton", "tartosa")
    return "coastal" if any(word in context for word in coastal_words) else "unknown"


def aligned(data: dict | None, wanted: str) -> bool:
    alignment = str((data or {}).get("occult_alignment") or "").strip().casefold()
    if wanted == "good":
        return alignment in {"good", "benevolent"}
    if wanted == "bad":
        return alignment in {"bad", "malevolent", "unseelie"}
    return True
