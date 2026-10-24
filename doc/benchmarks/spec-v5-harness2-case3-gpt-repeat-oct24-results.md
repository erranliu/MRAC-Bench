# Spec v5 / harness v2：case3 GPT 七模型重跑（2026-10-24，各 1 次）

GPT-5.6 Sol / Luna / Terra、GPT-6 Sol / Luna / Astra、GPT-6.1 Sol 各运行 1 次。Terra 此前只在 @4 测过，本批首次补测 @5；其余六个模型保留原 @5 记录并新增本次样本。
case3 v1、spec-mrac-v2@5、harness v2、high、unelevated、8 轮审计预算、每次 agent 调用上限 1800 秒。GPT 批次并发 4，无自动重试；第 8 轮只审计。与同日 Kimi / MiniMax 共用调度服务，总并发上限为 6，OpenRouter 组上限为 2。与 10 月 17 日补测使用同一份运行代码和 Codex CLI 0.159.2。暂停与预算耗尽计未收敛，执行错误单列；收敛表示连续两次未报告发现，不是独立正确性评分。费用按已保存价格表估计，不代表实际账单。

# C000003 · case3 · case v1

生成时间（UTC）：2026-10-24T01:37:49.740190+00:00

统计样本 5：收敛 1，未收敛 1，错误 3；排除/待完成 2。

PAUSED 统一计未收敛。正常耗时和修复均值包含收敛与未收敛；错误耗时另列。

## Token 与 API 费用

价格日期：2026-10-02；standard / short context；USD。
已知 token 小计：16639949；usage 覆盖：28/28 调用。
已知 API 费用小计：$7.093622；费用覆盖：28/28 调用。
其中 OpenRouter 实际返回费用：$0.000000；其他 API 预估费用：$7.093622。
其中排除/待完成记录的已知费用：$2.951996。

OpenRouter 直接汇总 usage.cost（账户收费），缺失费用记未知，不用本地单价推算。其他 provider 按本地单价估算。小计包含错误、取消和进行中记录的已知消耗。
CLI usage 是多个 API 请求的累计值；上下文档位为估算选择，不按累计输入量触发长上下文加价。

## 模型汇总

收敛率 = 收敛次数 / 已完成样本数（含错误，排除取消、待输入和进行中记录）。按收敛率降序排列，无已完成样本的行放在最后。

| 排名 | Provider / 模型 / effort | 样本 | 收敛 | 未收敛 | 错误 | 收敛率 | 正常均时 min | 错误均时 min | 平均修复 | 已知 token | 已知费用 USD | 费用覆盖 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | openai / gpt-6-sol / high | 1 | 1 | 0 | 0 | 100.0% | 16.5 | 未知 | 1.00 | 4267207 | 1.560267 | 4/4 |
| 2 | openai / gpt-5.6-luna / high | 1 | 0 | 0 | 1 | 0.0% | 未知 | 15.3 | 未知 | 1997570 | 0.095474 | 2/2 |
| 3 | openai / gpt-5.6-sol / high | 1 | 0 | 0 | 1 | 0.0% | 未知 | 13.5 | 未知 | 1959322 | 1.736610 | 2/2 |
| 4 | openai / gpt-5.6-terra / high | 1 | 0 | 0 | 1 | 0.0% | 未知 | 13.5 | 未知 | 1222359 | 0.623214 | 2/2 |
| 5 | openai / gpt-6-luna / high | 1 | 0 | 1 | 0 | 0.0% | 30.7 | 未知 | 6.00 | 4284069 | 0.126062 | 14/14 |
| 6 | openai / gpt-6-astra / high | 0 | 0 | 0 | 0 | 未知 | 未知 | 未知 | 未知 | 1256382 | 2.441924 | 2/2 |
| 7 | openai / gpt-6.1-sol / high | 0 | 0 | 0 | 0 | 未知 | 未知 | 未知 | 未知 | 1653040 | 0.510072 | 2/2 |

## Token 明细与平均费用

缓存计数属于输入，reasoning 属于输出，均不重复加到总 token 或费用。费用均值仅使用费用完整的已完成样本，样本数另列。

| 条件 / 模型 / effort | 输入 | 缓存读取 | 缓存写入 | 输出 | reasoning | 总 token | usage 覆盖 | 平均 token / 样本 | 平均费用 USD | 费用均值样本 |
|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|
| bb5c0fac / gpt-5.6-luna / high | 1976400 | 1806720 | 0 | 21170 | 9055 | 1997570 | 2/2 | 1997570 / 1 | 0.095474 | 1 |
| bb5c0fac / gpt-5.6-sol / high | 1940784 | 1777024 | 0 | 18538 | 7425 | 1959322 | 2/2 | 1959322 / 1 | 1.736610 | 1 |
| bb5c0fac / gpt-5.6-terra / high | 1210144 | 1079808 | 0 | 12215 | 6294 | 1222359 | 2/2 | 1222359 / 1 | 0.623214 | 1 |
| bb5c0fac / gpt-6-astra / high | 1250431 | 1151104 | 0 | 5951 | 1269 | 1256382 | 2/2 | 未知 / 0 | 未知 | 0 |
| bb5c0fac / gpt-6-luna / high | 4232264 | 3589632 | 0 | 51805 | 29869 | 4284069 | 14/14 | 4284069 / 1 | 0.126062 | 1 |
| bb5c0fac / gpt-6-sol / high | 4243353 | 3980544 | 0 | 23854 | 12566 | 4267207 | 4/4 | 4267207 / 1 | 1.560267 | 1 |
| bb5c0fac / gpt-6.1-sol / high | 1646253 | 1500160 | 0 | 6787 | 2685 | 1653040 | 2/2 | 未知 / 0 | 未知 | 0 |

## 停止原因

| 原因 | 次数 |
|---|---:|
| audit_budget_exhausted | 1 |
| awaiting_input | 2 |
| converged | 1 |
| execution_error | 3 |

## 错误类型与阶段

| 维度 | 值 | 次数 |
|---|---|---:|
| error_types | EXECUTION_ENVIRONMENT_ERROR | 3 |
| error_stages | repair-01 | 3 |

## 逐次记录

| Run | 协议 / 版本 | 模型 / effort | 统计结果 / 停止原因 | 原始状态 | 耗时 min | 审计 / 修复 | 已知 token | 已知费用 USD | 费用覆盖 |
|---|---|---|---|---|---:|---|---:|---:|---|
| `run-dea4e047b394496cb32bce1cddf05803` | spec-mrac-v2@5 | gpt-5.6-sol / high | ERROR / execution_error | EXECUTION_ENVIRONMENT_ERROR | 13.5 | 1 / 1 | 1959322 | 1.736610 | 2/2 |
| `run-0b93dfb52f304db08ce3df05ebe9106d` | spec-mrac-v2@5 | gpt-5.6-luna / high | ERROR / execution_error | EXECUTION_ENVIRONMENT_ERROR | 15.3 | 1 / 1 | 1997570 | 0.095474 | 2/2 |
| `run-7ab55a443654423aad3546d54ec6cff9` | spec-mrac-v2@5 | gpt-5.6-terra / high | ERROR / execution_error | EXECUTION_ENVIRONMENT_ERROR | 13.5 | 1 / 1 | 1222359 | 0.623214 | 2/2 |
| `run-c845e2ead673474d8295998d73a03cbf` | spec-mrac-v2@5 | gpt-6-sol / high | CONVERGED / converged | CONVERGED | 16.5 | 3 / 1 | 4267207 | 1.560267 | 4/4 |
| `run-f042c90c33eb4bb6a7d8505b67c40742` | spec-mrac-v2@5 | gpt-6-luna / high | NON_CONVERGED / audit_budget_exhausted | NON_CONVERGED | 30.7 | 8 / 6 | 4284069 | 0.126062 | 14/14 |
| `run-adbaa66d40cc45408ef5897b9c6265ea` | spec-mrac-v2@5 | gpt-6-astra / high | 排除/待完成 / awaiting_input | NEEDS_INPUT | 8.6 | 1 / 1 | 1256382 | 2.441924 | 2/2 |
| `run-458607b05f204752a2aadeae9382be36` | spec-mrac-v2@5 | gpt-6.1-sol / high | 排除/待完成 / awaiting_input | NEEDS_INPUT | 10.7 | 1 / 1 | 1653040 | 0.510072 | 2/2 |

完整输入/缓存/输出/reasoning token、单价、缺失项、错误阶段和运行条件见 [spec-v5-harness2-case3-gpt-repeat-oct24-results.json](spec-v5-harness2-case3-gpt-repeat-oct24-results.json)。

逐次运行的 `evidence_path` 相对于 bench home；原始执行目录不随本报告提交。
