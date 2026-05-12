"""End-to-end `run_benchmark` wiring: real worktree (git init), fake harness/grader."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import pagehub_benchmarks.runner.run as run_mod
from pagehub_benchmarks.config import REPO_ROOT
from tests.fakes import FakeGrader, FakeHarness, ar, gr

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _write_benchmark(tmp_path: Path) -> Path:
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "prompts").mkdir()
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "prompts" / "demo.md").write_text("Build the demo. Get the tests passing — that is all.\n")
    (tmp_path / "fixtures" / "demo.json").write_text(json.dumps(
        {"version": 1, "collections": [{"name": "demo-rules", "items": []}]}
    ))
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


def test_run_benchmark_writes_record_and_commits(tmp_path: Path, monkeypatch):
    yaml_path = _write_benchmark(tmp_path)

    harness = FakeHarness([ar(1000, 100, 2.0, "sess"), ar(200, 30, 1.0, "sess")])
    grader = FakeGrader([gr(False, ["needs work"]), gr(True)])
    monkeypatch.setattr(run_mod, "get_harness", lambda name: harness)
    # EvalsGrader(...) is constructed positionally in run_benchmark; ignore args.

    class _G:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return grader

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(run_mod, "EvalsGrader", _G)
    # pricing.yaml is the real one (has claude-opus-4-7).

    written = run_mod.run_benchmark(
        str(yaml_path),
        results_dir=tmp_path / "results",
        worktrees_dir=tmp_path / "worktrees",
        serve=False,
        build_site=False,
    )
    assert len(written) == 1
    rec = json.loads(written[0].read_text())
    assert rec["benchmark"] == "demo"
    assert rec["passed"] is True
    assert rec["attempts"] == 2
    assert rec["total_input_tokens"] == 1200
    assert rec["target_start"] == "empty"
    # the worktree was a fresh `git init` and got a commit -> a sha
    assert rec["built_git_sha"] and len(rec["built_git_sha"]) >= 7
    assert Path(rec["worktree_path"]).is_dir()
    assert (Path(rec["worktree_path"]) / ".git").is_dir()
    # the harness saw a start then a continue (same session continued)
    assert [c[0] for c in harness.calls] == ["start", "continue"]
    # and the docs/ dir of the real repo was NOT touched (build_site=False)
    # (smoke check: we didn't pass --build-site, so nothing wrote to REPO_ROOT)
    assert REPO_ROOT not in written[0].parents
