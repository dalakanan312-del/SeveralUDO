"""One definition of life/death, including parked dynasty observations."""
def integer(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def deceased(sim, day, *, historical=False):
    data = sim.data or {}
    observed = integer(data.get('infinite_frozen_global_day')) if data.get('infinite_frozen') else None
    death = integer(data.get('death_global_day'))
    if historical and death is not None:
        return death <= day
    return bool(data.get('death_confirmed') or data.get('game_was_dead') or
                (death is not None and death <= (observed if observed is not None else day)))


def living(sim, day, *, historical=False):
    birth = integer((sim.data or {}).get('birth_global_day'), integer(getattr(sim, 'global_day', None), 1))
    return not getattr(sim, 'deleted', False) and not deceased(sim, day, historical=historical) and (birth is None or birth <= day)
