"""HTTP hardening middleware — security headers and request body limits."""

from typing import Callable

from fastapi import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings

# Headers applied to every response. CSP is intentionally absent: the SPA
# loads Tailwind and React from CDNs and inlines styles, so a policy tight
# enough to matter would need a separate rollout with nonces.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
HSTS_HEADER = "max-age=31536000; includeSubDomains"


async def security_headers_middleware(request: Request, call_next: Callable) -> Response:
    """Attach baseline security headers to every response."""
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if get_settings().is_production:
        response.headers.setdefault("Strict-Transport-Security", HSTS_HEADER)
    return response


class BodySizeLimitMiddleware:
    """Reject request bodies larger than ``max_bytes`` with HTTP 413.

    Checks ``Content-Length`` up front and, for chunked bodies, counts the
    bytes actually received so a client cannot bypass the limit by omitting
    the header. Uvicorn has no built-in body limit; without this a single
    multipart upload could exhaust the container's memory.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                pass

        received = 0
        too_large = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    # Starve the downstream reader so it stops consuming.
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def guarded_send(message: Message) -> None:
            if too_large and message["type"] == "http.response.start":
                message = {**message, "status": 413}
            await send(message)

        await self.app(scope, limited_receive, guarded_send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit_mb = self.max_bytes // (1024 * 1024)
        response = JSONResponse(
            status_code=413,
            content={"detail": f"Cererea depășește limita de {limit_mb}MB"},
        )
        await response(scope, receive, send)
