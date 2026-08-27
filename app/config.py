from __future__ import annotations

import os
import hashlib
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'") )


_load_env()


def _database_url() -> str:
    return os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL") or "sqlite:///./data/decades-v4.db"


def _automatic_snapshots(database_url: str | None = None) -> bool:
    configured = os.getenv("DECADES_AUTOMATIC_SNAPSHOTS", "").strip().casefold()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    return (database_url or _database_url()).startswith("sqlite")


def _public_url() -> str:
    configured = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        return configured
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    return f"https://{railway_domain}" if railway_domain else "http://127.0.0.1:8000"


def _session_secret() -> str:
    configured = os.getenv("SESSION_SECRET", "").strip()
    if configured:
        return configured
    # The 3.x Railway deployment already has this private value. Domain
    # separation prevents the original access value from becoming the cookie
    # signing key while keeping the first hosted 4.x deployment secure.
    transition_secret = os.getenv("OWNER_ACCESS_KEY", "").strip()
    if transition_secret:
        return hashlib.sha256(f"decades-v4-session:{transition_secret}".encode()).hexdigest()
    return "development-only-change-me"


@dataclass(frozen=True)
class Settings:
    database_url: str = _database_url()
    session_secret: str = _session_secret()
    public_url: str = _public_url()
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
    google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    portrait_provider: str = os.getenv("PORTRAIT_PROVIDER", "manual").casefold()
    comfyui_url: str = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_image_model: str = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
    desktop_installer_url: str = os.getenv(
        "DESKTOP_INSTALLER_URL",
        "https://github.com/dalakanan312-del/SeveralUDO/releases/download/v4.2.9/Decades-Tracker-4.2.9-Setup.exe",
    )
    skip_startup_migrations: bool = os.getenv("DECADES_SKIP_STARTUP_MIGRATIONS", "").casefold() in {"1","true","yes","on"}
    automatic_snapshots: bool = _automatic_snapshots(database_url)

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def local_mode(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlalchemy_database_url(self) -> str:
        """Use SQLAlchemy's psycopg 3 driver for ordinary Neon URLs."""
        if self.database_url.startswith("postgres://"):
            return "postgresql+psycopg://" + self.database_url.removeprefix("postgres://")
        if self.database_url.startswith("postgresql://"):
            return "postgresql+psycopg://" + self.database_url.removeprefix("postgresql://")
        return self.database_url


settings = Settings()
