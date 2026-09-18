"""Read PandaSama's saved certificate text, never generate or edit game data.

Verified against Childbirth 1.965: the rename interaction stores its editable
birth details in GameObject.custom_description. These are certificate entries,
not clinical measurements or Sim body-weight statistics.
"""
from itertools import islice


DEFINITIONS = frozenset((9454473550490189272, 9454473550490189273))
TUNING = 10745341224055388351


def _get(owner, name, default=None):
    try:
        value = getattr(owner, name, default)
        return value() if callable(value) else value
    except Exception:
        return default


def _values(owner):
    if owner is None:
        return ()
    values = _get(owner, "values")
    return values if values is not None else owner


def certificate(obj):
    definition_id = _get(_get(obj, "definition"), "id")
    tuning_id = _get(type(obj), "guid64")
    if definition_id not in DEFINITIONS and tuning_id != TUNING:
        return None
    description = _get(obj, "custom_description", "")
    if not isinstance(description, str) or not description.strip():
        return None
    stored = _get(obj, "stored_sim_info_component")
    baby_id = _get(stored, "get_stored_sim_id") or _get(obj, "get_stored_sim_id")
    return {
        "certificate_id": str(_get(obj, "id", "") or ""),
        "definition_id": str(definition_id or ""),
        "baby_game_sim_id": str(baby_id or ""),
        "baby_name": str(_get(obj, "custom_name", "") or "")[:400],
        "text": description.strip()[:1000],
        "source": "PandaSama birth certificate",
    }


def attach(members, services):
    """One bounded scan per report, including certificates placed on the lot.

    Never match by birth order or inventory owner (which may be the mother).
    Unbound home-birth certificates require an exact, unique full Sim name.
    Check uniqueness against all loaded Sim infos, not just this report's Sims.
    """
    by_id = {str(row.get("game_sim_id")): row for row in members if row.get("game_sim_id")}
    manager = _get(services, "sim_info_manager")
    infos = []
    names = {}
    try:
        infos = list(islice(iter(_values(manager)), 10001))
    except Exception:
        pass
    complete_names = manager is not None and len(infos) <= 10000 and bool(infos)
    for sim in infos:
        sid = str(_get(sim, "sim_id", "") or "")
        name = " ".join(str(_get(sim, part, "") or "").strip() for part in ("first_name", "last_name")).strip().casefold()
        if name and sid:
            names.setdefault(name, set()).add(sid)
    sources = [_get(services, "object_manager"), _get(services, "inventory_manager")]
    for sim in infos:
        instance = _get(sim, "get_sim_instance")
        inventory = _get(instance, "inventory_component")
        if inventory is not None:
            sources.append(inventory)
    seen = set()
    scanned = 0
    for source in sources:
        try:
            for obj in islice(iter(_values(source)), max(0, 10000 - scanned)):
                scanned += 1
                key = str(_get(obj, "id", "") or id(obj))
                if key in seen:
                    continue
                seen.add(key)
                row = certificate(obj)
                if not row:
                    continue
                sid = row["baby_game_sim_id"]
                match = "stored_sim_id"
                if not sid and complete_names:
                    candidates = names.get(row["baby_name"].strip().casefold(), set())
                    if len(candidates) == 1:
                        sid = next(iter(candidates))
                        match = "unique_full_name"
                if sid in by_id:
                    row.update(baby_game_sim_id=sid, match=match)
                    certificates = by_id[sid].setdefault("birth_certificates", [])
                    if len(certificates) < 12:
                        certificates.append(row)
        except Exception:
            # Optional objects or a loading/unloading zone cannot stop reports.
            continue
    for member in members:
        if "birth_certificates" in member:
            member["birth_certificates"].sort(key=lambda row: row["certificate_id"])
