"""Security-header middleware for the FastAPI surface.

Adds the standard defensive headers to every response (including error
responses). All values are overridable via env so an operator can tune the
policy without a code change:

  API_CSP             default-src 'none'; frame-ancestors 'none'
  API_ENABLE_HSTS     "1" to emit Strict-Transport-Security (only set this
                      when the API is served over TLS — HSTS on plain HTTP is
                      a footgun)
  API_HSTS_MAX_AGE    63072000 (2 years)

Rationale per header:
  * CSP ``default-src 'none'`` — the API returns JSON, never HTML/JS, so the
    strictest possible policy is correct and costs nothing.
  * X-Content-Type-Options: nosniff — stop content-type sniffing.
  * X-Frame-Options: DENY + frame-ancestors 'none' — the API must never be
    framed.
  * Referrer-Policy: no-referrer — don't leak token-bearing URLs.
  * Cache-Control: no-store — responses can carry personal data; keep them
    out of shared caches.
  * Permissions-Policy — disable powerful browser features wholesale.
"""

from __future__ import annotations

import os

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from utils.env import env_bool


def _csp() -> str:
    return os.getenv("API_CSP", "default-src 'none'; frame-ancestors 'none'")


def _hsts_value() -> str | None:
    if not env_bool("API_ENABLE_HSTS"):
        return None
    max_age = os.getenv("API_HSTS_MAX_AGE", "63072000")
    return f"max-age={max_age}; includeSubDomains"


def security_headers() -> dict[str, str]:
    """The header set as a plain dict (also handy for tests)."""
    headers = {
        "Content-Security-Policy": _csp(),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    }
    hsts = _hsts_value()
    if hsts:
        headers["Strict-Transport-Security"] = hsts
    return headers


class SecurityHeadersMiddleware:
    """Pure-ASGI middleware so headers attach even to streamed/error responses.

    We hook the ``http.response.start`` message and append our header set to
    whatever the route produced. Existing headers are not duplicated — the
    route rarely sets these, but if it does, ours are appended after, and
    well-behaved clients honour the first; to be safe we de-dupe by name.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {
                    k.decode("latin-1").lower()
                    for k, _ in message.get("headers", [])
                }
                extra = [
                    (k.encode("latin-1"), v.encode("latin-1"))
                    for k, v in security_headers().items()
                    if k.lower() not in existing
                ]
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        await self.app(scope, receive, send_with_headers)


def add_security_headers(app) -> None:
    """Attach the middleware to a FastAPI/Starlette app."""
    app.add_middleware(SecurityHeadersMiddleware)
