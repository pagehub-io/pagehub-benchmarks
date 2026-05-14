"""Render a benchmark's build prompt as a Jinja2 template.

The prompt file (``prompts/<name>.md``) is treated as a Jinja2 template. Five
variables are *always* available regardless of YAML; the benchmark may declare
additional ``template_vars`` to feed the renderer.

Auto-vars (resolved here from the :class:`BenchmarkSpec`):

- ``benchmark_name``       — the benchmark's name.
- ``target_repo``          — the git URL of the target repo.
- ``target_port``          — extracted from ``grader.env``: the port off the
                             ``*_url`` whose key starts with the benchmark
                             name, else the first non-``pagehub-browser``
                             ``*_url``. ``""`` if none can be inferred.
- ``pagehub_evals_url``    — ``grader.evals_base_url``.
- ``grader_fixture``       — the grader's fixture bundle, fetched over HTTP
                             from ``GET {evals_base_url}/v1/fixtures/{stem}``
                             and pretty-printed (``json.dumps(indent=2)``).

An unresolved ``{{ foo }}`` (foo neither auto nor declared) raises
:class:`PromptRenderError` immediately at render time — not later when the
harness chokes on a literal ``{{ foo }}`` in its prompt. Declared-but-unused
vars are fine; they show up in the returned :class:`RenderedPrompt` as
``unused_vars`` so the runner log can surface a warning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import jinja2

from pagehub_benchmarks.config import BenchmarkSpec, ConfigError
from pagehub_benchmarks.runner.fixture_fetch import FixtureFetcher


class PromptRenderError(ConfigError):
    """A prompt template references an undefined variable, or fixture fetch failed."""


_AUTO_VAR_NAMES = (
    "benchmark_name",
    "target_repo",
    "target_port",
    "pagehub_evals_url",
    "grader_fixture",
)


@dataclass
class RenderedPrompt:
    text: str
    template_vars: dict[str, str]
    unused_vars: list[str] = field(default_factory=list)


def _infer_target_port(spec: BenchmarkSpec) -> str:
    env = spec.grader.env or {}
    candidates: list[tuple[str, str]] = [
        (k, v) for k, v in env.items() if k.endswith("_url") and isinstance(v, str)
    ]
    if not candidates:
        return ""

    def _port(url: str) -> str:
        try:
            p = urlparse(url).port
        except ValueError:
            return ""
        return str(p) if p is not None else ""

    prefix = spec.name
    for k, v in candidates:
        if k.startswith(prefix):
            port = _port(v)
            if port:
                return port
    for k, v in candidates:
        if k.startswith("pagehub-browser"):
            continue
        port = _port(v)
        if port:
            return port
    return _port(candidates[0][1])


def _auto_vars(spec: BenchmarkSpec, fetcher: FixtureFetcher) -> dict[str, str]:
    fixture = fetcher.fetch(spec)
    return {
        "benchmark_name": spec.name,
        "target_repo": spec.target_repo,
        "target_port": _infer_target_port(spec),
        "pagehub_evals_url": spec.grader.evals_base_url,
        "grader_fixture": fixture,
    }


_VAR_RE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)")


def _referenced_names(template_source: str) -> set[str]:
    """Coarse pass for the ``unused_vars`` warning. Strict undefined detection
    is enforced by Jinja's :class:`StrictUndefined` mode at render time."""
    return set(_VAR_RE.findall(template_source))


def render_prompt(spec: BenchmarkSpec, *, fetcher: FixtureFetcher) -> RenderedPrompt:
    """Render ``spec``'s build prompt. Raises :class:`PromptRenderError` on
    an unresolved variable, fixture-fetch failure, or template syntax error."""
    template_source = spec.read_prompt()

    auto = _auto_vars(spec, fetcher)
    custom = dict(spec.template_vars)

    overlap = sorted(set(auto) & set(custom))
    if overlap:
        raise PromptRenderError(
            f"template_vars override reserved auto-var(s): {overlap} "
            f"(reserved: {list(_AUTO_VAR_NAMES)})"
        )
    merged: dict[str, str] = {**auto, **custom}

    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    try:
        tmpl = env.from_string(template_source)
        text = tmpl.render(**merged)
    except jinja2.UndefinedError as exc:
        raise PromptRenderError(
            f"prompt template {spec.build_prompt_path} references undefined variable: {exc.message}"
        ) from exc
    except jinja2.TemplateSyntaxError as exc:
        raise PromptRenderError(
            f"prompt template {spec.build_prompt_path} has a syntax error on line "
            f"{exc.lineno}: {exc.message}"
        ) from exc

    referenced = _referenced_names(template_source)
    unused = sorted(k for k in custom if k not in referenced)

    return RenderedPrompt(text=text, template_vars=merged, unused_vars=unused)


__all__ = ["PromptRenderError", "RenderedPrompt", "render_prompt"]
