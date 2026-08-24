from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .config import ROOT


CLOCK_SYNC_VERSION = "2.0.1"
CLOCK_SYNC_FOLDER = "SeveralUDOClockSync"
BRIDGE_ROOT = ROOT / "clock_bridge"


def config_document(endpoint: str = "PASTE_ENDPOINT_FROM_TRACKER", token: str = "PASTE_PRIVATE_TOKEN_FROM_TRACKER") -> bytes:
    return (json.dumps({
        "receiver_url": endpoint,
        "sync_token": token,
        "enabled": True,
    }, indent=2) + "\n").encode("utf-8")


def build_bundle(endpoint: str = "", token: str = "") -> bytes:
    """Build a complete Windows Clock Sync folder without retaining secrets."""
    required = (
        "SeveralUDOClockSync.ts4script",
        "SeveralUDOClockRelay.ps1",
        "Start SeveralUDO Clock Relay.bat",
        "README - Install Clock Sync.txt",
        "TROUBLESHOOTING.txt",
    )
    missing = [name for name in required if not (BRIDGE_ROOT / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Clock Sync kit is missing: {', '.join(missing)}")

    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for name in required:
            archive.writestr(f"{CLOCK_SYNC_FOLDER}/{name}", (BRIDGE_ROOT / name).read_bytes())
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
