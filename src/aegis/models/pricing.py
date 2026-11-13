"""A small local price table for cost estimation.

Providers report token counts, not dollars, so the router's cost logging
needs prices from somewhere. This table holds list prices in USD per
1,000,000 tokens as (input, output), for the models the adapters know by
name. Every figure computed from it is an estimate: adapters flag their
responses with cost_is_estimate=True and the trace carries that flag, so
an estimate can never be mistaken for a billed amount.

Prices are list prices noted when this table was written (2026-11-13).
They will drift; the table is data, and updating it is a one-line change
per model. An unknown model prices at zero with the estimate flag set, so
cost logging degrades visibly instead of inventing a number.
"""

from __future__ import annotations

# model name -> (input USD per 1M tokens, output USD per 1M tokens)
PRICES_USD_PER_1M: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-4-1": (15.0, 75.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # OpenAI
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    # Local and open-weight models served on own hardware: no per-token
    # provider price. Zero here means "no metered cost", not "free to run".
    "llama3.1": (0.0, 0.0),
    "llama3.1:8b": (0.0, 0.0),
    "qwen2.5": (0.0, 0.0),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated USD cost for one call, from the price table.

    Always an estimate, even when the table has the model; the caller is
    responsible for carrying cost_is_estimate=True into the trace.
    """
    prices = PRICES_USD_PER_1M.get(model)
    if prices is None:
        return 0.0
    input_price, output_price = prices
    return (
        input_tokens * input_price + output_tokens * output_price
    ) / 1_000_000.0


def known_model(model: str) -> bool:
    return model in PRICES_USD_PER_1M
