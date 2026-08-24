from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def uid() -> str:
    return uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    google_subject: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(160), default="")
    picture_url: Mapped[str] = mapped_column(Text, default="")
    recovery_hash: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(160), default="My Chronicle")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Membership(Base):
    __tablename__ = "memberships"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(30), default="owner")


class WorkspaceInvite(Base):
    """One-use invitation for adding a Google account to a workspace."""
    __tablename__ = "workspace_invites"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(String(30), default="editor")
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    invited_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LegacyWorkspaceCode(Base):
    """Hash-only bridge from a private legacy code to its v4 workspace."""
    __tablename__ = "legacy_workspace_codes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(160), default="Legacy workspace")
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    browser_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    webhook_url: Mapped[str] = mapped_column(Text, default="")
    categories: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class NotificationEvent(Base):
    __tablename__ = "notification_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    save_id: Mapped[str] = mapped_column(ForeignKey("saves.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(240))
    body: Mapped[str] = mapped_column(Text, default="")
    target_url: Mapped[str] = mapped_column(Text, default="/p/automation")
    source_key: Mapped[str] = mapped_column(String(255), default="")
    delivery: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    __table_args__ = (UniqueConstraint("save_id", "source_key", name="uq_notification_save_source"),)


class BackupSnapshot(Base):
    __tablename__ = "backup_snapshots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    save_id: Mapped[str] = mapped_column(ForeignKey("saves.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(80), default="automatic")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    package: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class ChronicleSave(Base):
    __tablename__ = "saves"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    global_day: Mapped[int] = mapped_column(Integer, default=1)
    start_year: Mapped[int] = mapped_column(Integer, default=1300)
    days_per_year: Mapped[int] = mapped_column(Integer, default=4)
    pregnancy_days: Mapped[int] = mapped_column(Integer, default=4)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Record(Base):
    """Versioned domain record shared by every tracker feature."""
    __tablename__ = "records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    save_id: Mapped[str] = mapped_column(ForeignKey("saves.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    label: Mapped[str] = mapped_column(String(240), default="")
    global_day: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    updated_by_device: Mapped[str] = mapped_column(String(32), default="server")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (Index("ix_records_save_kind_day", "save_id", "kind", "global_day"),)


class Change(Base):
    __tablename__ = "changes"
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(32), unique=True, default=uid)
    save_id: Mapped[str] = mapped_column(ForeignKey("saves.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    record_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    operation: Mapped[str] = mapped_column(String(12))
    base_version: Mapped[int] = mapped_column(Integer, default=0)
    new_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Conflict(Base):
    __tablename__ = "sync_conflicts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    save_id: Mapped[str] = mapped_column(String(32), index=True)
    record_id: Mapped[str] = mapped_column(String(32), index=True)
    local_change: Mapped[dict] = mapped_column(JSON)
    server_record: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    save_id: Mapped[str] = mapped_column(ForeignKey("saves.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DiceAudit(Base):
    __tablename__ = "dice_audit"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    save_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    context: Mapped[str] = mapped_column(String(80), default="practice")
    context_id: Mapped[str] = mapped_column(String(32), default="")
    notation: Mapped[str] = mapped_column(String(30))
    faces: Mapped[list] = mapped_column(JSON)
    total: Mapped[int] = mapped_column(Integer)
    commitment: Mapped[str] = mapped_column(String(64))
    reveal: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class ClockLink(Base):
    __tablename__ = "clock_links"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    save_id: Mapped[str] = mapped_column(ForeignKey("saves.id", ondelete="CASCADE"), unique=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    game_anchor_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tracker_anchor_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_game_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_game_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_game_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Portrait(Base):
    __tablename__ = "portraits"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    save_id: Mapped[str] = mapped_column(ForeignKey("saves.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(40), default="default")
    mime_type: Mapped[str] = mapped_column(String(80))
    image: Mapped[bytes] = mapped_column(LargeBinary)
    source: Mapped[str] = mapped_column(String(30), default="upload")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("record_id", "stage", name="uq_portrait_record_stage"),)
