from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .models import (
    ChronicleSave,
    LegacyWorkspaceCode,
    Membership,
    User,
    Workspace,
    WorkspaceInvite,
)
from .security import hash_secret, token


def legacy_database_error_message(exc: Exception) -> str:
    detail = str(exc).casefold()
    if any(marker in detail for marker in ("diskfull", "disk full", "project size limit", "could not extend file")):
        return (
            "The Neon database is full. The tracker can still read existing data, "
            "but new imports and changes require storage to be freed or the Neon plan to be expanded."
        )
    return "The Neon database could not be reached. Check the connection string and try again."


EDIT_ROLES = {"owner", "editor"}


def memberships_for(session: Session, user_id: str) -> list[Membership]:
    return list(session.scalars(select(Membership).where(Membership.user_id == user_id)))


def role_for(session: Session, user_id: str, workspace_id: str) -> str | None:
    row = session.scalar(select(Membership).where(
        Membership.user_id == user_id,
        Membership.workspace_id == workspace_id,
    ))
    return row.role if row else None


def require_owner(session: Session, user_id: str, workspace_id: str) -> Membership:
    row = session.scalar(select(Membership).where(
        Membership.user_id == user_id,
        Membership.workspace_id == workspace_id,
        Membership.role == "owner",
    ))
    if row is None:
        raise PermissionError("Only a workspace owner can do that.")
    return row


def create_invite(session: Session, workspace_id: str, invited_by: User,
                  email: str, role: str = "editor", days: int = 7) -> tuple[WorkspaceInvite, str]:
    require_owner(session, invited_by.id, workspace_id)
    normalized = email.strip().casefold()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise ValueError("Enter a valid email address.")
    role = role if role in EDIT_ROLES else "editor"
    raw = token(32)
    invite = WorkspaceInvite(
        workspace_id=workspace_id,
        email=normalized,
        role=role,
        token_hash=hash_secret(raw),
        invited_by_user_id=invited_by.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=max(1, min(days, 30))),
    )
    session.add(invite)
    session.flush()
    return invite, raw


def invitation_for_token(session: Session, raw: str) -> WorkspaceInvite | None:
    invite = session.scalar(select(WorkspaceInvite).where(
        WorkspaceInvite.token_hash == hash_secret(raw.strip()),
        WorkspaceInvite.revoked.is_(False),
        WorkspaceInvite.accepted_at.is_(None),
    ))
    if not invite:
        return None
    expires = invite.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return invite if expires > datetime.now(timezone.utc) else None


def accept_invitation(session: Session, user: User, raw: str) -> Membership:
    invite = invitation_for_token(session, raw)
    if not invite:
        raise ValueError("That invitation is invalid, expired, or already used.")
    return accept_invitation_record(session, user, invite)


def accept_invitation_record(session: Session, user: User, invite: WorkspaceInvite) -> Membership:
    expires = invite.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if invite.revoked or invite.accepted_at is not None or expires <= datetime.now(timezone.utc):
        raise ValueError("That invitation is invalid, expired, or already used.")
    if invite.email and invite.email != user.email.casefold():
        raise ValueError(f"This invitation was created for {invite.email}. Sign in with that Google account.")
    membership = session.scalar(select(Membership).where(
        Membership.user_id == user.id,
        Membership.workspace_id == invite.workspace_id,
    ))
    if membership:
        if membership.role != "owner":
            membership.role = invite.role
    else:
        membership = Membership(user_id=user.id, workspace_id=invite.workspace_id, role=invite.role)
        session.add(membership)
    invite.accepted_at = datetime.now(timezone.utc)
    session.flush()
    return membership


def register_legacy_code(session: Session, workspace_id: str, user: User,
                         raw_code: str, label: str = "Legacy workspace") -> LegacyWorkspaceCode:
    require_owner(session, user.id, workspace_id)
    code = raw_code.strip()
    if len(code) < 12:
        raise ValueError("Workspace codes must be at least 12 characters long.")
    digest = hash_secret(code)
    existing = session.scalar(select(LegacyWorkspaceCode).where(LegacyWorkspaceCode.code_hash == digest))
    if existing and existing.workspace_id != workspace_id:
        raise ValueError("That code is already connected to another workspace.")
    if existing:
        return existing
    row = LegacyWorkspaceCode(
        workspace_id=workspace_id,
        code_hash=digest,
        label=label.strip() or "Legacy workspace",
        created_by_user_id=user.id,
    )
    session.add(row)
    session.flush()
    return row


def _infer_legacy_workspace(session: Session, digest: str) -> Workspace | None:
    """Match the old Neon owner hash to already-migrated v4 save names.

    Old tables may not exist on a new installation; that is an expected and
    harmless condition. The private code itself is never queried or stored.
    """
    try:
        with session.begin_nested():
            names = [str(row[0]).strip().casefold() for row in session.execute(text(
                "SELECT name FROM public.decades_saves WHERE owner_hash=:digest"
            ), {"digest": digest}) if row[0]]
    except SQLAlchemyError:
        return None
    if not names:
        return None
    candidates = list(session.scalars(select(ChronicleSave)))
    workspace_ids = {save.workspace_id for save in candidates if save.name.strip().casefold() in names}
    return session.get(Workspace, next(iter(workspace_ids))) if len(workspace_ids) == 1 else None


def claim_legacy_code(session: Session, user: User, raw_code: str,
                      source_url: str = "", preferred_workspace: Workspace | None = None) -> tuple[Workspace, list[dict]]:
    code = raw_code.strip()
    if len(code) < 12:
        raise ValueError("Workspace codes must be at least 12 characters long.")
    digest = hash_secret(code)
    link = session.scalar(select(LegacyWorkspaceCode).where(LegacyWorkspaceCode.code_hash == digest))
    workspace = session.get(Workspace, link.workspace_id) if link else _infer_legacy_workspace(session, digest)
    # A returning owner/editor has already completed the expensive migration.
    # Login must be read-only in this case: re-reading every legacy table wastes
    # memory and can rewrite a large imported save merely to open its workspace.
    if workspace is not None:
        existing_membership = session.scalar(select(Membership).where(
            Membership.user_id == user.id,
            Membership.workspace_id == workspace.id,
        ))
        if existing_membership is not None:
            return workspace, []
    imported: list[dict] = []
    try:
        from . import legacy_neon
        workspace, imported = legacy_neon.import_owner_workspace(
            session, user, digest, source_url, workspace or preferred_workspace
        )
    except (LookupError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    except SQLAlchemyError as exc:
        raise ValueError(legacy_database_error_message(exc)) from exc
    if link is None:
        link = LegacyWorkspaceCode(
            workspace_id=workspace.id,
            code_hash=digest,
            label="Imported legacy workspace",
            created_by_user_id=user.id,
        )
        session.add(link)
    membership = session.scalar(select(Membership).where(
        Membership.user_id == user.id,
        Membership.workspace_id == workspace.id,
    ))
    if not membership:
        owner_exists = session.scalar(select(Membership.user_id).where(
            Membership.workspace_id == workspace.id,
            Membership.role == "owner",
        ))
        session.add(Membership(
            user_id=user.id,
            workspace_id=workspace.id,
            role="editor" if owner_exists else "owner",
        ))
    link.claimed_at = datetime.now(timezone.utc)
    session.flush()
    return workspace, imported


def auto_claim_linked_email(session: Session, user: User) -> Workspace | None:
    """Honor an email that was linked to a 3.x workspace before Google login."""
    try:
        with session.begin_nested():
            digest = session.execute(text(
                "SELECT workspace_hash FROM public.decades_identities WHERE lower(email)=:email"
            ), {"email": user.email.casefold()}).scalar_one_or_none()
    except SQLAlchemyError:
        return None
    if not digest:
        return None
    link = session.scalar(select(LegacyWorkspaceCode).where(LegacyWorkspaceCode.code_hash == str(digest)))
    workspace = session.get(Workspace, link.workspace_id) if link else _infer_legacy_workspace(session, str(digest))
    if not workspace:
        try:
            from . import legacy_neon
            workspace, _imported = legacy_neon.import_owner_workspace(session, user, str(digest))
        except (LookupError, ValueError, SQLAlchemyError):
            return None
    membership = session.scalar(select(Membership).where(
        Membership.user_id == user.id,
        Membership.workspace_id == workspace.id,
    ))
    if not membership:
        session.add(Membership(user_id=user.id, workspace_id=workspace.id, role="editor"))
    return workspace
