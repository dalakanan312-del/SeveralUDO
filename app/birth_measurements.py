"""Optional, per-child birth measurements with explicit units and provenance."""
from __future__ import annotations

import math
import re
import hashlib
import json


SOURCE = "PandaSama birth certificate"
NUMBER = r"(?<![\w.,+\-])([0-9]+(?:[.,][0-9]+)?)\s*"
WEIGHT = re.compile(NUMBER + r"(kg|kilograms?|grams?|g|lbs?\.?|pounds?|oz|ounces?)(?!\w)", re.I)
LENGTH = re.compile(NUMBER + r"(cm|centimeters?|centimetres?|inches|inch|in\.?)(?!\w)", re.I)
UNITS = {"weight": {"g": 1, "kg": 1000, "lb": 453.59237, "oz": 28.349523125},
         "length": {"cm": 1, "in": 2.54}}


def fingerprint(data):
    return hashlib.sha256(json.dumps(data.get("birth_measurements") or {}, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def measurement(kind, value, unit):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("Enter a numeric birth measurement.")
    try:
        number = float(str(value).strip().replace(",", "."))
    except (ValueError, TypeError):
        raise ValueError("Enter a numeric birth measurement.") from None
    factor = UNITS[kind].get(unit)
    if factor is None:
        raise ValueError("Choose a supported measurement unit.")
    normalized = number * factor
    # Broad data-validation bounds, not medical advice or gameplay outcomes.
    maximum = 30000 if kind == "weight" else 150
    if not math.isfinite(number) or not 0 < normalized <= maximum:
        raise ValueError("Birth measurements must be positive and within the supported range.")
    return {"value": number, "unit": unit, "grams" if kind == "weight" else "cm": round(normalized, 4)}


def _unit(raw):
    raw = raw.lower().rstrip(".")
    if raw in {"g", "gram", "grams"}: return "g"
    if raw == "kg" or raw.startswith("kilogram"): return "kg"
    if raw.startswith(("lb", "pound")): return "lb"
    if raw == "oz" or raw.startswith("ounce"): return "oz"
    if raw == "cm" or raw.startswith("centimet"): return "cm"
    return "in"


def parse(text):
    """Accept explicit unit-bearing text; don't infer values from health buffs."""
    if not isinstance(text, str): return {}
    text = text[:1000]
    result = {}
    for kind, pattern in (("weight", WEIGHT), ("length", LENGTH)):
        matches = list(pattern.finditer(text))
        try:
            if len(matches) == 1:
                result[kind] = measurement(kind, matches[0][1], _unit(matches[0][2]))
            elif kind == "weight" and len(matches) == 2 and [_unit(m[2]) for m in matches] == ["lb", "oz"]:
                pounds, ounces = [float(m[1].replace(",", ".")) for m in matches]
                if 0 <= ounces < 16 and not text[matches[0].end():matches[1].start()].strip(" ,"):
                    result[kind] = measurement(kind, pounds * 16 + ounces, "oz")
        except ValueError:
            pass
    return result


def from_report(snapshot):
    sid = str(snapshot.get("game_sim_id") or "")
    rows = snapshot.get("birth_certificates")
    if not sid or not isinstance(rows, list): return None
    readings = []
    for row in rows[:12]:
        if not isinstance(row, dict) or row.get("source") != SOURCE: continue
        if str(row.get("baby_game_sim_id") or "") != sid: continue
        if row.get("match") not in {"stored_sim_id", "unique_full_name"}: continue
        values = parse(row.get("text"))
        if values:
            readings.append({**values, "source": SOURCE, "evidence": "Certificate entry",
                "certificate_id": str(row.get("certificate_id") or "")[:100],
                "match": row["match"], "text": str(row.get("text") or "")[:1000]})
    if not readings: return None
    # Multiple objects for one child must agree. Don't silently choose one.
    merged = dict(readings[0])
    for kind, normalized in (("weight", "grams"), ("length", "cm")):
        values = [row[kind] for row in readings if kind in row]
        if values and any(abs(v[normalized] - values[0][normalized]) > 0.01 for v in values):
            return None
        if values: merged[kind] = values[0]
    return merged


def updates(data, snapshot):
    prior = data.get("birth_measurements") or {}
    if prior.get("manual"):
        return {}
    detected = from_report(snapshot)
    if not detected: return {}
    # Partial / missing reports never erase previously recorded measurements.
    value = {**prior, **detected}
    return {"birth_measurements": value} if value != prior else {}


def manual(form):
    result = {"source": "Player-entered", "evidence": "Player-entered", "manual": True}
    for kind in UNITS:
        value = measurement(kind, form.get("birth_" + kind), form.get("birth_" + kind + "_unit"))
        if value: result[kind] = value
    return result


def display(data, kind):
    value = (data.get("birth_measurements") or {}).get(kind)
    if not isinstance(value, dict): return "Not recorded"
    try:
        valid = measurement(kind, value.get("value"), value.get("unit"))
        if not valid: return "Not recorded"
        primary = f"{valid['value']:g} {valid['unit']}"
        converted = f"{valid['grams'] / 1000:.2f} kg" if kind == "weight" else f"{valid['cm']:.1f} cm"
        if valid["unit"] in {"lb", "oz", "in"}:
            return f"{primary} ({converted})"
        return primary
    except (ValueError, TypeError):
        return "Not recorded"
