from __future__ import annotations

import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send


SESSION_MODE_KEY = "_session_mode"
PERSISTENT_MODE = "persistent"
BROWSER_MODE = "browser"
REMEMBER_DEVICE_SECONDS = 60 * 60 * 24 * 90
_MAX_AGE_PATTERN = re.compile(r";\s*max-age=[^;]*", re.IGNORECASE)


def persistent_session_requested(value: object) -> bool:
    """Interpret the value submitted by a stay-signed-in checkbox."""
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def set_session_mode(request, stay_signed_in: object) -> None:
    request.session[SESSION_MODE_KEY] = (
        PERSISTENT_MODE if persistent_session_requested(stay_signed_in) else BROWSER_MODE
    )


def apply_session_cookie_mode(cookie: str, mode: str, max_age: int) -> str:
    """Apply the selected persistence policy to a non-expiring session cookie."""
    if mode == BROWSER_MODE:
        return _MAX_AGE_PATTERN.sub("", cookie)
    if mode == PERSISTENT_MODE:
        replacement = f"; Max-Age={int(max_age)}"
        if _MAX_AGE_PATTERN.search(cookie):
            return _MAX_AGE_PATTERN.sub(replacement, cookie)
        return f"{cookie}{replacement}"
    return cookie


class StaySignedInMiddleware:
    """Make a signed session cookie persistent only when the user asks for it."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        session_cookie: str = "session",
        persistent_max_age: int = REMEMBER_DEVICE_SECONDS,
    ) -> None:
        self.app = app
        self.cookie_prefix = f"{session_cookie}=".encode("latin-1")
        self.persistent_max_age = int(persistent_max_age)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                session = scope.get("session") or {}
                mode = session.get(SESSION_MODE_KEY)
                if mode in {PERSISTENT_MODE, BROWSER_MODE}:
                    rewritten: list[tuple[bytes, bytes]] = []
                    for name, value in message.get("headers", []):
                        if name.lower() == b"set-cookie" and value.lower().startswith(self.cookie_prefix.lower()):
                            cookie = value.decode("latin-1")
                            # A cleared session must remain an immediate deletion.
                            if not cookie.casefold().startswith(
                                f"{self.cookie_prefix.decode('latin-1')}null;"
                            ):
                                cookie = apply_session_cookie_mode(
                                    cookie, mode, self.persistent_max_age
                                )
                            value = cookie.encode("latin-1")
                        rewritten.append((name, value))
                    message["headers"] = rewritten
            await send(message)

        await self.app(scope, receive, send_wrapper)
