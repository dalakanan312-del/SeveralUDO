from __future__ import annotations

import os
import re
import struct
import zlib
from functools import lru_cache
from pathlib import Path


STBL_RESOURCE = 0x220557DA
_HASH_LABEL = re.compile(r"^hash:\s*(\d+)\s*$", re.IGNORECASE)
_ILLNESS_WORDS = (
    "allergy", "anemia", "anxiety", "appendicitis", "asthma", "bronchitis", "cancer",
    "arthritis", "bipolar disorder", "borderline personality disorder",
    "bloaty head", "bubonic plague", "burning belly", "chicken pox", "cholera",
    "cold", "consumption", "deafness", "diabetes", "diphtheria", "depression",
    "dysentery", "ear infection", "eczema", "epilepsy", "flat head syndrome", "flu",
    "food poisoning", "gastroenteritis", "heart attack", "hypertension",
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
)


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


def _read_stbl(raw: bytes) -> dict[int, str]:
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
        if text:
            result[key] = text
    return result


def _package_localizations(paths) -> dict[int, str]:
    labels: dict[int, str] = {}
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
                        labels.update(_read_stbl(resource))
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
def trait_localizations() -> dict[int, str]:
    labels = dict(game_localizations())
    labels.update(gameplay_mod_localizations())
    labels.update(healthcare_localizations())
    return labels


def readable_trait_label(value, localizations: dict[int, str] | None = None) -> str:
    """Turn a Clock Sync localization hash into a readable local trait label."""
    if isinstance(value, dict):
        value = value.get("name") or value.get("display_name") or value.get("title") or value.get("trait") or ""
    label = str(value or "").strip()
    match = _HASH_LABEL.match(label)
    if not match:
        return label
    key = int(match.group(1))
    if not key:
        return ""
    name = (localizations if localizations is not None else trait_localizations()).get(key, "").strip()
    if not name:
        return f"Unidentified custom trait (ID {key})"
    choices = re.findall(r"\{(?:T|M|F|U|DAE)\d*\.([^}]+)\}", name)
    if choices:
        name = " / ".join(dict.fromkeys(choices))
    if re.match(r"(?i)^trait[_-]", name):
        name = re.sub(r"(?i)^trait[_-]", "", name)
        name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ").replace("-", " ")
    return " ".join(name.replace("\ufffd", "é").split())


def readable_trait_labels(value, localizations: dict[int, str] | None = None) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else (() if value in (None, "") else (value,))
    labels: list[str] = []
    for item in values:
        label = readable_trait_label(item, localizations)
        if label and label not in labels:
            labels.append(label)
    return labels


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
    if not any(word in folded for word in _ILLNESS_WORDS):
        return None
    stable_key = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    return stable_key, name


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
        if not name or any(word in searchable for word in _NON_ACTIVE_WORDS):
            continue
        key = str(item.get("source_key") or item.get("name") or "").casefold()
        if key:
            merged[key] = item
    return {**snapshot, "illness_scan_supported": True, "illnesses": list(merged.values()),
            "unknown_health_traits": unknown}
