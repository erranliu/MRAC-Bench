# MRAC Bench Spec Track v0.1

## 1. 项目定位

MRAC Bench 是一个用于测量 AI 在多轮“审计 → 修复 → 再审计”过程中是否能够稳定收敛的 benchmark framework。

当前版本只实现 **Spec-MRAC Track**：

> 给定一个真实软件工程任务、固定 repository snapshot 与原始任务描述，让 AI 生成 implementation spec，并通过多轮独立审计与修复，观察 spec 是否能在固定轮数预算内收敛。

当前版本的目标不是覆盖完整的软件工程执行，而是首先建立稳定、可复现、可扩展的：

1. MRAC Protocol
2. Case Package
3. Runner
4. Result / trajectory 记录格式
5. Codex Exec Adapter

后续版本将在相同基础架构上扩展：

- Execution-MRAC
- Roadmap-MRAC
- Architecture-MRAC
- Test-Plan-MRAC
- 多 agent 支持
- Docker execution environment
- leaderboard / distributed execution

当前版本固定使用 `codex exec`，不实现多 agent，但必须保留 AgentAdapter 扩展边界。

---

# 2. 核心研究对象

Spec-MRAC 的基本流程：

```text
Original Task
+
Repository Snapshot
        ↓
Generate Spec
        ↓
Independent Audit
        ↓
Blocking Issues?
   ├── YES
   │    ↓
   │ Repair Spec
   │    ↓
   │ Independent Audit
   │    ↓
   │ ...
   │
   └── NO
        ↓
    CONVERGED
```

达到最大 audit round 数仍未满足收敛条件：

```text
NON_CONVERGED
```

MRAC Bench 当前不判断：

> “最终 spec 是否绝对正确”。

它测量的是：

> 在固定 repository、任务、prompt protocol 和 agent/model 条件下，AI 是否能通过独立审计—修复循环，使目标 artifact 在有限预算内达到预定义收敛状态。

---

# 3. v0.1 核心目标

必须完成以下能力：

## 3.1 Case Package

每个 benchmark case 可以独立描述：

- case id
- case version
- repository URL
- 固定 commit
- 原始 task/spec
- metadata
- 使用的 protocol
- 最大 MRAC rounds

Case 应可以由用户直接运行。

例如：

```bash
mracbench run --case featurebench-037
```

---

## 3.2 MRAC Protocol

MRAC Protocol 必须独立于具体 case。

至少包含三个阶段：

```text
generate
audit
repair
```

Protocol 必须可版本化，例如：

```text
spec-mrac-v1
spec-mrac-v1.1
```

不同 case 默认使用同一个 protocol。

禁止为单个 case 编写特殊 audit prompt 或 repair prompt，除非未来明确引入特殊 case 类型。

---

## 3.3 Codex Exec Agent

v0.1 只支持：

```text
codex exec
```

但必须通过统一接口调用，不允许 runner 直接散布 subprocess 调用逻辑。

接口概念：

```python
class AgentAdapter:
    def run(
        self,
        prompt: str,
        workspace: Path,
        *,
        timeout: int | None = None,
        readonly: bool = False,
        metadata: dict | None = None,
    ) -> AgentResult:
        ...
```

当前实现：

```python
class CodexExecAdapter(AgentAdapter):
    ...
```

后续应可以增加：

```text
ClaudeCodeAdapter
OpenHandsAdapter
GeminiCLIAdapter
RemoteAgentAdapter
```

而无需修改 MRAC protocol engine。

---

## 3.4 MRAC Runner

Runner 负责：

1. 加载 case
2. 准备 repository snapshot
3. 初始化 run directory
4. 调用 agent 生成 spec
5. 启动独立 audit
6. 解析 audit 结果
7. 判断是否收敛
8. 若未收敛，调用 repair
9. 保存 artifact
10. 再次启动独立 audit
11. 达到收敛或 max rounds 后结束
12. 输出结构化结果

---

# 4. 当前版本明确不做的事情

v0.1 不实现：

- 多 agent
- Execution-MRAC
- build / test / code execution benchmark
- Docker case runtime
- hidden verifier
- web UI
- leaderboard server
- distributed execution
- cloud orchestration
- 自动 benchmark submission
- 自动 case mining
- 自动模型排名
- 复杂综合分数

这些必须可以未来增加，但不能增加当前版本复杂度。

---

# 5. Artifact 模型

MRAC 不应硬编码为“代码修复”。

当前核心抽象：

```text
Artifact
```

Spec Track 中：

```text
artifact_type = spec
```

未来可以扩展：

```text
artifact_type = code
artifact_type = roadmap
artifact_type = architecture
artifact_type = test_plan
```

当前 artifact 最简单可以是一个 Markdown 文件：

```text
artifacts/spec.md
```

每轮修复产生新的 snapshot：

```text
artifacts/
  spec.initial.md
  spec.round-01.md
  spec.round-02.md
  spec.round-03.md
```

禁止覆盖历史 artifact。

---

# 6. Case Package 设计

建议结构：

```text
cases/
  featurebench-037/
    case.yaml
    task.md
```

示例：

```yaml
id: featurebench-037
version: 1

source:
  benchmark: FeatureBench
  upstream_id: "037"

repository:
  url: https://github.com/example/project.git
  commit: abcdef123456

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
  tags:
    - feature
    - multi-file
```

原则：

- case package 不包含 repository 本体
- repository 通过 URL + commit 唯一固定
- task 必须保存为本地静态文件
- upstream benchmark 信息保留
- case metadata 不参与模型 prompt，除非明确规定

---

# 7. Repository Workspace

Spec-MRAC 不需要可执行 runtime。

默认流程：

```text
git clone
↓
checkout fixed commit
↓
workspace
```

repository 应固定在：

```text
.workspaces/<repo-hash>/<commit>/
```

可以缓存。

当前版本可以暂时不强制 OS 级 readonly mount，但 Protocol 必须明确：

> Spec Track 中 agent 不得修改 repository source files。

允许写入的只有 run directory / artifacts。

如果 Codex 本身难以技术性强制 readonly，可以：

1. prompt 明确禁止修改 repo
2. 每轮前后检查 git diff
3. 若 repository 被修改，则 run 标记为 protocol violation

未来 Docker 版本再提供强制 readonly mount。

---

# 8. Protocol Package

目录：

```text
protocols/
  spec-mrac-v1/
    protocol.yaml
    generate.md
    audit.md
    repair.md
```

`protocol.yaml` 示例：

```yaml
id: spec-mrac-v1
version: 1

artifact_type: spec

stages:
  generate:
    prompt: generate.md

  audit:
    prompt: audit.md

  repair:
    prompt: repair.md

convergence:
  type: consecutive_clean_audits
  required_clean_audits: 2

limits:
  max_audit_rounds: 8
```

---

# 9. Generate Protocol

输入：

- original task
- repository path / repository context
- protocol instruction

输出：

```text
spec.md
```

Spec 应描述足够完整的 implementation plan，但当前 benchmark 不预设具体 spec 模板，除非 protocol 中定义。

Generate 阶段只执行一次。

---

# 10. Audit Protocol

Audit 每轮必须是：

> 新的、独立的 agent invocation。

不得复用前一轮 audit session。

Auditor 输入：

- original task
- fixed repository
- 当前 spec artifact
- audit prompt

Auditor 不应看到：

- 之前 audit 的文本
- 之前 issue list
- 之前 auditor reasoning
- gold implementation
- hidden tests
- 后续真实 PR

目的是最大化 audit 独立性。

---

# 11. Audit 输出格式

必须结构化。

建议要求 agent 最终输出 JSON，例如：

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

无 blocking issue：

```json
{
  "status": "clean",
  "issues": []
}
```

v0.1 收敛判断只关心：

```text
blocking issue 是否存在
```

不要用 issue 数量作为主评分依据。

如果需要 severity，第一版建议只允许：

```text
blocking
non_blocking
```

MRAC convergence 只由 blocking issue 决定。

---

# 12. Repair Protocol

Repair 输入：

- original task
- repository
- 当前 spec
- 当前这一轮 audit 输出
- repair prompt

输出：

```text
完整的新 spec
```

不是 patch。

必须保存为新 artifact snapshot。

Repair agent 可以看到当前轮 audit 结果。

但下一轮 auditor 不应看到旧 audit。

---

# 13. 收敛定义

v0.1 默认：

> 连续两次独立 audit 均不存在 blocking issue，视为 CONVERGED。

例如：

```text
Audit 1: issues
Repair

Audit 2: clean
Audit 3: clean

=> converged
```

如果：

```text
Audit 2: clean
Audit 3: issues
```

则继续：

```text
Repair
Audit 4
...
```

达到：

```text
max_audit_rounds
```

仍未满足连续两次 clean：

```text
NON_CONVERGED
```

这里的 audit round 指 audit invocation 数量。

---

# 14. Run Directory

每次执行生成独立 run：

```text
runs/
  20260916-xxxx-case037/
    run.yaml
    result.json

    input/
      task.md
      case.yaml
      protocol.yaml

    artifacts/
      spec.initial.md
      spec.round-01.md
      spec.round-02.md

    audits/
      audit-01.json
      audit-02.json
      audit-03.json

    raw/
      generate/
      audit-01/
      repair-01/
      audit-02/
      ...

    logs/
      runner.log
```

所有原始 agent stdout/stderr 都应保存。

Benchmark 的核心要求之一是：

> trajectory 完全可审计。

---

# 15. Result Schema

最终：

```json
{
  "run_id": "...",
  "case_id": "featurebench-037",
  "case_version": 1,
  "protocol_id": "spec-mrac-v1",

  "agent": {
    "type": "codex_exec",
    "model": "...",
    "version": "..."
  },

  "status": "converged",
  "convergence_round": 4,

  "audit_rounds": 5,
  "repair_rounds": 3,

  "trajectory": [
    {
      "audit_round": 1,
      "blocking_issue_count": 5,
      "status": "issues_found"
    },
    {
      "audit_round": 2,
      "blocking_issue_count": 2,
      "status": "issues_found"
    },
    {
      "audit_round": 3,
      "blocking_issue_count": 0,
      "status": "clean"
    }
  ],

  "protocol_violation": false,

  "usage": {
    "wall_time_seconds": null,
    "tokens": null,
    "cost": null
  }
}
```

usage 字段拿不到时允许 null。

---

# 16. CLI

至少实现：

```bash
mracbench list-cases
```

```bash
mracbench show-case <case-id>
```

```bash
mracbench run --case <case-id>
```

指定 model：

```bash
mracbench run \
  --case featurebench-037 \
  --model <model-name>
```

覆盖最大轮数：

```bash
mracbench run \
  --case featurebench-037 \
  --max-rounds 8
```

运行 suite：

```bash
mracbench run \
  --suite core-v1
```

查看：

```bash
mracbench show-run <run-id>
```

---

# 17. Suite

即使第一版只有少量 case，也需要支持 suite。

目录：

```text
suites/
  core-v1.yaml
```

示例：

```yaml
id: core-v1
version: 1

track: spec

protocol:
  id: spec-mrac-v1

cases:
  - featurebench-037
  - featurebench-081
  - featurebench-123
```

Suite 是正式 benchmark 发布单位。

---

# 18. Scoring

v0.1 不需要复杂评分系统。

每次 run：

```text
CONVERGED
NON_CONVERGED
ERROR
PROTOCOL_VIOLATION
```

Suite 核心指标：

```text
Convergence@N
```

例如：

```text
Convergence@8 = 14 / 20 = 70%
```

同时记录：

```text
Convergence@1
Convergence@2
...
Convergence@N
```

从而得到完整 convergence curve。

辅助指标：

```text
median convergence round
mean convergence round
```

第一版不将 issue count 纳入正式分数。

---

# 19. 重复运行

架构必须支持同一：

```text
case × model × protocol
```

重复多次。

例如：

```bash
mracbench run \
  --suite core-v1 \
  --repeat 3
```

结果：

```text
case001
run1: converge@3
run2: converge@4
run3: non-converged
```

case convergence rate：

```text
2 / 3
```

Suite 最终可以计算总体 convergence rate。

---

# 20. Crash Recovery

正式版本必须按阶段实时写盘。

每一次：

```text
generate
audit
repair
```

完成后立即保存状态。

后续应支持：

```bash
mracbench resume <run-id>
```

v0.1 如果时间有限，可以先实现状态持久化，把完整 resume 放到紧随后的版本。

但代码架构禁止假设：

> 一个 case 必须一次运行到底。

---

# 21. Agent Adapter

建议：

```text
mracbench/agents/
  base.py
  codex_exec.py
```

核心模型：

```python
@dataclass
class AgentRequest:
    prompt: str
    workspace: Path
    timeout_seconds: int | None
    readonly: bool
    metadata: dict


@dataclass
class AgentResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    duration_seconds: float
    usage: dict | None
```

Runner 只能依赖：

```text
AgentAdapter
```

不得依赖：

```text
CodexExecAdapter
```

实现细节。

---

# 22. Codex Exec Adapter

Codex adapter 应统一完成：

- command construction
- cwd
- prompt 输入
- timeout
- stdout
- stderr
- exit code
- model 参数
- raw output 保存

不要把 MRAC protocol 逻辑写进 Codex adapter。

Codex adapter 是 dumb executor。

---

# 23. Protocol Engine

建议：

```text
mracbench/protocol/
  engine.py
  loader.py
  parser.py
  convergence.py
```

Protocol Engine 负责：

```text
Generate
↓
Audit
↓
Parse
↓
Convergence check
↓
Repair
↓
Audit
...
```

它不知道 Codex 是什么。

它也不应知道具体 FeatureBench / SWE-bench 是什么。

---

# 24. Case Loader

建议：

```text
mracbench/cases/
  loader.py
  schema.py
  repository.py
```

负责：

- YAML schema validation
- repo clone/cache
- checkout commit
- task loading
- workspace preparation

未来 Docker 逻辑也应扩展在 environment 层，而不是改 Protocol Engine。

---

# 25. 推荐项目目录

```text
mrac-bench/
│
├── pyproject.toml
├── README.md
│
├── mracbench/
│   ├── __init__.py
│   ├── cli.py
│   ├── runner.py
│   ├── models.py
│   │
│   ├── agents/
│   │   ├── base.py
│   │   └── codex_exec.py
│   │
│   ├── cases/
│   │   ├── loader.py
│   │   ├── schema.py
│   │   └── repository.py
│   │
│   ├── protocol/
│   │   ├── loader.py
│   │   ├── engine.py
│   │   ├── parser.py
│   │   └── convergence.py
│   │
│   ├── runs/
│   │   ├── store.py
│   │   └── schema.py
│   │
│   └── suites/
│       └── loader.py
│
├── protocols/
│   └── spec-mrac-v1/
│       ├── protocol.yaml
│       ├── generate.md
│       ├── audit.md
│       └── repair.md
│
├── cases/
│   └── ...
│
├── suites/
│   └── core-v1.yaml
│
├── schemas/
│
├── tests/
│
└── docs/
```

---

# 26. 测试要求

必须包含单元测试，至少覆盖：

## Case

- valid case loads
- invalid case fails
- repository commit 固定

## Protocol

- protocol loading
- audit parser
- clean detection
- issue detection
- two consecutive clean convergence
- clean → issues 不收敛
- max round termination

## Agent

提供：

```text
FakeAgentAdapter
```

用于测试 runner。

不要让核心测试依赖真实 Codex API。

例如 FakeAgent 可以预设：

```text
audit1 → issues
repair1 → success
audit2 → clean
audit3 → clean
```

验证最终：

```text
CONVERGED
```

---

# 27. Error Model

至少区分：

```text
CONVERGED
NON_CONVERGED

AGENT_ERROR
TIMEOUT
PARSE_ERROR
REPOSITORY_ERROR
PROTOCOL_VIOLATION
INTERNAL_ERROR
```

不能把执行失败当成 NON_CONVERGED。

`NON_CONVERGED` 必须只表示：

> MRAC 正常执行完预算，但没有达到收敛条件。

---

# 28. 可复现性信息

每次 run 必须保存：

- MRAC Bench git commit
- case id/version
- protocol id/version
- repo URL
- repo commit
- agent
- model
- Codex CLI version
- OS
- Python version
- start/end timestamp
- effective config

未来 leaderboard 必须可以基于这些字段验证实验条件。

---

# 29. README 的最小用户体验

用户 clone 后应可以：

```bash
git clone ...
cd mrac-bench

uv sync
```

确认 Codex：

```bash
codex --version
```

查看 case：

```bash
mracbench list-cases
```

运行：

```bash
mracbench run --case example-case
```

或者：

```bash
mracbench run --suite core-v1
```

最后直接显示：

```text
MRAC Bench

Case: featurebench-037
Protocol: spec-mrac-v1
Agent: codex
Model: xxx

Audit 1: 6 blocking issues
Repair 1: complete

Audit 2: 2 blocking issues
Repair 2: complete

Audit 3: clean
Audit 4: clean

CONVERGED @ 4
```

---

# 30. 第一阶段实施顺序

不要一次完成所有功能。

## Milestone 1：单 case 闭环

完成：

```text
case load
repo checkout
codex exec
generate
audit
repair
convergence
result save
```

使用一个真实 case 跑通。

---

## Milestone 2：工程结构稳定化

完成：

- AgentAdapter
- Protocol package
- Case package
- result schema
- FakeAgent tests
- CLI

---

## Milestone 3：Multiple Cases

完成：

- suite
- sequential execution
- summary
- repeat runs

---

## Milestone 4：正式可发布质量

完成：

- crash-safe state persistence
- resume
- improved errors
- documentation
- package install
- protocol version locking
- reproducibility metadata

---

# 31. 架构约束

开发中必须遵守以下原则。

### 31.1 不提前做通用 agent platform

当前唯一 executor：

```text
codex exec
```

只保留 AgentAdapter。

不要实现 plugin system、RPC framework 或 agent registry 等非必要设施。

### 31.2 Case 与 Protocol 分离

Case 定义：

```text
测什么任务
```

Protocol 定义：

```text
如何运行 MRAC
```

不得混淆。

### 31.3 Protocol 与 Agent 分离

Protocol 不得包含 Codex-specific command。

Agent 不得理解 MRAC。

### 31.4 MRAC Engine 与 software engineering 解耦

Engine 操作的是：

```text
artifact
```

不是：

```text
code
```

这样后续才能扩展到不同 AI 工作环节。

### 31.5 完整 trajectory 优先

任何为了“方便”而丢失历史 audit、artifact、raw output 的实现都不可接受。

MRAC Bench 的研究价值很大一部分来自：

> 可完整审计模型如何收敛或不收敛。

---

# 32. v0.1 完成定义

满足以下条件即认为 v0.1 工程成立：

1. 至少 3 个真实复杂开源 case
2. case 使用固定 repo commit
3. 用户可以一条 CLI 直接运行
4. 固定使用 `codex exec`
5. 自动完成 spec generate
6. 自动完成多轮 independent audit
7. 自动完成 repair
8. 自动判断 convergence
9. 支持 max rounds
10. 保存完整 trajectory
11. 输出结构化 result
12. suite 可以连续运行多个 case
13. 核心 runner 使用 FakeAgent 有完整测试
14. AgentAdapter 已存在，未来增加 agent 不需要修改 Protocol Engine
15. 架构能够未来加入 Execution-MRAC，而不需要重写 Case / Protocol / Result 主体

---

# 33. 当前版本最终产品形态

用户最终应只需要：

```bash
mracbench run \
  --suite core-v1 \
  --model <model>
```

系统自动：

```text
load cases
↓
prepare fixed repos
↓
generate spec
↓
audit
↓
repair
↓
re-audit
↓
determine convergence
↓
repeat for all cases
↓
save full trajectories
↓
output benchmark summary
```

这个版本本身就是一个可以公开发布和被其他研究者复现的 **MRAC Bench Spec Track**。

后续完整 MRAC Bench 应在此基础上增加新的 artifact track，而不是重新设计基础架构。