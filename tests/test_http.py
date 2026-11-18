import httpx
import respx

from app.ingest.http import PoliteClient, RateLimiter


def test_retries_on_429_then_succeeds() -> None:
    sleeps: list[float] = []
    with respx.mock:
        route = respx.get("https://api.example/x").mock(
            side_effect=[httpx.Response(429), httpx.Response(503), httpx.Response(200, json={"ok": 1})]
        )
        cl = PoliteClient("JobLens-test", rps=0, retries=3, sleep=sleeps.append)
        assert cl.get_json("https://api.example/x") == (200, {"ok": 1})
        assert route.call_count == 3
    assert sleeps == [1, 2]  # exponential backoff


def test_gives_up_after_retries_and_404_not_retried() -> None:
    with respx.mock:
        r5 = respx.get("https://api.example/5").mock(return_value=httpx.Response(500))
        r4 = respx.get("https://api.example/4").mock(return_value=httpx.Response(404))
        cl = PoliteClient("JobLens-test", rps=0, retries=2, sleep=lambda _: None)
        assert cl.get_json("https://api.example/5") == (500, None)
        assert r5.call_count == 3
        assert cl.get_json("https://api.example/4") == (404, None)
        assert r4.call_count == 1


def test_user_agent_header_sent() -> None:
    with respx.mock:
        route = respx.get("https://api.example/ua").mock(return_value=httpx.Response(200, json=[]))
        PoliteClient("JobLens/0.1 (test)", rps=0).get_json("https://api.example/ua")
        assert route.calls[0].request.headers["user-agent"] == "JobLens/0.1 (test)"


def test_rate_limiter_spacing() -> None:
    t = [0.0]
    slept: list[float] = []

    def sleep(d: float) -> None:
        slept.append(d)
        t[0] += d

    rl = RateLimiter(2, clock=lambda: t[0], sleep=sleep)
    for _ in range(3):
        rl.wait()
    assert slept == [0.5, 0.5]
