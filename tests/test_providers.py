import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from conftest import CLEAN, SPEC, StubAgent

from mrac_contracts.providers import (
    ProviderError,
    load_provider,
    load_providers,
    normalize_provider,
    validate_selection,
)
from mracbench.codex_exec import CodexExecAdapter
from mracbench.models import AgentRequest
from mracbench.providers import saved_provider
from mracbench.repository_flow import resume_repository_run
from mracbench.runner import run_case


@pytest.fixture
def provider():
    return normalize_provider(
        {
            "id": "zai",
            "name": "Z.AI",
            "base_url": "https://api.z.ai/api/v1",
            "env_key": "MRAC_TEST_API_KEY",
            "model_catalog": {
                "models": [
                    {"slug": "glm-5.3-flash", "supported_reasoning_levels": [{"effort": "high"}]}
                ]
            },
        }
    )


@pytest.mark.parametrize(
    "change",
    [
        {"api_key": "must-not-be-stored"},
        {"experimental_bearer_token": "must-not-be-stored"},
        {"id": "openai"},
        {"id": "unsafe.section"},
        {"schema_version": True},
        {"wire_api": "chat"},
        {"base_url": "https://secret:token@example.com/v1"},
        {"base_url": "https://example.com/v1?api_key=secret"},
        {"env_key": "bad key"},
    ],
)
def test_invalid_provider_configuration_is_rejected(provider, change):
    with pytest.raises(ProviderError):
        normalize_provider({**provider, **change})


def test_relative_catalog_and_provider_reference_are_frozen(tmp_path, provider):
    catalog = tmp_path / "models.json"
    catalog.write_text(json.dumps(provider["model_catalog"]), encoding="utf-8")
    source = tmp_path / "provider.yaml"
    raw = {k: v for k, v in provider.items() if k != "model_catalog"}
    source.write_text(
        yaml.safe_dump({**raw, "model_catalog_file": "models.json"}), encoding="utf-8"
    )
    assert load_provider(source) == provider
    assert load_providers({"zai": {"file": "provider.yaml"}}, tmp_path) == {"zai": provider}
    with pytest.raises(ProviderError, match="match"):
        load_providers({"other": {"file": "provider.yaml"}}, tmp_path)
    validate_selection(provider, "glm-5.3-flash", "high")
    with pytest.raises(ProviderError, match="catalog"):
        validate_selection(provider, "another-model")
    with pytest.raises(ProviderError, match="effort"):
        validate_selection(provider, "glm-5.3-flash", "max")


def test_adapter_injects_provider_and_redacts_secret_before_persisting(
    tmp_path, provider, monkeypatch
):
    secret = "provider-private-token-0123456789"
    monkeypatch.setenv("MRAC_TEST_API_KEY", secret)
    script = tmp_path / "fake.py"
    script.write_text(
        """
import sys, os, json, tomllib, time
from pathlib import Path
args=sys.argv[1:]
values={}
for index,arg in enumerate(args):
 if arg == '-c':
  key, value=args[index+1].split('=',1)
  values[key]=tomllib.loads('value='+value)['value']
assert values['model_provider']=='zai'
assert values['model_providers.zai.base_url']=='https://api.z.ai/api/v1'
assert values['model_providers.zai.env_key']=='MRAC_TEST_API_KEY'
assert values['model_providers.zai.wire_api']=='responses'
assert values['model_providers.zai.requires_openai_auth'] is False
assert json.loads(Path(values['model_catalog_json']).read_bytes())['models'][0]['slug']=='glm-5.3-flash'
secret=os.environ['MRAC_TEST_API_KEY']
assert secret not in ' '.join(args)
sys.stdin.read()
os.write(1,secret[:7].encode());time.sleep(0.05);os.write(1,(secret[7:]+'\\n').encode())
os.write(2,(secret+'\\n').encode())
Path(args[args.index('--output-last-message')+1]).write_text(secret,encoding='utf-8')
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1}}),flush=True)
""",
        encoding="utf-8",
    )
    request = AgentRequest(
        "inspect", tmp_path, tmp_path / "raw", 10, model="glm-5.3-flash", reasoning_effort="high"
    )
    result = CodexExecAdapter(command=[sys.executable, str(script)], provider=provider).run(request)
    assert result.success, result.error_message
    assert result.final_text == "[REDACTED]"
    assert result.stdout.startswith("[REDACTED]")
    assert result.stderr.strip() == "[REDACTED]"
    assert result.usage == {"input_tokens": 1}
    for path in request.raw_dir.iterdir():
        assert secret.encode() not in path.read_bytes(), path
    assert not Path(
        result.metadata["command"][result.metadata["command"].index("--output-last-message") + 1]
    ).exists()


def test_missing_credential_is_not_an_agent_invocation(tmp_path, provider, monkeypatch):
    monkeypatch.delenv("MRAC_TEST_API_KEY", raising=False)
    result = CodexExecAdapter(command=["must-not-start"], provider=provider).run(
        AgentRequest("inspect", tmp_path, tmp_path / "raw", 1, model="glm-5.3-flash")
    )
    assert result.started is False
    assert result.error_type == "PROVIDER_ERROR"
    assert "MRAC_TEST_API_KEY" in result.error_message


def test_provider_timeout_preserves_redacted_partial_output(tmp_path, provider, monkeypatch):
    secret = "timeout-private-credential-123456"
    monkeypatch.setenv("MRAC_TEST_API_KEY", secret)
    script = tmp_path / "timeout.py"
    script.write_text(
        "import os,time\nprint(os.environ['MRAC_TEST_API_KEY'],flush=True)\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    result = CodexExecAdapter(command=[sys.executable, str(script)], provider=provider).run(
        AgentRequest("inspect", tmp_path, tmp_path / "raw", 1, model="glm-5.3-flash")
    )
    assert result.started and result.error_type == "TIMEOUT"
    assert result.stdout.strip() == "[REDACTED]"
    assert result.duration_seconds < 15


def test_malformed_provider_file_does_not_echo_its_contents(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text('id: ["accidental-private-value"\n', encoding="utf-8")
    with pytest.raises(ProviderError) as error:
        load_provider(path)
    assert "accidental-private-value" not in str(error.value)


def test_run_snapshots_provider_without_credentials(config, provider, monkeypatch):
    secret = "private-test-value-1234567890"
    monkeypatch.setenv("MRAC_TEST_API_KEY", secret)
    agent = StubAgent([SPEC, CLEAN, CLEAN])
    agent.provider = provider
    path, result = run_case(replace(config, model="glm-5.3-flash", provider=provider), agent)
    assert result["status"] == "CONVERGED"
    assert result["agent"]["provider"]["id"] == "zai"
    assert saved_provider(path) == provider
    assert secret not in (path / "run.yaml").read_text(encoding="utf-8")
    assert json.loads((path / "input/provider.json").read_bytes()) == provider


def test_resume_rejects_provider_change_before_call_or_checkpoint(config, provider):
    config = replace(config, model="glm-5.3-flash", protocol_id="spec-mrac-v2", provider=provider)

    def invalid(request):
        return "invalid JSON"

    agent = StubAgent([invalid])
    agent.provider = provider
    path, result = run_case(config, agent)
    assert result["status"] == "PARSE_ERROR" or result["status"] == "AUDIT_INVALID"
    before = (path / "repository-state.json").read_bytes()
    changed = StubAgent([])
    changed.provider = {**provider, "base_url": "https://other.invalid/v1"}
    with pytest.raises(ProviderError, match="differs"):
        resume_repository_run(path, changed)
    assert (path / "repository-state.json").read_bytes() == before
    assert changed.requests == []
