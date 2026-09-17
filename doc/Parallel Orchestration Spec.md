# MRAC Bench — 多任务并行编排 Spec

## 文档信息

- 状态：第一版实施规范；代码与自动验证已落地，真实模型写入环境的验收限制见[实施记录](./Parallel%20Orchestration%20Implementation%20Report.md)。
- 日期：2026-09-17。
- 对应计划：[Parallel Orchestration Plan](./Parallel%20Orchestration%20Plan.md)。
- 当前协议依据：[Protocols](./Protocols.md)。早期 Milestone 1 文档仅作历史参考。
- MUST 表示必须，SHOULD 表示建议，MAY 表示可选。本文描述目标行为，不表示现有 CLI 已提供这些能力。

## 1. 目标、范围与不变量

建立独立于 Codex 对话、独立于单次 runner 协议状态机的持久化编排机制。用户注册 case 后，通过编号或名称提交批次；服务按固定计划并行执行完整 run，支持重启对账、显式续跑、取消及集中查看证据。

第一版支持单机、单个活动调度器、多个独立执行进程，首先验收 Windows 10/11；Python 3.11+。跨机器调度、多个活动调度器、Web UI、阶段级分布式执行、自动模型排名、动态 DAG 和任意外部命令执行不在范围内。其他平台不得未经测试即宣称具有相同的进程树清理保证。

必须保持以下不变量：

| ID | 不变量 |
|---|---|
| INV-01 | 编排核心不导入 runner 或协议实现，不读写协议私有 checkpoint，不决定 audit/fix/收敛语义。 |
| INV-02 | 一项任务调度完整 run；单个 run 内部的阶段由 runner 串行管理。 |
| INV-03 | 全程只读的 run 可共享相同固定基线；包含写阶段的 run 始终独占自己的 checkout。 |
| INV-04 | 同一 run 同时最多有一个执行者；失联不等于停止，未证实原进程树停止不得启动继任者。 |
| INV-05 | 提交成功的批次固定任务、输入、协议和执行条件；恢复不重新解析默认值或当前 case 名称。 |
| INV-06 | 故障恢复不增加协议预算；用户续跑与重新实验是明确、可追溯的独立操作。 |
| INV-07 | 批次证据与单次证据分别拥有写入者；历史失败、旧 attempt、旧 run 不被成功重试覆盖。 |
| INV-08 | 编号不回收，case 版本不覆盖；repo 清理不删除 case 或证据。 |

## 2. 组件与依赖边界

```text
CLI / Codex / 其他客户端
           ↓
编排服务：计划、任务状态、额度、事件、依赖
           ↓
Backend 接口 → MRAC 接入器 → Worker/Supervisor → runner 子进程
                                                ↓
                                         协议 → AgentAdapter

共享资源模块：case 注册与解析、repo 分配与租约
公共执行契约：版本化请求、身份、状态、能力与结果
```

- 编排核心 MUST 可用假 backend 独立测试；只有 MRAC 接入器理解 MRAC 结果和 CLI。
- Worker 负责进程身份、启动回执、日志、心跳和进程树收尾，不解释模型输出。
- runner 只接收本 run 的固定输入、输出目录、repo 描述和执行操作，不依赖批次 ID、调度数据库或服务存活。
- case/repo 模块 MUST 可被独立单次 CLI 使用，不依赖调度器。资源描述为普通数据，资源访问仍需真实锁验证，不能只相信路径或令牌字符串。
- 编排、资源模块及 runner 可在同一仓库、同一发行包中交付，但 MUST 有独立包边界和入口；编排服务不直接调用 `run_case()`。
- Codex 退出不停止已提交任务；服务明确启动后持续工作。提交不偷偷依赖 Codex 的工具调用生命周期。

## 3. 管理根目录与所有权

`bench-home` 解析优先级为显式 `--bench-home` → `MRACBENCH_HOME` → `~/.mracbench`，转换为绝对路径。不得因当前工作目录变化而切换注册表。第一版控制数据库位于本机磁盘；不支持网络共享盘上的多主机数据库访问。

```text
<bench-home>/
  cases/
    registry.sqlite
    packages/C000001/v1/package/          # 原始 case 包
    packages/C000001/v1/manifest.json     # 包内容清单，不混入原始文件
    .staging/                            # 尚未发布的注册事务
  repos/
    shared/<repo-key>/<commit>/<checkout-key>/<generation>/
    writable/<batch-id>/<run-id>/
    writable/standalone/<run-id>/         # 受管的独立单次运行
    .control/                            # 资源目录、锁、租约、清理记录
  batches/<batch-id>/
    orchestration/
      request.yaml
      resolved-plan.json
      inputs/                            # 批次固定输入包，按摘要去重
      events.jsonl
      attempts.json
      workers/<attempt-id>/              # 请求、启动回执、stdout/stderr、结束回执
      operations/                        # 人工输入、续跑、重新实验、清理关联记录
      summary.json
      report.md
      manifest.json
    runs/<run-id>/                       # runner 原有证据结构，原样保留
  runs/<run-id>/                         # 受管的独立单次证据
  control/
    scheduler.sqlite
    service.lock
    logs/
```

- 所有受管 repo MUST 在 `repos/` 内。repo 与 run 证据不得互相嵌套；repo 根目录不得包含 case 或证据根目录。
- 批次编排证据与本批次 run 证据是同一批次目录下的兄弟目录；报告使用相对链接，复制整个批次目录后仍能查看。
- 路径片段使用系统 ID，不直接拼接用户提供的名称。创建、打开、清理时验证绝对路径和链接边界；拒绝越界 symlink/junction。
- `orchestration/` 由编排服务及其 Worker 写入；`runs/<run-id>/` 由该 runner 写入。编排报告引用 runner 文件及 hash，不改写 runner 结果。
- 自动检查点不构成跨目录事务，具体提交与恢复规则见第 5、8、10 节。

## 4. Case 注册、选择与版本

### 4.1 身份

每个管理根目录拥有一个注册表，case 记录至少包含：`number`（正整数）、`case_key`（如 `C000001`）、`name`、`aliases`、`source_id`、`default_version`、`active/archived`、创建时间。`case_key` 使用 C 加至少六位十进制数字，不限制编号增长。

版本记录包含：整数 `version`、受管 package 相对路径、全部包文件的 SHA-256 清单、清单摘要、原始 `case.yaml`、固定仓库信息和注册时间。

- 编号由事务分配，只保证唯一递增，不要求连续；注销/归档不回收编号。
- `C000001`、`1`、名称及别名都可选择同一 case；纯数字和 `C[0-9]+` 保留给编号，不允许作为名称或别名。
- 名称可含中文及空格，按 Unicode NFC 与 casefold 后检验唯一；禁止首尾空白、空名、控制字符、路径分隔符及 `@`。展示保留用户拼写，带空格时通过 CLI 引号传入。
- 改名原子更新；旧名变为永久保留别名，不能转给其他 case。名称和别名共享唯一命名空间。
- `source_id` 保留原 case.yaml 的 id，不改写原文、不用编号替换它。注册后 package 目录名无需等于 source_id；loader 应显式接收 package root 与 source_id。旧项目目录式加载仍保留原校验。

### 4.2 注册与发布

注册 MUST 校验当前 case schema、完整 commit、任务与 related Specs 哈希、路径边界，并按字节复制整个普通文件包（不含 `.git`；拒绝链接和特殊文件）。包内说明文件也进入内容清单。注册不调用模型；仓库拉取验证在运行准备时完成，不能把“注册成功”显示为“仓库已可用”。

以注册写锁保护编号、名称和版本发布：先提交不可见的 PENDING 操作并保留编号/名称 → 复制到同卷 staging 并验证 → 原子发布版本目录 → 事务发布 READY 版本与默认版本。PENDING 不参与选择；其编号不得分配给另一操作。可见版本必须有完整文件；崩溃留下的未索引目录依据操作对账，不能当成成功注册。不能以覆盖现有目录的方式重试。内容清单只覆盖 package 内原始文件，生成的 manifest 位于包外，不参与自身摘要。

- 初次注册创建新编号；添加版本必须显式指定已有 case，并保持 source_id 一致。
- 新版本号取包中 `case.version`，必须高于已注册最高版本；成功后成为默认版本。
- 已有版本、相同清单摘要视为幂等成功；相同版本、不同内容拒绝。即使只修改说明文件，也需新版本。
- 注册操作支持 `request_id`，相同 ID/相同请求返回原结果，不同请求拒绝；首次注册未知响应的重试不得分配第二个 case。
- 归档后不能提交新任务，包括显式指定旧版本；已冻结批次与历史恢复不受影响。可显式取消归档。
- 第一版不提供 case 物理删除，避免破坏永久身份与版本追溯。

### 4.3 使用

不指定版本时选提交当时的 `default_version`；指定版本必须精确存在。任何名称、版本或 hash 错误都拒绝，不回退到另一版本或本地同名目录。批次及受管单次执行共享同一解析器。

示意 CLI：

```text
mracbench case register <目录> --name requests-1963 --request-id <id>
mracbench case register <新版本目录> --case C000001 --request-id <id>
mracbench case list
mracbench case show 1
mracbench case rename C000001 --name requests-redirect
mracbench case archive C000001
mracbench case unarchive C000001
mracbench run --managed --case C000001 --case-version 1 ...
mracbench run --managed --case requests-redirect ...
```

`--managed` 明确选择注册表模式；旧 `run --case <目录 id>` 保持原语义，避免存在同名注册项时静默改变输入。批次始终使用注册表，不需要此开关。

## 5. 批次提交与固定计划

### 5.1 输入与展开

第一版支持 case × 模型配置 × 协议 × repeat 的笛卡尔积，以及组间静态依赖。模型配置将 model、reasoning effort、资源分组绑定为一项，避免产生无效组合。组 ID 在批次内唯一，重复的 case 选择器解析到相同 case/version 时拒绝；重复实验使用 `repeat`。

```yaml
schema_version: 1
name: spec-comparison
concurrency:
  total: 4
  groups:
    codex-main: 2
retry:
  max_auto_recoveries: 1
groups:
  - id: baseline
    cases:
      - selector: C000001
        version: 1
    model_configs:
      - model: "<available-model>"
        reasoning_effort: high
        resource_group: codex-main
    protocols: [spec-mrac-v2]
    repeat: 3
    timeout_seconds: 1800
    max_audit_rounds: 8
  - id: comparison
    depends_on:
      - group: baseline
        when: completed
    cases:
      - selector: requests-1963
    model_configs:
      - model: "<available-model>"
        reasoning_effort: high
        resource_group: codex-main
    protocols: [spec-mrac-v1]
    repeat: 1
```

上例为配置形状示意，模型占位符需替换。每组 MUST 显式选择协议及模型；不能把 CLI/账户的可变默认模型写成已固定模型。可不指定 reasoning effort，但必须保存 null，注明采用已固定 CLI/模型的默认行为，不能声称服务端模型绝对可复现。

- 组可提供 `execution_spec_file`；仅当该组所有协议均要求显式执行 Spec 时允许，按原字节固定。混合不兼容协议应拆组。
- 参数优先级、预算合法性由 MRAC 接入器调用 runner 公共校验入口确定，不在编排核心复制协议判断。
- `repeat` 是独立实验，不是失败重试；展开序号固定并进入 task 身份。
- 依赖只支持 `completed`（上游全部正常结束且证据有效）或 `converged`（再要求全部 outcome=CONVERGED）。上游 PAUSED/WAITING_INPUT 使下游保持等待；上游不可满足条件的终态使下游 SKIPPED。
- 第一版依赖仅控制启动顺序，不传递动态 Spec/代码产物。以本批次产物继续执行应在产物固定后提交新批次，不隐式读取“latest”。
- 验证无环、引用存在、任务数非零、所有额度为正整数。配置未知字段或未声明的资源组均拒绝。

### 5.2 提交事务

`batch submit` 必须支持客户端生成的 `request_id`。相同 ID/相同请求返回同一 batch；相同 ID/不同请求拒绝，不能产生重复实验。先查询幂等记录再解析当前默认版本；成功提交后的重试不因名称、默认版本或源文件变化而重新生成计划。

成功返回前 MUST 完成：解析所有 case/version → 复制并验证 case、protocol/prompts、执行 Spec → 固定有效配置与 runner/agent 版本及可执行环境身份 → 展开 task → 写入不可变 resolved-plan → 将 batch、task 和提交事件事务性写入 scheduler DB。

输入 staging 与目录发布先于数据库可调度提交；中途失败不允许部分任务开始。重启发现未登记目录时依据 request_id/hash 对账，不能自动发起第二个批次。冻结后启动只读固定输入包；源目录、注册默认版本、当前 protocol 文件改变不得影响排队任务。

运行环境身份至少包含 runner 包版本与代码内容摘要、Python/AgentAdapter/CLI 版本及解释器/可执行路径。源码 checkout 可变时记录执行代码清单摘要并在启动/恢复前核验；变化则拒绝，不自动使用新代码。冻结可验证条件，不承诺固定外部模型服务实现。

## 6. Repo 复用、隔离与清理

### 6.1 分类与共享身份

由 runner capability 声明整个 run 的 `repository_access=read_only|writable`。接入器据此请求资源；exec 中只读 AUDIT 仍访问该 run 独占的候选 repo，不共享其他 exec 的可写候选。

共享键由规范化仓库来源、完整 commit、检出配置摘要构成。来源只做有明确等价性的规范化，不随意合并不同 URL；配置包含换行/attributes、稀疏检出、LFS/submodule 策略等影响内容的选项。第一版保持现有 submodule 拒绝行为，不新增 LFS 功能。ready manifest 保存实际文件基线和 Git 控制信息。

每次创建使用新 generation；`PREPARING → READY` 前独占准备锁，完整 checkout 与校验成功后才发布。其他请求等待 READY 或得到明确准备错误，不能接触半成品。健康 generation 跨 run、跨批次共享。

### 6.2 租约与污染

- 资源模块 MUST 实现跨进程的共享读锁/排他维护锁，可用同一资源目录下由平台提供的可靠锁实现；SQLite 心跳或内存引用计数不能替代资源锁。
- 实际执行者在读取前持有共享租约；准备、删除、重建需要排他锁。所有访问入口包括 managed 单次运行遵守同一规则。
- 心跳只用于发现异常。监督进程、runner 或子进程状态不明时，资源保持占用/隔离，不能仅按 TTL 释放。
- run 的前后完整性检查沿用原协议；Git 只读检查应禁用可避免的 optional locks，禁止共享区写缓存、生成物和日志。只读是执行能力与检查契约，不宣称文件哈希能归因于具体违规者。
- 检测污染时将 generation 标记 `QUARANTINED`，拒绝新租约，停止相关在途任务并归档差异。根据最后一次通过检查的时间与租约时间段，保守标记所有可能受影响的 run；不能只处罚首先发现污染的 run。
- 已有 runner 结果不改写；编排添加 `evidence_validity=INVALID` 及关联事件，报告将其从有效收敛计数中排除。第一版不自动释放依赖该结果的新任务；已启动的依赖后代也标记 INVALID 并停止在途执行。
- 不自动 reset 污染目录。仅在所有旧执行者确认停止、证据归档后，显式修复操作可创建新 generation；旧 generation 保留到清理。

### 6.3 可写 repo

可写 repo 按 run 独立分配，固定基线与 Git 控制状态继续由 runner 保护。恢复和显式继续使用原目录；重新实验创建新 run 和新目录。第一版可使用独立 clone，不强制引入共享 Git 对象库或 worktree。

中断写阶段可能留下未验证候选。只有 runner 公共恢复校验确认检查点和工作区一致时才恢复；不能由编排层自行 reset、丢弃变更或把文件内容推断为已完成阶段。

### 6.4 清理

`repo clean` 默认输出候选计划；`--apply` 执行。计划绑定资源 ID、generation、目录与保留状态，执行前必须重新取得排他锁并核验，不能信任过期列表。

- 共享 READY repo 无活动/不明租约即可清理；暂停 run 可按固定基线重建并校验。污染 repo 还需完成影响取证。
- 可写 repo 有活动进程、状态不明、PAUSED、WAITING_INPUT 或恢复保留标记时不得清理。
- 正常完成或明确放弃恢复的 repo，在终止记录、run 证据和批次清单（独立单次使用单次封存清单）验证通过后可清理。FAILED/CANCELLED 默认保留；使用 `workspace release` 显式放弃恢复能力后才成为候选。
- 清理只处理资源目录登记且位于 `repos/` 下的精确目录；未知目录报告、不自动递归删除。不得跟随目录链接。
- 记录删除前清单、操作者请求、结果和是否可再恢复；全局记录写入资源目录，批次关联记录进入批次事件。跨库通过唯一 operation_id 和可重放对账发布，不能依赖跨库原子事务。
- repo 删除不删除单次 patch、输入、日志或 case。完整运行现场不保证能仅凭 patch 恢复。

## 7. 身份、状态与并发额度

### 7.1 身份

| 身份 | 含义 |
|---|---|
| batch_id | 一次固定任务计划；请求幂等，不由显示名称决定。 |
| task_id | 展开的逻辑实验单元，含 case/version、模型配置、协议与 repeat 序号。 |
| run_id | 一次实验历史；恢复/继续不改变，重新实验改变。 |
| attempt_id | 一次启动或继续 runner 的执行尝试，必须在 spawn 前持久化。 |
| operation_id | 续跑、回答、取消、重新实验等操作的幂等标识。 |

attempt 保存递增 ownership epoch、请求摘要、run 路径、进程身份和时间。调度器换届采用独立 service epoch；接管已有 attempt 不使仍正常运行的结果失效。更换 attempt 才废止旧结果提交权。PID 必须与主机/启动周期、进程创建时间及监督句柄联合核验，不能仅用 `pid_alive`。

### 7.2 状态

任务状态：`QUEUED / STARTING / RUNNING / RECOVERING / RETRY_WAIT / PAUSED / WAITING_INPUT / COMPLETED / FAILED / CANCEL_REQUESTED / CANCELLED / SKIPPED`。

- 新任务 QUEUED；资源就绪且原子领取所有额度后 STARTING；确认 runner 启动回执后 RUNNING。
- Worker 无法证明进程和结果状态时 RECOVERING。该状态不是完成，不允许自动忽略。
- PAUSED/WAITING_INPUT 释放执行额度，但保留 run 与必要 repo 恢复引用；显式操作校验通过后重新排队。
- COMPLETED 表示协议正常结束，`outcome` 单独记录 CONVERGED 或 NON_CONVERGED。证据有效性单列，不能用退出码 1 判断为可重试错误。
- BLOCKED、输入/协议校验失败、污染、无法恢复等映射为 FAILED 并保留原 runner 状态及原因；PAUSED 不是 FAILED。
- FAILED/CANCELLED/COMPLETED/SKIPPED 对本次 run 生命周期不再自动重开。FAILED 如被 runner 判定仍可恢复，可由用户显式 recover 保留历史失败记录后恢复同一 run；CANCELLED 仅能 rerun。显式重新实验保存旧 run 并建立 successor run；已有下游 SKIPPED 或已执行结果不自动改写，需提交新批次重算依赖。
- WAITING_INPUT 和 PAUSED 的显式继续沿用同一 run；不提供把自然语言回答自动发送给其他任务的机制。

批次状态按全部 task 投影：有可执行/在途任务为 RUNNING；仅等待人工或不明资源为 WAITING；全部终态且至少一项失败/跳过/证据无效为 FINISHED_WITH_ERRORS；全部正常完成为 FINISHED；整批取消完成为 CANCELLED。未收敛数量单列，不自动等同基础设施失败。

### 7.3 额度与公平性

服务配置提供全局及资源组上限，批次配置可再收紧，不能通过多个批次绕过账户组上限。资源组是显式逻辑名称，不包含凭证；凭证不进入证据包。

STARTING、RUNNING、CANCEL_REQUESTED 及尚不能证明停止的 RECOVERING 占用额度。完成进程树收尾后方可释放。静态依赖等待、PAUSED、WAITING_INPUT、RETRY_WAIT 不占执行额度。

所有所需额度在同一 scheduler 事务中领取，不能部分持有并死锁；repo 未就绪则不 spawn。repo 租约取得后重新核验任务所有权和取消状态。第一版按批次轮转、批次内提交序号选择就绪任务；不抢占运行中任务。一个任务失败默认不取消其他独立任务。

## 8. 公共 runner 契约与可靠启动

### 8.1 版本化进程接口

新增 `mracbench machine` 入口，提供 `capabilities / validate / start / inspect / resume / abort`。执行通过参数数组传递 UTF-8 JSON 请求文件，不使用 shell 拼接，不解析人类日志。请求的 `operation=start|recover|continue|answer` 明确区分执行意图；start/resume 命令共用该执行入口。abort 在监督确认停止后，以 run-dir、operation-id、expected-revision 追加基础设施终止状态。

| 对象 | 必需字段/行为 |
|---|---|
| ExecutionRequest | schema_version=1、operation_id、operation、request_sha256、run_spec_sha256、run_id、attempt_id、固定输入包路径/hash、绝对 run_dir、repo 描述、有效执行配置；恢复/继续/回答另带 expected_revision。 |
| Receipt | schema_version、operation_id、run_id、attempt_id、request_sha256、started、进程身份、started_at、error；以原子文件发布。 |
| PublicStatus | schema_version、run_id、revision、lifecycle、runner_status、outcome、error、checkpoint_valid、allowed_actions、输入身份、产物相对路径/hash。 |
| Capability | contract_version、支持的协议版本、repository_access、可用操作与限制；状态级 allowed_actions 必须由 inspect 实时校验产生。 |

capabilities/validate/inspect 为只读调用，使用各自最小请求：capabilities 只需版本，validate 使用固定输入包/hash 和配置，inspect 使用 run_dir/run_id；这些查询不要求尚不存在的 attempt_id。validate 使用冻结输入而非工作目录默认文件。未知主版本拒绝，不能猜测私有状态。PublicStatus 位于 runner 自己拥有的公开文件中；它是协议状态的可重建投影，不能替代协议的权威 checkpoint。runner 内部可以理解旧状态，接入器只消费公共契约。

`request_sha256` 是请求去掉该字段后的规范 JSON 摘要（键排序、UTF-8、无额外空白）；`run_spec_sha256` 只覆盖本 run 的不可变输入与初始配置，不含 operation/attempt/进程身份。`start` 接收预分配 run_id/run_dir，原子登记初始化摘要；重复 operation_id/同请求返回已有状态，不新建模型调用；同 operation_id/不同请求或同 run_id/不同 run_spec_sha256 均拒绝。

确认未执行任何阶段的失败初始化，可由新 attempt 在原 run_id 下完成幂等 start；已有阶段记录的 run 必须走 resume，并验证前执行者已停止、run 锁和修订。部分初始化目录通过初始化日志修复或明确失败，不能删除后当新 run 重跑。未显式提供身份时，旧单次 CLI 仍可生成 ID。

读取结果时验证 schema、run/attempt 身份、输入摘要、checkpoint 有效性、产物存在及 hash。进程退出码只是传输诊断；没有有效完成记录时不能算 COMPLETED。暂停后的旧 result.json 不能冒充后续 attempt 的新完成结果，必须使用 revision/attempt 关联。

### 8.2 启动、监督与重启对账

1. 调度器事务创建 attempt、分配 run 身份、占用额度并写启动意图。
2. 启动独立 Supervisor；它对 attempt 持有 OS 排他锁，先写自身身份和回执，再创建受监督 runner。
3. Windows 必须在 runner 获准执行前把它纳入启用 KILL_ON_JOB_CLOSE 的 Job Object，禁用 breakaway 并避免句柄继承；Supervisor 崩溃时终止整个作业。验收覆盖实际 runner/Codex 的子进程创建路径；不声称 Job 能控制通过外部服务另行创建的进程。若执行器要求脱离监督的启动方式，第一版拒绝该能力，不能静默退化为只杀父 PID。机制依据 [Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。
4. runner 原子登记启动身份后才允许调用 agent；Supervisor 持久化 started/finished 回执与输出，不依赖调度器连接持续存在。
5. runner 退出后，Supervisor 确认全部子进程已结束才提交完成回执。调度器核验当前 attempt 后事务性接受结果、释放额度并写事件。

Job 关联必须随 runner 创建完成，不能留下“创建成功但尚未入 Job，Supervisor 已死”的孤儿窗口。Windows 10/11 可使用 [PROC_THREAD_ATTRIBUTE_JOB_LIST](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)；若实现选择其他机制，必须提供同等崩溃窗口验收证据。

重启先取得唯一 service OS 锁，再对账所有在途 attempt：有效完成记录则收取；监督进程仍活则重新监控；监督进程已死则确认作业与子进程退出，调用 inspect；身份或存活无法确认则保留 RECOVERING，展示原因和人工处理入口。

“已启动但调度器未记到”依靠 attempt 目录、启动意图、Supervisor 锁和 runner 幂等登记消解。不能只看数据库心跳或创建时间推测进程未启动。旧 attempt 的迟到结果只归档，不覆盖当前状态。

单次调用可能因崩溃而发生可识别的重复计算；不承诺模型调用 exactly-once。保证单 run 单执行者、一次逻辑结果接受和完整 attempt 历史。

## 9. 恢复、续跑、重新实验与取消

| 操作 | run 身份 | 预算/输入 | 默认触发 |
|---|---|---|---|
| recover | 沿用 | 不增加预算、不更换输入；按协议处理未完成调用与 clean | 有限的已分类基础设施故障 |
| continue | 沿用 | 按当前协议批准后续批次或解除暂停；精确记录预算变化 | 用户显式命令 |
| answer | 沿用 | 固定回答文件，允许的阶段由 runner 校验 | 用户显式命令 |
| rerun | 新 run、可写新 repo | 使用批次原固定输入及初始预算；不继承 clean | 用户显式命令 |
| cancel | 沿用 | 停止执行，保留证据 | 用户显式命令 |

每种修改操作要求 operation_id 和 expected_revision；重复操作先返回幂等结果，新操作才检查修订，过期修订明确冲突。批量操作记录每项结果，可用同 operation_id 重试未完成项，不能因半途崩溃重复扩预算。显式 recover 仍受 runner 的恢复校验和原预算约束，不受自动次数上限限制，但每次操作都计入证据；不能借此恢复未知存活状态的原执行者。

第一版默认最多一次自动恢复；上限为非负整数，0 表示禁用。退避 5 秒，第二次及以上若显式提高上限则指数退避、上限 60 秒。计数属于逻辑 task，服务重启不清零。允许的故障为明确的启动前基础设施失败和监督中断后 runner 确认可恢复的检查点；不从 stderr 自由文本猜测“限流”。TIMEOUT、AGENT_ERROR、PARSE_ERROR、协议违规、输入错误、NON_CONVERGED、PAUSED、NEEDS_INPUT 不自动重试。

恢复能力按协议公开：

| 协议 | 启动/检查 | 中断恢复 | 显式继续 |
|---|---|---|---|
| spec-mrac-v2 当前版本 | 支持 | 仅可校验检查点，按协议废弃未完成审计 | PAUSED/NEEDS_INPUT 按当前语义 |
| exec-mrac-v1 | 支持 | 必须新增不扩预算的恢复路径；未验证写入不得自动接受 | PAUSED 用明确批准增加六次 audit；回答不额外增预算 |
| spec-flow-simple-v1 | 支持 | 第一版不新增任意中断恢复 | 仅原有 PAUSED 且预算剩余 |
| spec-mrac-v1 | 支持 | 不支持 | 不支持 |

exec recover 若原预算已耗尽，只保存/返回 PAUSED，不隐式增加六次。协议不能恢复时任务明确 FAILED/RECOVERY_UNSUPPORTED；不自动换新 run 冒充恢复。历史只读格式继续只读，不能通过编排入口强制迁移。

取消先持久化 CANCEL_REQUESTED、阻止继续派发，再请求 Supervisor 收尾；第一版最长宽限 10 秒，之后终止受管进程树。确认树为空并保存结果后才 CANCELLED/释放资源。未启动任务直接取消，PAUSED/WAITING_INPUT 取消不启动模型。若有效完成在取消事务之前已被接受，返回已经完成；否则保留晚到原始结果但以取消为操作终态。

外部强制终止导致无合法协议终态时，由接入器记录基础设施取消事实，不伪造 CONVERGED 或修改已有证据。runner 可通过公共 abort 在取得 run 锁后追加终止记录；旧协议不支持时仍保留其原始状态。

## 10. 持久化、证据与报告

scheduler DB 是编排状态权威，使用事务保存状态变化与同一条事件 outbox。运行在本地 SQLite WAL，启用 foreign keys、busy timeout 和 FULL 同步；服务单写为主，读命令不持有长事务。WAL 的同机访问和多库事务限制依据 [SQLite WAL 文档](https://www.sqlite.org/wal.html)。磁盘失败应停止新派发、保留在途 Supervisor 记录，不报告操作成功。

最少实体：batches、tasks、dependencies、attempts、operations、resource_claims、events、export_cursors。run 与 task/attempt 的历史映射不能只保存“最后一次”。case 注册表和 repo 资源目录各自权威；跨组件操作以 operation_id 对账，不声称存在跨数据库与文件系统原子提交。

- request.yaml、resolved-plan 和 inputs 一经发布不可覆盖，manifest 保存 hash。
- events 带 batch 内单调序号、唯一 event_id、UTC 时间、对象 ID、operation_id、前后状态和原因；同一事务保证事件与状态一致。
- events.jsonl 是可重建导出：崩溃后按序号检查截断尾部并从 outbox 补齐，不重复、不遗漏；不能把未 fsync 的导出作为权威状态。
- attempts.json、summary.json、report.md 为原子替换的投影，包含导出 revision、事件高水位与生成时间。读取时显示是否尚有未导出事件。
- 活跃批次的 manifest 随投影修订；不可变输入和历史操作独立存 hash。结束时生成一致修订的证据清单，后续续跑/重新实验保留上一修订的报告与清单。
- `batch export` 在调度状态一致快照下重新导出；活跃 run 标注未完成，不能宣称快照包括仍在写入的全部 raw 文件。暂停/终止且 runner 锁已释放时才能生成完整 run 文件清单。
- 报告至少列 task、case 编号/名称/版本、model/protocol、repeat、全部 run/attempt、状态、outcome、证据有效性、失败类型、耗时、repo 去留及证据相对链接。
- 初始 run 成绩与重新实验成绩分开；不得只保留最佳结果。token/cost 缺失用 null，不视为 0；未知/部分 usage 明确标记。
- 原始模型日志保留在 run；批次只保存调度、进程运输日志与引用，不复制全部审计/修复文件。仓库污染等批次判定通过附加证据表示，不重写 runner 的业务判断。

## 11. CLI、兼容与交付边界

新增公共命令族：

```text
mracbench orchestrator serve
mracbench orchestrator status
mracbench orchestrator stop
mracbench batch submit <batch.yaml> --request-id <id>
mracbench batch status <batch-id> [--json]
mracbench batch report <batch-id>
mracbench batch export <batch-id>
mracbench batch recover <batch-id> --task <task-id> --operation-id <id>
mracbench batch continue <batch-id> --task <task-id> --operation-id <id>
mracbench batch answer <batch-id> --task <task-id> --input-file <path> --operation-id <id>
mracbench batch rerun <batch-id> --task <task-id> --operation-id <id>
mracbench batch cancel <batch-id> [--task <task-id>] --operation-id <id>
mracbench repo list
mracbench repo clean [--batch <batch-id>] [--apply]
mracbench workspace release --run-dir <path> --operation-id <id>
```

所有命令支持统一 bench-home；修改命令可由 CLI 读取当前 revision 并附上请求，冲突时报告，不盲目刷新重试。`--json` 提供稳定 schema，显示型文本不作为自动化接口。

`serve` 是可独立启动的长期进程；第一版不强制安装系统服务。提交在服务停止时仍可持久化排队，状态明确显示 scheduler offline；不会自动执行。`stop` 停止新派发后退出调度器，存活 Supervisor 继续执行并保存结果，下次启动先对账；取消所有任务必须另用 cancel。

旧单次命令、既有 run 目录和四个协议的业务语义保持兼容；不自动导入历史 runs、移动旧 .workspaces、注册本地 case 或删除旧锁。提供显式 case 注册路径。新受管单次运行采用公共执行/资源接口和集中目录，但不要求调度服务运行。

受管 run 的公开所有权记录区分 standalone 与 batch。旧 resume/abort 指向 batch 所有的 run 时必须拒绝修改并提示对应 batch 操作，不能绕过 task 状态/额度启动；只读查看仍允许。standalone 的新执行与恢复使用相同 Supervisor 和资源锁能力，由 CLI 启动而不依赖调度服务；其 runner 核心仍不导入编排器。

若新增公共恢复路径需要变更检查点 schema，必须版本化、提供明确读取能力和拒绝策略；不得为了支持编排静默改变 clean、审计计数或六轮预算。协议语义确需改变时另行升级协议并更新 Protocols.md。

## 12. 验收标准

| ID | 场景与必须观察到的结果 |
|---|---|
| AC-01 | 编排核心使用 stub backend 完成提交/调度/报告；静态依赖检查证明不导入 runner/协议模块。 |
| AC-02 | 按 C000001、1、名称、旧别名选择相同 case；并发注册无重号，归档不回收，版本不可覆盖。 |
| AC-03 | 注册任意提交窗口崩溃后，可见版本全部可读、无重复编号归属；重复 request_id 幂等。 |
| AC-04 | 提交后修改原 case/protocol/Spec、默认版本和名称，排队任务仍使用固定输入；执行代码变化明确拒绝。 |
| AC-05 | 重复批次提交与发布窗口崩溃不产生重复 task；矩阵展开、依赖和 repeat 序号确定。 |
| AC-06 | 两个批次同基线只读任务同时运行并共享同一 READY generation；不同检出配置不共享；不读取准备半成品。 |
| AC-07 | 两个可写 run 修改同名文件互不影响；暂停恢复沿用原 repo，新实验使用新 repo。 |
| AC-08 | 共享 repo 被污染时停止分配并标记所有可能受影响结果/依赖后代；不自动 reset 或将无效证据计为收敛。 |
| AC-09 | 多批次全局/分组/批次额度不超限；暂停释放执行额度且保留恢复资源，未知进程不被释放。 |
| AC-10 | 在 spawn 前、spawn 后回执前、runner 初始化中、完成后入库前杀调度器，无双执行者，完成结果只接受一次。 |
| AC-11 | 杀 Supervisor 后 runner 与模型孙进程全部终止；PID 复用/权限拒绝不造成误接管或误杀，无法证明停止则 RECOVERING。 |
| AC-12 | 故障 recover 不增预算；exec 预算边界恢复只返回 PAUSED；重复 continue 仅增加一批六次，旧审计计数不丢。 |
| AC-13 | 不可校验的写阶段中断明确失败；不支持恢复的旧协议不自动重跑，TIMEOUT/PARSE_ERROR 等不自动重试。 |
| AC-14 | NON_CONVERGED 正常完成；PAUSED/NEEDS_INPUT 不自动继续；重复/过期 answer、continue、cancel 可预测且不重复调用模型。 |
| AC-15 | 排队、运行、暂停时取消，以及取消/完成竞态均符合第 9 节；未确认进程树停止不得标记 CANCELLED。 |
| AC-16 | outbox 导出崩溃、JSONL 截断、投影损坏可重建；历史失败、全部 attempt 和输入 hash 可追溯，整体复制批次后链接有效。 |
| AC-17 | 清理与新租约竞态不删除在用 repo；暂停可写 repo 受保护，显式放弃恢复才可清理失败目录；case/证据不被删除。 |
| AC-18 | 现有单次测试通过；未启动调度器时 managed 单次仍能使用统一 registry/repo；历史结果保持原样。 |
| AC-19 | 依赖循环/无效组拒绝，上游等待不会跳过，下游不满足终态条件时 SKIPPED；独立任务继续执行。 |
| AC-20 | 同一 batch 内 rerun 保存旧成绩与新成绩，默认不复活旧下游；服务 stop/offline 后提交与恢复行为明确。 |

自动测试使用本地临时 Git 仓库和 stub 进程，不消耗模型配额。真实模型烟测单独记录实际输入、工具版本、run 路径及结果；不得用真实模型偶然成功代替故障注入验收。
