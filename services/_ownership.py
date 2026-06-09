"""Shared ownership-scoping helper.

Every owned read/write fetches a row by primary key and refuses it when it's
missing OR belongs to another user — the security-critical check behind the
"return 404, not 403" anti-enumeration rule. Centralising it here means a future
call site can't silently forget the ``user_id`` comparison, and the policy lives
in exactly one place.

Usage::

    app = require_owned(session, Application, application_id, user_id,
                        ApplicationError, "Application not found.")

Raises ``exc(message)`` (the caller's service-specific error) on a missing or
cross-user row; returns the owned object otherwise.
"""

from __future__ import annotations

from typing import Type, TypeVar

from sqlalchemy.orm import Session

T = TypeVar("T")


def require_owned(
    session: Session,
    model: Type[T],
    obj_id: object,
    user_id: int,
    exc: Type[Exception],
    message: str,
) -> T:
    obj = session.get(model, obj_id)
    if obj is None or getattr(obj, "user_id", None) != user_id:
        raise exc(message)
    return obj
