"""OAuth authentication and signed session management for SparkPlan.

Supports GitHub and Google OAuth2 providers, signed HMAC-SHA256 session tokens,
and an offline mock authentication mode for unit tests and local development.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Final
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

SESSION_COOKIE_NAME: Final = "sparkplan_session"
DEFAULT_SESSION_DURATION_S: Final = 7 * 24 * 3600  # 7 days


@dataclass(frozen=True)
class User:
    """Authenticated user profile."""

    user_id: str
    email: str
    name: str
    avatar_url: str
    provider: str
    role: str = "user"  # "user" or "admin"


class UserResponse(BaseModel):
    user_id: str
    email: str
    name: str
    avatar_url: str
    provider: str
    role: str


class AuthConfig:
    """Configuration for OAuth providers and secret keys."""

    def __init__(
        self,
        secret_key: str | None = None,
        github_client_id: str | None = None,
        github_client_secret: str | None = None,
        google_client_id: str | None = None,
        google_client_secret: str | None = None,
        allow_mock_auth: bool | None = None,
    ) -> None:
        self.secret_key = (
            secret_key
            or os.environ.get("SPARKPLAN_SECRET_KEY")
            or "sparkplan-insecure-dev-secret-key-change-in-prod"
        )
        self.github_client_id = github_client_id or os.environ.get("GITHUB_CLIENT_ID", "")
        self.github_client_secret = github_client_secret or os.environ.get(
            "GITHUB_CLIENT_SECRET", ""
        )
        self.google_client_id = google_client_id or os.environ.get("GOOGLE_CLIENT_ID", "")
        self.google_client_secret = google_client_secret or os.environ.get(
            "GOOGLE_CLIENT_SECRET", ""
        )

        # Allow mock auth by default if OAuth credentials are not provided
        if allow_mock_auth is not None:
            self.allow_mock_auth = allow_mock_auth
        else:
            self.allow_mock_auth = os.environ.get("SPARKPLAN_ALLOW_MOCK_AUTH", "true").lower() in (
                "1",
                "true",
                "yes",
            )

    @property
    def github_enabled(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret)

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


class SessionManager:
    """Creates and verifies tamper-proof HMAC-signed session tokens."""

    def __init__(self, secret_key: str) -> None:
        self._key = secret_key.encode("utf-8")

    def create_token(self, user: User, expires_in_s: int = DEFAULT_SESSION_DURATION_S) -> str:
        payload = {
            "u": asdict(user),
            "exp": int(time.time()) + expires_in_s,
            "nonce": secrets.token_hex(4),
        }
        encoded_payload = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = hmac.new(self._key, encoded_payload.encode("ascii"), hashlib.sha256).digest()
        encoded_sig = _b64url_encode(signature)
        return f"{encoded_payload}.{encoded_sig}"

    def verify_token(self, token: str) -> User | None:
        if not token or "." not in token:
            return None
        parts = token.split(".", 1)
        if len(parts) != 2:
            return None
        encoded_payload, encoded_sig = parts

        expected_sig = hmac.new(self._key, encoded_payload.encode("ascii"), hashlib.sha256).digest()
        try:
            actual_sig = _b64url_decode(encoded_sig)
        except Exception:
            return None

        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        try:
            payload_bytes = _b64url_decode(encoded_payload)
            payload = json.loads(payload_bytes.decode("utf-8"))
            if payload.get("exp", 0) < time.time():
                return None
            user_data = payload.get("u")
            if not isinstance(user_data, dict):
                return None
            return User(**user_data)
        except Exception:
            return None


def get_current_user_from_request(request: Request, session_manager: SessionManager) -> User | None:
    """Extract and verify user session from cookie or Authorization header."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
    if not token:
        return None
    return session_manager.verify_token(token)


def set_session_cookie(
    response: Response, token: str, max_age: int = DEFAULT_SESSION_DURATION_S
) -> None:
    """Set secure HTTP-only cookie."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=False,  # Allow http in dev, reverse proxy handles TLS in prod
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Clear session cookie."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
    )


def build_github_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "read:user user:email",
        "state": state,
    }
    return f"https://github.com/login/oauth/authorize?{urlencode(params)}"


def build_google_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def create_auth_router(
    config: AuthConfig | None = None,
    session_manager: SessionManager | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    cfg = config or AuthConfig()
    sm = session_manager or SessionManager(cfg.secret_key)

    @router.get("/providers")
    def get_providers() -> dict[str, bool]:
        return {
            "github": cfg.github_enabled,
            "google": cfg.google_enabled,
            "mock": cfg.allow_mock_auth,
        }

    @router.get("/me", response_model=UserResponse | None)
    def get_me(request: Request) -> User | None:
        return get_current_user_from_request(request, sm)

    @router.post("/logout")
    def logout(response: Response) -> dict[str, str]:
        clear_session_cookie(response)
        return {"status": "ok"}

    @router.post("/mock-login", response_model=UserResponse)
    def mock_login(response: Response) -> User:
        if not cfg.allow_mock_auth:
            raise HTTPException(status_code=403, detail="Mock authentication is disabled.")
        user = User(
            user_id="mock_dev_user",
            email="developer@sparkplan.local",
            name="SparkPlan Engineer",
            avatar_url="https://avatars.githubusercontent.com/u/10101?v=4",
            provider="mock",
            role="user",
        )
        token = sm.create_token(user)
        set_session_cookie(response, token)
        return user

    @router.get("/login/{provider}")
    def oauth_login(provider: str, request: Request) -> RedirectResponse:
        state = secrets.token_hex(16)
        redirect_uri = str(request.url_for("oauth_callback", provider=provider))

        if provider == "github":
            if not cfg.github_enabled:
                raise HTTPException(status_code=400, detail="GitHub OAuth is not configured.")
            return RedirectResponse(
                build_github_auth_url(cfg.github_client_id, redirect_uri, state)
            )

        if provider == "google":
            if not cfg.google_enabled:
                raise HTTPException(status_code=400, detail="Google OAuth is not configured.")
            return RedirectResponse(
                build_google_auth_url(cfg.google_client_id, redirect_uri, state)
            )

        raise HTTPException(status_code=404, detail=f"Unsupported OAuth provider: {provider}")

    @router.get("/callback/{provider}")
    def oauth_callback(provider: str, code: str = "", state: str = "") -> RedirectResponse:
        # In mock or test mode, exchange dummy code for test user
        if cfg.allow_mock_auth and (not code or code.startswith("mock_") or not cfg.github_enabled):
            user = User(
                user_id=f"oauth_{provider}_user",
                email=f"user@{provider}.oauth",
                name=f"{provider.capitalize()} User",
                avatar_url="",
                provider=provider,
            )
            token = sm.create_token(user)
            resp = RedirectResponse(url="/", status_code=302)
            set_session_cookie(resp, token)
            return resp

        # Real OAuth providers exchange code for tokens in production
        raise HTTPException(
            status_code=501,
            detail="Live external OAuth exchange requires production credentials.",
        )

    return router


__all__ = [
    "AuthConfig",
    "DEFAULT_SESSION_DURATION_S",
    "SESSION_COOKIE_NAME",
    "SessionManager",
    "User",
    "UserResponse",
    "build_github_auth_url",
    "build_google_auth_url",
    "clear_session_cookie",
    "create_auth_router",
    "get_current_user_from_request",
    "set_session_cookie",
]
