"""Grader client: verdict/evidence projection + the HTTP flow (via httpx MockTransport)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from pagehub_benchmarks.grader.client import EvalsGrader, GraderError

_PASSING_RUN = {
    "id": "run-1",
    "status": "passed",
    "verdict": "passed",
    "evidence": {"requests": [{"request_name": "r0", "method": "GET", "url": "http://x/health",
                               "evaluations": [{"name": "ok", "kind": "status_eq", "passed": True}],
                               "passed": True}]},
}

_FAILING_RUN = {
    "id": "run-2",
    "status": "failed",
    "verdict": "failed",
    "evidence": {
        "requests": [
            {
                "request_name": "make-illegal-move",
                "method": "POST",
                "url": "http://x/games/g1/moves",
                "transport_error": None,
                "substitution_missed": [],
                "evaluations": [
                    {"name": "rejected", "kind": "json_path_eq", "passed": True, "detail": {}},
                    {"name": "board-unchanged", "kind": "json_path_eq", "passed": False,
                     "detail": {"path": "$.fen", "expected": "FEN_A", "observed": "FEN_B"}},
                ],
                "passed": False,
            },
            {
                "request_name": "create-game",
                "method": "POST",
                "url": "http://x/games",
                "transport_error": "ConnectError: refused",
                "substitution_missed": ["GAME_ID"],
                "evaluations": [],
                "passed": False,
            },
        ],
        "engine_error": None,
    },
}


def test_to_result_passing():
    res = EvalsGrader._to_result(_PASSING_RUN)
    assert res.passed is True
    assert res.failures == []
    assert res.verdict == "passed"
    assert res.run_id == "run-1"


def test_to_result_failing_collects_human_readable_failures():
    res = EvalsGrader._to_result(_FAILING_RUN)
    assert res.passed is False
    assert res.verdict == "failed"
    joined = "\n".join(res.failures)
    assert "board-unchanged" in joined
    assert "FEN_A" in joined and "FEN_B" in joined
    assert "transport error" in joined and "ConnectError" in joined
    assert "unresolved template vars" in joined and "GAME_ID" in joined
    # the passing eval is not reported
    assert "rejected" not in joined


def test_to_result_failed_with_no_detail_still_reports_something():
    res = EvalsGrader._to_result({"id": "r", "status": "error", "verdict": "error", "evidence": {}})
    assert res.passed is False
    assert res.failures and "verdict='error'" in res.failures[0]


def test_setup_and_grade_http_flow(tmp_path: Path):
    bundle = {
        "version": 1,
        "environments": [{"name": "chess-local", "variables": {"eval-chess-backend_url": "http://localhost:8003"}, "secrets": {}}],
        "requests": [{"name": "r", "method": "GET", "url": "{{eval-chess-backend_url}}/health",
                      "evaluations": [{"name": "ok", "kind": "status_eq", "config": {"expected": 200}}]}],
        "collections": [{"name": "eval-chess-backend", "items": ["r"]}],
    }
    bundle_path = tmp_path / "eval-chess-backend.json"
    bundle_path.write_text(json.dumps(bundle))

    seen: dict[str, object] = {"imported": False, "patched": None, "run_created": None, "polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        if request.method == "POST" and url.path == "/v1/fixtures/import":
            seen["imported"] = json.loads(request.content)
            return httpx.Response(200, json={"requests": {"created": 1, "updated": 0}})
        if request.method == "GET" and url.path == "/v1/collections":
            return httpx.Response(200, json={"items": [{"id": "col-9", "name": "eval-chess-backend"}]})
        if request.method == "GET" and url.path == "/v1/environments":
            return httpx.Response(200, json={"items": [{"id": "env-9", "name": "chess-local", "variables": {"eval-chess-backend_url": "http://localhost:8003"}}]})
        if request.method == "PATCH" and url.path == "/v1/environments/env-9":
            seen["patched"] = json.loads(request.content)
            return httpx.Response(200, json={"id": "env-9", "name": "chess-local", "variables": json.loads(request.content)["variables"]})
        if request.method == "POST" and url.path == "/v1/runs":
            seen["run_created"] = json.loads(request.content)
            return httpx.Response(202, json={"id": "run-77", "status": "pending"})
        if request.method == "GET" and url.path == "/v1/runs/run-77":
            seen["polls"] = int(seen["polls"]) + 1  # type: ignore[arg-type]
            if int(seen["polls"]) < 2:
                return httpx.Response(200, json={"id": "run-77", "status": "running", "evidence": {}})
            return httpx.Response(200, json=_PASSING_RUN | {"id": "run-77"})
        return httpx.Response(404, json={"detail": f"unexpected {request.method} {url.path}"})

    client = httpx.Client(base_url="http://evals.test", transport=httpx.MockTransport(handler))
    grader = EvalsGrader(
        "http://evals.test",
        bundle_path,
        "eval-chess-backend",
        {"eval-chess-backend_url": "http://localhost:18003"},
        token="test-token",
        poll_interval_seconds=0,
        client=client,
    )
    grader.setup()
    assert seen["imported"] == bundle
    assert grader._collection_id == "col-9"
    assert grader._environment_id == "env-9"
    # env vars overlaid (override wins)
    assert seen["patched"]["variables"]["eval-chess-backend_url"] == "http://localhost:18003"

    res = grader.grade()
    assert res.passed is True
    assert seen["run_created"] == {"collection_id": "col-9", "harness_id": "pagehub-benchmarks", "environment_id": "env-9"}
    assert int(seen["polls"]) >= 2


def test_grade_http_error_raises(tmp_path: Path):
    bundle_path = tmp_path / "b.json"
    bundle_path.write_text(json.dumps({"version": 1, "collections": [{"name": "c", "items": []}]}))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    client = httpx.Client(base_url="http://evals.test", transport=httpx.MockTransport(handler))
    grader = EvalsGrader("http://evals.test", bundle_path, "c", {}, token="t", client=client)
    with pytest.raises(GraderError):
        grader.setup()
