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
    # Tier gate: REST API access is a paid-plan feature when billing is
    # enabled; a no-op for self-hosted deployments (no Stripe key => unlimited
    # tier). 402 (not 403) so clients can distinguish "upgrade" from "denied".
    from services.billing import api_access_allowed

    if not api_access_allowed(user_id):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="API access requires the Power plan. Upgrade to use the REST API.",
        )
    return user_id
