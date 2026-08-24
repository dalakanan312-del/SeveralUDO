from __future__ import annotations

import hashlib
import re
import secrets
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import DiceAudit


PATTERN = re.compile(r"^\s*(?:(\d+)\s*)?[dD](\d+)\s*([+-]\s*\d+)?\s*$")


def parse(notation: str) -> tuple[int, int, int]:
    match = PATTERN.match(notation or "")
    if not match:
        raise ValueError("Use dice notation such as d20, 2d6, or d10+2.")
    quantity = int(match.group(1) or 1)
    sides = int(match.group(2))
    modifier = int((match.group(3) or "0").replace(" ", ""))
    if not 1 <= quantity <= 100 or not 2 <= sides <= 1000:
        raise ValueError("That die is outside the supported range.")
    return quantity, sides, modifier


def notation_for_roll(configured_die: str | None, configured_range: str | None = None) -> tuple[str, str]:
    """Return audited dice notation and a friendly label for a configured roll."""
    configured = str(configured_die or "").strip()
    try:
        parse(configured)
        normalized = configured.lower().replace(" ", "")
        return normalized, normalized
    except ValueError:
        pass
    if configured.casefold() in {"rng", "random", "range"}:
        values = [int(value) for value in re.findall(r"\d+", str(configured_range or ""))]
        if len(values) >= 2:
            low, high = sorted(values[:2])
            if low < high:
                return f"d{high - low + 1}+{low - 1}", f"{low}–{high}"
    return "d20", "d20"


def audited_roll(session: Session, notation: str, save_id: str | None = None,
                 context: str = "practice", context_id: str = "") -> DiceAudit:
    quantity, sides, modifier = parse(notation)
    reveal = secrets.token_hex(32)
    commitment = hashlib.sha256(reveal.encode()).hexdigest()
    faces = deterministic_faces(reveal, quantity, sides)
    audit = DiceAudit(
        save_id=save_id, context=context, context_id=context_id,
        notation=notation.lower().replace(" ", ""), faces=faces,
        total=sum(faces) + modifier, commitment=commitment, reveal=reveal,
    )
    session.add(audit)
    session.flush()
    if save_id:
        from .models import ChronicleSave
        from . import sync
        save = session.get(ChronicleSave, save_id)
        if save:
            sync.sync_dice_audit(session, save, audit)
    return audit


def verify(audit: DiceAudit) -> bool:
    quantity, sides, modifier = parse(audit.notation)
    return (
        hashlib.sha256(audit.reveal.encode()).hexdigest() == audit.commitment
        and deterministic_faces(audit.reveal, quantity, sides) == audit.faces
        and sum(audit.faces) + modifier == audit.total
    )


def deterministic_faces(reveal: str, quantity: int, sides: int) -> list[int]:
    """Derive unbiased faces from revealed entropy using rejection sampling."""
    faces, counter = [], 0
    limit = (1 << 256) - ((1 << 256) % sides)
    while len(faces) < quantity:
        value = int.from_bytes(hashlib.sha256(f"{reveal}:{counter}".encode()).digest(), "big")
        counter += 1
        if value < limit:
            faces.append((value % sides) + 1)
    return faces


def fairness_report(session: Session, save_id: str | None, notation: str = "d20") -> dict:
    quantity, sides, modifier = parse(notation)
    if quantity != 1 or modifier:
        return {"notation": notation, "eligible": False, "reason": "Fairness analysis uses unmodified single-die rolls."}
    query = select(DiceAudit).where(DiceAudit.notation == notation.casefold().replace(" ", ""))
    if save_id:
        query = query.where(DiceAudit.save_id == save_id)
    audits = list(session.scalars(query))
    faces = [int(a.faces[0]) for a in audits if len(a.faces) == 1 and verify(a)]
    counts = Counter(faces)
    expected = len(faces) / sides if faces else 0
    chi_square = sum(((counts.get(face, 0) - expected) ** 2) / expected for face in range(1, sides + 1)) if expected else 0
    return {
        "notation": notation, "eligible": True, "rolls": len(faces),
        "counts": {str(face): counts.get(face, 0) for face in range(1, sides + 1)},
        "expected_per_face": expected, "chi_square": chi_square,
        "degrees_of_freedom": sides - 1,
        "note": "A large imbalance is a review signal, not proof of unfairness. Cryptographic commitments verify ledger integrity.",
    }
