# Spec v4：case3 模型测试结果

统计快照日期：**2026-10-05（Asia/Shanghai）**。case3 v1，源包 ID 为 `steerline-pr196`。
本表仅包含批次 `B-bc2f373c3b6f676478ae126d` 的 **14 次完整流程测试**：7 个模型各 2 次，thinking effort 均为 high。
统一统计结果为 **6 次收敛、8 次未收敛、0 次错误**；未收敛包括 7 次六轮暂停和 1 次审计预算耗尽。

## 固定输入与运行条件

- 原始输入：SteerLine [PR #196](https://github.com/erranliu/SteerLine/pull/196) 初始提交 `6dd144b0b5f0de21e023675d15271aa700bdc6ba` 的 Spec；没有以模型修订稿替换输入。
- 实现前仓库基线：`6dce3cf1abf293e18bd78f92677c72a3fa2f0210`。
- Spec SHA-256：`63077ed93d2106435ff1c0a97170e0aa7efd4303d7c69be2fcd379de86e733be`；同一 Spec 和三份附件用于全部 14 次实验，附件哈希保存在随文 JSON。
- 实际协议：**`spec-mrac-v2@4`**。每轮结合固定仓库独立审计，所有发现直接进入修复，无独立裁决；同一 Spec 与基线上连续两次零 findings 才收敛。
- 总审计预算 8 轮、每次调用超时 1800 秒、并发上限 4、Windows sandbox 为 `unelevated`。
- Codex CLI：`codex-cli 0.159.2`；运行代码 SHA-256：`68ed722f39a4b00f365f2ac2e948c1cc0ed55f883cabcb448bcbc225ff3b36ca`；协议/提示词身份：`acd5336a8b92bd177be1389290cb64b56657d38863e4b26987ab283917ce370d`。
- 本次使用历史 @4：预算末轮有发现时仍完成该轮修复。当前默认 @5 的“末轮只审计、不修复”规则不适用于本表。

## 统计口径

- 每个 run ID 计一次；`CONVERGED` 计收敛，原始 `PAUSED` 与 `NON_CONVERGED` 统一计未收敛，执行/解析错误单列。原始状态和停止原因保留在 JSON。
- 正常流程包含收敛与未收敛；耗时和平均修复轮次均使用这个集合，暂停样本也计入。
- 耗时来自 runner 的 `usage.wall_time_seconds`，包含准备、审计、修复及检查点保存，不含事后的报告导出；并行 run 各自计时。
- 修复轮次使用已启动的 `repair_rounds`，不是发现项数。均值保留两位小数；分钟均值与范围保留一位小数。
- 先前未注册 trial、误选 Simple 的诊断、人工中止实验均不进入这 14 次统计。

## 按模型与 thinking effort 汇总

| 模型 / effort | 次数 | 收敛 | 未收敛 | 错误 | 平均耗时（范围，分钟） | 平均修复轮次 | 平均审计轮次 |
|---|---:|---:|---:|---:|---|---:|---:|
| GPT-5.6 Sol / high | 2 | 0 | 2 | 0 | 53.9（53.8–54.0） | 6.00 | 6.00 |
| GPT-5.6 Terra / high | 2 | 0 | 2 | 0 | 24.7（24.1–25.4） | 6.00 | 6.00 |
| GPT-5.6 Luna / high | 2 | 0 | 2 | 0 | 48.2（48.2–48.3） | 6.00 | 6.00 |
| GPT-6 Astra / high | 2 | 2 | 0 | 0 | 25.9（11.5–40.4） | 2.50 | 5.00 |
| GPT-6 Sol / high | 2 | 1 | 1 | 0 | 33.1（19.4–46.8） | 4.00 | 5.00 |
| GPT-6 Luna / high | 2 | 1 | 1 | 0 | 21.2（7.1–35.2） | 3.50 | 5.50 |
| GPT-6.1 Sol / high | 2 | 2 | 0 | 0 | 24.0（16.0–32.1） | 1.50 | 3.50 |

## 逐次结果

| 模型 | 第一次：原始状态 / 审计 / 修复 / 分钟 | 第二次：原始状态 / 审计 / 修复 / 分钟 |
|---|---|---|
| GPT-5.6 Sol | PAUSED / 6 / 6 / 54.0 | PAUSED / 6 / 6 / 53.8 |
| GPT-5.6 Terra | PAUSED / 6 / 6 / 24.1 | PAUSED / 6 / 6 / 25.4 |
| GPT-5.6 Luna | PAUSED / 6 / 6 / 48.3 | PAUSED / 6 / 6 / 48.2 |
| GPT-6 Astra | CONVERGED / 7 / 4 / 40.4 | CONVERGED / 3 / 1 / 11.5 |
| GPT-6 Sol | PAUSED / 6 / 6 / 46.8 | CONVERGED / 4 / 2 / 19.4 |
| GPT-6 Luna | CONVERGED / 3 / 1 / 7.1 | NON_CONVERGED / 8 / 6 / 35.2 |
| GPT-6.1 Sol | CONVERGED / 4 / 2 / 32.1 | CONVERGED / 3 / 1 / 16.0 |

## 结果解释与范围

- GPT-6 Astra 与 GPT-6.1 Sol 各 2/2 收敛；GPT-6 Sol 与 GPT-6 Luna 各 1/2；三个 GPT-5.6 模型合计 0/6。
- 7 个暂停样本均在连续六轮报告 P0–P2 并完成修复后停止；这些是正常的未收敛结果，未继续 resume。GPT-6 Luna 第二次用满 8 轮预算。
- 14 次终态执行/解析错误均为零。每个模型只有两次样本，表中差异用于描述本 case 的收敛表现；同一模型的耗时、发现和修复轨迹也有波动。
- 不将历史 Simple case1 汇总混入本表，也不将 @4 的本次结果登记为 @5 的真实模型验证。收敛表示达到协议条件，产品代码未实施或执行测试。

## 数据与复算

[随文数据](spec-v4-case3-results.json)沿用标准 `statistics.json` 的 schema 1，保存 14 个 run 的 batch/run ID、模型、effort、归一化状态、原始状态、停止原因、耗时、审计/修复次数、条件身份及逐次调用 usage。
`archive` 补充快照范围、输入来源和并发条件；显示名称按注册表使用 `case3`，提交时名称 `steerline-pr196` 保留在 `archive.case_name_at_submission`。

复算模型表时按 `model` 和 `reasoning_effort` 分组，选 `included == true` 且 `status` 为 `CONVERGED` 或 `NON_CONVERGED` 的记录。用 `wall_time_seconds / 60` 和 `repair_rounds` 求算术平均；`raw_status == PAUSED` 已归为未收敛。

原始证据未复制进 Git；JSON 的 `evidence_path` 相对 bench-home，按 batch/run ID 可定位本机或导出的完整记录。JSON 同时保留 token 与费用完整度，价表日期为 `2026-10-02`，仅是保存价表下的 API 等价估算，不是 Codex 订阅账单。
