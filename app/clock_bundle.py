from __future__ import annotations

import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .config import ROOT


CLOCK_SYNC_VERSION = "2.0.3"
CLOCK_SYNC_FOLDER = "SeveralUDOClockSync"
BRIDGE_ROOT = ROOT / "clock_bridge"
CLOCK_SYNC_REQUIRED_FILES = (
    "SeveralUDOClockSync.ts4script",
    "SeveralUDOClockRelay.ps1",
    "Start SeveralUDO Clock Relay.bat",
    "README - Install Clock Sync.txt",
    "TROUBLESHOOTING.txt",
)


def missing_files() -> list[str]:
    """Report files omitted from a desktop or hosted deployment bundle."""
    return [name for name in CLOCK_SYNC_REQUIRED_FILES if not (BRIDGE_ROOT / name).is_file()]


def config_document(endpoint: str = "PASTE_ENDPOINT_FROM_TRACKER", token: str = "PASTE_PRIVATE_TOKEN_FROM_TRACKER") -> bytes:
    return (json.dumps({
        "receiver_url": endpoint,
        "sync_token": token,
        "enabled": True,
    }, indent=2) + "\n").encode("utf-8")


def build_bundle(endpoint: str = "", token: str = "") -> bytes:
    """Build a complete Windows Clock Sync folder without retaining secrets."""
    required = CLOCK_SYNC_REQUIRED_FILES
    missing = missing_files()
    if missing:
        raise FileNotFoundError(f"Clock Sync kit is missing: {', '.join(missing)}")

    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for name in required:
            archive.writestr(f"{CLOCK_SYNC_FOLDER}/{name}", (BRIDGE_ROOT / name).read_bytes())
        # Some Windows security tools hide command files while extracting a ZIP.
        # Plain-text recovery copies let the owner restore the exact files by
        # removing only the final ".txt" extension.
        for name in ("SeveralUDOClockRelay.ps1", "Start SeveralUDO Clock Relay.bat"):
            archive.writestr(f"{CLOCK_SYNC_FOLDER}/{name}.backup.txt", (BRIDGE_ROOT / name).read_bytes())
        checksums = [
            f"{sha256((BRIDGE_ROOT / name).read_bytes()).hexdigest()}  {name}"
            for name in required
        ]
        archive.writestr(
            f"{CLOCK_SYNC_FOLDER}/KIT CONTENTS - VERIFY.txt",
            (
                "SeveralUDO Clock Sync 2.0.3 - expected contents\r\n"
                "=================================================\r\n\r\n"
                "The folder must contain the Script Mod, PowerShell relay and BAT starter.\r\n"
                "If Windows hides either command file, rename its .backup.txt copy by removing .backup.txt.\r\n\r\n"
                + "\r\n".join(f"- {name}" for name in required)
                + "\r\n- SeveralUDOClockRelay.ps1.backup.txt\r\n"
                "- Start SeveralUDO Clock Relay.bat.backup.txt\r\n\r\n"
                "SHA-256 checksums for the five original files:\r\n"
                + "\r\n".join(checksums)
                + "\r\n"
            ).encode("utf-8"),
        )
        archive.writestr(
            "START HERE - SeveralUDO Clock Sync.txt",
            (
                "Open the SeveralUDOClockSync folder inside this ZIP.\r\n"
                "Extract the entire folder before installing or starting anything.\r\n"
                "It contains SeveralUDOClockRelay.ps1 and Start SeveralUDO Clock Relay.bat.\r\n"
                "If those two files are hidden by Windows security, recovery copies are included as .backup.txt files.\r\n"
            ).encode("utf-8"),
        )
        if endpoint and token:
            archive.writestr(f"{CLOCK_SYNC_FOLDER}/config.json", config_document(endpoint, token))
            archive.writestr(
                f"{CLOCK_SYNC_FOLDER}/PRIVATE CONFIG - DO NOT SHARE.txt",
                b"This kit contains the private token for one tracker save. Do not upload or share config.json.\r\n",
            )
        else:
            archive.writestr(f"{CLOCK_SYNC_FOLDER}/config-template.json", config_document())
    return output.getvalue()


def bridge_file(name: str) -> Path:
    allowed = {
        "script": "SeveralUDOClockSync.ts4script",
        "relay": "SeveralUDOClockRelay.ps1",
        "starter": "Start SeveralUDO Clock Relay.bat",
        "instructions": "README - Install Clock Sync.txt",
        "troubleshooting": "TROUBLESHOOTING.txt",
    }
    return BRIDGE_ROOT / allowed[name]
