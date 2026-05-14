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


def test_build_with_rendered_prompt_and_template_vars(tmp_path: Path):
    """Run records carrying the new fields surface them on the run-detail page."""
    run = dict(SAMPLE_RUN)
    run.update(
        {
            "rendered_prompt": "Build eval-chess-frontend on port 8004.",
            "template_vars": {
                "benchmark_name": "eval-chess-frontend",
                "target_repo": "git@github.com:pagehub-io/eval-chess-frontend.git",
                "target_port": "8004",
                "pagehub_evals_url": "http://localhost:8002",
                "grader_fixture": json.dumps(
                    {"version": 1, "collections": [{"name": "eval-chess-frontend", "items": []}]},
                    indent=2,
                ),
            },
            "per_attempt": [
                dict(run["per_attempt"][0], rendered_prompt="Build eval-chess-frontend on port 8004."),
                dict(run["per_attempt"][1], rendered_prompt="The conformance evals are still failing:\n\n- chess-07 failed\n\nFix the code..."),
            ],
        }
    )
    results = tmp_path / "results" / "eval-chess-frontend"
    results.mkdir(parents=True)
    (results / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.json").write_text(
        json.dumps(run)
    )
    docs = tmp_path / "docs"
    build(results_dir=tmp_path / "results", docs_dir=docs)

    run_html = (docs / "runs" / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.html").read_text()
    # Template-vars table renders, with each auto-var.
    assert "Template vars" in run_html
    assert "benchmark_name" in run_html
    assert "target_port" in run_html
    assert "grader_fixture" in run_html
    # The multi-line fixture body lands inside a <details><pre> block.
    assert "<details>" in run_html
    assert "eval-chess-frontend" in run_html
    # Rendered-prompt section appears with per-attempt details.
    assert "Rendered prompts" in run_html
    assert "Build eval-chess-frontend on port 8004." in run_html
    assert "Fix the code" in run_html


def test_build_without_rendered_prompt_fields_renders_gracefully(tmp_path: Path):
    """Legacy records (no rendered_prompt / template_vars) MUST still render —
    just without the new sections. This guarantees the eval-chess-backend +
    eval-chess-frontend records already on main keep working post-merge."""
    legacy = dict(SAMPLE_RUN)
    # Belt-and-braces: ensure the fields are not present at all.
    legacy.pop("rendered_prompt", None)
    legacy.pop("template_vars", None)
    # Per-attempt rows also have no rendered_prompt key.
    legacy["per_attempt"] = [
        {k: v for k, v in row.items() if k != "rendered_prompt"} for row in legacy["per_attempt"]
    ]
    results = tmp_path / "results" / "eval-chess-backend"
    results.mkdir(parents=True)
    (results / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.json").write_text(
        json.dumps(legacy)
    )
    docs = tmp_path / "docs"
    build(results_dir=tmp_path / "results", docs_dir=docs)

    run_html = (docs / "runs" / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.html").read_text()
    # New sections suppressed when the record carries no data for them.
    assert "Template vars" not in run_html
    assert "Rendered prompts" not in run_html
    # But the rest of the page still rendered (metrics, per-attempt table, etc).
    assert "Per-attempt breakdown" in run_html
    assert "claude-opus-4-7" in run_html


def test_build_removes_orphans_from_prior_run(tmp_path: Path):
    # Simulate the state after renaming a benchmark/run: docs/ contains
    # output paths from the *previous* build that the current build will
    # not emit.
    docs = tmp_path / "docs"
    (docs / "runs").mkdir(parents=True)
    (docs / "benchmarks").mkdir()
    (docs / "results" / "old-benchmark").mkdir(parents=True)
    orphan_run = docs / "runs" / "stale-run.html"
    orphan_run.write_text("<html>stale</html>")
    orphan_bench = docs / "benchmarks" / "old-benchmark.html"
    orphan_bench.write_text("<html>stale</html>")
    orphan_result = docs / "results" / "old-benchmark" / "stale.json"
    orphan_result.write_text("{}")
    # Hand-placed GitHub Pages drop-ins should survive a rebuild.
    nojekyll = docs / ".nojekyll"
    nojekyll.write_text("")
    cname = docs / "CNAME"
    cname.write_text("benchmarks.example.com\n")

    # One real run so the build has something to write.
    results = tmp_path / "results" / "eval-chess-backend"
    results.mkdir(parents=True)
    (results / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.json").write_text(
        json.dumps(SAMPLE_RUN)
    )
    build(results_dir=tmp_path / "results", docs_dir=docs)

    # Orphans gone.
    assert not orphan_run.exists()
    assert not orphan_bench.exists()
    assert not orphan_result.exists()
    # The orphan's now-empty parent directory is pruned too.
    assert not (docs / "results" / "old-benchmark").exists()
    # Allowlisted drop-ins survive.
    assert nojekyll.exists()
    assert cname.read_text() == "benchmarks.example.com\n"
    # And the build's own output is present.
    assert (docs / "index.html").is_file()
    assert (docs / "runs" / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.html").is_file()
    assert (docs / "results" / "eval-chess-backend" / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.json").is_file()
