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
from pagehub_benchmarks.runner.pricing import cost_usd
from pagehub_benchmarks.runner.push import GitPusher, Pusher, branch_for
from pagehub_benchmarks.runner.results import AttemptRecord, RunRecord, config_slug
from pagehub_benchmarks.runner.workspace import (
    capture_built_sha,
    prepare_worktree,
    run_service,
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


def _health_url(spec: BenchmarkSpec) -> str | None:
    """Best-effort: derive a (host-side) health URL from a ``*_url`` entry in grader.env."""
    for value in spec.grader.env.values():
        v = str(value).rstrip("/")
        if v.startswith(("http://", "https://")):
            return f"{_local_probe_url(v)}/health"
    return None


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
    built_sha: str | None = None,
    service_factory: Callable[[], AbstractContextManager[Any]] | None = None,
    clock: Callable[[], datetime] = _utcnow,
) -> RunRecord:
    model = harness_spec.model
    if model not in pricing:
        raise ConfigError(f"no pricing entry for model {model!r}")
    price = pricing[model]
    config = dict(harness_spec.config)
    prompt = spec.read_prompt()
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
        if attempt_no == 1:
            ar = harness.start_build(str(worktree_dir), prompt, model, config)
        else:
            ar = harness.continue_build(session_handle, build_followup_prompt(last_failures))
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
    )


# --------------------------------------------------------------------------
# CLI-facing wrapper


def _select_harnesses(
    spec: BenchmarkSpec,
    harness: str | None,
    model: str | None,
    config_overrides: dict[str, Any] | None,
) -> list[HarnessSpec]:
    out: list[HarnessSpec] = []
    for h in spec.harnesses:
        if harness is not None and h.harness != harness:
            continue
        if model is not None and h.model != model:
            continue
        if config_overrides:
            h = dataclasses.replace(h, config={**h.config, **config_overrides})
        out.append(h)
    if not out:
        raise ConfigError(
            f"no harness in {spec.name!r} matched harness={harness!r} model={model!r}"
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
    config_overrides: dict[str, Any] | None = None,
    max_attempts: int | None = None,
    results_dir: str | Path | None = None,
    worktrees_dir: str | Path | None = None,
    serve: bool = True,
    build_site: bool = True,
    pusher: Pusher | None = None,
) -> list[Path]:
    spec = load_benchmark(name_or_path)
    if max_attempts is not None:
        spec = dataclasses.replace(spec, max_attempts=max_attempts)
    spec.read_prompt()  # fail fast on a missing/empty prompt

    pricing = load_pricing()
    selected = _select_harnesses(spec, harness, model, config_overrides)
    for h in selected:
        if h.model not in pricing:
            raise ConfigError(
                f"pricing.yaml has no entry for model {h.model!r} (needed by {spec.name})"
            )

    results_root = Path(results_dir) if results_dir else DEFAULT_RESULTS_DIR
    worktrees_root = Path(worktrees_dir) if worktrees_dir else DEFAULT_WORKTREES_DIR
    pusher = pusher if pusher is not None else GitPusher()

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
            service_factory = (
                (lambda wt=worktree: run_service(wt, _health_url(spec))) if serve else None
            )
            record = execute_benchmark_run(
                spec=spec,
                harness_spec=h,
                harness=harness_obj,
                grader=grader,
                worktree_dir=worktree,
                pricing=pricing,
                service_factory=service_factory,
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
