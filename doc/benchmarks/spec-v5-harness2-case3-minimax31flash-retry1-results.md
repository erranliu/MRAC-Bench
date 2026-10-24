# Spec v5 / harness v2：case3 MiniMax M3.1 Flash 第 2 次运行

用户要求在首轮 AUDIT_INVALID 后重新尝试一次。本次新建独立运行，前次记录保留。经 OpenRouter Responses 接口使用 `minimax/minimax-m3.1-flash`；case3 v1、spec-mrac-v2@5、harness v2、high、unelevated、8 轮审计预算、每次 agent 调用上限 1800 秒。与前次 provider、模型目录、提示、协议、代码与 CLI 配置一致。本批次并发 1，与 GPT 批次共用调度总上限 6；无自动重试，第 8 轮只审计。本报告仅包含本次样本，两个 MiniMax 样本并列保留在统一汇总中。收敛是流程结果，执行错误另列。OpenRouter 费用取返回 usage.cost，缺失记未知。

# C000003 · case3 · case v1

生成时间（UTC）：2026-10-24T01:48:23.389121+00:00

统计样本 1：收敛 0，未收敛 0，错误 1；排除/待完成 0。

PAUSED 统一计未收敛。正常耗时和修复均值包含收敛与未收敛；错误耗时另列。

## Token 与 API 费用

价格日期：2026-10-02；standard / short context；USD。
已知 token 小计：6025298；usage 覆盖：107/107 调用。
已知 API 费用小计：$0.110861；费用覆盖：107/107 调用。
其中 OpenRouter 实际返回费用：$0.110861；其他 API 预估费用：$0.000000。
其中排除/待完成记录的已知费用：$0.000000。

OpenRouter 直接汇总 usage.cost（账户收费），缺失费用记未知，不用本地单价推算。其他 provider 按本地单价估算。小计包含错误、取消和进行中记录的已知消耗。
CLI usage 是多个 API 请求的累计值；上下文档位为估算选择，不按累计输入量触发长上下文加价。

## 模型汇总

收敛率 = 收敛次数 / 已完成样本数（含错误，排除取消、待输入和进行中记录）。按收敛率降序排列，无已完成样本的行放在最后。

| 排名 | Provider / 模型 / effort | 样本 | 收敛 | 未收敛 | 错误 | 收敛率 | 正常均时 min | 错误均时 min | 平均修复 | 已知 token | 已知费用 USD | 费用覆盖 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | openrouter / minimax/minimax-m3.1-flash / high | 1 | 0 | 0 | 1 | 0.0% | 未知 | 37.3 | 未知 | 6025298 | 0.110861 | 107/107 |

## Token 明细与平均费用

缓存计数属于输入，reasoning 属于输出，均不重复加到总 token 或费用。费用均值仅使用费用完整的已完成样本，样本数另列。

| 条件 / 模型 / effort | 输入 | 缓存读取 | 缓存写入 | 输出 | reasoning | 总 token | usage 覆盖 | 平均 token / 样本 | 平均费用 USD | 费用均值样本 |
|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|
| 74e69385 / minimax/minimax-m3.1-flash / high | 5966357 | 5757696 | 未知 | 58941 | 0 | 6025298 | 107/107 | 6025298 / 1 | 0.110861 | 1 |

## 停止原因

| 原因 | 次数 |
|---|---:|
| execution_error | 1 |

## 错误类型与阶段

| 维度 | 值 | 次数 |
|---|---|---:|
| error_types | AUDIT_INVALID | 1 |
| error_stages | audit-02 | 1 |

## 逐次记录

| Run | 协议 / 版本 | 模型 / effort | 统计结果 / 停止原因 | 原始状态 | 耗时 min | 审计 / 修复 | 已知 token | 已知费用 USD | 费用覆盖 |
|---|---|---|---|---|---:|---|---:|---:|---|
| `run-85ae652f3da24e55b126d7bd5415f7e9` | spec-mrac-v2@5 | minimax/minimax-m3.1-flash / high | ERROR / execution_error | AUDIT_INVALID | 37.3 | 2 / 1 | 6025298 | 0.110861 | 107/107 |

完整输入/缓存/输出/reasoning token、单价、缺失项、错误阶段和运行条件见 [spec-v5-harness2-case3-minimax31flash-retry1-results.json](spec-v5-harness2-case3-minimax31flash-retry1-results.json)。

逐次运行的 `evidence_path` 相对于 bench home；原始执行目录不随本报告提交。
