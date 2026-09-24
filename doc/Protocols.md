# 并存协议管理

受管批次通过独立编排服务调用公共 machine 接口，协议内审计、修复和收敛仍由 runner 控制。recover 仅处理可验证中断，不增加预算；exec 的 PAUSED 经显式 continue 批准另外六次 audit。answer 保存必要输入，不授予额外预算。旧协议的中断恢复能力不因编排而扩展，spec-flow-simple-v1@4/@5/@6 只支持 PAUSED 续跑，@1/@2/@3 历史运行只读。入口见[编排使用说明](./Parallel%20Orchestration%20Guide.md)。

显式 Responses provider 属于执行条件，随输入快照固定，恢复必须匹配原 provider/模型目录；凭证值不纳入快照。PROVIDER_ERROR 表示本地配置或环境凭证缺失，CLI 未启动时不计 audit invocation。v2/exec 将该错误纳入可校验 checkpoint 的显式恢复范围，预算规则保持不变。详见 [Provider 接入](./Providers.md)。

本文件是 MRAC Bench 协议的索引、行为契约和维护规范。可执行配置位于
`protocols/<id>/protocol.yaml`，提示词与配置一同保存到每次 run 的 `input/`。
修改协议时必须同步这里的登记与差异说明。本文描述现有实现；早期 Milestone 1
Spec、Plan 和验收报告保留为历史设计记录。

## 协议登记

| ID | version | workflow | 输入含义 | 起点 | 收敛含义 |
|---|---:|---|---|---|---|
| `spec-mrac-v2` | 2 | `repository-spec-freeze`（默认） | 待审计的原始 Spec | 原样复制，直接仓库审计 | 同一 Spec 与基线上两次独立空 findings 审计 |
| `spec-mrac-v1` | 1 | `generate-audit-repair` | 原始任务 | 生成 implementation Spec | 同一产物连续两次无 blocking issue |
| `spec-flow-simple-v1` | 6 | `spec-init-freeze` | 待审计的原始 Spec | 原样复制输入 Spec，初始化审计 | 同一 Spec 字节连续两次经裁决的冻结 clean |
| `exec-mrac-v1` | 1 | `exec-mrac` | 显式选定的执行 Spec | 独立可写 checkout 中实施代码 | 同一 Spec、基线和完整产品候选快照连续两轮零问题 |

- [spec-mrac-v2 配置](../protocols/spec-mrac-v2/protocol.yaml)
- [spec-mrac-v1 配置](../protocols/spec-mrac-v1/protocol.yaml)
- [spec-flow-simple-v1 配置](../protocols/spec-flow-simple-v1/protocol.yaml)
- [exec-mrac-v1 配置](../protocols/exec-mrac-v1/protocol.yaml)

Spec 协议使用只读 checkout；exec 协议的实施/修复阶段使用独立可写 checkout，审计只读。
各协议共享输入哈希和证据存储，但不共享审计 schema 或收敛判断。除旧 spec-mrac-v1
外都没有 generate 阶段，不能把普通 issue 文本自动扩写成新的 Spec。

## 选择、版本与复现

case 与 protocol 独立，在 run 中组合。协议选择优先级：`run --protocol <id>` →
运行层默认值 `spec-mrac-v2`（代码中的 `DEFAULT_PROTOCOL_ID`）。CLI 和 Python API 使用
同一个默认值。显式传入无效协议会报错，不回退默认值。

case schema 不包含协议选择字段，制作模板和内置 case 均不声明 `protocol`。
旧 case 或历史输入快照若保留 `protocol` 字段，loader 将其视为不参与执行的历史数据，
不读取它的 id、不校验其内容、不用它选择协议；无需先改写旧 case 才能运行。

`run.yaml` 记录实际 `protocol_id`、`protocol_version`、选择来源
`protocol_selection`（`default` / `explicit`）及 `requested_config.protocol_id`
（未指定时为 null），不再记录 `case_default_protocol`。运行选择不修改 case。

```powershell
# 同一 case 使用运行层默认协议 spec-mrac-v2
uv run python -m mracbench run --case <case-id> --model gpt-5.6-luna --reasoning-effort high

# 显式选择 flow-simple 旧协议；12 是总 audit 预算，包含初始化审计
uv run python -m mracbench run --case <case-id> --protocol spec-flow-simple-v1 --model gpt-5.6-luna --reasoning-effort high --max-rounds 12

# 按保存的协议恢复，不重新选择默认值
uv run python -m mracbench resume --run-dir C:\mrac-runs\<run-id>
```

协议 ID 标识一套实验语义。新增不同测量对象、阶段图或审计边界时建立独立 ID。
同一 ID 下修改提示词、schema、裁决或结果含义时递增整数 `version`；仅修复不影响行为的
排版、链接不需要升级。ID 里的 `v1` / `v2` 是名称的一部分，实际修订版本以 YAML `version` 为准。
每次 run 固定实际配置、提示词原文及 SHA-256；恢复使用 run 内快照，不重读当前项目的
case 或协议文件，也不重新选择默认协议。已经开始或结束的实验不因编辑项目协议或
变更默认值而改变。旧 run 中的 `case_default_protocol` 仅保留为当时的记录，不作恢复依据。

新增协议必须登记：目的、来源及固定版本、输入契约、阶段、模型角色、输出 schema、
收敛/停止条件、预算与恢复、证据格式、兼容性和验证用例。当前只支持登记的四种明确的
workflow，不支持在 YAML 中编排任意阶段图。

## 执行协议：exec-mrac-v1

这是独立的代码执行协议，`artifact_type: code`。默认协议仍是 spec-mrac-v2；执行代码
必须显式选择 exec-mrac-v1，并用 `run --spec-file` 指定非空 UTF-8 Markdown Spec。
case 只提供固定项目/任务包和仓库基线，不能选择协议。执行与审计以显式选定 Spec 为
唯一实现依据，不自动使用上一 run 或 case 中的原始任务替换它。

```powershell
uv run python -m mracbench run --case <case-id> --protocol exec-mrac-v1 --spec-file C:\approved\spec.md --model gpt-5.6-luna --reasoning-effort high --runs-dir C:\mrac-runs --workspace-dir C:\mrac-workspaces
uv run python -m mracbench status --run-dir C:\mrac-runs\<run-id>
# 预算耗尽后，这个显式操作授予另外六次 audit，先处理保存的待修复项
uv run python -m mracbench resume --run-dir C:\mrac-runs\<run-id>
uv run python -m mracbench report --run-dir C:\mrac-runs\<run-id>
```

### 提示词来源与适配

参考 3bb0 checkout 的 mrac-flow-simple SKILL 及共享控制器，来源 commit 为
`4e0559443a88fa5a2577d2c73c6b365128bdaaea`；文件 hash 登记在协议 YAML。
审计提取 `pr-loop`：检查具体正确性、回归、恢复、所有权、必要删除和测试缺陷，忽略
可选加固和无关风格建议，只读最少必要支撑源码，不在审计中运行 Unity。
修复提取 `fix-code` / `apply-fix` 的代码修复约束：修复当前问题、实际改变产品 diff、
保留基线与 Spec 意图、记录验证。原技能没有独立的完整代码修复 prompt；这里的
repair.md 根据这些约束编写，不声称逐字复制。

本协议比较 Spec 与保存的候选 diff，不要求 Plan、已提交 PR、发布流程或 CLI-L1。
源技能“忽略未提交工作区变化”不适用：本协议的工作区就是候选实现，未暂存变更和新增
文件必须进入 diff。原技能的裁决、P3 延期、结构性 BLOCKED 和连续六轮失败计数不迁入；
本协议采用用户指定的每批总计六次 audit、所有问题修复、两次零问题。

### 阶段与权限

`IMPLEMENT → AUDIT → FIX（有问题时）→ AUDIT → CONVERGED`。

- IMPLEMENT/FIX 使用 Codex `workspace-write`，只允许修改本 run 的独立 checkout，
  可运行相关验证，必须如实记录失败或未执行的检查；不得修改固定 Spec、其他工作区、
  全局安装、Git 元数据、commit/push 或自动发布 PR。
- AUDIT 使用 read-only，新会话，仅接收固定 Spec、基线、完整候选 diff、相关源码及
  当前验证记录，不接收历史审计或修复解释。验证记录为模型报告，不自动等同于已通过
  的产品验收；原始命令事件另保存在 raw 中。
- 确需缺失输入时返回 NEEDS_INPUT，保留 IMPLEMENT/FIX 和待解决项。可用
  `resume --input-file <UTF-8回答>` 继续。没有回答时只报告现有问题，不再次调用模型。
  回答可以澄清执行条件，不能悄悄替换 Spec；新 Spec 要启动新 run。exec 的 resume
  不接受 `--spec-file`。

每个 run 的 checkout 为 `<workspace-dir>/exec/<run-id>/`，从 case 的固定 commit 创建，
不复用 Spec 协议的只读缓存。HEAD、origin、refs、Git 配置及暂存树保持不变；代码修改
保留在工作区，由 runner 用临时 index 生成候选，模型不需要 git add。

### 完整 diff、边界与产物

候选 patch 相对于固定基线，包含已跟踪文件的全部修改/删除以及未跟踪且未被 Git 忽略
的新增文件，支持二进制内容与文件模式。临时 index 放在 run 的 scratch 目录，不改变
checkout 的真实暂存区。候选 hash 包含基线、完整候选 Git tree 和产品文件的实际字节 hash。

各阶段均允许只读 Git 检查：`git --no-optional-locks status --short`、
`git diff --no-ext-diff --no-textconv <base_head> --`、`git show` 和 `git ls-files`。
普通 diff 不含未跟踪文件，必须通过 `git ls-files --others --exclude-standard` 枚举并
读取新增文件；保存的完整 patch 仍为审计依据。prompt 仅传 Spec、checkout 路径、基线
和候选 patch 路径/身份，不内联 product_changes 或 ignored_files，也不提供磁盘文件清单。

Git 忽略的生成物不导出为产品 patch，也不单独保存完整清单。审计员按需用
`git ls-files --others --ignored --exclude-standard -- <relevant-path>` 查询相关路径。
必要产品代码/资源不得隐藏在 ignored 路径中；审计必须报告这种遗漏。分类相关性由审计
判断，runner 不假装能自动识别任意文件是否属于实现。审计前后检查整个 checkout，
包含 ignored 输出；审计员对这些文件的修改也属于违规。完整文件哈希表仅在内存中
临时计算，候选元数据仅保存基线、tree、签名、workspace 和 patch 哈希。

Spec 原文复制到 `input/execution-spec.md`；记录选择路径与 SHA-256，后续用快照执行，
不依赖外部源文件仍存在。每个变化的候选保存：

```text
candidates/0001/changes.patch
candidates/0001/manifest.json       # 精简身份元数据，无完整文件清单（保留文件名以兼容旧 run）
implementations/implement-01.json   # 实施总结、验证或缺失输入
audits/audit-01.json
repairs/repair-01.json              # 每项修复与验证证据
pauses/pause-01.json                # 原问题、候选和 clean 计数
resumptions/resume-01.json          # 继续前的完整结果
exec-state.json                    # 原子规范检查点和证据 hash
run-report.md
```

`final_checkout` 指向代码目录，`final_artifact` 指向最新可应用 patch，`final_candidate_sha256`
与两个 clean audit ID 构成成功证据。可在相同基线的干净副本中用
`git apply --binary --index <changes.patch>` 应用产品变更。运行器不会自行应用到用户项目。

### 审计与修复输出

审计返回 `audit_id/spec_sha256/candidate_sha256/findings`。每项 finding 含
`severity/title/evidence`，P0–P3 都属于必须处理的问题；**只有空 findings 才 clean**。
没有非阻塞豁免、延期或主控驳回后计 clean 的路径。控制器校验文档/候选身份、审计员
新会话 ID，以及审计前后代码未变化。所有发现应指向 Spec 落实或实际代码缺陷，不能
为配额引入无关改进。

实施/修复返回 disposition（complete 或 needs_input）、summary、非空 validation。
validation 的每项是 command/status/evidence，status 可为 passed、failed、not_run。
修复 complete 还必须有覆盖全部发现的 fixes（finding_id/summary/evidence），且产品
diff 实际变化；只改日志或 ignored 输出不能关闭问题。needs_input 不得声称部分关闭。
Spec 已由基线满足时，首次 IMPLEMENT 可以不改代码，但仍须经历两轮独立审计。

### 六轮预算、续跑和通过标准

每批固定 **6 次实际启动的 audit**，IMPLEMENT/FIX 单独计数，不占 audit 预算。
case 的历史 audit 限额不参与本协议，`--max-rounds` 只允许省略或显式写 6。

- 相同 Spec、基线和产品候选，连续两个独立审计都零问题，立即 CONVERGED。
- 第六轮仍有问题：保存原发现和当前代码，**不执行第六轮修复**，PAUSED。
- 显式 resume：增加六次总额度（6→12→18），先修复保存的问题，再重新审计。
- 第六轮为首次 clean：暂停时保留；继续时产品代码未变，第七轮 clean 即可收敛。
- 任何修复或失败审计打断 clean。已启动但超时/无效输出的 audit 仍消耗一次额度；
  调用失败不会伪装成 clean，也不自动重试。错误后显式恢复时，有剩余额度则保持预算，
  已耗尽才授予新的一批六次，并记录扩额。
- 每次继续都校验固定输入、模型/推理强度、adapter 版本、Git 控制状态和保存的代码
  候选。外部修改产品代码会拒绝恢复，不能把旧 clean 用在新代码上。ignored 生成物
  的空闲期变化不改变产品候选身份，但审计调用期间对它们的写入仍会被拒绝。
- 保存的失败可写调用可保留部分代码，恢复后继续该 IMPLEMENT/FIX。只恢复可验证的
  检查点，不自动修复强制崩溃留下的未知状态。旧子进程仍活着或 run 锁被占用时拒绝
  恢复；不自动覆盖或删除现有 checkout。

本协议的预算终点是 PAUSED，不使用 NON_CONVERGED 结束该批实验。退出码与统一 CLI
一致：CONVERGED=0、错误=2、PAUSED=3、NEEDS_INPUT=5、ABORTED=6。默认协议仍是
spec-mrac-v2，只有显式选择 exec-mrac-v1 才进入可写阶段。

验收覆盖六轮边界、重复扩额、跨批 clean、P3 修复、完整 patch 应用、只读违规、Git
控制状态、输入/候选篡改、失败恢复、独立 checkout 及实际子进程的 sandbox 参数。

## 默认协议：spec-mrac-v2

当前修订为 `spec-mrac-v2@2`，参考 1df0 checkout 的
`.codex/skills/mrac-spec/SKILL.md`、`scripts/mracspec.py` 和
`references/controller.md`，固定来源 commit 为
`a21a921d4ed9039bce606b35e1c3267a0dea0de6`。来源和 Bench 状态版本均为 3；
来源文件 SHA-256 保存在协议 YAML 中。运行时不依赖该本机 checkout。

### 起点、审计和仓库规则

原样复制输入 Spec 后直接 `AUDIT → FIX（有发现时）→ AUDIT → FROZEN`。
没有 generate、单独 spec-init、裁决、Plan、实现或后置审查。审计核心指令与源技能一致：
“对 Spec 的正确性和可实施性做审计。”每轮必须检查固定仓库，包括 clean 轮。

输入明确提供当前 Spec、原始 Spec 及 hash、固定 commit、仓库位置和附件。
`input/repository-instructions.json` 保存固定提交中 `AGENTS.md` / `AGENTS.override.md`
的路径索引；各阶段按需要读取相关固定版本规则，但不能覆盖只读、实验输入和授权范围。
CLI 禁用自动用户配置/规则加载。Spec 引用其他版本时，在 case 固定基线上核对差异，
不自动换仓库或 fetch 别的提交。

新审计输出和 `audits/audit-NN.json` 只要求：

```json
{"audit_id":"...","findings":[{"severity":"P1","title":"缺口","evidence":"Spec 条款、固定提交中的路径和符号/章节"}]}
```

无问题时 `findings: []`。不再要求 `repository_review`、`checks` 或
`repository_assessment`；没有证据覆盖率评审或 insufficient 停止分支。
每条 finding 保留依据，控制器检查格式、Spec hash、固定基线和审计员独立性，
不机械证明仓库检查覆盖率或结论真假。无法读取基线等执行错误不能冒充 clean。

为对齐源技能，解析器仍接受旧输出中的可选 `repository_review`：
若提供则验证 commit、非空 checks 及固定提交中的文件路径，并原样保留；
新提示词不请求此字段，也不为新输出补造检查清单。这不允许恢复旧版运行。

每次 CLI 审计创建新会话，记录 `thread_id`；已记录的审计会话 ID 不能复用。
原始模型输出保存在 raw 中，合法审计记录不改写 finding 内容，runner 分配 F1、F2 等内部 ID。
无效 JSON 保持 AUDIT_INVALID，不自动纠正或转换成 clean。

### 直接 FIX 和必要输入

审计记录成功即完成本轮：有发现则所有 P0–P3 直接进入 FIX，无接受、驳回、暂缓或
exception 分类。修复输入为 `findings`，待修复项存为 `pending_fix.findings`。
不再调用 review，不生成 reviews、decisions、accepted_count、review_rounds 或 deferred_p3。
轮次统计使用 `reported_count` 和 `reported_by_severity`；blocking_issue_count
仍统计 P0–P2，但 P3-only 也必须修复，不能计 clean。

修复使用用户意图和固定仓库，在授权范围内自主补足设计，区分既有约束和本次新增决定。
continue 返回完整 Spec 和覆盖每项 finding 的 summary/evidence；Spec 字节必须变化。
缺少必要事实或选择时，返回 needs_input、具体 reason 和非空 questions，保留全部
pending findings，Bench 状态为 NEEDS_INPUT，阶段仍为 FIX。没有 BLOCKED。

```powershell
uv run python -m mracbench run --case <case-id> --model gpt-5.6-luna --reasoning-effort high
uv run python -m mracbench status --run-dir C:\mrac-runs\<run-id>
uv run python -m mracbench resume --run-dir C:\mrac-runs\<run-id> --input-file C:\answers.md
uv run python -m mracbench report --run-dir C:\mrac-runs\<run-id>
uv run python -m mracbench abort --run-dir C:\mrac-runs\<run-id> --reason "停止本次实验"
```

回答只能用于 pending FIX，保存在本 run 的 responses 中。原始 Spec 快照不变；
回答作为明确的后续输入，不能声称原始 Spec 已隐含该决定。
NEEDS_INPUT 下没有新回答或修订 Spec 的 resume 只报告问题，不再次调用模型。

### 冻结、暂停与预算

两个不同新审计员在相同 Spec 字节和同一基线上，连续两轮返回空 findings，
且无 pending 修复项，才进入 FROZEN（Bench 状态 CONVERGED）。
修复、废弃轮或外部修订清空 clean。冻结记录包含两个 audit ID、审计会话 ID、
frozen hash 和固定基线。冻结记录的是审计员的设计可实施性判断，不是覆盖率证明。

连续六轮包含 P0–P2 findings，完成第六轮全部修复后 PAUSED。clean 或 P3-only
重置此计数；P3-only 仍进入 FIX。暂停后显式 resume 清零计数并开始新审计。

默认没有 audit 总硬上限，不读取 case 的 max_audit_rounds；仅显式 --max-rounds
或协议 limits 设置总上限。最后一轮的全部修复仍完成，随后耗尽则 NON_CONVERGED。
硬预算终止优先于六轮暂停，resume 不扩充预算。NEEDS_INPUT 保留 FIX，回答后先完成
修复再检查预算。每次模型调用超时按 CLI / case 生效，默认 1800 秒。

### 检查点、历史与恢复

`repository-state.json` 是原子规范检查点（schema_version 3），含阶段、计数、
待修复 findings 和不可变证据 hash。`state.json`、`result.json` 和报告是展示记录。
`working/spec.md` 允许用户修订，artifacts 中的历史版本不可变。

- 未完成或无效审计：恢复时保留原记录、标记 abandoned、清除 clean，用新 ID 审计。
  已启动的失败调用仍计入 audit 预算。
- FIX：保留全部问题，恢复后先修复，不直接跳到新审计。
- 外部修订：活动轮报 AUDIT_STALE；resume 保存新 artifact 并废弃旧活动轮。
  可用 --spec-file 显式导入，但不能以此绕过历史 evidence 损坏。
- FROZEN：检查冻结 hash；改变后报 FROZEN_SPEC_CHANGED，必须新开 run。
- 配置与输入从 run 快照恢复，模型、推理强度、adapter 版本、基线和预算固定。
- 活跃 run 使用 OS 锁；旧 agent 仍活着时拒绝恢复。未知 Git 缓存锁不自动删除。
- Abort 保留原因和待修复证据，进入 ABORTED，不能继续。

`spec-mrac-v2@1` / state version 2 使用旧裁决语义，仅支持 status/report 只读查看。
不重写其检查点、审计、报告或 clean 计数，不允许 resume、导入 Spec/回答或 abort；
要使用新语义必须从选定 Spec 新开 run。报告明确标注历史裁决语义。
旧版本 clean 可能包含已驳回或暂缓的问题，不能带入新版本。state version 1 不支持。

NEEDS_INPUT 退出码为 5，ABORTED 为 6，PAUSED 为 3；其他校验/执行错误为 2。
status 返回 RUNNING 时退出码为 0，不表示收敛。

### 与源技能的适配边界

Bench 用固定 checkout 和新 CLI 会话替代交互主控/子 agent；用独立 repair 调用
完成父会话的修复职责。模型返回完整 Spec，runner 保存文本及修复记录并检查仓库不变。
没有独立裁决调用，不继承父会话历史，不创建 Beads 任务或产品分支。
Bench 要求整个 run 的 checkout 保持固定 HEAD；源技能允许按捕获 commit 读取。
冻结不证明代码编译、产品测试、部署资源存在或未来实现正确。

其余三个协议保持各自语义；spec-flow-simple-v1 仍有裁决，其旧 BLOCKED / document-only
结果不迁移到本协议。

## 旧协议：spec-mrac-v1

流程为 `generate → audit → 必要时 repair → audit`。三个阶段均可读取原始任务和固定
仓库，audit 另接收当前 Spec，repair 另接收本轮 audit。每次调用独立、无历史会话。

audit 返回 `status` 和 `issues`；每项含 `id/severity/title/description/evidence/required_change`。
severity 是 `blocking` 或 `non_blocking`。无 blocking 即 clean；非阻塞建议可以保留。
没有主控裁决阶段。最终预算轮不再 repair。默认最多 8 次 audit，结果为 `CONVERGED`、
`NON_CONVERGED` 或执行错误。此协议不支持 resume。原有提示词和判定行为保持不变。

## spec-flow-simple-v1 的参考来源和适配边界

参考仓库 commit：`90666ff1f49ce9e9c8569c969177adcfef593470`。
读取来源为用户指定的 bdf6 checkout 中：

- `.codex/skills/mrac-flow-simple/SKILL.md`
- `.codex/skills/mrac-flow/scripts/mracflow.py` 的 Simple profile
- `.codex/skills/mrac-flow/scripts/audit_policy.py`

原文件 SHA-256 登记于新协议 YAML 的 `source.source_sha256`。关键来源包括
`SIMPLE_AUDIT_INSTRUCTIONS`、两个 Spec repair guidance、`validate_simple_review`、
`apply_audit_action`、`update_simple_failure_streak` 和 Spec fix 转移逻辑。
运行时不依赖这些外部路径。

保留的行为：复制源 Spec；初始化审计与冻结审计的不同读取范围；P0–P3；逐条裁决；
accepted/rejected/deferred；依据原意与固定基线修复；初始化修复直接
进入冻结；两个独立 clean；连续六轮接受 P0–P2 后暂停并保留 pending fix。

Bench 适配点：

1. 提取到 Spec 冻结为止，不接入 Beads、分支创建、Plan、产品代码实现、PR、CLI-L1。
2. 原交互主控的裁决和修复由显式模型调用承担，各自使用完整的受控输入；不传隐式
   对话历史。裁决、修复与审计默认使用同一指定模型/推理强度，每次都是新的调用。
3. 外部相关 Spec 必须封装为 case 中的固定附件，禁止读取任意实时外部路径。
4. 总 audit 预算是额外的 benchmark 硬上限，与六轮软暂停分开计算。
5. 源流程冻结后进入 PLAN；这里在冻结时返回 `CONVERGED`，记录 `flow.phase: FROZEN`。
6. 审计采用同一 adapter 的新 `codex exec --ephemeral` 实现独立性，不创建用户侧任务。
7. 修订 @2 删除所有结构性阻塞条件和 repair block；@3/@4/@5/@6 保持此语义，不支持 BLOCKED 或替代的缺输入状态。
   接受项继续修复；必要假设须明确写出，不能伪装成已验证事实。
8. @4/@5/@6 使用 flow state 3，并在每次启动/续跑时先验证只读仓库 MCP 能力；校验失败为执行错误。
   只恢复 PAUSED，不支持崩溃续跑或扩充总预算。@1/state 1、@2/state 2 与 @3/state 3 历史结果只读。

这些差异属于实验定义，不能将这个协议描述为完整 MRAC-Flow Simple 产品工作流。

## 输入与阶段

case 的 `task.file` 是唯一不可变原始 Spec；先校验 `task.sha256`，再原样复制到
`artifacts/spec.initial.md`。复制保留原始 UTF-8 字节、BOM、CRLF；不调用模型生成或
整理初始文档。修复输出则作为新的 UTF-8 完整 Markdown 产物保存。

`repository.commit` 是受控实现基线，必须是完整 SHA。裁决和修复仅使用该快照，
区分基线事实与目标行为；不因 Spec 引用其他基线进入阻塞，不自行 fetch 其他提交，
也不使用分支最新版本补证。

| 阶段 | 明确提供的输入 | 读取边界与职责 |
|---|---|---|
| `repository-read-check` | 仓库路径、固定 HEAD | 实际查询固定 HEAD、搜索并读取源码；事件证据不完整则执行失败，不启动 audit |
| `spec-init` | 当前 Spec、原始 Spec 及 hash、固定仓库及 SHA、相关附件 | 可以读取固定基线代码和仓库内相关 Spec；只报告阻碍 Spec 声明收益完成的问题及证据，不报告纯代码缺陷，不给修复方案 |
| `spec-freeze-loop` | 当前 Spec、Spec 字节 hash、audit ID | 仅审独立 Spec 的价值、自洽、所有权、权威状态、生命周期/失败边界、完整性和可观察验收；无工具调用、代码、Plan、原始意图或历史审计 |
| `review` | 本轮 findings、审计种类、当前 Spec、原始意图、固定基线、附件 | 逐条裁决现有发现；冻结阶段不能引入仅由代码或意图比较产生的新发现 |
| `repair-init` | 独立候选文件、原始意图文件、接受项文件、固定基线、附件 | 通过受限候选 MCP 局部编辑 Spec；必要假设明确标注，保留正确细节 |
| `repair-freeze` | 同上 | 基于原意与基线作最小一致修正，随后做闭环核验 |
| `closure`（@6） | 修复前后 Spec、原始意图、接受项、diff | 只读候选 MCP；检查每项接受发现是否在候选中闭合，不替代独立冻结审计 |

初始化读取直接相关的代码/设计；裁决和修复的代码读取限于 Spec 命名的符号及直接语义
依赖。原始意图优先于当前代码。遇到歧义须作最小一致修正并明确必要假设，
保留正确细节，不以未经验证的推断冒充基线事实；后续审计继续检验这些修正。

@4 起启动时先执行只读仓库能力 preflight：通过固定仓库 MCP 工具查询 HEAD、搜索源码并读取搜索结果中的文件。
工具只读取固定 Git manifest 中的普通文件，逐文件核对 hash，拒绝符号链接和越界路径；每次调用的输入和结果保存在 raw evidence。
失败记为 `REPOSITORY_ACCESS_ERROR`（MCP 工具启动/调用失败或探针证据不足）；
错误停止该 run，不记作 clean，也不转成 BLOCKED。preflight 是模型调用，但不计 audit budget；其 request/raw/execution 保存在 run evidence。

冻结审计使用专用的空工作目录且不启动仓库 MCP 服务，prompt 不注入仓库路径、原始任务、附件或旧审计。
spec-init、review 和 repair 获得固定 HEAD MCP 工具，MCP server 将每次操作限制为只读 manifest 中的普通文件；@6 repair 另有只写候选 Spec 的 MCP 工具，closure 只有候选文件只读工具。模型不通过 shell 检查仓库或编辑候选。
空工作目录、仓库、已有输入和证据的写入变化会被检查。当前不声称具备容器级读取隔离，
也不将“没有提供仓库路径”等同于操作系统禁止读取该路径。

### 相关 Spec 附件

外部 Spec 先复制到 case 包内，再声明相对路径和原始字节 SHA-256：

```yaml
related_specs:
  - file: related/ownership.md
    sha256: "<64 位 SHA-256>"
```

路径不得逃出 case 包，文件必须是非空 UTF-8，哈希必须匹配。不得重复声明同名附件、
原始 task 或 case.yaml。所有声明在启动模型前检查；快照为 `input/related-spec-01.md`
等，提供给允许阶段的 JSON 保留原始包内文件名、hash 和正文。冻结审计不接收附件。
旧协议会校验/保存声明的附件，但不会注入它们；使用附件作为审计输入应选新协议。

## 审计、裁决与修复契约

审计原始 JSON 只含 `audit_id` 和 `findings`，每项只含 `severity/title/evidence`。
允许 P0、P1、P2、P3。控制器按返回顺序赋予 F1、F2 等本轮 ID。审计原文保存在 raw 中，
解析后另存 audits；不改写审计发现。

主控返回 `audit_id/decisions`，每项必须恰好裁决一次：

- `accepted`：任何级别都需要修复，包括 P3。
- `rejected`：必须有包含具体反证的单行 reason，最多 500 字符。
- `deferred`：只允许 P3；保留在结果的 `deferred_p3` 中，不妨碍 clean。
- 不接受 `exception` 字段；产品决策、范围、外部或后续依赖不再是终止分支。

主控不返回 next_action，控制器根据裁决推导状态。reason 只用于 rejected；审计发现
的严重度和措辞不由主控重写。schema 校验能验证裁决完整性，但反证是否充分仍是模型
判断，不构成机械证明。

历史 @5 repair 使用随 `protocol.yaml` 冻结的 CLI 输出 schema，模型只返回：

```json
{"spec":"完整修订 Spec，可在标题前保留原有元数据","fixes":[{"finding_id":"F1","evidence":"原始意图与固定基线证明"}]}
```

runner 将本轮 `audit_id` 和唯一的 `disposition=continue` 写入修复证据。`fixes` 必须覆盖全部接受项，
Spec 必须非空且改变当前字节；一级标题的位置与正文质量由下一轮独立审计判断。
原有元数据无需迁移，保存新的 artifact 时不覆写已有版本。空文档、缺失接受项、旧 block
输出或 exception 字段属于 PARSE_ERROR，不静默忽略，也不转换成 clean、PAUSED 或 NEEDS_INPUT。
@4 仍按其冻结输入快照要求 `audit_id/disposition/spec/fixes`、每项 summary/evidence，且 Spec 以 `# ` 开头。
@5 的 CLI 输出 schema 与原始回复保存在 raw 证据；不支持 schema 的执行环境明确失败，不回退到提示词示例。

@6 repair 不再把整份 Spec 和逐项依据放进模型的最终 JSON。runner 在独立工作区复制候选 Spec、原始意图、接受项及固定附件；模型通过 `mrac_candidate` MCP 读取/搜索文件，只能对候选 `spec.md` 做唯一文本片段替换。固定仓库仍由 `mrac_repository` MCP 只读提供；Codex 阶段保持 read-only sandbox。runner 记录候选文件、工具调用、前后 hash 和 diff，并拒绝额外文件、不可变输入变动、空白/未改动/非 UTF-8 的候选、丢失原有一级标题或明显不完整的文档。

@6 的 `closure` 是另一段新模型调用，只通过只读候选 MCP 比较修复前后 Spec、原始意图、接受项与 diff。它必须实际读取这五份文件；回复 `CLOSED` 或逐行给出未解决项 `F1: 原因`。runner 也接受等价的短 JSON，但不要求模型重写整个 Spec 或重复固定 ID。闭环未通过时自动给 repair 一次带反馈的重试；核验格式或读取证据缺失时，只重试核验一次。全部尝试和失败原因保留为证据，repair/closure 调用分别计数，不增加 audit 预算。最终未修复为 `REPAIR_INVALID`，核验无效为 `CLOSURE_INVALID`；冻结审计仍不接收这些交接记录，只审新的完整 Spec。

## 状态、计数、暂停与恢复

初始化无接受项，或初始化修复成功，都直接进入 `spec-freeze-loop`，初始化不贡献 clean。
冻结无接受项才是经裁决的 clean，允许留下被驳回或延期的问题。相同字节的两个不同
audit ID 连续 clean，且无 pending fix，才能记录 frozen hash 和两个 clean ID。
接受项、修复或字节变化打断 clean；不可变产物被外部改写直接报违规。

冻结循环有接受的 P0–P2 时 failure streak 增加；clean 时归零。仅接受 P3 不增加这个
计数，也不算 clean。达到六轮后在裁决完成时 PAUSED，尚未执行该轮修复；pending fix
写入暂停记录。初始化不参与这项计数。

总预算只计已启动的 audit，包括初始化、失败 audit；review 和 repair 单独计数。
默认总预算为 8，可通过 case limits 或 `--max-rounds` 覆盖。最后一轮仍会完成裁决：
满足冻结先 CONVERGED，否则预算耗尽为 NON_CONVERGED，不继续修复，
也不再产生 PAUSED。相同轮同时满足硬预算和六轮软暂停时，硬预算优先。

`resume` 只接受 @4/@5/@6 / state 3 的 PAUSED。它取得 run 锁，校验输入、产物、审计/裁决/修复、raw 和原结果
的保存哈希；使用原 case/protocol 快照，保留原 model、reasoning effort、timeout、
总预算和累计计数，并要求 adapter 类型与版本一致。先存档暂停终态，再清除软暂停计数
和 clean，从 pending fix 继续，后续必须有新的冻结审计。

恢复不增加预算：默认 8 次的 run 若在第 7 次总审计后暂停，恢复只剩 1 次 audit，无法
获得两次新的 clean。需要评估暂停后恢复的实验，应在启动时给足预算，例如 12。
不接受 `resume --model`、`--protocol` 或预算覆盖。运行中的锁、变脏或漂移的仓库、损坏证据、非 PAUSED
状态拒绝恢复，并保留原结果。异常进程遗留锁须确认进程已结束后人工处理。

| 结果 | 退出码 | 含义 |
|---|---:|---|
| CONVERGED | 0 | 冻结完成 |
| NON_CONVERGED | 1 | 总 audit 预算耗尽 |
| PAUSED | 3 | 六轮软暂停，待显式恢复 |
| CASE_ERROR / REPOSITORY_ERROR / REPOSITORY_ACCESS_ERROR / EXECUTION_POLICY_ERROR / AGENT_ERROR / TIMEOUT / PARSE_ERROR / REPAIR_INVALID / CLOSURE_INVALID / PROTOCOL_VIOLATION / INTERNAL_ERROR | 2 | 配置、执行、格式、修复或边界错误，不是正常未收敛 |

恢复校验错误以退出码 2 输出错误，不重写原 run 的终态。wall time 累加各次实际执行时长，
不计用户等待恢复的时间。@6 的修复及核验内部各允许一次有记录的重试；终态错误不自动重跑，崩溃也不续跑。

## spec-flow-simple-v1 证据

除通用 run 文件外，新协议保存：

```text
input/execution-config.json      # 固定模型、推理强度、预算、adapter 与协议选择
input/related-spec-01.md         # 可选附件的原始字节
audits/spec-init-01.json
audits/spec-freeze-loop-02.json
reviews/spec-init-01.json        # 每个 finding 的原始裁决
repairs/repair-01.json           # @6 尝试状态、接受项 ID、前后 hash 与证据路径
repairs/repair-01.diff           # @6 候选 Spec 的完整 diff
raw/repair-01/workspace/spec.md  # @6 候选 Spec 文件；同目录有固定输入文件
raw/repair-01/candidate-events.jsonl # @6 候选 MCP 读取/编辑记录
closures/closure-01.json         # @6 独立闭环核验；重试为 closure-01-retry.json
pauses/pause-01.json             # failure streak、pending fix、文档 hash
resumptions/resume-01.json       # 原 PAUSED result 与 hash、恢复时间
raw/repository-read-check-01/mcp-servers.json
raw/repository-read-check-01/repository-read-events.jsonl # 每次仓库 MCP 的输入、输出与成功状态
audit-workspaces/<stage>/        # 冻结审计的独立空 cwd
```

trajectory 记录审计种类、ID、artifact/hash、reported count、accepted count、接受项分级、
裁决路径和 clean streak。`blocking_issue_count` 在新协议中仅统计接受的 P0–P2；
accepted P3 也会触发修复，因此不能只用这个数推断 clean。`deferred_p3` 是带 audit ID
和 artifact hash 的历次延期记录，不自动宣称旧版本上的延期项仍存在于最终文档。

冻结证明仅覆盖该协议所定义的 Spec 审计范围。它不证明最终 Spec 的绝对正确性、
全量原意蕴含、实现可行性或产品测试通过。

## 维护与验收

维护验收执行 `uv run pytest -q`、`uv run ruff check mracbench tests` 和
`uv run ruff format --check mracbench tests`。关键回归包括：

- 旧协议的输出、错误和预算行为；无协议字段的 case 可默认/显式运行；遗留字段不影响选择。
- 原始字节复制、初始化无生成、初始化不计入冻结 clean、初始化修复直接进入冻结。
- 分阶段输入隔离、固定附件及 hash；审计保持独立。
- v2 仅 audit/repair 调用、紧凑 findings 输出、全部 P0–P3 直接 FIX、旧裁决运行只读。
- spec-flow-simple-v1@4/@5/@6 验证实际仓库只读 MCP 能力；spec-init/review/repair 能读取固定仓库，freeze audit 仍仅接收 Spec；缺失仓库读取证据不能产生 clean。每项恰好裁决一次、接受项进入修复，且不支持 BLOCKED。@6 另验证候选 MCP 的独占写边界、实际读取证据、文件改动、闭环回复与两个独立重试上限；@5/@4 保留原输出规则。@1/@2/@3 历史 state 不续跑。
- 相同内容双 clean、修复重置、六轮暂停及 clean 重置计数。
- 预算边界、暂停恢复、输入/证据/配置篡改、锁和仓库违规。

真实模型运行是单独实验，不作为普通单元测试。跨协议对比必须同时报告协议 ID/version、
输入与基线 hash、模型/推理强度、预算，以及 audit/review/repair 数量与时长。由于初始
产物和审计范围不同，不能把两个协议的“收敛轮数”当作同一标尺直接排名。

## 变更记录

- 2026-09-16：保留 `spec-mrac-v1@1`；新增 `spec-flow-simple-v1@1`、显式协议选择、
  固定附件、分阶段输入、裁决/修复记录和 PAUSED 恢复。
- 2026-09-16：解除 case 与协议选择的绑定，默认协议改由运行层提供，保留显式选择及
  历史快照兼容。协议提示词与审计语义没有变化，协议版本保持不变。

- 2026-09-16：新增默认协议 `spec-mrac-v2@1`，对齐 3bb0 的 mrac-spec state v2；所有轮次要求仓库证据，移除该协议的 BLOCKED，保留 FIX，暂停发生在修复之后。
- 2026-09-16：新增独立执行协议 `exec-mrac-v1@1`，参考 3bb0 的 pr-loop/fix-code；每批六次 audit，耗尽后保留问题，显式续跑增加六次并先修复。
- 2026-09-16：`spec-mrac-v2@2` 对齐 1df0 的 mrac-spec state v3，移除裁决调用与证据覆盖率评审；全部 findings 直接修复，仅空 findings 计 clean。旧 state 2 仅保留只读查看。

- 2026-09-17：`spec-flow-simple-v1@2` 删除结构性 exception 和 repair block；接受项继续修复，不支持 BLOCKED。使用 flow state 2；@1 历史结果只读。双 clean、六轮暂停与总预算规则保持。
- 2026-09-23：`spec-flow-simple-v1@3` 对齐 Simple 技能的仓库读取边界：增加每次启动/续跑时的真实固定 HEAD、`rg` 文件及内容搜索、源码读取 preflight，并从 Codex 命令事件核对证据。初始化审计、review、修复可读取固定基线；freeze 审计仍只读 Spec。工具策略拒绝及 preflight 缺证据分别作为 `EXECUTION_POLICY_ERROR` / `REPOSITORY_ACCESS_ERROR`，不允许形成 clean。旧 @1/@2 运行只读。

- 2026-09-23：`spec-flow-simple-v1@4` 改用 runner 提供的只读 MCP 仓库服务供初始化审计、review、repair 检索固定基线；仓库路径、HEAD 与 git manifest 固定，单文件逐次校验 SHA-256，限制读取大小/搜索结果，拒绝符号链接及路径越界。冻结审计不暴露仓库工具。preflight 直接验证 MCP 查询、搜索和文件读取事件；CLI shell policy 不再作为源码访问通道。
- 2026-09-24：`spec-flow-simple-v1@5` 用 CLI 输出 schema 固定 repair 的 JSON 外形；模型仅返回完整 Spec 和每项接受发现的依据，runner 记录固定 audit ID 与 continue。Spec 文本只做非空校验，允许保留标题前的原始元数据，后续冻结审计判断文档质量。@4 的修复格式和既有结果保持原语义。
- 2026-09-24：`spec-flow-simple-v1@6` 把 repair 的完整 Spec JSON 改为受限候选 MCP 文件编辑；runner 保存前后字节、diff、接受项和工具事件。新增独立只读闭环核验，必须实际读取五份固定文件；回复简化为 `CLOSED` 或未解决项行。无效候选与缺证据核验各有一次受限重试，不占 audit 预算。@4/@5 快照保持原解析路径。
