"""Loading + validating benchmark definitions and the pricing table.

A benchmark lives in ``benchmarks/<name>.yaml``; its build prompt in
``prompts/<name>.md``; the grader's fixture bundle in the pagehub-evals repo
checkout (path resolved relative to ``PAGEHUB_EVALS_REPO``, default
``~/github/pagehub-io/pagehub-evals``). Pricing lives in ``pricing.yaml`` at
the repo root: USD per 1,000,000 tokens, per model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Repo root = parent of this package directory.
REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS_DIR = REPO_ROOT / "benchmarks"
PROMPTS_DIR = REPO_ROOT / "prompts"
PRICING_FILE = REPO_ROOT / "pricing.yaml"
DEFAULT_EVALS_BASE_URL = "http://localhost:8002"
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_PAGEHUB_EVALS_REPO = "~/github/pagehub-io/pagehub-evals"


class ConfigError(ValueError):
    """A benchmark YAML, prompt file, or pricing table is malformed."""


def pagehub_evals_repo() -> Path:
    raw = os.environ.get("PAGEHUB_EVALS_REPO", DEFAULT_PAGEHUB_EVALS_REPO)
    return Path(raw).expanduser()


# --------------------------------------------------------------------------
# benchmark spec


@dataclass(frozen=True)
class HarnessSpec:
    harness: str
    model: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraderSpec:
    evals_base_url: str
    fixture_bundle: str  # path within the pagehub-evals repo
    collection: str
    env: dict[str, str] = field(default_factory=dict)

    @property
    def fixture_bundle_path(self) -> Path:
        return pagehub_evals_repo() / self.fixture_bundle


@dataclass(frozen=True)
class BenchmarkSpec:
    name: str
    description: str
    target_repo: str
    target_start: str  # "empty" | commit/tag/branch
    build_prompt_file: str  # path relative to repo root
    grader: GraderSpec
    max_attempts: int
    harnesses: list[HarnessSpec]
    source_path: Path
    # Extra variables a benchmark can declare to feed its Jinja2 prompt
    # template. Auto-vars (benchmark_name, target_repo, target_port,
    # pagehub_evals_url, grader_fixture) are always available; this map
    # supplements them. Reserved-name collisions are caught at render time.
    template_vars: dict[str, str] = field(default_factory=dict)

    @property
    def build_prompt_path(self) -> Path:
        p = Path(self.build_prompt_file)
        return p if p.is_absolute() else REPO_ROOT / p

    def read_prompt(self) -> str:
        path = self.build_prompt_path
        if not path.is_file():
            raise ConfigError(f"build_prompt_file not found: {path}")
        text = path.read_text().strip()
        if not text:
            raise ConfigError(f"build_prompt_file is empty: {path}")
        return text


def _require(d: dict[str, Any], key: str, where: str) -> Any:
    if key not in d or d[key] in (None, ""):
        raise ConfigError(f"{where}: missing required key {key!r}")
    return d[key]


def parse_benchmark(data: dict[str, Any], source_path: Path) -> BenchmarkSpec:
    if not isinstance(data, dict):
        raise ConfigError(f"{source_path}: top level must be a mapping")
    where = str(source_path)

    grader_raw = _require(data, "grader", where)
    if not isinstance(grader_raw, dict):
        raise ConfigError(f"{where}: 'grader' must be a mapping")
    grader = GraderSpec(
        evals_base_url=str(grader_raw.get("evals_base_url") or DEFAULT_EVALS_BASE_URL).rstrip("/"),
        fixture_bundle=str(_require(grader_raw, "fixture_bundle", f"{where}:grader")),
        collection=str(_require(grader_raw, "collection", f"{where}:grader")),
        env={str(k): str(v) for k, v in (grader_raw.get("env") or {}).items()},
    )

    harnesses_raw = _require(data, "harnesses", where)
    if not isinstance(harnesses_raw, list) or not harnesses_raw:
        raise ConfigError(f"{where}: 'harnesses' must be a non-empty list (the matrix)")
    harnesses: list[HarnessSpec] = []
    for i, h in enumerate(harnesses_raw):
        if not isinstance(h, dict):
            raise ConfigError(f"{where}: harnesses[{i}] must be a mapping")
        harnesses.append(
            HarnessSpec(
                harness=str(_require(h, "harness", f"{where}:harnesses[{i}]")),
                model=str(_require(h, "model", f"{where}:harnesses[{i}]")),
                config={str(k): v for k, v in (h.get("config") or {}).items()},
            )
        )

    max_attempts = data.get("max_attempts", DEFAULT_MAX_ATTEMPTS)
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ConfigError(f"{where}: 'max_attempts' must be a positive int")

    tv_raw = data.get("template_vars") or {}
    if not isinstance(tv_raw, dict):
        raise ConfigError(f"{where}: 'template_vars' must be a mapping")
    template_vars = {str(k): str(v) for k, v in tv_raw.items()}

    return BenchmarkSpec(
        name=str(_require(data, "name", where)),
        description=str(data.get("description") or ""),
        target_repo=str(_require(data, "target_repo", where)),
        target_start=str(data.get("target_start") or "empty"),
        build_prompt_file=str(_require(data, "build_prompt_file", where)),
        grader=grader,
        max_attempts=max_attempts,
        harnesses=harnesses,
        source_path=source_path,
        template_vars=template_vars,
    )


def load_benchmark(name_or_path: str) -> BenchmarkSpec:
    p = Path(name_or_path)
    if p.suffix in (".yaml", ".yml") and p.is_file():
        path = p
    else:
        path = BENCHMARKS_DIR / f"{name_or_path}.yaml"
    if not path.is_file():
        raise ConfigError(f"benchmark not found: {path}")
    data = yaml.safe_load(path.read_text())
    return parse_benchmark(data, path.resolve())


# --------------------------------------------------------------------------
# pricing


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1,000,000 tokens."""

    input: float
    output: float
    cache_write: float
    cache_read: float


def load_pricing(path: Path | None = None) -> dict[str, ModelPrice]:
    path = path or PRICING_FILE
    if not path.is_file():
        raise ConfigError(f"pricing file not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    models_raw = data.get("models")
    if not isinstance(models_raw, dict) or not models_raw:
        raise ConfigError(f"{path}: expected a non-empty 'models' mapping")
    out: dict[str, ModelPrice] = {}
    for model, rates in models_raw.items():
        if not isinstance(rates, dict):
            raise ConfigError(f"{path}: models.{model} must be a mapping")
        try:
            out[str(model)] = ModelPrice(
                input=float(rates["input"]),
                output=float(rates["output"]),
                cache_write=float(rates["cache_write"]),
                cache_read=float(rates["cache_read"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(
                f"{path}: models.{model} needs numeric input/output/cache_write/cache_read"
            ) from exc
    return out
