"""Read-only Sims 3 DBPF/OBJS reader; never loads game assemblies.

Format reference: https://modthesims.info/wiki.php?title=Sims_3:0x06B981ED
"""
from __future__ import annotations
import struct
from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import shutil
import tempfile
import time
import re
from datetime import datetime, timezone
from .sims3_trait_names import TRAIT_NAMES

# A new decoder can enrich the same saved bytes once, without replaying them
# on every restart. Keep this separate from the source-file fingerprint.
READER_REVISION = 3

MAX_BYTES = 512 * 1024 * 1024
MAX_ITEMS = 2_000_000
OBJS = 0x06B981ED

class SaveReadError(ValueError):
    pass

class Reader:
    def __init__(self, data, pos=0, end=None):
        self.data, self.pos = data, pos
        self.end = len(data) if end is None else end
    def take(self, size):
        if size < 0 or self.pos + size > self.end:
            raise SaveReadError('Truncated saved data')
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result
    def number(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))[0]
    def byte(self): return self.number('B')
    def uint(self): return self.number('I')
    def varint(self, msb=False, signed=False):
        value = 0
        for index in range(5):
            byte = self.byte()
            if msb:
                if index == 0 and signed and byte & 0x40: value = -1
                value = (value << 7) | (byte & 127)
            else: value |= (byte & 127) << (7 * index)
            if not byte & 128: return value
        raise SaveReadError('Oversized integer')
    def text(self):
        return self.take(self.varint()).decode('utf-8', errors='strict')

def decompress(data, size):
    if size > MAX_BYTES or len(data) < 5 or data[1] != 0xfb or data[0] & 0x3e != 0x10:
        raise SaveReadError('Unsupported save compression')
    reader = Reader(data)
    flags = reader.byte()
    reader.byte()
    width = 4 if flags & 0x80 else 3
    declared = int.from_bytes(reader.take(width), 'big')
    if flags & 1: reader.take(width)
    if declared != size: raise SaveReadError('Inconsistent decompressed size')
    output = bytearray()
    while reader.pos < reader.end:
        control = reader.byte()
        count = offset = 0
        if control < 0x80:
            x = reader.byte()
            literal, count, offset = control & 3, ((control >> 2) & 7) + 3, ((control & 0x60) << 3) + x + 1
        elif control < 0xc0:
            x, y = reader.byte(), reader.byte()
            literal, count, offset = x >> 6, (control & 63) + 4, ((x & 63) << 8) + y + 1
        elif control < 0xe0:
            x, y, z = reader.byte(), reader.byte(), reader.byte()
            literal, count, offset = control & 3, ((control & 12) << 6) + z + 5, ((control & 16) << 12) + (x << 8) + y + 1
        else: literal = ((control & 31) + 1) * 4 if control < 0xfc else control & 3
        if len(output) + literal + count > size: raise SaveReadError('Oversized compressed resource')
        output.extend(reader.take(literal))
        if count:
            if offset > len(output): raise SaveReadError('Invalid compressed back reference')
            for _ in range(count): output.append(output[-offset])
        if control >= 0xfc: break
    if len(output) != size: raise SaveReadError('Incomplete resource')
    return bytes(output)

def resources(path: Path, wanted=None):
    if path.stat().st_size > MAX_BYTES: raise SaveReadError('Save exceeds supported size')
    data = path.read_bytes()
    if data[:4] != b'DBPF' or len(data) < 96: raise SaveReadError('Not a Sims 3 save package')
    if struct.unpack_from('<I', data, 4)[0] != 2: raise SaveReadError('Unsupported DBPF version')
    count, position = struct.unpack_from('<I', data, 36)[0], struct.unpack_from('<I', data, 64)[0]
    if count > MAX_ITEMS or position < 96: raise SaveReadError('Invalid resource index')
    reader = Reader(data, position)
    flags = reader.uint()
    if flags & ~7: raise SaveReadError('Unknown DBPF index flags')
    common = [reader.uint() if flags & (1 << i) else None for i in range(3)]
    for _ in range(count):
        typ, group, high = [common[i] if flags & (1 << i) else reader.uint() for i in range(3)]
        low, offset, stored, size, compression = [reader.uint() for _ in range(5)]
        if wanted is not None and typ not in wanted: continue
        raw = Reader(data, offset).take(stored & 0x7fffffff)
        if compression & 0xffff == 0xffff: raw = decompress(raw, size)
        elif compression & 0xffff or len(raw) != size: raise SaveReadError('Unsupported resource encoding')
        yield typ, group, (high << 32) | low, raw

@dataclass(frozen=True)
class TypeDefinition:
    name: str
    fields: tuple

@dataclass(frozen=True)
class Ref:
    index: int

class ObjectGraph:
    def __init__(self, data):
        self.data = data
        reader = Reader(data)
        version, magic, count, instances, types_offset, index_offset, tgi_offset = [reader.uint() for _ in range(7)]
        if version != 0x500 or magic != int.from_bytes(b'OBJS', 'little'):
            raise SaveReadError('Unsupported Sims 3 object format')
        if max(count, instances) > MAX_ITEMS or not 28 <= index_offset < types_offset <= tgi_offset <= len(data):
            raise SaveReadError('Invalid saved object sections')
        index = Reader(data, index_offset, types_offset)
        self.offsets = [index.uint() for _ in range(instances)]
        if self.offsets != sorted(set(self.offsets)) or (self.offsets and not 28 <= self.offsets[0] <= self.offsets[-1] < index_offset):
            raise SaveReadError('Invalid saved object offsets')
        self.instance_end = index_offset
        reader = Reader(data, types_offset, tgi_offset)
        self.types = []
        for _ in range(count):
            name = self.signature(reader)
            field_count = reader.varint(msb=True, signed=True)
            if not -1 <= field_count <= 10000: raise SaveReadError('Invalid saved field count')
            fields = tuple((reader.text(), reader.byte()) for _ in range(max(0, field_count)))
            self.types.append(TypeDefinition(name, fields))
        self.cache = {}
    def signature(self, reader, depth=0):
        if depth > 32: raise SaveReadError('Nested type limit')
        while True:
            flag = reader.byte()
            if flag in (0, 4): return reader.text()
            if 0x20 <= flag <= 0x3f:
                base = self.signature(reader, depth + 1)
                args = [self.signature(reader, depth + 1) for _ in range(flag - 0x20 + 1)]
                return base + '<' + ','.join(args) + '>'
            if flag not in (1, 2, 3) and not 0x10 <= flag < 0x20:
                raise SaveReadError('Unsupported saved type signature')
    def value(self, reader, code, depth=0):
        if depth > 32: raise SaveReadError('Nested value limit')
        if code == 0: return None
        if code == 1: return Ref(reader.uint())
        formats = {2:'?', 3:'B', 4:'H', 5:'q', 7:'d', 8:'h', 9:'i', 10:'q', 11:'b', 12:'f', 13:'H', 14:'I', 15:'Q', 25:'I'}
        if code in formats: return reader.number(formats[code])
        if code == 0x17:
            reader.varint(msb=True)
            return self.value(reader, reader.byte(), depth + 1)
        if code == 0x10:
            typ = reader.uint()
            if typ >= len(self.types): raise SaveReadError('Unknown object type')
            definition = self.types[typ]
            if definition.name == 'System.String': return reader.text()
            if definition.name == 'Sims3.Gameplay.Utilities.SimClockUtils':
                # IPersistable.Write stores exactly SimClock.CurrentTicks as
                # an Int64. Verified against the matching Read implementation.
                return {'_type':definition.name, 'current_ticks':reader.number('q')}
            if definition.name.startswith('System.Collections.Generic.List`1<'):
                return Ref(reader.uint())
            if definition.name in ('System.Collections.Generic.Dictionary`2<System.UInt64,Sims3.Gameplay.Skills.Skill>',
                                   'System.Collections.Generic.Dictionary`2<System.UInt64,Sims3.Gameplay.ActorSystems.Trait>'):
                count = reader.varint(msb=True)
                if count > MAX_ITEMS: raise SaveReadError('Oversized saved dictionary')
                return {reader.number('Q'): Ref(reader.uint()) for _ in range(count)}
            return {'_type': definition.name, **{name:self.value(reader, code, depth + 1) for name, code in definition.fields}}
        if code == 0x11:
            length, element = reader.uint(), reader.byte()
            if length > MAX_ITEMS: raise SaveReadError('Oversized saved array')
            if element == 0x10:
                reader.uint()
                return [Ref(reader.uint()) for _ in range(length)]
            return [self.value(reader, element, depth + 1) for _ in range(length)]
        raise SaveReadError(f'Unsupported saved value {code:02x}')
    def get(self, reference):
        index = reference.index if isinstance(reference, Ref) else reference
        if index == 0: return None
        if not 1 <= index <= len(self.offsets): raise SaveReadError('Invalid saved reference')
        if index not in self.cache:
            end = self.offsets[index] if index < len(self.offsets) else self.instance_end
            reader = Reader(self.data, self.offsets[index-1], end)
            result = self.value(reader, reader.byte())
            if reader.pos != reader.end: raise SaveReadError('Unrecognized object serializer')
            self.cache[index] = result
        return self.cache[index]
    def objects(self, name):
        types = {i for i, value in enumerate(self.types) if value.name == name}
        for index, offset in enumerate(self.offsets, 1):
            if self.data[offset] == 0x10 and struct.unpack_from('<I', self.data, offset + 1)[0] in types:
                yield index, self.get(index)

    def resolve(self, value):
        for _ in range(8):
            if not isinstance(value, Ref): return value
            value = self.get(value)
        raise SaveReadError('Cyclic saved reference')


def default_root():
    configured = os.getenv('SIMS3_SAVES_DIR')
    if configured: return Path(configured).expanduser()
    documents = Path.home() / 'Documents'
    if os.name == 'nt':
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0:
                documents = Path(buffer.value)
        except (OSError, AttributeError): pass
    return documents / 'Electronic Arts' / 'The Sims 3' / 'Saves'


def discover_saves(root=None):
    folder = Path(root) if root else default_root()
    if not folder.is_dir(): return []
    return sorted((path for path in folder.iterdir() if path.is_dir()
                   and path.suffix.casefold() == '.sims3' and not path.is_symlink()), key=lambda p: p.name.casefold())


def save_files(folder):
    folder = Path(folder)
    if folder.suffix.casefold() != '.sims3' or not folder.is_dir():
        raise SaveReadError('Choose a regular .sims3 save folder, not a backup.')
    files = [folder / 'Meta.data'] + sorted(folder.glob('*.nhd'))
    if len(files) < 2 or any(not p.is_file() or p.is_symlink() for p in files):
        raise SaveReadError('The save is incomplete. Finish saving in the game and try again.')
    if sum(p.stat().st_size for p in files) > MAX_BYTES:
        raise SaveReadError('This save is too large for the read-only scanner.')
    return files


def file_signature(folder):
    return tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in save_files(folder))


def metadata(path):
    matches = list(resources(path, {0x628A788F}))
    if len(matches) != 1: raise SaveReadError('The active household metadata could not be identified.')
    reader = Reader(matches[0][3])
    if reader.uint() != 3: raise SaveReadError('Unsupported Sims 3 save metadata version.')
    def utf16():
        length = reader.uint()
        if length > 10000: raise SaveReadError('Oversized save metadata text')
        return reader.take(length * 2).decode('utf-16le')
    world, household = utf16(), utf16()
    reader.uint()
    household_id = reader.number('Q')
    return {'world_name': world, 'household_name': household, 'active_household_game_id': str(household_id)}


def extract_traits(graph, obj):
    """Read a complete trait manager or leave previous traits untouched.

    Sims 3 uses 64-bit trait IDs, not Sims 4 localization hashes. Unknown custom
    IDs remain explicit and lossless; missing/unreadable managers are not empty.
    """
    manager = graph.resolve(obj.get('mTraitManager'))
    if not isinstance(manager, dict): return {}
    values = graph.resolve(manager.get('mValues'))
    if not isinstance(values, dict): return {}
    rewards = graph.resolve(manager.get('mRewardTraits'))
    if rewards is None: rewards = []
    if not isinstance(rewards, list): raise SaveReadError('Unsupported reward trait list')
    identities = {}
    def add(value, expected=None):
        value = graph.resolve(value)
        identity = value.get('mTraitGuid') if isinstance(value, dict) else value
        if type(identity) is not int or not 0 <= identity < 2**64:
            raise SaveReadError('Unreadable saved trait identity')
        if expected is not None and (type(expected) is not int or identity != expected):
            raise SaveReadError('Inconsistent saved trait identity')
        if identity: identities.setdefault(str(identity), None)
    for identity, value in values.items(): add(value, identity)
    for value in rewards: add(value)
    details = []
    for identity in identities:
        identifier = TRAIT_NAMES.get(identity)
        if identifier:
            name = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', identifier)
            name = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', name).replace('_', ' ')
            name = re.sub(r'\b(To|Of|The|A|An|At|In|With|For)\b', lambda m:m[0].lower(), name)
            name = {'Book Worm':'Bookworm', 'Absent Minded':'Absent-Minded',
                    'Avant Garde':'Avant Garde', 'Natural Born Performer':'Natural Born Performer'}.get(name, name)
        else: name = f'Unknown Sims 3 trait (ID {identity})'
        details.append({'name':name, 'trait_id':identity, 'identifier':identifier or '',
                        'resolved':bool(identifier), 'game_edition':'sims3'})
    details.sort(key=lambda item:(item['name'].casefold(), item['trait_id']))
    return {'traits':[item['name'] for item in details], 'trait_details':details, 'traits_scan_supported':True}


def extract_population(graph, meta):
    """Only named, verified fields; unknown time/age duration/health stay absent."""
    prefix = 'Sims3.Gameplay.'
    people = dict(graph.objects(prefix + 'CAS.SimDescription'))
    homes = dict(graph.objects(prefix + 'CAS.Household'))
    def resolve(value): return graph.resolve(value)
    def text_field(obj, field):
        value = resolve(obj.get(field))
        return value.strip() if isinstance(value, str) else ''
    def person_hint(value):
        obj = resolve(value)
        if not isinstance(obj, dict) or not obj.get('mSimDescriptionId'): return None
        return {'game_sim_id': str(obj['mSimDescriptionId']),
                'name': ' '.join(filter(None, (text_field(obj, 'mFirstName'), text_field(obj, 'mLastName')))),
                'sex': 'Female' if obj.get('mSimFlags', 0) & 0x2000 else 'Male' if obj.get('mSimFlags', 0) & 0x1000 else 'Unknown'}
    def genealogy_person(value):
        genealogy = resolve(value)
        if not isinstance(genealogy, dict): return None
        return person_hint(genealogy.get('mSim')) or person_hint(genealogy.get('mMiniSim'))
    sims, warnings = [], set()
    stages = {1:'newborn', 2:'toddler', 4:'child', 8:'teen', 16:'youngadult', 32:'adult', 64:'elder'}
    for index, obj in people.items():
        flags = obj.get('mSimFlags', 0)
        # Exclude pets and service objects without human Sim descriptions.
        if flags & 0xf00 != 0x100: continue
        hint = person_hint(Ref(index))
        if not hint or not hint['name']: continue
        home = resolve(obj.get('mHousehold')) or {}
        stage = stages.get(flags & 0x7f)
        sim = {**hint, 'first_name':text_field(obj,'mFirstName'), 'last_name':text_field(obj,'mLastName'),
               'game_household_id':str(home.get('mHouseholdId') or ''),
               'world_name':meta['world_name'], 'source':'read-only Sims 3 save scan',
               'game_edition':'sims3', 'telemetry_version':0}
        if stage: sim.update(age_stage=stage, is_baby=stage == 'newborn')
        partner = person_hint(obj.get('mPartner'))
        if partner: sim['significant_other_game_id'] = partner['game_sim_id']
        # A non-null serialized Pregnancy proves pregnancy, but absence alone
        # must not invent a delivery or claim zero babies.
        if isinstance(obj.get('Pregnancy'), Ref) and obj['Pregnancy'].index:
            sim.update(is_pregnant=True, pregnancy_scan_supported=True)
        try:
            genealogy = resolve(obj.get('mGenealogy')) or {}
            for field, ids_key, rows_key in [('mNaturalParents','parent_game_sim_ids','parents'),
                                             ('mChildren','child_game_sim_ids','children'),
                                             ('mSiblings','sibling_game_sim_ids','siblings')]:
                refs = resolve(genealogy.get(field))
                if not isinstance(refs, list): continue
                rows = [person for ref in refs if (person := genealogy_person(ref))]
                if rows:
                    sim[ids_key], sim[rows_key] = [p['game_sim_id'] for p in rows], rows
        except (SaveReadError, UnicodeError): warnings.add('Some family links use unsupported data and were left unchanged.')
        try:
            manager = resolve(obj.get('SkillManager')) or {}
            skills = resolve(manager.get('mValues'))
            if isinstance(skills, dict):
                details = []
                for skill_ref in skills.values():
                    skill = resolve(skill_ref)
                    if not isinstance(skill, dict): continue
                    level = skill.get('SkillLevel')
                    if not isinstance(level, int) or not 1 <= level <= 100: continue
                    # Saved script class gives a meaningful identifier without
                    # incorrectly applying Sims 4's hash dictionary to Sims 3.
                    name = skill.get('_type', '').rsplit('.', 1)[-1]
                    if not name or name == 'Skill': continue
                    details.append({'name':name, 'level':level, 'id':str(skill.get('mSkillGuid') or '')})
                if details:
                    sim['skills'] = [f"{s['name']} (level {s['level']})" for s in details]
                    sim['skill_details'] = details
        except (SaveReadError, UnicodeError): warnings.add('Some saved skills could not be decoded and were left unchanged.')
        try:
            sim.update(extract_traits(graph, obj))
        except (SaveReadError, UnicodeError):
            warnings.add('Some saved traits could not be decoded and were left unchanged.')
        sims.append(sim)
    households = []
    for obj in homes.values():
        identity = str(obj.get('mHouseholdId') or '')
        if not identity: continue
        households.append({'game_household_id':identity, 'name':text_field(obj,'mName') or 'Unnamed household',
                           'funds':obj.get('mFamilyFunds'), 'has_home_lot':bool(obj.get('mLotId')),
                           'member_game_ids':[s['game_sim_id'] for s in sims if s['game_household_id'] == identity],
                           'is_player':identity == meta['active_household_game_id'],
                           'is_unplayed':identity != meta['active_household_game_id']})
    return households, sims, sorted(warnings)


def extract_clock(graph):
    clocks = list(graph.objects('Sims3.Gameplay.Utilities.SimClockUtils'))
    if len(clocks) != 1: raise SaveReadError('The saved world clock is missing or ambiguous.')
    ticks = clocks[0][1].get('current_ticks')
    if type(ticks) is not int or not 0 <= ticks <= 54000 * 365 * 10000:
        raise SaveReadError('The saved world clock is outside supported bounds.')
    # Game constants: 37.5 simulator ticks/minute, 2250/hour, 54000/day.
    # Use integer arithmetic; Sunday is zero (the tracker weekday convention).
    day, remainder = divmod(ticks, 54000)
    hour, remainder = divmod(remainder, 2250)
    minute = remainder * 2 // 75
    second = (remainder * 8 // 5) % 60
    return {'ticks':ticks, 'game_day':day, 'game_hour':hour, 'game_minute':minute,
            'game_second':second, 'weekday':('Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')[day % 7],
            'week':day // 7 + 1, 'source':'Sims3.Gameplay.Utilities.SimClockUtils', 'verified':True}


def world_population(path, *, active_meta=None, saved_at=None):
    """Decode one saved world; qualify households whose local IDs can repeat."""
    world_key = path.stem.casefold()
    world_name = path.stem.rsplit('_0x', 1)[0]
    meta = active_meta or {'world_name':world_name, 'active_household_game_id':''}
    matches = list(resources(path, {OBJS}))
    if len(matches) != 1: raise SaveReadError('Could not uniquely identify saved Sim objects.')
    graph = ObjectGraph(matches[0][3])
    households, sims, warnings = extract_population(graph, meta)
    try: saved_clock = extract_clock(graph)
    except SaveReadError as exc:
        saved_clock = None
        warnings.append(f'Clock unavailable: {exc} The tracker day was not inferred.')
    namespace = 'sims3:' + hashlib.sha256(world_key.encode()).hexdigest()[:16] + ':'
    for household in households:
        raw_id = household['game_household_id']
        household.update(game_household_id=namespace + raw_id, raw_household_game_id=raw_id,
                         source_world_key=world_key, world_name=world_name)
    for sim in sims:
        raw_id = sim.get('game_household_id')
        sim.update(game_household_id=namespace + raw_id if raw_id else '',
                   raw_household_game_id=raw_id, source_world_key=world_key,
                   world_name=world_name, source_world_saved_at=saved_at,
                   source_world_active=active_meta is not None)
    active_id = namespace + meta['active_household_game_id'] if active_meta else ''
    if active_meta and (not sims or not any(h['game_household_id'] == active_id for h in households)):
        raise SaveReadError('The active household could not be verified; nothing was imported.')
    return {'key':world_key, 'name':world_name, 'file_name':path.name, 'saved_at':saved_at,
            'active':active_meta is not None, 'households':households, 'sims':sims,
            'active_household_game_id':active_id, 'warnings':warnings, 'clock':saved_clock}


def merge_world_populations(worlds):
    """Current world wins over travel copies; otherwise use newest saved world.

    Never merge older fields into a newer Sim or use a name as its identity.
    Household identities are world-scoped, while Sim IDs stay save-wide.
    """
    ranked = sorted(worlds, key=lambda w:(w['active'], w.get('saved_at') or '', w['key']), reverse=True)
    by_sim, by_home = {}, {}
    duplicates = 0
    for world in ranked:
        for home in world['households']: by_home.setdefault(home['game_household_id'], dict(home))
        for sim in world['sims']:
            if sim['game_sim_id'] in by_sim: duplicates += 1
            else: by_sim[sim['game_sim_id']] = sim
    for home in by_home.values():
        home['member_game_ids'] = [sid for sid,sim in by_sim.items() if sim.get('game_household_id') == home['game_household_id']]
    return list(by_home.values()), list(by_sim.values()), duplicates


def inspect_save(folder, *, quiet_seconds=30, all_worlds=False):
    """Copy a stable completed save, parse only the copy, reject concurrent writes."""
    folder = Path(folder)
    before = file_signature(folder)
    if time.time_ns() - max(row[2] for row in before) < quiet_seconds * 1e9:
        raise SaveReadError('Waiting for the game to finish saving (30 seconds without file changes).')
    digest = hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix='decades-sims3-read-') as temporary:
        copied = Path(temporary)
        for name, _, _ in before:
            shutil.copyfile(folder / name, copied / name)
            digest.update(name.encode())
            with (copied / name).open('rb') as handle:
                for block in iter(lambda:handle.read(1024*1024), b''): digest.update(block)
        if file_signature(folder) != before: raise SaveReadError('Save changed while copying; waiting for the next completed save.')
        meta = metadata(copied / 'Meta.data')
        active_files = [p for p in copied.glob('*.nhd') if p.stem.rsplit('_0x',1)[0].casefold() == meta['world_name'].casefold()]
        if len(active_files) != 1: raise SaveReadError('Could not safely identify the active saved world.')
        saved_times = {name:datetime.fromtimestamp(stamp/1e9, timezone.utc).isoformat() for name,_,stamp in before}
        active = world_population(active_files[0], active_meta=meta, saved_at=saved_times[active_files[0].name])
        worlds, warnings = [active], list(active['warnings'])
        skipped_worlds = []
        if all_worlds:
            for path in sorted(copied.glob('*.nhd')):
                if path == active_files[0]: continue
                try:
                    world = world_population(path, saved_at=saved_times[path.name])
                    worlds.append(world)
                    warnings.extend(world['warnings'])
                except (SaveReadError, UnicodeError) as exc:
                    skipped_worlds.append(path.name)
                    warnings.append(f'{path.name} was not imported: {exc}')
        households, sims, duplicates = merge_world_populations(worlds)
        meta['active_household_game_id'] = active['active_household_game_id']
        saved_clock = ({**active['clock'], 'world_key':active['key'], 'world_name':active['name'],
                        'saved_at':active['saved_at']} if active['clock'] else None)
    if file_signature(folder) != before: raise SaveReadError('The source save changed during scanning. Please try again.')
    return {'game_edition':'sims3', 'path':str(folder.resolve()), 'file_name':folder.name,
            'fingerprint':digest.hexdigest(), 'modified_at':datetime.fromtimestamp(max(r[2] for r in before)/1e9, timezone.utc).isoformat(),
            'size':sum(r[1] for r in before), 'sim_count':len(sims), 'household_count':len(households),
            'portrait_count':0, 'slot':{**meta, 'slot_name':folder.stem, 'game_day':None, **(saved_clock or {})},
            'saved_clock':saved_clock,
            'sims':sims, 'households':households, 'warnings':warnings,
            'all_worlds':all_worlds, 'world_count':len(worlds), 'duplicate_sim_copies':duplicates,
            'worlds':[{k:w[k] for k in ('key','name','file_name','saved_at','active')} for w in worlds],
            'skipped_worlds':skipped_worlds,
            'limitations':'Saved state only, not live. The active world clock is read at save time; event occurrence times are not inferred from it. Exact birth/death times, illnesses, portraits and pregnancy completion are not decoded. Unknown custom trait IDs are retained without guessed names. Unreadable fields are left unchanged.'}
