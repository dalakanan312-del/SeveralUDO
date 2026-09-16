"""Delivery-backed multiple-birth labels, without rewriting frozen history."""
import re
from collections import defaultdict
from sqlalchemy import select
from .models import Record

NAMES = {1: 'Singleton', 2: 'Twin', 3: 'Triplet', 4: 'Quadruplet',
         5: 'Quintuplet', 6: 'Sextuplet', 7: 'Septuplet', 8: 'Octuplet'}
CLOSED = {'delivered', 'complete', 'completed', 'stillbirth', 'miscarriage', 'cancelled', 'canceled', 'ended', 'closed'}
GENERATED = {'Automatic tracker inference', 'Reviewed suggestion', 'Reviewed Clock Sync suggestion'}
MULTIPLE_TAG = re.compile(r'^(?:Singleton|Twin|Triplet|Quadruplet|Quintuplet|Sextuplet|Septuplet|Octuplet|\d+-baby multiple) birth$')


def integer(value):
    try:
        return int(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def label(count):
    return NAMES.get(count, f'{count}-baby multiple') if count and count > 0 else ''


def count_for(pregnancy, linked_count=0):
    """Actual delivery wins over a forecast; partial deliveries keep expectations."""
    if not pregnancy:
        return linked_count or None
    data = pregnancy.data or {}
    known = max(0, integer(data.get('babies_delivered')) or 0, linked_count)
    if str(data.get('status') or '').casefold() in CLOSED:
        return known or None
    return max(known, integer(data.get('babies_expected')) or 0) or None


class BirthGroups:
    def __init__(self, records):
        rows = [r for r in records if not r.deleted or (r.data or {}).get('infinite_frozen')]
        self.sims = [r for r in rows if r.kind == 'sim']
        self.pregnancies = {r.id: r for r in rows if r.kind == 'pregnancy'}
        self.by_delivery = defaultdict(list)
        self.explicit = defaultdict(list)
        for p in self.pregnancies.values():
            d = p.data or {}
            day = integer(d.get('actual_delivery_global_day'))
            if day is None:
                day = integer(d.get('delivery_global_day'))
            if d.get('mother_id') and day is not None:
                self.by_delivery[(p.save_id, str(d['mother_id']), day)].append(p)
            for sid in set(d.get('linked_newborn_ids') or []):
                self.explicit[str(sid)].append(p)
        self.for_sim = {}
        self.children = defaultdict(set)
        for sim in self.sims:
            p = self.match(sim)
            if p:
                self.for_sim[sim.id] = p
                self.children[p.id].add(sim.id)

    def match(self, sim):
        d = sim.data or {}
        pid = str(d.get('pregnancy_id') or '')
        if pid:
            candidates = [self.pregnancies[pid]] if pid in self.pregnancies else []
        else:
            candidates = self.explicit.get(sim.id, [])
            if not candidates and not (d.get('birth_year_only') or d.get('birth_global_day_estimated')
                    or str(d.get('legitimacy') or '').casefold() == 'adopted'):
                candidates = self.by_delivery.get((sim.save_id, str(d.get('mother_id') or ''), integer(d.get('birth_global_day'))), [])
        candidates = [p for p in candidates if p.save_id == sim.save_id and
                      (not d.get('mother_id') or (p.data or {}).get('mother_id') == d['mother_id'])]
        # Shared birthdays alone are not proof. Require one unambiguous recorded
        # pregnancy/delivery; never use household, surname or approximate years.
        return candidates[0] if len(candidates) == 1 else None

    def count(self, pregnancy):
        return count_for(pregnancy, len(self.children.get(pregnancy.id, ())))

    def corrected_data(self, sim):
        original = sim.data or {}
        pregnancy = self.for_sim.get(sim.id)
        if not pregnancy or original.get('multiple_birth_status_source') == 'manual':
            return dict(original)
        current = str(original.get('multiple_birth_status') or '')
        last_auto = original.get('multiple_birth_status_auto_value')
        if last_auto is not None and current != last_auto:
            return dict(original)
        if current and last_auto is None and original.get('birth_circumstances_source') not in GENERATED:
            return dict(original)
        count = self.count(pregnancy)
        status = label(count)
        if not status:
            return dict(original)
        data = {**original, 'multiple_birth_status': status,
                'multiple_birth_status_source': 'Recorded pregnancy and children',
                'multiple_birth_status_auto_value': status,
                'multiple_birth_count': count, 'multiple_birth_pregnancy_id': pregnancy.id}
        tags = original.get('birth_circumstance_tags') or []
        summary = str(original.get('birth_circumstances') or '')
        # Only replace the multiplicity clause in an unedited generated summary.
        # Custom prose, locations, complications, dates and other tags stay put.
        if tags and all(isinstance(t, str) for t in tags) and summary == '; '.join(tags) + '.':
            updated_tags = [status + ' birth' if MULTIPLE_TAG.fullmatch(t) else t for t in tags]
            if not any(MULTIPLE_TAG.fullmatch(t) for t in tags):
                updated_tags.insert(1, status + ' birth')
            data['birth_circumstance_tags'] = updated_tags
            data['birth_circumstances'] = '; '.join(updated_tags) + '.'
        return data


def load(session, save):
    return BirthGroups(list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind.in_(('sim', 'pregnancy'))))))


def reconcile(session, save):
    from . import domain, infinite_decades
    if (not save or not domain.automation_enabled(save) or infinite_decades.frozen(save)
            or not (save.settings or {}).get('automatic_birth_circumstances', True)):
        return 0
    groups = load(session, save)
    changed = 0
    for sim in groups.sims:
        if sim.deleted or (sim.data or {}).get('infinite_frozen'):
            continue
        data = groups.corrected_data(sim)
        if data == sim.data:
            continue
        base = sim.version
        sim.data = data
        sim.version += 1
        domain.journal(session, sim, 'upsert', base)
        changed += 1
    return changed


def retain_edit_provenance(previous, updated):
    if updated.get('multiple_birth_status', '') != previous.get('multiple_birth_status', ''):
        updated['multiple_birth_status_source'] = 'manual'
        updated.pop('multiple_birth_status_auto_value', None)
    if updated.get('birth_circumstances', '') != previous.get('birth_circumstances', ''):
        updated['birth_circumstance_tags'] = []
        updated['birth_circumstances_source'] = 'Player-entered'
