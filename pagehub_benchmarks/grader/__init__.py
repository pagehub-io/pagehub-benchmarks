"""Grader: thin pagehub-evals client that turns a built repo into a verdict."""

from pagehub_benchmarks.grader.client import EvalsGrader, GraderError, GraderResult

__all__ = ["EvalsGrader", "GraderResult", "GraderError"]
