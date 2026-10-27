# case3 v1 · v2 / v3 合并有效成绩

整理时间：2026-10-27T10:06:42+08:00。共 **13 次有效运行：9 次收敛、4 次未收敛**。

| 模型 | 来源 | 有效次数 | 平均时间 | 收敛率 | 平均审计次数 |
|---|---|---:|---:|---:|---:|
| gpt-5.6-sol | openai | 1 | 22.7 分钟 | 100%（1/1） | 4.0 |
| gpt-5.6-luna | openai | 1 | 56.3 分钟 | 0%（0/1） | 6.0 |
| gpt-6-sol | openai | 1 | 16.5 分钟 | 100%（1/1） | 3.0 |
| gpt-6-luna | openai | 2 | 29.0 分钟 | 50%（1/2） | 7.5 |
| gpt-6-astra | openai | 2 | 6.5 分钟 | 100%（2/2） | 2.5 |
| gpt-6.1-sol | openai | 2 | 3.9 分钟 | 100%（2/2） | 2.0 |
| gpt-6.1-terra | openai | 1 | 5.1 分钟 | 100%（1/1） | 4.0 |
| Kimi K3.1 Pro | openrouter | 1 | 6.0 分钟 | 100%（1/1） | 2.0 |
| DeepSeek V4.1 Flash（10-05） | deepseek | 1 | 50.7 分钟 | 0%（0/1） | 6.0 |
| DeepSeek V4.1 Flash 1017 | deepseek | 1 | 96.0 分钟 | 0%（0/1） | 8.0 |

有效运行包括收敛及预算耗尽或暂停的未收敛；执行错误、待输入和独立修复重放不计入。平均时间取完整流程 wall time；收敛率分母是有效运行次数。没有有效样本的模型不列入表格。

数据来自同一 case3 v1、high、unelevated 和 8 轮审计预算，但 v2 对应 Spec @5、v3 对应 Spec @6；@6 的修复阶段增加仓库 MCP 和提示词调整。批次并发不同，样本数仅 1–2 次，不能以合并均值推断版本影响或稳定排名。收敛表示连续两轮审计未报告发现，不是独立质量评分。

DeepSeek 两次使用同一服务别名，但测试批次和当时发布信息不同，分别列出；别名不代表不可变权重 ID。

- [合并数据 JSON](case3-v2-v3-valid-results.json)：保留每条 run ID、版本、条件和耗时。
- [v3 正式成绩及审计发现](spec-v6-harness3-case3-results.md)
- [v2 全部成绩和排除原因](spec-v5-harness2-case3-all-results.md)
- [v2 有效成绩](spec-v5-harness2-case3-valid-results.md)
