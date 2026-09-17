"""Explicit, secret-free Codex Responses provider configuration."""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from yaml import YAMLError

from .execution import ContractError, digest, parse_yaml


class ProviderError(ContractError):
    kind = "PROVIDER_ERROR"


RESERVED = {"openai", "ollama", "lmstudio", "amazon-bedrock"}
FIELDS = {"schema_version", "id", "name", "base_url", "env_key", "wire_api", "model_catalog"}


def normalize_provider(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ProviderError(
            "Provider contains unknown fields; inline credentials are not supported"
        )
    if type(value.get("schema_version", 1)) is not int or value.get("schema_version", 1) != 1:
        raise ProviderError("Unsupported provider schema version")
    provider_id = value.get("id")
    if (
        not isinstance(provider_id, str)
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", provider_id)
        or provider_id.lower() in RESERVED
    ):
        raise ProviderError("Custom provider id must be a non-reserved simple identifier")
    name = value.get("name", provider_id)
    if not isinstance(name, str) or not name.strip() or any(ord(c) < 32 for c in name):
        raise ProviderError("Invalid provider display name")
    base_url = value.get("base_url")
    if not isinstance(base_url, str) or any(c.isspace() for c in base_url):
        raise ProviderError("Provider requires an explicit base_url")
    try:
        url = urlsplit(base_url)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError
        _ = url.port
    except ValueError as exc:
        raise ProviderError(
            "base_url must be an HTTP(S) URL without credentials, query or fragment"
        ) from exc
    if value.get("wire_api", "responses") != "responses":
        raise ProviderError("Codex providers require wire_api=responses")
    env_key = value.get("env_key")
    if env_key is not None and (
        not isinstance(env_key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_key)
    ):
        raise ProviderError("env_key must name an environment variable, not contain its value")
    result = {
        "schema_version": 1,
        "id": provider_id,
        "name": name,
        "base_url": base_url.rstrip("/"),
        "wire_api": "responses",
        "env_key": env_key,
    }
    catalog = value.get("model_catalog")
    if catalog is not None:
        if (
            not isinstance(catalog, dict)
            or set(catalog) != {"models"}
            or not isinstance(catalog["models"], list)
            or not catalog["models"]
        ):
            raise ProviderError("Model catalog must contain a nonempty models array")
        seen = set()
        for model in catalog["models"]:
            if (
                not isinstance(model, dict)
                or not isinstance(model.get("slug"), str)
                or not model["slug"].strip()
                or model["slug"] in seen
            ):
                raise ProviderError("Model catalog requires unique nonempty slugs")
            seen.add(model["slug"])
            levels = model.get("supported_reasoning_levels", [])
            if not isinstance(levels, list) or any(
                not isinstance(level, dict) or not isinstance(level.get("effort"), str)
                for level in levels
            ):
                raise ProviderError("Invalid model catalog reasoning levels")
        try:
            result["model_catalog"] = json.loads(
                json.dumps(catalog, ensure_ascii=False, allow_nan=False)
            )
        except (ValueError, TypeError) as exc:
            raise ProviderError("Model catalog must be finite JSON data") from exc
    return result


def load_provider(value, directory=None):
    """Load a file or mapping, expanding catalog references before any snapshot is saved."""
    if isinstance(value, (str, Path)):
        path = Path(value).resolve()
        try:
            data = parse_yaml(path.read_bytes())
        except (OSError, ValueError, YAMLError) as exc:
            raise ProviderError("Cannot read provider configuration as YAML/JSON") from exc
        return load_provider(data, path.parent)
    if not isinstance(value, dict):
        raise ProviderError("Provider file must contain a mapping")
    value = dict(value)
    if "model_catalog_file" in value:
        if "model_catalog" in value or directory is None:
            raise ProviderError("Specify one model catalog source")
        reference = value.pop("model_catalog_file")
        if not isinstance(reference, str) or not reference.strip():
            raise ProviderError("Invalid model_catalog_file")
        try:
            value["model_catalog"] = json.loads((Path(directory) / reference).read_bytes())
        except (OSError, ValueError) as exc:
            raise ProviderError("Cannot read model_catalog_file as JSON") from exc
    return normalize_provider(value)


def load_providers(value, directory):
    if not isinstance(value, dict):
        raise ProviderError("providers must be a mapping")
    result = {}
    for key, definition in value.items():
        if isinstance(definition, dict) and set(definition) == {"file"}:
            provider = load_provider(Path(directory) / definition["file"])
            if provider["id"] != key:
                raise ProviderError("Provider reference must match the provider file id")
        elif isinstance(definition, dict):
            if "id" in definition and definition["id"] != key:
                raise ProviderError("Provider id does not match its declaration")
            provider = load_provider({**definition, "id": key}, directory)
        else:
            raise ProviderError("Provider entry must be a mapping or a file reference")
        result[key] = provider
    return result


def validate_selection(provider, model, effort=None):
    provider = normalize_provider(provider)
    if provider is None:
        return
    if not isinstance(model, str) or not model.strip():
        raise ProviderError("Custom providers require an explicit model")
    if "model_catalog" in provider:
        entry = next((m for m in provider["model_catalog"]["models"] if m["slug"] == model), None)
        if entry is None:
            raise ProviderError("Selected model is not present in the frozen provider catalog")
        levels = entry.get("supported_reasoning_levels")
        if (
            effort is not None
            and levels is not None
            and effort not in {level["effort"] for level in levels}
        ):
            raise ProviderError(
                "Selected reasoning effort is not supported by the provider catalog"
            )


def credential(provider):
    key = provider.get("env_key") if provider else None
    if key is None:
        return None
    value = os.environ.get(key)
    if not value or not value.strip() or any(c in value for c in "\r\n\x00"):
        raise ProviderError(f"Missing or invalid provider credential environment variable: {key}")
    return value


def provider_identity(provider):
    provider = normalize_provider(provider)
    return {"id": provider["id"], "sha256": digest(provider)} if provider else None
