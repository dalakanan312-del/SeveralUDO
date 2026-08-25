"""Clock Sync 2.1.0 guarded life-history telemetry for current Sims 4 builds."""

import base64
import re
import sys

from . import compat_201 as _compat


VERSION = "2.1.0"
_core = _compat._core
_core.VERSION = VERSION
_compat.VERSION = VERSION
_previous_extended_snapshot = _compat._extended_snapshot


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
            result.append({"name": label, "level": level})
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
        for value in bits:
            label = _humanize(value, ("relationshipBit_", "relationship_bit_"))
            if label and label not in bit_labels:
                bit_labels.append(label)
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
            "friendship_score": friendship,
            "romance_score": romance,
        })
    rows = sorted(by_id.values(), key=lambda row: (str(row.get("category") or ""), str(row.get("name") or "")))
    return {"relationships": rows, "relationship_scan_supported": True}, True


_HEALTH_WORDS = (
    "illness", "disease", "infection", "fever", "symptom", "sick", "malaria",
    "measles", "pox", "plague", "cholera", "flu", "cold", "pneumonia",
    "hemorrhage", "haemorrhage", "postpartum", "nausea", "cough", "pain",
)


def _health_details(sim_info):
    component = getattr(sim_info, "buff_component", None)
    buffs = _read(component, ("get_all_buffs", "buffs", "active_buffs")) if component is not None else ()
    rows = []
    for buff in buffs:
        tuning = getattr(buff, "buff_type", None) or getattr(buff, "tuning", None) or buff
        label = _humanize(tuning, ("buff_", "trait_"))
        if not label or not any(word in label.casefold() for word in _HEALTH_WORDS):
            continue
        rows.append({
            "name": label,
            "remaining_minutes": _number(buff, ("remaining_time", "time_remaining", "remaining_minutes")),
            "severity": _humanize(_safe_value(buff, ("severity",), None)),
            "provider": _tuning_provider(tuning),
        })
    unique = []
    seen = set()
    for row in rows:
        if row["name"].casefold() not in seen:
            seen.add(row["name"].casefold())
            unique.append(row)
    return {
        "health_buffs": unique,
        "symptoms": [row["name"] for row in unique],
        "health_scan_supported": component is not None,
    }, component is not None


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
    death_type = result.get("death_type") or _humanize(_safe_value(sim_info, ("death_type",), None))
    occult_types = [str(value) for value in (result.get("occult_types") or [])]
    is_ghost = bool(_safe_value(sim_info, ("is_ghost",), False)) or any(
        "ghost" in value.casefold() for value in occult_types
    )
    return {
        "death_type": death_type or None,
        "death_details": {
            "death_type": death_type or None,
            "is_ghost": is_ghost,
            "place": result.get("lot_name") or None,
            "world": result.get("world_name") or None,
        },
        "is_ghost": is_ghost,
        "death_scan_supported": "is_dead" in result,
    }, "is_dead" in result


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
            "telemetry_version": 4,
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
        "telemetry_version": 4,
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
    result.update(_environment_diagnostics(capabilities, errors))
    return result


# The 2.0.1 compatibility layer's population reader resolves this function
# from its own module globals, so patch both references.
_compat._extended_snapshot = _extended_snapshot
_core._extended_snapshot = _extended_snapshot
