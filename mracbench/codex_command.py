"""Codex argv construction and command-file planning, without process execution."""

import json


def command_files(request, provider):
    files = {}
    if request.output_schema is not None:
        files[request.raw_dir / "output-schema.json"] = request.output_schema
    if provider and "model_catalog" in provider:
        files[request.raw_dir / "model-catalog.json"] = provider["model_catalog"]
    return files


def provider_arguments(provider, raw):
    if provider is None:
        return []
    values = {"model_provider": provider["id"]}
    for key in ("name", "base_url", "wire_api", "env_key"):
        if provider.get(key) is not None:
            values[f"model_providers.{provider['id']}.{key}"] = provider[key]
    values[f"model_providers.{provider['id']}.requires_openai_auth"] = False
    if "model_catalog" in provider:
        path = raw / "model-catalog.json"
        values["model_catalog_json"] = str(path.resolve())
    return [
        part
        for key, value in values.items()
        for part in ("-c", f"{key}={json.dumps(value, ensure_ascii=False)}")
    ]


def mcp_arguments(servers):
    values = {}
    for name, config in (servers or {}).items():
        if not name.replace("_", "").isalnum() or not isinstance(config, dict):
            raise ValueError("Invalid MCP server configuration")
        if not isinstance(config.get("command"), str) or not config["command"]:
            raise ValueError(f"MCP server {name} requires an executable command")
        if not isinstance(config.get("args", []), list) or any(
            not isinstance(value, str) for value in config.get("args", [])
        ):
            raise ValueError(f"MCP server {name} args must be strings")
        for key, value in config.items():
            if key not in {
                "command",
                "args",
                "enabled",
                "startup_timeout_sec",
                "tool_timeout_sec",
            }:
                raise ValueError(f"Unsupported MCP server option: {key}")
            values[f"mcp_servers.{name}.{key}"] = value
    return [
        part
        for key, value in values.items()
        for part in ("-c", f"{key}={json.dumps(value, ensure_ascii=False)}")
    ]


def build_command(
    base_command,
    request,
    output_path,
    *,
    provider=None,
    windows=False,
    windows_sandbox="elevated",
    accounting_endpoint=None,
):
    raw = request.raw_dir
    command = [
        *base_command,
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--sandbox",
        "read-only" if request.readonly else "workspace-write",
        "--json",
        "--color",
        "never",
        "-c",
        'approval_policy="never"',
        "-c",
        'web_search="disabled"',
        "-c",
        "project_doc_max_bytes=0",
        "-c",
        "features.multi_agent=false",
        "-c",
        "features.apps=false",
        "--cd",
        str(request.workspace),
        "--output-last-message",
        str(output_path),
    ]
    if windows:
        # --ignore-user-config also drops the native Windows sandbox
        # implementation. Select it explicitly so workspace-write can
        # execute file tools inside the isolated repair checkout.
        command += ["-c", f'windows.sandbox="{windows_sandbox}"']
    if request.output_schema is not None:
        schema_path = raw / "output-schema.json"
        command += ["--output-schema", str(schema_path)]
    command += provider_arguments(provider, raw)
    if accounting_endpoint is not None:
        command += [
            "-c",
            f"model_providers.{provider['id']}.base_url={json.dumps(accounting_endpoint)}",
        ]
    command += mcp_arguments(request.mcp_servers)
    if request.model:
        command += ["--model", request.model]
    if request.reasoning_effort:
        command += ["-c", f'model_reasoning_effort="{request.reasoning_effort}"']
    if request.skip_git_repo_check:
        command.append("--skip-git-repo-check")
    command.append("-")
    return command
