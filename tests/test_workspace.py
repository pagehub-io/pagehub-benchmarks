"""DOM readiness probe — pagehub-browser session lifecycle, with httpx.MockTransport.

The probe drives pagehub-browser through four calls per attempt: POST
``/v1/sessions`` → POST ``/v1/sessions/{id}/navigate`` → POST
``/v1/sessions/{id}/wait-for`` → DELETE ``/v1/sessions/{id}``. We mock the
transport so each test scripts the exact sequence of responses and asserts
the probe returns the expected verdict without ever touching a real browser
service.
"""

from __future__ import annotations

import httpx

from pagehub_benchmarks.runner.workspace import wait_for_dom_ready


def _make_client(handler):
    return httpx.Client(
        base_url="http://browser.test", transport=httpx.MockTransport(handler)
    )


def test_returns_true_on_first_attempt_when_testid_visible():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/v1/sessions":
            return httpx.Response(201, json={"id": "sess-abc"})
        if request.method == "POST" and request.url.path == "/v1/sessions/sess-abc/navigate":
            return httpx.Response(200, json={"ok": True})
        if request.method == "POST" and request.url.path == "/v1/sessions/sess-abc/wait-for":
            return httpx.Response(200, json={"message": "Element is now visible."})
        if request.method == "DELETE" and request.url.path == "/v1/sessions/sess-abc":
            return httpx.Response(204)
        return httpx.Response(500, json={"error": f"unexpected {request.method} {request.url.path}"})

    client = _make_client(handler)
    ok = wait_for_dom_ready(
        browser_base_url="http://browser.test",
        sut_url="http://host.docker.internal:8004",
        testid="board",
        timeout_s=10.0,
        client=client,
    )
    assert ok is True
    # The session was cleaned up (DELETE called) even on the success path —
    # otherwise pagehub-browser would leak headless-Chrome instances per attempt.
    assert ("DELETE", "/v1/sessions/sess-abc") in calls


def test_navigate_payload_carries_sut_url_and_testid():
    """The navigate URL must be the SUT-from-browser perspective (i.e.
    ``host.docker.internal`` preserved, NOT localhost-translated). The
    wait-for locator must be ``strategy: testid`` with the configured value.
    Anything else and the probe is testing the wrong page or the wrong
    element."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/sessions":
            return httpx.Response(201, json={"id": "s1"})
        if request.method == "POST" and request.url.path.endswith("/navigate"):
            captured["navigate_body"] = request.read().decode()
            return httpx.Response(200, json={"ok": True})
        if request.method == "POST" and request.url.path.endswith("/wait-for"):
            captured["wait_for_body"] = request.read().decode()
            return httpx.Response(200, json={"message": "visible"})
        return httpx.Response(204)

    client = _make_client(handler)
    wait_for_dom_ready(
        browser_base_url="http://browser.test",
        sut_url="http://host.docker.internal:8004",
        testid="board",
        timeout_s=10.0,
        client=client,
    )
    import json as _json
    nav = _json.loads(captured["navigate_body"])  # type: ignore[arg-type]
    wf = _json.loads(captured["wait_for_body"])  # type: ignore[arg-type]
    assert nav["url"] == "http://host.docker.internal:8004"
    assert wf["locator"] == {"strategy": "testid", "value": "board"}
    assert wf["state"] == "visible"


def test_retries_when_wait_for_404s_then_succeeds(monkeypatch):
    """The whole session-open → navigate → wait-for sequence retries until
    either the testid materializes or the outer timeout trips. A wait-for
    404 on attempt 1 should NOT abort the probe."""
    # Squash the per-iter sleep so the test runs in millis.
    monkeypatch.setattr("pagehub_benchmarks.runner.workspace.time.sleep", lambda _s: None)

    sequence = iter(
        [
            httpx.Response(404, json={"error": "not visible yet"}),
            httpx.Response(200, json={"message": "visible"}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/sessions":
            return httpx.Response(201, json={"id": "s-x"})
        if request.method == "POST" and request.url.path.endswith("/navigate"):
            return httpx.Response(200, json={"ok": True})
        if request.method == "POST" and request.url.path.endswith("/wait-for"):
            return next(sequence)
        return httpx.Response(204)

    client = _make_client(handler)
    ok = wait_for_dom_ready(
        browser_base_url="http://browser.test",
        sut_url="http://host:8004",
        testid="board",
        timeout_s=10.0,
        client=client,
    )
    assert ok is True


def test_returns_false_after_timeout(monkeypatch, capsys):
    """When the testid never becomes visible, the probe returns ``False``
    rather than raising — the runner is supposed to proceed to grading,
    which will produce real failures if the SUT genuinely isn't rendering
    the testid. We also expect a warning printed so the operator sees why
    grading is firing against a never-rendered SUT."""
    monkeypatch.setattr("pagehub_benchmarks.runner.workspace.time.sleep", lambda _s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/sessions":
            return httpx.Response(201, json={"id": "s"})
        if request.method == "POST" and request.url.path.endswith("/navigate"):
            return httpx.Response(200, json={"ok": True})
        if request.method == "POST" and request.url.path.endswith("/wait-for"):
            return httpx.Response(404, json={"error": "still not there"})
        return httpx.Response(204)

    client = _make_client(handler)
    ok = wait_for_dom_ready(
        browser_base_url="http://browser.test",
        sut_url="http://host:8004",
        testid="board",
        # Tiny timeout so the polling loop exits almost immediately.
        timeout_s=0.05,
        client=client,
    )
    assert ok is False
    out = capsys.readouterr().out
    assert "DOM readiness probe timed out" in out
    assert "testid='board'" in out


def test_session_id_missing_in_response_is_handled(monkeypatch, capsys):
    monkeypatch.setattr("pagehub_benchmarks.runner.workspace.time.sleep", lambda _s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/sessions":
            return httpx.Response(201, json={})  # no id field
        return httpx.Response(500, json={"error": "shouldn't get here"})

    client = _make_client(handler)
    ok = wait_for_dom_ready(
        browser_base_url="http://browser.test",
        sut_url="http://host:8004",
        testid="board",
        timeout_s=0.05,
        client=client,
    )
    assert ok is False  # times out instead of raising


def test_browser_connection_error_is_retried_not_raised(monkeypatch):
    """A flaky browser (5xx on session create) gets retried within the
    outer timeout budget, not surfaced as an exception that aborts the run.
    Verified by scripting: first session POST 503, then OK."""
    monkeypatch.setattr("pagehub_benchmarks.runner.workspace.time.sleep", lambda _s: None)

    session_responses = iter(
        [
            httpx.Response(503, json={"error": "browser warming up"}),
            httpx.Response(201, json={"id": "s2"}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/sessions":
            return next(session_responses)
        if request.method == "POST" and request.url.path.endswith("/navigate"):
            return httpx.Response(200, json={"ok": True})
        if request.method == "POST" and request.url.path.endswith("/wait-for"):
            return httpx.Response(200, json={"message": "visible"})
        return httpx.Response(204)

    client = _make_client(handler)
    ok = wait_for_dom_ready(
        browser_base_url="http://browser.test",
        sut_url="http://host:8004",
        testid="board",
        timeout_s=10.0,
        client=client,
    )
    assert ok is True
