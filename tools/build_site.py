"""Generate the static results site from ``results/**/*.json`` into ``docs/``.

No SPA framework, no JS build step — Jinja2 templates + a few lines of vanilla
JS for client-side table sort. Run via ``make site`` or
``python -m tools.build_site``; the runner also calls :func:`build` after each
run (``--build-site``, on by default).

Output:
  docs/index.html                 all runs (sortable) + per-benchmark summary
  docs/runs/<run-id>.html         one run: metrics, per-attempt table, links
  docs/benchmarks/<name>.html     one benchmark: definition + its runs
  docs/results/<benchmark>/*.json copies of the raw run records (linked from
                                  run pages so they're reachable on the site)
  docs/style.css                  copied from static/style.css

GitHub URLs for the "Links" sections come from env (defaults below):
  PAGEHUB_BENCHMARKS_REPO_URL   https://github.com/pagehub-io/pagehub-benchmarks
  PAGEHUB_EVALS_REPO_URL        https://github.com/pagehub-io/pagehub-evals
"""

from __future__ import annotations

import json
import os
import re
import shutil
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
STATIC_DIR = REPO_ROOT / "static"
DEFAULT_RESULTS_DIR = REPO_ROOT / "results"
DEFAULT_DOCS_DIR = REPO_ROOT / "docs"
DEFAULT_BENCHMARKS_DIR = REPO_ROOT / "benchmarks"
DEFAULT_THEORIES_DIR = REPO_ROOT / "theories"

BENCHMARKS_REPO_URL = os.environ.get(
    "PAGEHUB_BENCHMARKS_REPO_URL", "https://github.com/pagehub-io/pagehub-benchmarks"
).rstrip("/")
EVALS_REPO_URL = os.environ.get(
    "PAGEHUB_EVALS_REPO_URL", "https://github.com/pagehub-io/pagehub-evals"
).rstrip("/")
BLOB_BRANCH = os.environ.get("PAGEHUB_BENCHMARKS_BLOB_BRANCH", "main")

_SLUG = re.compile(r"[^A-Za-z0-9._-]+")

# Files under docs/ that survive an orphan-sweep even though build_site.py
# didn't write them. These are drop-ins an operator may add by hand for
# GitHub Pages (Jekyll bypass marker, custom-domain config, empty-dir marker).
_PRESERVED_BASENAMES = frozenset({".nojekyll", ".gitkeep", "CNAME"})


def _slug(s: str) -> str:
    return _SLUG.sub("-", s).strip("-") or "x"


def github_repo_url(remote_or_url: str) -> str:
    """``git@github.com:org/repo.git`` / ``https://github.com/org/repo`` -> canonical https URL."""
    s = (remote_or_url or "").strip()
    if s.startswith("git@github.com:"):
        s = "https://github.com/" + s[len("git@github.com:") :]
    if s.startswith("ssh://git@github.com/"):
        s = "https://github.com/" + s[len("ssh://git@github.com/") :]
    if s.endswith(".git"):
        s = s[:-4]
    return s.rstrip("/")


def _repo_short(remote_or_url: str) -> str:
    u = github_repo_url(remote_or_url)
    return u.replace("https://github.com/", "") or u


def _config_str(config: dict[str, Any] | None) -> str:
    if not config:
        return "default"
    return " ".join(f"{k}={v}" for k, v in sorted(config.items()))


def _fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, AttributeError):
        return iso or ""


def _is_jsonish(value: str) -> bool:
    """Cheap heuristic: does ``value`` look like JSON we should pretty-print?

    Used by the template-vars table to decide between an inline ``<code>`` and
    a collapsible ``<details><pre>``. We don't try to be clever — anything
    that starts with ``{`` or ``[`` and parses as JSON gets the JSON
    treatment.
    """
    s = value.lstrip()
    if not s or s[0] not in "{[":
        return False
    try:
        json.loads(value)
    except (ValueError, TypeError):
        return False
    return True


def _template_var_rows(template_vars: dict[str, str]) -> list[dict]:
    """Project ``{name: value}`` into rows the run-detail template renders.

    Each row carries presentational hints: whether the value is multi-line
    (folds into a ``<details>``) and whether it parses as JSON (gets the
    pretty-printed JSON branch). Single-line non-JSON values stay inline.
    """
    rows: list[dict] = []
    for name in sorted(template_vars):
        value = str(template_vars.get(name) or "")
        multiline = "\n" in value or len(value) > 200
        rows.append({
            "name": name,
            "value": value,
            "is_multiline": multiline,
            "is_json": _is_jsonish(value),
            "byte_length": len(value.encode("utf-8")),
        })
    return rows


# --------------------------------------------------------------------------
# benchmark metadata (from benchmarks/<name>.yaml, best-effort)


def _benchmark_meta(name: str, benchmarks_dir: Path) -> dict[str, Any]:
    yaml_path = benchmarks_dir / f"{name}.yaml"
    prompt_file = f"prompts/{name}.md"
    fixture_bundle = f"fixtures/{name}.json"
    description = ""
    target_repo = ""
    target_start = "empty"
    collection = name
    if yaml_path.is_file():
        try:
            data = yaml.safe_load(yaml_path.read_text()) or {}
            description = str(data.get("description") or "").strip()
            prompt_file = str(data.get("build_prompt_file") or prompt_file)
            target_repo = str(data.get("target_repo") or "")
            target_start = str(data.get("target_start") or "empty")
            grader = data.get("grader") or {}
            fixture_bundle = str(grader.get("fixture_bundle") or fixture_bundle)
            collection = str(grader.get("collection") or name)
        except yaml.YAMLError:
            pass
    return {
        "name": name,
        "slug": _slug(name),
        "description": description,
        "prompt_file": prompt_file,
        "fixture_bundle": fixture_bundle,
        "collection": collection,
        "target_repo": target_repo,
        "target_start": target_start,
        "yaml_url": f"{BENCHMARKS_REPO_URL}/blob/{BLOB_BRANCH}/benchmarks/{name}.yaml",
        "prompt_url": f"{BENCHMARKS_REPO_URL}/blob/{BLOB_BRANCH}/{prompt_file}",
        "fixture_url": f"{EVALS_REPO_URL}/blob/main/{fixture_bundle}",
        "target_repo_url": github_repo_url(target_repo) if target_repo else BENCHMARKS_REPO_URL,
        "target_repo_short": _repo_short(target_repo) if target_repo else "",
    }


# --------------------------------------------------------------------------
# load + enrich run records


def load_runs(results_dir: Path, benchmarks_dir: Path) -> tuple[list[dict], dict[str, dict]]:
    runs: list[dict] = []
    meta_cache: dict[str, dict] = {}
    if not results_dir.is_dir():
        return runs, meta_cache
    for path in sorted(results_dir.rglob("*.json")):
        try:
            rec = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(rec, dict) or "benchmark" not in rec:
            continue
        bench = str(rec["benchmark"])
        meta = meta_cache.get(bench)
        if meta is None:
            meta = _benchmark_meta(bench, benchmarks_dir)
            meta_cache[bench] = meta

        run_id = _slug(path.stem)
        rel_results = path.relative_to(results_dir).as_posix()
        target_repo = str(rec.get("target_repo") or meta["target_repo"])
        target_repo_url = github_repo_url(target_repo) if target_repo else meta["target_repo_url"]
        built_sha = rec.get("built_git_sha")
        commit_url = (
            f"{target_repo_url}/commit/{built_sha}" if built_sha and target_repo_url.startswith("https://github.com/") else None
        )
        passed = bool(rec.get("passed"))
        attempts = int(rec.get("attempts") or 0)
        wt = str(rec.get("worktree_path") or "")
        pushed_commit = rec.get("pushed_commit")
        pushed_commit_url = (
            f"{target_repo_url}/commit/{pushed_commit}"
            if pushed_commit and target_repo_url.startswith("https://github.com/")
            else None
        )
        pushed_branch = rec.get("pushed_branch")
        # The branch's last segment is the only thing that differs between
        # runs of the same config; show it as a compact label in the index.
        pushed_branch_short = (
            pushed_branch.rsplit("/", 1)[-1] if pushed_branch else ""
        )
        rec.update(
            {
                "run_id": run_id,
                "benchmark_slug": meta["slug"],
                "config_str": _config_str(rec.get("config")),
                "date_display": _fmt_date(str(rec.get("started_at") or "")),
                "passed": passed,
                "attempts_display": str(attempts) if passed else "—",
                "results_url": f"results/{rel_results}",
                "results_src_path": str(path),
                "target_repo_url": target_repo_url,
                "target_repo_short": _repo_short(target_repo) if target_repo else "",
                "commit_url": commit_url,
                "worktree_basename": Path(wt).name if wt else "",
                # benchmark-derived links
                "yaml_url": meta["yaml_url"],
                "prompt_url": meta["prompt_url"],
                "prompt_file": meta["prompt_file"],
                "fixture_url": meta["fixture_url"],
                "fixture_bundle": meta["fixture_bundle"],
                "max_attempts": int(rec.get("max_attempts") or attempts or 0),
                # built-code-push fields (None when the record predates the push feature)
                "pushed_branch": pushed_branch,
                "pushed_branch_url": rec.get("pushed_branch_url"),
                "pushed_branch_short": pushed_branch_short,
                "pushed_commit": pushed_commit,
                "pushed_commit_url": pushed_commit_url,
                "pushed_to_default_branch": bool(rec.get("pushed_to_default_branch")),
                "pushed_at": rec.get("pushed_at"),
                "push_error": rec.get("push_error"),
            }
        )
        rec.setdefault("total_input_tokens", 0)
        rec.setdefault("total_output_tokens", 0)
        rec.setdefault("total_cache_tokens", 0)
        rec.setdefault("cost_usd", 0.0)
        rec.setdefault("total_wall_time_seconds", 0.0)
        rec.setdefault("per_attempt", [])
        # Prompt-template snapshot — present on records from the Jinja2
        # rendering feature onward; older records (eval-chess-backend run
        # #1, eval-chess-frontend run #1) don't carry them and just render
        # nothing in their "Template vars" / "Rendered prompt" sections.
        rec.setdefault("rendered_prompt", "")
        rec.setdefault("template_vars", {})
        rec["template_var_rows"] = _template_var_rows(rec.get("template_vars") or {})
        # Per-attempt rendered_prompt is also opt-in; default to empty string
        # so the template can `{% if a.rendered_prompt %}` it.
        for a in rec.get("per_attempt") or []:
            a.setdefault("rendered_prompt", "")
        runs.append(rec)
    # newest first
    runs.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
    return runs, meta_cache


def _benchmark_summary(name: str, meta: dict, runs_for_bench: list[dict]) -> dict[str, Any]:
    passing = [r for r in runs_for_bench if r["passed"]]
    pass_attempts = sorted(int(r["attempts"]) for r in passing)
    cheapest = min(passing, key=lambda r: float(r.get("cost_usd") or 0.0)) if passing else None
    n = len(runs_for_bench)
    return {
        **meta,
        "run_count": n,
        "pass_rate_display": (f"{len(passing)}/{n} ({100 * len(passing) // n}%)" if n else "—"),
        "best_attempts_display": (str(pass_attempts[0]) if pass_attempts else "—"),
        "median_attempts_display": (
            (f"{statistics.median(pass_attempts):g}") if pass_attempts else "—"
        ),
        "cheapest": cheapest,
        "runs": runs_for_bench,  # already newest-first from load_runs ordering
    }


# --------------------------------------------------------------------------
# render


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def build(
    results_dir: Path | str | None = None,
    docs_dir: Path | str | None = None,
    benchmarks_dir: Path | str | None = None,
    theories_dir: Path | str | None = None,
) -> Path:
    results_dir = Path(results_dir) if results_dir else DEFAULT_RESULTS_DIR
    docs_dir = Path(docs_dir) if docs_dir else DEFAULT_DOCS_DIR
    benchmarks_dir = Path(benchmarks_dir) if benchmarks_dir else DEFAULT_BENCHMARKS_DIR
    theories_dir = Path(theories_dir) if theories_dir else DEFAULT_THEORIES_DIR

    runs, meta_cache = load_runs(results_dir, benchmarks_dir)

    # group runs by benchmark
    by_bench: dict[str, list[dict]] = {}
    for r in runs:
        by_bench.setdefault(str(r["benchmark"]), []).append(r)
    # include benchmarks that have a YAML but no runs yet
    if benchmarks_dir.is_dir():
        for p in benchmarks_dir.glob("*.yaml"):
            by_bench.setdefault(p.stem, by_bench.get(p.stem, []))
            meta_cache.setdefault(p.stem, _benchmark_meta(p.stem, benchmarks_dir))
    benchmarks = [
        _benchmark_summary(name, meta_cache.get(name, _benchmark_meta(name, benchmarks_dir)), by_bench.get(name, []))
        for name in sorted(by_bench)
    ]

    env = _env()
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "runs").mkdir(exist_ok=True)
    (docs_dir / "benchmarks").mkdir(exist_ok=True)

    # Track everything this build emits. After all writes, anything else
    # under docs/ is a stale orphan from a prior build (e.g., a renamed
    # benchmark) and gets swept — docs/ is canonical output.
    written: set[Path] = set()

    # static asset
    if STATIC_DIR.is_dir():
        dst = docs_dir / "style.css"
        shutil.copy2(STATIC_DIR / "style.css", dst)
        written.add(dst)

    common = {"repo_url": BENCHMARKS_REPO_URL}

    # Load theories early so the home-page link counter ("theories (N)")
    # can render before _render_theories writes the per-theory files.
    from pagehub_benchmarks.theories import TheoryError as _TheoryError
    from pagehub_benchmarks.theories import load_all_theories as _load_theories
    try:
        loaded_theories = _load_theories(theories_dir)
    except _TheoryError as exc:
        print(f"(theories disabled — {exc})")
        loaded_theories = []

    index_path = docs_dir / "index.html"
    index_path.write_text(
        env.get_template("index.html").render(
            rel="", runs=runs, benchmarks=benchmarks, theories=loaded_theories, **common
        )
    )
    written.add(index_path)

    bench_tmpl = env.get_template("benchmark.html")
    for b in benchmarks:
        bench_path = docs_dir / "benchmarks" / f"{b['slug']}.html"
        bench_path.write_text(bench_tmpl.render(rel="../", b=b, **common))
        written.add(bench_path)

    run_tmpl = env.get_template("run.html")
    seen_ids: set[str] = set()
    for r in runs:
        rid = r["run_id"]
        if rid in seen_ids:
            # extremely unlikely (sub-second same-config collisions) — disambiguate
            i = 2
            while f"{rid}-{i}" in seen_ids:
                i += 1
            rid = f"{rid}-{i}"
            r["run_id"] = rid
        seen_ids.add(rid)
        run_path = docs_dir / "runs" / f"{rid}.html"
        run_path.write_text(run_tmpl.render(rel="../", r=r, **common))
        written.add(run_path)
        # copy the raw record so the in-site JSON link resolves
        dst = docs_dir / "results" / Path(r["results_url"]).relative_to("results")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(r["results_src_path"], dst)
        written.add(dst)

    _render_theories(env, docs_dir, loaded_theories, by_bench, common, written)

    _clean_orphans(docs_dir, written)
    return docs_dir


def _render_theories(
    env: Environment,
    docs_dir: Path,
    theories: list,
    by_bench: dict[str, list[dict]],
    common: dict,
    written: set[Path],
) -> None:
    """Render the theory index + per-theory pages.

    A theory ties a baseline benchmark to a treatment benchmark and shows a
    side-by-side metric comparison. If either side has no runs yet, the
    cells say "no runs yet" instead of trying to project from nothing.
    """
    if not theories:
        # Don't create docs/theories/ at all if there are no theory files —
        # the orphan sweep would just remove it anyway.
        return

    theory_index_tmpl = env.get_template("theory_index.html")
    theory_tmpl = env.get_template("theory.html")
    out_dir = docs_dir / "theories"
    out_dir.mkdir(parents=True, exist_ok=True)

    views = []
    for t in theories:
        baseline_runs = by_bench.get(t.baseline, [])
        treatment_runs = by_bench.get(t.treatment, [])
        cells = _theory_cells(t.metrics, baseline_runs, treatment_runs)
        views.append(
            {
                "theory": t,
                "baseline_run_count": len(baseline_runs),
                "treatment_run_count": len(treatment_runs),
                "cells": cells,
                "body_html": _md_to_html(t.body_markdown),
                "baseline_runs": baseline_runs,
                "treatment_runs": treatment_runs,
            }
        )

    index_path = out_dir / "index.html"
    index_path.write_text(theory_index_tmpl.render(rel="../", theories=views, **common))
    written.add(index_path)

    for v in views:
        tpath = out_dir / f"{v['theory'].slug}.html"
        tpath.write_text(theory_tmpl.render(rel="../", v=v, **common))
        written.add(tpath)


_METRIC_FORMATTERS = {
    "cost_usd": lambda v: f"${float(v):.4f}",
    "total_wall_time_seconds": lambda v: f"{float(v):.0f}s",
    "total_input_tokens": lambda v: f"{int(v):,}",
    "total_output_tokens": lambda v: f"{int(v):,}",
    "total_cache_tokens": lambda v: f"{int(v):,}",
    "attempts": lambda v: str(int(v)),
    "max_attempts": lambda v: str(int(v)),
    "passed": lambda v: "✓" if v else "✗",
}


def _format_metric(metric: str, value):  # noqa: ANN001, ANN201
    fmt = _METRIC_FORMATTERS.get(metric)
    try:
        return fmt(value) if fmt else str(value)
    except (TypeError, ValueError):
        return str(value)


def _theory_cells(metrics: list[str], baseline_runs: list[dict], treatment_runs: list[dict]) -> list[dict]:
    """Project each metric into a baseline-vs-treatment cell.

    Strategy: use the *most recent* run on each side (``load_runs`` returns
    newest-first). If a side has no run, render "no runs yet" — the
    template still shows the row so a reader sees which metric the theory
    cares about and which side is missing.
    """
    b_latest = baseline_runs[0] if baseline_runs else None
    t_latest = treatment_runs[0] if treatment_runs else None
    out: list[dict] = []
    for m in metrics:
        bv = b_latest.get(m) if b_latest else None
        tv = t_latest.get(m) if t_latest else None
        out.append(
            {
                "metric": m,
                "baseline_display": _format_metric(m, bv) if b_latest is not None else "no runs yet",
                "treatment_display": _format_metric(m, tv) if t_latest is not None else "no runs yet",
                "baseline_value": bv,
                "treatment_value": tv,
            }
        )
    return out


def _md_to_html(markdown: str) -> str:
    """No external markdown lib — wrap the body in <pre> and trust the source.

    The theory body is operator-authored markdown; rendering it raw in
    ``<pre>`` keeps formatting predictable without dragging in a markdown
    parser as a dependency. A future iteration can swap in ``markdown-it``
    or similar if richer rendering becomes worth the dep weight.
    """
    return f'<div class="theory-body"><pre>{_escape_html(markdown)}</pre></div>'


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _clean_orphans(docs_dir: Path, written: set[Path]) -> None:
    """Remove files under ``docs_dir`` that ``build()`` didn't just write.

    Keeps the set in ``_PRESERVED_BASENAMES`` (`.nojekyll`, `.gitkeep`,
    `CNAME`) so a hand-placed GitHub Pages config survives rebuilds. Empty
    directories left behind by removals are pruned bottom-up.
    """
    if not docs_dir.is_dir():
        return
    for path in docs_dir.rglob("*"):
        if not path.is_file():
            continue
        if path in written or path.name in _PRESERVED_BASENAMES:
            continue
        path.unlink()
    # bottom-up so a directory whose only children are now-removed orphans
    # also goes
    for path in sorted(docs_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="tools.build_site", description="Build the static results site")
    p.add_argument("--results-dir", default=None)
    p.add_argument("--docs-dir", default=None)
    p.add_argument("--theories-dir", default=None)
    args = p.parse_args(argv)
    out = build(
        results_dir=args.results_dir,
        docs_dir=args.docs_dir,
        theories_dir=args.theories_dir,
    )
    print(f"wrote site to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
