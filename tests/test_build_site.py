"""The static site generator: renders index / run / benchmark pages from records."""

from __future__ import annotations

import json
from pathlib import Path

from tools.build_site import build, github_repo_url

SAMPLE_RUN = {
    "benchmark": "eval-chess-backend",
    "harness": "claude-code",
    "model": "claude-opus-4-7",
    "config": {"effort": "xhigh"},
    "started_at": "2026-05-12T16:30:00Z",
    "finished_at": "2026-05-12T16:58:00Z",
    "target_repo": "git@github.com:pagehub-io/eval-chess-backend.git",
    "target_start": "empty",
    "built_git_sha": "abc123def4567890",
    "worktree_path": "/tmp/wt/eval-chess-backend/claude-code__x__y",
    "max_attempts": 5,
    "attempts": 2,
    "passed": True,
    "total_input_tokens": 1234567,
    "total_output_tokens": 89012,
    "total_cache_tokens": 555,
    "cost_usd": 12.3456,
    "total_wall_time_seconds": 1680.0,
    "per_attempt": [
        {"attempt": 1, "input_tokens": 1000000, "output_tokens": 50000, "wall_time_seconds": 900.0,
         "grader_passed": False, "grader_failures": ["chess-07 :: castle-k-fen [json_path_eq] failed: {}"]},
        {"attempt": 2, "input_tokens": 234567, "output_tokens": 39012, "wall_time_seconds": 780.0,
         "grader_passed": True, "grader_failures": []},
    ],
}


def test_github_repo_url():
    assert github_repo_url("git@github.com:pagehub-io/eval-chess-backend.git") == "https://github.com/pagehub-io/eval-chess-backend"
    assert github_repo_url("https://github.com/pagehub-io/x") == "https://github.com/pagehub-io/x"
    assert github_repo_url("ssh://git@github.com/o/r.git") == "https://github.com/o/r"


def test_build_with_one_run(tmp_path: Path):
    results = tmp_path / "results" / "eval-chess-backend"
    results.mkdir(parents=True)
    (results / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.json").write_text(
        json.dumps(SAMPLE_RUN)
    )
    docs = tmp_path / "docs"
    build(results_dir=tmp_path / "results", docs_dir=docs)

    index = (docs / "index.html").read_text()
    assert "eval-chess-backend" in index
    assert "claude-opus-4-7" in index
    assert "1/1 (100%)" in index  # pass rate
    assert "$12.3456" in index

    run_html = (docs / "runs" / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.html").read_text()
    assert "PASSED" in run_html
    assert "https://github.com/pagehub-io/eval-chess-backend" in run_html
    assert "https://github.com/pagehub-io/eval-chess-backend/commit/abc123def4567890" in run_html
    assert "benchmarks/eval-chess-backend.yaml" in run_html
    assert "prompts/eval-chess-backend.md" in run_html
    assert "fixtures/eval-chess-backend.json" in run_html
    assert "castle-k-fen" in run_html  # a grader failure surfaced
    assert "../results/eval-chess-backend/claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.json" in run_html

    bench_html = (docs / "benchmarks" / "eval-chess-backend.html").read_text()
    assert "claude-opus-4-7" in bench_html

    # the raw record was copied into the site so its in-page link resolves
    copied = docs / "results" / "eval-chess-backend" / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.json"
    assert copied.is_file()
    assert json.loads(copied.read_text())["benchmark"] == "eval-chess-backend"


def test_build_with_pushed_run_shows_built_code_section(tmp_path: Path):
    pushed_run = dict(SAMPLE_RUN)
    pushed_run.update(
        {
            "pushed_branch": "bench/claude-code/claude-opus-4-7/effort-xhigh/2026-05-12T16-30-00Z",
            "pushed_branch_url": "https://github.com/pagehub-io/eval-chess-backend/tree/bench/claude-code/claude-opus-4-7/effort-xhigh/2026-05-12T16-30-00Z",
            "pushed_commit": "abc123def4567890",
            "pushed_to_default_branch": True,
            "pushed_at": "2026-05-12T17:00:00Z",
            "push_error": None,
        }
    )
    results = tmp_path / "results" / "eval-chess-backend"
    results.mkdir(parents=True)
    (results / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.json").write_text(
        json.dumps(pushed_run)
    )
    docs = tmp_path / "docs"
    build(results_dir=tmp_path / "results", docs_dir=docs)

    run_html = (docs / "runs" / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.html").read_text()
    assert "Built code" in run_html
    assert (
        "https://github.com/pagehub-io/eval-chess-backend/tree/bench/claude-code/claude-opus-4-7/effort-xhigh/2026-05-12T16-30-00Z"
        in run_html
    )
    assert "default branch" in run_html  # the badge

    index = (docs / "index.html").read_text()
    # The compact branch label in the all-runs table.
    assert "2026-05-12T16-30-00Z" in index


def test_build_with_push_failure_surfaces_error_note(tmp_path: Path):
    failed_run = dict(SAMPLE_RUN)
    failed_run.update(
        {
            "pushed_branch": None,
            "pushed_branch_url": None,
            "pushed_commit": "abc123def4567890",
            "pushed_to_default_branch": False,
            "pushed_at": None,
            "push_error": "remote: Permission denied",
        }
    )
    results = tmp_path / "results" / "eval-chess-backend"
    results.mkdir(parents=True)
    (results / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.json").write_text(
        json.dumps(failed_run)
    )
    docs = tmp_path / "docs"
    build(results_dir=tmp_path / "results", docs_dir=docs)

    run_html = (docs / "runs" / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.html").read_text()
    assert "push error" in run_html
    assert "Permission denied" in run_html

    index = (docs / "index.html").read_text()
    assert "push failed" in index


def test_build_empty_is_fine(tmp_path: Path):
    docs = tmp_path / "docs"
    build(results_dir=tmp_path / "results", docs_dir=docs)  # no results dir at all
    assert "No runs recorded yet" in (docs / "index.html").read_text()
