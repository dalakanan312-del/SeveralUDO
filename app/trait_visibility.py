"""Presentation of traits without removing internal evidence from automation.

game_traits remains the complete backwards-compatible collection. Individual
trait_details carry the game's classification; legacy names alone cannot tell
us whether a trait is hidden.
"""
import re
from .game_metadata import readable_named_labels

PREFERENCE_BUCKETS = ("likes", "dislikes", "preferences")


def preference_category(value):
    """Use game categories or explicit preference labels, never substring guesses."""
    row = value if isinstance(value, dict) else {"name": value}
    explicit = str(row.get("preference_kind") or "").casefold()
    if explicit in PREFERENCE_BUCKETS:
        return explicit
    if row.get("is_preference") is False:
        return None
    kind = str(row.get("trait_type") or row.get("preference_type") or "").rsplit(".", 1)[-1].upper()
    if kind in {"LIKE", "LIKES"}: return "likes"
    if kind in {"DISLIKE", "DISLIKES"}: return "dislikes"
    if kind == "PERSONALITY":
        return None
    for key in ("technical_name", "tuning_name", "name", "display_name", "title"):
        for label in readable_named_labels([row.get(key)], kind="trait"):
            match = re.match(r"^(?:(?:sim\s*preferences?|preferences?)\s+)?(likes?|dislikes?)\b", label, re.I)
            if match:
                return "dislikes" if match.group(1).lower().startswith("dislike") else "likes"
    if row.get("is_preference") is True or kind in {"PREFERENCE", "PREFERENCES", "LIKES_DISLIKES"}:
        return "preferences"
    return None


def preference_groups(trait_sections, preferences=None):
    """Combine native preferences with preference-traits for display only."""
    result = {key:list(trait_sections.get(key, [])) for key in PREFERENCE_BUCKETS}
    values = preferences if isinstance(preferences, (list, tuple)) else [preferences] if preferences else []
    for value in values:
        bucket = preference_category(value) or "preferences"
        for label in readable_named_labels([value], kind="preference"):
            if label not in result[bucket]:
                result[bucket].append(label)
    return result


def category(row: dict) -> str:
    if row.get("is_hidden") is True:
        return "hidden"
    if row.get("is_hidden") is False:
        return "visible"
    return "unknown"


def groups(values, details=None) -> dict[str, list[str]]:
    labels = readable_named_labels(values, details, kind="trait")
    result = {"visible": [], "hidden": [], "unknown": [], "likes": [], "dislikes": [], "preferences": []}
    classified = set()
    known_regular = set()
    for row in details or ():
        if not isinstance(row, dict):
            continue
        bucket = preference_category(row) or category(row)
        if bucket == "unknown":
            if row.get("is_preference") is False or str(row.get("trait_type") or "").rsplit(".", 1)[-1].upper() == "PERSONALITY":
                known_regular.update(readable_named_labels([row], kind="trait"))
            continue
        for label in readable_named_labels([row], kind="trait"):
            if label in labels:
                if label not in result[bucket]:
                    result[bucket].append(label)
                classified.add(label)
                if bucket not in PREFERENCE_BUCKETS:
                    known_regular.add(label)
    for label in labels:
        bucket = None if label in known_regular else preference_category(label)
        if label not in classified and bucket:
            result[bucket].append(label)
            classified.add(label)
    result["unknown"] = [label for label in labels if label not in classified]
    return result


def reviewed(form, details=None, prefix="", values=None) -> dict:
    """Recombine split editor fields while preserving tuning IDs and evidence."""
    result = []
    rows = []
    for bucket, field in (("visible", "traits"), ("hidden", "hidden_traits"), ("unknown", "unclassified_traits")):
        labels = readable_named_labels(str(form.get(prefix + field) or "").splitlines(), kind="trait")
        for label in labels:
            if label not in result:
                result.append(label)
            matches = [dict(row) for row in details or () if isinstance(row, dict)
                       and label in readable_named_labels([row], kind="trait")]
            matching = [row for row in matches if category(row) == bucket]
            for row in matching or matches[:1] or [{"name": label}]:
                if form.get("preference_sections") == "1" and preference_category(row):
                    row.pop("preference_kind", None)
                    row.update(is_preference=False, preference_source="player")
                if category(row) != bucket or not matches:
                    row.update(is_hidden={"visible": False, "hidden": True, "unknown": None}[bucket],
                               visibility_source="player" if bucket != "unknown" else "unreported")
                rows.append(row)
    # Preferences remain in the complete raw trait collection for compatibility,
    # but are edited independently. Older open forms must not drop them.
    prior = groups(values if values is not None else details, details)
    for bucket in PREFERENCE_BUCKETS:
        if form.get("preference_sections") == "1":
            labels = readable_named_labels(str(form.get(prefix + bucket) or "").splitlines(), kind="trait")
        else:
            labels = prior[bucket]
        for label in labels:
            if label in result:
                continue
            result.append(label)
            matches = [dict(row) for row in details or () if isinstance(row, dict)
                       and label in readable_named_labels([row], kind="trait")]
            for row in matches or [{"name":label}]:
                if preference_category(row) != bucket:
                    row.update(preference_kind=bucket, preference_source="player")
                rows.append(row)
    return {"traits": result, "trait_details": rows}


def retain_classification(rows, previous):
    """Older readers can refresh names without erasing known trait types."""
    result = []
    for row in rows:
        row = dict(row)
        matches = [old for old in previous or () if isinstance(old, dict) and
                   (str(old.get("tuning_id")) == str(row["tuning_id"]) if row.get("tuning_id")
                    else old.get("name") == row.get("name"))]
        if category(row) == "unknown":
            visible_matches = [old for old in matches if category(old) != "unknown"]
            if len(visible_matches) == 1:
                for key in ("is_hidden", "trait_type", "trait_type_id", "visibility_source"):
                    if key in visible_matches[0] and row.get(key) is None:
                        row[key] = visible_matches[0][key]
        if not preference_category(row) and not row.get("trait_type") and row.get("is_preference") is not False:
            pref_matches = [old for old in matches if preference_category(old)]
            if len(pref_matches) == 1:
                row["preference_kind"] = preference_category(pref_matches[0])
                row["preference_source"] = pref_matches[0].get("preference_source", "retained-game-classification")
        result.append(row)
    return result
