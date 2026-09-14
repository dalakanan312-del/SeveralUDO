from __future__ import annotations

"""Read individual Sim portraits from the user's Sims 4 Tray library.

Tray files are treated as read-only.  A householdbinary protobuf connects a
Sim's name to the instance identifier in the corresponding SGI filename.  The
SGI contains the game's encrypted JPEG portrait and optional PNG alpha mask.
"""

import base64
import io
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from sqlalchemy import func, select

from . import portraits
from .save_scanner import SaveScanError, _text, _value, protobuf_fields


MAX_TRAY_FILE_BYTES = 16 * 1024 * 1024
_SGI_HEADER_BYTES = 24
_SGI_XOR_KEY = bytes.fromhex("4125e6cd47bab21a")


@dataclass(frozen=True)
class TrayPortrait:
    first_name: str
    last_name: str
    tray_sim_id: int
    image_path: Path
    household_path: Path
    modified_at: float
    age_stage: str = ""

    @property
    def name(self) -> str:
        return " ".join(value for value in (self.first_name, self.last_name) if value).strip()


def default_tray_root() -> Path:
    configured = os.environ.get("SIMS4_TRAY_DIR")
    if configured:
        return Path(configured).expanduser()
    profile = Path(os.environ.get("USERPROFILE") or Path.home())
    return profile / "Documents" / "Electronic Arts" / "The Sims 4" / "Tray"


def _name_key(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def decode_sgi(data: bytes) -> bytes:
    """Decode an SGI without changing it; return PNG when it has transparency."""
    if len(data) <= _SGI_HEADER_BYTES or len(data) > MAX_TRAY_FILE_BYTES:
        raise SaveScanError("The Tray portrait has an unsupported size.")
    payload = data[_SGI_HEADER_BYTES:]
    image = bytes(value ^ _SGI_XOR_KEY[index % len(_SGI_XOR_KEY)] for index, value in enumerate(payload))
    if not image.startswith(b"\xff\xd8\xff"):
        raise SaveScanError("The Tray portrait is not a supported Sims 4 JPEG.")
    try:
        decoded = portraits.open_image(image)
        if decoded.mode == "RGBA":
            output = io.BytesIO()
            decoded.save(output, format="PNG")
            return output.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise SaveScanError("The Tray portrait or its transparency mask is invalid.") from exc
    return image


def _household_sims(data: bytes) -> list[tuple[str, str, int, str]]:
    if len(data) <= 16 or len(data) > MAX_TRAY_FILE_BYTES:
        return []
    # The fourth little-endian header word is the protobuf payload length.
    # A short fixed trailer follows it in current Tray files and must not be
    # interpreted as protobuf fields.
    payload_size = int.from_bytes(data[12:16], "little")
    if not payload_size or payload_size > len(data) - 16:
        return []
    try:
        top = protobuf_fields(data[16:16 + payload_size])
    except SaveScanError:
        return []
    found: list[tuple[str, str, int, str]] = []
    for wire, household_blob in top.get(1, ()):
        if wire != 2 or not isinstance(household_blob, bytes):
            continue
        try:
            household = protobuf_fields(household_blob)
        except SaveScanError:
            continue
        for sim_wire, sim_blob in household.get(6, ()):
            if sim_wire != 2 or not isinstance(sim_blob, bytes):
                continue
            try:
                sim = protobuf_fields(sim_blob)
            except SaveScanError:
                continue
            tray_sim_id = _value(sim, 1)
            first_name, last_name = _text(sim, 5), _text(sim, 6)
            if isinstance(tray_sim_id, int) and tray_sim_id and first_name and first_name != ".":
                # EA's CAS AgeGender flags (infant was added at 0x80; the
                # existing stage flags did not shift). FileSerialization field 8.
                age = {1:"newborn", 2:"toddler", 4:"child", 8:"teen",
                       16:"youngadult", 32:"adult", 64:"elder", 128:"infant"}.get(_value(sim, 8), "")
                found.append((first_name, "" if last_name == "." else last_name, tray_sim_id, age))
    return found


def discover_portraits(root: Path | None = None) -> list[TrayPortrait]:
    """Index valid name-to-SGI links, newest household copies first."""
    folder = Path(root) if root else default_tray_root()
    if not folder.is_dir():
        return []
    try:
        files = [item for item in folder.iterdir() if item.is_file()]
    except OSError:
        return []
    images: dict[int, Path] = {}
    households: list[Path] = []
    for path in files:
        suffix = path.suffix.casefold()
        if suffix == ".sgi" and "!" in path.name:
            try:
                instance = int(path.stem.split("!", 1)[1], 16)
            except (IndexError, ValueError):
                continue
            current = images.get(instance)
            try:
                if current is None or path.stat().st_mtime > current.stat().st_mtime:
                    images[instance] = path
            except OSError:
                continue
        elif suffix == ".householdbinary":
            households.append(path)
    found: list[TrayPortrait] = []
    for household_path in households:
        try:
            if household_path.stat().st_size > MAX_TRAY_FILE_BYTES:
                continue
            household_data = household_path.read_bytes()
            modified = household_path.stat().st_mtime
        except OSError:
            continue
        for first_name, last_name, tray_sim_id, age_stage in _household_sims(household_data):
            image_path = images.get(tray_sim_id)
            if image_path:
                try:
                    candidate_modified = max(modified, image_path.stat().st_mtime)
                except OSError:
                    continue
                found.append(TrayPortrait(first_name, last_name, tray_sim_id, image_path, household_path, candidate_modified, age_stage))
    return sorted(found, key=lambda item: item.modified_at, reverse=True)


def _sim_names(sim) -> set[str]:
    data = sim.data or {}
    names = {str(sim.label or "")}
    first = str(data.get("first_name") or "").strip()
    last_values = (
        data.get("last_name"), data.get("surname"), data.get("surname_at_birth"),
        data.get("birth_surname"), data.get("married_name"),
    )
    if first:
        names.update(" ".join((first, str(last or "").strip())).strip() for last in last_values if last)
    return {key for value in names if (key := _name_key(value))}


def match_portraits(candidates: list[TrayPortrait], sims: list) -> tuple[dict, int]:
    """Keep same-name people distinct; never guess between identical names/stages.

    Library IDs are regenerated by the game, so they are NOT game Sim IDs.
    Keep every same-name person in the newest Library household copy, then use
    life stage only where it gives a unique match in both directions.
    """
    from .clock import _stage_key

    tracker_names: dict[str, dict] = {}
    for sim in sims:
        for key in _sim_names(sim):
            tracker_names.setdefault(key, {})[sim.id] = sim
    newest: dict[str, list[TrayPortrait]] = {}
    for item in sorted(candidates, key=lambda item: item.modified_at, reverse=True):
        group = newest.setdefault(_name_key(item.name), [])
        if not group or group[0].household_path == item.household_path:
            if not any(other.tray_sim_id == item.tray_sim_id for other in group):
                group.append(item)
    matched = {}
    ambiguous = 0
    for key, group in newest.items():
        choices = list(tracker_names.get(key, {}).values())
        if not choices:
            continue
        if len(choices) == len(group) == 1:
            pairs = [(choices[0], group[0])]
        else:
            pairs = []
            for candidate in group:
                eligible = [sim for sim in choices if candidate.age_stage and
                            _stage_key((sim.data or {}).get("game_age_stage") or
                                       (sim.data or {}).get("life_stage")) == candidate.age_stage]
                same_stage = [item for item in group if item.age_stage == candidate.age_stage]
                if len(eligible) == len(same_stage) == 1:
                    pairs.append((eligible[0], candidate))
            if len(pairs) < max(len(choices), len(group)):
                ambiguous += 1
        for sim, candidate in pairs:
            if sim.id not in matched or candidate.modified_at > matched[sim.id].modified_at:
                matched[sim.id] = candidate
    return matched, ambiguous


def import_portraits(session, save, *, root: Path | None = None, target_record_id: str | None = None) -> dict:
    """Import only exact, unambiguous name matches; manual portraits remain protected."""
    from . import clock
    from .models import Portrait, Record

    candidates = discover_portraits(root)
    sims = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
    )))
    matches, ambiguous = match_portraits(candidates, sims)
    matched = updated = protected = unchanged = invalid = 0
    for sim in sims:
        if sim.id not in matches or (target_record_id and sim.id != target_record_id):
            continue
        candidate = matches[sim.id]
        matched += 1
        try:
            raw = candidate.image_path.read_bytes()
            image = decode_sgi(raw)
        except (OSError, SaveScanError):
            invalid += 1
            continue
        stage = candidate.age_stage or clock._stage_key((sim.data or {}).get("game_age_stage") or (sim.data or {}).get("life_stage")) or "default"
        before = session.scalar(select(Portrait).where(
            Portrait.record_id == sim.id,
            func.lower(func.replace(Portrait.stage, " ", "")) == stage.casefold(),
        ))
        was_manual = bool(before and before.source not in {"tray-library-game", "save-file-game", "clock-sync-game"})
        changed = clock._store_game_portrait(session, save, sim, {
            "age_stage": stage,
            "portrait_image_base64": base64.b64encode(image).decode("ascii"),
            "portrait_source": "tray-library-game",
        })
        if changed:
            updated += 1
        elif was_manual:
            protected += 1
        else:
            unchanged += 1
    return {
        "available": len(candidates), "matched": matched, "updated": updated,
        "protected": protected, "unchanged": unchanged, "ambiguous": ambiguous,
        "invalid": invalid,
    }
