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

BENCHMARKS_REPO_URL = os.environ.get(
    "PAGEHUB_BENCHMARKS_REPO_URL", "https://github.com/pagehub-io/pagehub-benchmarks"
).rstrip("/")
EVALS_REPO_URL = os.environ.get(
    "PAGEHUB_EVALS_REPO_URL", "https://github.com/pagehub-io/pagehub-evals"
).rstrip("/")
BLOB_BRANCH = os.environ.get("PAGEHUB_BENCHMARKS_BLOB_BRANCH", "main")

_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


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
            }
        )
        rec.setdefault("total_input_tokens", 0)
        rec.setdefault("total_output_tokens", 0)
        rec.setdefault("total_cache_tokens", 0)
        rec.setdefault("cost_usd", 0.0)
        rec.setdefault("total_wall_time_seconds", 0.0)
        rec.setdefault("per_attempt", [])
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


def build(results_dir: Path | str | None = None, docs_dir: Path | str | None = None,
          benchmarks_dir: Path | str | None = None) -> Path:
    results_dir = Path(results_dir) if results_dir else DEFAULT_RESULTS_DIR
    docs_dir = Path(docs_dir) if docs_dir else DEFAULT_DOCS_DIR
    benchmarks_dir = Path(benchmarks_dir) if benchmarks_dir else DEFAULT_BENCHMARKS_DIR

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

    # static asset
    if STATIC_DIR.is_dir():
        shutil.copy2(STATIC_DIR / "style.css", docs_dir / "style.css")

    common = {"repo_url": BENCHMARKS_REPO_URL}

    (docs_dir / "index.html").write_text(
        env.get_template("index.html").render(rel="", runs=runs, benchmarks=benchmarks, **common)
    )

    bench_tmpl = env.get_template("benchmark.html")
    for b in benchmarks:
        (docs_dir / "benchmarks" / f"{b['slug']}.html").write_text(
            bench_tmpl.render(rel="../", b=b, **common)
        )

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
        (docs_dir / "runs" / f"{rid}.html").write_text(run_tmpl.render(rel="../", r=r, **common))
        # copy the raw record so the in-site JSON link resolves
        dst = docs_dir / "results" / Path(r["results_url"]).relative_to("results")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(r["results_src_path"], dst)

    return docs_dir


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="tools.build_site", description="Build the static results site")
    p.add_argument("--results-dir", default=None)
    p.add_argument("--docs-dir", default=None)
    args = p.parse_args(argv)
    out = build(results_dir=args.results_dir, docs_dir=args.docs_dir)
    print(f"wrote site to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
