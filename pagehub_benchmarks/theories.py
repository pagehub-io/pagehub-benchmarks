"""Theories — declared hypotheses with attached baseline/treatment benchmarks.

A theory lives in ``theories/<slug>.md`` and looks like:

    ---
    name: eval-fixture-injection
    hypothesis: Injecting the grader fixture into the build prompt reduces
                attempts, total tokens, and wall-time vs a build prompt that
                does not reference the evals.
    baseline: eval-chess-frontend
    treatment: eval-chess-frontend-with-fixture
    metrics: [attempts, total_output_tokens, total_cache_tokens, cost_usd,
              total_wall_time_seconds, passed]
    status: pending
    ---
    ## Background
    ...
    ## Expected outcome
    ...

The frontmatter is YAML between two ``---`` lines; the body is markdown.
``status`` is one of ``pending`` | ``supported`` | ``refuted`` |
``inconclusive`` — updated by hand after runs land, not by the runner.

This module loads / validates theory files and projects them to a
:class:`Theory` dataclass the static-site generator renders against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pagehub_benchmarks.config import REPO_ROOT, ConfigError

THEORIES_DIR = REPO_ROOT / "theories"

VALID_STATUSES = frozenset({"pending", "supported", "refuted", "inconclusive"})

DEFAULT_METRICS = (
    "attempts",
    "total_output_tokens",
    "total_cache_tokens",
    "cost_usd",
    "total_wall_time_seconds",
    "passed",
)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n(.*)$", re.DOTALL)


class TheoryError(ConfigError):
    """A theory file is malformed."""


@dataclass(frozen=True)
class Theory:
    slug: str             # filename stem; the site uses this as the page slug
    name: str             # the ``name:`` field — the hypothesis's human ID
    hypothesis: str       # one-liner
    baseline: str         # benchmark name (a benchmarks/<>.yaml stem)
    treatment: str        # benchmark name (a benchmarks/<>.yaml stem)
    metrics: list[str]    # which run-record metrics to compare side-by-side
    status: str           # pending | supported | refuted | inconclusive
    body_markdown: str    # the prose body after the frontmatter
    source_path: Path


def parse_theory(text: str, source_path: Path) -> Theory:
    """Parse a theory markdown file. Raises :class:`TheoryError` on any
    structural problem so the caller can fail loudly during the site build."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise TheoryError(
            f"{source_path}: missing YAML frontmatter (expected '---\\n…\\n---\\n')"
        )
    fm_yaml, body = m.group(1), m.group(2)
    try:
        fm = yaml.safe_load(fm_yaml) or {}
    except yaml.YAMLError as exc:
        raise TheoryError(f"{source_path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(fm, dict):
        raise TheoryError(f"{source_path}: frontmatter must be a mapping")

    def _required(key: str) -> str:
        v = fm.get(key)
        if not isinstance(v, str) or not v.strip():
            raise TheoryError(f"{source_path}: frontmatter missing required string field {key!r}")
        return v.strip()

    name = _required("name")
    hypothesis = _required("hypothesis")
    baseline = _required("baseline")
    treatment = _required("treatment")
    if baseline == treatment:
        raise TheoryError(
            f"{source_path}: baseline and treatment must differ (got {baseline!r})"
        )

    status = (fm.get("status") or "pending").strip()
    if status not in VALID_STATUSES:
        raise TheoryError(
            f"{source_path}: status={status!r} — must be one of {sorted(VALID_STATUSES)}"
        )

    metrics_raw = fm.get("metrics") or list(DEFAULT_METRICS)
    if not isinstance(metrics_raw, list) or not all(isinstance(m, str) for m in metrics_raw):
        raise TheoryError(f"{source_path}: 'metrics' must be a list of strings")
    metrics: list[str] = [str(m) for m in metrics_raw]
    if not metrics:
        raise TheoryError(f"{source_path}: 'metrics' must declare at least one metric")

    return Theory(
        slug=source_path.stem,
        name=name,
        hypothesis=hypothesis,
        baseline=baseline,
        treatment=treatment,
        metrics=metrics,
        status=status,
        body_markdown=body,
        source_path=source_path.resolve(),
    )


def load_theory(name_or_path: str) -> Theory:
    p = Path(name_or_path)
    path = p if p.suffix == ".md" and p.is_file() else (THEORIES_DIR / f"{name_or_path}.md")
    if not path.is_file():
        raise TheoryError(f"theory not found: {path}")
    return parse_theory(path.read_text(), path)


def load_all_theories(theories_dir: Path | None = None) -> list[Theory]:
    """Load every ``theories/*.md`` (sorted by slug). Missing dir → empty list."""
    root = theories_dir or THEORIES_DIR
    if not root.is_dir():
        return []
    out: list[Theory] = []
    for p in sorted(root.glob("*.md")):
        out.append(parse_theory(p.read_text(), p))
    return out


# --------------------------------------------------------------------------
# the projection the site renders against


@dataclass
class TheoryMetricCell:
    """One row of the baseline-vs-treatment comparison table."""
    metric: str
    baseline_display: str
    treatment_display: str
    baseline_value: float | int | str | None = None
    treatment_value: float | int | str | None = None


@dataclass
class TheoryView:
    """Site-friendly projection of a Theory + its current metrics."""
    theory: Theory
    baseline_run_count: int = 0
    treatment_run_count: int = 0
    cells: list[TheoryMetricCell] = field(default_factory=list)


__all__ = [
    "DEFAULT_METRICS",
    "VALID_STATUSES",
    "Theory",
    "TheoryError",
    "TheoryMetricCell",
    "TheoryView",
    "load_all_theories",
    "load_theory",
    "parse_theory",
]
