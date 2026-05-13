"""Push-to-target-repo behavior, exercised end-to-end via ``run_benchmark``
with a FakePusher (no real git push, no network).

What we care about:
  - every run pushes a ``bench/<harness>/<model>/<config-slug>/<ISO>`` branch
  - default-branch push only when the run passed AND the target is empty
  - push failures are recorded but never abort the run
  - all ``pushed_*`` fields land in the result JSON
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import pagehub_benchmarks.runner.run as run_mod
from tests.fakes import FakeGrader, FakeHarness, FakePusher, ar, gr

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _write_benchmark(tmp_path: Path) -> Path:
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "prompts").mkdir()
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "prompts" / "demo.md").write_text(
        "Build the demo. Get the tests passing — that is all.\n"
    )
    (tmp_path / "fixtures" / "demo.json").write_text(
        json.dumps({"version": 1, "collections": [{"name": "demo-rules", "items": []}]})
    )
    yaml_path = tmp_path / "benchmarks" / "demo.yaml"
    yaml_path.write_text(
        "name: demo\n"
        "description: demo\n"
        "target_repo: git@github.com:example/demo.git\n"
        "target_start: empty\n"
        f"build_prompt_file: {tmp_path / 'prompts' / 'demo.md'}\n"
        "grader:\n"
        "  fixture_bundle: fixtures/demo.json\n"
        "  collection: demo-rules\n"
        "  env: {demo_url: 'http://localhost:9999'}\n"
        "max_attempts: 3\n"
        "harnesses:\n"
        "  - harness: fake-harness\n"
        "    model: claude-opus-4-7\n"
        "    config: {effort: xhigh}\n"
    )
    return yaml_path


def _wire(monkeypatch, harness, grader) -> None:
    monkeypatch.setattr(run_mod, "get_harness", lambda name: harness)

    class _G:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return grader

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(run_mod, "EvalsGrader", _G)


def _run(tmp_path: Path, monkeypatch, *, harness, grader, pusher) -> dict:
    _wire(monkeypatch, harness, grader)
    yaml_path = _write_benchmark(tmp_path)
    written = run_mod.run_benchmark(
        str(yaml_path),
        results_dir=tmp_path / "results",
        worktrees_dir=tmp_path / "worktrees",
        serve=False,
        build_site=False,
        pusher=pusher,
    )
    assert len(written) == 1
    return json.loads(written[0].read_text())


def test_push_happens_on_pass_with_empty_target_uses_default_branch(tmp_path, monkeypatch):
    harness = FakeHarness([ar()])
    grader = FakeGrader([gr(True)])
    pusher = FakePusher(target_empty=True)

    rec = _run(tmp_path, monkeypatch, harness=harness, grader=grader, pusher=pusher)

    assert len(pusher.push_calls) == 1
    call = pusher.push_calls[0]
    assert call["target_repo"] == "git@github.com:example/demo.git"
    # branch shape: bench/<harness>/<model>/<config-slug>/<ISO>
    assert call["branch"].startswith("bench/fake-harness/claude-opus-4-7/effort-xhigh/")
    assert call["push_to_default_branch"] is True
    assert pusher.is_empty_calls == ["git@github.com:example/demo.git"]

    assert rec["pushed_branch"] == call["branch"]
    assert (
        rec["pushed_branch_url"]
        == f"https://github.com/example/demo/tree/{call['branch']}"
    )
    assert rec["pushed_to_default_branch"] is True
    assert rec["pushed_commit"]  # the fake supplies a sha
    assert rec["pushed_at"]
    assert rec["push_error"] is None


def test_push_happens_on_fail_but_skips_default_branch(tmp_path, monkeypatch):
    harness = FakeHarness([ar()])  # one attempt, repeats
    grader = FakeGrader([gr(False, ["still broken"])])  # never passes
    pusher = FakePusher(target_empty=True)  # would-have-pushed-default if it had passed

    rec = _run(tmp_path, monkeypatch, harness=harness, grader=grader, pusher=pusher)

    assert rec["passed"] is False
    assert len(pusher.push_calls) == 1
    # Empty-target probe is skipped entirely when the run failed.
    assert pusher.is_empty_calls == []
    assert pusher.push_calls[0]["push_to_default_branch"] is False
    assert rec["pushed_branch"]
    assert rec["pushed_to_default_branch"] is False


def test_push_skips_default_branch_when_target_not_empty(tmp_path, monkeypatch):
    harness = FakeHarness([ar()])
    grader = FakeGrader([gr(True)])
    pusher = FakePusher(target_empty=False)

    rec = _run(tmp_path, monkeypatch, harness=harness, grader=grader, pusher=pusher)

    assert pusher.is_empty_calls == ["git@github.com:example/demo.git"]
    assert pusher.push_calls[0]["push_to_default_branch"] is False
    assert rec["pushed_to_default_branch"] is False
    assert rec["pushed_branch"]  # branch push still happened


def test_push_failure_does_not_abort_the_run(tmp_path, monkeypatch):
    harness = FakeHarness([ar()])
    grader = FakeGrader([gr(True)])
    pusher = FakePusher(fail_with="ref bench/foo already exists")

    rec = _run(tmp_path, monkeypatch, harness=harness, grader=grader, pusher=pusher)

    assert rec["passed"] is True  # grader verdict is the source of truth
    assert rec["push_error"] == "ref bench/foo already exists"
    assert rec["pushed_branch"] is None
    assert rec["pushed_branch_url"] is None
    assert rec["pushed_commit"]  # the fake still surfaces the head sha


def test_push_exception_is_caught_and_recorded(tmp_path, monkeypatch):
    harness = FakeHarness([ar()])
    grader = FakeGrader([gr(True)])
    pusher = FakePusher(raise_with=RuntimeError("network unreachable"))

    rec = _run(tmp_path, monkeypatch, harness=harness, grader=grader, pusher=pusher)

    assert rec["passed"] is True
    assert rec["push_error"] and "network unreachable" in rec["push_error"]
    assert rec["pushed_branch"] is None


def test_default_branch_leg_failure_recorded_but_branch_push_kept(tmp_path, monkeypatch):
    harness = FakeHarness([ar()])
    grader = FakeGrader([gr(True)])
    pusher = FakePusher(
        target_empty=True, fail_default_with="protected branch rejected"
    )

    rec = _run(tmp_path, monkeypatch, harness=harness, grader=grader, pusher=pusher)

    assert rec["passed"] is True
    assert rec["pushed_branch"]
    assert rec["pushed_to_default_branch"] is False
    assert rec["push_error"] == "protected branch rejected"
