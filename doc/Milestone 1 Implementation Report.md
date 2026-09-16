# Milestone 1 实施与验收记录

日期：2026-09-16。对应 [Spec](./MRAC%20Bench%20Spec%20Track%20v0.1%20Milestone%201%20Spec.md) 与 [Plan](./MRAC%20Bench%20Spec%20Track%20v0.1%20Milestone%201%20Plan.md)。

## 1. 交付结果

Milestone 1 的单 case 闭环已实现并完成真实验收。实现覆盖输入校验、固定 Git commit、Codex generation、独立 audit、repair、连续 clean 收敛、预算终止、仓库违规检查和完整运行记录。

代码提供最小 `AgentAdapter` 接口，runner 不直接启动 Codex 进程。Protocol 独立保存在 `protocols/spec-mrac-v1/`，Case 独立保存在 `cases/psf__requests-1963/`。开发入口为 `uv run python -m mracbench run`。

## 2. 自动化验证

执行结果：

| 检查 | 结果 |
|---|---|
| `uv sync` | 安装成功，依赖锁定在 `uv.lock` |
| `uv run pytest -q` | 55 passed |
| `uv run ruff check mracbench tests` | All checks passed |
| `uv run ruff format --check mracbench tests` | 17 files already formatted |

测试使用本地临时 Git 仓库、stub adapter 和临时 CLI，覆盖输入错误、严格 JSON 解析、non-blocking 处理、clean streak 重置、最后一轮禁止 repair、失败调用计数、历史 artifact 保留、重复 run 隔离、仓库改动检测和 timeout 终止子进程。常规测试不调用真实模型。

## 3. 真实运行

运行命令：

```bash
uv run python -m mracbench run --case psf__requests-1963 --model gpt-5.6-luna --max-rounds 8 --timeout 600
```

| 条件 | 实际值 |
|---|---|
| Case | `psf__requests-1963`, version 1 |
| 来源 | SWE-bench Lite 的 Requests 连续重定向任务 |
| Repository | `https://github.com/psf/requests.git` |
| Commit | `110048f9837f8441ea536804115e80b69f400277` |
| Protocol | `spec-mrac-v1`, version 1 |
| Agent / model | `codex_exec` / `gpt-5.6-luna` |
| CLI | `codex-cli 0.145.0` |
| Python | 3.12.13 |
| OS | Windows |
| 起止时间 | 2026-09-16 02:12:15–02:17:36 UTC |
| 墙钟时间 | 321.172 秒 |
| Run id | `20260916T021215Z-psf__requests-1963-03fa600f` |

实际 trajectory：

| 阶段 | 结果 |
|---|---|
| Generate | 保存 `spec.initial.md` |
| Audit 1 | 1 个 blocking issue |
| Repair 1 | 保存完整 `spec.round-01.md` |
| Audit 2 | clean |
| Audit 3 | clean，与 Audit 2 审计同一 spec |
| 最终 | `CONVERGED @ 3`，audit 3 次，repair 1 次，protocol violation 为 false |

本地验收产物：

- [result.json](../runs/20260916T021215Z-psf__requests-1963-03fa600f/result.json)
- [run.yaml](../runs/20260916T021215Z-psf__requests-1963-03fa600f/run.yaml)
- [最终 spec](../runs/20260916T021215Z-psf__requests-1963-03fa600f/artifacts/spec.round-01.md)
- [verification.json](../runs/20260916T021215Z-psf__requests-1963-03fa600f/verification.json)

`runs/` 已被 Git 忽略，上述链接指向本工作区保留的验收记录。分享验收证据时需要另行附带该 run directory。

## 4. 产物复核

真实 run 的 22 项记录一致性检查全部通过，包括：

- 5 次阶段调用具有不同 PID 和不同 Codex thread id。
- 所有阶段均保存完整 request、stdout、stderr、最终回复、调用元信息及前后仓库检查。
- 3 次 audit 请求只包含 task、repository path 和当前 spec，没有当前或历史 audit 输入。
- Audit 2 与 Audit 3 的完整请求逐字节相同，目标 artifact 哈希相同。
- 所有输入快照哈希与 `run.yaml` 一致；输入 task 与 case package 一致。
- 每轮 artifact SHA-256 与结果 trajectory 一致；两份 spec snapshot 都保留。
- 每次阶段调用前后的仓库检查均无改动；最终 state 与 result 一致。

项目实施前尚无 Git commit，因此本次 run 的 `benchmark_commit` 为 `null`，`benchmark_dirty` 为 true。`verification.json` 另行记录了实际验收实现的 Python 文件、`pyproject.toml` 和 `uv.lock` 的 SHA-256，便于识别本次版本。未为填充元信息自动创建 Git commit。

## 5. 实现边界

- M1 使用 Python 模块命令；完整发布 CLI、suite/repeat 和 resume 未实现。
- 所有输出的外层格式由程序校验，spec 内容质量由审计协议测量。
- 只读 sandbox、输入构造及仓库差异检测共同约束调用；M1 不提供容器级文件读取隔离。
- 每次调用若返回 usage，则保存在 raw 执行记录；聚合 token/cost 保留为 `null`。
- 用于执行的个人 Codex 配置被忽略，model 建议显式传入。可执行命令、版本和运行条件保存在记录中。

运行说明见 [README](../README.md)。
