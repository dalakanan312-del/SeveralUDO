"""Reject browser cross-site mutations without interrupting token-authenticated relays."""
from urllib.parse import urlsplit
from starlette.responses import JSONResponse


def origin(value):
    try:
        parts = urlsplit(value)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            return None
        return parts.scheme, parts.hostname.casefold(), parts.port or (443 if parts.scheme == 'https' else 80)
    except ValueError:
        return None


class RequestSafetyMiddleware:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get('headers', [])}
        path = scope.get('path', '')
        authenticated_api = path in {'/api/sync/push','/api/sync/pull','/api/clock/report','/api/clock/ping'} and headers.get('authorization', '').startswith('Bearer ')
        unsafe = scope['method'] not in {'GET', 'HEAD', 'OPTIONS'}
        supplied = headers.get('origin')
        site = headers.get('sec-fetch-site')
        expected = {origin(scope.get('scheme', 'http')+'://'+headers.get('host', ''))}
        if not self.settings.local_mode:
            expected.add(origin(self.settings.public_url))
        expected.discard(None)
        blocked = (unsafe and supplied is not None and origin(supplied) not in expected)
        blocked |= site == 'cross-site' and (unsafe or self.settings.local_mode)
        if blocked and not authenticated_api:
            response = JSONResponse({'detail': 'Open the tracker directly and try again. Cross-site changes are not allowed.'}, status_code=403)
            return await response(scope, receive, send)
        await self.app(scope, receive, send)
