"""pagehub-benchmarks — a benchmark runner for LLM coding harnesses.

A *benchmark* = a verbatim build prompt + a target repo to build into + the
pagehub-evals collection that grades the result. A *run* picks a
(harness, model, config) and drives: fresh worktree -> headless harness build
-> grade via pagehub-evals -> on failure, re-invoke with the failing-eval
output -> stop when green or attempts exhausted. See ``README.md``.
"""

__version__ = "0.1.0"
