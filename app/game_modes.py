"""Edition-specific tracker and Clock Sync presentation helpers."""

from __future__ import annotations


SIMS4 = "sims4"
SIMS3 = "sims3"

GAME_MODES = {
    SIMS4: {
        "id": SIMS4,
        "name": "The Sims 4",
        "short_name": "Sims 4",
        "clock_label": "Sims 4 Clock Sync",
        "default_days_per_year": 4,
    },
    SIMS3: {
        "id": SIMS3,
        "name": "The Sims 3",
        "short_name": "Sims 3",
        "clock_label": "Sims 3 save reader",
        "default_days_per_year": 4,
    },
}


def normalize(value: object) -> str:
    """Return the supported game edition, retaining Sims 4 for older saves."""
    text = str(value or "").casefold().replace(" ", "")
    return SIMS3 if text in {"sims3", "thesims3", "3", "ts3"} else SIMS4


def for_save(save) -> dict:
    return GAME_MODES[normalize((getattr(save, "settings", None) or {}).get("game_mode"))]


def set_mode(save, value: object) -> str:
    mode = normalize(value)
    settings = dict(getattr(save, "settings", None) or {})
    settings["game_mode"] = mode
    save.settings = settings
    return mode
