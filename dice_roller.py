from __future__ import annotations

import re
import secrets

SUPPORTED_DICE = (4, 6, 8, 10, 12, 20, 100)
_DICE_PATTERN = re.compile(r"^\s*(?:(\d+)\s*)?[dD](\d+)\s*([+-]\s*\d+)?\s*$")


def parse(notation):
    match = _DICE_PATTERN.match(str(notation or ""))
    if not match:
        return None
    quantity = int(match.group(1) or 1)
    sides = int(match.group(2))
    modifier = int((match.group(3) or "0").replace(" ", ""))
    if quantity < 1 or quantity > 100 or sides < 2 or sides > 1000:
        return None
    normalized=f"{quantity if quantity != 1 else ''}d{sides}"
    if modifier:
        normalized+=f"{modifier:+d}"
    return {"quantity": quantity, "sides": sides, "modifier": modifier, "notation": normalized}


def roll(notation):
    spec = parse(notation)
    if not spec:
        raise ValueError("Use dice notation such as d20, 2d6, or d10+2.")
    values = [secrets.randbelow(spec["sides"]) + 1 for _ in range(spec["quantity"])]
    return {**spec, "rolls": values, "total": sum(values) + spec["modifier"]}
