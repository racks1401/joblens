"""Polite HTTP client: global rate limit, timeout, retries with exponential backoff on 429/5xx."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.logs import log_event

log = logging.getLogger("joblens.http")


class RateLimiter:
    """At most `rps` requests per second across all callers."""

    def __init__(self, rps: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.interval = 1.0 / rps if rps > 0 else 0.0
        self.clock, self.sleep = clock, sleep
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = self.clock()
            if now < self.next_at:
                self.sleep(self.next_at - now)
                now = self.next_at
            self.next_at = now + self.interval


class PoliteClient:
    """GET JSON with retries. Returns (status_code, json_or_None); never raises on HTTP errors."""

    def __init__(self, user_agent: str, rps: float = 2, timeout_s: float = 15, retries: int = 3,
                 sleep: Callable[[float], None] = time.sleep, transport: httpx.BaseTransport | None = None) -> None:
        self.client = httpx.Client(headers={"User-Agent": user_agent, "Accept": "application/json"},
                                   timeout=timeout_s, follow_redirects=True, transport=transport)
        self.limiter = RateLimiter(rps, sleep=sleep)
        self.retries, self.sleep = retries, sleep

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        attempt = 0
        err: str | None = None
        while True:
            self.limiter.wait()
            try:
                r = self.client.get(url, params=params)
                status = r.status_code
            except httpx.TransportError as e:
                status, r = 0, None
                err = type(e).__name__
            else:
                err = None
            retryable = status == 0 or status == 429 or status >= 500
            if retryable and attempt < self.retries:
                delay = 2 ** attempt
                log_event(log, "http_retry", level=logging.WARNING, url=url, status=status, error=err, delay_s=delay)
                self.sleep(delay)
                attempt += 1
                continue
            if r is None or status != 200:
                return status, None
            try:
                return status, r.json()
            except ValueError:
                return status, None

    def close(self) -> None:
        self.client.close()
