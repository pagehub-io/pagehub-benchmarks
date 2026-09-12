"""The static site generator: renders index / run / benchmark pages from records."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.build_site import (
    RUN_TOTAL_LOWER_BOUND_CAVEATS,
    RUN_TOTAL_NEUTRAL_CAVEATS,
    build,
    github_repo_url,
)

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


def test_build_renders_raw_claude_json_block_per_attempt(tmp_path: Path):
    """Run records carrying per-attempt ``raw`` (the verbatim claude -p JSON)
    surface it under a collapsible details block on the run page."""
    run = dict(SAMPLE_RUN)
    raw_for_a1 = {
        "session_id": "sess-1",
        "is_error": False,
        "result": "ok",
        "total_cost_usd": 1.23,
        "modelUsage": {
            "claude-opus-4-7": {"inputTokens": 1, "outputTokens": 2},
            "claude-haiku-4-5": {"inputTokens": 200, "outputTokens": 30},
        },
    }
    run["per_attempt"] = [
        dict(run["per_attempt"][0], raw=raw_for_a1),
        dict(run["per_attempt"][1], raw={}),
    ]
    results = tmp_path / "results" / "eval-chess-frontend"
    results.mkdir(parents=True)
    (results / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.json").write_text(
        json.dumps(run)
    )
    docs = tmp_path / "docs"
    build(results_dir=tmp_path / "results", docs_dir=docs)

    run_html = (docs / "runs" / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.html").read_text()
    assert "Raw harness JSON" in run_html
    # The first attempt's raw payload is rendered (collapsed by default).
    assert "claude-haiku-4-5" in run_html
    assert "inputTokens" in run_html and "200" in run_html
    # JSON quotes are HTML-escaped under autoescape (either &#34; or &quot;).
    assert "&#34;modelUsage&#34;" in run_html or "&quot;modelUsage&quot;" in run_html
    # Only attempts with non-empty raw render a block.
    assert run_html.count("raw JSON (") == 1


def test_build_without_rendered_prompt_fields_renders_gracefully(tmp_path: Path):
    """Legacy records (no rendered_prompt / template_vars) MUST still render —
    just without the new sections. This guarantees the eval-chess-backend +
    eval-chess-frontend records already on main keep working post-merge."""
    legacy = dict(SAMPLE_RUN)
    # Belt-and-braces: ensure the fields are not present at all.
    legacy.pop("rendered_prompt", None)
    legacy.pop("template_vars", None)
    # Per-attempt rows also have no rendered_prompt / raw keys.
    legacy["per_attempt"] = [
        {k: v for k, v in row.items() if k not in ("rendered_prompt", "raw")}
        for row in legacy["per_attempt"]
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
    assert "Raw harness JSON" not in run_html
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


def test_build_flags_an_attempt_whose_tokens_are_not_its_own(tmp_path: Path):
    """The codex-cli harness marks any attempt whose token figures are not a
    measure of that attempt alone. The raw JSON is collapsed by default, so a
    reader of the per-attempt table would otherwise see an over- or
    under-reported figure with nothing saying so. Attempts without the marker
    — and legacy records, which have no ``raw`` at all — must stay clean."""
    run = dict(SAMPLE_RUN)
    run["per_attempt"] = [
        dict(run["per_attempt"][0], raw={"usage_faithful": True, "usage_caveats": []}),
        dict(
            run["per_attempt"][1],
            raw={"usage_faithful": False, "usage_caveats": ["absorbed_missing_leg"]},
        ),
    ]
    results = tmp_path / "results" / "eval-chess-frontend"
    results.mkdir(parents=True)
    (results / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.json").write_text(
        json.dumps(run)
    )
    docs = tmp_path / "docs"
    build(results_dir=tmp_path / "results", docs_dir=docs)
    run_html = (docs / "runs" / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.html").read_text()
    # One marker in the table (plus the one in the explanatory line above it).
    assert run_html.count("absorbed_missing_leg") >= 1
    assert run_html.count("&#9888;") == 2
    assert "not a measure of this attempt alone" in run_html


def test_build_marks_no_attempt_when_the_harness_reports_none(tmp_path: Path):
    """A run whose attempts are all faithful, and a legacy run with no ``raw``
    at all, must carry no warning glyph in the table — a false warning on
    every historical record would make the marker worthless."""
    results = tmp_path / "results" / "eval-chess-frontend"
    results.mkdir(parents=True)
    (results / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.json").write_text(
        json.dumps(SAMPLE_RUN)
    )
    docs = tmp_path / "docs"
    build(results_dir=tmp_path / "results", docs_dir=docs)
    run_html = (docs / "runs" / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.html").read_text()
    assert run_html.count("&#9888;") == 1  # only the explanatory line


# --------------------------------------------------------------------------
# Run-level totals: which caveats make an AGGREGATE a lower bound
#
# Round 12, I-2: the attempt row was marked but every aggregate the site
# publishes — the run headline, the index's head-to-head cost table, the
# benchmark page and the theory page — rendered the same understated figure
# with nothing to say so. One test per surface, because the round-11 fix was
# pinned by a rendered-page test that only covered the page it was written
# for.


def _run_with_caveats(*caveats: str) -> dict:
    """SAMPLE_RUN whose second attempt carries ``caveats``."""
    run = dict(SAMPLE_RUN)
    run["per_attempt"] = [
        dict(run["per_attempt"][0], raw={"usage_faithful": True, "usage_caveats": []}),
        dict(
            run["per_attempt"][1],
            raw={"usage_faithful": not caveats, "usage_caveats": list(caveats)},
        ),
    ]
    return run


def _site(tmp_path: Path, run: dict, *, with_theory: bool = False) -> Path:
    results = tmp_path / "results" / "eval-chess-backend"
    results.mkdir(parents=True)
    (results / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.json").write_text(
        json.dumps(run)
    )
    theories = tmp_path / "theories"
    theories.mkdir()
    if with_theory:
        (theories / "cheaper.md").write_text(
            "---\n"
            "name: cheaper\n"
            "hypothesis: the treatment is cheaper\n"
            "baseline: eval-chess-backend\n"
            "treatment: eval-chess-frontend\n"
            "metrics:\n  - cost_usd\n  - total_input_tokens\n  - attempts\n"
            "  - total_wall_time_seconds\n"
            "status: pending\n"
            "---\n\n## Background\n\nnone\n"
        )
    docs = tmp_path / "docs"
    build(
        results_dir=tmp_path / "results",
        docs_dir=docs,
        benchmarks_dir=tmp_path / "benchmarks",
        theories_dir=theories,
    )
    return docs


RUN_HTML = "runs/claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.html"


def test_run_page_headline_presents_a_short_total_as_a_lower_bound(tmp_path: Path):
    """SURFACE 1 of 4 — the run page's own headline and Tokens card.

    ``dead_leg_unmeasured`` means a dead start leg's thread was abandoned and
    never resumed or read, so its spend is in no attempt's delta anywhere:
    the RUN total is genuinely short, not merely redistributed. The headline
    and the cost/token figures must say so."""
    docs = _site(tmp_path, _run_with_caveats("dead_leg_unmeasured"))
    run_html = (docs / RUN_HTML).read_text()
    # Headline and Tokens card both present the figure as a lower bound.
    assert run_html.count("&#8805;$12.3456") == 2
    # Token totals are short for the same reason and carry the same prefix.
    assert "&#8805;1,234,567" in run_html
    # And the page says, in words, what the marker means.
    assert "lower bound" in run_html
    assert "dead_leg_unmeasured" in run_html


def test_index_presents_a_short_total_as_a_lower_bound(tmp_path: Path):
    """SURFACE 2 of 4 — the head-to-head cost table this repo exists to
    produce, plus the "cheapest pass" card above it."""
    docs = _site(tmp_path, _run_with_caveats("dead_leg_unmeasured"))
    index = (docs / "index.html").read_text()
    assert index.count("&#8805;$12.3456") == 2  # all-runs row + cheapest-pass card
    assert index.count("&#9888;") >= 1
    assert "lower bound" in index
    # The token columns take the prefix but no triangle, so the number itself
    # has to explain it — the cost cell's tooltip is columns away (round 13).
    assert '<span title="This run\'s totals are a lower bound' in index
    assert "&#8805;1,234,567</span>" in index


def test_benchmark_page_presents_a_short_total_as_a_lower_bound(tmp_path: Path):
    """SURFACE 3 of 4 — the per-benchmark run table."""
    docs = _site(tmp_path, _run_with_caveats("dead_leg_unmeasured"))
    bench_html = (docs / "benchmarks" / "eval-chess-backend.html").read_text()
    assert "&#8805;$12.3456" in bench_html
    assert bench_html.count("&#9888;") >= 1
    assert "lower bound" in bench_html


def test_theory_page_presents_a_short_total_as_a_lower_bound(tmp_path: Path):
    """SURFACE 4 of 4 — the theory comparison, where a baseline and a
    treatment are read side by side and a short figure reads as a win."""
    docs = _site(tmp_path, _run_with_caveats("dead_leg_unmeasured"), with_theory=True)
    theory_html = (docs / "theories" / "cheaper.html").read_text()
    # The metric comparison cell AND the run row underneath it.
    assert theory_html.count("&#8805;$12.3456") == 2
    assert theory_html.count("&#9888;") >= 1
    assert "lower bound" in theory_html


def test_missing_makes_the_run_total_a_lower_bound(tmp_path: Path):
    """``missing`` is the second caveat that shortens a RUN total. Executed
    (2026-09-12): two runs whose published attempt records are identical —
    ``["missing"]`` then ``["absorbed_missing_leg"]`` — totalled 3,959 and
    7,158 input tokens against a true spend of 7,158, because whether the
    unmeasured turn lands in a later leg's delta depends on whether codex's
    rollout repaired the baseline, which the record does not publish. So the
    total is a lower bound: sometimes exact, never provably so."""
    docs = _site(tmp_path, _run_with_caveats("missing"), with_theory=True)
    assert "&#8805;$12.3456" in (docs / "index.html").read_text()
    assert "&#8805;$12.3456" in (docs / RUN_HTML).read_text()


def test_absorbed_missing_leg_alone_does_not_shorten_the_run_total(tmp_path: Path):
    """The rule has teeth in both directions: ``absorbed_missing_leg`` says an
    attempt's delta may span an earlier unmeasured leg of the SAME run — it
    moves spend between attempt rows and leaves the run total whole. Marking
    every caveated run as short would make the aggregate marker meaningless.
    The attempt row still gets its own triangle."""
    docs = _site(tmp_path, _run_with_caveats("absorbed_missing_leg"), with_theory=True)
    for page in ("index.html", RUN_HTML, "benchmarks/eval-chess-backend.html",
                 "theories/cheaper.html"):
        html = (docs / page).read_text()
        assert "&#8805;" not in html, page
        assert "lower bound" not in html, page
    # …but the per-attempt marking round 11 added is untouched.
    assert (docs / RUN_HTML).read_text().count("&#9888;") == 2


def test_a_faithful_run_carries_no_lower_bound_marking(tmp_path: Path):
    """A run with no caveats at all — and a legacy record with no ``raw`` —
    must render the plain figure. A false ``≥`` on every historical record
    would make the marking worthless."""
    docs = _site(tmp_path, dict(SAMPLE_RUN), with_theory=True)
    for page in ("index.html", RUN_HTML, "benchmarks/eval-chess-backend.html",
                 "theories/cheaper.html"):
        html = (docs / page).read_text()
        assert "&#8805;" not in html, page
        assert "$12.3456" in html or "cost" in html, page


def test_a_short_runs_wall_time_and_attempts_are_not_marked_as_lower_bounds(tmp_path: Path):
    """The lower bound applies to the token-derived figures and to nothing
    else (``LOWER_BOUND_METRICS``): wall time, attempts and pass/fail are
    measured elsewhere and are not short.

    The DANGEROUS direction — dropping a token metric, so a short figure
    publishes clean — is pinned by the four surface tests above. This pins the
    other one, which is cheap to ship and hard to notice: round 14 added
    ``attempts`` and the wall time to the set, the suite as it stood (271
    tests) stayed green, and a caveated run's theory page rendered
    ``&#8805;2 &#9888;`` on an attempt count that is complete. A lower-bound marker on a figure that is exact
    devalues the marker everywhere it is right."""
    docs = _site(tmp_path, _run_with_caveats("dead_leg_unmeasured"), with_theory=True)
    theory_html = (docs / "theories" / "cheaper.html").read_text()
    for metric, figure in (("attempts", "2"), ("total_wall_time_seconds", "1680s")):
        cell = re.search(rf'<td class="mono">{metric}</td>\s*<td>(.*?)</td>', theory_html, re.S)
        assert cell, f"the theory comparison has no {metric} row to check"
        assert cell.group(1).strip() == figure, (metric, cell.group(1))
    # ...and not vacuously: the token-derived cells of the SAME table ARE
    # marked, so this really is a run whose totals are a lower bound.
    assert "&#8805;$12.3456" in theory_html


def test_every_harness_caveat_is_classified_for_run_totals():
    """The site decides run-level marking from string literals, because it
    renders records written by harness versions it does not import. This is
    the anti-drift pin: a fourth caveat value must be classified as shortening
    a run total or not, deliberately, rather than defaulting to silence."""
    from pagehub_benchmarks.harnesses.codex_cli import (
        USAGE_CAVEAT_ABSORBED,
        USAGE_CAVEAT_DEAD_LEG,
        USAGE_CAVEAT_MISSING,
        USAGE_CAVEATS,
    )

    assert set(USAGE_CAVEATS) == RUN_TOTAL_LOWER_BOUND_CAVEATS | RUN_TOTAL_NEUTRAL_CAVEATS
    assert not RUN_TOTAL_LOWER_BOUND_CAVEATS & RUN_TOTAL_NEUTRAL_CAVEATS
    assert {USAGE_CAVEAT_MISSING, USAGE_CAVEAT_DEAD_LEG} == RUN_TOTAL_LOWER_BOUND_CAVEATS
    assert {USAGE_CAVEAT_ABSORBED} == RUN_TOTAL_NEUTRAL_CAVEATS
