# Spec v4：case3 外部模型实验（4 次）

OpenRouter 的 GLM 5.3 Flash 和 DeepSeek 官方 V4.1 Flash 各测试两次；API 名称分别为 `z-ai/glm-5.3-flash` 和 `deepseek-flash`。
case3 v1 初始 Spec、三份附件、实现前基线、协议提示词、运行代码和 CLI 沿用[原始 14 次实验](spec-v4-case3-results.md)。
实际协议为 `spec-mrac-v2@4`，high，8 轮审计预算，每次调用 1800 秒超时，unelevated 沙箱。历史 @4 保留末轮修复。新批次 4 并行，各服务 2 并行；同期 GPT 批次最多 10 并行。

统计口径沿用原报告：暂停与预算耗尽计未收敛，调用错误单列。不同服务、模型目录和同期并发会影响工具支持及耗时，原始配置保留在 JSON 和运行证据中。OpenRouter 费用优先使用返回的实际费用；未定价的 DeepSeek 消耗不记为零费用。

模型名称依据：[OpenRouter GLM 页面](https://openrouter.ai/z-ai/glm-5.3-flash)、[DeepSeek 官方更新记录](https://api-docs.deepseek.com/updates/)。

# C000003 · case3 · case v1

生成时间（UTC）：2026-10-04T23:21:43.705015+00:00

统计样本 4：收敛 0，未收敛 0，错误 4；排除/待完成 0。

PAUSED 统一计未收敛。正常耗时和修复均值包含收敛与未收敛；错误耗时另列。

## Token 与 API 费用

价格日期：2026-10-02；standard / short context；USD。
已知 token 小计：30895592；usage 覆盖：326/329 调用。
已知 API 费用小计：$0.896965；费用覆盖：324/329 调用。
其中 OpenRouter 实际返回费用：$0.896965；其他 API 预估费用：$0.000000。
其中排除/待完成记录的已知费用：$0.000000。

OpenRouter 直接汇总 usage.cost（账户收费），缺失费用记未知，不用本地单价推算。其他 provider 按本地单价估算。小计包含错误、取消和进行中记录的已知消耗。
CLI usage 是多个 API 请求的累计值；上下文档位为估算选择，不按累计输入量触发长上下文加价。

## 模型汇总

收敛率 = 收敛次数 / 已完成样本数（含错误，排除取消、待输入和进行中记录）。按收敛率降序排列，无已完成样本的行放在最后。

| 排名 | Provider / 模型 / effort | 样本 | 收敛 | 未收敛 | 错误 | 收敛率 | 正常均时 min | 错误均时 min | 平均修复 | 已知 token | 已知费用 USD | 费用覆盖 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | deepseek / deepseek-flash / high | 2 | 0 | 0 | 2 | 0.0% | 未知 | 34.5 | 未知 | 6347513 | 0.000000 | 0/4 |
| 2 | openrouter / z-ai/glm-5.3-flash / high | 2 | 0 | 0 | 2 | 0.0% | 未知 | 52.0 | 未知 | 24548079 | 0.896965 | 324/325 |

## Token 明细与平均费用

缓存计数属于输入，reasoning 属于输出，均不重复加到总 token 或费用。费用均值仅使用费用完整的已完成样本，样本数另列。

| 条件 / 模型 / effort | 输入 | 缓存读取 | 缓存写入 | 输出 | reasoning | 总 token | usage 覆盖 | 平均 token / 样本 | 平均费用 USD | 费用均值样本 |
|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|
| 75ba06e4 / deepseek-flash / high | 6268575 | 6047104 | 0 | 78938 | 64372 | 6347513 | 2/4 | 未知 / 0 | 未知 | 0 |
| f753267f / z-ai/glm-5.3-flash / high | 24421889 | 22770944 | 未知 | 126190 | 93269 | 24548079 | 324/325 | 18893729 / 1 | 0.687174 | 1 |

## 停止原因

| 原因 | 次数 |
|---|---:|
| execution_error | 4 |

## 错误类型与阶段

| 维度 | 值 | 次数 |
|---|---|---:|
| error_types | FIX_INVALID | 1 |
| error_types | TIMEOUT | 3 |
| error_stages | repair-01 | 3 |
| error_stages | repair-04 | 1 |

## 逐次记录

| Run | 协议 / 版本 | 模型 / effort | 统计结果 / 停止原因 | 原始状态 | 耗时 min | 审计 / 修复 | 已知 token | 已知费用 USD | 费用覆盖 |
|---|---|---|---|---|---:|---|---:|---:|---|
| [run-0e6d1e2346da4033bba8ab1d93aa8328](batches/B-5226d43a0bc92317b784963a/runs/run-0e6d1e2346da4033bba8ab1d93aa8328/) | spec-mrac-v2@4 | z-ai/glm-5.3-flash / high | ERROR / execution_error | TIMEOUT | 40.2 | 1 / 1 | 5654350 | 0.209791 | 64/65 |
| [run-8481360cb849432587d6ed18fd1aa977](batches/B-5226d43a0bc92317b784963a/runs/run-8481360cb849432587d6ed18fd1aa977/) | spec-mrac-v2@4 | z-ai/glm-5.3-flash / high | ERROR / execution_error | FIX_INVALID | 63.9 | 4 / 4 | 18893729 | 0.687174 | 260/260 |
| [run-d535ca60042c48c5888d9a5d89ec7e30](batches/B-5226d43a0bc92317b784963a/runs/run-d535ca60042c48c5888d9a5d89ec7e30/) | spec-mrac-v2@4 | deepseek-flash / high | ERROR / execution_error | TIMEOUT | 34.1 | 1 / 1 | 3078334 | 0.000000 | 0/2 |
| [run-ed97485e57cf4c5983e37160a84cde75](batches/B-5226d43a0bc92317b784963a/runs/run-ed97485e57cf4c5983e37160a84cde75/) | spec-mrac-v2@4 | deepseek-flash / high | ERROR / execution_error | TIMEOUT | 34.9 | 1 / 1 | 3269179 | 0.000000 | 0/2 |

完整输入/缓存/输出/reasoning token、单价、缺失项、错误阶段和运行条件见 [statistics.json](statistics.json)。
