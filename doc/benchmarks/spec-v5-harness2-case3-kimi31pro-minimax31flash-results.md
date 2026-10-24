# Spec v5 / harness v2：case3 Kimi K3.1 Pro 与 MiniMax M3.1 Flash（各 1 次）

经 OpenRouter Responses 接口使用 `moonshotai/kimi-k3.1-pro` 与 `minimax/minimax-m3.1-flash`。模型目录在 2026-10-24 从 OpenRouter API 保存，两个模型均支持 high；实际服务别名与 canonical slug 分别保留，不将服务别名视为不可变权重版本。
case3 v1、spec-mrac-v2@5、harness v2、high、unelevated、8 轮审计预算、每次 agent 调用上限 1800 秒。两模型各 1 次，并发 2，无自动重试；第 8 轮只审计。与 10 月 17 日补测使用同一份运行代码和 Codex CLI 0.159.2。暂停与预算耗尽计未收敛，执行错误另列。收敛是流程结果，单次样本不作稳定能力排名。OpenRouter 费用按响应返回的 usage.cost 汇总，缺失记未知。

# C000003 · case3 · case v1

生成时间（UTC）：2026-10-24T00:54:05.499659+00:00

统计样本 2：收敛 1，未收敛 0，错误 1；排除/待完成 0。

PAUSED 统一计未收敛。正常耗时和修复均值包含收敛与未收敛；错误耗时另列。

## Token 与 API 费用

价格日期：2026-10-02；standard / short context；USD。
已知 token 小计：5858674；usage 覆盖：82/82 调用。
已知 API 费用小计：$0.778612；费用覆盖：82/82 调用。
其中 OpenRouter 实际返回费用：$0.778612；其他 API 预估费用：$0.000000。
其中排除/待完成记录的已知费用：$0.000000。

OpenRouter 直接汇总 usage.cost（账户收费），缺失费用记未知，不用本地单价推算。其他 provider 按本地单价估算。小计包含错误、取消和进行中记录的已知消耗。
CLI usage 是多个 API 请求的累计值；上下文档位为估算选择，不按累计输入量触发长上下文加价。

## 模型汇总

收敛率 = 收敛次数 / 已完成样本数（含错误，排除取消、待输入和进行中记录）。按收敛率降序排列，无已完成样本的行放在最后。

| 排名 | Provider / 模型 / effort | 样本 | 收敛 | 未收敛 | 错误 | 收敛率 | 正常均时 min | 错误均时 min | 平均修复 | 已知 token | 已知费用 USD | 费用覆盖 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | openrouter / moonshotai/kimi-k3.1-pro / high | 1 | 1 | 0 | 0 | 100.0% | 6.0 | 未知 | 0.00 | 1308375 | 0.706926 | 24/24 |
| 2 | openrouter / minimax/minimax-m3.1-flash / high | 1 | 0 | 0 | 1 | 0.0% | 未知 | 10.2 | 未知 | 4550299 | 0.071686 | 58/58 |

## Token 明细与平均费用

缓存计数属于输入，reasoning 属于输出，均不重复加到总 token 或费用。费用均值仅使用费用完整的已完成样本，样本数另列。

| 条件 / 模型 / effort | 输入 | 缓存读取 | 缓存写入 | 输出 | reasoning | 总 token | usage 覆盖 | 平均 token / 样本 | 平均费用 USD | 费用均值样本 |
|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|
| 74e69385 / minimax/minimax-m3.1-flash / high | 4521125 | 4405248 | 未知 | 29174 | 0 | 4550299 | 58/58 | 4550299 / 1 | 0.071686 | 1 |
| 74e69385 / moonshotai/kimi-k3.1-pro / high | 1295679 | 1117440 | 未知 | 12696 | 5239 | 1308375 | 24/24 | 1308375 / 1 | 0.706926 | 1 |

## 停止原因

| 原因 | 次数 |
|---|---:|
| converged | 1 |
| execution_error | 1 |

## 错误类型与阶段

| 维度 | 值 | 次数 |
|---|---|---:|
| error_types | AUDIT_INVALID | 1 |
| error_stages | audit-01 | 1 |

## 逐次记录

| Run | 协议 / 版本 | 模型 / effort | 统计结果 / 停止原因 | 原始状态 | 耗时 min | 审计 / 修复 | 已知 token | 已知费用 USD | 费用覆盖 |
|---|---|---|---|---|---:|---|---:|---:|---|
| `run-4523278275104ec0b760026fead6b5b9` | spec-mrac-v2@5 | moonshotai/kimi-k3.1-pro / high | CONVERGED / converged | CONVERGED | 6.0 | 2 / 0 | 1308375 | 0.706926 | 24/24 |
| `run-809dc3b5ffc04627b38db949ee1bde32` | spec-mrac-v2@5 | minimax/minimax-m3.1-flash / high | ERROR / execution_error | AUDIT_INVALID | 10.2 | 1 / 0 | 4550299 | 0.071686 | 58/58 |

完整输入/缓存/输出/reasoning token、单价、缺失项、错误阶段和运行条件见 [spec-v5-harness2-case3-kimi31pro-minimax31flash-results.json](spec-v5-harness2-case3-kimi31pro-minimax31flash-results.json)。

逐次运行的 `evidence_path` 相对于 bench home；原始执行目录不随本报告提交。
