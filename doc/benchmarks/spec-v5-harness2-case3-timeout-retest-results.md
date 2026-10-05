# Spec v5 / harness v2：case3 超时配置复测（6 次）

从前两批实际 TIMEOUT 记录选择模型及服务配置，每种只跑一次：GPT-5.6 Sol/Luna、GPT-6 Sol/Luna、OpenRouter GLM 5.3 Flash、DeepSeek 官方 V4.1 Flash（API 名称 `deepseek-flash`）。
case3 v1 原始 Spec、三份附件和实现前基线保持一致。high、unelevated、1800 秒整轮上限、8 轮审计预算、6 并行，无自动重试或续跑。
实际协议 `spec-mrac-v2@5`：末轮审计后结束，不执行末轮修复。harness v2 采用 30 秒完成后收尾窗口、300 秒单工具上限及 UTF-8 Spec MCP；逐阶段完成验证、清理原因和时间记录保存在 JSON 的 execution_invocations 中。

这次同时改变协议 @4→@5 和 harness 条件，单次复测不能单独证明 harness 的因果效果。原始结果保留，统计口径沿用[原报告](spec-v4-case3-results.md)。非工具时间包括模型等待与客户端开销，不能当作纯 API 延迟；费用按标准报告记录实际返回值或保存价格表估算。

# C000003 · case3 · case v1

生成时间（UTC）：2026-10-05T01:10:48.216770+00:00

统计样本 6：收敛 2，未收敛 2，错误 2；排除/待完成 0。

PAUSED 统一计未收敛。正常耗时和修复均值包含收敛与未收敛；错误耗时另列。

## Token 与 API 费用

价格日期：2026-10-02；standard / short context；USD。
已知 token 小计：95340772；usage 覆盖：191/193 调用。
已知 API 费用小计：$12.795911；费用覆盖：179/193 调用。
其中 OpenRouter 实际返回费用：$0.362486；其他 API 预估费用：$12.433425。
其中排除/待完成记录的已知费用：$0.000000。

OpenRouter 直接汇总 usage.cost（账户收费），缺失费用记未知，不用本地单价推算。其他 provider 按本地单价估算。小计包含错误、取消和进行中记录的已知消耗。
CLI usage 是多个 API 请求的累计值；上下文档位为估算选择，不按累计输入量触发长上下文加价。

## 模型汇总

收敛率 = 收敛次数 / 已完成样本数（含错误，排除取消、待输入和进行中记录）。按收敛率降序排列，无已完成样本的行放在最后。

| 排名 | Provider / 模型 / effort | 样本 | 收敛 | 未收敛 | 错误 | 收敛率 | 正常均时 min | 错误均时 min | 平均修复 | 已知 token | 已知费用 USD | 费用覆盖 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | openai / gpt-5.6-sol / high | 1 | 1 | 0 | 0 | 100.0% | 22.7 | 未知 | 2.00 | 9362733 | 6.969111 | 6/6 |
| 2 | openai / gpt-6-luna / high | 1 | 1 | 0 | 0 | 100.0% | 27.3 | 未知 | 3.00 | 4917095 | 0.126000 | 10/10 |
| 3 | deepseek / deepseek-flash / high | 1 | 0 | 1 | 0 | 0.0% | 50.7 | 未知 | 6.00 | 38454872 | 0.000000 | 0/12 |
| 4 | openai / gpt-5.6-luna / high | 1 | 0 | 1 | 0 | 0.0% | 56.3 | 未知 | 6.00 | 17785693 | 0.833409 | 12/12 |
| 5 | openai / gpt-6-sol / high | 1 | 0 | 0 | 1 | 0.0% | 未知 | 38.9 | 未知 | 11447749 | 4.504904 | 10/11 |
| 6 | openrouter / z-ai/glm-5.3-flash / high | 1 | 0 | 0 | 1 | 0.0% | 未知 | 46.5 | 未知 | 13372630 | 0.362486 | 141/142 |

## Token 明细与平均费用

缓存计数属于输入，reasoning 属于输出，均不重复加到总 token 或费用。费用均值仅使用费用完整的已完成样本，样本数另列。

| 条件 / 模型 / effort | 输入 | 缓存读取 | 缓存写入 | 输出 | reasoning | 总 token | usage 覆盖 | 平均 token / 样本 | 平均费用 USD | 费用均值样本 |
|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|
| e98640f1 / deepseek-flash / high | 37888587 | 36855808 | 0 | 566285 | 451586 | 38454872 | 12/12 | 38454872 / 1 | 未知 | 0 |
| e571df4a / gpt-5.6-luna / high | 17621635 | 16043264 | 0 | 164058 | 94607 | 17785693 | 12/12 | 17785693 / 1 | 0.833409 | 1 |
| e571df4a / gpt-5.6-sol / high | 9309274 | 8704768 | 0 | 53459 | 30930 | 9362733 | 6/6 | 9362733 / 1 | 6.969111 | 1 |
| e571df4a / gpt-6-luna / high | 4872270 | 4262656 | 0 | 44825 | 27547 | 4917095 | 10/10 | 4917095 / 1 | 0.126000 | 1 |
| e571df4a / gpt-6-sol / high | 11375930 | 10536192 | 0 | 71819 | 34020 | 11447749 | 10/11 | 未知 / 0 | 未知 | 0 |
| a211bd8c / z-ai/glm-5.3-flash / high | 13324922 | 12537600 | 未知 | 47708 | 32964 | 13372630 | 141/142 | 未知 / 0 | 未知 | 0 |

## 停止原因

| 原因 | 次数 |
|---|---:|
| converged | 2 |
| early_pause | 2 |
| execution_error | 2 |

## 错误类型与阶段

| 维度 | 值 | 次数 |
|---|---|---:|
| error_types | AGENT_ERROR | 1 |
| error_types | TIMEOUT | 1 |
| error_stages | audit-02 | 1 |
| error_stages | audit-07 | 1 |

## 逐次记录

| Run | 协议 / 版本 | 模型 / effort | 统计结果 / 停止原因 | 原始状态 | 耗时 min | 审计 / 修复 | 已知 token | 已知费用 USD | 费用覆盖 |
|---|---|---|---|---|---:|---|---:|---:|---|
| [run-321dc516c30e4cfca3d9dc3cb2d2438f](batches/B-04350627bf891a30b456b184/runs/run-321dc516c30e4cfca3d9dc3cb2d2438f/) | spec-mrac-v2@5 | gpt-5.6-sol / high | CONVERGED / converged | CONVERGED | 22.7 | 4 / 2 | 9362733 | 6.969111 | 6/6 |
| [run-8e03ce8a5f7a4cdda80ac0ac213ee3ac](batches/B-04350627bf891a30b456b184/runs/run-8e03ce8a5f7a4cdda80ac0ac213ee3ac/) | spec-mrac-v2@5 | gpt-5.6-luna / high | NON_CONVERGED / early_pause | PAUSED | 56.3 | 6 / 6 | 17785693 | 0.833409 | 12/12 |
| [run-d34d76e1ed7c44328f582b18f70e708e](batches/B-04350627bf891a30b456b184/runs/run-d34d76e1ed7c44328f582b18f70e708e/) | spec-mrac-v2@5 | gpt-6-sol / high | ERROR / execution_error | AGENT_ERROR | 38.9 | 7 / 4 | 11447749 | 4.504904 | 10/11 |
| [run-6827eb102763484499ebe2697055cefd](batches/B-04350627bf891a30b456b184/runs/run-6827eb102763484499ebe2697055cefd/) | spec-mrac-v2@5 | gpt-6-luna / high | CONVERGED / converged | CONVERGED | 27.3 | 7 / 3 | 4917095 | 0.126000 | 10/10 |
| [run-f71017be96ad46369d0c9ee53529c96d](batches/B-04350627bf891a30b456b184/runs/run-f71017be96ad46369d0c9ee53529c96d/) | spec-mrac-v2@5 | z-ai/glm-5.3-flash / high | ERROR / execution_error | TIMEOUT | 46.5 | 2 / 1 | 13372630 | 0.362486 | 141/142 |
| [run-8a9fa5b3f8c34609af1292cd867457c0](batches/B-04350627bf891a30b456b184/runs/run-8a9fa5b3f8c34609af1292cd867457c0/) | spec-mrac-v2@5 | deepseek-flash / high | NON_CONVERGED / early_pause | PAUSED | 50.7 | 6 / 6 | 38454872 | 0.000000 | 0/12 |

完整输入/缓存/输出/reasoning token、单价、缺失项、错误阶段和运行条件见 [statistics.json](statistics.json)。
