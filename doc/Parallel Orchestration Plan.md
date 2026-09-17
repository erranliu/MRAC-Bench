# MRAC Bench — 多任务并行编排 Plan

## 文档信息

- 状态：核心实现与自动化验证已交付；真实模型写入验收受本机执行环境限制，保留未完成项。
- 实施记录：[Parallel Orchestration Implementation Report](./Parallel%20Orchestration%20Implementation%20Report.md)。
- 日期：2026-09-17。
- 对应规范：[Parallel Orchestration Spec](./Parallel%20Orchestration%20Spec.md)。
- 目标：交付可独立运行的批次编排、case 注册、集中 repo 管理和可恢复的证据链。
- 实施原则：先固定契约和身份，再实现共享资源及进程监督，最后接入真实 runner 与批次调度。第一版不引入多机服务、Redis 或阶段级任务拆分。

## 1. 当前基线与需要改变的边界

| 当前实现 | 变化目标 |
|---|---|
| `runner.py::run_case` 管理单次执行，按 workflow 分发 | 保持单次责任；增加固定输入包、预分配 run 身份和显式 repo 描述入口。 |
| `cases.py` 从项目 `cases/<id>` 加载，要求 id 与目录名一致 | 保留旧 loader，新增注册包 loader；source_id 不改写，注册编号与物理目录分离。 |
| `repository.py::prepare_repository` 对 URL/SHA checkout 持有整次排他文件锁 | 新受管路径采用 READY generation、共享读租约和排他维护锁；不简单删除锁。 |
| `exec_repository.py` 每个 run 有独立 checkout | 接入集中 repos/writable 路径，保持候选与 Git 控制校验。 |
| `RunStore` 内部生成 run ID，初始化即写目录 | 可选外部身份与幂等初始化日志，原单次生成规则兼容。 |
| v2/exec 使用 session OS 锁，旧路径还存在排他创建式锁 | 公共入口统一运行所有权；历史锁不自动删除，不仅依据 PID 判死。 |
| `resume_exec` 在预算耗尽后增加六次 | 抽出 recover/continue，前者绝不扩预算；更新状态 schema 与恢复测试。 |
| `codex_exec.py` 管理模型子进程与超时 | 保留单 invocation 行为，新增外部 Supervisor 对整棵 runner 进程树的控制。 |
| CLI 输出面向人的文本，部分 inspect 依赖协议文件 | 增加公共 machine 契约，由 runner 自己投影私有状态；编排核心不探测私有文件。 |
| setuptools 当前只打包 `mracbench` | 加入独立包及 CLI 入口，验证安装后的边界与运行能力。 |

实施前以当前 `doc/Protocols.md` 和代码为协议基准；不得从旧 Milestone 1 Spec 恢复已经废弃的语义。

## 2. 建议代码组织与依赖约束

```text
mrac_contracts/
  execution.py               # JSON schema/版本、请求和结果类型
mrac_resources/
  home.py                    # 根目录解析、路径边界
  cases.py                   # 注册、别名、版本发布与包清单
  repositories.py            # 资源身份、generation、租约与保留
  locks.py                   # 平台 OS 锁
  cleanup.py                 # 清理计划与重新核验
mrac_orchestrator/
  cli.py
  store.py                   # 状态事务、操作幂等、事件 outbox
  plan.py                    # 固定计划、矩阵、静态依赖
  scheduler.py               # 额度与派发，不含协议知识
  supervisor.py              # 启动回执、进程树、结束回执
  platform_windows.py        # Job Object、进程身份
  reconcile.py               # 重启对账
  evidence.py                # 导出、清单、报告
  backends/base.py            # 通用 backend 接口
  backends/mrac.py            # MRAC 命令与结果适配
mracbench/
  machine.py                 # runner 公共契约入口
  ...                        # 现有流程继续保留
```

`mrac_contracts` 无 runner/调度依赖；资源模块不 import 编排器；runner 可使用 contracts/resources；编排核心只依赖公共契约及 backend 抽象。MRAC 接入器通过子进程调用 machine，不直接调用流程函数。以 import 边界检查和独立 stub backend 测试作为 AC-01 证据。

模块名称可做等价调整，但不能以“复用代码”为由让 scheduler 开始识别 audit/fix 或修改协议 checkpoint。

## 3. 实施阶段与门槛

### P0：契约、目录和测试骨架

- [x] 为请求、回执、能力、公开状态、resolved-plan 和事件定义版本 1 schema；确定必需字段、nullable 字段、未知版本/字段的拒绝规则。
- [x] 实现统一 bench-home 解析、规范化 ID、相对路径与 Windows junction/symlink 边界检查。
- [x] 建立独立包、开发入口及安装配置；保留原 `python -m mracbench` 行为。
- [x] 建立确定性 stub backend/子进程夹具，能等待屏障、生成孙进程、模拟暂停、损坏结果与崩溃。
- [x] 固定 Spec 第 7 节状态转换表为测试数据；显式区分执行状态、runner outcome 与 evidence_validity。

门槛：schema 正反例通过；编排包可在不加载 runner 的测试中运行。对应 AC-01。

### P1：Case 注册及输入快照

- [x] 实现 registry schema、单写锁、请求幂等、编号分配、名称/别名唯一约束和归档。
- [x] 实现 PENDING 编号保留 → staging → 版本目录 → READY 发布；恢复未完成注册，拒绝覆盖已有版本。
- [x] 注册层校验并保留 source_id 与原始包字节；冻结 bundle 按 source_id 物化兼容目录，复用原 runner loader。
- [x] 提供 register/list/show/rename/archive/unarchive 与编号、数字、名称、版本选择。
- [x] 实现冻结输入 bundle 和清单：case、协议/prompts、显式执行 Spec、有效设置及执行代码身份。
- [x] 实现 `run --managed` 与旧目录式选择的明确区分，禁止未知注册选择器回退。

门槛：AC-02、AC-03、AC-04 的输入部分通过。注册过程中杀进程后不存在指向半包的可见版本；排队任务不受外部文件变化影响。

### P2：公共 runner 入口和幂等执行身份

- [x] 扩展 RunConfig/RunStore，支持预分配 run_id/run_dir 和冻结输入；新增幂等初始化日志，保留旧 API 默认参数行为。
- [x] 实现 machine capabilities/validate/start/inspect/resume/abort，公开状态由 runner 适配各协议生成。
- [x] 固定 allowed_actions 与状态 revision；区分 run 不可变配置摘要和 attempt 请求摘要，校验 operation_id 和 expected_revision，重复成功操作先走幂等返回。
- [x] 所有协议能公开检查结果；旧协议不支持的恢复能力明确返回，不能通过猜文件名恢复。
- [x] 拆分 exec 的 recover 与 continue：预算已耗尽的 recover 返回 PAUSED；用户 continue 的六轮增加与操作记录共同提交，重复请求不重复增加。
- [x] 支持中断后的阶段核对与现有证据检查；写阶段无法验证的现场返回明确拒绝。
- [x] 公共运行状态使用独立 schema 1，保留协议 checkpoint 字段与旧记录的只读/允许操作矩阵，不就地迁移原始证据。

门槛：AC-12、AC-13、AC-14 的 runner 契约部分通过；重复 start 不启动第二次模型调用，旧暂停结果不冒充新 attempt 结果。原 runner 的 audit 计数、clean 与六轮语义回归通过。

### P3：集中 repo 与并发租约

- [x] 实现 repo-key/checkout-key、PREPARING/READY/QUARANTINED generation 及准备完成的原子发布。
- [x] 实现跨进程共享读锁与排他维护锁，记录租约身份和历史区间；单独验证 Windows 行为。
- [x] 新受管 runner 通过资源描述访问 repo；前后检查依旧由 runner 执行，共享区不保存日志和生成物。
- [x] 可写 run 分配独立目录，恢复绑定原 repo，重新实验分配新 run/repo。
- [x] 实现污染事件、受影响区间和保守归因；对已完成结果追加 INVALID 判定，不改写其 result.json。
- [x] 资源目录持久化污染、租约与保留状态；删除操作通过持久化 outbox 幂等导入批次事件。
- [x] 暂停/等待与未知执行者具有不同的租约/恢复保留语义；共享基线可重建，可写现场不擅自重建。

门槛：AC-06、AC-07、AC-08 的资源部分通过；两个真实子进程同时读同一 generation，维护操作被可靠阻止；可写修改互不影响。

### P4：Supervisor 与崩溃窗口

- [x] 实现持久化启动意图、attempt 排他 OS 锁、自身身份、runner 回执与结束回执。
- [x] Windows 10/11 使用 Job Object；优先通过 PROC_THREAD_ATTRIBUTE_JOB_LIST 在创建时关联，覆盖 Supervisor 在创建/入 Job 间死亡的窗口。作业句柄仅由明确的监督所有者持有，不泄漏给后代。
- [x] 验证当前 Codex/Node 子进程启动方式与 Job 继承兼容，不能以 `taskkill /PID` 作为可靠监督的唯一机制。
- [x] 心跳输出与进程身份核验包含创建时间/启动周期，权限拒绝返回 UNKNOWN，不当成死亡。
- [x] 实现调度器退出后 Supervisor 独立存活、输出持续落盘、重启后接管监控；Supervisor 崩溃终止整棵树。
- [x] 实现取消的 10 秒宽限、强制结束、子进程清空证明与回执；未证实停止保持 RECOVERING/占用。

门槛：AC-10、AC-11、AC-15 的进程层通过。使用明确同步屏障注入崩溃，不能靠随机 sleep 或仅 mock Popen 宣称覆盖真实进程树。

### P5：持久化调度、批次与操作

- [x] 建立本地 SQLite schema、WAL/FULL 配置、迁移版本、事务 API、service OS 单例锁与 outbox。
- [x] 实现批次两阶段发布和 request_id 去重；文件与 DB 的未完成提交可对账，不提前派发。
- [x] 矩阵按配置顺序稳定展开；校验协议参数、模型配置、重复 case、环与未知依赖。
- [x] 实现全局/组/批次原子额度、批次轮转公平性、等待依赖与 SKIPPED 判定。
- [x] 实现 attempt 所有权、调度器换届接管、启动/结束对账、有限自动恢复及持久化退避计数。
- [x] 实现显式 recover/continue/answer/rerun/cancel 操作；回答先复制校验再提交，批量操作每项幂等。可恢复 FAILED 的显式 recover 保留同一 run 及历史失败，不复活已 SKIPPED 的下游。
- [x] 实现污染影响后代标记与停止；rerun 记录新旧成绩，不自动复活旧下游。
- [x] 实现 serve/status/stop 和 offline 提交；控制服务停止不等同取消全部任务。

门槛：AC-05、AC-09、AC-10、AC-12 至 AC-15、AC-19、AC-20 端到端 stub 测试通过。重启前后额度、任务数量、重试次数和操作结果一致。

### P6：批次证据、报告与安全清理

- [x] 事件序号与状态同事务保存；按事件高水位原子重建 JSONL，覆盖截断修复和幂等重放。
- [x] 输出 attempts、summary、report 和 manifest；输入不可变，报告投影原子替换并携带 revision。
- [x] 保存所有 run/attempt 以及旧报告修订；汇总同时展示初始实验、重新实验、无效证据和未收敛。
- [x] 实现 batch export；活跃证据标注不完整，不将仍写入的 raw 当成完整封存。
- [x] 清理提供候选计划与 apply，执行时重新验证 generation、锁、保留引用和实际路径。
- [x] 实现显式 workspace release、共享 repo 重建验证、污染取证保留和清理跨组件事件对账。
- [x] 验证复制完整批次目录后报告链接有效，repo 删除后 case/证据仍可离线查看。

门槛：AC-16、AC-17、AC-20 的证据部分通过；导出过程被杀后可恢复，无事件丢失/重复，无在用目录误删。

### P7：兼容、集成验收与使用文档

- [x] 原四协议单次测试通过；新增 managed 单次在无服务情况下共享 registry/repo 并使用同等 Supervisor。旧 resume/abort 拒绝直接修改 batch 所有的 run，避免绕过编排状态。
- [x] 验证安装后的命令入口、路径含空格/中文、不同 cwd、环境变量和显式 bench-home。
- [x] 更新 README，标注新入口和旧模式边界；新增批次 YAML 示例、case 管理和恢复/清理操作说明。
- [x] 更新 casemaker 的交付后注册步骤，仍保持原始 Spec 字节与 source_id；不自动注册用户未选择的包。
- [x] 在 Protocols.md 记录新的基础设施恢复入口与兼容能力；协议业务语义未变时不冒增协议版本。
- [x] 写实施报告，逐条映射 AC、验证命令和证据路径；未通过项明确列出，不宣称完成。
- [x] 在明确模型配置下执行两个只读、两个独占 exec 的真实烟测，完整保存输入、隔离、状态和证据链；本次得到收敛、超时与权限相关 NEEDS_INPUT，详见实施记录。

- [ ] 在实际允许 workspace-write 的 Codex 执行环境中复验真实 exec 写入成功；本机两次烟测均停在 NEEDS_INPUT，不标记为通过。

门槛：自动化验收与真实烟测分开报告。本次核心实现已交付，真实写入的最终环境验收仍保留上述未完成项。

## 4. 实施顺序与提交拆分

依赖顺序：`P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7`。可在本地先做独立平台技术验证，但每阶段完成门槛通过后才接入下一层。

建议按阶段拆成可独立审查的提交；每个提交只承诺其实际实现能力。新增 CLI 在功能未完成时明确报 unsupported，不能返回空的成功结果。不要把测试夹具的假 backend 注册成用户可选择的真实执行器。

关键改动独立审查：case 原文与 source_id 保留、共享 repo 锁协议、幂等 start、exec 预算操作、Windows Job 继承、跨库清理事件。审查可由实施者完成；本计划不要求自动启动子代理或迭代审计循环。

## 5. 验证策略与故障注入矩阵

| 注入点 | 验证核心 | 对应 AC |
|---|---|---|
| case 包发布前后、registry commit 前后 | 无可见半包、重试无重复编号归属 | 02、03 |
| batch 输入发布后、scheduler commit 前后 | 不派发半批次、重复提交同一 batch | 04、05 |
| 准备共享 repo 时另一进程请求/清理 | 不读半成品，读锁与维护锁互斥 | 06、17 |
| 多读者运行时修改共享文件 | 全部可能受影响者标记无效，禁止自动 reset | 08 |
| 多个批次同时领取资源 | 总/组/批次额度不超限，无部分领取死锁 | 09 |
| spawn 前后、runner 初始化、结束回执/DB commit 间 | 不重复启动、不丢完成、迟到结果不覆盖 | 10 |
| Supervisor 突然退出，runner 带孙进程 | 整树停止；状态未知时保护资源 | 11、15 |
| exec 审计计数刚达到六次时崩溃 | recover 不扩预算，continue 幂等扩六次 | 12 |
| 写阶段中断、候选与 checkpoint 不一致 | 明确拒绝恢复，不 reset 或承认未验证产物 | 13 |
| 回答/继续/取消与状态修订竞争 | 幂等或显式冲突，不重复执行 | 14、15 |
| outbox 导出中断、截断 JSONL、损坏报告投影 | 重新导出无遗漏，旧记录保留 | 16 |
| clean 计划生成后新租约进入、路径替换为 junction | 重新核验阻止删除，不越出 repos | 17 |
| 停服务、offline 提交、重新启动及 rerun | 排队/接管符合契约，旧成绩和依赖不改写 | 19、20 |

测试层次：纯状态/契约测试 → 临时 SQLite 与本地 Git 集成 → 真实 OS 子进程故障测试 → 四协议 stub adapter 集成 → 可选外部条件就绪后的真实模型烟测。异步测试采用屏障、有限超时和可靠 teardown，失败后不得残留后台进程。

各阶段只运行相关测试与回归；最终执行一次完整 pytest、ruff check、ruff format --check。新增包必须加入 lint/format 路径；全套通过后没有新改动或失败证据时不重复跑同一组测试。

## 6. 交付清单与完成定义

- [x] 版本化公共执行契约与 schema 示例。
- [x] 独立编排服务、Worker/Supervisor、MRAC 接入器和完整 CLI。
- [x] case 注册表及不可变版本包、编号/名称解析。
- [x] 集中只读共享 repo、可写独立 repo、污染与清理管理。
- [x] 固定批次计划、持久化状态、幂等操作与故障恢复。
- [x] 批次/单次分离的证据布局、可重建报告与清单。
- [x] 自动验收证据、Windows 进程监督证据、兼容回归和真实烟测状态。
- [x] 用户文档、示例、实施报告。

核心实现与自动验证完成不等于外部模型环境已满足写入条件。保留真实写入未通过项及完整证据，满足该环境条件后再关闭最后的验收项。
