"""Orchestration: for each (harness, model, config) in a benchmark's matrix,
drive build -> grade -> (on failure, re-invoke with the failures) -> stop when
green or attempts exhausted, then write the run record.

``execute_benchmark_run`` is the pure core (harness, grader, and worktree are
injected — that is what the unit tests exercise with fakes). ``run_benchmark``
is the CLI-facing wrapper: it loads the spec, prepares worktrees, constructs
the real :class:`ClaudeCodeHarness` + :class:`EvalsGrader`, and persists
results. ``dry_run_report`` sanity-checks YAML + prompt + grader wiring +
pricing without calling the harness or pagehub-evals.
"""

from __future__ import annotations

import dataclasses
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pagehub_benchmarks.config import (
    REPO_ROOT,
    BenchmarkSpec,
    ConfigError,
    HarnessSpec,
    ModelPrice,
    load_benchmark,
    load_pricing,
)
from pagehub_benchmarks.grader import EvalsGrader, GraderResult
from pagehub_benchmarks.harnesses import HARNESSES, Harness, get_harness
from pagehub_benchmarks.runner.fixture_fetch import (
    FixtureFetcher,
    fixture_fetcher_from_env,
)
from pagehub_benchmarks.runner.pricing import cost_usd
from pagehub_benchmarks.runner.prompt_render import RenderedPrompt, render_prompt
from pagehub_benchmarks.runner.push import GitPusher, Pusher, branch_for
from pagehub_benchmarks.runner.results import AttemptRecord, RunRecord, config_slug
from pagehub_benchmarks.runner.workspace import (
    capture_built_sha,
    prepare_worktree,
    run_service,
    wait_for_dom_ready,
)

DEFAULT_RESULTS_DIR = REPO_ROOT / "results"
DEFAULT_WORKTREES_DIR = REPO_ROOT / ".worktrees"


# --------------------------------------------------------------------------
# protocols (so tests can inject lightweight fakes)


class GraderLike(Protocol):
    def setup(self) -> None: ...
    def grade(self) -> GraderResult: ...


# --------------------------------------------------------------------------
# helpers


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def build_followup_prompt(failures: list[str]) -> str:
    """The retry prompt: the failing-eval output + 'fix it, get tests passing'."""
    if failures:
        bullets = "\n".join(f"- {f}" for f in failures)
        head = f"The conformance evals are still failing:\n\n{bullets}\n\n"
    else:
        head = "The conformance evals are still failing (no per-evaluation detail was reported).\n\n"
    return head + (
        "Fix the code so every eval passes. Build it, get the tests passing — "
        "that is all."
    )


def _local_probe_url(url: str) -> str:
    """``host.docker.internal`` reaches the host *from a container*; from the
    host itself it resolves elsewhere — probe localhost instead."""
    return url.replace("host.docker.internal", "localhost").replace("host.containers.internal", "localhost")


_BROWSER_ENV_KEY = "pagehub-browser_url"


def _health_url(spec: BenchmarkSpec) -> str | None:
    """Best-effort: derive a (host-side) health URL from a ``*_url`` entry in grader.env."""
    for value in spec.grader.env.values():
        v = str(value).rstrip("/")
        if v.startswith(("http://", "https://")):
            return f"{_local_probe_url(v)}/health"
    return None


def _sut_url_for_browser(spec: BenchmarkSpec) -> str | None:
    """The SUT URL as pagehub-browser (running inside its own container)
    should navigate to it — i.e. ``host.docker.internal`` preserved. Picks
    the first ``*_url`` entry in ``grader.env`` whose key is NOT
    ``pagehub-browser_url`` (that one points at the browser itself, not the
    SUT). Returns ``None`` if no such entry exists."""
    for key, value in spec.grader.env.items():
        if key == _BROWSER_ENV_KEY:
            continue
        v = str(value).rstrip("/")
        if v.startswith(("http://", "https://")):
            return v
    return None


def _browser_base_url_from_host(spec: BenchmarkSpec) -> str | None:
    """The pagehub-browser base URL as the *runner* (on the host) sees it —
    ``host.docker.internal`` → ``localhost``. Returns ``None`` if the YAML
    doesn't declare a ``pagehub-browser_url`` entry."""
    value = spec.grader.env.get(_BROWSER_ENV_KEY)
    if not value:
        return None
    v = str(value).rstrip("/")
    if not v.startswith(("http://", "https://")):
        return None
    return _local_probe_url(v)


_ADMIN_TOKEN_ENV = "PAGEHUB_BROWSER_ADMIN_TOKEN"
_RESET_SESSIONS_PATH = "/v1/admin/reset-sessions"


def reset_pagehub_browser_sessions(
    browser_base_url: str | None,
    *,
    reason: str,
    admin_token: str | None,
    timeout_s: float = 10.0,
) -> bool:
    """POST ``/v1/admin/reset-sessions`` so the next attempt starts with a
    clean session table — the brittle 503 capacity cascade we saw on the
    May 22 chess-frontend sweep was pagehub-browser sitting at MAX_SESSIONS
    after the eval-game-hoppers run leaked sessions (PR #20 DOM probe bug).

    Best-effort: any failure (no URL, no token, 401/404, network error)
    logs a warning and returns ``False``. A regressed pagehub-browser
    deploy that doesn't yet carry the admin endpoint must NOT brick the
    benchmark — that would be a worse regression than the 503 cascade
    we're trying to prevent.
    """
    if not browser_base_url:
        return False
    if not admin_token:
        print(
            f"(reset-sessions skipped: {_ADMIN_TOKEN_ENV} not set in the runner "
            f"environment; the per-attempt session purge is a no-op)"
        )
        return False
    url = browser_base_url.rstrip("/") + _RESET_SESSIONS_PATH
    payload = json.dumps({"reason": reason}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        # 404 — endpoint missing (pagehub-browser predates the admin route).
        # 401 — admin token mismatch.
        # Either way: log + carry on; do not fail the run.
        print(
            f"(reset-sessions: HTTP {exc.code} from {url} — continuing without "
            f"reset; benchmark proceeds)"
        )
        return False
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(
            f"(reset-sessions: network error talking to {url} "
            f"({type(exc).__name__}: {exc}) — continuing without reset)"
        )
        return False
    try:
        data = json.loads(body)
        closed = int(data.get("closed", 0))
    except (ValueError, TypeError):
        closed = 0
    print(f"reset-sessions: closed {closed} pagehub-browser session(s) (reason={reason!r})")
    return True


def _build_dom_ready_probe(spec: BenchmarkSpec) -> Callable[[], bool] | None:
    """Construct the per-attempt DOM readiness probe, or ``None`` if the
    benchmark's grader spec doesn't declare ``ready_testid`` (preserving
    the legacy HTTP-only health probe behavior for older benchmarks)."""
    testid = spec.grader.ready_testid
    if not testid:
        return None
    browser_url = _browser_base_url_from_host(spec)
    sut_url = _sut_url_for_browser(spec)
    if not (browser_url and sut_url):
        # ready_testid set but we can't resolve URLs — silently fall back to
        # the HTTP-only probe rather than failing the run. Warn so it's not
        # silent.
        print(
            f"(warning: grader.ready_testid={testid!r} declared but couldn't "
            f"resolve browser+SUT URLs from grader.env; skipping DOM probe)"
        )
        return None
    timeout = spec.grader.ready_timeout_seconds
    return lambda: wait_for_dom_ready(
        browser_base_url=browser_url,
        sut_url=sut_url,
        testid=testid,
        timeout_s=timeout,
    )


def _gateway_url_from(harness_spec: HarnessSpec) -> str | None:
    """The gateway base URL declared in the harness's ``config.gateway.url``,
    or ``None`` when no gateway is configured."""
    gw = (harness_spec.config or {}).get("gateway")
    if not isinstance(gw, dict):
        return None
    url = str(gw.get("url") or "").strip().rstrip("/")
    return url or None


def _probe_provider_model(gateway_url: str | None) -> str | None:
    """Best-effort: GET ``<gateway_url>/health`` and return its
    ``default_model`` field — the id the gateway will translate to on its
    upstream call. Returns ``None`` when no gateway is in play or any step
    fails; this is a documentation aid, not a correctness signal.
    """
    base = (gateway_url or "").strip().rstrip("/")
    if not base:
        return None
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=2) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    value = data.get("default_model") if isinstance(data, dict) else None
    return str(value) if value else None


# --------------------------------------------------------------------------
# the pure core


def execute_benchmark_run(
    *,
    spec: BenchmarkSpec,
    harness_spec: HarnessSpec,
    harness: Harness,
    grader: GraderLike,
    worktree_dir: str | Path,
    pricing: dict[str, ModelPrice],
    fixture_fetcher: FixtureFetcher,
    built_sha: str | None = None,
    service_factory: Callable[[], AbstractContextManager[Any]] | None = None,
    clock: Callable[[], datetime] = _utcnow,
    provider_model: str | None = None,
    reset_browser_sessions: Callable[[str], None] | None = None,
) -> RunRecord:
    model = harness_spec.model
    if model not in pricing:
        raise ConfigError(f"no pricing entry for model {model!r}")
    price = pricing[model]
    config = dict(harness_spec.config)
    rendered: RenderedPrompt = render_prompt(spec, fetcher=fixture_fetcher)
    if rendered.unused_vars:
        print(
            f"(prompt-render warning: declared template_vars not referenced "
            f"in prompts/{spec.name}.md: {rendered.unused_vars})"
        )
    prompt = rendered.text
    max_attempts = spec.max_attempts

    grader.setup()

    started_at = clock()
    per_attempt: list[AttemptRecord] = []
    total_in = total_out = total_cache_create = total_cache_read = 0
    total_wall = 0.0
    session_handle: str = ""
    passed = False
    last_failures: list[str] = []
    attempt_no = 0

    for attempt_no in range(1, max_attempts + 1):
        # Purge pagehub-browser's session table BEFORE each attempt. The
        # capacity cascade we saw on May 22 (chess-frontend matrix:
        # downstream 503s after eval-game-hoppers leaked sessions) is
        # entirely a "previous attempt's session leak filled the MAX_SESSIONS
        # registry" failure mode — clearing per-attempt closes the door on
        # carryover between attempts within a run AND between runs.
        if reset_browser_sessions is not None:
            reset_browser_sessions(
                f"{spec.name}/{harness_spec.harness}/attempt-{attempt_no}"
            )
        if attempt_no == 1:
            sent_prompt = prompt
            ar = harness.start_build(str(worktree_dir), sent_prompt, model, config)
        else:
            sent_prompt = build_followup_prompt(last_failures)
            ar = harness.continue_build(session_handle, sent_prompt)
        if ar.session_handle:
            session_handle = ar.session_handle

        total_in += ar.input_tokens
        total_out += ar.output_tokens
        total_cache_create += ar.cache_creation_tokens
        total_cache_read += ar.cache_read_tokens
        total_wall += ar.wall_time_seconds

        svc: AbstractContextManager[Any] = (
            service_factory() if service_factory is not None else nullcontext()
        )
        with svc:
            gr = grader.grade()

        per_attempt.append(
            AttemptRecord(
                attempt=attempt_no,
                input_tokens=ar.input_tokens,
                output_tokens=ar.output_tokens,
                cache_tokens=ar.cache_tokens,
                wall_time_seconds=round(ar.wall_time_seconds, 3),
                grader_passed=gr.passed,
                grader_failures=list(gr.failures),
                rendered_prompt=sent_prompt,
                raw=dict(ar.raw) if ar.raw else {},
            )
        )
        if gr.passed:
            passed = True
            break
        last_failures = list(gr.failures)

    finished_at = clock()
    cost = cost_usd(
        price,
        input_tokens=total_in,
        output_tokens=total_out,
        cache_creation_tokens=total_cache_create,
        cache_read_tokens=total_cache_read,
    )
    return RunRecord(
        benchmark=spec.name,
        harness=harness_spec.harness,
        model=model,
        config=config,
        started_at=_iso(started_at),
        finished_at=_iso(finished_at),
        target_repo=spec.target_repo,
        target_start=spec.target_start,
        built_git_sha=built_sha,
        worktree_path=str(worktree_dir),
        max_attempts=max_attempts,
        attempts=attempt_no,  # the attempt that went green, or the cap if never
        passed=passed,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_cache_tokens=total_cache_create + total_cache_read,
        cost_usd=cost,
        total_wall_time_seconds=round(total_wall, 3),
        per_attempt=per_attempt,
        rendered_prompt=prompt,
        template_vars=dict(rendered.template_vars),
        provider_model=provider_model,
    )


# --------------------------------------------------------------------------
# CLI-facing wrapper


def _select_harnesses(
    spec: BenchmarkSpec,
    harness: str | None,
    model: str | None,
    effort: str | None,
    config_overrides: dict[str, Any] | None,
) -> list[HarnessSpec]:
    out: list[HarnessSpec] = []
    for h in spec.harnesses:
        if harness is not None and h.harness != harness:
            continue
        if model is not None and h.model != model:
            continue
        # ``effort`` filters by the row's ``config.effort`` value. The matrix may
        # carry multiple rows for the same (harness, model) differing only in
        # effort (e.g. an Opus sweep at medium / high / xhigh); --effort picks
        # exactly one. Distinct from ``config_overrides``, which *mutates* the
        # selected rows' config.
        if effort is not None and (h.config or {}).get("effort") != effort:
            continue
        if config_overrides:
            h = dataclasses.replace(h, config={**h.config, **config_overrides})
        out.append(h)
    if not out:
        raise ConfigError(
            f"no harness in {spec.name!r} matched "
            f"harness={harness!r} model={model!r} effort={effort!r}"
        )
    return out


def _new_worktree_dir(spec: BenchmarkSpec, h: HarnessSpec, root: Path) -> Path:
    stamp = _utcnow().strftime("%Y%m%dT%H%M%SZ")
    slug = config_slug(h.config)
    return root / spec.name / f"{h.harness}__{h.model}__{slug}__{stamp}"


def run_benchmark(
    name_or_path: str,
    *,
    harness: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    config_overrides: dict[str, Any] | None = None,
    max_attempts: int | None = None,
    results_dir: str | Path | None = None,
    worktrees_dir: str | Path | None = None,
    serve: bool = True,
    build_site: bool = True,
    pusher: Pusher | None = None,
    fixture_fetcher: FixtureFetcher | None = None,
) -> list[Path]:
    spec = load_benchmark(name_or_path)
    if max_attempts is not None:
        spec = dataclasses.replace(spec, max_attempts=max_attempts)
    spec.read_prompt()  # fail fast on a missing/empty prompt

    pricing = load_pricing()
    selected = _select_harnesses(spec, harness, model, effort, config_overrides)
    for h in selected:
        if h.model not in pricing:
            raise ConfigError(
                f"pricing.yaml has no entry for model {h.model!r} (needed by {spec.name})"
            )

    results_root = Path(results_dir) if results_dir else DEFAULT_RESULTS_DIR
    worktrees_root = Path(worktrees_dir) if worktrees_dir else DEFAULT_WORKTREES_DIR
    pusher = pusher if pusher is not None else GitPusher()
    fetcher = fixture_fetcher if fixture_fetcher is not None else fixture_fetcher_from_env()

    written: list[Path] = []
    for h in selected:
        worktree = _new_worktree_dir(spec, h, worktrees_root)
        prepare_worktree(spec.target_repo, spec.target_start, worktree)
        harness_obj = get_harness(h.harness)
        with EvalsGrader(
            spec.grader.evals_base_url,
            spec.grader.fixture_bundle_path,
            spec.grader.collection,
            spec.grader.env,
        ) as grader:
            dom_ready = _build_dom_ready_probe(spec)
            service_factory = (
                (
                    lambda wt=worktree, dr=dom_ready: run_service(
                        wt, _health_url(spec), dom_ready_probe=dr
                    )
                )
                if serve
                else None
            )
            browser_base = _browser_base_url_from_host(spec)
            admin_token = os.environ.get(_ADMIN_TOKEN_ENV) or None

            def _reset(reason: str, _u=browser_base, _t=admin_token) -> None:
                reset_pagehub_browser_sessions(_u, reason=reason, admin_token=_t)

            record = execute_benchmark_run(
                spec=spec,
                harness_spec=h,
                harness=harness_obj,
                grader=grader,
                worktree_dir=worktree,
                pricing=pricing,
                fixture_fetcher=fetcher,
                service_factory=service_factory,
                provider_model=_probe_provider_model(_gateway_url_from(h)),
                reset_browser_sessions=_reset if browser_base else None,
            )
        record.built_git_sha = capture_built_sha(worktree)
        _push_built_tree(record, h, spec, worktree, pusher)
        path = record.write(results_root)
        written.append(path)
        print(
            f"[{spec.name}] {h.harness} {h.model} {config_slug(h.config)}: "
            f"{'PASS' if record.passed else 'FAIL'} in {record.attempts}/{record.max_attempts} "
            f"attempts, ${record.cost_usd:.4f}, {record.total_wall_time_seconds:.0f}s "
            f"-> {path}"
        )
    if build_site and written:
        _rebuild_site(results_root)
    return written


def _push_built_tree(
    record: RunRecord,
    h: HarnessSpec,
    spec: BenchmarkSpec,
    worktree: Path,
    pusher: Pusher,
) -> None:
    """Push every run (pass or fail). On a pass to an empty target, also push
    the default branch. The grader verdict is the source of truth — a push
    failure logs loudly but never fails the run."""
    started = datetime.fromisoformat(record.started_at.replace("Z", "+00:00"))
    branch = branch_for(
        harness=h.harness,
        model=h.model,
        config_slug=config_slug(h.config),
        when=started,
    )
    push_to_default = False
    if record.passed:
        try:
            push_to_default = pusher.is_target_empty(spec.target_repo)
        except Exception as exc:  # noqa: BLE001
            print(
                f"(warning: could not probe {spec.target_repo} for emptiness: "
                f"{type(exc).__name__}: {exc})"
            )
    try:
        pr = pusher.push(
            worktree=worktree,
            target_repo=spec.target_repo,
            branch=branch,
            push_to_default_branch=push_to_default,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"(push failed: {type(exc).__name__}: {exc})")
        record.push_error = f"{type(exc).__name__}: {exc}"
        return
    record.pushed_branch = pr.pushed_branch
    record.pushed_branch_url = pr.pushed_branch_url
    record.pushed_commit = pr.pushed_commit
    record.pushed_to_default_branch = pr.pushed_to_default_branch
    record.pushed_at = pr.pushed_at
    record.push_error = pr.error
    if pr.pushed_branch_url:
        flag = " (also -> default branch)" if pr.pushed_to_default_branch else ""
        print(f"pushed -> {pr.pushed_branch_url}{flag}")
    if pr.error:
        print(f"(push error: {pr.error})")


def _rebuild_site(results_dir: Path) -> None:
    """Regenerate docs/ from the run records (best-effort — never fails a run)."""
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from tools.build_site import build as build_site_fn
    except ImportError as exc:  # pragma: no cover - tooling not on path
        print(f"(skipping site rebuild: {exc})")
        return
    try:
        out = build_site_fn(results_dir=results_dir)
        print(f"site rebuilt -> {out}")
    except Exception as exc:  # noqa: BLE001  pragma: no cover
        print(f"(site rebuild failed: {type(exc).__name__}: {exc})")


# --------------------------------------------------------------------------
# dry run


def dry_run_report(spec: BenchmarkSpec) -> list[str]:
    """Validate YAML + prompt + grader wiring + pricing offline. Returns notes."""
    notes: list[str] = []
    notes.append(f"benchmark: {spec.name} — {spec.description or '(no description)'}")
    notes.append(f"target: {spec.target_repo} @ {spec.target_start}")

    prompt = spec.read_prompt()
    notes.append(f"prompt: {spec.build_prompt_path} ({len(prompt)} chars) OK")

    bundle_path = spec.grader.fixture_bundle_path
    if not bundle_path.is_file():
        raise ConfigError(
            f"grader.fixture_bundle not found: {bundle_path} "
            f"(set PAGEHUB_EVALS_REPO if your pagehub-evals checkout is elsewhere)"
        )
    try:
        bundle = json.loads(bundle_path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"grader.fixture_bundle is not valid JSON: {exc}") from exc
    if bundle.get("version") != 1:
        raise ConfigError(f"grader.fixture_bundle version must be 1, got {bundle.get('version')!r}")
    coll_names = {c.get("name") for c in bundle.get("collections", []) or []}
    if spec.grader.collection not in coll_names:
        raise ConfigError(
            f"grader.collection {spec.grader.collection!r} not declared in the bundle "
            f"(bundle has: {sorted(n for n in coll_names if n)})"
        )
    notes.append(
        f"grader: evals={spec.grader.evals_base_url} bundle={bundle_path} "
        f"collection={spec.grader.collection!r} env={spec.grader.env} OK"
    )

    pricing = load_pricing()
    for h in spec.harnesses:
        if h.harness not in HARNESSES:
            raise ConfigError(f"unknown harness {h.harness!r} in matrix")
        if h.model not in pricing:
            raise ConfigError(f"pricing.yaml has no entry for model {h.model!r}")
        notes.append(
            f"matrix: harness={h.harness} model={h.model} config={h.config} "
            f"price/MTok in={pricing[h.model].input} out={pricing[h.model].output} OK"
        )
    notes.append(f"max_attempts: {spec.max_attempts}")
    return notes


__all__ = [
    "execute_benchmark_run",
    "run_benchmark",
    "dry_run_report",
    "build_followup_prompt",
]
