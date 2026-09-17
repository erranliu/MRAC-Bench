"""Runner-side provider snapshot and resume checks."""

from mrac_contracts.execution import digest, read_json
from mrac_contracts.providers import ProviderError, normalize_provider, validate_selection


def check_adapter(adapter, provider, model=None, effort=None):
    provider = normalize_provider(provider)
    if normalize_provider(getattr(adapter, "provider", None)) != provider:
        raise ProviderError("Adapter provider differs from the run's frozen provider")
    validate_selection(provider, model, effort)


def saved_provider(path):
    from .cases import load_yaml

    if not (path / "run.yaml").exists():
        return None  # Let the protocol loader report a missing/uninitialized run.
    metadata = load_yaml((path / "run.yaml").read_bytes(), "run.yaml")
    expected = metadata.get("effective_config", {}).get("provider")
    if expected is None:
        return None
    snapshot = path / "input/provider.json"
    if digest(snapshot.read_bytes()) != metadata["input_sha256"].get("provider.json"):
        raise ProviderError("Provider snapshot hash changed")
    provider = normalize_provider(read_json(snapshot))
    if provider != normalize_provider(expected):
        raise ProviderError("Provider snapshot differs from pinned execution configuration")
    return provider
