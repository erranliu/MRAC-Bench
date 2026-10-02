"""Case/version statistics JSON followed by a Markdown projection."""

import json
import math
import os
import re
import warnings
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import mean

from mrac_contracts.execution import OCCUPIED, ContractError, atomic, digest, now
from mrac_resources.cases import Registry
from mrac_resources.home import checked
from mrac_resources.locks import file_lock

from .pricing import (
    TOKEN_FIELDS,
    estimate,
    load_pricing,
    normalize_usage,
    reported_cost,
    select_price,
)


def read_object(path):
    try:
        value = json.loads(Path(path).read_bytes())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def case_key(row):
    case = row.get("execution", {}).get("case", {})
    key, version = case.get("case_key"), case.get("version")
    if not isinstance(key, str) or not re.fullmatch(r"C\d+", key):
        return None
    if type(version) is not int or version < 1:
        return None
    return key, version


def latest_case_keys(store):
    registry = Registry(store.home)
    try:
        return {
            (case["case_key"], case["default_version"])
            for case in registry.list()
            if not case["archived"]
        }
    finally:
        registry.close()


def all_runs(store):
    """History reruns are samples; recovery/continuation revisions are not."""
    records = {}
    for task in store.tasks():
        for row in [*task.get("history", []), task]:
            previous = records.get(row["run_id"])
            if previous is None or row.get("revision", 0) >= previous.get("revision", 0):
                records[row["run_id"]] = row
    for path in (store.home / "runs").glob("*/managed-request.json"):
        request = read_object(path)
        if not request.get("run_id") or request["run_id"] in records:
            continue
        public = read_object(path.parent / "lifecycle.json")
        records[request["run_id"]] = {
            "run_id": request["run_id"],
            "run_dir": str(path.parent),
            "batch_id": None,
            "execution": request,
            "state": public.get("lifecycle", "RUNNING"),
            "revision": public.get("revision", 0),
            "evidence_validity": "VALID",
            "outcome": public.get("outcome"),
            "last_observation": {"public": public},
        }
    return sorted(records.values(), key=lambda row: row["run_id"])


def invocations(path):
    """Read only small execution records. Interrupted calls may expose stdout usage."""
    for stage in sorted((path / "raw").glob("*")):
        if not stage.is_dir():
            continue
        execution = read_object(stage / "execution.json")
        metadata = read_object(stage / "invocation.json")
        if not execution.get("started", metadata.get("started", False)):
            if not execution and not metadata and (stage / "request.txt").exists():
                yield stage.name, None, None
            continue
        ledger = read_object(stage / "provider-usage.json")
        if ledger.get("provider") == "openrouter":
            entries = ledger.get("requests", [])
            if not entries:
                yield stage.name, None, reported_cost(None)
            for entry in entries:
                usage = entry.get("usage")
                yield (
                    f"{stage.name}/api-{entry['request_index']}",
                    usage,
                    {
                        **reported_cost(usage),
                        "response_id": entry.get("response_id"),
                        "http_status": entry.get("http_status"),
                        "billing_issue": entry.get("billing_issue"),
                    },
                )
            continue  # Do not count Codex's duplicate turn totals again.
        usage = execution.get("usage")
        if usage is None:
            # Single Codex exec turn totals are cumulative; retain the last total,
            # never add it to execution.json's duplicate copy.
            try:
                with (stage / "stdout.txt").open(encoding="utf-8", errors="replace") as stream:
                    for line in stream:
                        try:
                            event = json.loads(line)
                            if event.get("type") == "turn.completed":
                                usage = event.get("usage")
                        except (ValueError, AttributeError):
                            continue
            except OSError:
                pass
        yield stage.name, usage, None


def consumption(calls):
    totals, missing = {}, {}
    for field in TOKEN_FIELDS:
        values = [call["tokens"][field] for call in calls]
        known = [value for value in values if value is not None]
        totals[field] = sum(known) if known or not calls else None
        missing[field] = len(values) - len(known)
    costs = [call["cost"].get("total_usd", call["cost"]["estimated_usd"]) for call in calls]
    known_costs = [Decimal(value) for value in costs if value is not None]
    subtotal = str(sum(known_costs, Decimal(0)))
    reported = [
        Decimal(call["cost"]["total_usd"])
        for call in calls
        if call["cost"].get("kind") == "reported" and call["cost"].get("total_usd") is not None
    ]
    return {
        "tokens": {
            "known_totals": totals,
            "missing_by_field": missing,
            "invocations": len(calls),
            "usage_recorded_invocations": sum(
                call["tokens"]["total_tokens"] is not None for call in calls
            ),
            "complete": not any(missing.values()),
        },
        "cost": {
            "currency": "USD",
            "known_subtotal_usd": subtotal,
            "total_usd": subtotal if len(known_costs) == len(calls) else None,
            "estimated_usd": subtotal if len(known_costs) == len(calls) and not reported else None,
            "reported_subtotal_usd": str(sum(reported, Decimal(0))),
            "estimated_subtotal_usd": str(sum(known_costs, Decimal(0)) - sum(reported, Decimal(0))),
            "reported_invocations": len(reported),
            "priced_invocations": len(known_costs),
            "invocations": len(calls),
            "complete": len(known_costs) == len(calls),
        },
    }


def reported_outcome(row, raw):
    state = row["state"]
    if row.get("evidence_validity", "VALID") != "VALID":
        return None, "invalid_evidence"
    if state in {"CANCELLED", "SKIPPED"} or raw == "ABORTED":
        return None, state.lower() if raw != "ABORTED" else "aborted"
    if state in OCCUPIED or state in {"QUEUED", "RETRY_WAIT"}:
        return None, "in_progress"
    if state == "WAITING_INPUT" or raw in {"NEEDS_INPUT", "BLOCKED"}:
        return None, "awaiting_input"
    if raw == "CONVERGED":
        return "CONVERGED", "converged"
    if raw in {"NON_CONVERGED", "PAUSED"}:
        return "NON_CONVERGED", "early_pause" if raw == "PAUSED" else "audit_budget_exhausted"
    if raw or state == "FAILED":
        return "ERROR", "execution_error"
    return None, "result_missing"


def normalized_run(row, directory, pricing, tier=None, context=None):
    path = Path(row["run_dir"])
    public = read_object(path / "lifecycle.json")
    public = public or row.get("last_observation", {}).get("public", {})
    result = public.get("result") or read_object(path / "result.json")
    raw = result.get("status") or row.get("outcome")
    outcome, reason = reported_outcome(row, raw)
    execution = row["execution"]
    settings = execution["settings"]
    provider = settings.get("provider") or {"id": "openai"}
    model = settings.get("model")
    price = select_price(pricing, provider["id"], model, tier, context)
    calls = []
    for stage, raw_usage, actual_cost in invocations(path):
        tokens, issues = normalize_usage(raw_usage)
        calls.append(
            {
                "stage": stage,
                "raw_usage": raw_usage,
                "tokens": tokens,
                "usage_issues": issues,
                "cost": actual_cost if actual_cost is not None else estimate(tokens, price),
            }
        )
    if not calls and (
        any(result.get(field, 0) for field in ("audit_rounds", "repair_rounds"))
        or (row.get("attempt_id") and not result and row["state"] == "FAILED")
    ):
        tokens, issues = normalize_usage(None)
        calls.append(
            {
                "stage": "unrecorded_invocations",
                "raw_usage": None,
                "tokens": tokens,
                "usage_issues": issues,
                "cost": estimate(tokens, price),
            }
        )
    error = result.get("error") or {}
    if not isinstance(error, dict):
        error = {"type": "runner_error"}
    protocol = settings["protocol_id"]
    hashes = {
        key: value
        for key, value in execution.get("bundle_manifest", {}).items()
        if key.startswith("protocols/" + protocol + "/")
    }
    conditions = {
        "cli_version": execution.get("agent_version"),
        "code_sha256": execution.get("code_identity", {}).get("sha256"),
        "protocol_sha256": digest(hashes) if hashes else None,
        "windows_sandbox": settings.get("windows_sandbox"),
        "max_audit_rounds": settings.get("max_rounds"),
        "timeout_seconds": settings.get("timeout_seconds"),
        "provider_base_url": provider.get("base_url"),
        "execution_spec_sha256": execution.get("bundle_manifest", {}).get("execution-spec.md"),
    }
    return {
        "run_id": row["run_id"],
        "batch_id": row.get("batch_id"),
        "revision": public.get("revision", row.get("revision")),
        "protocol_id": protocol,
        "protocol_version": settings.get("protocol_version"),
        "provider": provider["id"],
        "model": model,
        "reasoning_effort": settings.get("reasoning_effort"),
        "conditions": conditions,
        "lifecycle": row["state"],
        "raw_status": raw,
        "status": outcome,
        "included": outcome is not None,
        "stop_reason": reason,
        "error_type": error.get("type"),
        "error_stage": error.get("stage"),
        "wall_time_seconds": result.get("usage", {}).get("wall_time_seconds"),
        "audit_rounds": result.get("audit_rounds"),
        "repair_rounds": result.get("repair_rounds"),
        "price": price,
        "pricing_issue": None if price or provider["id"] == "openrouter" else "price_missing",
        "invocations": calls,
        **consumption(calls),
        "evidence_path": Path(os.path.relpath(path, directory)).as_posix(),
    }


def metric(values):
    known = [
        value
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    ]
    return {
        "n": len(known),
        "missing": len(values) - len(known),
        "mean": mean(known) if known else None,
        "min": min(known) if known else None,
        "max": max(known) if known else None,
    }


def aggregate(rows):
    selected = [row for row in rows if row["included"]]
    normal = [row for row in selected if row["status"] in {"CONVERGED", "NON_CONVERGED"}]
    errors = [row for row in selected if row["status"] == "ERROR"]
    counts = Counter(row["status"] for row in selected)
    calls = [call for row in rows for call in row["invocations"]]
    costs = [
        Decimal(row["cost"].get("total_usd", row["cost"]["estimated_usd"]))
        for row in selected
        if row["cost"]["complete"]
    ]
    return {
        "counts": {
            "runs": len(rows),
            "included": len(selected),
            "excluded": len(rows) - len(selected),
            "converged": counts["CONVERGED"],
            "non_converged": counts["NON_CONVERGED"],
            "errors": counts["ERROR"],
        },
        "convergence_rate": counts["CONVERGED"] / len(selected) if selected else None,
        "stop_reasons": dict(Counter(row["stop_reason"] for row in rows)),
        "error_types": dict(
            Counter(row["error_type"] or row["raw_status"] or "unknown" for row in errors)
        ),
        "error_stages": dict(Counter(row["error_stage"] or "unknown" for row in errors)),
        "normal_wall_time_seconds": metric([row["wall_time_seconds"] for row in normal]),
        "error_wall_time_seconds": metric([row["wall_time_seconds"] for row in errors]),
        "normal_repair_rounds": metric([row["repair_rounds"] for row in normal]),
        "completed_run_tokens": metric(
            [
                row["tokens"]["known_totals"]["total_tokens"]
                if row["tokens"]["missing_by_field"]["total_tokens"] == 0
                else None
                for row in selected
            ]
        ),
        "average_cost_per_completed_run_usd": str(sum(costs) / len(costs)) if costs else None,
        "average_cost_sample_count": len(costs),
        **consumption(calls),
        "excluded_known_cost_usd": str(
            sum(
                (Decimal(row["cost"]["known_subtotal_usd"]) for row in rows if not row["included"]),
                Decimal(0),
            )
        ),
    }


def build_report(store, key, rows, pricing, tier=None, context=None):
    directory = store.home / "reports" / key[0] / f"v{key[1]}"
    fingerprints = {row["execution"]["case"].get("sha256") for row in rows}
    if len(fingerprints) > 1:
        raise ContractError(f"Conflicting package fingerprints for {key[0]} v{key[1]}")
    normalized = [normalized_run(row, directory, pricing, tier, context) for row in rows]
    groups = defaultdict(list)
    for row in normalized:
        group = (
            row["protocol_id"],
            row["protocol_version"],
            row["provider"],
            row["model"],
            row["reasoning_effort"],
            digest(row["conditions"]),
        )
        groups[group].append(row)
    summaries = []
    for group, members in sorted(groups.items(), key=lambda item: json.dumps(item[0])):
        summaries.append(
            {
                "protocol_id": group[0],
                "protocol_version": group[1],
                "provider": group[2],
                "model": group[3],
                "reasoning_effort": group[4],
                "conditions_id": group[5],
                "conditions": members[0]["conditions"],
                **aggregate(members),
            }
        )
    case = rows[-1]["execution"]["case"]
    return {
        "schema_version": 1,
        "generated_at": now(),
        "case": {
            "case_key": key[0],
            "version": key[1],
            "name": case.get("name"),
            "source_id": case.get("source_id"),
            "sha256": case.get("sha256"),
        },
        "pricing": {
            "checked_on": pricing["checked_on"],
            "currency": "USD",
            "tier": tier or pricing["default_tier"],
            "context": context or pricing["default_context"],
            "notes": pricing.get("notes", []),
            "sources": pricing.get("sources", []),
        },
        "summary": aggregate(normalized),
        "groups": summaries,
        "runs": normalized,
    }


def cell(value):
    return (
        str(value if value is not None else "未知")
        .replace("|", "\\|")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def number(value, places=1, divisor=1):
    return f"{Decimal(str(value)) / divisor:.{places}f}" if value is not None else "未知"


def convergence_rate(counts):
    return counts["converged"] / counts["included"] if counts["included"] else None


def ranking_key(group):
    rate = convergence_rate(group["counts"])
    return (
        -(rate if rate is not None else -1),
        group["provider"],
        group["model"],
        group["reasoning_effort"] or "",
        group["conditions_id"],
    )


def render_report(data):
    case, total = data["case"], data["summary"]
    counts = total["counts"]
    lines = [
        f"# {cell(case['case_key'])} · {cell(case['name'])} · case v{case['version']}",
        "",
        f"生成时间（UTC）：{data['generated_at']}",
        "",
        f"统计样本 {counts['included']}：收敛 {counts['converged']}，未收敛 {counts['non_converged']}，错误 {counts['errors']}；排除/待完成 {counts['excluded']}。",
        "",
        "PAUSED 统一计未收敛。正常耗时和修复均值包含收敛与未收敛；错误耗时另列。",
        "",
        "## Token 与 API 费用",
        "",
        f"价格日期：{data['pricing']['checked_on']}；{data['pricing']['tier']} / {data['pricing']['context']} context；USD。",
        f"已知 token 小计：{cell(total['tokens']['known_totals']['total_tokens'])}；usage 覆盖：{total['tokens']['usage_recorded_invocations']}/{total['tokens']['invocations']} 调用。",
        f"已知 API 费用小计：${number(total['cost']['known_subtotal_usd'], 6)}；费用覆盖：{total['cost']['priced_invocations']}/{total['cost']['invocations']} 调用。",
        f"其中 OpenRouter 实际返回费用：${number(total['cost'].get('reported_subtotal_usd', '0'), 6)}；其他 API 预估费用：${number(total['cost'].get('estimated_subtotal_usd', total['cost']['known_subtotal_usd']), 6)}。",
        f"其中排除/待完成记录的已知费用：${number(total['excluded_known_cost_usd'], 6)}。",
        "",
        "OpenRouter 直接汇总 usage.cost（账户收费），缺失费用记未知，不用本地单价推算。其他 provider 按本地单价估算。小计包含错误、取消和进行中记录的已知消耗。",
        "CLI usage 是多个 API 请求的累计值；上下文档位为估算选择，不按累计输入量触发长上下文加价。",
        "",
        "## 模型汇总",
        "",
        "收敛率 = 收敛次数 / 已完成样本数（含错误，排除取消、待输入和进行中记录）。按收敛率降序排列，无已完成样本的行放在最后。",
        "",
        "| 排名 | Provider / 模型 / effort | 样本 | 收敛 | 未收敛 | 错误 | 收敛率 | 正常均时 min | 错误均时 min | 平均修复 | 已知 token | 已知费用 USD | 费用覆盖 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    if scope := data.get("report_scope"):
        lines[0] += f" · {cell(scope['protocol_id'])}@{cell(scope['protocol_version'])}"
    for rank, group in enumerate(sorted(data["groups"], key=ranking_key), 1):
        count = group["counts"]
        rate = convergence_rate(count)
        lines.append(
            "| "
            + " | ".join(
                map(
                    cell,
                    [
                        rank,
                        f"{group['provider']} / {group['model']} / {group['reasoning_effort'] or 'default/未记录'}",
                        count["included"],
                        count["converged"],
                        count["non_converged"],
                        count["errors"],
                        number(rate * 100, 1) + "%" if rate is not None else "未知",
                        number(group["normal_wall_time_seconds"]["mean"], divisor=60),
                        number(group["error_wall_time_seconds"]["mean"], divisor=60),
                        number(group["normal_repair_rounds"]["mean"], 2),
                        group["tokens"]["known_totals"]["total_tokens"],
                        number(group["cost"]["known_subtotal_usd"], 6),
                        f"{group['cost']['priced_invocations']}/{group['cost']['invocations']}",
                    ],
                )
            )
            + " |"
        )
    lines += [
        "",
        "## Token 明细与平均费用",
        "",
        "缓存计数属于输入，reasoning 属于输出，均不重复加到总 token 或费用。费用均值仅使用费用完整的已完成样本，样本数另列。",
        "",
        "| 条件 / 模型 / effort | 输入 | 缓存读取 | 缓存写入 | 输出 | reasoning | 总 token | usage 覆盖 | 平均 token / 样本 | 平均费用 USD | 费用均值样本 |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|",
    ]
    for group in data["groups"]:
        tokens = group["tokens"]
        values = tokens["known_totals"]
        lines.append(
            "| "
            + " | ".join(
                map(
                    cell,
                    [
                        f"{group['conditions_id'][:8]} / {group['model']} / {group['reasoning_effort'] or 'default/未记录'}",
                        values["input_tokens"],
                        values["cached_input_tokens"],
                        values["cache_write_input_tokens"],
                        values["output_tokens"],
                        values["reasoning_output_tokens"],
                        values["total_tokens"],
                        f"{tokens['usage_recorded_invocations']}/{tokens['invocations']}",
                        f"{number(group['completed_run_tokens']['mean'], 0)} / {group['completed_run_tokens']['n']}",
                        number(group["average_cost_per_completed_run_usd"], 6),
                        group["average_cost_sample_count"],
                    ],
                )
            )
            + " |"
        )
    lines += ["", "## 停止原因", "", "| 原因 | 次数 |", "|---|---:|"]
    for reason, count in sorted(total["stop_reasons"].items()):
        lines.append(f"| {cell(reason)} | {count} |")
    lines += ["", "## 错误类型与阶段", "", "| 维度 | 值 | 次数 |", "|---|---|---:|"]
    for dimension in ("error_types", "error_stages"):
        for value, count in sorted(total[dimension].items()):
            lines.append(f"| {dimension} | {cell(value)} | {count} |")
    lines += [
        "",
        "## 逐次记录",
        "",
        "| Run | 协议 / 版本 | 模型 / effort | 统计结果 / 停止原因 | 原始状态 | 耗时 min | 审计 / 修复 | 已知 token | 已知费用 USD | 费用覆盖 |",
        "|---|---|---|---|---|---:|---|---:|---:|---|",
    ]
    for row in data["runs"]:
        link = row["evidence_path"].replace(" ", "%20")
        lines.append(
            "| "
            + " | ".join(
                map(
                    cell,
                    [
                        f"[{row['run_id']}]({link}/)",
                        f"{row['protocol_id']}@{row['protocol_version']}",
                        f"{row['model']} / {row['reasoning_effort'] or 'default/未记录'}",
                        f"{row['status'] or '排除/待完成'} / {row['stop_reason']}",
                        row["raw_status"],
                        number(row["wall_time_seconds"], divisor=60),
                        f"{row['audit_rounds']} / {row['repair_rounds']}",
                        row["tokens"]["known_totals"]["total_tokens"],
                        number(row["cost"]["known_subtotal_usd"], 6),
                        f"{row['cost']['priced_invocations']}/{row['cost']['invocations']}",
                    ],
                )
            )
            + " |"
        )
    lines += [
        "",
        "完整输入/缓存/输出/reasoning token、单价、缺失项、错误阶段和运行条件见 [statistics.json](statistics.json)。",
        "",
    ]
    return "\n".join(lines)


def write_case_reports(store, *, keys=None, pricing_file=None, tier=None, context=None):
    if keys is None:
        keys = latest_case_keys(store)
    if not keys:
        return []
    pricing = load_pricing(pricing_file)
    grouped = defaultdict(list)
    for row in all_runs(store):
        key = case_key(row)
        if key is not None and key in keys:
            grouped[key].append(row)
    written = []
    for key, rows in sorted(grouped.items()):
        directory = checked(store.home, store.home / "reports" / key[0] / f"v{key[1]}")
        directory.mkdir(parents=True, exist_ok=True)
        with file_lock(directory / ".statistics.lock"):
            data = build_report(store, key, rows, pricing, tier, context)
            atomic(directory / "statistics.json", data)
            # Render from the JSON that was written, never from a second DB query.
            document = render_report(read_object(directory / "statistics.json"))
            atomic(directory / "report.md", document.encode("utf-8"), raw=True)
        written.append(
            {
                "case_key": key[0],
                "case_version": key[1],
                "statistics": str(directory / "statistics.json"),
                "report": str(directory / "report.md"),
            }
        )
    return written


def refresh_reports(store, keys):
    """Reporting errors do not alter model results or trigger another model call."""
    if not keys:
        return
    try:
        write_case_reports(store, keys=keys & latest_case_keys(store))
    except Exception as exc:  # noqa: BLE001 -- reporting must not fail an execution
        warnings.warn(f"Case statistics update failed: {exc}", RuntimeWarning, stacklevel=2)
