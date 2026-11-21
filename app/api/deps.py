"""Shared route dependencies."""

from __future__ import annotations

import hmac
import os

from fastapi import Header

from app.api.errors import APIError
from app.config import get_settings

PLACEHOLDER_KEYS = {"", "change-me", "changeme", "admin", "secret", "test"}


def require_admin(x_api_key: str | None = Header(default=None)) -> None:
    """Admin routes need X-API-Key == ADMIN_API_KEY. A missing, short (< 12 chars) or placeholder key counts as
    not configured, so admin routes stay closed until a real key is set."""
    expected = get_settings().env.admin_api_key
    if expected.strip().lower() in PLACEHOLDER_KEYS or len(expected) < 12:
        expected = ""
    if not expected or not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise APIError(401, "unauthorized", "Valid X-API-Key header required")


UI_KEY_HEADER = "X-UI-Key"
UI_KEY_OPEN_PATHS = {"/health"}


def ui_key_error(path: str, given: str | None) -> APIError | None:
    """The UI-to-API key check run on every request, or None when the request may pass.

    UI_API_KEY is read from the process environment only (not from .env or settings files). When it is set, every
    route except /health needs the X-UI-Key header to match it. When it is unset the check is off (local runs,
    Docker Compose, tests), except in public-demo mode, where the API refuses everything but /health instead of
    running open. A short (< 12 chars) or placeholder key also refuses everything, so a weak key never protects."""
    if path in UI_KEY_OPEN_PATHS:
        return None
    expected = os.environ.get("UI_API_KEY", "").strip()
    if not expected:
        if get_settings().env.public_demo:
            return APIError(503, "ui_key_not_configured", "UI_API_KEY is not set; the public demo API is closed.")
        return None
    if expected.lower() in PLACEHOLDER_KEYS or len(expected) < 12:
        return APIError(503, "ui_key_misconfigured", "UI_API_KEY is too short or a placeholder; the API is closed.")
    if not given or not hmac.compare_digest(given.encode(), expected.encode()):
        return APIError(401, "ui_key_required", f"Valid {UI_KEY_HEADER} header required")
    return None
