"""Theory file parsing, validation, and site rendering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pagehub_benchmarks.theories import (
    DEFAULT_METRICS,
    Theory,
    TheoryError,
    load_all_theories,
    parse_theory,
)


def _theory(
    *,
    name="t1",
    hypothesis="hypothesis text",
    baseline="bench-a",
    treatment="bench-b",
    metrics=None,
    status="pending",
    body="\n## Background\nfoo\n",
):
    parts = [
        "---",
        f"name: {name}",
        f"hypothesis: {hypothesis}",
        f"baseline: {baseline}",
        f"treatment: {treatment}",
        f"status: {status}",
    ]
    if metrics is not None:
        parts.append("metrics:")
        for m in metrics:
            parts.append(f"  - {m}")
    parts.append("---")
    return "\n".join(parts) + body


def test_parse_minimum_theory(tmp_path):
    source = tmp_path / "x.md"
    source.write_text(_theory())
    t = parse_theory(source.read_text(), source)
    assert isinstance(t, Theory)
    assert t.name == "t1"
    assert t.baseline == "bench-a"
    assert t.treatment == "bench-b"
    assert t.status == "pending"
    assert t.metrics == list(DEFAULT_METRICS)
    assert "Background" in t.body_markdown
    assert t.slug == "x"


def test_parse_custom_metrics(tmp_path):
    source = tmp_path / "x.md"
    source.write_text(_theory(metrics=["attempts", "cost_usd"]))
    t = parse_theory(source.read_text(), source)
    assert t.metrics == ["attempts", "cost_usd"]


def test_missing_frontmatter_rejected(tmp_path):
    source = tmp_path / "x.md"
    source.write_text("just some markdown\nno frontmatter at all\n")
    with pytest.raises(TheoryError) as exc:
        parse_theory(source.read_text(), source)
    assert "frontmatter" in str(exc.value)


@pytest.mark.parametrize(
    "missing",
    ["name", "hypothesis", "baseline", "treatment"],
)
def test_missing_required_field_rejected(tmp_path, missing):
    kwargs = {"name": "x", "hypothesis": "h", "baseline": "a", "treatment": "b"}
    kwargs[missing] = ""
    source = tmp_path / "x.md"
    source.write_text(_theory(**kwargs))
    with pytest.raises(TheoryError) as exc:
        parse_theory(source.read_text(), source)
    assert missing in str(exc.value)


def test_baseline_equals_treatment_rejected(tmp_path):
    source = tmp_path / "x.md"
    source.write_text(_theory(baseline="same", treatment="same"))
    with pytest.raises(TheoryError) as exc:
        parse_theory(source.read_text(), source)
    assert "baseline" in str(exc.value) and "treatment" in str(exc.value)


def test_invalid_status_rejected(tmp_path):
    source = tmp_path / "x.md"
    source.write_text(_theory(status="who-knows"))
    with pytest.raises(TheoryError) as exc:
        parse_theory(source.read_text(), source)
    assert "status" in str(exc.value)


def test_metrics_must_be_list_of_strings(tmp_path):
    source = tmp_path / "x.md"
    # An int in the metrics list — invalid.
    body = _theory(metrics=None).replace(
        "status: pending", "status: pending\nmetrics:\n  - 12\n"
    )
    source.write_text(body)
    with pytest.raises(TheoryError):
        parse_theory(source.read_text(), source)


def test_load_all_theories_sorted_by_slug(tmp_path):
    (tmp_path / "b.md").write_text(_theory(name="b"))
    (tmp_path / "a.md").write_text(_theory(name="a"))
    out = load_all_theories(tmp_path)
    assert [t.slug for t in out] == ["a", "b"]


def test_load_all_theories_missing_dir_returns_empty(tmp_path):
    assert load_all_theories(tmp_path / "no-such-dir") == []


# --------------------------------------------------------------------------
# Site rendering


SAMPLE_RUN = {
    "benchmark": "bench-a",
    "harness": "claude-code",
    "model": "claude-opus-4-7",
    "config": {"effort": "xhigh"},
    "started_at": "2026-05-12T16:30:00Z",
    "finished_at": "2026-05-12T16:58:00Z",
    "target_repo": "git@github.com:org/bench-a.git",
    "target_start": "empty",
    "built_git_sha": "abc",
    "worktree_path": "/tmp/wt",
    "max_attempts": 5,
    "attempts": 2,
    "passed": True,
    "total_input_tokens": 1234,
    "total_output_tokens": 567,
    "total_cache_tokens": 999,
    "cost_usd": 1.2345,
    "total_wall_time_seconds": 100.0,
    "per_attempt": [
        {"attempt": 1, "input_tokens": 1234, "output_tokens": 567,
         "cache_tokens": 999, "wall_time_seconds": 100.0,
         "grader_passed": True, "grader_failures": []},
    ],
}


def _make_repo(tmp_path: Path):
    """Lay out a minimal repo shape the site builder can chew on."""
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "theories").mkdir()
    (tmp_path / "results").mkdir()
    (tmp_path / "benchmarks" / "bench-a.yaml").write_text(
        "name: bench-a\ntarget_repo: git@github.com:org/bench-a.git\n"
        "build_prompt_file: prompts/bench-a.md\n"
        "grader: {fixture_bundle: fixtures/bench-a.json, collection: bench-a}\n"
        "harnesses: [{harness: claude-code, model: claude-opus-4-7}]\n"
    )
    (tmp_path / "benchmarks" / "bench-b.yaml").write_text(
        "name: bench-b\ntarget_repo: git@github.com:org/bench-b.git\n"
        "build_prompt_file: prompts/bench-b.md\n"
        "grader: {fixture_bundle: fixtures/bench-b.json, collection: bench-b}\n"
        "harnesses: [{harness: claude-code, model: claude-opus-4-7}]\n"
    )
    return tmp_path


def test_build_renders_theory_index_and_detail_pages(tmp_path: Path):
    from tools.build_site import build

    _make_repo(tmp_path)
    (tmp_path / "theories" / "fixture-injection.md").write_text(
        _theory(name="fixture-injection", baseline="bench-a", treatment="bench-b")
    )
    # one baseline run, no treatment run.
    (tmp_path / "results" / "bench-a").mkdir()
    (tmp_path / "results" / "bench-a" / "run1.json").write_text(json.dumps(SAMPLE_RUN))

    docs = tmp_path / "docs"
    build(
        results_dir=tmp_path / "results",
        docs_dir=docs,
        benchmarks_dir=tmp_path / "benchmarks",
        theories_dir=tmp_path / "theories",
    )

    idx = (docs / "theories" / "index.html").read_text()
    assert "fixture-injection" in idx
    assert "bench-a" in idx and "bench-b" in idx
    assert "pending" in idx

    detail = (docs / "theories" / "fixture-injection.html").read_text()
    assert "fixture-injection" in detail
    # Baseline cell populated (latest run from SAMPLE_RUN).
    assert "$1.2345" in detail  # cost_usd format
    # Treatment side has no runs yet.
    assert "no runs yet" in detail
    # Home page surfaces the theories link.
    home = (docs / "index.html").read_text()
    assert "theories" in home


def test_build_with_both_sides_having_runs(tmp_path: Path):
    from tools.build_site import build

    _make_repo(tmp_path)
    (tmp_path / "theories" / "x.md").write_text(
        _theory(name="x", baseline="bench-a", treatment="bench-b")
    )
    (tmp_path / "results" / "bench-a").mkdir()
    (tmp_path / "results" / "bench-a" / "r.json").write_text(json.dumps(SAMPLE_RUN))
    treatment_run = dict(SAMPLE_RUN, benchmark="bench-b", cost_usd=0.5,
                         attempts=1, total_wall_time_seconds=50.0,
                         total_output_tokens=200,
                         started_at="2026-05-13T10:00:00Z")
    (tmp_path / "results" / "bench-b").mkdir()
    (tmp_path / "results" / "bench-b" / "r.json").write_text(json.dumps(treatment_run))

    docs = tmp_path / "docs"
    build(
        results_dir=tmp_path / "results",
        docs_dir=docs,
        benchmarks_dir=tmp_path / "benchmarks",
        theories_dir=tmp_path / "theories",
    )
    detail = (docs / "theories" / "x.html").read_text()
    assert "$1.2345" in detail and "$0.5000" in detail
    # Side-by-side: attempts 2 (baseline) and 1 (treatment) both render.
    assert "<td>2</td>" in detail
    assert "<td>1</td>" in detail
    assert "no runs yet" not in detail


def test_build_with_no_theories_does_not_create_theories_dir(tmp_path: Path):
    from tools.build_site import build

    _make_repo(tmp_path)
    # No theories file written.
    docs = tmp_path / "docs"
    build(
        results_dir=tmp_path / "results",
        docs_dir=docs,
        benchmarks_dir=tmp_path / "benchmarks",
        theories_dir=tmp_path / "theories",
    )
    # Empty theories dir on disk -> no docs/theories/ written.
    assert not (docs / "theories").exists()
    # The home-page link counter doesn't render the "theories (N)" suffix.
    home = (docs / "index.html").read_text()
    assert "theories (" not in home


def test_build_sweeps_orphan_theory_pages(tmp_path: Path):
    """A theory renamed/removed should leave no stale page behind."""
    from tools.build_site import build

    _make_repo(tmp_path)
    # First build: one theory file produces one page.
    (tmp_path / "theories" / "old-theory.md").write_text(
        _theory(name="old-theory", baseline="bench-a", treatment="bench-b")
    )
    docs = tmp_path / "docs"
    build(
        results_dir=tmp_path / "results",
        docs_dir=docs,
        benchmarks_dir=tmp_path / "benchmarks",
        theories_dir=tmp_path / "theories",
    )
    assert (docs / "theories" / "old-theory.html").is_file()

    # Rename the theory file and rebuild — the old page must be swept.
    (tmp_path / "theories" / "old-theory.md").unlink()
    (tmp_path / "theories" / "new-theory.md").write_text(
        _theory(name="new-theory", baseline="bench-a", treatment="bench-b")
    )
    build(
        results_dir=tmp_path / "results",
        docs_dir=docs,
        benchmarks_dir=tmp_path / "benchmarks",
        theories_dir=tmp_path / "theories",
    )
    assert not (docs / "theories" / "old-theory.html").exists()
    assert (docs / "theories" / "new-theory.html").is_file()


def test_build_with_malformed_theory_is_warned_not_fatal(tmp_path: Path, capsys):
    """A bad theory file shouldn't abort the whole site build —
    the rest of the run records still need to publish."""
    from tools.build_site import build

    _make_repo(tmp_path)
    (tmp_path / "theories" / "bad.md").write_text("no frontmatter here\n")
    (tmp_path / "results" / "bench-a").mkdir()
    (tmp_path / "results" / "bench-a" / "r.json").write_text(json.dumps(SAMPLE_RUN))

    docs = tmp_path / "docs"
    build(
        results_dir=tmp_path / "results",
        docs_dir=docs,
        benchmarks_dir=tmp_path / "benchmarks",
        theories_dir=tmp_path / "theories",
    )
    # Site index still wrote.
    assert (docs / "index.html").is_file()
    # And the run page for SAMPLE_RUN landed.
    assert any(p.suffix == ".html" for p in (docs / "runs").iterdir())
    # Capture once so capsys works in deterministic state — content not
    # asserted (order varies); just verify the build didn't crash.
    _ = capsys.readouterr()
