from __future__ import annotations

import os
import smtplib
import ssl
import threading
import time
from datetime import datetime, timezone
from email.message import EmailMessage

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import (
    ChronicleSave,
    Membership,
    NotificationEvent,
    NotificationPreference,
    User,
)


DEFAULT_CATEGORIES = ["baby", "sim", "pregnancy", "death", "marriage", "illness", "roll", "sync"]
_started = False


def preference(session: Session, user_id: str, workspace_id: str) -> NotificationPreference:
    row = session.get(NotificationPreference, {"user_id": user_id, "workspace_id": workspace_id})
    if row is None:
        row = NotificationPreference(
            user_id=user_id,
            workspace_id=workspace_id,
            browser_enabled=True,
            categories=list(DEFAULT_CATEGORIES),
        )
        session.add(row)
        session.flush()
    return row


def record(session: Session, save: ChronicleSave, category: str, title: str, body: str,
           target_url: str = "/p/automation", source_key: str = "") -> NotificationEvent:
    stable = source_key.strip() or f"{category}:{title}:{save.revision}"
    existing = session.scalar(select(NotificationEvent).where(
        NotificationEvent.save_id == save.id,
        NotificationEvent.source_key == stable,
    ))
    if existing:
        return existing
    event = NotificationEvent(
        workspace_id=save.workspace_id,
        save_id=save.id,
        category=category[:40],
        title=title[:240],
        body=body,
        target_url=target_url,
        source_key=stable[:255],
        delivery={},
    )
    session.add(event)
    session.flush()
    return event


def candidate_event(session: Session, save: ChronicleSave, candidate) -> NotificationEvent:
    action = str((candidate.data or {}).get("action") or "game_change")
    labels = {
        "new_baby": ("baby", "New baby detected!", f"Would you like to add {candidate.label} to the tracker?"),
        "new_sim": ("sim", "New Sim detected", f"Review {candidate.label} and connect or add their profile."),
        "pregnancy_discovered": ("pregnancy", "New pregnancy detected", candidate.label),
        "pregnancy_outcome": ("pregnancy", "Pregnancy outcome detected", candidate.label),
        "sim_death": ("death", "Death detected", candidate.label),
        "relationship_change": ("marriage", "Relationship change detected", candidate.label),
        "relationship_end": ("marriage", "Relationship ending detected", candidate.label),
        "illness_detected": ("illness", "Illness detected", candidate.label),
        "illness_recovered": ("illness", "Recovery detected", candidate.label),
        "unknown_illness": ("illness", "Unrecognized health condition", candidate.label),
    }
    category, title, body = labels.get(action, ("sim", "Game change detected", candidate.label))
    return record(session, save, category, title, body, "/p/automation", f"candidate:{candidate.id}")


def recent(session: Session, workspace_id: str, save_id: str | None = None,
           after: datetime | None = None, limit: int = 25) -> list[NotificationEvent]:
    query = select(NotificationEvent).where(NotificationEvent.workspace_id == workspace_id)
    if save_id:
        query = query.where(NotificationEvent.save_id == save_id)
    if after:
        query = query.where(NotificationEvent.created_at > after)
    return list(session.scalars(query.order_by(NotificationEvent.created_at.asc()).limit(max(1, min(limit, 100)))))


def _smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM"))


def _send_email(address: str, event: NotificationEvent) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    message = EmailMessage()
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = address
    message["Subject"] = f"Decades Tracker · {event.title}"
    message.set_content(f"{event.body}\n\nOpen the tracker: {settings.public_url}{event.target_url}")
    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=20) as client:
        if os.getenv("SMTP_TLS", "true").casefold() not in {"0", "false", "no"}:
            client.starttls(context=context)
        if os.getenv("SMTP_USERNAME"):
            client.login(os.environ["SMTP_USERNAME"], os.getenv("SMTP_PASSWORD", ""))
        client.send_message(message)


def _send_webhook(url: str, event: NotificationEvent) -> None:
    response = httpx.post(url, json={
        "app": "Decades Tracker",
        "category": event.category,
        "title": event.title,
        "body": event.body,
        "url": f"{settings.public_url}{event.target_url}",
        "created_at": event.created_at.isoformat(),
    }, timeout=20)
    response.raise_for_status()


def deliver_pending() -> int:
    delivered = 0
    with SessionLocal() as session:
        events = list(session.scalars(select(NotificationEvent).order_by(
            NotificationEvent.created_at.asc()).limit(100)))
        for event in events:
            state = dict(event.delivery or {})
            memberships = list(session.scalars(select(Membership).where(
                Membership.workspace_id == event.workspace_id,
            )))
            for member in memberships:
                key = member.user_id
                if state.get(key, {}).get("complete"):
                    continue
                user = session.get(User, member.user_id)
                pref = session.get(NotificationPreference, {
                    "user_id": member.user_id,
                    "workspace_id": event.workspace_id,
                })
                if not user or not pref:
                    state[key] = {"complete": True, "reason": "no external delivery configured"}
                    continue
                categories = set(pref.categories or DEFAULT_CATEGORIES)
                if categories and event.category not in categories:
                    state[key] = {"complete": True, "reason": "category disabled"}
                    continue
                results: dict[str, str | bool] = {"complete": True}
                if pref.email_enabled:
                    if _smtp_configured():
                        try:
                            _send_email(user.email, event); results["email"] = "sent"; delivered += 1
                        except Exception as exc:
                            results.update(complete=False, email=f"error: {str(exc)[:120]}")
                    else:
                        results["email"] = "SMTP is not configured"
                if pref.webhook_enabled and pref.webhook_url:
                    try:
                        _send_webhook(pref.webhook_url, event); results["webhook"] = "sent"; delivered += 1
                    except Exception as exc:
                        results.update(complete=False, webhook=f"error: {str(exc)[:120]}")
                if not pref.email_enabled and not pref.webhook_enabled:
                    results["reason"] = "external delivery disabled"
                state[key] = results
            event.delivery = state
        session.commit()
    return delivered


def _loop() -> None:
    while True:
        try:
            deliver_pending()
        except Exception:
            pass
        time.sleep(30)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="decades-notifications", daemon=True).start()
