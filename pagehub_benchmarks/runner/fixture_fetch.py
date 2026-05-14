"""Fetch a grader fixture bundle over HTTP from pagehub-evals.

PR A (pagehub-evals) added ``GET /v1/fixtures/{name}`` which returns the raw
on-disk bytes of ``fixtures/<name>.json``. pagehub-benchmarks calls it at
prompt-render time so the build prompt's ``{{ grader_fixture }}`` substitution
sees the same bundle the grader will run.

Why not read from a local pagehub-evals clone? Because in production the
benchmark runner and pagehub-evals are deployed independently — the local
clone is a dev convenience, not a contract. The grader client already speaks
HTTP to ``evals_base_url``; the fixture fetch reuses that channel and that
auth context (operator JWT — same as fixture-import).

Bytes are pretty-printed (``json.loads`` + ``json.dumps(indent=2,
sort_keys=False)``) **before** substitution into the prompt, so the harness
sees readable JSON instead of a one-line blob. This is intentional: the
on-disk fixture is already authored with indentation, but pretty-printing
the fetched bytes ourselves keeps the prompt stable even if the server's
response formatting drifts.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

import httpx

from pagehub_benchmarks.config import BenchmarkSpec, ConfigError
from pagehub_benchmarks.grader.client import _resolve_token


class FixtureFetchError(ConfigError):
    """The fixture endpoint was unreachable, refused us, or returned non-JSON."""


class FixtureFetcher(Protocol):
    """Used by the prompt renderer; ``HTTPFixtureFetcher`` is the real impl,
    tests inject a fake."""

    def fetch(self, spec: BenchmarkSpec) -> str:
        """Return the fixture body as a pretty-printed JSON string."""
        ...


def _fixture_name(spec: BenchmarkSpec) -> str:
    """The path-param ``name`` is the basename of ``grader.fixture_bundle``
    minus extension. E.g. ``fixtures/eval-chess-frontend.json`` → ``eval-chess-frontend``.
    """
    from pathlib import PurePosixPath

    return PurePosixPath(spec.grader.fixture_bundle).stem


def _fixture_url(spec: BenchmarkSpec) -> str:
    base = spec.grader.evals_base_url.rstrip("/")
    return f"{base}/v1/fixtures/{_fixture_name(spec)}"


def _pretty_print(raw: str, *, url: str) -> str:
    """Round-trip the bytes through ``json.loads`` + ``json.dumps`` so the
    prompt sees stable, readable JSON. Raises if the bytes don't parse."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FixtureFetchError(
            f"fixture at {url} did not return valid JSON: {exc}"
        ) from exc
    return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False)


class HTTPFixtureFetcher:
    """Fetch via ``GET /v1/fixtures/{name}`` using the grader's auth token.

    Reuses :func:`pagehub_benchmarks.grader.client._resolve_token` so we read
    ``PAGEHUB_EVALS_TOKEN`` first (real instance) and mint a dev HS256 token
    otherwise — same envelope as ``EvalsGrader``, no per-feature auth wiring.
    """

    def __init__(self, *, token: str | None = None, timeout_seconds: float = 10.0) -> None:
        self._token = token or _resolve_token()
        self._timeout = timeout_seconds

    def fetch(self, spec: BenchmarkSpec) -> str:
        url = _fixture_url(spec)
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            resp = httpx.get(url, headers=headers, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise FixtureFetchError(
                f"could not reach {url}: {type(exc).__name__}: {exc} — "
                f"is pagehub-evals running at {spec.grader.evals_base_url}?"
            ) from exc
        if resp.status_code == 404:
            raise FixtureFetchError(
                f"fixture {_fixture_name(spec)!r} not found at {url} — "
                f"is pagehub-evals on a version that supports the fixtures endpoint? "
                f"(needs PR #9 on pagehub-evals; or check fixtures/<name>.json exists in the evals repo)"
            )
        if resp.status_code >= 400:
            raise FixtureFetchError(
                f"GET {url} -> {resp.status_code}: {resp.text[:500]}"
            )
        return _pretty_print(resp.text, url=url)


# --------------------------------------------------------------------------
# helpers for tests + the CLI subcommand


def fixture_fetcher_from_env() -> FixtureFetcher:
    """Real impl unless ``PAGEHUB_BENCHMARKS_DISABLE_FIXTURE_FETCH`` is set
    (in which case a placeholder is substituted — used only by ``--dry-run``).
    """
    if os.environ.get("PAGEHUB_BENCHMARKS_DISABLE_FIXTURE_FETCH"):
        return _DisabledFetcher()
    return HTTPFixtureFetcher()


class _DisabledFetcher:
    """A stand-in for ``--dry-run`` / smoke checks without a live pagehub-evals."""

    def fetch(self, spec: BenchmarkSpec) -> str:
        return f'{{"_disabled": "set PAGEHUB_BENCHMARKS_DISABLE_FIXTURE_FETCH=0 to fetch {_fixture_url(spec)!r}"}}'


__all__ = [
    "FixtureFetchError",
    "FixtureFetcher",
    "HTTPFixtureFetcher",
    "fixture_fetcher_from_env",
]
