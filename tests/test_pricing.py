from __future__ import annotations

import pytest

from pagehub_benchmarks.config import ModelPrice, load_pricing
from pagehub_benchmarks.runner.pricing import cost_usd


def test_cost_usd_math():
    price = ModelPrice(input=15.0, output=75.0, cache_write=18.75, cache_read=1.5)
    # 1,000,000 input tokens == exactly $15
    assert cost_usd(price, input_tokens=1_000_000, output_tokens=0) == pytest.approx(15.0)
    # mixed
    got = cost_usd(
        price,
        input_tokens=200_000,
        output_tokens=50_000,
        cache_creation_tokens=400_000,
        cache_read_tokens=1_000_000,
    )
    expected = (15.0 * 200_000 + 75.0 * 50_000 + 18.75 * 400_000 + 1.5 * 1_000_000) / 1_000_000
    assert got == pytest.approx(expected)


def test_zero_usage_is_zero():
    price = ModelPrice(input=15.0, output=75.0, cache_write=18.75, cache_read=1.5)
    assert cost_usd(price, input_tokens=0, output_tokens=0) == 0.0


def test_shipped_pricing_table_loads_and_has_opus():
    table = load_pricing()
    assert "claude-opus-4-7" in table
    opus = table["claude-opus-4-7"]
    # current Anthropic Opus rates
    assert opus.input == 15.0
    assert opus.output == 75.0
    assert opus.cache_write == 18.75
    assert opus.cache_read == 1.5
