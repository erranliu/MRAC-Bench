# 多任务编排使用说明

编排服务与 runner 分离。受管进程监督当前支持 Windows 10/11；其他平台明确拒绝受管启动，旧单次 runner 不受此限制。下面的命令也可通过 `uv run python -m mracbench` 调用。

## 注册与选择 case

管理根目录按 `--bench-home` → `MRACBENCH_HOME` → `~/.mracbench` 选择，与当前工作目录无关。各终端应使用同一根目录。

```powershell
uv run mracbench case register cases/psf__requests-1963 --name requests-1963 --request-id register-requests-v1 --bench-home C:\mrac-data
uv run mracbench case list --bench-home C:\mrac-data
uv run mracbench case show C000001 --bench-home C:\mrac-data
uv run mracbench case show 1 --bench-home C:\mrac-data
uv run mracbench case show requests-1963 --bench-home C:\mrac-data
```

实际编号以注册输出为准。名称可含中文和空格，命令行中用引号包裹。注册按原字节复制包，不改写 case.yaml 中的源 id，不启动模型。同一请求重试使用同一个 request-id，新请求使用新值。

```powershell
uv run mracbench case register C:\new-case-package --case C000001 --request-id requests-v2 --bench-home C:\mrac-data
uv run mracbench case rename C000001 --name requests-redirect --bench-home C:\mrac-data
uv run mracbench case archive C000001 --bench-home C:\mrac-data
uv run mracbench case unarchive C000001 --bench-home C:\mrac-data
```

新版本必须在源包中递增 version。版本不可覆盖；旧名称保留为别名；归档不回收编号。源包移动或修改不影响已注册版本和已提交批次。

## 提交与运行

修改 [examples/batch.yaml](../examples/batch.yaml) 中的 case、模型和预算。每组按 case × model_configs × protocols × repeat 展开。resource_group 是限额分组，不是凭证。

```powershell
uv run mracbench batch submit examples/batch.yaml --request-id comparison-001 --project-root . --bench-home C:\mrac-data
uv run mracbench orchestrator serve --total 4 --group codex-main=2 --bench-home C:\mrac-data
```

submit 返回 batch ID，成功返回前固定全部输入、协议、有效配置和执行环境身份。服务离线时任务只排队，不调用模型。serve 是可从普通终端启动的独立长期进程，不依赖 Codex 对话持续在线。服务重启时使用相同额度参数。

服务总额度、服务组额度、批次总额度和批次组额度同时生效。`depends_on: [{group: spec-v2, when: converged}]` 支持静态组依赖；completed 接受正常结束（包括未收敛），converged 要求全部收敛。等待人工时下游继续等待，无法满足的终态使下游 SKIPPED。依赖不自动传递动态生成的 Spec。

exec 组只放 exec 协议，并提供 execution_spec_file；Spec 在提交时复制固定。exec 初始审计额度为六次。修改执行代码或 CLI 版本后，旧批次拒绝静默使用新环境；应保留原环境恢复或提交新批次。

## 查看与操作

```powershell
uv run mracbench batch status <batch-id> --json --bench-home C:\mrac-data
uv run mracbench batch report <batch-id> --bench-home C:\mrac-data
uv run mracbench batch export <batch-id> --bench-home C:\mrac-data
uv run mracbench batch recover <batch-id> --task T000001 --operation-id recover-001 --bench-home C:\mrac-data
uv run mracbench batch continue <batch-id> --task T000001 --operation-id continue-001 --bench-home C:\mrac-data
uv run mracbench batch answer <batch-id> --task T000001 --input-file C:\answers.md --operation-id answer-001 --bench-home C:\mrac-data
uv run mracbench batch rerun <batch-id> --task T000001 --operation-id rerun-001 --bench-home C:\mrac-data
uv run mracbench batch cancel <batch-id> --task T000001 --operation-id cancel-001 --bench-home C:\mrac-data
```

- recover 使用同一 run 的可验证 checkpoint，不扩预算。
- continue 显式解除暂停；exec 的六轮暂停增加下一批六次。
- answer 仅处理 runner 正在等待的问题，回答文件保存为证据。
- rerun 创建新 run/可写 repo，保留旧成绩；不自动重新执行已经跳过或完成的下游。
- cancel 不带 task 时取消整批；确认受管进程树停止后才释放额度，最长宽限 10 秒。

相同修改操作重试使用相同 operation-id；可传 expected-revision 做乐观并发控制。PAUSED/WAITING_INPUT 释放执行额度，保留可写现场。仅明确的基础设施故障允许有限自动恢复；TIMEOUT、AGENT_ERROR、解析错误、协议违规、未收敛与等待输入不会自动重跑。

`orchestrator stop` 停止派发，已有 Supervisor 继续保存结果，下次服务启动先对账。停止服务不等同于取消任务。

## 目录与清理

```text
<bench-home>/
  cases/registry.sqlite
  cases/packages/C000001/v1/package/
  repos/shared/...
  repos/writable/<batch-id>/<run-id>/
  batches/<batch-id>/orchestration/
  batches/<batch-id>/runs/<run-id>/
  control/scheduler.sqlite
```

批次 report.md 通过相对链接进入各 run；resolved-plan 固定清单，events 保存有序事件，attempts 保存尝试历史，repositories 记录资源去留，manifest 保存证据摘要。整个批次目录可复制用于离线查看，但不支持移动目录后原地续跑。

```powershell
uv run mracbench repo list --bench-home C:\mrac-data
uv run mracbench repo clean --bench-home C:\mrac-data
uv run mracbench repo clean --batch <batch-id> --apply --bench-home C:\mrac-data
uv run mracbench workspace release --run-dir C:\mrac-data\batches\<batch-id>\runs\<run-id> --operation-id release-001 --bench-home C:\mrac-data
```

clean 默认只列候选；apply 才删除。锁、活跃或不明进程、暂停/等待、待执行恢复和缺失封存证据都会阻止不适当的清理。FAILED/CANCELLED 默认保留，workspace release 明确放弃恢复能力后才能清理。case 和运行证据不会随 repo 清理删除。按 batch 清理只处理该批次的独占目录。

共享污染会停止复用，并标记可能受影响的实验/依赖后代 INVALID。`repo rebuild <repo-key>` 要求所有使用者已停止，归档污染元数据和 diff，退役旧 generation；下次取用创建新 generation。旧污染目录保留调查，不自动 reset。

## 受管单次与验证

```powershell
uv run mracbench run --managed --case C000001 --case-version 1 --protocol spec-mrac-v2 --model gpt-5.6-luna --bench-home C:\mrac-data
uv run mracbench resume --run-dir C:\mrac-data\runs\<run-id>
uv run python scripts/orchestration_smoke.py --bench-home C:\mrac-smoke --model <model> --reasoning-effort high
```

受管单次共用 registry/repo/Supervisor，不需要调度服务。旧 run --case 仍按项目目录加载；批次拥有的 run 必须经 batch 命令修改。烟测默认两个只读、两个可写 run，可用 protocol/repeat/timeout 参数调整，失败和等待不会自动续批。

自动测试使用本地 Git、stub CLI 与真实 OS 子进程，不调用模型。真实模型的当前限额及实际权限属于环境条件。runner 为写阶段传入 workspace-write，但外部权限策略仍可能拒绝写入；这种情况保留 NEEDS_INPUT 和原始诊断，不放宽沙盒以通过测试。[官方非交互模式说明](https://learn.chatgpt.com/docs/non-interactive-mode)
