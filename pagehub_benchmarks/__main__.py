"""``python -m pagehub_benchmarks`` — benchmark runner CLI.

    python -m pagehub_benchmarks list
    python -m pagehub_benchmarks run <benchmark> [--harness H] [--model M]
        [--config k=v ...] [--max-attempts N] [--results-dir DIR]
        [--worktrees-dir DIR] [--no-serve] [--dry-run]
    python -m pagehub_benchmarks render-prompt <benchmark>
        Render <benchmark>'s build prompt (Jinja2 + fixture fetch over HTTP)
        to stdout. For smoke-checking that {{ grader_fixture }} actually
        substitutes without burning tokens on a real harness invocation.

``--dry-run`` sanity-checks YAML + prompt + grader wiring + pricing without
calling the harness or pagehub-evals (the same check ``make run ... DRY_RUN=1``
runs). A real run builds the target repo and grades it — that costs tokens.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from pagehub_benchmarks.config import (
    BENCHMARKS_DIR,
    ConfigError,
    load_benchmark,
)
from pagehub_benchmarks.runner.run import dry_run_report, run_benchmark


def _parse_config_overrides(items: list[str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for raw in items or []:
        if "=" not in raw:
            raise SystemExit(f"--config expects k=v, got {raw!r}")
        k, v = raw.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _cmd_list(_args: argparse.Namespace) -> int:
    if not BENCHMARKS_DIR.is_dir():
        print("(no benchmarks/ directory)")
        return 0
    found = sorted(p.stem for p in BENCHMARKS_DIR.glob("*.yaml"))
    if not found:
        print("(no benchmarks defined)")
        return 0
    for name in found:
        try:
            spec = load_benchmark(name)
            print(f"{name}\t{spec.description or ''}")
        except ConfigError as exc:
            print(f"{name}\t<invalid: {exc}>")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    if args.dry_run:
        spec = load_benchmark(args.benchmark)
        if args.max_attempts is not None:
            import dataclasses

            spec = dataclasses.replace(spec, max_attempts=args.max_attempts)
        for line in dry_run_report(spec):
            print(line)
        print("dry-run OK")
        return 0
    paths = run_benchmark(
        args.benchmark,
        harness=args.harness,
        model=args.model,
        config_overrides=_parse_config_overrides(args.config),
        max_attempts=args.max_attempts,
        results_dir=args.results_dir,
        worktrees_dir=args.worktrees_dir,
        serve=not args.no_serve,
        build_site=not args.no_build_site,
    )
    print(f"wrote {len(paths)} run record(s)")
    return 0


def _cmd_render_prompt(args: argparse.Namespace) -> int:
    from pagehub_benchmarks.runner.fixture_fetch import fixture_fetcher_from_env
    from pagehub_benchmarks.runner.prompt_render import render_prompt

    spec = load_benchmark(args.benchmark)
    fetcher = fixture_fetcher_from_env()
    rendered = render_prompt(spec, fetcher=fetcher)
    if rendered.unused_vars and not args.quiet:
        print(
            f"# (warning: template_vars declared but not referenced: {rendered.unused_vars})",
            file=sys.stderr,
        )
    sys.stdout.write(rendered.text)
    if not rendered.text.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _cmd_site(args: argparse.Namespace) -> int:
    import sys

    from pagehub_benchmarks.config import REPO_ROOT

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from tools.build_site import build as build_site

    out = build_site(results_dir=args.results_dir, docs_dir=args.docs_dir)
    print(f"wrote site to {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pagehub_benchmarks", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list defined benchmarks").set_defaults(func=_cmd_list)

    r = sub.add_parser("run", help="run a benchmark's harness matrix")
    r.add_argument("benchmark", help="benchmark name (benchmarks/<name>.yaml) or a path")
    r.add_argument("--harness", help="only this harness from the matrix")
    r.add_argument("--model", help="only this model from the matrix")
    r.add_argument("--config", action="append", metavar="k=v", help="override a config key (repeatable)")
    r.add_argument("--max-attempts", type=int, help="override max_attempts")
    r.add_argument("--results-dir", help="where to write run records (default: results/)")
    r.add_argument("--worktrees-dir", help="where to make build worktrees (default: .worktrees/)")
    r.add_argument("--no-serve", action="store_true", help="don't try to `make up` the built service before grading")
    r.add_argument("--no-build-site", action="store_true", help="don't regenerate docs/ after the run")
    r.add_argument("--dry-run", action="store_true", help="validate wiring only; no harness, no pagehub-evals")
    r.set_defaults(func=_cmd_run)

    s = sub.add_parser("site", help="regenerate the static results site (docs/) from results/")
    s.add_argument("--results-dir", help="run records to read (default: results/)")
    s.add_argument("--docs-dir", help="output dir (default: docs/)")
    s.set_defaults(func=_cmd_site)

    rp = sub.add_parser(
        "render-prompt",
        help="render a benchmark's Jinja2 build prompt to stdout (no harness call)",
    )
    rp.add_argument("benchmark", help="benchmark name (benchmarks/<name>.yaml) or a path")
    rp.add_argument("--quiet", "-q", action="store_true", help="suppress the unused-vars warning on stderr")
    rp.set_defaults(func=_cmd_render_prompt)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, FileNotFoundError) as exc:
        # FixtureFetchError and PromptRenderError subclass ConfigError, so
        # they ride this same envelope (no need to list them explicitly).
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
