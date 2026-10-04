import time
from dataclasses import asdict, replace
from pathlib import Path

from mrac_contracts.execution import canonical
from mrac_contracts.providers import ProviderError, normalize_provider, provider_identity

from .cases import load_case
from .configuration import resolve_limits
from .models import AgentAdapter, BenchError, RunConfig
from .protocol import DEFAULT_PROTOCOL_ID, load_protocol
from .providers import check_adapter
from .runs import RunStore
from .workflows import FLOW_REGISTRY


def run_case(config: RunConfig, adapter: AgentAdapter) -> tuple[Path, dict]:
    config = replace(config, provider=normalize_provider(config.provider))
    check_adapter(adapter, config.provider, config.model, config.reasoning_effort)
    started = time.monotonic()
    store = RunStore(config.runs_dir, config.case_id, config.project_root, config.run_id)
    result = {
        "run_id": store.run_id,
        "case_id": config.case_id,
        "case_version": None,
        "protocol_id": None,
        "protocol_version": None,
        "agent": {"type": adapter.agent_type, "model": config.model, "version": None},
        "status": "RUNNING",
        "convergence_round": None,
        "audit_rounds": 0,
        "repair_rounds": 0,
        "trajectory": [],
        "protocol_violation": False,
        "usage": {"wall_time_seconds": None, "tokens": None, "cost": None},
        "error": None,
        "final_artifact": None,
    }
    active_stage = "initialize"
    store.metadata["requested_config"] = {
        key: str(value.resolve()) if isinstance(value, Path) else value
        for key, value in asdict(config).items()
    }
    store.save_metadata()
    store.checkpoint(result, active_stage)
    try:
        case = load_case(config.project_root, config.case_id)
        for name, content in case.snapshots.items():
            store.snapshot(name, content)
        if config.provider is not None:
            store.snapshot("provider.json", canonical(config.provider))
            result["agent"]["provider"] = provider_identity(config.provider)
        protocol = load_protocol(
            config.project_root,
            config.protocol_id if config.protocol_id is not None else DEFAULT_PROTOCOL_ID,
        )
        for name, content in protocol.snapshots.items():
            store.snapshot(name, content)
        effective = resolve_limits(
            case,
            protocol,
            max_rounds=config.max_rounds,
            timeout_seconds=config.timeout_seconds,
            spec_file=config.spec_file,
        )
        maximum, timeout = effective.max_audit_rounds, effective.timeout_seconds
        result.update(
            case_version=case.version, protocol_id=protocol.id, protocol_version=protocol.version
        )
        result["agent"]["version"] = adapter.version()
        store.metadata.update(
            case_id=case.id,
            case_version=case.version,
            protocol_selection="explicit" if config.protocol_id is not None else "default",
            protocol_id=protocol.id,
            protocol_version=protocol.version,
            repository={"url": case.repository_url, "commit": case.commit},
            agent=result["agent"],
            effective_config={
                "max_audit_rounds": maximum,
                "agent_timeout_seconds": timeout,
                "required_clean_audits": 2,
                "model": config.model,
                "reasoning_effort": config.reasoning_effort,
                "readonly": effective.readonly,
                "ignore_user_config": True,
                "windows_sandbox": getattr(adapter, "windows_sandbox", "elevated"),
            },
        )
        store.save_metadata()
        if config.provider is not None:
            store.metadata["effective_config"]["provider"] = config.provider
            store.save_metadata()
        return FLOW_REGISTRY[protocol.workflow].start(
            config, adapter, store, case, protocol, result, maximum, timeout, started
        )
    except (BenchError, ProviderError) as exc:
        result["status"] = exc.kind
        result["protocol_violation"] = exc.kind == "PROTOCOL_VIOLATION"
        result["error"] = {
            "type": exc.kind,
            "message": str(exc),
            "stage": active_stage,
        }
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 -- persist unexpected terminal failures
        result["status"] = "INTERNAL_ERROR"
        result["error"] = {
            "type": "INTERNAL_ERROR",
            "message": str(exc) or type(exc).__name__,
            "stage": active_stage,
        }
    result["usage"]["wall_time_seconds"] = round(time.monotonic() - started, 3)
    store.finish(result)
    return store.path, result
