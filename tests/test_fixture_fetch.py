"""HTTP fixture-fetch: URL shape, auth header, error mapping, pretty-print."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from pagehub_benchmarks.config import parse_benchmark
from pagehub_benchmarks.runner.fixture_fetch import (
    FixtureFetchError,
    HTTPFixtureFetcher,
    _fixture_url,
)


def _spec(tmp_path: Path, *, evals_base_url="http://evals.test", fixture_bundle="fixtures/eval-chess-frontend.json"):
    prompt = tmp_path / "prompts" / "demo.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("placeholder")
    data = {
        "name": "demo",
        "target_repo": "git@github.com:example/demo.git",
        "build_prompt_file": str(prompt),
        "grader": {
            "evals_base_url": evals_base_url,
            "fixture_bundle": fixture_bundle,
            "collection": "demo",
            "env": {"demo_url": "http://localhost:9999"},
        },
        "harnesses": [{"harness": "fake", "model": "test-model"}],
    }
    return parse_benchmark(data, tmp_path / "demo.yaml")


def test_url_is_evals_base_slash_v1_fixtures_slash_stem(tmp_path):
    spec = _spec(tmp_path, fixture_bundle="fixtures/eval-chess-frontend.json")
    assert _fixture_url(spec) == "http://evals.test/v1/fixtures/eval-chess-frontend"


def test_url_handles_trailing_slash_on_base_url(tmp_path):
    spec = _spec(tmp_path, evals_base_url="http://evals.test/")
    assert _fixture_url(spec) == "http://evals.test/v1/fixtures/eval-chess-frontend"


def test_fetch_happy_path_pretty_prints_and_sends_auth(tmp_path):
    spec = _spec(tmp_path)
    seen_request: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_request["url"] = str(request.url)
        seen_request["auth"] = request.headers.get("authorization", "")
        body = '{"version":1,"collections":[{"name":"x","items":["a","b"]}]}'
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    transport = httpx.MockTransport(handler)
    fetcher = HTTPFixtureFetcher(token="fake-token")
    # Patch the module-level httpx.get used by HTTPFixtureFetcher.
    import pagehub_benchmarks.runner.fixture_fetch as fetch_mod
    real_get = httpx.get
    fetch_mod.httpx = httpx  # ensure attribute lookup goes through the module
    def _fake_get(url, headers=None, timeout=None):
        return httpx.Client(transport=transport).get(url, headers=headers, timeout=timeout)
    fetch_mod.httpx.get = _fake_get
    try:
        body = fetcher.fetch(spec)
    finally:
        fetch_mod.httpx.get = real_get

    assert seen_request["url"] == "http://evals.test/v1/fixtures/eval-chess-frontend"
    assert seen_request["auth"] == "Bearer fake-token"
    # Pretty-printed (indent=2, sort_keys=False — order preserved).
    parsed = json.loads(body)
    assert parsed["version"] == 1
    assert "\n" in body
    assert body.startswith("{\n")
    # sort_keys=False preserves declaration order.
    first_key_line = body.splitlines()[1].strip()
    assert first_key_line.startswith('"version"')


def test_fetch_404_raises_clear_error(tmp_path):
    spec = _spec(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    fetcher = HTTPFixtureFetcher(token="t")
    import pagehub_benchmarks.runner.fixture_fetch as fetch_mod
    real_get = httpx.get
    def _fake_get(url, headers=None, timeout=None):
        return httpx.Client(transport=transport).get(url, headers=headers, timeout=timeout)
    fetch_mod.httpx.get = _fake_get
    try:
        with pytest.raises(FixtureFetchError) as exc:
            fetcher.fetch(spec)
    finally:
        fetch_mod.httpx.get = real_get

    msg = str(exc.value)
    assert "eval-chess-frontend" in msg
    assert "404" in msg or "not found" in msg


def test_fetch_500_raises_with_status_and_body(tmp_path):
    spec = _spec(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="kaboom")

    transport = httpx.MockTransport(handler)
    fetcher = HTTPFixtureFetcher(token="t")
    import pagehub_benchmarks.runner.fixture_fetch as fetch_mod
    real_get = httpx.get
    def _fake_get(url, headers=None, timeout=None):
        return httpx.Client(transport=transport).get(url, headers=headers, timeout=timeout)
    fetch_mod.httpx.get = _fake_get
    try:
        with pytest.raises(FixtureFetchError) as exc:
            fetcher.fetch(spec)
    finally:
        fetch_mod.httpx.get = real_get

    assert "500" in str(exc.value)
    assert "kaboom" in str(exc.value)


def test_fetch_transport_error_mentions_base_url(tmp_path):
    spec = _spec(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)
    fetcher = HTTPFixtureFetcher(token="t")
    import pagehub_benchmarks.runner.fixture_fetch as fetch_mod
    real_get = httpx.get
    def _fake_get(url, headers=None, timeout=None):
        return httpx.Client(transport=transport).get(url, headers=headers, timeout=timeout)
    fetch_mod.httpx.get = _fake_get
    try:
        with pytest.raises(FixtureFetchError) as exc:
            fetcher.fetch(spec)
    finally:
        fetch_mod.httpx.get = real_get

    msg = str(exc.value)
    assert "evals.test" in msg  # base URL surfaces in the error so debugging is easy


def test_fetch_invalid_json_response_raises(tmp_path):
    spec = _spec(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json!")

    transport = httpx.MockTransport(handler)
    fetcher = HTTPFixtureFetcher(token="t")
    import pagehub_benchmarks.runner.fixture_fetch as fetch_mod
    real_get = httpx.get
    def _fake_get(url, headers=None, timeout=None):
        return httpx.Client(transport=transport).get(url, headers=headers, timeout=timeout)
    fetch_mod.httpx.get = _fake_get
    try:
        with pytest.raises(FixtureFetchError) as exc:
            fetcher.fetch(spec)
    finally:
        fetch_mod.httpx.get = real_get

    assert "valid JSON" in str(exc.value)
