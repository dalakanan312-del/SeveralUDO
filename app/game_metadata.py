from __future__ import annotations

import json
import os
import re
import struct
import zlib
from functools import lru_cache
from pathlib import Path


STBL_RESOURCE = 0x220557DA
_BUNDLED_LOCALIZATIONS = Path(__file__).with_name("game_localization_fallbacks.json")
_HASH_LABEL = re.compile(
    r"^\s*(?:(?:hash|localization(?:\s+key)?|string(?:\s+id)?)\s*[:#=]?\s*)"
    r"(-?(?:0x)?[0-9a-f]+)\s*$",
    re.IGNORECASE,
)
_UNIDENTIFIED_HASH_LABEL = re.compile(
    r"^\s*(?:unidentified|unknown)\s+(?:custom\s+)?(?:[a-z][a-z\s_-]*?)?\s*"
    r"\(\s*id\s+(-?(?:0x)?[0-9a-f]+)\s*\)\s*$",
    re.IGNORECASE,
)
_TECHNICAL_NAME_PREFIX = re.compile(
    r"^(?:trait|buff|skill|statistic|milestone|developmental\s+milestone|aspiration|degree|career|"
    r"relationship\s*bit|relationshipbit|preference|lifestyle|fear)[\s:_-]+",
    re.IGNORECASE,
)
_LEVEL_SUFFIX = re.compile(r"^(.*?)\s*\(\s*level\s+([^()]+)\s*\)\s*$", re.IGNORECASE)
_ILLNESS_WORDS = (
    "allergy", "anemia", "anxiety", "appendicitis", "asthma", "bronchitis", "cancer",
    "arthritis", "bipolar disorder", "borderline personality disorder",
    "bloaty head", "bubonic plague", "burning belly", "chicken pox", "cholera",
    "cold", "consumption", "deafness", "diabetes", "diphtheria", "depression",
    "dysentery", "ear infection", "eczema", "epilepsy", "flat head syndrome", "flu",
    "food poisoning", "gastroenteritis", "heart attack", "hypertension", "influenza",
    "gas and giggles", "itchy plumbob", "kidney disease", "leprosy", "llama flu",
    "malaria", "measles", "meningitis", "migraine", "mumps",
    "obsessive compulsive disorder", "pneumonia", "postpartum depression",
    "plague", "polio", "rabies", "scarlet fever", "seasonal affective disorder", "sinusitis",
    "sleep disorder", "smallpox", "strep", "tetanus", "tonsillitis",
    "tuberculosis", "typhoid", "typhus", "urinary tract infection", "whooping cough",
    "yeast infection", "yellow fever", "starry eyes", "sweaty shivers", "triple threat",
)
_NON_ACTIVE_WORDS = (
    "immune", "immunity", "immuniz", "vaccin", "recovered", "resolved",
    "resistant", "recent", "cooldown", "capability timer", "enabled", "enabler",
    "core trait", "insurance", "medication", "medicine", "treatment", "remission",
    "therapy", "chemo", "radiation", "followup", "surveillance", "allergy shot",
    "surgery", "transplant", "chance", "eligib", "broadcaster", "commodity",
    "mixer", "loot", "testset", "module", "remove", "unknown", "undiagnosed",
    "notification", "support group", "supportgroup", "ghost", "survivor",
    "cancer free", "management", "prescribed", "prescription", "pill taken",
    "pills taken", "death by", "dying from", "suppression", "suppressor",
)

_ILLNESS_ALIASES = (
    (("urinary tract infection", "urinarytractinfection", "uti buff", "uti trait"), "Urinary Tract Infection"),
    (("yeast infection", "yeastinfection"), "Yeast Infection"),
    (("pregnancy induced anemia", "pregnancyinducedanemia", "pregnancy related anemia"), "Pregnancy-Induced Anemia"),
    (("gestational diabetes", "gestationaldiabetes"), "Gestational Diabetes"),
    (("postpartum depression", "postpartumdepression"), "Postpartum Depression"),
    (("postpartum hemorrhage", "postpartum haemorrhage", "postpartumhemorrhage", "postpartumhaemorrhage"), "Postpartum Hemorrhage"),
    (("seasonal affective disorder", "seasonalaffectivedisorder"), "Seasonal Affective Disorder"),
    (("borderline personality disorder", "borderlinepersonalitydisorder"), "Borderline Personality Disorder"),
    (("obsessive compulsive disorder", "obsessivecompulsivedisorder"), "Obsessive Compulsive Disorder"),
    (("flat head syndrome", "flatheadsyndrome"), "Flat Head Syndrome"),
    (("gastroenteritis", "stomach flu", "stomachflu"), "Gastroenteritis"),
    (("ear infection", "earinfection"), "Ear Infection"),
    (("whooping cough", "whoopingcough", "pertussis"), "Whooping Cough"),
    (("breast cancer", "breastcancer"), "Breast Cancer"),
    (("colon cancer", "coloncancer"), "Colon Cancer"),
    (("prostate cancer", "prostatecancer"), "Prostate Cancer"),
    (("kidney disease", "kidneydisease"), "Kidney Disease"),
    (("kidney failure", "kidneyfailure"), "Kidney Failure"),
    (("heart attack", "heartattack"), "Heart Attack"),
    (("blood clot", "bloodclot"), "Blood Clot"),
    (("pulmonary embolism", "pulmonaryembolism"), "Pulmonary Embolism"),
    (("animal dander allergy", "animaldanderallergy", "pet dander allergy", "petdanderallergy"), "Animal Dander Allergy"),
    (("bee allergy", "beeallergy"), "Bee Allergy"),
    (("influenza", "flu buff", "flubuff", "flu trait", "flutrait"), "Influenza"),
    (("tuberculosis",), "Tuberculosis"),
    (("meningitis",), "Meningitis"),
    (("pneumonia",), "Pneumonia"),
    (("tonsillitis",), "Tonsillitis"),
    (("bronchitis",), "Bronchitis"),
    (("sinusitis", "sinus infection"), "Sinusitis"),
    (("malaria",), "Malaria"),
    (("common cold", "commoncold", "cold buff", "coldbuff", "cold trait", "coldtrait"), "Cold"),
    (("appendicitis",), "Appendicitis"),
    (("diphtheria",), "Diphtheria"),
    (("dysentery",), "Dysentery"),
    (("smallpox", "small pox"), "Smallpox"),
    (("chicken pox", "chickenpox"), "Chicken Pox"),
    (("scarlet fever", "scarletfever"), "Scarlet Fever"),
    (("yellow fever", "yellowfever"), "Yellow Fever"),
    (("typhoid",), "Typhoid"),
    (("typhus",), "Typhus"),
    (("cholera",), "Cholera"),
    (("measles",), "Measles"),
    (("mumps",), "Mumps"),
    (("polio",), "Polio"),
    (("rabies",), "Rabies"),
    (("tetanus",), "Tetanus"),
    (("cancer",), "Cancer"),
    (("severe anemia", "severeanemia", "anemia", "anaemia"), "Anemia"),
    (("hypertension",), "Hypertension"),
    (("diabetes",), "Diabetes"),
    (("asthma",), "Asthma"),
    (("arthritis",), "Arthritis"),
    (("eczema",), "Eczema"),
    (("migraine",), "Migraine"),
    (("sleep disorder", "sleepdisorder"), "Sleep Disorder"),
    (("anxiety",), "Anxiety"),
    (("depression",), "Depression"),
    (("deafness",), "Deafness"),
    (("general allergies", "allergies", "allergy"), "Allergy"),
    (("sepsis",), "Sepsis"),
    (("hemorrhage", "haemorrhage"), "Hemorrhage"),
)


def canonical_illness_name(value: str) -> str:
    """Normalize readable or technical disease markers without importing a mod."""
    raw = " ".join(str(value or "").replace("_", " ").replace("-", " ").split()).strip()
    folded = raw.casefold()
    compact = re.sub(r"[^a-z0-9]+", "", folded)
    if not raw or any(word.replace(" ", "") in compact for word in _NON_ACTIVE_WORDS):
        return ""
    for aliases, canonical in _ILLNESS_ALIASES:
        for alias in aliases:
            alias_folded = alias.casefold()
            if alias_folded in folded or re.sub(r"[^a-z0-9]+", "", alias_folded) in compact:
                return canonical
    return ""


def inactive_health_marker(value: str) -> bool:
    """Return whether telemetry describes treatment/state machinery, not disease.

    Optional health mods expose many internal buffs beside actual conditions.
    Keeping this check public lets both metadata enrichment and the receiver apply
    the same conservative filter even to older Clock Sync payloads.
    """
    raw = " ".join(str(value or "").replace("_", " ").replace("-", " ").split()).casefold()
    compact = re.sub(r"[^a-z0-9]+", "", raw)
    return bool(raw and any(word.replace(" ", "") in compact for word in _NON_ACTIVE_WORDS))


def _mods_root() -> Path:
    profile = Path(os.environ.get("USERPROFILE") or Path.home())
    return profile / "Documents" / "Electronic Arts" / "The Sims 4" / "Mods"


def _sims_install_roots() -> tuple[Path, ...]:
    configured = os.environ.get("SIMS4_INSTALL_DIR")
    candidates = [Path(configured)] if configured else []
    candidates.extend((
        Path(r"C:\Program Files\EA Games\The Sims 4"),
        Path(r"C:\Program Files (x86)\Origin Games\The Sims 4"),
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\The Sims 4"),
    ))
    return tuple(dict.fromkeys(path for path in candidates if path.is_dir()))


def _healthcare_packages() -> tuple[Path, ...]:
    root = _mods_root()
    if not root.is_dir():
        return ()
    folders: set[Path] = {root}
    common = root / "game mods"
    if (common / "HealthcareRedux.log").is_file():
        folders.add(common)
    else:
        try:
            for current, _, filenames in os.walk(root):
                if any(name.casefold() == "healthcareredux.log" for name in filenames):
                    folders.add(Path(current))
        except OSError:
            return ()
    packages: set[Path] = set()
    for folder in folders:
        try:
            for path in folder.glob("*.package"):
                folded = path.name.casefold()
                if "healthcareredux" in folded or "healthcare_redux" in folded:
                    packages.add(path)
        except OSError:
            continue
    return tuple(sorted(packages))


def _responsible_pregnancy_packages() -> tuple[Path, ...]:
    """Find only Kemzima Responsible Pregnancy packages, when installed."""
    root = _mods_root()
    if not root.is_dir():
        return ()
    packages: set[Path] = set()
    try:
        for current, _, filenames in os.walk(root):
            for name in filenames:
                folded = name.casefold()
                if folded.endswith(".package") and "responsiblepregnancy" in folded and "kemzima" in folded:
                    packages.add(Path(current) / name)
    except OSError:
        return ()
    return tuple(sorted(packages))


def _dbpf_index_metadata(header: bytes):
    if len(header) < 72 or header[:4] != b"DBPF":
        return None
    entry_count = struct.unpack_from("<I", header, 36)[0]
    index_offset_low = struct.unpack_from("<I", header, 40)[0]
    index_size = struct.unpack_from("<I", header, 44)[0]
    index_offset_64 = struct.unpack_from("<Q", header, 64)[0]
    return entry_count, index_offset_64 or index_offset_low, index_size


def _parse_dbpf_index(index: bytes, entry_count: int, package_size: int):
    if not entry_count or len(index) < 4:
        return
    cursor = 0
    index_flags = struct.unpack_from("<I", index, cursor)[0]
    cursor += 4
    common_type = struct.unpack_from("<I", index, cursor)[0] if index_flags & 1 else None
    cursor += 4 if common_type is not None else 0
    common_group = struct.unpack_from("<I", index, cursor)[0] if index_flags & 2 else None
    cursor += 4 if common_group is not None else 0
    common_instance_high = struct.unpack_from("<I", index, cursor)[0] if index_flags & 4 else None
    cursor += 4 if common_instance_high is not None else 0
    for _ in range(entry_count):
        try:
            type_id = common_type if common_type is not None else struct.unpack_from("<I", index, cursor)[0]
            cursor += 0 if common_type is not None else 4
            group_id = common_group if common_group is not None else struct.unpack_from("<I", index, cursor)[0]
            cursor += 0 if common_group is not None else 4
            instance_high = common_instance_high if common_instance_high is not None else struct.unpack_from("<I", index, cursor)[0]
            cursor += 0 if common_instance_high is not None else 4
            instance_low, offset, stored_size, raw_size, compression, committed = struct.unpack_from("<IIIIHH", index, cursor)
            cursor += 20
        except struct.error:
            return
        stored_size &= 0x7FFFFFFF
        if offset + stored_size > package_size:
            continue
        yield type_id, group_id, (instance_high << 32) | instance_low, offset, stored_size, raw_size, compression, committed


def _dbpf_entries(raw: bytes):
    metadata = _dbpf_index_metadata(raw[:72])
    if not metadata:
        return
    entry_count, index_offset, index_size = metadata
    if index_offset + min(index_size, 4) > len(raw):
        return
    yield from _parse_dbpf_index(raw[index_offset:index_offset + index_size], entry_count, len(raw)) or ()


def _resource_bytes(package: bytes, entry) -> bytes | None:
    _, _, _, offset, stored_size, raw_size, compression, _ = entry
    return _decode_resource(package[offset:offset + stored_size], stored_size, raw_size, compression)


def _decode_resource(stored: bytes, stored_size: int, raw_size: int, compression: int) -> bytes | None:
    if len(stored) >= 5 and stored[1] == 0xFB:
        return _refpack_decompress(stored, raw_size)
    if compression in (0, 0xFFFF) or stored_size == raw_size:
        return stored
    if compression == 0x5A42:
        try:
            return zlib.decompress(stored)
        except zlib.error:
            return None
    return None


def _refpack_decompress(stored: bytes, expected_size: int = 0) -> bytes | None:
    """Decode the RefPack/QFS compression used by Sims string resources."""
    if len(stored) < 5 or stored[1] != 0xFB:
        return None
    declared_size = int.from_bytes(stored[2:5], "big")
    cursor = 5
    output = bytearray()
    try:
        while cursor < len(stored):
            control = stored[cursor]
            cursor += 1
            plain = copy_length = copy_offset = 0
            if control >= 0xFC:
                plain = control & 0x03
                output.extend(stored[cursor:cursor + plain])
                cursor += plain
                break
            if control >= 0xE0:
                plain = ((control & 0x1F) << 2) + 4
            elif control >= 0xC0:
                first, second, third = stored[cursor:cursor + 3]
                cursor += 3
                plain = control & 0x03
                copy_length = ((control & 0x0C) << 6) + third + 5
                copy_offset = ((control & 0x10) << 12) + (first << 8) + second + 1
            elif control >= 0x80:
                first, second = stored[cursor:cursor + 2]
                cursor += 2
                plain = (first >> 6) & 0x03
                copy_length = (control & 0x3F) + 4
                copy_offset = ((first & 0x3F) << 8) + second + 1
            else:
                first = stored[cursor]
                cursor += 1
                plain = control & 0x03
                copy_length = ((control & 0x1C) >> 2) + 3
                copy_offset = ((control & 0x60) << 3) + first + 1
            if cursor + plain > len(stored):
                return None
            output.extend(stored[cursor:cursor + plain])
            cursor += plain
            if copy_length:
                if copy_offset <= 0 or copy_offset > len(output):
                    return None
                for _ in range(copy_length):
                    output.append(output[-copy_offset])
        required = expected_size or declared_size
        if required and len(output) != required:
            return None
        return bytes(output)
    except (IndexError, ValueError):
        return None


def _read_stbl(raw: bytes, wanted_keys: set[int] | None = None) -> dict[int, str]:
    if len(raw) < 21 or raw[:4] != b"STBL":
        return {}
    try:
        count = struct.unpack_from("<Q", raw, 7)[0]
    except struct.error:
        return {}
    cursor = 21
    result: dict[int, str] = {}
    for _ in range(count):
        try:
            key, _, length = struct.unpack_from("<IBH", raw, cursor)
        except struct.error:
            break
        cursor += 7
        value = raw[cursor:cursor + length]
        cursor += length
        if len(value) != length:
            break
        text = value.decode("utf-8", errors="replace").strip("\x00 ")
        if text and (wanted_keys is None or key in wanted_keys):
            result[key] = text
    return result


def _package_localizations(paths, wanted_keys: set[int] | None = None) -> dict[int, str]:
    labels: dict[int, str] = {}
    targets = set(wanted_keys) if wanted_keys is not None else None
    for path in paths:
        try:
            with path.open("rb") as package:
                header = package.read(72)
                metadata = _dbpf_index_metadata(header)
                if not metadata:
                    continue
                entry_count, index_offset, index_size = metadata
                package.seek(0, 2)
                package_size = package.tell()
                package.seek(index_offset)
                index = package.read(index_size)
                entries = tuple(_parse_dbpf_index(index, entry_count, package_size) or ())
                for entry in entries:
                    if entry[0] != STBL_RESOURCE:
                        continue
                    _, _, _, offset, stored_size, raw_size, compression, _ = entry
                    package.seek(offset)
                    resource = _decode_resource(package.read(stored_size), stored_size, raw_size, compression)
                    if resource:
                        labels.update(_read_stbl(resource, targets))
                        if targets is not None and targets.issubset(labels):
                            return labels
        except OSError:
            continue
    return labels


@lru_cache(maxsize=1)
def game_localizations() -> dict[int, str]:
    """Load the local game's English labels without touching the running game."""
    packages: set[Path] = set()
    for root in _sims_install_roots():
        try:
            packages.update(root.rglob("Strings_ENG_US.package"))
        except OSError:
            continue
    return _package_localizations(sorted(packages))


@lru_cache(maxsize=1)
def gameplay_mod_localizations() -> dict[int, str]:
    """Read localization only from the small gameplay-mod folder, never the full CC library."""
    root = _mods_root() / "game mods"
    try:
        packages = tuple(root.rglob("*.package")) if root.is_dir() else ()
    except OSError:
        packages = ()
    return _package_localizations(packages)


@lru_cache(maxsize=1)
def healthcare_localizations() -> dict[int, str]:
    """Read Healthcare Redux's own localized labels without importing its mod."""
    return _package_localizations(_healthcare_packages())


@lru_cache(maxsize=1)
def responsible_pregnancy_localizations() -> dict[int, str]:
    """Read Kemzima's labels without importing or requiring the mod."""
    return _package_localizations(_responsible_pregnancy_packages())


@lru_cache(maxsize=1)
def bundled_localizations() -> dict[int, str]:
    """Load the compact, non-personal name dictionary shipped to hosted editions."""
    try:
        payload = json.loads(_BUNDLED_LOCALIZATIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    rows = payload.get("names", payload) if isinstance(payload, dict) else {}
    result: dict[int, str] = {}
    for raw_key, raw_name in rows.items() if isinstance(rows, dict) else ():
        try:
            key = int(str(raw_key), 0) & 0xFFFFFFFF
        except (TypeError, ValueError):
            continue
        name = str(raw_name or "").strip()
        if key and name:
            result[key] = name
    return result


@lru_cache(maxsize=1)
def trait_localizations() -> dict[int, str]:
    # Railway cannot access a player's Sims installation. Keep a compact set
    # of observed public game labels in the app, then let local files override
    # it when the desktop edition is available.
    labels = dict(bundled_localizations())
    labels.update(game_localizations())
    labels.update(gameplay_mod_localizations())
    labels.update(healthcare_localizations())
    labels.update(responsible_pregnancy_localizations())
    return labels


def localization_hash(value) -> int | None:
    """Return a normalized 32-bit Sims localization key from common displays."""
    if isinstance(value, dict):
        for key in ("localization_key", "localization_hash", "hash", "string_id"):
            if value.get(key) not in (None, ""):
                value = value.get(key)
                break
        else:
            value = value.get("name") or value.get("display_name") or value.get("title") or value.get("trait") or ""
    if isinstance(value, int):
        return int(value) & 0xFFFFFFFF if value else None
    label = str(value or "").strip()
    match = _HASH_LABEL.match(label) or _UNIDENTIFIED_HASH_LABEL.match(label)
    if not match:
        return None
    raw = match.group(1)
    try:
        unsigned = raw.casefold().lstrip("-")
        key = int(raw, 16 if unsigned.startswith("0x") or re.search(r"[a-f]", unsigned) else 10) & 0xFFFFFFFF
    except ValueError:
        return None
    return key or None


def _clean_game_name(value: str, kind: str = "") -> str:
    name = str(value or "").strip()
    choices = re.findall(r"\{(?:T|M|F|U|DAE)\d*\.([^}]+)\}", name)
    if choices:
        name = " / ".join(dict.fromkeys(choices))
    name = name.replace("\ufffd", "é")
    technical = bool(_TECHNICAL_NAME_PREFIX.match(name) or "_" in name or re.search(r"(?<=[a-z0-9])(?=[A-Z])", name))
    name = _TECHNICAL_NAME_PREFIX.sub("", name)
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    name = name.replace("_", " ")
    if technical:
        name = name.replace("-", " ")
    name = " ".join(name.split()).strip()
    name = re.sub(r"\bWoo\s+Hoo\b", "WooHoo", name, flags=re.IGNORECASE)
    if kind:
        folded = kind.casefold().replace("_", " ")
        if name.casefold().startswith(folded + " "):
            name = name[len(folded):].strip()
    return name


def _named_values(value) -> list:
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        return [{"name": key, "value": item} for key, item in value.items()]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _detail_name(value, kind: str = "") -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("display_name") or value.get("title") or value.get("trait") or value.get("skill") or ""
    return _clean_game_name(str(value or ""), kind)


def localization_aliases(value, details=None, kind: str = "") -> dict[int, str]:
    """Pair hashes with Clock Sync's stable tuning details when possible."""
    values = _named_values(value)
    detail_rows = _named_values(details)
    by_tuning = {
        str(row.get("tuning_id")): _detail_name(row, kind)
        for row in detail_rows if isinstance(row, dict) and row.get("tuning_id") and _detail_name(row, kind)
    }
    aliases: dict[int, str] = {}
    positionally_aligned = bool(values) and len(values) == len(detail_rows)
    for index, item in enumerate(values):
        key = localization_hash(item)
        if not key:
            continue
        detail = ""
        if isinstance(item, dict) and item.get("tuning_id") is not None:
            detail = by_tuning.get(str(item.get("tuning_id")), "")
        if not detail and positionally_aligned and index < len(detail_rows):
            detail = _detail_name(detail_rows[index], kind)
        if detail and localization_hash(detail) is None:
            aliases[key] = detail
    return aliases


def readable_named_labels(value, details=None, *, kind: str = "",
                          localizations: dict[int, str] | None = None,
                          aliases: dict[int, str] | None = None) -> list[str]:
    """Render any Clock Sync named collection without leaking hash labels."""
    values = _named_values(value)
    detail_rows = _named_values(details)
    known_aliases = dict(aliases or {})
    known_aliases.update(localization_aliases(values, detail_rows, kind))
    needs_catalog = any(localization_hash(item) for item in values)
    catalog = (trait_localizations() if localizations is None else localizations) if needs_catalog else (localizations or {})
    by_tuning = {
        str(row.get("tuning_id")): _detail_name(row, kind)
        for row in detail_rows if isinstance(row, dict) and row.get("tuning_id") and _detail_name(row, kind)
    }
    labels: list[str] = []
    positionally_aligned = bool(values) and len(values) == len(detail_rows)
    for index, item in enumerate(values):
        raw = item
        level = None
        tuning_id = None
        if isinstance(item, dict):
            raw = item.get("name") or item.get("display_name") or item.get("title") or item.get("trait") or item.get("skill") or ""
            level = item.get("level", item.get("value"))
            tuning_id = item.get("tuning_id")
        else:
            level_match = _LEVEL_SUFFIX.match(str(raw or ""))
            if level_match:
                raw, level = level_match.group(1), level_match.group(2)
        key = localization_hash(raw)
        if key is None and (_HASH_LABEL.match(str(raw or "")) or _UNIDENTIFIED_HASH_LABEL.match(str(raw or ""))):
            # Zero is a missing/empty localization sentinel, not a real game
            # name, so do not expose it on Sim profiles.
            continue
        if key:
            label = _clean_game_name(catalog.get(key) or known_aliases.get(key) or "", kind)
            if not label and tuning_id is not None:
                label = by_tuning.get(str(tuning_id), "")
            if not label and positionally_aligned and index < len(detail_rows):
                label = _detail_name(detail_rows[index], kind)
            if not label:
                # Keep the stable identifier in metadata, but never expose a
                # raw "hash: ..." string as if it were a name.
                unknown_kind = "custom trait" if kind == "trait" else (kind or "game value")
                label = f"Unidentified {unknown_kind} (ID {key})"
        else:
            label = _clean_game_name(str(raw or ""), kind)
        if label and level not in (None, ""):
            label = f"{label} (level {level})"
        if label and label not in labels:
            labels.append(label)
    return labels


def readable_trait_label(value, localizations: dict[int, str] | None = None) -> str:
    """Turn a Clock Sync localization hash into a readable local trait label."""
    labels = readable_named_labels(value, kind="trait", localizations=localizations)
    return labels[0] if labels else ""


def readable_trait_labels(value, localizations: dict[int, str] | None = None) -> list[str]:
    return readable_named_labels(value, kind="trait", localizations=localizations)


_RESPONSIBLE_PREGNANCY_RULES = (
    (("suddeninfantdeathsyndrome",), "sids", "Sudden infant death syndrome", "Critical newborn outcome", "critical"),
    (("congenitaltoxoplasmosis",), "congenital-toxoplasmosis", "Congenital toxoplasmosis", "Newborn complication", "critical"),
    (("lowbirthweight",), "low-birth-weight", "Low birth weight", "Newborn complication", "high"),
    (("colickybaby",), "colicky-baby", "Colicky baby", "Newborn condition", "moderate"),
    (("temporarilylactoseintolerant",), "temporary-lactose-intolerance", "Temporary lactose intolerance", "Newborn condition", "moderate"),
    (("breastmilkhealth", "taintedmilk"), "feeding-health-risk", "Infant feeding health risk", "Newborn care", "moderate"),
    (("toxoplasmosisinfection",), "toxoplasmosis", "Toxoplasmosis infection", "Pregnancy complication", "high"),
    (("pregnancyinsomnia",), "pregnancy-insomnia", "Pregnancy insomnia", "Pregnancy condition", "moderate"),
    (("overexertedpregnancy",), "prenatal-overexertion", "Prenatal overexertion", "Pregnancy risk", "moderate"),
    (("sciaticaflare",), "sciatica-flare", "Sciatica flare", "Pregnancy condition", "moderate"),
    (("recentlyconsumedalcohol", "alcoholinpast24hours"), "alcohol-exposure", "Alcohol exposure", "Pregnancy exposure", "high"),
    (("recentlyconsumeddrugs",), "drug-exposure", "Recreational drug exposure", "Pregnancy exposure", "high"),
    (("secondhandsmoke",), "secondhand-smoke", "Secondhand smoke exposure", "Pregnancy exposure", "moderate"),
    (("recentlyconsumedcaffeine", "caffeinebuffwarning", "caffeinewarning"), "caffeine-exposure", "Caffeine exposure", "Pregnancy exposure", "low"),
    (("dietunwell",), "nutrition-unwell", "Prenatal nutrition — unwell", "Prenatal nutrition", "moderate"),
    (("dietsluggish",), "nutrition-sluggish", "Prenatal nutrition — sluggish", "Prenatal nutrition", "low"),
    (("dietwellnourished",), "nutrition-well-nourished", "Prenatal nutrition — well nourished", "Prenatal nutrition", "positive"),
    (("diethealthymeal",), "nutrition-healthy-meal", "Prenatal nutrition — healthy meal", "Prenatal nutrition", "positive"),
    (("chemicalheadache",), "chemical-headache", "Chemical exposure headache", "Environmental exposure", "moderate"),
    (("toxicfumes",), "toxic-fumes", "Toxic fumes exposure", "Environmental exposure", "high"),
    (("airpollution",), "air-pollution", "Air pollution exposure", "Environmental exposure", "moderate"),
    (("moldsystem",), "mold-exposure", "Mold exposure", "Environmental exposure", "moderate"),
    (("gardeningspray", "insectrepellent", "eaudebleach", "graffitipaint", "chemicallab"),
     "chemical-exposure", "Household chemical exposure", "Environmental exposure", "moderate"),
)


def _responsible_pregnancy_marker(value) -> dict | None:
    if isinstance(value, dict):
        technical = str(value.get("technical_name") or value.get("raw_name") or value.get("name") or "")
    else:
        technical = str(value or "")
    compact = re.sub(r"[^a-z0-9]+", "", technical.casefold())
    if "responsiblepregnancy" not in compact and "kemzima" not in compact:
        return None
    for aliases, key, name, category, severity in _RESPONSIBLE_PREGNANCY_RULES:
        if not any(alias in compact for alias in aliases):
            continue
        if key == "toxoplasmosis":
            stage = re.search(r"stage\s*([123])", technical, re.IGNORECASE)
            if stage:
                name += " — stage " + stage.group(1)
        return {"key": key, "name": name, "category": category, "severity": severity}
    return None


def responsible_pregnancy_states(value) -> list[dict]:
    """Normalize Clock Sync's optional-mod states into a stable UI contract."""
    rows = _named_values(value)
    result: dict[str, dict] = {}
    for raw in rows:
        supplied = dict(raw) if isinstance(raw, dict) else {}
        key = str(supplied.get("key") or "").strip().casefold()
        name = str(supplied.get("name") or supplied.get("label") or "").strip()
        category = str(supplied.get("category") or "").strip()
        severity = str(supplied.get("severity") or "").strip().casefold()
        classified = _responsible_pregnancy_marker(raw)
        if classified:
            key = key or classified["key"]
            name = name or classified["name"]
            category = category or classified["category"]
            severity = severity or classified["severity"]
        if not key or not name:
            continue
        result[key] = {
            **supplied,
            "key": key,
            "name": name,
            "category": category or "Responsible Pregnancy",
            "severity": severity or "unrated",
            "provider": "Kemzima Responsible Pregnancy",
        }
    return sorted(result.values(), key=lambda row: (row["category"], row["name"]))


OCCULT_ORDER = (
    "Alien", "Vampire", "Mermaid", "Spellcaster", "Werewolf", "Fairy",
    "Ghost", "Servo", "PlantSim", "Skeleton",
)

_OCCULT_MARKERS = {
    "Alien": ("occultalien", "occult alien", "alien occult", "is alien"),
    "Vampire": ("occultvampire", "occult vampire", "vampire occult", "is vampire"),
    "Mermaid": ("occultmermaid", "occult mermaid", "mermaid occult", "is mermaid"),
    "Spellcaster": ("witchoccult", "occultwitch", "occult witch", "occult spellcaster", "is spellcaster"),
    "Werewolf": ("occultwerewolf", "occult werewolf", "werewolf occult", "is werewolf"),
    "Fairy": ("occultfairy", "occult fairy", "fairy occult", "is fairy"),
    "Ghost": ("occultghost", "occult ghost", "ghost occult", "is ghost"),
    "Servo": ("humanoid robots maintrait", "humanoid robot main trait", "occultservo", "is servo"),
    "PlantSim": ("occultplantsim", "occult plant sim", "plantsim trait", "plant sim trait"),
    "Skeleton": ("occultskeleton", "occult skeleton", "skeleton trait"),
}


def _occult_label(value) -> str:
    text = " ".join(str(value or "").replace("_", " ").replace("-", " ").split()).strip()
    folded = re.sub(r"[^a-z0-9]+", "", text.casefold())
    aliases = {
        "human":"Human", "alien":"Alien", "vampire":"Vampire",
        "mermaid":"Mermaid", "merman":"Mermaid", "witch":"Spellcaster",
        "spellcaster":"Spellcaster", "werewolf":"Werewolf", "fairy":"Fairy",
        "ghost":"Ghost", "servo":"Servo", "robot":"Servo",
        "plantsim":"PlantSim", "skeleton":"Skeleton",
    }
    for prefix in ("occulttype", "occult"):
        if folded.startswith(prefix) and folded[len(prefix):] in aliases:
            return aliases[folded[len(prefix):]]
    if folded in aliases:
        return aliases[folded]
    for label in OCCULT_ORDER:
        compact = re.sub(r"[^a-z0-9]+", "", label.casefold())
        if folded.endswith(compact) or folded.startswith(compact):
            return label
    return ""


def occult_identity(snapshot: dict, localizations: dict[int, str] | None = None) -> dict:
    """Normalize explicit Clock Sync state or infer positive occult traits safely."""
    explicit = snapshot.get("occult_types")
    if explicit in (None, ""):
        explicit = snapshot.get("occult_type", snapshot.get("species_occult"))
    values = explicit if isinstance(explicit, (list, tuple, set)) else (() if explicit in (None, "") else (explicit,))
    found = []
    for value in values:
        label = _occult_label(value)
        if label and label != "Human" and label not in found:
            found.append(label)

    source = "clock-sync-explicit" if explicit not in (None, "") or snapshot.get("occult_scan_supported") else "trait-inference"
    if not found:
        trait_labels = readable_trait_labels(snapshot.get("traits"), localizations)
        for raw in trait_labels:
            searchable = " ".join(str(raw or "").replace("_", " ").replace("-", " ").split()).casefold()
            compact = re.sub(r"[^a-z0-9]+", "", searchable)
            exact = _occult_label(raw) if compact in {
                "alien", "vampire", "mermaid", "merman", "witch", "spellcaster",
                "werewolf", "fairy", "ghost", "servo", "robot", "plantsim", "skeleton",
            } else ""
            if exact and exact != "Human" and exact not in found:
                found.append(exact)
            for label in OCCULT_ORDER:
                markers = _OCCULT_MARKERS[label]
                if any(marker.replace(" ", "") in compact for marker in markers) and label not in found:
                    found.append(label)
    found.sort(key=lambda item: OCCULT_ORDER.index(item) if item in OCCULT_ORDER else len(OCCULT_ORDER))
    authoritative = bool(snapshot.get("occult_scan_supported") or explicit not in (None, ""))
    if found:
        display = found[0] if len(found) == 1 else f"Hybrid ({' / '.join(found)})"
    elif authoritative:
        display = "Human"
    else:
        display = ""
    return {"display": display, "types": found, "source": source, "authoritative": authoritative}


def _healthcare_trait_name(label, localizations: dict[int, str]) -> str:
    if isinstance(label, dict):
        label = label.get("name") or label.get("display_name") or label.get("title") or label.get("trait") or ""
    text = str(label or "").strip()
    match = _HASH_LABEL.match(text)
    if not match:
        return text
    return localizations.get(int(match.group(1)), "").strip()


def illness_name_from_localized_label(label: str, localizations: dict[int, str] | None = None) -> tuple[str, str] | None:
    labels = localizations if localizations is not None else healthcare_localizations()
    name = _healthcare_trait_name(label, labels)
    folded = name.casefold()
    if not name or any(word in folded for word in _NON_ACTIVE_WORDS):
        return None
    canonical = canonical_illness_name(name)
    if not canonical and not any(word in folded for word in _ILLNESS_WORDS):
        return None
    canonical = canonical or name
    stable_key = re.sub(r"[^a-z0-9]+", "-", canonical.casefold()).strip("-")
    return stable_key, canonical


def _signature_illness(label, signatures: list[dict] | None) -> tuple[str, str] | None:
    raw = str(label or "").strip()
    readable = readable_trait_label(raw)
    for item in signatures or ():
        if not bool(item.get("active", True)):
            continue
        pattern = str(item.get("pattern") or "").strip()
        if not pattern:
            continue
        mode = str(item.get("match_type") or "contains").casefold()
        target = raw if mode == "hash" else readable
        matched = target.casefold() == pattern.casefold() if mode in {"exact", "hash"} else pattern.casefold() in target.casefold()
        if matched:
            name = str(item.get("illness_name") or readable or raw).strip()
            key = re.sub(r"[^a-z0-9]+", "-", pattern.casefold()).strip("-")
            return f"custom-signature:{key}", name
    return None


def trait_illnesses(snapshot: dict, localizations: dict[int, str] | None = None,
                    signatures: list[dict] | None = None) -> list[dict]:
    """Promote recognized illness traits already present in Clock Sync telemetry."""
    labels = localizations if localizations is not None else healthcare_localizations()
    found: dict[str, dict] = {}
    for label in snapshot.get("traits") or ():
        detected = _signature_illness(label, signatures) or illness_name_from_localized_label(label, labels)
        if not detected:
            continue
        key, name = detected
        source_key = f"healthcare-redux-trait:{key}"
        found[source_key] = {
            "source_key": source_key,
            "name": name,
            "provider": "Healthcare Redux trait",
        }
    return list(found.values())


def unclassified_health_traits(snapshot: dict, localizations: dict[int, str] | None = None,
                               signatures: list[dict] | None = None) -> list[dict]:
    """Return active-looking traits from a health package for human review.

    They are deliberately not recorded as illnesses until the player approves a
    name. This catches new mod releases without turning immunity or treatment
    markers into false illness episodes.
    """
    labels = localizations if localizations is not None else healthcare_localizations()
    known_values = {" ".join(value.split()).casefold() for value in labels.values() if value}
    result = []
    for raw in snapshot.get("traits") or ():
        if _signature_illness(raw, signatures) or illness_name_from_localized_label(raw, labels):
            continue
        source = str(raw or "").strip()
        readable = readable_trait_label(source, labels)
        folded = readable.casefold()
        match = _HASH_LABEL.match(source)
        belongs_to_health_package = bool(match and int(match.group(1)) in labels) or folded in known_values
        illness_like = any(word in folded for word in _ILLNESS_WORDS)
        if not (belongs_to_health_package or illness_like) or not readable or any(word in folded for word in _NON_ACTIVE_WORDS):
            continue
        if readable.startswith("Unidentified custom trait"):
            continue
        result.append({"raw": source, "label": readable})
    return result


def healthcare_scan_supported(snapshot: dict, localizations: dict[int, str] | None = None) -> bool:
    """Confirm Healthcare Redux telemetry is present, even when the Sim is healthy."""
    labels = localizations if localizations is not None else healthcare_localizations()
    for label in snapshot.get("traits") or ():
        name = _healthcare_trait_name(label, labels).casefold()
        if "healthcare redux core trait" in name or "healthcare enabled trait" in name:
            return True
    return False


def enrich_illness_snapshot(snapshot: dict, signatures: list[dict] | None = None) -> dict:
    labels = healthcare_localizations()
    inferred = trait_illnesses(snapshot, labels, signatures)
    supported = (
        bool(snapshot.get("illness_scan_supported"))
        or "illnesses" in snapshot
        or healthcare_scan_supported(snapshot, labels)
    )
    unknown = unclassified_health_traits(snapshot, labels, signatures)
    if not inferred and not supported and not unknown:
        return snapshot
    merged: dict[str, dict] = {}
    for item in list(snapshot.get("illnesses") or ()) + inferred:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        searchable = " ".join((
            name, str(item.get("source_key") or ""), str(item.get("provider") or ""),
        )).casefold()
        if not name or inactive_health_marker(searchable):
            continue
        canonical = canonical_illness_name(searchable)
        if canonical:
            item = {**item, "raw_name": item.get("raw_name") or name, "name": canonical}
            name = canonical
        key = str(item.get("source_key") or item.get("name") or "").casefold()
        if key:
            merged[key] = item
    return {**snapshot, "illness_scan_supported": True, "illnesses": list(merged.values()),
            "unknown_health_traits": unknown}
