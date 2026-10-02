"""Local API-equivalent pricing; no network or model calls."""

from decimal import Decimal, InvalidOperation
from pathlib import Path

from mrac_contracts.execution import ContractError, parse_yaml

TOKEN_FIELDS = (
    "input_tokens",
    "uncached_input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def decimal(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError
        return result
    except (InvalidOperation, ValueError) as exc:
        raise ContractError("Pricing values must be finite nonnegative decimals") from exc


def load_pricing(path=None):
    data = parse_yaml(Path(path or Path(__file__).with_name("api-pricing.yaml")).read_bytes())
    if data.get("schema_version") != 1 or data.get("currency") != "USD":
        raise ContractError("Unsupported API pricing configuration")
    if type(data.get("unit_tokens")) is not int or data["unit_tokens"] <= 0:
        raise ContractError("Pricing unit_tokens must be positive")
    for provider in data["providers"].values():
        for model in provider["models"].values():
            for context in ("short", "long"):
                if model.get(context) is not None:
                    for field in ("input", "cached_input", "cache_write", "output"):
                        decimal(model[context][field])
            for value in model["tier_multipliers"].values():
                decimal(value)
    return data


def select_price(catalog, provider, model, tier=None, context=None):
    tier = tier or catalog["default_tier"]
    context = context or catalog["default_context"]
    provider_data = catalog["providers"].get(provider, {})
    resolved = provider_data.get("aliases", {}).get(model, model)
    entry = provider_data.get("models", {}).get(resolved)
    if not entry or not entry.get(context) or tier not in entry["tier_multipliers"]:
        return None
    multiplier = decimal(entry["tier_multipliers"][tier])
    return {
        "provider": provider,
        "model": resolved,
        "tier": tier,
        "context": context,
        "currency": catalog["currency"],
        "unit_tokens": catalog["unit_tokens"],
        "checked_on": catalog["checked_on"],
        "source": entry["source"],
        "rates": {key: str(decimal(value) * multiplier) for key, value in entry[context].items()},
    }


def normalize_usage(raw):
    """Normalize Codex/Responses counters. Cache/reasoning are subsets, not extra totals."""
    values = dict.fromkeys(TOKEN_FIELDS)
    issues = []
    if not isinstance(raw, dict):
        return values, ["usage_missing"]
    input_details = raw.get("input_tokens_details") or {}
    output_details = raw.get("output_tokens_details") or {}
    if not isinstance(input_details, dict):
        input_details = {}
    if not isinstance(output_details, dict):
        output_details = {}
    fields = {
        "input_tokens": raw.get("input_tokens"),
        "cached_input_tokens": raw.get("cached_input_tokens", input_details.get("cached_tokens")),
        "cache_write_input_tokens": raw.get("cache_write_input_tokens"),
        "output_tokens": raw.get("output_tokens"),
        "reasoning_output_tokens": raw.get(
            "reasoning_output_tokens", output_details.get("reasoning_tokens")
        ),
    }
    for key, value in fields.items():
        if type(value) is int and value >= 0:
            values[key] = value
        else:
            issues.append(key + "_missing_or_invalid")
    if values["input_tokens"] is not None and values["output_tokens"] is not None:
        values["total_tokens"] = values["input_tokens"] + values["output_tokens"]
    cache = (values["cached_input_tokens"], values["cache_write_input_tokens"])
    if values["input_tokens"] is not None and all(value is not None for value in cache):
        remaining = values["input_tokens"] - sum(cache)
        if remaining < 0:
            issues.append("cache_exceeds_input")
        else:
            values["uncached_input_tokens"] = remaining
    if (
        values["reasoning_output_tokens"] is not None
        and values["output_tokens"] is not None
        and values["reasoning_output_tokens"] > values["output_tokens"]
    ):
        issues.append("reasoning_exceeds_output")
    return values, issues


def estimate(tokens, price):
    if price is None:
        return {"estimated_usd": None, "components_usd": None, "issue": "price_missing"}
    buckets = {
        "input": tokens["uncached_input_tokens"],
        "cached_input": tokens["cached_input_tokens"],
        "cache_write": tokens["cache_write_input_tokens"],
        "output": tokens["output_tokens"],
    }
    if any(value is None for value in buckets.values()):
        return {"estimated_usd": None, "components_usd": None, "issue": "billing_tokens_missing"}
    components = {
        key: Decimal(value) * decimal(price["rates"][key]) / price["unit_tokens"]
        for key, value in buckets.items()
    }
    return {
        "estimated_usd": str(sum(components.values())),
        "components_usd": {key: str(value) for key, value in components.items()},
        "issue": None,
    }
