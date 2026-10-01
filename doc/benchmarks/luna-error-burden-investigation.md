# Luna high：v11 错误与暂停调查

调查开始于 2026-10-02。用户授权最多 20 个 case1 Simple 测试，使用
GPT-5.6 Luna / high 和 GPT-6 Luna / high，目标是降低执行错误和暂停。
`NON_CONVERGED` 是正常预算结果，不作为故障。已在 20 次预算内完成调查。预算包含中止的诊断调用，不包含未启动的排队取消。

## 历史记录：12 个错误与 3 个暂停

| 类型 | 数量 | 证据与原因 | 对应处理 |
|---|---:|---|---|
| 读取预检失败 | 7 | Luna 共 3 个、Terra 4 个。模型自行设计搜索参数，出现超过 12 个 glob、brace glob、空 query；两个 Terra 调用服务端搜索成功，但客户端 45 秒超时，没有后续 read。 | 保留原有 head → search → read 预检，给出无 query/glob 的单结果默认采样调用，缩小工作量。 |
| 执行策略错误 | 2 | 两个 Luna 在 repair-01 报告 checkout 只读、shell/写入被拒。CLI 实际请求 workspace-write；可见日志没有足够工具执行细节，不能断言是模型误认还是执行环境拒绝。 | 沿用已验证可读写的 unelevated 环境；提示中明确 Spec 已由 runner 复制、可在当前隔离 checkout 中就地编辑。 |
| Windows sandbox 初始化失败 | 2 | GPT-6.1 Sol；helper_sandbox_lock_failed，shell 无法启动，候选 Spec 未变化，旧结果归为 REPAIR_INVALID。 | 保留既有 unelevated 支持及环境错误分类修复。这不改变审计判断。 |
| audit_id 格式/一致性失败 | 1 | GPT-6.1 Sol 返回合法 JSON，但复制 run ID 时漏掉其中的 406；并非 JSON 语法错误。 | 控制器按调用绑定 ID，模型只输出发现或决定。内部证据继续保留 audit_id。 |
| 连续六轮有接受的 freeze 问题 | 3 | 全部为 GPT-5.6 Luna；触发既有 soft pause，尚有 pending_fix。 | 保留暂停规则，观察减少修复导航和文档重写负担是否改善；不通过放宽评审或自动增加轮次消除暂停。 |

暂停 run：`run-87970ded0b63434a83c1de75b0ac982e`、
`run-27e33c6cd7474e678028cd5b06a50b94`、
`run-35b113c8065845359989d8fb29ad5b2b`。
初始 Spec 为 6,174 字节，分别增长至 19,244、28,324、27,956 字节。
后续问题涉及保存成功与加载有效性之间的新承诺、不同操作的条件不一致、
新增/保留的验证规则未明确等。文档增长与暂停同时出现，不能据此证明增长导致暂停。

## 实验 A：降低调用负担，保留审计语义

- 新协议版本 12，用于区分历史 v11 结果。
- 保留 spec-init / freeze / review / repair / closure 顺序、8 次审计预算、
  两次连续 clean 条件和六轮暂停规则。
- 审计与 review 的判断标准逐字沿用 v11，只移除输出 envelope 中的 audit_id。
- 预检指定 `repository_search({"max_results":1})`，随后读取返回的一行。
  没有新增恢复阶段或校验。
- runner 用固定快照的路径表解析 Spec 中点名的源文件和程序集，给修复调用导航提示；
  不预先解释代码、不过滤问题、不限制模型读取必要的直接依赖。
- repair 的外围执行说明缩短并明确文件已经准备好；修复核心提示词保持 v11。
- 原有 SpecCheckout、repair_files、closure_files 已承担项目/Spec/比较文件的复制。
  模型不需要执行复制脚本、回传整份替换文本或生成 diff。
- 去除模型无需使用的 spec_sha256；完整哈希仍由 runner 保存到证据。
- CLI 0.159.2，unelevated。每个新 run 都计入 20 次预算；未自动续跑历史暂停记录。

## 实验 B：修复 MCP 传输，减少无效参数往返

协议版本 13，修复源文件与审计语义相同；修复调用外围说明补充就地局部编辑，
不要求重新生成全部未变内容。修复导航提示兼容句末点号。

实验 A 的 GPT-6 Luna `run-fefee61ed08b41a49b598fd242445647` 首次审计中，
`MergeObjectLibraryStore.cs` 的读取在服务端成功，在客户端超时。
该文件返回片段含 87 个非 ASCII 字符；其他同时返回成功的文件片段都是 ASCII。
Windows Python 的管道标准输出默认 GBK，服务端却以 `ensure_ascii=False` 输出 JSON-RPC。
将这次实际响应按 GBK 编码，再按 UTF-8 解码，可确定复现 UnicodeDecodeError。
这提供了一个确切传输故障原因，不能把所有历史超时都归为模型注意力不足。
[MCP 传输规范](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
要求 JSON-RPC 消息使用 UTF-8；ASCII escape 是合法且不改变文本内容的 JSON 编码。

- JSON-RPC 外层采用 ASCII escape，客户端按 UTF-8 解码后恢复原始文字。
  标准输入明确使用 UTF-8，避免非 ASCII 搜索词或路径被错误解码。
- repository/candidate MCP 共用传输函数，两个工具服务都得到修正。
- 本地强制 PYTHONIOENCODING=gbk，复放同一实际 MCP 文件读取：响应为有效 UTF-8，
  包含完整 83 行和原始非 ASCII 内容，exit 0。
- max_results/max_lines 是期望数量，脚本自动缩到既有 50 项/200 行输出上限，
  保留截断标记。模型不必记住上限或因 80 项/260 行重发同样请求。
  没有增加输出规模、阶段、重试次数或新的结果校验。

已启动的 A 调用共 10 个。3 个在定位时已以 MCP 环境错误结束；
其余 7 个均已记录 6–15 次传输超时，因此主动停止诊断，保留日志。
两个未启动的 A 排队任务取消，不计入测试预算。
这 7 个主动停止记录不计为正常完成或收敛，也不混入修正版完整测试的错误率。

## 实验 C：原始 Spec 正文展示与默认工作目录

协议版本 14，仅修复调用外围输入展示不同：原始 source_spec 保留全文，
由 JSON 转义字符串改为独立 Markdown 正文。修复核心提示词不变，
metadata 与 accepted_findings 仍用 JSON。正文放在独立数据块中，不删除或概括内容。

同时明确使用 shell 默认工作目录、相对路径。B 的一个 5.6 Luna 修复调用把
Windows 绝对目录中的 `\runs` 转义写坏，出现 CreateProcessAsUserW 267；
模型随后恢复，未导致终态错误。省去可选 workdir 的复制可减少这类负担。

C 只有一个 5.6 Luna / high 样本。B 仍有暂停，因此最后一个尚未启动的 6 Luna
名额改为这次实验；两个模型的完整流程验证各 5 次，总预算为 20 次。
这个样本有一次中间 clean，随后又接受问题，最终用满 8 次审计并正常结束为
NON_CONVERGED。修复次数 5，没有暂停或执行/解析错误。不能用单样本证明正文展示
单独造成了结果变化；其初始审计发现与 B 不同，运行时并发量和缓存状态也不同。

## 测试记录与预算

- A：`B-605cdb8eaa392991d4c8075a`，启动 4 个。
- A 复现：`B-d9aff8d72a3fef033c71b921`，启动 6 个，另 2 个启动前取消。
- B：`B-79d9b10cb6d660bc74975fd8`，5.6 Luna 4 次、6 Luna 5 次，另 1 个启动前取消。
- C：`B-a65b377d7c40784c150732ae`，5.6 Luna 1 次。
- 共 20 次已启动 run：10 次诊断、10 次完整流程验证。5.6 Luna 共 11 次，6 Luna 9 次。
- 三个启动前取消不计预算。并发上限 8，CLI 0.159.2，unelevated。
- A 的 3 个自然失败与 7 个主动中止分列；主动中止前均已出现传输超时。
  10 个诊断调用共记录 94 次客户端 MCP 传输超时。
- 修正版完整流程的 10 个 run 共记录 **0 次 MCP 传输超时、0 个终态执行或解析错误**。
  猜错文件路径等模型已自行恢复的工具失败仍留在随文数据，不能将“终态错误为零”
  理解为每一个工具调用都没有失败。

### 完整流程验证汇总

以下耗时和修复均值包括本行所有已停止的完整流程 run，暂停单列；
与历史 v11 表只统计正常流程均值的口径不同。诊断调用不进入此表。

| 模型 / effort / 协议版本 | 次数 | 收敛 | 未收敛 | 错误 | 暂停 | 平均耗时（范围，分钟） | 已启动修复次数均值 |
|---|---:|---:|---:|---:|---:|---|---:|
| gpt-5.6-luna / high / v13 | 4 | 0 | 0 | 0 | 4 | 59.4（52.0–72.8） | 6.25 |
| gpt-5.6-luna / high / v14 | 1 | 0 | 1 | 0 | 0 | 54.4（单样本） | 5.00 |
| gpt-6-luna / high / v13 | 5 | 5 | 0 | 0 | 0 | 17.6（5.4–28.9） | 2.80 |

### 逐次结果

| run ID | 版本 / 模型 | 结果 | 耗时（分钟） | 审计 / 修复 |
|---|---|---|---:|---|
| run-681f20566dbd4585ac899e17dd0c054d | v13 / gpt-5.6-luna | PAUSED | 56.3 | 7 / 6 |
| run-0df6fd5f074f450e9a77db7d32eed9a4 | v13 / gpt-5.6-luna | PAUSED | 72.8 | 7 / 7 |
| run-4f382371655c425eab8fbe10e321e4f8 | v13 / gpt-5.6-luna | PAUSED | 52.0 | 7 / 6 |
| run-5b42ee77e6e54299b3c7fc38d603a440 | v13 / gpt-5.6-luna | PAUSED | 56.5 | 7 / 6 |
| run-a6bb72ec0a3a45129771b5d91de49804 | v13 / gpt-6-luna | CONVERGED | 5.4 | 3 / 0 |
| run-dfb9d9fddeb643a59c02cb9e3801c0d2 | v13 / gpt-6-luna | CONVERGED | 22.6 | 6 / 4 |
| run-612da54de942416da4487f182c9f2d7d | v13 / gpt-6-luna | CONVERGED | 14.3 | 4 / 2 |
| run-a9c5998413af4c74b1618701ce928b2c | v13 / gpt-6-luna | CONVERGED | 28.9 | 8 / 6 |
| run-91da57e89e984c2695ae167786fb20fe | v13 / gpt-6-luna | CONVERGED | 16.8 | 4 / 2 |
| run-fb21331445ab41f6a38fcbcc26be1478 | v14 / gpt-5.6-luna | NON_CONVERGED | 54.4 | 8 / 5 |

## 尚未解决的暂停

B 的四个 5.6 Luna 样本均在第 7 次审计后触发原有六轮暂停条件。
最后接受的问题分别涉及：必需 catalog 的 load 校验；save 操作顺序与 first-failure；
异常、同一 shard 重复 ID 的权威性和测试覆盖；空 map-box / activity-reward 家族的谓词。
这些是有完整 JSON 与已接受发现的内容问题，不属于 parse error 或文件执行失败。
最终 Spec 为 24,154–38,764 字节；C 为 22,522 字节。减负没有稳定消除内容扩展和
互相矛盾的风险。**暂停问题仍未稳定解决**，不能通过改审计标准、移除暂停条件或
无指导自动续跑，把它计为已解决。

## 交付与验证

- 保留 A/B/C 的冻结工作树和运行证据；交付改动位于独立最终工作树，便于继续复用
  原实验 checkout 和按原版本恢复暂停记录。
- 最终脚本另修正一个已有截断标记边界：单文件达到结果数上限时也标记 truncated。
  它使用已有字段，没有新增流程或模型校验。该边界修正仅做离线回归验证，未追加模型调用。
- 两项离线回归通过：强制 GBK 管道时中文路径、中文/emoji 内容的 UTF-8 往返；
  260 行 / 80 项请求自动缩到既有输出上限并报告截断。它们不消耗模型测试预算。
- MCP、控制器 ID 绑定及既有解析契约的 30 项离线回归通过；改动文件静态检查通过。
- 六份审计、review、repair、closure 核心说明对照 v11，除移除返回 audit_id 的格式部分外
  保持一致；8 次审计预算、clean 条件、暂停条件与流程顺序保持一致。
- [随文数据](luna-error-burden-results.json)包含 20 个 run 的结果、耗时、调用次数、
  Spec 大小、工具失败计数及冻结代码 SHA-256；不包含认证信息或机器绝对路径。

## 评估口径

分别统计错误、暂停、正常结束；正常结束包括 CONVERGED 与 NON_CONVERGED。
记录每次耗时、审计次数、修复次数与具体失败阶段。
不能将减少发现数量、提高收敛率或改变审计标准作为本次修复成功的证据。
若暂停仍出现，应报告保留规则下的真实结果，不能伪装为正常结束。
