"""Providers (all OpenAI-compatible chat-completions endpoints) and the price table."""
from __future__ import annotations

import os

PROVIDERS = {
    # Groq free tier: 8,000 tokens/minute per model, and a single request above that is rejected (413).
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY", "free_tier": True,
             "context_char_budget": 15000, "max_tokens": 2048},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "key_env": "GEMINI_API_KEY",
               "free_tier": True},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY", "free_tier": False},
}

# USD per 1M tokens (input, output): published list prices, used for the "list-price equivalent" column.
# Free-tier runs are billed at $0 but tokens are still counted.
LIST_PRICES = {
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "openai/gpt-oss-120b": (0.15, 0.75),
    "openai/gpt-oss-20b": (0.10, 0.50),
    # qwen/qwen3.8-27b: no published list price found; list-price cost reported as n/a
    "moonshotai/kimi-k2-instruct": (1.00, 3.00),
    "qwen/qwen3-32b": (0.29, 0.59),
    "meta-llama/llama-4-maverick-17b-128e-instruct": (0.20, 0.60),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.0-flash": (0.10, 0.40),
}


def parse_model(spec: str) -> tuple[str, str]:
    provider, _, model = spec.partition(":")
    if provider not in PROVIDERS or not model:
        raise ValueError(f"model must be provider:model with provider in {sorted(PROVIDERS)}, got {spec!r}")
    return provider, model


def api_key(provider: str) -> str | None:
    return os.environ.get(PROVIDERS[provider]["key_env"]) or None


def cost(provider: str, model: str, tokens_in: int, tokens_out: int) -> tuple[float, float | None]:
    """Returns (billed_cost, list_price_cost). billed is 0 for free-tier providers."""
    price = LIST_PRICES.get(model)
    listed = None if price is None else (tokens_in * price[0] + tokens_out * price[1]) / 1e6
    billed = 0.0 if PROVIDERS[provider]["free_tier"] else (listed or 0.0)
    return billed, listed
