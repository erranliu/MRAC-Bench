# Spec v5 / harness v2：case3 DeepSeek V4.1 Flash 1017（1 次）

使用 DeepSeek 官方 Responses 服务及 `deepseek-flash`。[2026-10-17 官方更新记录](https://api-docs.deepseek.com/updates/)说明该 API 名称已指向 DeepSeek-V4.1-Flash-1017；这是服务别名，未声称固定不可变的权重版本。
case3 v1、high、unelevated、8 轮审计预算、1800 秒整轮上限，单并行、无自动重试。使用 spec-mrac-v2@5，末轮只审计；harness v2 使用 30 秒完成后收尾窗口和 300 秒单工具上限。
保留上一批模型目录配置及 Codex CLI 0.159.2。因旧工作树已清理，本次从独立源代码快照运行，实际代码身份单独记录和分组。单次结果不能证明稳定的能力差异。

统计口径沿用原报告：暂停与预算耗尽计未收敛，执行错误另列；费用为标准报告按保存价格表处理的估计或未知值，不代表 DeepSeek 实际账单。

# C000003 · case3 · case v1

生成时间（UTC）：2026-10-17T14:46:01.593941+00:00

统计样本 1：收敛 0，未收敛 1，错误 0；排除/待完成 0。

PAUSED 统一计未收敛。正常耗时和修复均值包含收敛与未收敛；错误耗时另列。

## Token 与 API 费用

价格日期：2026-10-02；standard / short context；USD。
已知 token 小计：117183110；usage 覆盖：14/14 调用。
已知 API 费用小计：$0.000000；费用覆盖：0/14 调用。
其中 OpenRouter 实际返回费用：$0.000000；其他 API 预估费用：$0.000000。
其中排除/待完成记录的已知费用：$0.000000。

OpenRouter 直接汇总 usage.cost（账户收费），缺失费用记未知，不用本地单价推算。其他 provider 按本地单价估算。小计包含错误、取消和进行中记录的已知消耗。
CLI usage 是多个 API 请求的累计值；上下文档位为估算选择，不按累计输入量触发长上下文加价。

## 模型汇总

收敛率 = 收敛次数 / 已完成样本数（含错误，排除取消、待输入和进行中记录）。按收敛率降序排列，无已完成样本的行放在最后。

| 排名 | Provider / 模型 / effort | 样本 | 收敛 | 未收敛 | 错误 | 收敛率 | 正常均时 min | 错误均时 min | 平均修复 | 已知 token | 已知费用 USD | 费用覆盖 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | deepseek / deepseek-flash / high | 1 | 0 | 1 | 0 | 0.0% | 96.0 | 未知 | 6.00 | 117183110 | 0.000000 | 0/14 |

## Token 明细与平均费用

缓存计数属于输入，reasoning 属于输出，均不重复加到总 token 或费用。费用均值仅使用费用完整的已完成样本，样本数另列。

| 条件 / 模型 / effort | 输入 | 缓存读取 | 缓存写入 | 输出 | reasoning | 总 token | usage 覆盖 | 平均 token / 样本 | 平均费用 USD | 费用均值样本 |
|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|
| 1bfcfa0f / deepseek-flash / high | 116092608 | 114296832 | 0 | 1090502 | 901743 | 117183110 | 14/14 | 117183110 / 1 | 未知 | 0 |

## 停止原因

| 原因 | 次数 |
|---|---:|
| audit_budget_exhausted | 1 |

## 错误类型与阶段

| 维度 | 值 | 次数 |
|---|---|---:|

## 逐次记录

| Run | 协议 / 版本 | 模型 / effort | 统计结果 / 停止原因 | 原始状态 | 耗时 min | 审计 / 修复 | 已知 token | 已知费用 USD | 费用覆盖 |
|---|---|---|---|---|---:|---|---:|---:|---|
| `run-4e15e1e82db2403aab37a29967392a7f` | spec-mrac-v2@5 | deepseek-flash / high | NON_CONVERGED / audit_budget_exhausted | NON_CONVERGED | 96.0 | 8 / 6 | 117183110 | 0.000000 | 0/14 |

完整输入/缓存/输出/reasoning token、单价、缺失项、错误阶段和运行条件见 [spec-v5-harness2-case3-deepseek-flash-1017-results.json](spec-v5-harness2-case3-deepseek-flash-1017-results.json)。

逐次运行的 `evidence_path` 相对于 bench home；原始执行目录不随本报告提交。
