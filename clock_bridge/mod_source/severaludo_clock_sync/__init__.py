"""Clock Sync 2.2.6 reliable, queued life-history telemetry for The Sims 4."""

import base64
import hashlib
import json
import os
import re
import sys
import threading
import time

from . import compat_201 as _compat


VERSION = "2.2.6"
_core = _compat._core
_core.VERSION = VERSION
_compat.VERSION = VERSION
_previous_extended_snapshot = _compat._extended_snapshot
_previous_household_snapshot = getattr(_core, "_household_snapshot", lambda: ("", []))
_previous_send_payload = getattr(_core, "_send_payload", None)
_previous_config_path = getattr(_core, "_config_path", lambda: "")


def _config_path_v224():
    """Locate config beside the installed mod, including redirected Documents."""
    candidates = []
    module_file = str(globals().get("__file__", "") or "")
    marker = ".ts4script"
    marker_at = module_file.lower().find(marker)
    if marker_at >= 0:
        archive = module_file[:marker_at + len(marker)]
        candidates.append(os.path.join(os.path.dirname(archive), "config.json"))

    # Windows may redirect Documents into OneDrive. The game process keeps the
    # real known-folder path in the registry even when USERPROFILE\Documents is
    # no longer the active Sims user-data directory.
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            documents = os.path.expandvars(winreg.QueryValueEx(key, "Personal")[0])
            candidates.append(os.path.join(
                documents, "Electronic Arts", "The Sims 4", "Mods",
                "SeveralUDOClockSync", "config.json",
            ))
    except Exception:
        pass

    for variable in ("OneDriveConsumer", "OneDriveCommercial", "OneDrive"):
        root = os.environ.get(variable)
        if root:
            candidates.append(os.path.join(
                root, "Documents", "Electronic Arts", "The Sims 4", "Mods",
                "SeveralUDOClockSync", "config.json",
            ))
    fallback = _previous_config_path()
    if fallback:
        candidates.append(fallback)
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return candidates[0] if candidates else fallback


def _tuning_id(value):
    """Return a stable EA/creator tuning identifier without requiring a pack."""
    if value is None:
        return None
    for owner in (value, getattr(value, "resource_key", None), getattr(value, "guid", None)):
        if owner is None:
            continue
        for name in ("guid64", "instance", "tuning_id", "id"):
            raw = getattr(owner, name, None)
            try:
                raw = raw() if callable(raw) else raw
            except Exception:
                raw = None
            if raw not in (None, "", 0):
                return str(raw)
    return None


def _as_values(value, mapping_keys=False):
    if value is None:
        return ()
    if hasattr(value, "items"):
        try:
            return tuple(value.keys() if mapping_keys else value.values())
        except Exception:
            return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    try:
        return tuple(value)
    except Exception:
        return (value,)


def _read(owner, names, mapping_keys=False):
    if owner is None:
        return ()
    for name in names:
        value = _core._value_or_call(owner, name, None)
        if value is not None:
            values = _as_values(value, mapping_keys=mapping_keys)
            if values:
                return values
    return ()


def _humanize(value, prefixes=()):
    if value is None:
        return ""
    candidates = []
    for attr in ("display_name", "stat_name", "skill_name", "name", "__name__"):
        text = _core._value_or_call(value, attr, None)
        if text:
            candidates.append(str(text))
    candidates.append(_core._tuning_text(value))
    for raw in candidates:
        text = str(raw or "").strip()
        if not text or text.startswith("<") or " object at " in text:
            continue
        text = text.rsplit(".", 1)[-1].strip("<>'\"")
        for prefix in prefixes:
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):]
                break
        text = text.replace("_", " ").replace("-", " ")
        text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
        text = " ".join(text.split()).strip()
        if text and not text.isdigit():
            return text
    return ""


def _skill_type(stat):
    explicit = (getattr(stat, "stat_type", None) or
                getattr(stat, "statistic_type", None))
    if explicit is not None:
        return explicit
    # SimInfo.all_skills can return the tuned Skill class rather than the
    # live statistic instance on some game builds.
    if getattr(stat, "guid64", None) is not None or getattr(stat, "is_skill", None) is not None:
        return stat
    return type(stat)


def _is_skill(stat):
    tuning = _skill_type(stat)
    for owner in (stat, tuning):
        value = _core._value_or_call(owner, "is_skill", None)
        if value is not None:
            try:
                return bool(value)
            except Exception:
                pass
    names = []
    for owner in (stat, tuning):
        names.append(str(getattr(owner, "__name__", "") or ""))
        names.extend(str(item.__name__) for item in getattr(owner, "__mro__", ())
                     if getattr(item, "__name__", None))
    joined = " ".join(names).lower()
    return ("skill" in joined and
            not any(marker in joined for marker in ("tracker", "multiplier", "loot")))


def _skill_level(stat):
    for owner in (stat, _skill_type(stat)):
        for attr in ("skill_level", "level", "get_user_value", "get_value", "value"):
            value = _core._value_or_call(owner, attr, None)
            try:
                number = int(float(value))
            except Exception:
                continue
            if number >= 0:
                return number
    return None


def _skill_snapshot(sim_info):
    """Read skills from both modern SimInfo collections and legacy trackers."""
    candidates = []
    seen = set()

    def add(values):
        for stat in values:
            if stat is None:
                continue
            identity = str(getattr(_skill_type(stat), "guid64", "") or id(stat))
            if identity not in seen:
                seen.add(identity)
                candidates.append(stat)

    add(_read(sim_info, ("all_skills", "skill_statistics", "sim_skills", "skills")))
    trackers = []
    for name in ("skill_tracker", "commodity_tracker", "statistic_tracker", "ranked_statistic_tracker"):
        tracker = getattr(sim_info, name, None)
        if tracker is not None and tracker not in trackers:
            trackers.append(tracker)
    for tracker in trackers:
        add(_read(tracker, ("all_skills", "skill_statistics", "all_statistics", "statistics")))
        add(_read(tracker, ("_statistics",), mapping_keys=False))

    result = []
    for stat in candidates:
        if not _is_skill(stat):
            continue
        tuning = _skill_type(stat)
        level = _skill_level(stat)
        if level is None:
            for owner in tuple([sim_info] + trackers):
                getter = getattr(owner, "get_statistic", None)
                if not callable(getter):
                    continue
                try:
                    live_stat = getter(tuning)
                except Exception:
                    live_stat = None
                if live_stat is not None:
                    level = _skill_level(live_stat)
                    if level is not None:
                        break
        if level is None or level < 1:
            continue
        label = _humanize(tuning, ("skill_", "skill "))
        if label and not any(item["name"] == label for item in result):
            result.append({"name": label, "level": level, "tuning_id": _tuning_id(tuning)})
    result.sort(key=lambda item: item["name"].lower())
    return result, bool(trackers or candidates)


def _milestone_tuning(value):
    for attr in ("milestone", "developmental_milestone", "milestone_type"):
        nested = getattr(value, attr, None)
        if nested is not None:
            return nested
    return value


def _milestone_snapshot(sim_info):
    """Use the current game's completed-milestone API with safe fallbacks."""
    tracker = getattr(sim_info, "developmental_milestone_tracker", None)
    if tracker is None:
        return [], False
    values = _read(tracker, (
        "get_all_completed_milestones", "completed_milestones",
        "all_completed_milestones",
    ), mapping_keys=True)
    if not values:
        archived = _read(tracker, ("archived_milestones", "_archived_milestones_data"), mapping_keys=True)
        active = _read(tracker, ("active_milestones", "_active_milestones_data"), mapping_keys=True)
        checker = getattr(tracker, "is_milestone_completed", None)
        accepted = list(archived)
        if callable(checker):
            for value in active:
                tuning = _milestone_tuning(value)
                try:
                    if checker(tuning):
                        accepted.append(tuning)
                except Exception:
                    pass
        values = accepted
    labels = []
    for value in values:
        label = _humanize(_milestone_tuning(value), ("milestone_", "developmental milestone "))
        if label and label not in labels:
            labels.append(label)
    labels.sort(key=lambda value: value.lower())
    return labels, True


def _milestone_details(sim_info):
    tracker = getattr(sim_info, "developmental_milestone_tracker", None)
    if tracker is None:
        return []
    values = _read(tracker, (
        "get_all_completed_milestones", "completed_milestones",
        "all_completed_milestones", "archived_milestones",
    ), mapping_keys=True)
    rows = []
    seen = set()
    for value in values:
        tuning = _milestone_tuning(value)
        label = _humanize(tuning, ("milestone_", "developmental milestone "))
        key = _tuning_id(tuning) or label.casefold()
        if label and key not in seen:
            seen.add(key)
            rows.append({"name": label, "tuning_id": _tuning_id(tuning)})
    return sorted(rows, key=lambda row: row["name"].casefold())


def _safe_call(owner, name, *args):
    if owner is None:
        return None
    method = getattr(owner, name, None)
    if not callable(method):
        return None
    try:
        return method(*args)
    except Exception:
        return None


def _safe_value(owner, names, default=None):
    if owner is None:
        return default
    for name in names:
        # Tuned game resources are Python classes and therefore callable, but
        # they are values here—not zero-argument methods to instantiate.
        raw = getattr(owner, name, None)
        if isinstance(raw, type):
            return raw
        value = _core._value_or_call(owner, name, None)
        if value is not None:
            return value
    return default


def _number(owner, names):
    value = _safe_value(owner, names, None)
    try:
        return round(float(value), 2)
    except Exception:
        return None


def _plain_number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except Exception:
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number) if number.is_integer() else round(number, 2)


def _funds_amount(household, fallback=None):
    """Unwrap Sims FamilyFunds objects into a stable JSON number."""
    raw = _safe_value(household, ("funds", "money"), fallback) if household is not None else fallback
    direct = _plain_number(raw)
    if direct is not None:
        return direct
    for name in ("money", "amount", "value", "household_funds", "available_funds"):
        nested = _safe_value(raw, (name,), None)
        number = _plain_number(nested)
        if number is not None:
            return number
    return _plain_number(fallback)


def _json_safe(value, depth=0):
    """Return protocol data containing JSON primitives only.

    Sims services occasionally return proxy objects even for values documented
    as numbers.  The protocol normalizes known numeric wrappers and degrades an
    unexpected leaf to a short readable string instead of losing the report.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return _plain_number(value)
    if depth >= 12:
        return str(type(value).__name__)
    if isinstance(value, dict):
        return {str(key): _json_safe(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item, depth + 1) for item in value]
    numeric = _funds_amount(None, value)
    if numeric is not None:
        return numeric
    tuning = _tuning_id(value)
    if tuning:
        return str(tuning)
    label = _humanize(value)
    if label:
        return label[:240]
    try:
        return str(value)[:240]
    except Exception:
        return str(type(value).__name__)


def _sim_reference(sim_info):
    if sim_info is None:
        return None
    raw_id = sim_info if isinstance(sim_info, int) or (
        isinstance(sim_info, str) and sim_info.isdigit()
    ) else None
    for attr in ("sim_info", "target_sim_info"):
        nested = getattr(sim_info, attr, None)
        if nested is not None:
            sim_info = nested
            break
    game_id = _safe_value(sim_info, ("sim_id", "id"), raw_id)
    if game_id is None:
        return None
    first = str(_safe_value(sim_info, ("first_name",), "") or "").strip()
    last = str(_safe_value(sim_info, ("last_name",), "") or "").strip()
    return {
        "game_sim_id": str(game_id),
        "first_name": first,
        "last_name": last,
        "name": " ".join(value for value in (first, last) if value),
        "sex": _humanize(_safe_value(sim_info, ("gender", "sex"), None), ("Gender.",)),
        "age_stage": _humanize(_safe_value(sim_info, ("age",), None), ("Age.",)),
    }


def _named_collection(owner, names, prefixes=()):
    values = _read(owner, names, mapping_keys=False)
    rows = []
    seen = set()
    for value in values:
        label = _humanize(value, prefixes)
        if not label or label.casefold() in seen:
            continue
        seen.add(label.casefold())
        rows.append(label)
    rows.sort(key=lambda item: item.casefold())
    return rows


def _pregnancy_details(sim_info):
    tracker = getattr(sim_info, "pregnancy_tracker", None)
    if tracker is None:
        return {}, False
    stage = _humanize(_safe_value(tracker, (
        "pregnancy_stage", "current_stage", "trimester", "pregnancy_trimester",
    ), None), ("Pregnancy.", "Trimester."))
    remaining = _number(tracker, (
        "hours_remaining", "pregnancy_hours_remaining", "time_until_birth",
    ))
    progress = _number(tracker, (
        "pregnancy_progress_percentage", "pregnancy_progress", "progress",
    ))
    if progress is not None and progress <= 1:
        progress = round(progress * 100, 2)
    labor = bool(_safe_value(tracker, ("is_in_labor", "in_labor", "is_giving_birth"), False))
    expected = _safe_value(tracker, (
        "offspring_count", "pregnancy_offspring_count", "expected_offspring_count",
    ), None)
    try:
        expected = int(expected) if expected is not None else None
    except Exception:
        expected = None
    return {
        "pregnancy_stage": stage or None,
        "pregnancy_progress_percentage": progress,
        "pregnancy_hours_remaining": remaining,
        "is_in_labor": labor,
        "babies_expected": expected,
        "pregnancy_scan_supported": True,
    }, True


def _genealogy_details(sim_info):
    genealogy = getattr(sim_info, "genealogy", None)
    source = genealogy or sim_info
    groups = (
        ("children", "child_game_sim_ids", ("get_children", "children", "get_child_sim_infos")),
        ("siblings", "sibling_game_sim_ids", ("get_siblings", "siblings", "get_sibling_sim_infos")),
        ("grandparents", "grandparent_game_sim_ids", ("get_grandparents", "grandparents")),
        ("grandchildren", "grandchild_game_sim_ids", ("get_grandchildren", "grandchildren")),
    )
    result = {}
    supported = genealogy is not None
    for field, id_field, names in groups:
        rows = []
        seen = set()
        for value in _read(source, names):
            reference = _sim_reference(value)
            if not reference or reference["game_sim_id"] in seen:
                continue
            seen.add(reference["game_sim_id"])
            rows.append(reference)
        result[field] = rows
        result[id_field] = [
            row["game_sim_id"] for row in rows
        ]
    result["genealogy_scan_supported"] = supported
    return result, supported


def _relationship_category(bits, fallback="Relationship"):
    text = " ".join(bits).casefold()
    for markers, category in (
        (("widow", "widower"), "Widowed"),
        (("divorc", "ex spouse", "ex_spouse"), "Divorced"),
        (("married", "spouse", "marriage"), "Marriage"),
        (("fiance", "engaged", "betroth"), "Engagement"),
        (("parent", "child", "sibling", "grandparent", "family"), "Family"),
        (("romance", "romantic", "lover", "partner"), "Romantic"),
        (("friend", "acquaintance"), "Friendship"),
    ):
        if any(marker in text for marker in markers):
            return category
    return fallback or "Relationship"


def _relationship_details(sim_info, existing):
    tracker = getattr(sim_info, "relationship_tracker", None)
    if tracker is None:
        return {"relationships": existing or [], "relationship_scan_supported": False}, False
    by_id = {
        str(row.get("other_game_sim_id") or ""): dict(row)
        for row in (existing or []) if isinstance(row, dict) and row.get("other_game_sim_id")
    }
    targets = _read(tracker, ("get_target_sim_infos", "target_sim_infos", "relationships"))
    for target in targets:
        reference = _sim_reference(target)
        if not reference:
            continue
        other_id = reference["game_sim_id"]
        bits = ()
        for argument in (other_id, target):
            values = _safe_call(tracker, "get_all_bits", argument)
            if values is not None:
                bits = _as_values(values)
                if bits:
                    break
        bit_labels = []
        bit_details = []
        for value in bits:
            label = _humanize(value, ("relationshipBit_", "relationship_bit_"))
            if label and label not in bit_labels:
                bit_labels.append(label)
                bit_details.append({"name": label, "tuning_id": _tuning_id(value)})
        row = by_id.get(other_id, {})
        category = _relationship_category(bit_labels, row.get("category", "Relationship"))
        friendship = None
        romance = None
        for name, field in (("get_friendship_score", "friendship"), ("get_romance_score", "romance")):
            value = _safe_call(tracker, name, other_id)
            try:
                value = round(float(value), 1) if value is not None else None
            except Exception:
                value = None
            if field == "friendship":
                friendship = value
            else:
                romance = value
        by_id[other_id] = dict(row, **reference)
        by_id[other_id].update({
            "other_game_sim_id": other_id,
            "category": category,
            "relationship_bits": bit_labels,
            "relationship_bit_details": bit_details,
            "friendship_score": friendship,
            "romance_score": romance,
        })
    rows = sorted(by_id.values(), key=lambda row: (str(row.get("category") or ""), str(row.get("name") or "")))
    return {"relationships": rows, "relationship_scan_supported": True}, True


_HEALTH_WORDS = (
    "illness", "disease", "infection", "fever", "symptom", "sick", "malaria",
    "measles", "pox", "plague", "cholera", "flu", "influenza", "cold", "pneumonia",
    "hemorrhage", "haemorrhage", "postpartum", "nausea", "cough", "pain",
)


# Healthcare Redux exposes its active diseases through ordinary Sims buff and
# trait trackers.  Matching those public game objects keeps the integration
# optional: no Healthcare Redux module is imported and an absent mod simply
# contributes no matching markers.  Specific aliases come before broad ones so
# stomach flu is not mistaken for influenza and gestational diabetes retains
# its own name.
_HEALTH_CONDITION_ALIASES = (
    (("urinarytractinfection", "urinary tract infection", "utibuff", "utitrait", "gynecologybuffuti"), "Urinary Tract Infection"),
    (("yeastinfection", "yeast infection"), "Yeast Infection"),
    (("pregnancyinducedanemia", "pregnancy induced anemia", "pregnancyrelatedanemia"), "Pregnancy-Induced Anemia"),
    (("gestationaldiabetes", "gestational diabetes"), "Gestational Diabetes"),
    (("postpartumdepression", "postpartum depression"), "Postpartum Depression"),
    (("postpartumhemorrhage", "postpartumhaemorrhage", "postpartum hemorrhage", "postpartum haemorrhage"), "Postpartum Hemorrhage"),
    (("seasonalaffectivedisorder", "seasonal affective disorder"), "Seasonal Affective Disorder"),
    (("borderlinepersonalitydisorder", "borderline personality disorder"), "Borderline Personality Disorder"),
    (("obsessivecompulsivedisorder", "obsessive compulsive disorder"), "Obsessive Compulsive Disorder"),
    (("flatheadsyndrome", "flat head syndrome"), "Flat Head Syndrome"),
    (("gastroenteritis", "stomachflu", "stomach flu"), "Gastroenteritis"),
    (("earinfection", "ear infection"), "Ear Infection"),
    (("whoopingcough", "whooping cough", "pertussis"), "Whooping Cough"),
    (("breastcancer", "breast cancer"), "Breast Cancer"),
    (("coloncancer", "colon cancer"), "Colon Cancer"),
    (("prostatecancer", "prostate cancer"), "Prostate Cancer"),
    (("kidneydisease", "kidney disease"), "Kidney Disease"),
    (("kidneyfailure", "kidney failure"), "Kidney Failure"),
    (("heartattack", "heart attack"), "Heart Attack"),
    (("bloodclot", "blood clot"), "Blood Clot"),
    (("pulmonaryembolism", "pulmonary embolism"), "Pulmonary Embolism"),
    (("animaldanderallergy", "animal dander allergy", "petdanderallergy", "pet dander allergy"), "Animal Dander Allergy"),
    (("beeallergy", "bee allergy"), "Bee Allergy"),
    (("influenza", "flubuff", "flutrait", "flu buff", "flu trait"), "Influenza"),
    (("tuberculosis",), "Tuberculosis"),
    (("meningitis",), "Meningitis"),
    (("pneumonia",), "Pneumonia"),
    (("tonsillitis",), "Tonsillitis"),
    (("bronchitis",), "Bronchitis"),
    (("sinusitis", "sinus infection"), "Sinusitis"),
    (("malaria",), "Malaria"),
    (("commoncold", "coldbuff", "coldtrait", "common cold"), "Cold"),
    (("appendicitis",), "Appendicitis"),
    (("diphtheria",), "Diphtheria"),
    (("dysentery",), "Dysentery"),
    (("smallpox", "small pox"), "Smallpox"),
    (("chickenpox", "chicken pox"), "Chicken Pox"),
    (("scarletfever", "scarlet fever"), "Scarlet Fever"),
    (("yellowfever", "yellow fever"), "Yellow Fever"),
    (("typhoid",), "Typhoid"),
    (("typhus",), "Typhus"),
    (("cholera",), "Cholera"),
    (("measles",), "Measles"),
    (("mumps",), "Mumps"),
    (("polio",), "Polio"),
    (("rabies",), "Rabies"),
    (("tetanus",), "Tetanus"),
    (("cancer",), "Cancer"),
    (("severeanemia", "anemia", "anaemia"), "Anemia"),
    (("hypertension",), "Hypertension"),
    (("diabetes",), "Diabetes"),
    (("asthma",), "Asthma"),
    (("arthritis",), "Arthritis"),
    (("eczema",), "Eczema"),
    (("migraine",), "Migraine"),
    (("sleepdisorder", "sleep disorder"), "Sleep Disorder"),
    (("anxiety",), "Anxiety"),
    (("depression",), "Depression"),
    (("deafness",), "Deafness"),
    (("allergy",), "Allergy"),
    (("sepsis",), "Sepsis"),
    (("hemorrhage", "haemorrhage"), "Hemorrhage"),
)

_INACTIVE_HEALTH_MARKERS = (
    "recent", "immune", "immunity", "immunization", "immunisation",
    "vaccin", "recovered", "resolved", "remission", "survivor", "cancerfree",
    "prescribed", "prescription", "medication", "medicine", "pillstaken", "taken",
    "treatment", "therapy", "cooldown", "chance", "loot", "testset", "broadcaster",
    "commodity", "enabled", "enabler", "notification", "mixer", "remove", "cleanup",
    "diagnostics", "appointment", "followup", "surgery", "postop", "death", "dying",
    "management", "coretrait", "undetected", "unknown", "undiagnosed",
)

# Healthcare Redux can leave the progression/stage buff active while its
# disease-specific buff is unavailable.  That state is medically meaningful
# but must not be guessed as malaria, meningitis or tuberculosis.  Report a
# clearly pending diagnosis so the tracker does not silently treat the Sim as
# healthy.  Exact disease aliases still take precedence below.
_PENDING_HEALTH_ALIASES = (
    (("deadlydiseasecommodity", "deadly disease commodity"), "Deadly Disease — diagnosis pending"),
    (("viraldiseasecommodity", "viral disease commodity"), "Viral Disease — diagnosis pending"),
    (("bacterialinfectioncommodity", "bacterial infection commodity"), "Bacterial Infection — diagnosis pending"),
    (("undiagnosedillnessbuff", "undiagnosed illness buff", "corehasillness"), "Illness — diagnosis pending"),
)


def _health_marker_text(value):
    return " ".join((
        _humanize(value, ("buff_", "trait_")),
        str(_core._tuning_text(value) or ""),
    )).casefold()


def _health_condition_name(value, provider=""):
    text = _health_marker_text(value)
    compact = re.sub(r"[^a-z0-9]+", "", text)
    if not compact:
        return ""
    for aliases, canonical in _PENDING_HEALTH_ALIASES:
        for alias in aliases:
            folded = alias.casefold()
            if folded in text or re.sub(r"[^a-z0-9]+", "", folded) in compact:
                return canonical
    if any(marker in compact for marker in _INACTIVE_HEALTH_MARKERS):
        return ""
    for aliases, canonical in _HEALTH_CONDITION_ALIASES:
        for alias in aliases:
            folded = alias.casefold()
            if folded in text or re.sub(r"[^a-z0-9]+", "", folded) in compact:
                return canonical
    label = _humanize(value, ("buff_", "trait_"))
    # Preserve existing base-game/custom illness reporting, but only accept a
    # Healthcare Redux marker when it names a known condition above.  This
    # prevents its generic symptom, visit and medication buffs from creating
    # false illness episodes.
    if provider != "Healthcare Redux" and label and any(word in label.casefold() for word in _HEALTH_WORDS):
        return label
    return ""


def _health_details(sim_info):
    # BuffComponent belongs to SimInfo as ``Buffs`` on current game builds,
    # while older builds and test doubles expose ``buff_component``.  Some
    # releases also keep the component only on the instantiated Sim.  Read all
    # three forms so hidden Healthcare Redux disease buffs are not missed.
    components = []
    sim_instance = _safe_call(sim_info, "get_sim_instance")
    for owner in (sim_info, sim_instance):
        if owner is None:
            continue
        for name in ("buff_component", "Buffs"):
            component = getattr(owner, name, None)
            try:
                component = component() if callable(component) and not isinstance(component, type) else component
            except Exception:
                component = None
            if component is not None and component not in components:
                components.append(component)

    buffs = []
    buff_keys = set()

    def add_buffs(values):
        for buff in _as_values(values):
            if buff is None:
                continue
            tuning = getattr(buff, "buff_type", None) or getattr(buff, "tuning", None) or buff
            key = _tuning_id(tuning) or str(id(tuning))
            if key not in buff_keys:
                buff_keys.add(key)
                buffs.append(buff)

    for component in components:
        # get_active_buff_types is the supported current-game API. The other
        # collections retain compatibility with older patches and mod wrappers.
        add_buffs(_safe_call(component, "get_active_buff_types"))
        add_buffs(_read(component, ("get_all_buffs", "buffs", "active_buffs")))
        add_buffs(_read(component, ("_active_buffs",), mapping_keys=False))
        try:
            add_buffs(component)
        except Exception:
            pass
    trait_tracker = getattr(sim_info, "trait_tracker", None)
    traits = []
    trait_keys = set()
    if trait_tracker is not None:
        for collection_name in ("equipped_traits", "traits", "get_traits"):
            for trait in _read(trait_tracker, (collection_name,)):
                key = _tuning_id(trait) or str(id(trait))
                if key not in trait_keys:
                    trait_keys.add(key)
                    traits.append(trait)
    rows = []
    for marker, source_kind in tuple((buff, "active_buff") for buff in buffs) + tuple((trait, "trait") for trait in traits):
        tuning = getattr(marker, "buff_type", None) or getattr(marker, "tuning", None) or marker
        provider = _tuning_provider(tuning)
        name = _health_condition_name(tuning, provider)
        if not name:
            continue
        rows.append({
            "name": name,
            "raw_name": _humanize(tuning, ("buff_", "trait_")),
            "tuning_id": _tuning_id(tuning),
            "remaining_minutes": _number(marker, ("remaining_time", "time_remaining", "remaining_minutes")),
            "severity": _humanize(_safe_value(marker, ("severity",), None)),
            "provider": provider,
            "source_kind": source_kind,
        })
    unique = []
    seen = {}
    for row in rows:
        key = row["name"].casefold()
        if key not in seen:
            seen[key] = row
            unique.append(row)
        elif row.get("source_kind") == "trait":
            # A diagnosed trait is more stable than a transient symptom buff.
            seen[key]["source_kind"] = "trait+active_buff"
            if not seen[key].get("tuning_id"):
                seen[key]["tuning_id"] = row.get("tuning_id")
    pending_suffix = "— diagnosis pending"
    if any(not row["name"].endswith(pending_suffix) for row in unique):
        unique = [row for row in unique if not row["name"].endswith(pending_suffix)]
    elif any(row["name"].startswith("Deadly Disease") for row in unique):
        unique = [row for row in unique if not row["name"].startswith("Illness —")]
    return {
        "health_buffs": unique,
        "symptoms": [row["name"] for row in unique],
        "health_scan_supported": bool(components) or trait_tracker is not None,
        "healthcare_redux_detected": any(row.get("provider") == "Healthcare Redux" for row in unique),
    }, bool(components) or trait_tracker is not None


def _tuning_provider(value):
    text = _core._tuning_text(value).casefold()
    if "adeepindigo" in text or "healthcare" in text:
        return "Healthcare Redux"
    if "severaludo" in text:
        return "SeveralUDO"
    return "The Sims 4"


def _life_stage_details(sim_info):
    days = _number(sim_info, ("age_in_days", "age_days", "sim_age_days"))
    progress = _number(sim_info, ("age_progress_percentage", "age_progress", "life_stage_progress"))
    if progress is not None and progress <= 1:
        progress = round(progress * 100, 2)
    remaining = _number(sim_info, (
        "days_until_ready_to_age", "days_until_age_up", "get_days_until_ready_to_age",
    ))
    ready = bool(_safe_value(sim_info, ("is_ready_to_age", "ready_to_age"), False))
    return {
        "age_days": days,
        "age_progress_percentage": progress,
        "days_until_age_up": remaining,
        "age_transition_ready": ready,
        "life_stage_scan_supported": days is not None or progress is not None or remaining is not None,
    }, days is not None or progress is not None or remaining is not None


def _career_row(career):
    tuning = getattr(career, "career_tuning", None) or getattr(career, "current_track_tuning", None) or career
    return {
        "name": _humanize(tuning, ("career_",)),
        "tuning_id": _tuning_id(tuning),
        "title": _humanize(_safe_value(career, ("current_level_tuning", "job_title", "title"), None)),
        "branch": _humanize(_safe_value(career, ("current_track_tuning", "career_track", "branch"), None)),
        "level": _number(career, ("level", "career_level", "user_level")),
        "performance": _number(career, ("performance", "career_performance", "work_performance")),
        "retired": bool(_safe_value(career, ("is_retired", "retired"), False)),
    }


def _career_education_details(sim_info):
    career_tracker = getattr(sim_info, "career_tracker", None)
    careers = [_career_row(value) for value in _read(career_tracker, ("careers", "career_list", "active_careers"))]
    careers = [row for row in careers if row["name"]]
    degree_tracker = getattr(sim_info, "degree_tracker", None)
    degrees = _named_collection(degree_tracker, ("get_all_degrees", "degrees", "completed_degrees"), ("degree_",))
    school = _humanize(_safe_value(sim_info, ("school_data", "school_career", "education"), None))
    return {
        "careers": careers,
        "degrees": degrees,
        "school": school or None,
        "career_scan_supported": career_tracker is not None,
        "education_scan_supported": degree_tracker is not None or bool(school),
    }, career_tracker is not None or degree_tracker is not None or bool(school)


def _occult_progress_details(sim_info):
    tracker = getattr(sim_info, "occult_tracker", None)
    if tracker is None:
        return {"occult_progress": {}, "occult_progress_scan_supported": False}, False
    rank = _humanize(_safe_value(tracker, ("occult_rank", "rank", "current_rank"), None))
    perks = _named_collection(tracker, ("unlocked_perks", "perks", "owned_perks"), ("perk_",))
    weaknesses = _named_collection(tracker, ("weaknesses", "unlocked_weaknesses"), ("weakness_",))
    abilities = _named_collection(tracker, ("abilities", "unlocked_abilities", "powers"), ("ability_", "power_"))
    curses = _named_collection(tracker, ("curses", "active_curses"), ("curse_",))
    return {
        "occult_progress": {
            "rank": rank or None, "perks": perks, "weaknesses": weaknesses,
            "abilities": abilities, "curses": curses,
        },
        "occult_progress_scan_supported": True,
    }, True


def _personal_development_details(sim_info):
    aspiration_tracker = getattr(sim_info, "aspiration_tracker", None)
    active = _humanize(_safe_value(aspiration_tracker, (
        "active_aspiration", "current_aspiration", "primary_aspiration",
    ), None), ("aspiration_",))
    completed = _named_collection(aspiration_tracker, (
        "completed_aspirations", "get_completed_aspirations", "completed_milestones",
    ), ("aspiration_",))
    traits = _named_collection(getattr(sim_info, "trait_tracker", None), (
        "equipped_traits", "traits", "get_traits",
    ), ("trait_",))
    lifestyles = sorted(value for value in traits if "lifestyle" in value.casefold())
    fears = sorted(value for value in traits if "fear" in value.casefold())
    character_values = sorted(value for value in traits if any(marker in value.casefold() for marker in (
        "manners", "responsibility", "empathy", "conflict resolution", "emotional control",
    )))
    preferences = _named_collection(sim_info, (
        "preferences", "likes", "dislikes", "get_preferences",
    ), ("preference_",))
    return {
        "aspirations": ([active] if active else []) + [value for value in completed if value != active],
        "active_aspiration": active or None,
        "completed_aspirations": completed,
        "lifestyles": lifestyles,
        "fears": fears,
        "character_values": character_values,
        "preferences": preferences,
        "personal_development_scan_supported": aspiration_tracker is not None or bool(traits) or bool(preferences),
    }, aspiration_tracker is not None or bool(traits) or bool(preferences)


def _death_details(sim_info, result):
    raw_death_type = _safe_value(sim_info, ("death_type",), None)
    death_type = result.get("death_type") or _humanize(raw_death_type)
    occult_types = [str(value) for value in (result.get("occult_types") or [])]
    is_ghost = bool(_safe_value(sim_info, ("is_ghost",), False)) or any(
        "ghost" in value.casefold() for value in occult_types
    )
    return {
        "death_type": death_type or None,
        "death_type_id": _tuning_id(raw_death_type),
        "death_details": {
            "death_type": death_type or None,
            "death_type_id": _tuning_id(raw_death_type),
            "is_ghost": is_ghost,
            "place": result.get("lot_name") or None,
            "world": result.get("world_name") or None,
        },
        "is_ghost": is_ghost,
        "death_scan_supported": "is_dead" in result,
    }, "is_dead" in result


def _named_details(owner, names, prefixes=()):
    rows = []
    seen = set()
    for value in _read(owner, names, mapping_keys=False):
        label = _humanize(value, prefixes)
        key = _tuning_id(value) or label.casefold()
        if label and key not in seen:
            seen.add(key)
            rows.append({"name": label, "tuning_id": _tuning_id(value)})
    return sorted(rows, key=lambda row: row["name"].casefold())


def _stable_tuning_details(sim_info, result):
    trait_rows = _named_details(getattr(sim_info, "trait_tracker", None),
                                ("equipped_traits", "traits", "get_traits"), ("trait_",))
    degree_rows = _named_details(getattr(sim_info, "degree_tracker", None),
                                 ("get_all_degrees", "degrees", "completed_degrees"), ("degree_",))
    aspiration_rows = _named_details(getattr(sim_info, "aspiration_tracker", None),
                                     ("completed_aspirations", "get_completed_aspirations"), ("aspiration_",))
    return {
        "skill_details": [dict(row) for row in (result.get("skills") or []) if isinstance(row, dict)],
        "milestone_details": _milestone_details(sim_info),
        "trait_details": trait_rows,
        "degree_details": degree_rows,
        "aspiration_details": aspiration_rows,
        "stable_tuning_ids": {
            "skills": {row.get("name"): row.get("tuning_id") for row in (result.get("skills") or []) if isinstance(row, dict) and row.get("tuning_id")},
            "milestones": {row.get("name"): row.get("tuning_id") for row in _milestone_details(sim_info) if row.get("tuning_id")},
            "traits": {row.get("name"): row.get("tuning_id") for row in trait_rows if row.get("tuning_id")},
            "careers": {row.get("name"): row.get("tuning_id") for row in (result.get("careers") or []) if isinstance(row, dict) and row.get("tuning_id")},
            "illnesses": {row.get("name"): row.get("tuning_id") for row in (result.get("health_buffs") or []) if isinstance(row, dict) and row.get("tuning_id")},
        },
    }


def _resource_key(value):
    if value is None:
        return None
    result = {}
    for source, field in (("instance", "instance"), ("type", "type"), ("group", "group")):
        item = getattr(value, source, None)
        if item is not None:
            result[field] = str(item)
    return result or None


def _portrait_details(sim_info, result):
    config = _safe_call(_core, "_load_config")
    if isinstance(config, dict) and config.get("capture_portraits") is False:
        return {
            "game_portrait": {"available": False, "capture_mode": "disabled"},
            "portrait_image_base64": None,
            "portrait_mime_type": None,
            "portrait_scan_supported": True,
        }, True
    raw = _safe_value(sim_info, ("portrait_data", "portrait_bytes", "thumbnail_bytes"), None)
    payload = None
    # Keep relay reports small enough for modest Railway plans. The game does
    # not expose portrait bytes on every build, so a resource reference still
    # reports availability without attempting an unsafe file scrape.
    if isinstance(raw, (bytes, bytearray)) and 100 < len(raw) <= 250000:
        payload = base64.b64encode(bytes(raw)).decode("ascii")
    icon = _safe_value(sim_info, ("get_icon_info_data", "icon_info", "portrait_key"), None)
    key = _resource_key(icon)
    return {
        "game_portrait": {
            "available": bool(payload or key),
            "resource_key": key,
            "capture_mode": "embedded" if payload else ("resource-reference" if key else "unavailable"),
            "life_stage": result.get("age_stage"),
        },
        "portrait_image_base64": payload,
        "portrait_mime_type": "image/png" if payload else None,
        "portrait_scan_supported": raw is not None or icon is not None,
    }, raw is not None or icon is not None


def _environment_diagnostics(capabilities, errors):
    packs = []
    game_build = ""
    try:
        import sims4.common
        packs = sorted(_humanize(value, ("Pack.",)) for value in sims4.common.get_available_packs())
    except Exception:
        pass
    try:
        import sims4.version
        game_build = str(getattr(sims4.version, "version", "") or "")
    except Exception:
        pass
    optional_markers = {
        "Healthcare Redux": ("adeepindigo", "healthcare"),
        "MC Command Center": ("mc_cmd_center", "mc_utils"),
        "SeveralUDO Healthcare": ("severaludo_healthcare",),
    }
    loaded = tuple(str(name).casefold() for name in sys.modules)
    mods = sorted(label for label, markers in optional_markers.items()
                  if any(any(marker in module for marker in markers) for module in loaded))
    return {
        "clock_sync_version": VERSION,
        "game_build": game_build or None,
        "installed_packs": [value for value in packs if value],
        "detected_optional_mods": mods,
        "telemetry_capabilities": capabilities,
        "clock_sync_diagnostics": {
            "version": VERSION,
            "telemetry_version": 5,
            "errors": errors,
            "healthy": not errors,
        },
    }


def _extended_snapshot(sim_info, household):
    result = _previous_extended_snapshot(sim_info, household)
    skills, skills_supported = _skill_snapshot(sim_info)
    milestones, milestones_supported = _milestone_snapshot(sim_info)
    result.update({
        "skills": skills,
        "skills_scan_supported": skills_supported,
        "milestones": milestones,
        "milestone_scan_supported": milestones_supported,
        "telemetry_version": 5,
    })
    capabilities = {
        "skills": skills_supported,
        "milestones": milestones_supported,
    }
    errors = []
    modules = (
        ("pregnancy", lambda: _pregnancy_details(sim_info)),
        ("genealogy", lambda: _genealogy_details(sim_info)),
        ("relationships", lambda: _relationship_details(sim_info, result.get("relationships") or [])),
        ("health", lambda: _health_details(sim_info)),
        ("life_stage", lambda: _life_stage_details(sim_info)),
        ("career_education", lambda: _career_education_details(sim_info)),
        ("occult_progress", lambda: _occult_progress_details(sim_info)),
        ("personal_development", lambda: _personal_development_details(sim_info)),
        ("death", lambda: _death_details(sim_info, result)),
        ("portraits", lambda: _portrait_details(sim_info, result)),
    )
    for name, reader in modules:
        try:
            values, supported = reader()
            result.update(values)
            capabilities[name] = bool(supported)
        except Exception as exc:
            capabilities[name] = False
            errors.append({"feature": name, "error": type(exc).__name__})
    # A number of base-game and optional-mod illnesses are represented only by
    # active buffs. Promote guarded health matches into the same illness
    # contract used by the tracker, while retaining any richer illnesses read
    # by the compatibility layer.
    illnesses = {}
    for row in result.get("illnesses") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        key = str(row.get("source_key") or name).strip().casefold()
        if name and key:
            illnesses[key] = dict(row, source_key=key)
    for row in result.get("health_buffs") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        key = ("buff:" + name).casefold()
        if name and key not in illnesses:
            illnesses[key] = {
                "source_key": key,
                "name": name,
                "severity": row.get("severity") or "Unrated",
                "provider": row.get("provider") or "The Sims 4",
                "symptoms": [name],
                "health_buffs": [row],
            }
    if capabilities.get("health"):
        result["illness_scan_supported"] = True
        result["illnesses"] = list(illnesses.values())
    result.update(_stable_tuning_details(sim_info, result))
    capabilities["stable_tuning_ids"] = True
    result.update(_environment_diagnostics(capabilities, errors))
    return result


def _config_folder():
    return os.path.dirname(_core._config_path())


def _state_path():
    return os.path.join(_config_folder(), "clock_sync_state.json")


def _load_protocol_state():
    try:
        handle = open(_state_path(), "r")
        try:
            value = json.load(handle)
            return value if isinstance(value, dict) else {}
        finally:
            handle.close()
    except Exception:
        return {}


def _write_json_atomic(path, value):
    # ModGuard permits JSON writes but deliberately blocks unknown temporary
    # extensions in protected Windows paths.  Keep the staging file visibly
    # JSON as well, then atomically replace the destination as before.
    temporary = path + ".writing.json"
    handle = open(temporary, "w")
    if handle is None:
        raise IOError("The local Clock Sync queue could not be opened")
    try:
        handle.write(json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":")))
    finally:
        handle.close()
    os.replace(temporary, path)


def _save_protocol_state(value):
    try:
        _write_json_atomic(_state_path(), value)
    except Exception as error:
        _core.LOGGER.warn("Clock Sync state could not be written: {}", error)


def _canonical_checksum(value):
    body = _json_safe(dict(value))
    body.pop("report_checksum", None)
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _game_time_details():
    details = {"game_second": 0, "game_ticks": None}
    try:
        sim_now = _core.services.time_service().sim_now
        second = _safe_value(sim_now, ("second",), None)
        ticks = _safe_value(sim_now, ("absolute_ticks", "ticks"), None)
        if second is not None:
            details["game_second"] = max(0, min(59, int(second)))
        if ticks is not None:
            details["game_ticks"] = int(ticks)
    except Exception:
        pass
    return details


def _save_identity(config=None):
    config = config or {}
    slot_id = None
    slot_name = None
    try:
        persistence = _safe_call(_core.services, "get_persistence_service")
        slot_proto = _safe_value(persistence, (
            "save_slot_proto", "current_save_slot_proto", "save_slot_data",
        ), None)
        owners = (persistence, slot_proto)
        for owner in owners:
            if slot_id in (None, ""):
                slot_id = _safe_value(owner, (
                    "save_slot_id", "current_save_slot_id", "active_save_slot_id",
                    "save_slot_guid", "slot_id", "guid",
                ), None)
            if slot_name in (None, ""):
                slot_name = _safe_value(owner, (
                    "save_slot_name", "current_save_game_name", "save_game_name", "slot_name", "name",
                ), None)
    except Exception:
        pass
    if slot_id in (None, ""):
        slot_id = config.get("save_slot_id")
    if slot_name in (None, ""):
        slot_name = config.get("save_slot_name")
    explicit = str(config.get("save_identity") or "").strip()
    if explicit:
        identity = explicit
    elif slot_id not in (None, "") or slot_name not in (None, ""):
        source = "{}|{}".format(slot_id if slot_id is not None else "", slot_name or "")
        identity = hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]
    else:
        identity = ""
    return identity, (str(slot_id) if slot_id not in (None, "") else None), (str(slot_name) if slot_name else None)


def _household_id(household):
    value = _safe_value(household, ("household_id", "id"), None)
    return str(value) if value is not None else ""


def _household_members(household):
    try:
        return tuple(household)
    except Exception:
        return _read(household, ("sim_infos", "members"))


def _snapshot_member(sim_info, household, active_household):
    age_text = str(getattr(sim_info, "age", "") or "").lower()
    baby_value = getattr(sim_info, "is_baby", False)
    try:
        baby_value = baby_value() if callable(baby_value) else baby_value
    except Exception:
        baby_value = False
    member = {
        "game_sim_id": str(getattr(sim_info, "sim_id", "") or ""),
        "first_name": str(getattr(sim_info, "first_name", "") or ""),
        "last_name": str(getattr(sim_info, "last_name", "") or ""),
        "sex": str(getattr(sim_info, "gender", "") or ""),
        "age_stage": str(getattr(sim_info, "age", "Unknown") or "Unknown"),
        "is_baby": bool(baby_value or "baby" in age_text or "newborn" in age_text),
    }
    member.update(_core._pregnancy_snapshot(sim_info))
    member.update(_core._illness_snapshot(sim_info))
    member.update(_extended_snapshot(sim_info, household))
    members = _household_members(household)
    household_id = _household_id(household)
    member["household_id"] = household_id
    member["household_name"] = str(_safe_value(household, ("name",), "") or "")
    member["household_funds"] = _funds_amount(household, member.get("household_funds"))
    member["household_member_game_ids"] = [
        str(getattr(item, "sim_id", "") or "") for item in members if getattr(item, "sim_id", None)
    ]
    member["household_is_player"] = bool(_safe_value(household, ("is_player_household", "is_played_household"), False))
    member["household_is_unplayed"] = bool(_safe_value(household, ("is_unplayed",), False))
    head = _safe_value(household, ("head_of_household", "household_head", "last_played_sim_info"), None)
    member["is_household_head"] = bool(head is not None and str(getattr(head, "sim_id", "")) == member["game_sim_id"])
    if active_household is not None and household is not active_household:
        # The current zone belongs to the active household; do not attach it to
        # played Sims who are not loaded on that lot.
        member["lot_name"] = None
        member["world_name"] = None
        member["portrait_image_base64"] = None
        if isinstance(member.get("game_portrait"), dict):
            member["game_portrait"] = dict(member["game_portrait"], capture_mode="resource-reference")
    return member


def _played_households():
    active = _safe_call(_core.services, "active_household")
    households = []
    if active is not None:
        households.append(active)
    manager = _safe_call(_core.services, "household_manager")
    manager_supported = False
    candidates = _as_values(manager, mapping_keys=False) if manager is not None else ()
    if manager is not None and not candidates:
        candidates = _read(manager, ("values", "households"))
    manager_supported = bool(manager is not None and candidates)
    for household in candidates:
        if household is None or household in households:
            continue
        player = bool(_safe_value(household, ("is_player_household", "is_played_household"), False))
        unplayed = _safe_value(household, ("is_unplayed",), None)
        if player or unplayed is False:
            households.append(household)
    return active, households, manager_supported


def _played_population_snapshot():
    active, households, supported = _played_households()
    if not households:
        name, members = _previous_household_snapshot()
        return name, members, False, []
    members = []
    summaries = []
    for household in households:
        rows = _household_members(household)
        household_id = _household_id(household)
        summaries.append({
            "game_household_id": household_id,
            "name": str(_safe_value(household, ("name",), "") or ""),
            "member_game_ids": [str(getattr(item, "sim_id", "") or "") for item in rows if getattr(item, "sim_id", None)],
            "funds": _funds_amount(household),
            "is_player": bool(_safe_value(household, ("is_player_household", "is_played_household"), False)),
            "is_unplayed": bool(_safe_value(household, ("is_unplayed",), False)),
        })
        for sim_info in rows:
            try:
                member = _snapshot_member(sim_info, household, active)
                if member.get("game_sim_id"):
                    members.append(member)
            except Exception as error:
                _core.LOGGER.warn("Played Sim snapshot skipped safely: {}", error)
    active_name = str(_safe_value(active, ("name",), "") or "") if active is not None else ""
    return active_name, members, bool(supported), summaries


def _member_hash(member):
    # Embedded images live in their own portrait table. Excluding their bytes
    # from delta comparison keeps hashing inexpensive without losing updates:
    # game_portrait metadata still changes when a portrait becomes available.
    comparable = dict(member)
    comparable.pop("portrait_image_base64", None)
    encoded = json.dumps(_json_safe(comparable), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _protocol_report(game_day, game_hour, game_minute, household_name, members,
                     population_complete=False, population_households=None, force_full=False, config=None):
    config = config or {}
    state = _load_protocol_state()
    identity, slot_id, slot_name = _save_identity(config)
    prior_hashes = dict(state.get("member_hashes") or {})
    current_hashes = {
        str(item.get("game_sim_id") or ""): _member_hash(item)
        for item in members if item.get("game_sim_id")
    }
    identity_changed = bool(identity and state.get("save_identity") and identity != state.get("save_identity"))
    full = bool(force_full or population_complete or not prior_hashes or identity_changed)
    if full:
        outgoing = list(members)
        report_kind = "full"
    else:
        outgoing = [item for item in members
                    if current_hashes.get(str(item.get("game_sim_id") or "")) != prior_hashes.get(str(item.get("game_sim_id") or ""))]
        report_kind = "delta"
    prior_ids = set(str(value) for value in (state.get("population_sim_ids") or []))
    current_ids = set(current_hashes)
    removed = sorted(prior_ids - current_ids) if population_complete else []
    sequence = int(state.get("last_report_sequence") or 0) + 1
    clock_details = _game_time_details()
    report = _json_safe({
        "protocol_version": 2,
        "telemetry_version": 5,
        "clock_sync_version": VERSION,
        "mod_version": VERSION,
        "report_sequence": sequence,
        "report_id": "{}-{:012d}".format(identity or "unidentified-save", sequence),
        "report_kind": report_kind,
        "previous_report_checksum": state.get("last_report_checksum") or "",
        "game_day": int(game_day),
        "game_hour": int(game_hour),
        "game_minute": int(game_minute),
        "game_second": clock_details.get("game_second", 0),
        "game_ticks": clock_details.get("game_ticks"),
        "household_name": household_name,
        "household_sims": outgoing,
        "save_identity": identity or None,
        "save_slot_id": slot_id,
        "save_slot_name": slot_name,
        "population_scope": "played-households" if population_complete else "active-household",
        "population_complete": bool(population_complete),
        "population_sim_ids": sorted(current_ids) if population_complete else [],
        "population_households": list(population_households or []) if population_complete else [],
        "removed_game_sim_ids": removed,
    })
    report["report_checksum"] = _canonical_checksum(report)
    merged_hashes = current_hashes if population_complete else dict(prior_hashes, **current_hashes)
    new_state = dict(state)
    new_state.update({
        "protocol_version":2, "clock_sync_version":VERSION,
        "last_report_sequence":sequence, "last_report_checksum":report["report_checksum"],
        "save_identity":identity or state.get("save_identity"),
        "save_slot_id":slot_id, "save_slot_name":slot_name,
        "member_hashes":merged_hashes,
        "population_sim_ids":sorted(current_ids) if population_complete else list(prior_ids),
        "last_full_game_day":int(game_day) if full else state.get("last_full_game_day"),
    })
    _save_protocol_state(new_state)
    return report


def _send_payload_v22(config, payload):
    report = json.loads(payload.decode("utf-8"))
    sequence = int(report.get("report_sequence") or 0)
    folder = _config_folder()
    queue = os.path.join(folder, "report_queue")
    if not os.path.isdir(queue):
        os.makedirs(queue)
    file_name = "report-{:012d}-{}.json".format(sequence, str(report.get("report_checksum") or "")[:12])
    destination = os.path.join(queue, file_name)
    if not os.path.isfile(destination):
        envelope = {
            "receiver_url": config["receiver_url"],
            "sync_token": config["sync_token"],
            "report_sequence": sequence,
            "report_checksum": report.get("report_checksum"),
            "payload": report,
            # The Windows PowerShell 5 JSON parser can normalize 75.0 to 75.
            # Retain the exact Python JSON so the end-to-end checksum remains
            # byte-semantically equivalent after the server parses it.
            "payload_json": payload.decode("utf-8"),
        }
        _write_json_atomic(destination, envelope)
    return 202


def _report_payload_v22(config=None):
    game_day = _core._absolute_game_day()
    game_hour, game_minute = _core._game_clock()
    config = config or _core._load_config() or {}
    household_name, members, complete, household_rows = _played_population_snapshot()
    report = _protocol_report(game_day, game_hour, game_minute, household_name, members,
                              complete, household_rows, True, config)
    payload = json.dumps(report, separators=(",", ":")).encode("utf-8")
    return game_day, game_hour, game_minute, household_name, members, payload


def _post_day_v22(config, game_day, game_hour, game_minute, household_name, members):
    try:
        report = _protocol_report(game_day, game_hour, game_minute, household_name, members,
                                  False, [], False, config)
        _send_payload_v22(config, json.dumps(report, separators=(",", ":")).encode("utf-8"))
    except Exception as error:
        _core._last_reported_day = None
        _core._last_report_signature = None
        _core.LOGGER.warn("Could not queue in-game day {}: {}", game_day, error)
    finally:
        _core._send_in_progress = False


_last_full_population_game_day = None


def _queue_worker(config, payload, game_day):
    try:
        _send_payload_v22(config, payload)
        _core.LOGGER.info("Queued in-game day {} for SeveralUDO", game_day)
    except Exception as error:
        _core._last_reported_day = None
        _core._last_report_signature = None
        _core.LOGGER.warn("Could not queue in-game day {}: {}", game_day, error)
    finally:
        _core._send_in_progress = False


def _poll_clock_v22(_alarm_handle=None):
    global _last_full_population_game_day
    try:
        config = _core._load_config()
        if config is None or _core._send_in_progress:
            return
        game_day = _core._absolute_game_day()
        game_hour, game_minute = _core._game_clock()
        active_name, active_members = _previous_household_snapshot()
        active_signature = hashlib.sha256(json.dumps(_json_safe([
            {key:value for key,value in member.items() if key != "portrait_image_base64"}
            for member in active_members
        ]), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        full_due = (_last_full_population_game_day != game_day or
                    _load_protocol_state().get("last_full_game_day") != game_day)
        signature = (game_day, active_signature, bool(full_due))
        if signature == _core._last_report_signature:
            return
        if full_due:
            household_name, members, complete, household_rows = _played_population_snapshot()
            _last_full_population_game_day = game_day
        else:
            household_name, members, complete, household_rows = active_name, active_members, False, []
        report = _protocol_report(game_day, game_hour, game_minute, household_name, members,
                                  complete, household_rows, full_due, config)
        payload = json.dumps(report, separators=(",", ":")).encode("utf-8")
        _core._last_reported_day = game_day
        _core._last_report_signature = signature
        _core._send_in_progress = True
        worker = threading.Thread(target=_queue_worker, args=(config, payload, game_day))
        worker.daemon = True
        worker.start()
    except Exception as error:
        _core.LOGGER.exception("Clock Sync 2.2 polling failed: {}", error)


# The 2.0.1 compatibility layer's population reader resolves this function
# from its own module globals, so patch both references.
_compat._extended_snapshot = _extended_snapshot
_core._config_path = _config_path_v224
_core._extended_snapshot = _extended_snapshot
_core._send_payload = _send_payload_v22
_core._report_payload = _report_payload_v22
_core._post_day = _post_day_v22
_core._poll_clock = _poll_clock_v22
