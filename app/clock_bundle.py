from __future__ import annotations

import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .config import ROOT
from . import game_modes


CLOCK_SYNC_VERSION = "2.2.12"
CLOCK_SYNC_FOLDER = "SeveralUDOClockSync"
BRIDGE_ROOT = ROOT / "clock_bridge"
SIMS3_CLOCK_SYNC_VERSION = "retired"
SIMS3_CLOCK_RETIRED_MESSAGE = "Sims 3 Clock Sync has been withdrawn because it could not be made to work reliably. The desktop tracker reads completed Sims 3 saves as a supplement, not live Clock Sync."


def require_supported(game_mode):
    if game_modes.normalize(game_mode) == game_modes.SIMS3:
        raise ValueError(SIMS3_CLOCK_RETIRED_MESSAGE)

SIMS3_CLOCK_SYNC_FOLDER = "SeveralUDOSims3ClockSync"
SIMS3_BRIDGE_ROOT = ROOT / "clock_bridge_sims3"
CLOCK_SYNC_REQUIRED_FILES = (
    "SeveralUDOClockSync.ts4script",
    "SeveralUDOClockRelay.ps1",
    "Start SeveralUDO Clock Relay.bat",
    "Test SeveralUDO Clock Sync.bat",
    "Install or Update SeveralUDO Clock Sync.ps1",
    "Install or Update SeveralUDO Clock Sync.bat",
    "README - Install Clock Sync.txt",
    "TROUBLESHOOTING.txt",
)
SIMS3_CLOCK_SYNC_REQUIRED_FILES = (
    "SeveralUDOClockRelay.ps1",
    "Start SeveralUDO Sims 3 Clock Relay.bat",
    "Report Sims 3 Clock Now.ps1",
    "Report Sims 3 Clock Now.bat",
    "Sync Sims 3 Save Safely.ps1",
    "Sync Sims 3 Save Safely.bat",
    "Test SeveralUDO Sims 3 Clock Sync.bat",
    "Install or Update SeveralUDO Sims 3 Clock Sync.ps1",
    "Install or Update SeveralUDO Sims 3 Clock Sync.bat",
    "README - Install Sims 3 Clock Sync.txt",
    "TROUBLESHOOTING.txt",
)
def bundle_details(game_mode: object = game_modes.SIMS4) -> dict:
    mode = game_modes.normalize(game_mode)
    if mode == game_modes.SIMS3:
        return {
            "mode": mode, "version": SIMS3_CLOCK_SYNC_VERSION,
            "folder": SIMS3_CLOCK_SYNC_FOLDER, "root": SIMS3_BRIDGE_ROOT,
            "required": (), "available": False,
        }
    return {
        "mode": game_modes.SIMS4, "version": CLOCK_SYNC_VERSION,
        "folder": CLOCK_SYNC_FOLDER, "root": BRIDGE_ROOT,
        "required": CLOCK_SYNC_REQUIRED_FILES,
    }


def missing_files(game_mode: object = game_modes.SIMS4) -> list[str]:
    """Report files omitted from a desktop or hosted deployment bundle."""
    details = bundle_details(game_mode)
    return [name for name in details["required"] if not (details["root"] / name).is_file()]


def config_document(endpoint: str = "PASTE_ENDPOINT_FROM_TRACKER", token: str = "PASTE_PRIVATE_TOKEN_FROM_TRACKER",
                    capture_portraits: bool = True, game_mode: object = game_modes.SIMS4) -> bytes:
    require_supported(game_mode)
    mode = game_modes.normalize(game_mode)
    document = {
        "receiver_url": endpoint,
        "sync_token": token,
        "enabled": True,
        "game_edition": mode,
    }
    if mode == game_modes.SIMS4:
        document["capture_portraits"] = bool(capture_portraits)
    else:
        document["sims3_save_identity"] = ""
        document["sims3_population_scope"] = "town"
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def build_bundle(endpoint: str = "", token: str = "", capture_portraits: bool = True,
                 game_mode: object = game_modes.SIMS4) -> bytes:
    """Build a complete Windows Clock Sync folder without retaining secrets."""
    require_supported(game_mode)
    details = bundle_details(game_mode)
    required = details["required"]
    root = details["root"]
    folder = details["folder"]
    mode = details["mode"]
    version = details["version"]
    missing = missing_files(mode)
    if missing:
        raise FileNotFoundError(f"Clock Sync kit is missing: {', '.join(missing)}")

    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for name in required:
            archive.writestr(f"{folder}/{name}", (root / name).read_bytes())
        # Some Windows security tools hide command files while extracting a ZIP.
        # Plain-text recovery copies let the owner restore the exact files by
        # removing only the final ".txt" extension.
        for name in (item for item in required if item.endswith((".ps1", ".bat"))):
            archive.writestr(f"{folder}/{name}.backup.txt", (root / name).read_bytes())
        checksums = [f"{sha256((root / name).read_bytes()).hexdigest()}  {name}" for name in required]
        archive.writestr(
            f"{folder}/KIT CONTENTS - VERIFY.txt",
            (
                f"SeveralUDO {game_modes.GAME_MODES[mode]['short_name']} Clock Sync {version} - expected contents\r\n"
                "=================================================\r\n\r\n"
                "The folder must contain every listed relay, helper and instruction file.\r\n"
                "If Windows hides a command file, rename its .backup.txt copy by removing .backup.txt.\r\n\r\n"
                + "\r\n".join(f"- {name}" for name in required)
                + "\r\n\r\nPlain-text recovery copies of every .ps1 and .bat file are also included.\r\n\r\n"
                "SHA-256 checksums for the original files:\r\n" + "\r\n".join(checksums) + "\r\n"
            ).encode("utf-8"),
        )
        start_here_name = "START HERE - SeveralUDO Clock Sync.txt" if mode == game_modes.SIMS4 else f"START HERE - SeveralUDO {game_modes.GAME_MODES[mode]['short_name']} Clock Sync.txt"
        archive.writestr(
            start_here_name,
            (
                f"Open the {folder} folder inside this ZIP.\r\n"
                "Extract the entire folder before installing or starting anything.\r\n"
                "Read the included installation guide before running the helper files.\r\n"
                "If Windows security filtered a helper, recovery copies are included as .backup.txt files.\r\n"
            ).encode("utf-8"),
        )
        if endpoint and token:
            archive.writestr(f"{folder}/config.json", config_document(endpoint, token, capture_portraits, mode))
            archive.writestr(
                f"{folder}/PRIVATE CONFIG - DO NOT SHARE.txt",
                b"This kit contains the private token for one tracker save. Do not upload or share config.json.\r\n",
            )
        else:
            archive.writestr(f"{folder}/config-template.json", config_document(game_mode=mode))
    return output.getvalue()


def bridge_file(name: str, game_mode: object = game_modes.SIMS4) -> Path:
    require_supported(game_mode)
    mode = game_modes.normalize(game_mode)
    allowed = {
        "script": "SeveralUDOClockSync.ts4script",
        "relay": "SeveralUDOClockRelay.ps1",
        "starter": "Start SeveralUDO Clock Relay.bat",
        "self-test": "Test SeveralUDO Clock Sync.bat",
        "updater": "Install or Update SeveralUDO Clock Sync.ps1",
        "updater-starter": "Install or Update SeveralUDO Clock Sync.bat",
        "instructions": "README - Install Clock Sync.txt",
        "troubleshooting": "TROUBLESHOOTING.txt",
    } if mode == game_modes.SIMS4 else {
        "script": "Sync Sims 3 Save Safely.ps1",
        "relay": "SeveralUDOClockRelay.ps1",
        "starter": "Start SeveralUDO Sims 3 Clock Relay.bat",
        "self-test": "Test SeveralUDO Sims 3 Clock Sync.bat",
        "updater": "Install or Update SeveralUDO Sims 3 Clock Sync.ps1",
        "updater-starter": "Install or Update SeveralUDO Sims 3 Clock Sync.bat",
        "reporter": "Report Sims 3 Clock Now.bat",
        "safe-sync": "Sync Sims 3 Save Safely.bat",
        "instructions": "README - Install Sims 3 Clock Sync.txt",
        "troubleshooting": "TROUBLESHOOTING.txt",
    }
    return bundle_details(mode)["root"] / allowed[name]





