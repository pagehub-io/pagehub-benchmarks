"""The run record: per-attempt breakdown + totals, plus the on-disk filename.

One JSON file per run, append-only, written under
``results/<benchmark>/<harness>__<model>__<config-slug>__<ISO8601>.json``.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SLUG_SAFE = re.compile(r"[^A-Za-z0-9.]+")


def _slug_part(value: Any) -> str:
    s = _SLUG_SAFE.sub("-", str(value)).strip("-")
    return s or "x"


def config_slug(config: dict[str, Any] | None) -> str:
    """Stable, filesystem-safe slug for a harness config dict.

    ``{}`` -> ``default``; ``{"effort": "xhigh"}`` -> ``effort-xhigh``;
    ``{"effort": "xhigh", "temperature": 0.7}`` -> ``effort-xhigh_temperature-0.7``.
    Keys are sorted so the slug is deterministic.
    """
    if not config:
        return "default"
    parts = [f"{_slug_part(k)}-{_slug_part(v)}" for k, v in sorted(config.items())]
    return "_".join(parts)


def _fs_timestamp(dt: datetime) -> str:
    # ISO-8601 in UTC with ':' swapped for '-' so it is a legal filename segment.
    # A naive datetime is treated as already-UTC (not local) for determinism.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


def result_filename(
    harness: str, model: str, config: dict[str, Any] | None, when: datetime
) -> str:
    return f"{_slug_part(harness)}__{_slug_part(model)}__{config_slug(config)}__{_fs_timestamp(when)}.json"


@dataclass
class AttemptRecord:
    attempt: int
    input_tokens: int
    output_tokens: int
    cache_tokens: int
    wall_time_seconds: float
    grader_passed: bool
    grader_failures: list[str] = field(default_factory=list)


@dataclass
class RunRecord:
    benchmark: str
    harness: str
    model: str
    config: dict[str, Any]
    started_at: str
    finished_at: str
    target_repo: str
    target_start: str
    built_git_sha: str | None
    worktree_path: str
    max_attempts: int
    attempts: int  # the attempt # that went green, or the cap if never green
    passed: bool
    total_input_tokens: int
    total_output_tokens: int
    total_cache_tokens: int
    cost_usd: float
    total_wall_time_seconds: float
    per_attempt: list[AttemptRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    def write(self, results_dir: Path) -> Path:
        out_dir = Path(results_dir) / self.benchmark
        out_dir.mkdir(parents=True, exist_ok=True)
        # started_at is ISO-8601; reuse it for the filename timestamp.
        when = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
        path = out_dir / result_filename(self.harness, self.model, self.config, when)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")
        return path
