# Spec v4：case3 扩展实验结果（42 次）

在原有 7 个模型各 2 次实验上，新增各 4 次，共 28 次；合计各 6 次、42 次。
新增批次并发为 10，原批次并发为 4；每条运行记录保留批次和并发配置。
固定输入、Spec/附件/基线、运行代码、提示词、CLI、high、unelevated、8 轮审计预算及每次调用 1800 秒超时均与原批次一致。
实际协议仍为历史 `spec-mrac-v2@4`，保留末轮修复行为。

统计口径沿用[原始 14 次报告](spec-v4-case3-results.md)：暂停与预算耗尽计未收敛；正常流程耗时包含收敛与未收敛；调用错误单列。不同并发可能影响耗时，合并均值应结合原始批次解读。费用为保存价格表的 API 等价估算，并非 Codex 账单。

# C000003 · case3 · case v1

生成时间（UTC）：2026-10-04T23:19:47.852215+00:00

统计样本 41：收敛 16，未收敛 15，错误 10；排除/待完成 1。

PAUSED 统一计未收敛。正常耗时和修复均值包含收敛与未收敛；错误耗时另列。

## Token 与 API 费用

价格日期：2026-10-02；standard / short context；USD。
已知 token 小计：282530319；usage 覆盖：349/352 调用。
已知 API 费用小计：$165.925700；费用覆盖：349/352 调用。
其中 OpenRouter 实际返回费用：$0.000000；其他 API 预估费用：$165.925700。
其中排除/待完成记录的已知费用：$0.118548。

OpenRouter 直接汇总 usage.cost（账户收费），缺失费用记未知，不用本地单价推算。其他 provider 按本地单价估算。小计包含错误、取消和进行中记录的已知消耗。
CLI usage 是多个 API 请求的累计值；上下文档位为估算选择，不按累计输入量触发长上下文加价。

## 模型汇总

收敛率 = 收敛次数 / 已完成样本数（含错误，排除取消、待输入和进行中记录）。按收敛率降序排列，无已完成样本的行放在最后。

| 排名 | Provider / 模型 / effort | 样本 | 收敛 | 未收敛 | 错误 | 收敛率 | 正常均时 min | 错误均时 min | 平均修复 | 已知 token | 已知费用 USD | 费用覆盖 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | openai / gpt-6-astra / high | 6 | 6 | 0 | 0 | 100.0% | 16.5 | 未知 | 1.50 | 16397474 | 42.637260 | 31/31 |
| 2 | openai / gpt-6.1-sol / high | 6 | 6 | 0 | 0 | 100.0% | 19.4 | 未知 | 1.17 | 18164953 | 7.742762 | 27/27 |
| 3 | openai / gpt-6-luna / high | 5 | 2 | 2 | 1 | 40.0% | 23.4 | 32.9 | 3.75 | 14615614 | 0.490818 | 54/54 |
| 4 | openai / gpt-5.6-sol / high | 6 | 1 | 3 | 2 | 16.7% | 44.9 | 88.9 | 5.00 | 95938899 | 78.741052 | 66/68 |
| 5 | openai / gpt-6-sol / high | 6 | 1 | 1 | 4 | 16.7% | 33.1 | 36.6 | 4.00 | 28482564 | 11.302394 | 30/30 |
| 6 | openai / gpt-5.6-luna / high | 6 | 0 | 3 | 3 | 0.0% | 48.2 | 66.4 | 6.00 | 72972135 | 3.575980 | 63/64 |
| 7 | openai / gpt-5.6-terra / high | 6 | 0 | 6 | 0 | 0.0% | 30.0 | 未知 | 6.33 | 35958680 | 21.435434 | 78/78 |

## Token 明细与平均费用

缓存计数属于输入，reasoning 属于输出，均不重复加到总 token 或费用。费用均值仅使用费用完整的已完成样本，样本数另列。

| 条件 / 模型 / effort | 输入 | 缓存读取 | 缓存写入 | 输出 | reasoning | 总 token | usage 覆盖 | 平均 token / 样本 | 平均费用 USD | 费用均值样本 |
|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|
| bac4fc3d / gpt-5.6-luna / high | 72262992 | 65153280 | 0 | 709143 | 402270 | 72972135 | 63/64 | 11504976 / 5 | 0.573310 | 5 |
| bac4fc3d / gpt-5.6-sol / high | 95189636 | 88056320 | 0 | 749263 | 393768 | 95938899 | 66/68 | 15032540 / 4 | 12.074107 | 4 |
| bac4fc3d / gpt-5.6-terra / high | 35513814 | 30516992 | 0 | 444866 | 188297 | 35958680 | 78/78 | 5993113 / 6 | 3.572572 | 6 |
| bac4fc3d / gpt-6-astra / high | 16251247 | 14131840 | 0 | 146227 | 16098 | 16397474 | 31/31 | 2732912 / 6 | 7.106210 | 6 |
| bac4fc3d / gpt-6-luna / high | 14388609 | 11794944 | 0 | 227005 | 135464 | 14615614 | 54/54 | 2267951 / 5 | 0.074454 | 5 |
| bac4fc3d / gpt-6-sol / high | 28306393 | 26151168 | 0 | 176171 | 76958 | 28482564 | 30/30 | 4747094 / 6 | 1.883732 | 6 |
| bac4fc3d / gpt-6.1-sol / high | 17994586 | 15763200 | 0 | 170367 | 34988 | 18164953 | 27/27 | 3027492 / 6 | 1.290460 | 6 |

## 停止原因

| 原因 | 次数 |
|---|---:|
| audit_budget_exhausted | 4 |
| awaiting_input | 1 |
| converged | 16 |
| early_pause | 11 |
| execution_error | 10 |

## 错误类型与阶段

| 维度 | 值 | 次数 |
|---|---|---:|
| error_types | TIMEOUT | 10 |
| error_stages | repair-01 | 3 |
| error_stages | repair-02 | 2 |
| error_stages | repair-04 | 2 |
| error_stages | repair-06 | 3 |

## 逐次记录

| Run | 协议 / 版本 | 模型 / effort | 统计结果 / 停止原因 | 原始状态 | 耗时 min | 审计 / 修复 | 已知 token | 已知费用 USD | 费用覆盖 |
|---|---|---|---|---|---:|---|---:|---:|---|
| [run-cd276a8e7df84c23a4c64b8926f92167](batches/B-bc2f373c3b6f676478ae126d/runs/run-cd276a8e7df84c23a4c64b8926f92167/) | spec-mrac-v2@4 | gpt-5.6-sol / high | NON_CONVERGED / early_pause | PAUSED | 54.0 | 6 / 6 | 20058972 | 15.422566 | 12/12 |
| [run-e229f86f32b146abadb8e03de7ef5cf0](batches/B-bc2f373c3b6f676478ae126d/runs/run-e229f86f32b146abadb8e03de7ef5cf0/) | spec-mrac-v2@4 | gpt-5.6-sol / high | NON_CONVERGED / early_pause | PAUSED | 53.8 | 6 / 6 | 18069955 | 14.430777 | 12/12 |
| [run-e5f37c73e2c34841ab8fc70bd76276cb](batches/B-bc2f373c3b6f676478ae126d/runs/run-e5f37c73e2c34841ab8fc70bd76276cb/) | spec-mrac-v2@4 | gpt-5.6-terra / high | NON_CONVERGED / early_pause | PAUSED | 24.1 | 6 / 6 | 4715257 | 3.035252 | 12/12 |
| [run-d9e20c3c3b764f108a8a5b988de150c2](batches/B-bc2f373c3b6f676478ae126d/runs/run-d9e20c3c3b764f108a8a5b988de150c2/) | spec-mrac-v2@4 | gpt-5.6-terra / high | NON_CONVERGED / early_pause | PAUSED | 25.4 | 6 / 6 | 5879280 | 3.435034 | 12/12 |
| [run-de8d670c476949df94d7b64b34e5e97b](batches/B-bc2f373c3b6f676478ae126d/runs/run-de8d670c476949df94d7b64b34e5e97b/) | spec-mrac-v2@4 | gpt-5.6-luna / high | NON_CONVERGED / early_pause | PAUSED | 48.3 | 6 / 6 | 14468920 | 0.679062 | 12/12 |
| [run-1bebd744e6474f148562aaf28292ef59](batches/B-bc2f373c3b6f676478ae126d/runs/run-1bebd744e6474f148562aaf28292ef59/) | spec-mrac-v2@4 | gpt-5.6-luna / high | NON_CONVERGED / early_pause | PAUSED | 48.2 | 6 / 6 | 12767824 | 0.645629 | 12/12 |
| [run-b801b4c14f8e49288298f8dc5c79d3c4](batches/B-bc2f373c3b6f676478ae126d/runs/run-b801b4c14f8e49288298f8dc5c79d3c4/) | spec-mrac-v2@4 | gpt-6-astra / high | CONVERGED / converged | CONVERGED | 40.4 | 7 / 4 | 5738292 | 15.230728 | 11/11 |
| [run-90c319c4e0ed4e0babf87d084d76cff6](batches/B-bc2f373c3b6f676478ae126d/runs/run-90c319c4e0ed4e0babf87d084d76cff6/) | spec-mrac-v2@4 | gpt-6-astra / high | CONVERGED / converged | CONVERGED | 11.5 | 3 / 1 | 1986625 | 5.233546 | 4/4 |
| [run-8af7325a9a5f4ec89cc4c3e8885f8597](batches/B-bc2f373c3b6f676478ae126d/runs/run-8af7325a9a5f4ec89cc4c3e8885f8597/) | spec-mrac-v2@4 | gpt-6-sol / high | NON_CONVERGED / early_pause | PAUSED | 46.8 | 6 / 6 | 11886228 | 4.796069 | 12/12 |
| [run-86a2547a4cf54caaa74f181d5b6dc372](batches/B-bc2f373c3b6f676478ae126d/runs/run-86a2547a4cf54caaa74f181d5b6dc372/) | spec-mrac-v2@4 | gpt-6-sol / high | CONVERGED / converged | CONVERGED | 19.4 | 4 / 2 | 4988576 | 2.052278 | 6/6 |
| [run-d438b822943749c192cc1a6533b12e39](batches/B-bc2f373c3b6f676478ae126d/runs/run-d438b822943749c192cc1a6533b12e39/) | spec-mrac-v2@4 | gpt-6-luna / high | CONVERGED / converged | CONVERGED | 7.1 | 3 / 1 | 1405827 | 0.041519 | 4/4 |
| [run-fb7fe86b54d543d49a81a7fcc7b7c27b](batches/B-bc2f373c3b6f676478ae126d/runs/run-fb7fe86b54d543d49a81a7fcc7b7c27b/) | spec-mrac-v2@4 | gpt-6-luna / high | NON_CONVERGED / audit_budget_exhausted | NON_CONVERGED | 35.2 | 8 / 6 | 3251215 | 0.117453 | 14/14 |
| [run-df0fa50726fa43b2aed739b5b6329988](batches/B-bc2f373c3b6f676478ae126d/runs/run-df0fa50726fa43b2aed739b5b6329988/) | spec-mrac-v2@4 | gpt-6.1-sol / high | CONVERGED / converged | CONVERGED | 32.1 | 4 / 2 | 4321737 | 1.777965 | 6/6 |
| [run-2c428b443fa34434aecc1b557f33b862](batches/B-bc2f373c3b6f676478ae126d/runs/run-2c428b443fa34434aecc1b557f33b862/) | spec-mrac-v2@4 | gpt-6.1-sol / high | CONVERGED / converged | CONVERGED | 16.0 | 3 / 1 | 2640993 | 1.139896 | 4/4 |
| [run-12a73f3f713142a0b959cc43e554f3f9](batches/B-0b993abc2839163ab27543ab/runs/run-12a73f3f713142a0b959cc43e554f3f9/) | spec-mrac-v2@4 | gpt-5.6-sol / high | ERROR / execution_error | TIMEOUT | 87.4 | 6 / 6 | 14613310 | 13.096107 | 11/12 |
| [run-86618b490e4d48f692319ec943973c6e](batches/B-0b993abc2839163ab27543ab/runs/run-86618b490e4d48f692319ec943973c6e/) | spec-mrac-v2@4 | gpt-5.6-sol / high | CONVERGED / converged | CONVERGED | 20.3 | 4 / 2 | 4325845 | 4.581978 | 6/6 |
| [run-0aae1cd8b3584238943458b5e689eb77](batches/B-0b993abc2839163ab27543ab/runs/run-0aae1cd8b3584238943458b5e689eb77/) | spec-mrac-v2@4 | gpt-5.6-sol / high | NON_CONVERGED / early_pause | PAUSED | 51.6 | 6 / 6 | 17675387 | 13.861106 | 12/12 |
| [run-560bafca3f014884a9546b21a9f21ca7](batches/B-0b993abc2839163ab27543ab/runs/run-560bafca3f014884a9546b21a9f21ca7/) | spec-mrac-v2@4 | gpt-5.6-sol / high | ERROR / execution_error | TIMEOUT | 90.3 | 8 / 6 | 21195430 | 17.348517 | 13/14 |
| [run-948f896e121e44f8a4ad4de5847dedc6](batches/B-0b993abc2839163ab27543ab/runs/run-948f896e121e44f8a4ad4de5847dedc6/) | spec-mrac-v2@4 | gpt-5.6-terra / high | NON_CONVERGED / early_pause | PAUSED | 32.6 | 6 / 6 | 5428578 | 3.381937 | 12/12 |
| [run-140925299b3a468782bb7ea883b753bd](batches/B-0b993abc2839163ab27543ab/runs/run-140925299b3a468782bb7ea883b753bd/) | spec-mrac-v2@4 | gpt-5.6-terra / high | NON_CONVERGED / audit_budget_exhausted | NON_CONVERGED | 36.6 | 8 / 7 | 7086599 | 4.219385 | 15/15 |
| [run-8c255b06b7544b55b3ff95ce51413476](batches/B-0b993abc2839163ab27543ab/runs/run-8c255b06b7544b55b3ff95ce51413476/) | spec-mrac-v2@4 | gpt-5.6-terra / high | NON_CONVERGED / audit_budget_exhausted | NON_CONVERGED | 31.9 | 8 / 7 | 6371412 | 3.746804 | 15/15 |
| [run-dea555c4e71c43c59e43dba93fdebdb7](batches/B-0b993abc2839163ab27543ab/runs/run-dea555c4e71c43c59e43dba93fdebdb7/) | spec-mrac-v2@4 | gpt-5.6-terra / high | NON_CONVERGED / early_pause | PAUSED | 29.7 | 6 / 6 | 6477554 | 3.617021 | 12/12 |
| [run-57c4eac910244933828074f9fefc49b1](batches/B-0b993abc2839163ab27543ab/runs/run-57c4eac910244933828074f9fefc49b1/) | spec-mrac-v2@4 | gpt-5.6-luna / high | ERROR / execution_error | TIMEOUT | 82.8 | 6 / 6 | 15447254 | 0.709432 | 11/12 |
| [run-2a18bfb2d4fa4f0abff9c4c322ef1832](batches/B-0b993abc2839163ab27543ab/runs/run-2a18bfb2d4fa4f0abff9c4c322ef1832/) | spec-mrac-v2@4 | gpt-5.6-luna / high | NON_CONVERGED / early_pause | PAUSED | 48.0 | 6 / 6 | 10923698 | 0.582307 | 12/12 |
| [run-23834e80caad4eef88fed1e8cc4d0d1f](batches/B-0b993abc2839163ab27543ab/runs/run-23834e80caad4eef88fed1e8cc4d0d1f/) | spec-mrac-v2@4 | gpt-5.6-luna / high | ERROR / execution_error | TIMEOUT | 62.0 | 4 / 4 | 12211871 | 0.555799 | 8/8 |
| [run-bcf6e25ec0394134bcd8d3c242f418fc](batches/B-0b993abc2839163ab27543ab/runs/run-bcf6e25ec0394134bcd8d3c242f418fc/) | spec-mrac-v2@4 | gpt-5.6-luna / high | ERROR / execution_error | TIMEOUT | 54.3 | 4 / 4 | 7152568 | 0.403752 | 8/8 |
| [run-e52f1b4d796840f4a7aa2e53254f6c4a](batches/B-0b993abc2839163ab27543ab/runs/run-e52f1b4d796840f4a7aa2e53254f6c4a/) | spec-mrac-v2@4 | gpt-6-astra / high | CONVERGED / converged | CONVERGED | 10.4 | 3 / 1 | 2244184 | 5.657336 | 4/4 |
| [run-cd63f74019cf4705a94e66dd40689741](batches/B-0b993abc2839163ab27543ab/runs/run-cd63f74019cf4705a94e66dd40689741/) | spec-mrac-v2@4 | gpt-6-astra / high | CONVERGED / converged | CONVERGED | 11.5 | 3 / 1 | 1866471 | 5.177494 | 4/4 |
| [run-ed9a0f5863b640ad84e392f2a350fbf0](batches/B-0b993abc2839163ab27543ab/runs/run-ed9a0f5863b640ad84e392f2a350fbf0/) | spec-mrac-v2@4 | gpt-6-astra / high | CONVERGED / converged | CONVERGED | 11.7 | 3 / 1 | 1942514 | 5.128548 | 4/4 |
| [run-0f5216fce36c46b794286cade6970660](batches/B-0b993abc2839163ab27543ab/runs/run-0f5216fce36c46b794286cade6970660/) | spec-mrac-v2@4 | gpt-6-astra / high | CONVERGED / converged | CONVERGED | 13.7 | 3 / 1 | 2619388 | 6.209608 | 4/4 |
| [run-158584bc052c4a399452d241d5da0cdd](batches/B-0b993abc2839163ab27543ab/runs/run-158584bc052c4a399452d241d5da0cdd/) | spec-mrac-v2@4 | gpt-6-sol / high | ERROR / execution_error | TIMEOUT | 42.3 | 2 / 2 | 4075408 | 1.593485 | 4/4 |
| [run-857ea396fe3b44d9a3cdf0ab83b6d2fa](batches/B-0b993abc2839163ab27543ab/runs/run-857ea396fe3b44d9a3cdf0ab83b6d2fa/) | spec-mrac-v2@4 | gpt-6-sol / high | ERROR / execution_error | TIMEOUT | 32.9 | 1 / 1 | 3157465 | 1.035008 | 2/2 |
| [run-11ca2a0bbc1548e592339bacae8cfc00](batches/B-0b993abc2839163ab27543ab/runs/run-11ca2a0bbc1548e592339bacae8cfc00/) | spec-mrac-v2@4 | gpt-6-sol / high | ERROR / execution_error | TIMEOUT | 38.6 | 2 / 2 | 3243019 | 1.357580 | 4/4 |
| [run-ae1f7c5b20f240dbb3d3b1f3f1331a19](batches/B-0b993abc2839163ab27543ab/runs/run-ae1f7c5b20f240dbb3d3b1f3f1331a19/) | spec-mrac-v2@4 | gpt-6-sol / high | ERROR / execution_error | TIMEOUT | 32.7 | 1 / 1 | 1131868 | 0.467973 | 2/2 |
| [run-ad727c8a93e84f54bf38cec5c95fb472](batches/B-0b993abc2839163ab27543ab/runs/run-ad727c8a93e84f54bf38cec5c95fb472/) | spec-mrac-v2@4 | gpt-6-luna / high | ERROR / execution_error | TIMEOUT | 32.9 | 1 / 1 | 1024667 | 0.025000 | 2/2 |
| [run-efaf268efdf041df9f3a05655a84e6ca](batches/B-0b993abc2839163ab27543ab/runs/run-efaf268efdf041df9f3a05655a84e6ca/) | spec-mrac-v2@4 | gpt-6-luna / high | NON_CONVERGED / audit_budget_exhausted | NON_CONVERGED | 35.8 | 8 / 6 | 3820055 | 0.131535 | 14/14 |
| [run-93b6dcb2d26447c9a9400e7b186d79ce](batches/B-0b993abc2839163ab27543ab/runs/run-93b6dcb2d26447c9a9400e7b186d79ce/) | spec-mrac-v2@4 | gpt-6-luna / high | 排除/待完成 / awaiting_input | NEEDS_INPUT | 32.5 | 8 / 6 | 3275860 | 0.118548 | 14/14 |
| [run-ef78cf5f86ca4e75bac3f476e04e1c46](batches/B-0b993abc2839163ab27543ab/runs/run-ef78cf5f86ca4e75bac3f476e04e1c46/) | spec-mrac-v2@4 | gpt-6-luna / high | CONVERGED / converged | CONVERGED | 15.3 | 4 / 2 | 1837990 | 0.056764 | 6/6 |
| [run-05dfa6a4d5f84ad79d52495a314ae858](batches/B-0b993abc2839163ab27543ab/runs/run-05dfa6a4d5f84ad79d52495a314ae858/) | spec-mrac-v2@4 | gpt-6.1-sol / high | CONVERGED / converged | CONVERGED | 18.7 | 3 / 1 | 3128733 | 1.252176 | 4/4 |
| [run-6cba27f8ab4c42dca148ae699af9deb5](batches/B-0b993abc2839163ab27543ab/runs/run-6cba27f8ab4c42dca148ae699af9deb5/) | spec-mrac-v2@4 | gpt-6.1-sol / high | CONVERGED / converged | CONVERGED | 15.9 | 3 / 1 | 2657737 | 1.119552 | 4/4 |
| [run-bcd3783c7f0c49d1ba26f102e4257bea](batches/B-0b993abc2839163ab27543ab/runs/run-bcd3783c7f0c49d1ba26f102e4257bea/) | spec-mrac-v2@4 | gpt-6.1-sol / high | CONVERGED / converged | CONVERGED | 18.7 | 4 / 1 | 3210572 | 1.456730 | 5/5 |
| [run-8d5ec1b79ec8470983d6a6aa0b44e197](batches/B-0b993abc2839163ab27543ab/runs/run-8d5ec1b79ec8470983d6a6aa0b44e197/) | spec-mrac-v2@4 | gpt-6.1-sol / high | CONVERGED / converged | CONVERGED | 15.1 | 3 / 1 | 2205181 | 0.996442 | 4/4 |

完整输入/缓存/输出/reasoning token、单价、缺失项、错误阶段和运行条件见 [statistics.json](statistics.json)。
