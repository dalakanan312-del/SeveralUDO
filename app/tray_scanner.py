from __future__ import annotations

"""Read individual Sim portraits from the user's Sims 4 Tray library.

Tray files are treated as read-only.  A householdbinary protobuf connects a
Sim's name to the instance identifier in the corresponding SGI filename.  The
SGI contains the game's encrypted JPEG portrait.
"""

import base64
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

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
    """Return the JPEG payload from an SGI portrait without changing the file."""
    if len(data) <= _SGI_HEADER_BYTES or len(data) > MAX_TRAY_FILE_BYTES:
        raise SaveScanError("The Tray portrait has an unsupported size.")
    payload = data[_SGI_HEADER_BYTES:]
    image = bytes(value ^ _SGI_XOR_KEY[index % len(_SGI_XOR_KEY)] for index, value in enumerate(payload))
    if not image.startswith(b"\xff\xd8\xff"):
        raise SaveScanError("The Tray portrait is not a supported Sims 4 JPEG.")
    return image


def _household_sims(data: bytes) -> list[tuple[str, str, int]]:
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
    found: list[tuple[str, str, int]] = []
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
                found.append((first_name, "" if last_name == "." else last_name, tray_sim_id))
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
        for first_name, last_name, tray_sim_id in _household_sims(household_data):
            image_path = images.get(tray_sim_id)
            if image_path:
                try:
                    candidate_modified = max(modified, image_path.stat().st_mtime)
                except OSError:
                    continue
                found.append(TrayPortrait(first_name, last_name, tray_sim_id, image_path, household_path, candidate_modified))
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


def import_portraits(session, save, *, root: Path | None = None, target_record_id: str | None = None) -> dict:
    """Import only exact, unambiguous name matches; manual portraits remain protected."""
    from . import clock
    from .models import Portrait, Record

    candidates = discover_portraits(root)
    sims = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
    )))
    if target_record_id:
        sims = [sim for sim in sims if sim.id == target_record_id]

    tracker_names: dict[str, list] = {}
    for sim in sims:
        for key in _sim_names(sim):
            tracker_names.setdefault(key, []).append(sim)

    # The newest saved Library copy wins, but a name shared by multiple tracker
    # Sims is never assigned automatically.
    tray_by_name: dict[str, TrayPortrait] = {}
    for item in candidates:
        tray_by_name.setdefault(_name_key(item.name), item)

    matched = updated = protected = unchanged = ambiguous = invalid = 0
    touched: set[str] = set()
    for key, candidate in tray_by_name.items():
        matches = {sim.id: sim for sim in tracker_names.get(key, ())}
        if not matches:
            continue
        if len(matches) != 1:
            ambiguous += 1
            continue
        sim = next(iter(matches.values()))
        if sim.id in touched:
            continue
        touched.add(sim.id); matched += 1
        try:
            raw = candidate.image_path.read_bytes()
            image = decode_sgi(raw)
        except (OSError, SaveScanError):
            invalid += 1
            continue
        stage = clock._stage_key((sim.data or {}).get("game_age_stage") or (sim.data or {}).get("life_stage")) or "default"
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
