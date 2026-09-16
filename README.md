# MRAC Bench · Spec Track · Milestone 1

MRAC Bench 测量模型能否在固定任务与 repository snapshot 上，通过多轮独立审计和修复，使 implementation spec 达到预定义收敛状态。

当前实现支持一个 case 的完整闭环：生成 spec → 独立 audit → 必要时 repair → 再次 audit。连续两次 audit 没有 blocking issue 时收敛；最后一轮仍未满足条件时返回 `NON_CONVERGED`。

## 快速开始

需要 Python 3.11+、Git、uv 和已登录的 Codex CLI。本实现已在 Windows 上使用 Codex CLI 0.145.0 验证；CLI 必须支持 `exec --ephemeral --ignore-user-config --ignore-rules --output-last-message` 等参数。

在本仓库根目录运行：

```bash
uv sync
codex --version
uv run python -m mracbench run --case psf__requests-1963 --model <model-name>
```

模型名称必须是当前 Codex 账户可用的模型。建议显式指定 `--model`，以便准确记录实验条件。未指定时使用 Codex 内置默认模型，并在结果中记录 `model: null`；不会继承用户 config 中的模型或执行配置。

内置 case 来自 [SWE-bench Lite](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite)，固定 Requests 的 commit `110048f9837f8441ea536804115e80b69f400277`。只需读取目标源码，不安装或运行目标仓库的依赖和测试。

从其他项目制作 case：将 [casemaker.md](doc/casemaker.md) 交给该项目中的 AI，指定已定稿的 Spec 文件和实现前的仓库 commit。制作器原样封装 Spec，不从上下文补写需求；将生成的 case 目录复制到本仓库的 `cases/` 下即可。

`task.file` 指向固定输入文件（新 case 使用 `spec.md`），必填 `task.sha256` 为该文件原始字节的 SHA-256。loader 在启动 agent 前校验；缺失、格式错误或不匹配均返回 `CASE_ERROR`。已有 case 需补齐哈希。Spec 或基线变化时递增 case version；本仓库通过 `.gitattributes` 保留 case 文件的原始换行。

## 配置

```bash
uv run python -m mracbench run \
  --case psf__requests-1963 \
  --model <model-name> \
  --max-rounds 8 \
  --timeout 600
```

在 PowerShell 中可将命令写成一行。参数说明：

| 参数 | 含义 |
|---|---|
| `--case` | `cases/<id>/` 目录中的 case id |
| `--project-root` | case/protocol 所在项目根目录，默认当前目录 |
| `--model` | 传给每次 `codex exec` 的模型名 |
| `--max-rounds` | 覆盖最大 audit invocation 数，必须为正整数 |
| `--timeout` | 每次 agent invocation 超时秒数，必须为正整数 |
| `--runs-dir` | run 输出根目录，默认 `<project-root>/runs` |
| `--workspace-dir` | Git 缓存根目录，默认 `<project-root>/.workspaces` |
| `--codex-executable` | Codex 可执行文件名称/路径，默认 `codex` |

最大轮数优先级为命令参数 → case limits → protocol limits（默认 8）。超时优先级为命令参数 → case limits（默认 1800 秒）。连续 clean 的要求固定为 2，不提供覆盖。

进程退出码：`0` 表示 `CONVERGED`，`1` 表示 `NON_CONVERGED`，`2` 表示配置或执行错误。`--max-rounds 1` 可以运行，但无法满足两次 clean 的收敛条件。

## 运行记录

结束时命令会打印 run id、终态和 `result.json` 的绝对路径。执行过程中可查看 `state.json`、`logs/runner.log` 和 raw 文件。

```text
runs/<run-id>/
  run.yaml                    # 有效配置、环境、输入哈希和仓库信息
  state.json                  # 最近一次阶段检查点；M1 不支持 resume
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

审计预算只统计已启动的 audit 调用，repair 单独计数。失败 audit 也有 trajectory 项，blocking 数为 `null`，状态为错误类型。调用未启动时保留 raw 错误记录但不计入 audit 轮数。无效 JSON 不会转换成 clean，也不会自动重试。

所有历史 spec 都保留；连续 clean 的两轮必须审计同一 artifact。最终结果附带 `final_artifact`，正常耗尽预算时也能查看最后一份 spec。

usage 的总 token/cost 当前记为 `null`；每次调用若有 Codex usage 则保存在 `execution.json`。墙钟时间有实际数值。

## 独立性与仓库保护

每个阶段使用新的 `codex exec`，不 resume，不保留 session。调用忽略用户执行配置和规则文件，禁用 Web 搜索、多 agent 和 repository/user instruction 文档加载。任务、当前 spec 与当前 repair 所需 audit 通过明确的 JSON 字段提供；metadata 和旧 audit 不进入 auditor prompt。

采用 Codex read-only sandbox，并在调用前后检查 HEAD、Git 状态与文件内容哈希；包括忽略文件在内的新增/删除/修改均视为违规。历史结果放在 checkout 外。隔离边界不等同于容器或严格的文件读取白名单：禁止读取父目录、历史 run 和外部来源也通过 protocol prompt 约束。

缓存以 URL 哈希和固定 SHA 区分，每次只 fetch 指定 commit。已污染的缓存不会自动 reset 或删除，下一次运行会返回 `REPOSITORY_ERROR`。排查后可选择新的 `--workspace-dir`。同一 checkout 的锁覆盖整个 run；异常退出后残留锁需要确认原进程已结束再手动处理。M1 不支持 submodule 仓库。

只读检查发现改动时，`PROTOCOL_VIOLATION` 优先于 agent/解析错误；差异保存在对应阶段的 `repository-after.json`。普通 agent 错误为 `AGENT_ERROR`，超时为 `TIMEOUT`，无效输出为 `PARSE_ERROR`。这些都不是正常未收敛。

## 验证与开发

```bash
uv run pytest -q
uv run ruff check mracbench tests
uv run ruff format --check mracbench tests
```

常规测试使用本地临时 Git 仓库、stub adapter 和临时 CLI 程序；不联网、不调用真实模型。测试覆盖输入校验、audit 解析、收敛状态机、runner 错误和不可变产物、仓库污染，以及真实子进程的输出分流、非零退出和超时终止。

代码入口与职责：

- `cases.py` / `protocol.py`：加载、校验与 prompt 构造。
- `repository.py`：固定 commit checkout、缓存锁和改动检测。
- `codex_exec.py`：Codex 进程调用；不理解 MRAC 阶段。
- `audit.py`：audit JSON 与 spec 外层格式校验、收敛状态。
- `runner.py`：单 case 状态流转；仅依赖 adapter 接口。
- `runs.py`：输入快照、阶段状态、日志和最终结果。

M1 不判断最终 spec 的绝对正确性。Spec 解析只检查文档外层格式；内容正确性由独立 audit 协议测量。suite/repeat、稳定发布 CLI、完整 resume 和其他 agent 实现尚未加入。

设计依据：[Milestone 1 Spec](doc/MRAC%20Bench%20Spec%20Track%20v0.1%20Milestone%201%20Spec.md) · [Milestone 1 Plan](doc/MRAC%20Bench%20Spec%20Track%20v0.1%20Milestone%201%20Plan.md)。验收证据见 [实施与验收记录](doc/Milestone%201%20Implementation%20Report.md)。Codex 集成参考：[官方非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode)。
