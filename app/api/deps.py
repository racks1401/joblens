"""Shared route dependencies."""

from __future__ import annotations

import hmac

from fastapi import Header

from app.api.errors import APIError
from app.config import get_settings


def require_admin(x_api_key: str | None = Header(default=None)) -> None:
    """Admin routes need X-API-Key == ADMIN_API_KEY (and a key must be configured)."""
    expected = get_settings().env.admin_api_key
    if not expected or not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise APIError(401, "unauthorized", "Valid X-API-Key header required")
