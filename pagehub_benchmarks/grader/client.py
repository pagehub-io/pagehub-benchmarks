"""pagehub-evals grader client.

Given an evals base URL, a fixture-bundle file, a collection name, and a dict
of extra environment variables the run needs, this:

1. ``setup()`` — POST ``/v1/fixtures/import`` with the bundle (idempotent),
   then resolve the collection id (by name) and the environment id (the first
   environment the bundle declares), and PATCH that environment so its
   variables include the extra ``env`` dict (overlay; e.g. point a dependency
   URL at a worktree-local deployment).
2. ``grade()`` — POST ``/v1/runs`` for that collection+environment, poll
   ``GET /v1/runs/{id}`` until the run is terminal, and project the verdict +
   per-evaluation evidence into a :class:`GraderResult`. The ``failures`` list
   is human-readable and is what the runner feeds back into the harness on a
   retry.

Endpoints/shapes follow the pagehub-evals API (``api/fixtures``, ``api/runs``,
``api/collections``, ``api/environments``): runs are created with
``{collection_id, environment_id}`` and reach ``status`` ∈
{passed, failed, error} with a ``verdict`` and an ``evidence.requests[]`` list.

**Auth.** Fixture import and resource listing are operator-only (a user JWT).
Provide one via ``PAGEHUB_EVALS_TOKEN`` (raw bearer). If unset, a dev HS256
token is minted from ``PAGEHUB_EVALS_JWT_SIGNING_KEY`` (``kid:secret``,
default ``dev-kid:local-dev-jwt-signing-key-DO-NOT-USE-IN-PROD`` — matches
pagehub-evals' ``docker-compose.yml``), with issuer
``PAGEHUB_EVALS_JWT_ISSUER`` (default ``http://localhost:8080``), ``app_slug``
``pagehub-evals``, and ``email`` ``PAGEHUB_EVALS_OPERATOR_EMAIL`` (default
``support@pagehub.io``). This only works against a dev pagehub-evals; point at
a real instance by setting ``PAGEHUB_EVALS_TOKEN``.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

_TERMINAL_STATUSES = {"passed", "failed", "error"}
_ONE_HOUR = 3600


class GraderError(RuntimeError):
    """The grader could not produce a verdict (transport / auth / timeout)."""


@dataclass
class GraderResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    run_id: str | None = None
    verdict: str | None = None
    status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# auth


def _mint_dev_token() -> str:
    import jwt as pyjwt  # local import: only needed when no PAGEHUB_EVALS_TOKEN

    raw_key = os.environ.get(
        "PAGEHUB_EVALS_JWT_SIGNING_KEY",
        "dev-kid:local-dev-jwt-signing-key-DO-NOT-USE-IN-PROD",
    ).strip()
    if ":" not in raw_key:
        raise GraderError(
            "PAGEHUB_EVALS_JWT_SIGNING_KEY must be 'kid:secret'"
        )
    kid, secret = raw_key.split(":", 1)
    issuer = os.environ.get("PAGEHUB_EVALS_JWT_ISSUER", "http://localhost:8080").strip()
    email = os.environ.get("PAGEHUB_EVALS_OPERATOR_EMAIL", "support@pagehub.io").strip()
    now = int(time.time())
    payload = {
        # Stable subject so re-imports of the same bundle are idempotent
        # (fixture import keys "existing" rows on owner+name) and don't
        # accumulate copies in the dev DB across runs.
        "sub": str(uuid.uuid5(uuid.NAMESPACE_DNS, "pagehub-benchmarks-grader")),
        "app_slug": "pagehub-evals",
        "email": email,
        "iss": issuer,
        "iat": now,
        "exp": now + _ONE_HOUR,
    }
    return pyjwt.encode(payload, secret.strip(), algorithm="HS256", headers={"kid": kid.strip()})


def _resolve_token() -> str:
    token = os.environ.get("PAGEHUB_EVALS_TOKEN", "").strip()
    return token or _mint_dev_token()


# --------------------------------------------------------------------------
# grader


class EvalsGrader:
    def __init__(
        self,
        base_url: str,
        fixture_bundle_path: str | Path,
        collection_name: str,
        env_vars: dict[str, str] | None = None,
        *,
        token: str | None = None,
        poll_timeout_seconds: float = 180.0,
        poll_interval_seconds: float = 2.0,
        http_timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.fixture_bundle_path = Path(fixture_bundle_path)
        self.collection_name = collection_name
        self.env_vars = dict(env_vars or {})
        self.poll_timeout_seconds = poll_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._token = token or _resolve_token()
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url, timeout=http_timeout_seconds
        )
        self._collection_id: str | None = None
        self._environment_id: str | None = None

    # -- lifecycle ------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> EvalsGrader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- http -----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            resp = self._client.request(method, path, headers=self._headers(), **kw)
        except httpx.HTTPError as exc:
            raise GraderError(f"{method} {path} failed: {type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 400:
            raise GraderError(
                f"{method} {path} -> {resp.status_code}: {resp.text[:1000]}"
            )
        return resp

    # -- setup ----------------------------------------------------------

    def _load_bundle(self) -> dict[str, Any]:
        if not self.fixture_bundle_path.is_file():
            raise GraderError(f"fixture bundle not found: {self.fixture_bundle_path}")
        try:
            return json.loads(self.fixture_bundle_path.read_text())
        except json.JSONDecodeError as exc:
            raise GraderError(f"fixture bundle is not valid JSON: {exc}") from exc

    def setup(self) -> None:
        """Import the fixture bundle and resolve collection + environment ids."""
        bundle = self._load_bundle()
        self._request("POST", "/v1/fixtures/import", json=bundle)

        cols = self._request("GET", "/v1/collections").json().get("items", [])
        match = next((c for c in cols if c["name"] == self.collection_name), None)
        if match is None:
            raise GraderError(
                f"collection {self.collection_name!r} not found after import "
                f"(have: {sorted(c['name'] for c in cols)})"
            )
        self._collection_id = match["id"]

        bundle_env_names = [e["name"] for e in bundle.get("environments", []) or []]
        if bundle_env_names:
            envs = self._request("GET", "/v1/environments").json().get("items", [])
            target = next((e for e in envs if e["name"] == bundle_env_names[0]), None)
            if target is None:
                raise GraderError(
                    f"environment {bundle_env_names[0]!r} not found after import"
                )
            self._environment_id = target["id"]
            if self.env_vars:
                merged = {**(target.get("variables") or {}), **self.env_vars}
                self._request(
                    "PATCH",
                    f"/v1/environments/{target['id']}",
                    json={"variables": merged},
                )
        else:
            self._environment_id = None

    # -- grade ----------------------------------------------------------

    def grade(self) -> GraderResult:
        """Create a run of the collection, poll to terminal, return the verdict."""
        if self._collection_id is None:
            self.setup()
        body: dict[str, Any] = {
            "collection_id": self._collection_id,
            "harness_id": "pagehub-benchmarks",
        }
        if self._environment_id is not None:
            body["environment_id"] = self._environment_id
        run = self._request("POST", "/v1/runs", json=body).json()
        run_id = run["id"]

        deadline = time.monotonic() + self.poll_timeout_seconds
        while True:
            run = self._request("GET", f"/v1/runs/{run_id}").json()
            if run.get("status") in _TERMINAL_STATUSES:
                return self._to_result(run)
            if time.monotonic() >= deadline:
                raise GraderError(
                    f"run {run_id} did not reach a terminal status within "
                    f"{self.poll_timeout_seconds}s (last status={run.get('status')})"
                )
            time.sleep(self.poll_interval_seconds)

    @staticmethod
    def _to_result(run: dict[str, Any]) -> GraderResult:
        verdict = run.get("verdict")
        status = run.get("status")
        passed = verdict == "passed"
        failures: list[str] = []
        evidence = run.get("evidence") or {}
        for req in evidence.get("requests", []) or []:
            label = f"{req.get('request_name', '?')} ({req.get('method', '?')} {req.get('url', '?')})"
            if req.get("transport_error"):
                failures.append(f"{label}: transport error: {req['transport_error']}")
            if req.get("substitution_missed"):
                failures.append(
                    f"{label}: unresolved template vars: {req['substitution_missed']}"
                )
            for ev in req.get("evaluations", []) or []:
                if ev.get("passed"):
                    continue
                detail = ev.get("detail") or {}
                msg = (
                    f"{req.get('request_name', '?')} :: {ev.get('name', '?')} "
                    f"[{ev.get('kind', '?')}] failed: {json.dumps(detail, default=str)}"
                )
                if ev.get("error"):
                    msg += f" (error: {ev['error']})"
                failures.append(msg)
        if evidence.get("engine_error"):
            failures.append(f"engine error: {evidence['engine_error']}")
        if not passed and not failures:
            failures.append(
                f"run verdict={verdict!r} status={status!r} with no per-evaluation detail"
            )
        return GraderResult(
            passed=passed,
            failures=failures,
            run_id=run.get("id"),
            verdict=verdict,
            status=status,
            raw=run,
        )
