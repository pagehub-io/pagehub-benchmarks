"""The build→grade→retry loop, exercised with FakeHarness + FakeGrader."""

from __future__ import annotations

import json

import pytest

from pagehub_benchmarks.runner.run import build_followup_prompt, execute_benchmark_run
from tests.conftest import TEST_PRICING
from tests.fakes import FakeGrader, FakeHarness, ar, gr


def _run(spec, harness, grader, tmp_path, clock):
    return execute_benchmark_run(
        spec=spec,
        harness_spec=spec.harnesses[0],
        harness=harness,
        grader=grader,
        worktree_dir=tmp_path / "wt",
        pricing=TEST_PRICING,
        built_sha="deadbeef",
        clock=clock,
    )


def test_stops_on_first_pass(bench_spec, fixed_clock, tmp_path):
    harness = FakeHarness([ar(), ar(), ar()])
    grader = FakeGrader([gr(False, ["thing broke"]), gr(True)])
    rec = _run(bench_spec, harness, grader, tmp_path, fixed_clock)

    assert rec.passed is True
    assert rec.attempts == 2
    assert len(rec.per_attempt) == 2
    assert rec.per_attempt[0].grader_passed is False
    assert rec.per_attempt[0].grader_failures == ["thing broke"]
    assert rec.per_attempt[1].grader_passed is True
    # one start_build, then one continue_build (same session continued)
    kinds = [c[0] for c in harness.calls]
    assert kinds == ["start", "continue"]
    # the retry prompt carried the failure
    followup = harness.calls[1][2]
    assert "thing broke" in followup
    assert grader.setup_calls == 1
    assert grader.grade_calls == 2


def test_stops_at_max_attempts_when_never_green(bench_spec, fixed_clock, tmp_path):
    harness = FakeHarness([ar()])  # repeats
    grader = FakeGrader([gr(False, ["still broken"])])  # repeats
    rec = _run(bench_spec, harness, grader, tmp_path, fixed_clock)

    assert rec.passed is False
    assert rec.attempts == bench_spec.max_attempts == 3
    assert len(rec.per_attempt) == 3
    assert all(not a.grader_passed for a in rec.per_attempt)
    assert [c[0] for c in harness.calls] == ["start", "continue", "continue"]


def test_token_time_cost_summation(bench_spec, fixed_clock, tmp_path):
    # attempt 1: 1000 in / 200 out / 300 cache-write / 0 cache-read / 3s
    # attempt 2:  400 in / 100 out /   0 cache-write / 50 cache-read / 2s
    harness = FakeHarness(
        [
            ar(1000, 200, 3.0, "s1", cache_creation_tokens=300, cache_read_tokens=0),
            ar(400, 100, 2.0, "s1", cache_creation_tokens=0, cache_read_tokens=50),
        ]
    )
    grader = FakeGrader([gr(False, ["x"]), gr(True)])
    rec = _run(bench_spec, harness, grader, tmp_path, fixed_clock)

    assert rec.total_input_tokens == 1400
    assert rec.total_output_tokens == 300
    assert rec.total_cache_tokens == 350
    assert rec.total_wall_time_seconds == pytest.approx(5.0)
    # cost: ($10*1400 + $20*300 + $5*300 + $1*50) / 1e6
    expected = (10.0 * 1400 + 20.0 * 300 + 5.0 * 300 + 1.0 * 50) / 1_000_000
    assert rec.cost_usd == pytest.approx(expected)
    # per-attempt rows mirror the AttemptResults
    assert rec.per_attempt[0].input_tokens == 1000
    assert rec.per_attempt[0].cache_tokens == 300
    assert rec.per_attempt[1].cache_tokens == 50


def test_result_record_shape_round_trips(bench_spec, fixed_clock, tmp_path):
    harness = FakeHarness([ar(123, 45, 1.5, "s9")])
    grader = FakeGrader([gr(True)])
    rec = _run(bench_spec, harness, grader, tmp_path, fixed_clock)

    out = rec.write(tmp_path / "results")
    assert out.exists()
    # filename: <harness>__<model>__<config-slug>__<ISO8601>.json
    assert out.name == "fake__test-model__effort-xhigh__2026-05-12T16-30-00Z.json"
    assert out.parent.name == "demo"

    loaded = json.loads(out.read_text())
    expected_keys = {
        "benchmark", "harness", "model", "config", "started_at", "finished_at",
        "target_repo", "target_start", "built_git_sha", "worktree_path",
        "max_attempts", "attempts", "passed", "total_input_tokens",
        "total_output_tokens", "total_cache_tokens", "cost_usd",
        "total_wall_time_seconds", "per_attempt",
        "pushed_branch", "pushed_branch_url", "pushed_commit",
        "pushed_to_default_branch", "pushed_at", "push_error",
    }
    assert set(loaded) == expected_keys
    assert loaded["benchmark"] == "demo"
    assert loaded["harness"] == "fake"
    assert loaded["model"] == "test-model"
    assert loaded["config"] == {"effort": "xhigh"}
    assert loaded["target_start"] == "empty"
    assert loaded["built_git_sha"] == "deadbeef"
    assert loaded["passed"] is True
    assert loaded["attempts"] == 1
    assert loaded["started_at"] == "2026-05-12T16:30:00Z"
    assert len(loaded["per_attempt"]) == 1
    row = loaded["per_attempt"][0]
    assert set(row) == {
        "attempt", "input_tokens", "output_tokens", "cache_tokens",
        "wall_time_seconds", "grader_passed", "grader_failures",
    }
    assert row["attempt"] == 1
    assert row["input_tokens"] == 123


def test_service_factory_wraps_each_grade(bench_spec, fixed_clock, tmp_path):
    events: list[str] = []

    import contextlib

    @contextlib.contextmanager
    def service():
        events.append("up")
        try:
            yield
        finally:
            events.append("down")

    harness = FakeHarness([ar()])
    grader = FakeGrader([gr(False, ["x"]), gr(True)])
    execute_benchmark_run(
        spec=bench_spec,
        harness_spec=bench_spec.harnesses[0],
        harness=harness,
        grader=grader,
        worktree_dir=tmp_path / "wt",
        pricing=TEST_PRICING,
        service_factory=service,
        clock=fixed_clock,
    )
    # one up/down pair per attempt that ran (2 attempts here)
    assert events == ["up", "down", "up", "down"]


def test_unknown_model_is_an_error(bench_spec, fixed_clock, tmp_path):
    from pagehub_benchmarks.config import ConfigError

    harness = FakeHarness([ar()])
    grader = FakeGrader([gr(True)])
    with pytest.raises(ConfigError):
        execute_benchmark_run(
            spec=bench_spec,
            harness_spec=bench_spec.harnesses[0],
            harness=harness,
            grader=grader,
            worktree_dir=tmp_path / "wt",
            pricing={},  # no entry for "test-model"
            clock=fixed_clock,
        )


def test_followup_prompt_mentions_failures_and_no_git():
    p = build_followup_prompt(["eval A failed: x", "eval B failed: y"])
    assert "eval A failed: x" in p
    assert "eval B failed: y" in p
    assert "that is all" in p
    assert "PR" not in p and "push" not in p
    # empty-failures path is still a sane prompt
    assert "still failing" in build_followup_prompt([])
