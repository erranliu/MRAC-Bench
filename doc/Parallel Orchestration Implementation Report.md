# 多任务并行编排实施与验收记录

日期：2026-09-17。对应 [Spec](./Parallel%20Orchestration%20Spec.md)、[Plan](./Parallel%20Orchestration%20Plan.md) 与[使用说明](./Parallel%20Orchestration%20Guide.md)。

## 交付范围

已实现独立编排包、公共执行契约、受管资源模块和 Windows Supervisor。调度核心通过 MRAC backend 调用 runner 子进程，不直接调用协议状态机。旧单次入口保持兼容；新增注册 case、受管单次、批次提交/查询/恢复/续跑/回答/重新实验/取消，以及 repo 管理命令。

主要产物：

- `mrac_contracts`：schema version、请求摘要、身份校验、原子写入和数据库配置。
- `mrac_resources`：case 注册/版本/别名、路径校验、共享锁、repo generation、监督身份、租约区间、污染归因、封存检查与清理事件。
- `mrac_orchestrator`：固定矩阵、静态依赖、持久化状态、全局/分组/批次额度、幂等操作、重启对账、Supervisor 和报告导出。
- `mracbench/machine.py`：runner 自己解释检查点，公开状态、能力、固定输入校验、执行/恢复和封存。原协议业务阶段继续留在原模块。
- `examples/batch.yaml` 与 `scripts/orchestration_smoke.py`：可复用配置和显式真实模型烟测。

实际代码组织将平台进程原语放在 `mrac_resources/windows.py`，以供资源保护和 Supervisor 共用；对账实现位于 scheduler，清理位于 repositories。case 包复制到冻结 bundle 的原 source_id 目录后复用原 loader，不改写原 case.yaml。公共状态采用新的 schema 1 文件；没有修改旧协议 checkpoint 的字段语义或静默迁移历史记录。

## 自动验证

实现前基线为 211 项测试通过。完整回归曾执行 `uv run pytest -q`，243 项全部通过（624.39 秒）。之后针对新增的注册边界、租约区间和清理事件，以及最后的契约检查，继续运行编排专项回归，最终结果见本记录末尾。

自动测试使用本地临时 Git、stub AgentAdapter/CLI 和真实 Windows 子进程，不消耗模型配额。Windows 验证包括共享 LockFileEx、创建时 Job 关联、Supervisor 崩溃后后代终止及文件替换/只读 Git 对象清理。

| Spec 验收项 | 验证证据 |
|---|---|
| AC-01 | `test_core_has_no_runner_imports_and_yaml_rejects_duplicate_keys` 与 FakeBackend 调度测试 |
| AC-02、03 | 编号/名称/别名/版本/归档、并发注册、staging 中断和已发布目录尚未 READY 的恢复测试 |
| AC-04、05 | 固定 bundle、注册包篡改拒绝、批次提交事务回滚后不重新展开、重复 submit、非法配置拒绝 |
| AC-06、07 | 跨进程共享读锁、真实子进程双只读共用 generation、双 exec 独立 checkout |
| AC-08 | 共享污染隔离、活动租约归因、此前已验证完成的 run 保持有效；调度器实现依赖后代失效传播 |
| AC-09 | 暂停释放额度、多个批次共享账户额度上限、未知进程保留占用 |
| AC-10 | attempt 已登记但请求未写入的崩溃恢复、完成事务入库前失败后的结果重收取 |
| AC-11 | Job 创建/终止及真实 Supervisor 崩溃测试；进程身份绑定创建时间，未知权限状态不作死亡判定 |
| AC-12 | 六轮耗尽时 recover 保持 6，显式 continue 扩到 12；操作和 start 幂等测试 |
| AC-13、14 | 旧协议恢复能力拒绝、候选校验原有回归、TIMEOUT/PARSE_ERROR/AGENT_ERROR/违规不自动重试、暂停/回答操作校验 |
| AC-15 | 取消事务先于完成结果时取消生效；Supervisor 进程树收尾与提前取消控制 |
| AC-16 | 截断事件导出重建、旧成绩保留、相对报告链接、输入/运行清单和封存篡改拒绝 |
| AC-17 | 活跃读者阻止删除、失败/暂停保护、待恢复引用保护、必须封存后清理、清理事件只导入批次一次 |
| AC-18 | 原单次回归、固定 run ID、受管单次及安装入口；旧 batch-owned resume/abort 拒绝绕过调度 |
| AC-19、20 | 依赖等待/跳过/循环、独立任务继续、rerun 历史保留、离线提交及调度连接重建 |

验收项包含实现检查和自动测试，表中未声称对每种操作系统故障做穷举。数据库损坏、任意证据损坏或无法确认进程身份时，系统采取明确失败或 RECOVERING，不承诺自动修复。

`uv build` 成功生成 sdist 和 wheel，包含四个 Python 包及 `mracbench` 控制台入口。安装入口的 help、batch help 和 case list 已实际执行。

## 真实模型烟测

使用 Codex CLI 0.145.0、`gpt-5.6-luna`、reasoning effort high、合成的单文件本地 Git 基线。任务仅要求将 `answer()` 的返回值从 1 改为 2，不涉及用户业务代码或外部资料。

第一批上限并发 2，包含两个 spec-mrac-v2 和两个 exec-mrac-v1，单调用 timeout 120 秒，禁止自动恢复：

| Task | 协议 | 结果 |
|---|---|---|
| T000001 | spec-mrac-v2 | TIMEOUT，保留失败调用及原始输出 |
| T000002 | spec-mrac-v2 | CONVERGED |
| T000003 | exec-mrac-v1 | NEEDS_INPUT：模型执行环境报告 checkout 只读 |
| T000004 | exec-mrac-v1 | NEEDS_INPUT：模型执行环境报告 checkout 只读 |

证据位于 `.validation/orchestration-smoke/batches/B-4b7cf9ab407c030f8cadb6ec/`，该目录被 Git 忽略。两个只读 run 使用同一共享 generation；两个 exec 使用不同独占 checkout，均未互相覆盖。批次如实进入 WAITING，没有自动增加六轮、回答问题或重跑超时实验。

为排除仅因 smoke 位于 Codex 工作树路径而导致的问题，还在独立普通目录执行了一个 exec run，timeout 180 秒；结果仍为 NEEDS_INPUT。证据在 `C:/Users/lmas1/.mracbench-validation/20260917/batches/B-03aeb56e8376343ed657a885/`。

两次 exec 的 invocation.json 均记录 `readonly: false` 和 `--sandbox workspace-write`，checkout 分配符合设计；模型仍报告实际只读策略/命令执行限制。因此真实模型写入验收尚未通过，不能将其算作成功实施或收敛，也不能据此宣称已定位具体外部策略来源。未改动用户/组织权限配置，未改用更宽松沙盒。

官方说明将 workspace-write 作为允许修改的请求模式，实际沙盒还受平台与管理策略约束。本次使用 OpenAI Docs 核对了[非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[沙盒](https://learn.chatgpt.com/docs/sandboxing)和[权限配置](https://learn.chatgpt.com/docs/permissions)。

## 适用边界

- 当前受管监督只支持 Windows 10/11；不声称完成 Linux/macOS、多机或多个活动调度器的验收。
- 服务重启保留任务状态和额度占用，需使用相同的服务额度参数。stop 停止派发，已有 Supervisor 独立收尾。
- Code/CLI 版本身份改变后旧批次不能静默继续；历史证据仍可查看。
- 全量批次复制用于离线查看，不支持修改绝对运行路径后继续执行。
- 污染目录保留取证；显式 rebuild 创建下一代共享基线，不自动 reset。
- 本机真实 Codex 写入能力仍需在实际允许 workspace-write 的执行环境中复验；可复用 smoke 脚本，无需重建编排方案。

## 最终检查记录

最终记录：

- 完整回归：`uv run pytest -q`，243 passed，624.39 秒。
- 最终编排专项：`uv run pytest -q tests/test_orchestration.py tests/test_orchestration_faults.py tests/test_orchestration_boundaries.py tests/test_orchestration_integration.py`，46 passed，59.40 秒。包含完整回归之后追加的边界用例，计数不能与 243 简单相加。
- 四个 Python 包、全部 tests/scripts 的 `ruff check` 通过；`ruff format --check` 显示 56 个文件已符合格式。
- 最终代码 `uv build --quiet` 成功；sdist/wheel 位于 dist。
- 新增的最终边界包括中文 UTF-8 机器接口、四进程同时注册的 WAL 初始化竞争、检查 checkpoint 时不持有调度数据库写事务、清理 outbox 幂等导入及已封存证据的篡改拒绝。
- 真实模型写入仍保留 NEEDS_INPUT 环境验收项，没有标记为通过。
