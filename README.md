# MRAC Bench · Spec Track · Milestone 1

MRAC Bench 测量模型能否在固定任务与 repository snapshot 上，通过多轮独立审计和修复，使 implementation spec 达到预定义收敛状态。

当前支持四个并存协议：默认的 `spec-mrac-v2@2` 原样复制输入 Spec，每轮结合固定仓库审计，所有发现直接进入修复，无裁决阶段；`spec-mrac-v1` 保留生成 implementation Spec 的旧流程；`spec-flow-simple-v1` 保留初始化审计后纯 Spec 冻结的流程；`exec-mrac-v1` 按显式选定的 Spec 实施代码，对照完整 diff 审计并修复。协议契约、选择与维护规则见 [并存协议管理](doc/Protocols.md)。

case 与 protocol 独立，在运行时组合。不传 `--protocol` 时使用运行层默认的 `spec-mrac-v2`；显式指定时使用所选协议。case 不需要协议字段，旧 case 中残留的 `protocol` 字段不参与选择。

v2 需要相同 Spec 字节和仓库基线上的两次独立、空 findings 审计才冻结。审计仅输出 `audit_id/findings`，不要求 checks 或证据覆盖率评审。每项发现都要修复，包括 P3。连续六轮出现 P0–P2，在第六轮修复完成时暂停；clean 或 P3-only 重置该计数。它没有 BLOCKED 状态，必要输入缺失时保留 FIX。显式总预算耗尽返回 `NON_CONVERGED`。

## 快速开始

需要 Python 3.11+、Git、uv 和已登录的 Codex CLI。本实现已在 Windows 上使用 Codex CLI 0.145.0 验证；CLI 必须支持 `exec --ephemeral --ignore-user-config --ignore-rules --output-last-message` 等参数。

在本仓库根目录运行：

```bash
uv sync
codex --version
uv run python -m mracbench run --case psf__requests-1963 --protocol spec-mrac-v1 --model <model-name>
```

模型名称必须是当前 Codex 账户可用的模型。建议显式指定 `--model`，以便准确记录实验条件。未指定时使用 Codex 内置默认模型，并在结果中记录 `model: null`；不会继承用户 config 中的模型或执行配置。

内置 case 来自 [SWE-bench Lite](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite)，固定 Requests 的 commit `110048f9837f8441ea536804115e80b69f400277`。只需读取目标源码，不安装或运行目标仓库的依赖和测试。

从其他项目制作 case：将 [casemaker.md](doc/casemaker.md) 交给该项目中的 AI，指定已定稿的 Spec 文件和实现前的仓库 commit。制作器原样封装 Spec，不从上下文补写需求；将生成的 case 目录复制到本仓库的 `cases/` 下即可。

`task.file` 指向固定输入文件（新 case 使用 `spec.md`），必填 `task.sha256` 为该文件原始字节的 SHA-256。loader 在启动 agent 前校验；缺失、格式错误或不匹配均返回 `CASE_ERROR`。已有 case 需补齐哈希。Spec 或基线变化时递增 case version；本仓库通过 `.gitattributes` 保留 case 文件的原始换行。

## 配置

```bash
uv run python -m mracbench run \
  --case psf__requests-1963 \
  --protocol spec-mrac-v1 \
  --model <model-name> \
  --max-rounds 8 \
  --timeout 600
```

在 PowerShell 中可将命令写成一行。参数说明：

| 参数 | 含义 |
|---|---|
| `--case` | `cases/<id>/` 目录中的 case id |
| `--protocol` | 选择本次运行协议，默认 `spec-mrac-v2`；与 case 无关 |
| `--spec-file` | exec-mrac-v1 必填：显式选定的不可变执行 Spec；其他 run 协议不接受 |
| `--project-root` | case/protocol 所在项目根目录，默认当前目录 |
| `--model` | 传给每次 `codex exec` 的模型名 |
| `--reasoning-effort` | 显式推理强度，保存并用于各阶段；不指定则使用 CLI/模型默认值 |
| `--max-rounds` | 覆盖最大 audit invocation 数，必须为正整数 |
| `--timeout` | 每次 agent invocation 超时秒数，必须为正整数 |
| `--runs-dir` | run 输出根目录，默认 `<project-root>/runs` |
| `--workspace-dir` | checkout 根目录，默认 `<project-root>/.workspaces`；exec 每个 run 有独立可写目录 |
| `--codex-executable` | Codex 可执行文件名称/路径，默认 `codex` |

v2 的总审计上限默认为无，仅使用 `--max-rounds` 或协议配置，不读取 case 的历史轮数预算。两个旧 Spec 协议仍按命令参数 → case limits → protocol limits（默认 8）。exec-mrac-v1 每批固定 6 次 audit，耗尽后显式 resume 增加 6 次。超时优先级仍为命令参数 → case limits（默认 1800 秒）。连续 clean 固定要求 2 次。

运行退出码：`0` 为 `CONVERGED`，`1` 为 `NON_CONVERGED`，`2` 为配置/执行/校验错误，`3` 为 `PAUSED`；v2 另有 `5`（`NEEDS_INPUT`，阶段仍是 FIX）和 `6`（`ABORTED`）。`4`（BLOCKED）仅用于旧的 spec-flow-simple-v1。v2 最少两次 audit 即可冻结。

运行 v2、查看状态和提供必要输入：

```powershell
uv run python -m mracbench run --case <case-id> --protocol spec-mrac-v2 --model gpt-5.6-luna --reasoning-effort high
uv run python -m mracbench status --run-dir C:\mrac-runs\<run-id>
uv run python -m mracbench resume --run-dir C:\mrac-runs\<run-id>
# 仅在有具体待答问题时提供 UTF-8 回答文件
uv run python -m mracbench resume --run-dir C:\mrac-runs\<run-id> --input-file C:\answers.md
uv run python -m mracbench report --run-dir C:\mrac-runs\<run-id>
```

v2 当前状态版本为 3。`resume` 使用保存的输入与协议快照，保持模型、基线和预算不变：暂停后继续新审计；未完成审计会被废弃并重新发起；FIX 保留所有待修复项。无新回答的 NEEDS_INPUT 只报告当前问题。历史 `spec-mrac-v2@1`（state 2）仅能 status/report，只读保留旧结果；使用新语义须新开 run，不能继承旧 clean。旧 spec-flow-simple-v1 仍只支持 PAUSED 恢复，旧 BLOCKED 记录不会迁移。

## 按 Spec 执行代码

```powershell
uv run python -m mracbench run --case <case-id> --protocol exec-mrac-v1 --spec-file C:\approved\spec.md --model gpt-5.6-luna --reasoning-effort high --runs-dir C:\mrac-runs --workspace-dir C:\mrac-workspaces
uv run python -m mracbench resume --run-dir C:\mrac-runs\<run-id>
```

exec 先在 `<workspace-dir>/exec/<run-id>/` 实施，然后独立只读审计 Spec + 完整产品 diff。
各阶段允许自行使用只读 Git 命令检查差异；普通 `git diff` 不含未跟踪文件，须同时
枚举并读取新增文件。prompt 提供 Spec、checkout 路径、基线 SHA 和候选 patch 路径/身份，
不提供完整文件清单。磁盘仅保存完整 patch 与候选身份元数据；校验时在内存计算整个
checkout（含 ignored 输出）的文件哈希，保留审计写入检测，不落盘完整文件清单。
所有发现（包括 P3）均需修复；相同 Spec、基线和代码候选连续两轮零问题才收敛。
第六轮有问题时保存问题并暂停，不执行该轮修复；继续时总额度增加六次，先修复再审计。
第六轮若是首次 clean，产品代码不变则继续保留。初次实施和修复不占 audit 预算。

Spec 原文、hash、每轮 patch、新增/删除/二进制文件、验证记录和续跑记录均保存。
默认协议仍是 spec-mrac-v2；只有显式选择 exec 才允许在独立 checkout 写代码。
运行器不自动提交、推送或向用户项目应用变更。完整边界见[协议管理文档](doc/Protocols.md)。

## 运行记录

结束时命令会打印 run id、终态和 `result.json` 的绝对路径。执行过程中可查看 `state.json`、`logs/runner.log` 和 raw 文件。

```text
runs/<run-id>/
  run.yaml                    # 有效配置、环境、输入哈希和仓库信息
  state.json                  # 最近一次阶段检查点；按协议支持暂停和未完成阶段恢复
  result.json                 # 最终状态、trajectory 和计数
  input/                      # case/task/protocol/prompts 原文快照
    repository-manifest.json  # checkout 文件内容哈希基线
  artifacts/
    spec.initial.md
    spec.round-01.md           # 第一次成功 repair 的完整正文
  audits/
    audit-01.json              # 通过校验的 audit
  raw/<stage>/
    request.txt               # 实际发送的完整 prompt
    stdout.txt                # Codex JSONL 事件流，执行时持续写盘
    stderr.txt                # 原始诊断输出，执行时持续写盘
    final.txt                 # 单独保存的最终回复
    invocation.json           # 命令、PID、时间、退出码、启动状态
    execution.json            # adapter 统一执行结果与可得 usage
    repository-before.json
    repository-after.json
  logs/
    runner.log
    repository.log
```

审计预算只统计已启动的 audit 调用，spec-flow-simple-v1 的初始化 audit 也计入；repair 单独计数，仅有裁决的旧协议另外统计 review。v2 新运行按 reported_count/reported_by_severity 统计全部发现，不生成裁决记录。失败 audit 也有 trajectory 项，blocking 数为 `null`，状态为错误类型。调用未启动时保留 raw 错误记录但不计入 audit 轮数。无效 JSON 不会转换成 clean，也不会自动重试。

所有历史 spec 都保留；连续 clean 的两轮必须审计同一 artifact。最终结果附带 `final_artifact`，正常耗尽预算时也能查看最后一份 spec。

usage 的总 token/cost 当前记为 `null`；每次调用若有 Codex usage 则保存在 `execution.json`。墙钟时间有实际数值。

## 独立性与仓库保护

每个阶段使用新的 `codex exec`，不 resume 模型会话，不保留 session；Bench 的 PAUSED 恢复也会创建全新模型调用。调用忽略用户执行配置和规则文件，禁用 Web 搜索、多 agent 和 repository/user instruction 文档加载。各阶段输入通过明确的 JSON 字段提供；metadata 和旧 audit 不进入 auditor prompt。spec-flow-simple-v1 的冻结审计只接收当前 Spec 和标识/hash，使用空工作目录。v2 每轮必须读取固定基线仓库，显式提供固定提交中的仓库指令路径，由 prompt 要求读取相关规则；不会自动加载本机用户规则。

Spec 协议采用 Codex read-only sandbox，并在调用前后检查 HEAD、Git 状态与文件内容哈希；包括忽略文件在内的新增/删除/修改均视为违规。exec 的 IMPLEMENT/FIX 使用 workspace-write，允许专属 checkout 的产品修改；AUDIT 只读并检查包括 ignored 文件在内的写入变化，所有阶段保护固定基线和 Git 控制状态。历史结果放在 checkout 外。隔离边界不等同于容器或严格的文件读取白名单：禁止读取父目录、历史 run 和外部来源也通过 protocol prompt 约束。

缓存以 URL 哈希和固定 SHA 区分，每次只 fetch 指定 commit。已污染的缓存不会自动 reset 或删除，下一次运行会返回 `REPOSITORY_ERROR`。排查后可选择新的 `--workspace-dir`。Spec 协议同一 checkout 的锁覆盖整个 run；exec 使用每个 run 独有的 checkout 和 run 锁。异常退出后残留锁需要确认原进程已结束再手动处理。M1 不支持 submodule 仓库。

只读检查发现改动时，`PROTOCOL_VIOLATION` 优先于 agent/解析错误；差异保存在对应阶段的 `repository-after.json`。普通 agent 错误为 `AGENT_ERROR`，超时为 `TIMEOUT`，无效输出为 `PARSE_ERROR`。这些都不是正常未收敛。

## 多任务并行编排

新增独立持久化编排服务、Windows Supervisor、case 注册和集中 repo 管理。只读 run 跨批次复用固定 repo，可写 run 各自拥有 checkout；批次证据与单次证据分别放在同一个批次目录中。case 支持编号、名称及旧名称调用。

```powershell
uv run mracbench case register cases/psf__requests-1963 --name requests-1963 --request-id register-requests-v1 --bench-home C:\mrac-data
uv run mracbench batch submit examples/batch.yaml --request-id comparison-001 --bench-home C:\mrac-data
uv run mracbench orchestrator serve --total 4 --group codex-main=2 --bench-home C:\mrac-data
```

先按账户可用模型调整示例 YAML。提交可离线排队；恢复不扩预算，exec 暂停后显式 continue 才增加六次 audit。操作和清理规则见[使用说明](doc/Parallel%20Orchestration%20Guide.md)，设计依据见 [Spec](doc/Parallel%20Orchestration%20Spec.md) 和 [Plan](doc/Parallel%20Orchestration%20Plan.md)，验证与真实环境限制见[实施记录](doc/Parallel%20Orchestration%20Implementation%20Report.md)。

## 验证与开发

```bash
uv run pytest -q
uv run ruff check mracbench mrac_contracts mrac_resources mrac_orchestrator tests scripts
uv run ruff format --check mracbench mrac_contracts mrac_resources mrac_orchestrator tests scripts
```

常规测试使用本地临时 Git 仓库、stub adapter 和临时 CLI 程序；不联网、不调用真实模型。测试覆盖输入校验、audit 解析、收敛状态机、runner 错误和不可变产物、仓库污染，以及真实子进程的输出分流、非零退出和超时终止。

代码入口与职责：

- `cases.py` / `protocol.py`：加载、校验与 prompt 构造。
- `repository.py`：固定 commit checkout、缓存锁和改动检测。
- `exec_flow.py` / `exec_repository.py` / `exec_audit.py`：可写执行、完整候选 patch、严格零问题审计和六轮续跑。
- `codex_exec.py`：Codex 进程调用；不理解 MRAC 阶段。
- `audit.py`：audit JSON 与 spec 外层格式校验、收敛状态。
- `runner.py`：单 case 状态流转；仅依赖 adapter 接口。
- `repository_flow.py` / `repository_audit.py`：v2 审计 findings 直接进入 FIX、暂停/输入/未完成审计恢复及历史只读报告。
- `simple_flow.py` / `simple_audit.py`：初始化/裁决/冻结流程、严格输出契约和暂停恢复。
- `execution.py` / `evidence.py`：共享只读调用，以及新协议不可变证据和 run 锁。
- `runs.py`：输入快照、阶段状态、日志和最终结果。

Bench 不判断最终 Spec 的绝对正确性。Spec 解析只检查文档外层格式；内容正确性由各自 audit 协议测量。批次已支持矩阵和 repeat；任意损坏状态的自动恢复和其他 agent 实现尚未加入，恢复仍要求可校验的检查点。受管进程监督当前只验收 Windows 10/11。

设计依据：[Milestone 1 Spec](doc/MRAC%20Bench%20Spec%20Track%20v0.1%20Milestone%201%20Spec.md) · [Milestone 1 Plan](doc/MRAC%20Bench%20Spec%20Track%20v0.1%20Milestone%201%20Plan.md)。验收证据见 [实施与验收记录](doc/Milestone%201%20Implementation%20Report.md)。Codex 集成参考：[官方非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode)。
