"""Build a secret-free downloadable SQLite edition in memory."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent
EXTRA_FILES=("requirements-local.txt","Start Decades Tracker Local.bat","Start Decades Tracker Local.ps1",
             "LOCAL_EDITION_README.txt","PERSONAL_USE_NOTICE.txt","USER_GUIDE.txt")


def build(starter_save=None):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.glob("*.py")):
            archive.write(path,path.name)
        for name in EXTRA_FILES:
            path=ROOT/name
            if path.exists():archive.write(path,path.name)
        if starter_save:
            archive.writestr("STARTER_SAVE.decades-save",starter_save)
    return buffer.getvalue()
