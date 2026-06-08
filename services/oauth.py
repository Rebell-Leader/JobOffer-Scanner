"""OAuth 2.0 authorization-code login for Google and GitHub.

The flow (Streamlit has no native routing, so we use query params):

  1. ``build_authorize_url(provider, state)`` — the UI renders a link to this.
     ``state`` is a CSRF nonce stashed in the Streamlit session.
  2. Provider redirects back to ``OAUTH_REDIRECT_URI?code=…&state=…``.
  3. The UI reads the query params, checks ``state``, and calls
     ``complete_login(provider, code)``.
  4. ``complete_login`` exchanges the code for a token, fetches the user's
     email + stable id, then resolves to a local account:
       * existing OAuthIdentity        -> that user (login)
       * email matches existing user   -> link a new identity (link)
       * otherwise                     -> create a user + identity (register)

The network steps (token exchange, userinfo) are isolated in
``_exchange_code`` / ``_fetch_userinfo`` so the resolution logic
(``resolve_identity``) is unit-testable without hitting a provider.

Configuration (per provider, all required to enable it):
  GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET
  GITHUB_OAUTH_CLIENT_ID / GITHUB_OAUTH_CLIENT_SECRET
  OAUTH_REDIRECT_URI   (e.g. https://yourapp/  — must match provider config)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlencode

import requests
from sqlalchemy import select

from db.models import OAuthIdentity
from db.session import get_session
from services.audit import record as _audit
from services.auth import AuthedUser, create_oauth_user, find_user_by_email
from utils.env import env_float

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = env_float("OAUTH_HTTP_TIMEOUT", 10.0)


class OAuthError(ValueError):
    """User-facing OAuth failure (provider not configured, bad code, etc.)."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    client_id_env: str
    client_secret_env: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scopes: str
    label: str


PROVIDERS: Dict[str, ProviderConfig] = {
    "google": ProviderConfig(
        name="google",
        client_id_env="GOOGLE_OAUTH_CLIENT_ID",
        client_secret_env="GOOGLE_OAUTH_CLIENT_SECRET",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scopes="openid email",
        label="Google",
    ),
    "github": ProviderConfig(
        name="github",
        client_id_env="GITHUB_OAUTH_CLIENT_ID",
        client_secret_env="GITHUB_OAUTH_CLIENT_SECRET",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user",
        scopes="read:user user:email",
        label="GitHub",
    ),
}


def _config(provider: str) -> ProviderConfig:
    cfg = PROVIDERS.get(provider)
    if cfg is None:
        raise OAuthError(f"Unknown OAuth provider: {provider!r}")
    return cfg


def is_configured(provider: str) -> bool:
    try:
        cfg = _config(provider)
    except OAuthError:
        return False
    return bool(
        os.getenv(cfg.client_id_env)
        and os.getenv(cfg.client_secret_env)
        and os.getenv("OAUTH_REDIRECT_URI")
    )


def configured_providers() -> list[str]:
    return [name for name in PROVIDERS if is_configured(name)]


# ---------------------------------------------------------------------------
# Step 1: authorize URL
# ---------------------------------------------------------------------------

def build_authorize_url(provider: str, state: str) -> str:
    cfg = _config(provider)
    if not is_configured(provider):
        raise OAuthError(f"{cfg.label} login is not configured.")
    params = {
        "client_id": os.getenv(cfg.client_id_env),
        "redirect_uri": os.getenv("OAUTH_REDIRECT_URI"),
        "response_type": "code",
        "scope": cfg.scopes,
        "state": state,
    }
    if provider == "google":
        # Ask for a fresh consent + offline so the email scope is reliably
        # present; harmless for our purposes.
        params["access_type"] = "online"
        params["prompt"] = "select_account"
    return f"{cfg.authorize_url}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Steps 2-3: code exchange + userinfo (network — isolated for testability)
# ---------------------------------------------------------------------------

def _exchange_code(cfg: ProviderConfig, code: str) -> str:
    """Exchange an auth code for an access token. Returns the access token."""
    data = {
        "client_id": os.getenv(cfg.client_id_env),
        "client_secret": os.getenv(cfg.client_secret_env),
        "code": code,
        "redirect_uri": os.getenv("OAUTH_REDIRECT_URI"),
        "grant_type": "authorization_code",
    }
    resp = requests.post(
        cfg.token_url, data=data,
        headers={"Accept": "application/json"},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise OAuthError("Provider did not return an access token.")
    return token


def _as_bool(value) -> bool:
    """Coerce a provider claim (bool or "true"/"1"/"yes") to a real bool."""
    return value is True or str(value).strip().lower() in {"true", "1", "yes"}


def _fetch_userinfo(cfg: ProviderConfig, access_token: str) -> dict:
    """Fetch the user's profile; normalise to {provider_user_id, email,
    email_verified}.

    ``email_verified`` reflects whether the PROVIDER asserts the address is
    verified — Google's OIDC ``email_verified`` claim, or (GitHub) a verified
    primary from ``/user/emails``. It gates auto-linking to a pre-existing
    local account (see ``resolve_identity``), so an attacker can't take over an
    account by pointing an unverified provider email at it.
    """
    resp = requests.get(
        cfg.userinfo_url,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    info = resp.json()

    if cfg.name == "google":
        return {
            "provider_user_id": str(info.get("sub")),
            "email": info.get("email"),
            "email_verified": _as_bool(info.get("email_verified")),
        }
    if cfg.name == "github":
        # GitHub's /user.email carries no verified flag, so always consult
        # /user/emails for a VERIFIED primary. Only that path is trusted as
        # verified; the public profile email is accepted but unverified.
        verified = _github_verified_primary_email(access_token)
        if verified:
            return {"provider_user_id": str(info.get("id")), "email": verified,
                    "email_verified": True}
        return {"provider_user_id": str(info.get("id")), "email": info.get("email"),
                "email_verified": False}
    return {"provider_user_id": str(info.get("id") or info.get("sub")),
            "email": info.get("email"),
            "email_verified": _as_bool(info.get("email_verified"))}


def _github_verified_primary_email(access_token: str) -> Optional[str]:
    """Return the user's VERIFIED primary GitHub email, or None.

    Deliberately returns None (rather than an unverified fallback) when no
    verified primary exists — an unverified address must not be treated as
    proof of ownership.
    """
    try:
        resp = requests.get(
            "https://api.github.com/user/emails",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        emails = resp.json()
        return next(
            (e["email"] for e in emails if e.get("primary") and e.get("verified")),
            None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("GitHub email fetch failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Step 4: resolve to a local account (pure — testable)
# ---------------------------------------------------------------------------

def resolve_identity(
    provider: str, provider_user_id: str, email: Optional[str],
    email_verified: bool = False,
) -> AuthedUser:
    """Map an external identity to a local account, linking/creating as needed.

    Resolution order:
      1. Existing OAuthIdentity row -> that user (audit: oauth.login).
      2. Email matches an existing user -> link a new identity (oauth.link),
         but ONLY if the provider asserts the email is verified. Auto-linking
         on an UNVERIFIED email is an account-takeover vector (an attacker sets
         their provider email to the victim's address), so we refuse it and
         tell the user to link from settings after a password login.
      3. Otherwise -> create a new user + identity (oauth.register), recording
         the provider's verification status on the new account.
    """
    if not provider_user_id:
        raise OAuthError("Provider did not return a stable user id.")

    with get_session() as session:
        identity = session.execute(
            select(OAuthIdentity)
            .where(OAuthIdentity.provider == provider)
            .where(OAuthIdentity.provider_user_id == provider_user_id)
        ).scalar_one_or_none()
        if identity is not None:
            user_id = identity.user_id
            email_val = identity.email
            _audit("user.oauth.login", user_id=user_id,
                   details={"provider": provider})
            return AuthedUser(id=user_id, email=email_val or (email or ""))

    # No identity yet — we need an email to link/create.
    if not email:
        raise OAuthError(
            "Could not get a verified email from the provider. Make sure your "
            "account has a public/verified email."
        )

    existing = find_user_by_email(email)
    if existing is not None:
        if not email_verified:
            _audit("user.oauth.link_refused", user_id=existing.id,
                   details={"provider": provider, "reason": "unverified_email"})
            raise OAuthError(
                "An account already exists for this email. For your security we "
                f"won't link {provider} to it from an unverified email. Sign in "
                "with your password, then link the provider from account settings "
                "(or verify the email with the provider first)."
            )
        _link_identity(existing.id, provider, provider_user_id, email)
        _audit("user.oauth.link", user_id=existing.id, details={"provider": provider})
        return existing

    created = create_oauth_user(email, email_verified=email_verified)
    _link_identity(created.id, provider, provider_user_id, email)
    _audit("user.oauth.register", user_id=created.id, details={"provider": provider})
    return created


def _link_identity(user_id: int, provider: str, provider_user_id: str, email: Optional[str]) -> None:
    with get_session() as session:
        session.add(OAuthIdentity(
            user_id=user_id, provider=provider,
            provider_user_id=provider_user_id, email=email,
        ))
        session.commit()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def complete_login(provider: str, code: str) -> AuthedUser:
    """Full server-side half of the flow: code -> token -> userinfo -> account."""
    cfg = _config(provider)
    if not is_configured(provider):
        raise OAuthError(f"{cfg.label} login is not configured.")
    if not code:
        raise OAuthError("Missing authorization code.")
    try:
        token = _exchange_code(cfg, code)
        info = _fetch_userinfo(cfg, token)
    except OAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise OAuthError(f"{cfg.label} sign-in failed: {exc}") from exc
    return resolve_identity(
        provider, info["provider_user_id"], info.get("email"),
        email_verified=bool(info.get("email_verified")),
    )
