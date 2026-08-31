from __future__ import annotations

from collections import defaultdict

from . import game_metadata
from .models import ChronicleSave, Record


ACTIVE_ENROLLMENT_STATUSES = {"planning", "applied", "enrolled", "on leave", "probation"}
ACTIVE_TERM_STATUSES = {"planned", "in progress", "active", "probation"}
PASSED_TERM_STATUSES = {"completed", "passed"}
UNIVERSITY_HINTS = ("university", "college", "undergraduate", "postgraduate", "britechester", "foxbury")


def _integer(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _status(value, default: str) -> str:
    return " ".join(str(value or default).replace("_", " ").split()).strip()


def career_rows(sim: Record) -> list[dict]:
    data = sim.data or {}
    rows = []
    for raw in data.get("game_careers") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("title") or "").strip()
        title = str(raw.get("title") or "").strip()
        if name or title:
            rows.append({
                "name": name or title,
                "title": title,
                "branch": str(raw.get("branch") or "").strip(),
                "level": raw.get("level"),
                "performance": _number(raw.get("performance")),
                "tuning_id": raw.get("tuning_id"),
            })
    if not rows and str(data.get("game_career") or "").strip():
        rows.append({"name": str(data["game_career"]).strip(), "title": "", "branch": "", "level": None,
                     "performance": None, "tuning_id": None})
    return rows


def degree_labels(sim: Record) -> list[str]:
    data = sim.data or {}
    return game_metadata.readable_named_labels(data.get("game_degrees"), data.get("game_degree_details"), kind="degree")


def is_university_career(row: dict) -> bool:
    text = " ".join(str(row.get(key) or "") for key in ("name", "title", "branch")).casefold()
    return any(hint in text for hint in UNIVERSITY_HINTS)


def performance_band(value) -> dict:
    score = _number(value)
    if score is None:
        return {"label": "Not recorded", "tone": "unknown", "score": None, "percent": 0}
    score = max(0.0, min(100.0, score))
    if score >= 90:
        label, tone = "Honors pace", "excellent"
    elif score >= 75:
        label, tone = "Strong", "good"
    elif score >= 60:
        label, tone = "Satisfactory", "steady"
    elif score >= 40:
        label, tone = "At risk", "risk"
    else:
        label, tone = "Critical", "critical"
    return {"label": label, "tone": tone, "score": round(score, 1), "percent": round(score, 1)}


def term_is_open(term: Record) -> bool:
    return _status((term.data or {}).get("status"), "In progress").casefold() in ACTIVE_TERM_STATUSES


def enrollment_is_active(enrollment: Record) -> bool:
    return _status((enrollment.data or {}).get("status"), "Enrolled").casefold() in ACTIVE_ENROLLMENT_STATUSES


def dashboard(records: list[Record], save: ChronicleSave) -> dict:
    sims = sorted((item for item in records if item.kind == "sim"), key=lambda item: item.label.casefold())
    sim_by_id = {item.id: item for item in sims}
    enrollments = [item for item in records if item.kind == "university_enrollment"]
    terms = [item for item in records if item.kind == "university_term"]
    checkpoints = [item for item in records if item.kind == "university_performance"]
    terms_by_enrollment: dict[str, list[Record]] = defaultdict(list)
    for term in terms:
        terms_by_enrollment[str((term.data or {}).get("enrollment_id") or "")].append(term)
    for values in terms_by_enrollment.values():
        values.sort(key=lambda item: (_integer((item.data or {}).get("term_number")), item.global_day or 0, str(item.created_at or "")))
    checkpoints_by_enrollment: dict[str, list[Record]] = defaultdict(list)
    for checkpoint in checkpoints:
        checkpoints_by_enrollment[str((checkpoint.data or {}).get("enrollment_id") or "")].append(checkpoint)
    for values in checkpoints_by_enrollment.values():
        values.sort(key=lambda item: (item.global_day or 0, str(item.created_at or "")))

    rows = []
    all_scores = []
    active_sim_ids = set()
    due_terms = []
    for enrollment in sorted(enrollments, key=lambda item: str(item.updated_at or ""), reverse=True):
        data = enrollment.data or {}
        sim = sim_by_id.get(str(data.get("sim_id") or ""))
        linked_terms = terms_by_enrollment.get(enrollment.id, [])
        performance_history = checkpoints_by_enrollment.get(enrollment.id, [])
        open_terms = [item for item in linked_terms if term_is_open(item)]
        current_term = open_terms[-1] if open_terms else (linked_terms[-1] if linked_terms else None)
        latest_checkpoint = performance_history[-1] if performance_history else None
        current_score = ((latest_checkpoint.data or {}).get("performance") if latest_checkpoint else
                         (current_term.data or {}).get("performance") if current_term else data.get("performance"))
        performance = performance_band(current_score)
        if performance["score"] is not None:
            all_scores.append(performance["score"])
        earned = max(0, _integer(data.get("credits_earned")))
        required = max(1, _integer(data.get("credits_required"), 12))
        detected_careers = career_rows(sim) if sim else []
        detected_university = [item for item in detected_careers if is_university_career(item)]
        detected_source = detected_university or detected_careers
        detected_performance = next((item for item in detected_source if item.get("performance") is not None), None)
        active = enrollment_is_active(enrollment)
        if active and sim:
            active_sim_ids.add(sim.id)
        if current_term and term_is_open(current_term):
            end_day = _integer((current_term.data or {}).get("end_global_day"), current_term.global_day or save.global_day)
            if end_day <= save.global_day:
                due_terms.append({"term": current_term, "enrollment": enrollment, "sim": sim, "due_day": end_day,
                                  "performance": performance, "detected_performance": detected_performance})
        rows.append({
            "enrollment": enrollment,
            "sim": sim,
            "terms": linked_terms,
            "performance_history": performance_history,
            "current_term": current_term,
            "active": active,
            "credits_earned": earned,
            "credits_required": required,
            "credit_percent": min(100, round(earned * 100 / required)),
            "performance": performance,
            "detected_careers": detected_careers,
            "detected_performance": detected_performance,
            "degrees": degree_labels(sim) if sim else [],
        })

    candidates = []
    completed_degrees = []
    performance_watch = []
    for sim in sims:
        careers = career_rows(sim)
        university_careers = [item for item in careers if is_university_career(item)]
        degrees = degree_labels(sim)
        if university_careers and sim.id not in active_sim_ids:
            candidates.append({"sim": sim, "careers": university_careers, "suggested": university_careers[0]})
        if degrees:
            completed_degrees.append({"sim": sim, "degrees": degrees})
        for career in university_careers:
            if career.get("performance") is not None:
                performance_watch.append({"sim": sim, "career": career,
                                          "band": performance_band(career["performance"])})

    rows.sort(key=lambda row: (not row["active"], (row["sim"].label if row["sim"] else row["enrollment"].label).casefold()))
    due_terms.sort(key=lambda row: (row["due_day"], row["sim"].label.casefold() if row["sim"] else ""))
    return {
        "rows": rows,
        "active_rows": [row for row in rows if row["active"]],
        "past_rows": [row for row in rows if not row["active"]],
        "due_terms": due_terms,
        "candidates": candidates,
        "completed_degrees": completed_degrees,
        "performance_watch": sorted(performance_watch, key=lambda row: (row["band"]["score"] or 0, row["sim"].label.casefold())),
        "stats": {
            "active": sum(row["active"] for row in rows),
            "due": len(due_terms),
            "graduated": sum(_status((row["enrollment"].data or {}).get("status"), "").casefold() == "graduated" for row in rows),
            "average_performance": round(sum(all_scores) / len(all_scores), 1) if all_scores else None,
        },
    }


def apply_term_result(enrollment: Record, term: Record, *, status: str, performance=None,
                      grade: str = "", gpa=None, credits_earned: int = 0,
                      end_global_day: int | None = None, notes: str = "",
                      graduate: bool = False) -> dict:
    enrollment_data = dict(enrollment.data or {})
    term_data = dict(term.data or {})
    previous_status = _status(term_data.get("status"), "In progress").casefold()
    previous_applied = max(0, _integer(term_data.get("credits_applied")))
    normalized_status = _status(status, "In progress")
    passed = normalized_status.casefold() in PASSED_TERM_STATUSES
    newly_applied = max(0, _integer(credits_earned)) if passed else 0
    current_total = max(0, _integer(enrollment_data.get("credits_earned")))
    enrollment_data["credits_earned"] = max(0, current_total - previous_applied + newly_applied)
    required = max(1, _integer(enrollment_data.get("credits_required"), 12))
    term_data.update({
        "status": normalized_status,
        "performance": _number(performance),
        "grade": str(grade or "").strip(),
        "gpa": _number(gpa),
        "credits_earned": max(0, _integer(credits_earned)),
        "credits_applied": newly_applied,
        "end_global_day": end_global_day,
        "notes": str(notes or "").strip(),
    })
    graduated = bool(graduate or (passed and enrollment_data["credits_earned"] >= required))
    was_graduated = _status(enrollment_data.get("status"), "").casefold() == "graduated"
    if graduated:
        enrollment_data["status"] = "Graduated"
        enrollment_data["graduation_global_day"] = end_global_day
    elif normalized_status.casefold() in {"failed", "probation"}:
        enrollment_data["status"] = "Probation"
    elif normalized_status.casefold() == "withdrawn":
        enrollment_data["status"] = "Withdrawn"
    elif enrollment_data.get("status", "").casefold() in {"planning", "applied"}:
        enrollment_data["status"] = "Enrolled"
    return {
        "term_data": term_data,
        "enrollment_data": enrollment_data,
        "completed_transition": previous_status not in PASSED_TERM_STATUSES and passed,
        "graduated": graduated,
        "graduated_transition": graduated and not was_graduated,
    }
