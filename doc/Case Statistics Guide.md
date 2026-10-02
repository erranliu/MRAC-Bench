# 按 case 版本统计

编排运行记录由脚本整理为 `statistics.json`，再从该 JSON 生成 `report.md`。
同一注册 case ID、同一 case 版本跨批次汇总；不同版本写入不同目录。
case 名称或别名不参与归集身份。报告可覆盖更新，不要求不可变报告快照。

## 使用

```powershell
# 指定 case 的一个版本
uv run mracbench stats report --case case1 --case-version 1 --bench-home C:\mrac-data
# 指定 case 的所有有运行记录的版本
uv run mracbench stats report --case case1 --bench-home C:\mrac-data
# 所有 case 和版本
uv run mracbench stats report --bench-home C:\mrac-data
# 从已有 JSON 生成 Markdown，不读取运行数据库
uv run mracbench stats render --input C:\mrac-data\reports\C000001\v1\statistics.json
```

输出为 `<bench-home>/reports/<case-key>/v<case-version>/statistics.json` 和 `report.md`。
调度器在 run 停止时更新该 case 版本，同一个 tick 内多个停止事件合并更新。
batch 提交、报告导出、显式操作和受管单次运行/续跑也刷新关联报告。
统计更新失败只报告警告，不改变 runner 结果或触发新模型调用。
修改价格后手动重新生成报告即可；原始运行记录保持不变。

## 结果与计数

- CONVERGED 计收敛；NON_CONVERGED 和 PAUSED 统一计未收敛。
  early_pause 和 audit_budget_exhausted 只作为停止原因。
- 执行/解析失败计错误。进行中、待输入、取消、跳过、ABORTED 及无效证据另列，
  不进入已完成结果计数。原始生命周期和状态代码保留。
- 用 run ID 去重并选最新 revision；恢复或续跑仍是一个样本。
  rerun 的新 run ID 是新样本，旧 run 保留在历史中。
- 协议 ID/版本、provider、模型、effort、代码/提示词身份、CLI、sandbox 和预算等
  运行条件分别分组，避免隐式混算不同条件的平均值。
- 正常耗时和修复均值包含收敛与未收敛；错误耗时单列。
  缺失值不按零参与均值，JSON 给出已知样本数与缺失数量。
- JSON 保存调用与分组汇总、停止原因、错误类型/阶段及相对证据路径。
  只读取运行 JSON 和必要的 usage 日志，不遍历项目 checkout 或重新执行审计。

## Token 与费用

每个 run 和分组必含 tokens、cost，每次执行保留 raw_usage、所用单价和费用分项。
字段存在不代表源数据可取得：缺失值用 null、缺失计数与完整度标识；报告展示已知小计。
token 包括输入、普通输入、缓存读取、缓存写入、输出、reasoning 和总量。
Codex/Responses 输入是总输入，普通输入扣除缓存读取和写入；reasoning 属于输出，
不再次加到总 token 或费用。按已启动调用计消耗，覆盖预检、审计、评审、修复、闭环、
重试和恢复。execution.json 与 stdout 的重复 usage 不重复累加。
旧记录缺少 usage 时标缺失，不从字符数或耗时猜 token。

价格是 **API 等价预估**，不是 Codex 订阅账单。默认 USD、Standard processing、
standard reasoning mode、short context，来自开发时维护的
[api-pricing.yaml](../mrac_orchestrator/api-pricing.yaml)。生成报告不查询价格 API。
价表随 Python 安装包发布；`--pricing-file` 可指定相同结构的本地价表。

### 官方标准单价：每百万 token，USD，查询于 2026-10-02

| 模型 | 普通输入 | 缓存读取 | 缓存写入 | 输出 |
|---|---:|---:|---:|---:|
| GPT-5.6 Sol | 4 | 0.40 | 5 | 20 |
| GPT-5.6 Terra | 2 | 0.20 | 2.50 | 12 |
| GPT-5.6 Luna | 0.20 | 0.02 | 0.25 | 1.20 |
| GPT-5.6 Cyber | 12.50 | 1.25 | 15.625 | 75 |
| GPT-6 Astra | 10 | 1 | 12.50 | 50 |
| GPT-6 Sol | 2 | 0.20 | 2.50 | 10 |
| GPT-6 Luna | 0.10 | 0.01 | 0.125 | 0.50 |
| GPT-6.1 Sol | 2 | 0.10 | 2.50 | 10 |

来源为[官方价格页](https://developers.openai.com/api/docs/pricing)与各模型页，
逐项链接见价表；范围为[官方完整目录](https://developers.openai.com/api/docs/models/all)
公开的 GPT-5.6 及以后文本模型。gpt-5.6 按官方别名映射到 Sol。
不猜未公布模型、别名或第三方 provider 的价格；缺价标记 price_missing。
第三方 provider 可在本地价表 providers 下配置实际 API 单价。

```text
费用 = (普通输入 × 输入单价 + 缓存读取 × 缓存单价
       + 缓存写入 × 写入单价 + 总输出 × 输出单价) / 1,000,000
```

Decimal 计算费用，先汇总再显示舍入。小计包含错误、取消、进行中记录的已知消耗，
排除记录费用另列。平均费用只使用费用完整的已完成 run，样本数另列。
平均 token 只使用总 token 完整的已完成 run，JSON 同时给出已知样本数和缺失数。
缺价或缺计费 token 时，对应总费用为不完整。

价表记录长上下文价格和已公布处理档位倍率，可显式选择：

```powershell
uv run mracbench stats report --case case1 --pricing-tier fast --pricing-context long --bench-home C:\mrac-data
```

272K 门槛按单次 API 请求判断。CLI input_tokens 累计多次工具交互请求，
因此不会把累计输入大于 272K 自动当成长上下文；档位作为显式估算假设写入 JSON。
默认估算不加入地区附加费或托管工具费用；本机 MCP/文件工具无单独托管工具收费。
GPT-5.6 Sol 使用公布的促销价格，官方承诺至少持续至 2026-11-21；后续开发时更新配置。
