"""Presentation of traits without removing internal evidence from automation.

game_traits remains the complete backwards-compatible collection. Individual
trait_details carry the game's classification; legacy names alone cannot tell
us whether a trait is hidden.
"""
from .game_metadata import readable_named_labels


def category(row: dict) -> str:
    if row.get("is_hidden") is True:
        return "hidden"
    if row.get("is_hidden") is False:
        return "visible"
    return "unknown"


def groups(values, details=None) -> dict[str, list[str]]:
    labels = readable_named_labels(values, details, kind="trait")
    result = {"visible": [], "hidden": [], "unknown": []}
    classified = set()
    for row in details or ():
        if not isinstance(row, dict):
            continue
        bucket = category(row)
        if bucket == "unknown":
            continue
        for label in readable_named_labels([row], kind="trait"):
            if label in labels:
                if label not in result[bucket]:
                    result[bucket].append(label)
                classified.add(label)
    result["unknown"] = [label for label in labels if label not in classified]
    return result


def reviewed(form, details=None, prefix="") -> dict:
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
                if category(row) != bucket or not matches:
                    row.update(is_hidden={"visible": False, "hidden": True, "unknown": None}[bucket],
                               visibility_source="player" if bucket != "unknown" else "unreported")
                rows.append(row)
    return {"traits": result, "trait_details": rows}


def retain_classification(rows, previous):
    """Older readers can refresh names without erasing known trait types."""
    result = []
    for row in rows:
        row = dict(row)
        if category(row) == "unknown":
            matches = [old for old in previous or () if isinstance(old, dict)
                       and category(old) != "unknown" and (
                           str(old.get("tuning_id")) == str(row["tuning_id"]) if row.get("tuning_id")
                           else old.get("name") == row.get("name"))]
            if len(matches) == 1:
                for key in ("is_hidden", "trait_type", "trait_type_id", "visibility_source"):
                    if key in matches[0]:
                        row[key] = matches[0][key]
        result.append(row)
    return result
