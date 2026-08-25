"""Clock Sync 2.0.4 compatibility patch for current Sims 4 telemetry."""

import re

from . import compat_201 as _compat


VERSION = "2.0.4"
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


def _extended_snapshot(sim_info, household):
    result = _previous_extended_snapshot(sim_info, household)
    skills, skills_supported = _skill_snapshot(sim_info)
    milestones, milestones_supported = _milestone_snapshot(sim_info)
    result.update({
        "skills": skills,
        "skills_scan_supported": skills_supported,
        "milestones": milestones,
        "milestone_scan_supported": milestones_supported,
        "telemetry_version": 3,
    })
    return result


# The 2.0.1 compatibility layer's population reader resolves this function
# from its own module globals, so patch both references.
_compat._extended_snapshot = _extended_snapshot
_core._extended_snapshot = _extended_snapshot
