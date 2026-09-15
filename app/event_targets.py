"""Conservative event subjects: qualifiers describe targets, not dice outcomes."""
from __future__ import annotations
import re

PLACEHOLDERS = {'', 'global', 'global / see notes', 'see notes', 'affected areas', 'world', 'worldwide', 'all'}


def instruction(text):
    """Only the instruction before a result table; never its death/wealth prose."""
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    return re.split(r':|(?<!\w)\d+\s*(?:[-–—�]\s*\d+)?\s*[:–—�]', text, maxsplit=1)[0]


def selector(data, step=None):
    step = step or {}
    return instruction(step.get('selector') or step.get('context') or data.get('notes'))


def roll_scope(data, step=None, rule=None):
    step, rule = step or {}, rule or {}
    for obj in (step, rule):
        value = str(obj.get('roll_scope') or obj.get('roll_unit') or '').strip().casefold()
        if value in {'household', 'family', 'per household'}: return 'household'
        if value in {'sim', 'individual', 'per sim'}: return 'sim'
    explicit = str(data.get('roll_scope') or data.get('roll_unit') or '').strip().casefold()
    if step.get('parent_index') is None and not step.get('parent_indices'):
        if explicit in {'household','family','per household'}: return 'household'
        if explicit in {'sim','individual','per sim'}: return 'sim'
    text = selector(data, step).casefold()
    # Explicit per-Sim subjects take precedence over "in all households".
    if re.search(r'\b(?:per|each|every|all)\s+(?:eligible\s+|affected\s+)?(?:sims?|children|child|adults?|teens?|infants?|babies|animals?|men|women)\b', text): return 'sim'
    if re.search(r'\bfor\s+(?:the\s+)?(?:animals?|livestock)\b',text): return 'sim'
    if re.search(r'\b(?:per|each|every)\s+(?:\w+\s+){0,3}(?:household|family)\b', text): return 'household'
    if re.search(r'\b(?:households?|families)\b', text): return 'household'
    if explicit in {'household','family','per household'}: return 'household'
    if str(data.get('scope') or '').casefold() == 'household': return 'household'
    return 'sim'


def source_location(data, step=None):
    """Recover explicit local instructions behind a Global / See Notes label.

    None means no narrower qualifier. Empty text means local but not identified:
    the player must supply a location instead of applying it to the entire world.
    """
    step = step or {}
    explicit = str(step.get('location') or step.get('eligible_location') or '').strip()
    if explicit and explicit.casefold() not in PLACEHOLDERS: return explicit
    text = instruction(data.get('notes')) if not step else selector(data, step)
    match = re.search(
        r'\b(?:households?|families|sims?|residents|settlements)\s+(?:living\s+)?'
        r'(?:in|near|around|along|within|on)\s+(?:the\s+)?(.+?)'
        r'(?=\s+(?:must|may|should|can|will|shall|have\s+to|roll|rolls|face)\b|[.;:]|$)', text, re.I)
    if match:
        place = re.sub(r'\s+', ' ', match.group(1)).strip(' .,;')
        if not re.search(r'\b(?:affected|applicable|same|main|side|active|your)\b', place, re.I):
            return place
    if re.search(r'\b(?:affected local|nearby households|local households|affected areas)\b',
                 f"{data.get('affected_class','')} {text}", re.I): return ''
    return None


def location_at(sim, household, save, due, migrations=(), fallback=''):
    """Use the dated move first; defaults only fill missing information."""
    d, h, settings = sim.data or {}, household.data or {} if household else {}, save.settings or {} if save else {}
    moves = sorted((r for r in migrations if not r.deleted and str((r.data or {}).get('sim_id')) == sim.id),
                   key=lambda r: int((r.data or {}).get('move_global_day') or r.global_day or 0))
    if moves:
        place = str(d.get('birth_country') or d.get('birthplace') or (moves[0].data or {}).get('from_country') or '')
        for move in moves:
            md = move.data or {}
            if int(md.get('move_global_day') or move.global_day or 0) <= due:
                place = str(md.get('to_country') or md.get('to_location') or place)
        return place
    own = [d.get('current_country') or d.get('country'), d.get('location')]
    if any(own): return ', '.join(str(v) for v in own if v)
    home = [h.get('current_country') or h.get('country'), h.get('location')]
    if any(home): return ', '.join(str(v) for v in home if v)
    birth = d.get('birth_country') or d.get('birthplace')
    if birth: return str(birth)
    return str(settings.get('challenge_location') or settings.get('location') or settings.get('country')
               or d.get('last_game_world') or d.get('world') or h.get('world') or fallback or '')


def eligibility_text(data, step=None, rule=None):
    rule = rule or {}
    return ' '.join(str(v or '') for v in (rule.get('eligibility'), data.get('eligibility'),
                                          data.get('affected_class'), selector(data, step)))


def matches_attributes(data, step, rule, sim, household):
    """Explicit restrictions require evidence; missing facts aren't a match."""
    step, rule = step or {}, rule or {}
    sd, hd = sim.data or {}, household.data or {} if household else {}
    for field, actual_fields in (
        ('eligible_classes', ('social_class',)), ('eligible_occupations', ('occupation', 'career', 'trade')),
        ('eligible_religions', ('religion',)), ('eligible_occults', ('occult_types', 'occult_type', 'occult')),
    ):
        wanted = step.get(field) or rule.get(field) or data.get(field)
        if not wanted: continue
        values = wanted if isinstance(wanted, (list,tuple,set)) else re.split(r'[,;/]', str(wanted))
        values = [str(v).strip().casefold() for v in values if str(v).strip()]
        if any(v in {'all','any','all sims'} for v in values): continue
        actual = ' '.join(str(sd.get(f) or hd.get(f) or '') for f in actual_fields).casefold()
        if not any(re.search(r'(?<!\w)'+re.escape(v)+r'(?!\w)', actual) for v in values): return False
    subject = selector(data,step).casefold()
    animal_subject = bool(re.search(r'\b(?:for|per|each|every|all)\s+(?:the\s+)?(?:animals?|livestock|dogs?|cats?|horses?)\b',subject))
    species = str(sd.get('game_species') or sd.get('species') or sd.get('species_occult') or '').casefold()
    is_animal = bool(re.search(r'\b(?:animal|livestock|cat|dog|horse|cow|sheep|goat|chicken|llama)\b',species))
    if animal_subject != is_animal: return False
    evidence = ' '.join(str(sd.get(f) or hd.get(f) or '') for f in
                        ('occupation','career','trade','game_career','household_type','livelihood')).casefold()
    for condition, aliases in (
        (r'\b(?:farmers|farming households?|agricultural households?)\b',r'farm|agricultur|peasant'),
        (r'\b(?:merchants|merchant households?|trading households?)\b',r'merchant|trader|trading|commerce'),
        (r'\b(?:bakery households?|bakers)\b',r'bakery|baker'),
        (r'\b(?:fishing households?|fishermen)\b',r'fishing|fisher'),
    ):
        if re.search(condition,subject) and not re.search(aliases,evidence): return False
    return True
