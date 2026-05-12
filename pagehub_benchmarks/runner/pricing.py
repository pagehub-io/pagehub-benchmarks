"""Token-count -> USD, from ``pricing.yaml`` (USD per 1,000,000 tokens)."""

from __future__ import annotations

from pagehub_benchmarks.config import ModelPrice

_PER = 1_000_000


def cost_usd(
    price: ModelPrice,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """USD for one model's token usage. Cache writes/reads price separately."""
    total = (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_creation_tokens * price.cache_write
        + cache_read_tokens * price.cache_read
    )
    return round(total / _PER, 6)
