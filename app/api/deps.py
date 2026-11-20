"""Shared route dependencies."""

from __future__ import annotations

import hmac

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
