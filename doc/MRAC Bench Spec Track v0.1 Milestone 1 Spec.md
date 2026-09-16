# MRAC Bench Spec Track v0.1 — Milestone 1 实施规范

## 文档信息

- 状态：实施规范
- 范围：单个 Spec-MRAC case 的端到端闭环
- 依据：[MRAC Bench Spec Track v0.1 项目立项文档](./MRAC%20Bench%20Spec%20Track%20v0.1%20项目立项文档.md)
- 本文中的 MUST、SHOULD、MAY 分别表示必须、建议和可选要求。

## 1. 目标与完成标准

Milestone 1 建立最小可运行的 Spec-MRAC 执行链：读取一个 case，准备固定版本的仓库，调用 `codex exec` 生成 spec，由独立调用执行 audit，根据 audit 结果修复，并依照固定收敛规则结束运行。运行结束后，必须能从 run directory 检查输入、每版 spec、每轮 audit、原始 agent 输出和最终结果。

Milestone 1 完成时，开发者应能针对一个真实 case 发起一次运行，并得到以下终态之一：

- `CONVERGED`：连续两轮独立 audit 没有 blocking issue。
- `NON_CONVERGED`：正常执行至 audit 轮数上限，仍未达到收敛条件。
- 明确的错误终态：case、仓库、agent、超时或 audit 解析失败等。错误不得伪装成 `NON_CONVERGED`。

本里程碑以一条 case 的真实 `codex exec` 集成为验收目标。可提供仅支持单 case 的开发入口；完整安装体验和稳定 CLI 属于 Milestone 2。

## 2. 范围

### 2.1 必须实现

1. 加载并验证一个本地 case package。
2. 依据 repository URL 与完整 commit SHA 准备仓库，并确认 checkout 后的 HEAD 与指定 SHA 一致。
3. 将 task、case 配置和 protocol 配置作为本次运行的不可变输入快照保存。
4. 使用 `codex exec` 完成一次 spec generation。
5. 每轮 audit 使用新的 agent invocation；audit 只获得原始任务、固定仓库、当前 spec 和通用 audit prompt。
6. 对 audit JSON 做结构校验，识别 blocking issue。
7. 有 blocking issue 时调用 repair，保存完整新 spec；无 blocking issue 时不做多余 repair。
8. 按连续两次 clean audit 判断收敛，并支持最大 audit 轮数。
9. 保存完整 trajectory、agent 原始 stdout/stderr、运行元信息和结构化 `result.json`。
10. 检查并记录 agent 是否改动固定仓库；发生改动时以 `PROTOCOL_VIOLATION` 结束。

### 2.2 不在本里程碑实现

- 多 case suite、重复运行和汇总评分。
- 多 agent、除 `codex exec` 之外的 adapter 实现。
- 完整 AgentAdapter/Protocol/Case 通用框架的稳定化工作；可先保留最小模块边界，完整测试和固化属于 Milestone 2。
- `resume`、崩溃后续跑、分布式执行和并发调度。
- Docker、hidden verifier、build/test/code execution benchmark、Web UI 或 leaderboard。
- 自动 case 挖掘、自动模型排名或综合分数。

## 3. 输入与配置

运行以仓库中的一个 case 目录为入口，例如：

```text
cases/<case-id>/
  case.yaml
  task.md
```

`case.yaml` 至少包含：

```yaml
id: example-case
version: 1

source:
  benchmark: example
  upstream_id: "123"

repository:
  url: https://github.com/example/project.git
  commit: 0123456789abcdef0123456789abcdef01234567

task:
  file: task.md

track:
  type: spec

protocol:
  id: spec-mrac-v1

limits:
  max_audit_rounds: 8
  agent_timeout_seconds: 1800

metadata:
  language: python
  tags: []
```

校验要求：

- `id`、正整数 `version`、`repository.url`、`repository.commit`、`task.file`、track 与 protocol id 必须存在且类型正确。
- `repository.commit` MUST 是完整 commit SHA，不能使用分支名、tag 或短 SHA。
- `task.file` MUST 解析到 case 目录内的普通文件；缺失或越出 case 目录均为 `CASE_ERROR`。
- `track.type` 在本里程碑只接受 `spec`。
- `max_audit_rounds` 未填写时默认为 8，且必须大于 0；命令行覆盖（若开发入口支持）必须记录进 effective config。
- metadata 不进入 agent prompt。task 文件内容是原始任务的唯一权威文本。

Protocol 从 `protocols/spec-mrac-v1/` 读取，至少包含 `protocol.yaml`、`generate.md`、`audit.md` 和 `repair.md`。M1 可只支持一个 protocol id，但 prompt 文件必须作为 run 输入快照保存，禁止在执行中静默变化。

## 4. 单 case 运行流程

运行器按以下顺序执行；任何失败都应尽可能先保存已有阶段产物，再写入错误结果：

1. 创建唯一 `run_id` 和 run directory。
2. 加载 case 与 protocol，验证输入，并复制 case、task、protocol 配置和 prompt 文件到 `input/`。
3. 准备固定仓库：clone 或复用缓存，checkout 指定 SHA；确认最终 HEAD 完全匹配。
4. 记录仓库开始状态和可用于检测改动的基线。
5. 调用 generation，保存 `spec.initial.md` 和原始执行记录。
6. 从第 1 轮开始执行 audit，每轮均为新的 `codex exec` 调用。
7. 解析并保存 audit 结果，更新连续 clean 计数。
8. 若连续 clean 计数达到 2，终态为 `CONVERGED`，`convergence_round` 记为第二轮 clean audit 的轮次。
9. 若未收敛且本轮为最后允许的 audit，终态为 `NON_CONVERGED`，不再 repair。
10. 若本轮存在至少一个 blocking issue 且仍有后续 audit 预算，则调用 repair，保存新的完整 spec，然后继续下一轮 audit。
11. 若本轮无 blocking issue 但连续 clean 尚不足 2，则不调用 repair；下一轮针对同一份 spec 再做独立 audit。
12. 写入最终 `result.json`，其中状态、计数、trajectory 和文件路径必须与实际落盘阶段一致。

`audit_rounds` 统计已启动的 audit invocation 数；`repair_rounds` 统计已启动的 repair invocation 数。由于连续 clean 判定可能需要额外 audit，`audit_rounds` 不要求等于 `repair_rounds + 1`。

### 4.1 收敛状态示例

```text
Audit 1: 有 blocking issue → Repair 1
Audit 2: clean
Audit 3: clean
结果：CONVERGED，convergence_round = 3
```

```text
Audit 1: clean
Audit 2: 有 blocking issue → Repair 1
Audit 3: clean
Audit 4: clean
结果：CONVERGED，convergence_round = 4
```

```text
Audit 1: clean
Audit 2: clean
结果：CONVERGED，convergence_round = 2；没有 repair
```

## 5. Agent 调用约定

M1 的唯一真实 agent 是本机 `codex exec`。调用层至少应把命令构造、工作目录、模型、超时、stdout/stderr 与退出码集中在一个小型执行模块中，避免 runner 各阶段自行拼装 subprocess 命令。模块返回统一结果对象，最低字段为：

```text
success
stdout
stderr
exit_code
duration_seconds
```

每个阶段都是新的进程调用，不得续用上阶段的交互 session。运行器通过 prompt 和本机可用的只读执行约束要求 agent 不修改 repository source files；spec 内容通过阶段输出写入 artifacts。开始 generation 前，checkout 必须处于干净状态；复用缓存时不得默默沿用已有改动。每个阶段结束后都检查仓库状态。发现任何 tracked 或 untracked source-tree 变更，均将运行标记为 `PROTOCOL_VIOLATION`，并记录变更清单。run directory 不得放在 repository checkout 内。

Agent 的 model 与 timeout 来自 effective config。agent 可执行文件缺失、非零退出码或 timeout 时，不得继续后续 MRAC 阶段；结果分别记录为 `AGENT_ERROR` 或 `TIMEOUT`。M1 无法取得的 token/cost 可记为 `null`。

### 5.1 阶段 prompt 的输入边界

- **Generate**：原始 task、固定仓库、generate prompt。要求输出完整 Markdown implementation spec。
- **Audit**：原始 task、固定仓库、当前 spec、audit prompt。不得提供以前的 audit 文本、issue 列表或 auditor reasoning。
- **Repair**：原始 task、固定仓库、当前 spec、本轮 audit JSON、repair prompt。要求输出完整的新 spec，而非 patch。

所有阶段都不得把 metadata 当作任务指令。Generate 与 repair 的最终输出必须是非空、完整的 Markdown spec 正文，不得用 Markdown code fence 包裹，也不得附加解释性前后缀；runner 将该最终输出保存为对应 artifact。空输出或无法提取唯一最终正文属于 `PARSE_ERROR`。Audit 的最终输出按第 6 节 JSON 规则处理。agent 原始 stdout/stderr 与完整 request 仍须分别保存，不因 artifact 解析而丢弃。Audit 必须独立于历史 audit，但可以与其他阶段使用同一 repository snapshot。

## 6. Audit 输出与解析

Audit 的最终输出必须是单个 JSON object，不带 Markdown code fence 或前后说明文字。允许字段如下：

```json
{
  "status": "issues_found",
  "issues": [
    {
      "id": "A1",
      "severity": "blocking",
      "title": "Missing compatibility handling",
      "description": "...",
      "evidence": "...",
      "required_change": "..."
    }
  ]
}
```

字段规则：

- `status` 只能是 `clean` 或 `issues_found`。
- `issues` 必须是数组；每个 issue 的 `id`、`severity`、`title`、`description`、`evidence`、`required_change` 均为非空字符串。
- `severity` 只能是 `blocking` 或 `non_blocking`。
- `status` 为 `clean` 当且仅当数组中不存在 `blocking` issue。`clean` 结果可以包含 non-blocking issue。
- `status` 为 `issues_found` 当且仅当至少存在一个 blocking issue。
- issue id 在同一轮内必须唯一。

缺字段、额外文本导致无法解码、字段类型错误或 status 与 issues 不一致，均是 `PARSE_ERROR`，不得按 clean 处理。解析后的 JSON 需规范化写入 `audits/audit-NN.json`；原始文本必须保存在对应 `raw/` 阶段目录。

## 7. Artifact 与 run 目录

每次运行必须使用独立目录，建议布局如下：

```text
runs/<run-id>/
  run.yaml
  result.json
  input/
    case.yaml
    task.md
    protocol.yaml
    generate.md
    audit.md
    repair.md
  artifacts/
    spec.initial.md
    spec.round-01.md
  audits/
    audit-01.json
    audit-02.json
  raw/
    generate/{stdout.txt,stderr.txt,request.txt}
    audit-01/{stdout.txt,stderr.txt,request.txt}
    repair-01/{stdout.txt,stderr.txt,request.txt}
  logs/
    runner.log
```

- 初始 spec 必须保存为 `spec.initial.md`。
- 第 N 次成功 repair 的完整输出保存为 `spec.round-NN.md`；不得覆盖旧 spec。
- 每轮 audit 保存解析后的 JSON 和原始输出。
- `request.txt` 保存实际发给 agent 的完整 prompt，方便复查输入边界。
- `run.yaml` 保存有效配置和运行环境元信息。
- 阶段开始/结束与失败状态应及时写入磁盘；M1 不要求断点续跑，但不得只在进程退出时一次性保存全部状态。
- 任何失败都应保留已完成阶段的所有输入、输出与 artifact。

## 8. Result 格式

`result.json` 至少满足以下结构；字段可以增加，但不得删除必需字段或改变含义：

```json
{
  "run_id": "20260916T120000Z-example-case-a1b2c3",
  "case_id": "example-case",
  "case_version": 1,
  "protocol_id": "spec-mrac-v1",
  "agent": {
    "type": "codex_exec",
    "model": "configured-model",
    "version": null
  },
  "status": "CONVERGED",
  "convergence_round": 3,
  "audit_rounds": 3,
  "repair_rounds": 1,
  "trajectory": [
    {
      "audit_round": 1,
      "artifact": "artifacts/spec.initial.md",
      "blocking_issue_count": 2,
      "status": "issues_found",
      "repair_artifact": "artifacts/spec.round-01.md"
    },
    {
      "audit_round": 2,
      "artifact": "artifacts/spec.round-01.md",
      "blocking_issue_count": 0,
      "status": "clean",
      "repair_artifact": null
    },
    {
      "audit_round": 3,
      "artifact": "artifacts/spec.round-01.md",
      "blocking_issue_count": 0,
      "status": "clean",
      "repair_artifact": null
    }
  ],
  "protocol_violation": false,
  "usage": {
    "wall_time_seconds": null,
    "tokens": null,
    "cost": null
  },
  "error": null
}
```

终态 `status` 至少支持 `CONVERGED`、`NON_CONVERGED`、`CASE_ERROR`、`REPOSITORY_ERROR`、`AGENT_ERROR`、`TIMEOUT`、`PARSE_ERROR`、`PROTOCOL_VIOLATION`、`INTERNAL_ERROR`。`convergence_round` 仅在 `CONVERGED` 时有值；其他终态为 `null`。`error` 成功时为 `null`，失败时记录稳定的错误类型与可读消息。

`audit_rounds` 统计已启动的 audit invocation，`repair_rounds` 统计已启动的 repair invocation。`trajectory` 为每个已启动的 audit invocation 记录一项：成功解析时填写 `clean` / `issues_found` 和整数 blocking 数；agent 或解析失败时填写相应错误状态、`blocking_issue_count: null`、`repair_artifact: null`。这样错误不会被当作 clean 或正常预算耗尽，但实际发生的 invocation 仍可审计。

## 9. 最小运行入口

M1 必须有可重复执行的开发入口，形式可为模块命令或项目脚本，例如：

```bash
python -m mracbench run --case example-case
```

该入口只需运行一个 case，但必须在启动前验证配置并在结束时输出 run id、终态和 `result.json` 路径。允许传入 model、run 根目录和最大 audit 轮数；如实现这些参数，覆盖值必须写入 `run.yaml`。Milestone 2 再补齐安装后的 `mracbench` CLI、case 列表、suite、show-run 等命令。

## 10. 验收检查

### 10.1 自动化检查

M1 至少要有能验证下列关键逻辑的轻量检查；调用真实 `codex exec` 的集成验收不得作为每次单元测试的前置条件：

- case 缺必需字段、task 不存在、commit 格式无效时，在启动 agent 前失败。
- audit 合法 clean / blocking 输出能正确解析；非法 JSON 和 status 不一致返回 `PARSE_ERROR`。
- 连续两轮 clean 收敛；clean → blocking 会重置 clean streak；blocking → clean → clean 会在第 3 轮收敛。
- 达到 max audit rounds 时得到 `NON_CONVERGED`，且最后一轮后不再 repair。
- 有 blocking issue 时 repair 保存为新 artifact；clean audit 不触发 repair。
- 失败结果不被记为 `NON_CONVERGED`，已完成阶段产物仍存在。

完整 FakeAgent runner 测试矩阵属于 Milestone 2；M1 的轻量检查可以使用简单 stub 或纯函数测试。

### 10.2 真实 case 验收

至少选定一个具有固定公开 repository commit 的真实 case，完成一次真实 Codex 闭环运行。验收需确认：

- checkout HEAD 与 case 中 commit 一致。
- generate 输出存在且非空。
- 每轮 audit 都有独立 raw invocation 和可解析 JSON。
- 发生 repair 时，repair 得到完整新 spec，历史 artifact 未覆盖。
- 达到收敛、正常预算耗尽或明确错误之一，结果状态与 trajectory 一致。
- repository 没有被修改，或如被修改则被检测并标为 `PROTOCOL_VIOLATION`。
- `result.json`、run 配置快照、prompt、stdout/stderr 与所有阶段 artifact 均可供人工复核。

真实验收的结果可以是 `NON_CONVERGED`；Milestone 1 验证闭环与记录能力，不要求模型必须收敛。

## 11. 实施完成定义

当且仅当以下条件全部满足，Milestone 1 才算完成：

1. 单 case 开发入口可以从本地 case 配置启动完整流程。
2. 固定仓库快照校验、generation、independent audit、必要时 repair、收敛判断均已贯通。
3. `max_audit_rounds`、两次连续 clean 规则和错误状态按本文执行。
4. 运行输入、agent 请求与原始输出、全部 artifact、audit JSON、run metadata 和最终结果均被持久化。
5. 至少一例真实 case 完成 Codex 集成验收，并保留可复核 run directory。
6. 轻量自动化检查通过。

## 12. 从立项文档继承的关键约束

- Case 定义任务；Protocol 定义执行方法；两者分离。
- Protocol 不含 Codex 命令细节；Codex 执行层不负责 MRAC 流程。
- MRAC 操作的是 artifact；当前 artifact 为 spec Markdown。
- 每轮 audit 必须独立，且不能看到前轮 audit 内容。
- 只以 blocking issue 判断收敛，不以 issue 数量计分。
- 保留完整 trajectory；不得覆盖历史 spec 或丢弃原始输出。
- M1 仅使用 `codex exec` 和一个真实 case；后续扩展不得要求重写核心运行流程。
