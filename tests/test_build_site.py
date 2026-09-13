"""The static site generator: renders index / run / benchmark pages from records."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.build_site import (
    LOWER_BOUND_METRICS,
    RUN_TOTAL_LOWER_BOUND_CAVEATS,
    RUN_TOTAL_NEUTRAL_CAVEATS,
    build,
    github_repo_url,
)

# The metrics the theory fixture declares, split by whether the run-total
# lower bound applies to them. Written out BY NAME and deliberately not
# derived from ``LOWER_BOUND_METRICS``: a list read out of the set under test
# cannot notice the set changing. Every member below is checked in the
# direction its tuple names, so dropping a member (a short figure publishes
# clean) and promoting a non-member (a marker on an exact figure) both fail —
# see test_the_lower_bound_metric_set_is_pinned_by_name for why the set is
# ALSO pinned as a whole.
MARKED_METRICS = (
    "cost_usd",
    "total_input_tokens",
    "total_output_tokens",
    "total_cache_tokens",
)
UNMARKED_METRICS = ("attempts", "total_wall_time_seconds", "passed", "max_attempts")
THEORY_METRICS = MARKED_METRICS + UNMARKED_METRICS

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
    """A LEGACY record — ``per_attempt`` entries with no ``raw`` key at all,
    which is every one of the 27 published claude-code runs — carries no
    warning glyph anywhere on its page: not in the table, and not in the
    legend above it. A false warning on every historical record would make
    the marker worthless.

    (Scoped to the legacy shape in round 16: the docstring used to claim an
    all-faithful codex run too, and rendered only this one. That shape is
    ``test_a_clean_codex_run_carries_no_caveat_marking_at_all`` below.)

    The count is 0, not 1: round 16 gated the legend on a marked attempt
    existing. Ungated it shipped a &#9888; to all 27 published claude-code
    pages the moment this branch merged, because pages.yml rebuilds the site
    from source rather than publishing the committed docs/."""
    results = tmp_path / "results" / "eval-chess-frontend"
    results.mkdir(parents=True)
    (results / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.json").write_text(
        json.dumps(SAMPLE_RUN)
    )
    docs = tmp_path / "docs"
    build(results_dir=tmp_path / "results", docs_dir=docs)
    run_html = (docs / "runs" / "claude-code__claude-opus-4-7__effort-xhigh__2026-05-12T16-30-00Z.html").read_text()
    # The legend paragraph is gone with it: "on an attempt means" occurs only
    # inside templates/run.html's legend, which OPENS with that glyph, so a
    # separate assert on the phrase could not fail independently of the count
    # above (round 17, same class as round 16's N-11).
    assert run_html.count("&#9888;") == 0


def test_a_clean_codex_run_carries_no_caveat_marking_at_all(tmp_path: Path):
    """The shape the FIRST clean codex run will produce, which no site test
    rendered before round 16 (N-10): every attempt carries ``raw`` with
    ``usage_faithful`` true and an EMPTY ``usage_caveats`` list.

    Distinct from the legacy record above, where ``raw`` is absent entirely —
    an empty list and a missing key are different inputs to the template's
    ``{% if a.usage_caveats %}`` row gate and to the legend's
    ``selectattr('usage_caveats')``, and only this one exercises the codex
    path. Nothing on the page may suggest a measurement problem: no attempt
    triangle, no legend, no lower-bound prefix on any aggregate."""
    docs = _site(tmp_path, _run_with_caveats(), with_theory=True)
    run_html = (docs / RUN_HTML).read_text()
    # …and not vacuously: this really is the codex shape, raw JSON and all
    # (HTML-escaped in the <pre>, so the quotes are entities).
    assert "&#34;usage_faithful&#34;: true" in run_html
    assert "&#34;usage_caveats&#34;: []" in run_html
    # …and the legend paragraph with it (see the entailment note above).
    assert run_html.count("&#9888;") == 0
    for page in ("index.html", RUN_HTML, "benchmarks/eval-chess-backend.html",
                 "theories/cheaper.html"):
        html = (docs / page).read_text()
        # Every emission of the phrase "lower bound" — run.html's explainer and
        # _marks.html's lb_title — is inside a template unit that also emits
        # this glyph, and the glyph's spelling is pinned by the positive
        # surface tests, so asserting the phrase separately adds nothing
        # (round 17).
        assert "&#8805;" not in html, page


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
            "metrics:\n" + "".join(f"  - {m}\n" for m in THEORY_METRICS) +
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
    produce, plus the "cheapest pass" card above it.

    Also the pin on ``_marks.html``'s ``lb_title`` (review round 16). That
    macro is the ONLY statement of the run-total rule on the index, the
    benchmark page and the theory comparison — ``run.html``'s paragraph never
    renders for a reader who does not open a run page — and round 15 closed
    ``run.html`` while leaving this one unpinned: inverting the trailing
    clause to "the real figure is exactly this and needs no adjustment" left
    all 283 tests green, i.e. the site telling every index reader that a
    lower-bound total needs no adjustment. Only the OPENING clause was
    incidentally covered, by the substring assertion below. Deliberate
    rewording updates this string; an inversion cannot, because a sentence
    and its negation are not the same string."""
    docs = _site(tmp_path, _run_with_caveats("dead_leg_unmeasured"))
    index = (docs / "index.html").read_text()
    assert index.count("&#8805;$12.3456") == 2  # all-runs row + cheapest-pass card
    assert index.count("&#9888;") >= 1
    assert "lower bound" in index
    # The token columns take the prefix but no triangle, so the number itself
    # has to explain it — the cost cell's tooltip is columns away (round 13) —
    # and `lb` is what gives that title a `cursor: help` cue (round 16).
    assert '<span class="lb" title="This run\'s totals are a lower bound' in index
    assert "&#8805;1,234,567</span>" in index
    # …and the class is not decoration: the built stylesheet really gives it
    # the hover cue. Round 16 added `.lb` to the `cursor: help` rule; round 17
    # reverted that edit with all 289 tests still green, because the class was
    # pinned only where it is EMITTED and nothing read static/style.css.
    css = (docs / "style.css").read_text()
    assert re.search(r"(?:^|[,{}\s])\.lb\b[^{}]*\{[^}]*cursor:\s*help", css, re.M), css
    # The aggregate tooltip's sentence, verbatim, as every surface renders it.
    assert (
        "This run's totals are a lower bound, not a measurement: it spent tokens the "
        "harness could not attribute to any attempt (dead_leg_unmeasured), so the real "
        "figure is higher by an unknown amount. See the per-attempt table on the run "
        "page." in index
    )


def test_benchmark_page_presents_a_short_total_as_a_lower_bound(tmp_path: Path):
    """SURFACE 3 of 4 — the per-benchmark run table."""
    docs = _site(tmp_path, _run_with_caveats("dead_leg_unmeasured"))
    bench_html = (docs / "benchmarks" / "eval-chess-backend.html").read_text()
    assert "&#8805;$12.3456" in bench_html
    assert bench_html.count("&#9888;") >= 1
    assert "lower bound" in bench_html


def _theory_cell(theory_html: str, metric: str) -> str:
    """The baseline cell the theory comparison renders for ``metric``."""
    cell = re.search(rf'<td class="mono">{metric}</td>\s*<td>(.*?)</td>', theory_html, re.S)
    assert cell, f"the theory comparison has no {metric} row to check"
    return cell.group(1).strip()


def test_theory_page_presents_a_short_total_as_a_lower_bound(tmp_path: Path):
    """SURFACE 4 of 4 — the theory comparison, where a baseline and a
    treatment are read side by side and a short figure reads as a win.

    This is also the INCLUSION half of the ``LOWER_BOUND_METRICS`` membership
    rule, per metric. Until round 17 only ``cost_usd`` was pinned: dropping
    ``total_input_tokens``, ``total_output_tokens`` or ``total_cache_tokens``
    from the set — individually or all three at once — left all 289 tests
    green while this very table published 1,234,567 / 89,012 / 555 bare
    beside a cost cell reading ``≥$12.3456 ⚠``, i.e. round 12's I-2 defect on
    the one surface built for side-by-side comparison.

    Not hypothetical: the ONE theory shipped in this repo
    (theories/eval-fixture-injection.md) declares
    ``total_output_tokens``, ``total_cache_tokens``, ``cost_usd``,
    ``attempts``, ``total_wall_time_seconds`` and ``passed`` — i.e. two of the
    three droppable members and the promotable non-member, on the page a
    reader compares a baseline against a treatment on."""
    docs = _site(tmp_path, _run_with_caveats("dead_leg_unmeasured"), with_theory=True)
    theory_html = (docs / "theories" / "cheaper.html").read_text()
    # The metric comparison cell AND the run row underneath it.
    assert theory_html.count("&#8805;$12.3456") == 2
    assert theory_html.count("&#9888;") >= 1
    assert "lower bound" in theory_html
    # Every token-derived metric carries BOTH marks: the ≥ on the figure and
    # the ⚠ that explains it. One without the other is a figure a reader
    # cannot act on.
    for metric in MARKED_METRICS:
        cell = _theory_cell(theory_html, metric)
        assert "&#8805;" in cell, (metric, cell)
        assert "&#9888;" in cell, (metric, cell)


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
        # …and no "lower bound" copy either — entailed by this glyph, since
        # every emission of that phrase sits in a template unit that emits it.
        assert "&#8805;" not in html, page
    # …but the per-attempt marking round 11 added is untouched.
    assert (docs / RUN_HTML).read_text().count("&#9888;") == 2


def test_a_faithful_run_carries_no_lower_bound_marking(tmp_path: Path):
    """A LEGACY record — no ``raw`` on any attempt — must render the plain
    figure on every surface. A false ``≥`` on every historical record would
    make the marking worthless.

    (Scoped in round 16: the docstring claimed a caveat-free codex run as
    well and rendered only ``SAMPLE_RUN``. The codex shape is
    ``test_a_clean_codex_run_carries_no_caveat_marking_at_all``.)"""
    docs = _site(tmp_path, dict(SAMPLE_RUN), with_theory=True)
    for page in ("index.html", RUN_HTML, "benchmarks/eval-chess-backend.html",
                 "theories/cheaper.html"):
        html = (docs / page).read_text()
        assert "&#8805;" not in html, page
        # The plain figure is really there — the line above would also pass on
        # a page that rendered no cost at all. (The `and "&#8805;$12.3456" not
        # in html` conjunct this replaces was entailed by it: round 16, N-11.)
        assert "$12.3456" in html, page


def test_a_short_runs_wall_time_and_attempts_are_not_marked_as_lower_bounds(tmp_path: Path):
    """The lower bound applies to the token-derived figures and to nothing
    else (``LOWER_BOUND_METRICS``): wall time, attempts, the attempt cap and
    pass/fail are measured elsewhere and are not short.

    This is the EXCLUSION half of the membership rule; the inclusion half is
    in test_theory_page_presents_a_short_total_as_a_lower_bound. (Round 17
    correction: this docstring used to claim the inclusion half was "pinned
    by the four surface tests above". It was not — those three of the four
    token metrics were droppable with the suite green, because the other
    three surfaces mark their token columns from ``totals_are_lower_bound``
    in the templates and never consult ``LOWER_BOUND_METRICS`` at all. The
    theory comparison is the ONLY surface that keys marking on the metric.)

    Over-marking is cheap to ship and hard to notice: round 14 added
    ``attempts`` and the wall time to the set, the suite as it stood (271
    tests) stayed green, and a caveated run's theory page rendered
    ``&#8805;2 &#9888;`` on an attempt count that is complete. Adding
    ``passed`` still left 289 green in round 17 and renders ``&#8805;&#10003;
    &#9888;`` — a lower bound on a boolean — on the one metric plan §4.5 and
    build_site.py both name as out of scope. A lower-bound marker on a figure
    that is exact devalues the marker everywhere it is right."""
    docs = _site(tmp_path, _run_with_caveats("dead_leg_unmeasured"), with_theory=True)
    theory_html = (docs / "theories" / "cheaper.html").read_text()
    figures = {
        "attempts": "2",
        "total_wall_time_seconds": "1680s",
        "passed": "\u2713",
        "max_attempts": "5",
    }
    assert set(figures) == set(UNMARKED_METRICS)
    for metric in UNMARKED_METRICS:
        # Exact equality is the assertion: it excludes both marks and pins the
        # figure that is really there, so the row cannot pass by being absent.
        assert _theory_cell(theory_html, metric) == figures[metric], metric
    # ...and not vacuously: the token-derived cells of the SAME table ARE
    # marked, so this really is a run whose totals are a lower bound.
    assert "&#8805;$12.3456" in theory_html


def test_the_lower_bound_metric_set_is_pinned_by_name():
    """``LOWER_BOUND_METRICS`` as a whole, so a fifth member cannot arrive
    unexamined.

    The two rendering tests above check each metric they KNOW about, in the
    direction its tuple names. What they cannot see is a member added that
    the theory fixture does not declare — it would be marked on a table no
    test reads. This closes that: any edit to the set fails here and has to
    be classified into one of the two tuples, which is what puts it under a
    rendering assertion."""
    assert set(MARKED_METRICS) == LOWER_BOUND_METRICS


def test_a_neutral_caveat_is_never_named_as_a_reason_a_total_is_short(tmp_path: Path):
    """The reason list beside a lower-bound total names only the caveats that
    SHORTEN it (review round 15).

    ``build_site.py``'s rule comment states this normatively — "only the
    lower-bound subset reaches the templates on purpose" — and nothing pinned
    it: replacing the filtered list with the unfiltered ``caveats_any`` left
    the whole suite green while the run page and the index both named
    ``absorbed_missing_leg`` as a reason the totals are short, which is
    exactly what the site's own rule, the plan and the README all say it is
    not. Every other build-site test feeds ``_run_with_caveats`` a SINGLE
    caveat, and the one multi-caveat case mixes two that are both lower-bound,
    so no test could see the filter. Row 12 pins the boolean; this pins the
    rendered reason."""
    run = dict(SAMPLE_RUN)
    run["per_attempt"] = [
        dict(run["per_attempt"][0], raw={"usage_faithful": False, "usage_caveats": ["missing"]}),
        dict(
            run["per_attempt"][1],
            raw={"usage_faithful": False, "usage_caveats": ["absorbed_missing_leg"]},
        ),
    ]
    docs = _site(tmp_path, run, with_theory=True)
    for page in ("index.html", RUN_HTML, "benchmarks/eval-chess-backend.html",
                 "theories/cheaper.html"):
        html = (docs / page).read_text()
        # Every aggregate tooltip the page renders, on every surface.
        named = re.findall(r"could not attribute to any attempt \(([^)]*)\)", html)
        assert named, f"{page} marks no total, so this asserts nothing"
        assert set(named) == {"missing"}, (page, named)

    run_html = (docs / RUN_HTML).read_text()
    reasons = re.search(r"Reason\(s\): <code>([^<]*)</code>", run_html)
    assert reasons and reasons.group(1) == "missing", run_html[:2000]
    # …and not vacuously: the neutral caveat IS published, on the attempt row
    # that carries it, where it says spend MOVED rather than left the run.
    assert "(absorbed_missing_leg)" in run_html


# --------------------------------------------------------------------------
# Review round 15: the reader-facing copy is inside the §4.5 contract too.
#
# templates/run.html carries the largest block of normative caveat prose in
# the repo — and it was the one file the contract's scope excluded. Three
# independent inversions of that published copy each left the whole suite
# green: saying absorbed_missing_leg DOES shorten the totals, saying a dead
# leg is retried on the SAME thread so nothing is short, and telling the
# reader a marked attempt's figure is exact. The surface tests only assert
# that the substring "lower bound" and a caveat NAME appear; nothing asserted
# what the copy says. These do, on the rendered page.
#
# Round 16 correction: round 15 recorded run.html as "the only statement of
# these rules a reader of a published result ever sees". That is false, and
# the false half is the one that stayed unpinned. run.html renders on the RUN
# PAGE ONLY; _marks.html's lb_title states the run-total rule on the index,
# the benchmark page and the theory comparison, so a reader who never opens a
# run page sees that sentence and nothing else. It is pinned verbatim in
# test_index_presents_a_short_total_as_a_lower_bound, beside the surface it
# renders on.


def _explainer(docs: Path, marker: str) -> str:
    """The one rendered ``<p class="muted">`` containing ``marker``."""
    html = (docs / RUN_HTML).read_text()
    paras = [m.group(0) for m in re.finditer(r'<p class="muted">.*?</p>', html, re.S)]
    hits = [p for p in paras if marker in p]
    assert len(hits) == 1, f"expected exactly one paragraph containing {marker!r}, got {len(hits)}"
    return hits[0]


def test_the_run_page_explainer_states_the_run_total_rule(tmp_path: Path):
    """What the run page TELLS the reader a lower-bound total means, pinned
    clause by clause — including which caveat does not shorten it.

    Deliberate rewording is expected to update these strings; that is the
    point. An inversion cannot, because the sentence and its negation are not
    the same string."""
    docs = _site(tmp_path, _run_with_caveats("missing", "dead_leg_unmeasured"))
    para = _explainer(docs, "lower bound (&#8805;)")
    assert (
        "<strong>The token and cost totals above are a lower bound (&#8805;), not a "
        "measurement</strong> — this run spent tokens that are in none of them, so the "
        "real figures are higher by an unknown amount." in para
    )
    assert "<code>missing</code> — a turn was spent and nothing measured it;" in para
    assert (
        "<code>dead_leg_unmeasured</code> — a dead start leg was retried onto a "
        "<em>new</em> thread, so whatever the abandoned thread spent is billed to no "
        "attempt anywhere." in para
    )
    assert (
        "(<code>absorbed_missing_leg</code> alone does <em>not</em> shorten these "
        "totals: it moves spend between the attempt rows below.)" in para
    )
    # …and the copy is checked against the constants it explains, so a fourth
    # caveat cannot be classified in build_site.py while the page still tells
    # the reader the old vocabulary.
    for caveat in RUN_TOTAL_LOWER_BOUND_CAVEATS:
        assert f"<code>{caveat}</code> —" in para, caveat
    for caveat in RUN_TOTAL_NEUTRAL_CAVEATS:
        assert f"<code>{caveat}</code> alone does <em>not</em> shorten these totals" in para


def test_the_run_page_legend_states_what_a_marked_attempt_means(tmp_path: Path):
    """The per-attempt legend's three reasons, verbatim. Round 15 rewrote its
    first two clauses to say the figure is exact and provably its own — the
    opposite of what the marker means — and 275 tests passed."""
    docs = _site(tmp_path, _run_with_caveats("missing"))
    para = _explainer(docs, "on an attempt means")
    assert (
        "the harness could not measure that attempt's tokens exactly — the figure "
        "is short (nothing measured that attempt&#8217;s turn), not provably its own (it "
        "was taken against a baseline that may be short by an earlier unmeasured turn, so "
        "it may span that turn too), or short by an abandoned thread (a dead leg was "
        "retried onto a new thread, so whatever the harness walked away from is billed to "
        "no attempt)." in para
    )
    assert "More than one reason can apply to the same attempt." in para


def test_the_raw_json_explainer_says_what_usage_faithful_means(tmp_path: Path):
    """The run page's definition of ``usage_faithful``, verbatim (round 16).

    It is the second unpinned statement in the same class as ``lb_title``:
    the flag appears in every raw JSON block on the page, and this sentence
    is the only place the page says what it means. Rewriting "whether it
    measures that attempt alone" to "whether the run passed" — turning a
    measurement-quality flag into a pass/fail one — left all 283 tests
    green."""
    docs = _site(tmp_path, _run_with_caveats("missing"))
    para = _explainer(docs, "What the harness returned for each attempt")
    assert (
        "Codex CLI: a bounded summary of the <code>codex exec --json</code> stream "
        "(<code>thread_id</code>, the verbatim <code>usage</code> object and this "
        "attempt's <code>usage_delta</code> with the <code>usage_faithful</code> "
        "flag that says whether it measures that attempt alone" in para
    ), para


def test_an_unclassified_caveat_makes_the_run_total_a_lower_bound(tmp_path: Path):
    """The run-level filter is deny-by-default: a caveat value this checkout
    does not classify marks the total rather than clearing it.

    Unreachable from the harness in THIS checkout — the vocabulary guard below
    forces every ``USAGE_CAVEATS`` value to be classified — but build_site.py
    renders records written by harness versions it does not import, which is
    the one case that guard cannot see, and is its own stated reason for
    hard-coding the literals. Executed before the fix (round 17), with the
    filter written as an intersection with ``RUN_TOTAL_LOWER_BOUND_CAVEATS``:
    a record whose second attempt carried ``compaction_unmeasured`` rendered
    the attempt's ⚠ and named the caveat on the run page, while the index
    published a clean ``$12.3456`` — one page telling the reader that
    attempt's tokens are unmeasurable and then presenting the run total as a
    measurement. The attempt marker has always failed closed (it is gated on
    ``usage_caveats`` being non-empty at all); this makes the run marker match
    it."""
    docs = _site(tmp_path, _run_with_caveats("compaction_unmeasured"), with_theory=True)
    for page in ("index.html", RUN_HTML, "benchmarks/eval-chess-backend.html",
                 "theories/cheaper.html"):
        html = (docs / page).read_text()
        assert "&#8805;$12.3456" in html, page
        assert "compaction_unmeasured" in html, page
    # …and deny-by-default did not cost the neutral filter its teeth: a
    # KNOWN-neutral caveat alongside an unknown one is still not named as a
    # reason the total is short. (The neutral-alone case is
    # test_absorbed_missing_leg_alone_does_not_shorten_the_run_total.)
    docs = _site(
        tmp_path / "mixed", _run_with_caveats("compaction_unmeasured", "absorbed_missing_leg")
    )
    reasons = re.search(r"Reason\(s\): <code>([^<]*)</code>", (docs / RUN_HTML).read_text())
    assert reasons and reasons.group(1) == "compaction_unmeasured", reasons


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
