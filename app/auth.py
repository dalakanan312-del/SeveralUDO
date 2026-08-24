from __future__ import annotations

from datetime import datetime, timezone

from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Membership, User, Workspace
from .security import hash_secret, token


oauth = OAuth()
if settings.google_enabled:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def current_user(request: Request, session: Session) -> User | None:
    user_id = request.session.get("user_id")
    return session.get(User, user_id) if user_id else None


def require_user(request: Request, session: Session) -> User:
    user = current_user(request, session)
    if not user:
        raise HTTPException(401, "Sign in is required")
    return user


def provision_google_user(session: Session, claims: dict) -> tuple[User, Workspace, str | None]:
    subject = str(claims["sub"])
    email = str(claims["email"]).strip().casefold()
    user = session.scalar(select(User).where((User.google_subject == subject) | (User.email == email)))
    recovery_code = None
    if user is None:
        recovery_code = token(24)
        user = User(
            email=email,
            google_subject=subject,
            display_name=str(claims.get("name") or email.split("@", 1)[0]),
            picture_url=str(claims.get("picture") or ""),
            recovery_hash=hash_secret(recovery_code),
        )
        workspace = Workspace(name=f"{user.display_name}'s Chronicle")
        session.add_all([user, workspace])
        session.flush()
        session.add(Membership(user_id=user.id, workspace_id=workspace.id, role="owner"))
    else:
        user.google_subject = subject
        user.display_name = str(claims.get("name") or user.display_name)
        user.picture_url = str(claims.get("picture") or user.picture_url)
        user.last_login_at = datetime.now(timezone.utc)
        membership = session.scalar(select(Membership).where(Membership.user_id == user.id))
        workspace = session.get(Workspace, membership.workspace_id)
    session.flush()
    return user, workspace, recovery_code


def recover_user(session: Session, email: str, recovery_code: str) -> User | None:
    user = session.scalar(select(User).where(User.email == email.strip().casefold()))
    if not user or not user.recovery_hash:
        return None
    from .security import verify_secret
    return user if verify_secret(recovery_code.strip(), user.recovery_hash) else None
