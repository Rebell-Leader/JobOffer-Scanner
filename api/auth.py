"""FastAPI bearer-token dependency.

Reuses ``services.api_tokens.verify`` so the wire-format and verification
rules stay in one place. The dependency returns the resolved ``user_id`` for
the request; route handlers should declare ``user_id: int = Depends(require_user)``
to access it.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.api_tokens import verify

# auto_error=False lets us raise our own 401 with a consistent body shape.
_bearer = HTTPBearer(auto_error=False)


def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> int:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = verify(credentials.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id
