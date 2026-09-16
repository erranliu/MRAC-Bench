# 并存协议管理

本文件是 MRAC Bench 协议的索引、行为契约和维护规范。可执行配置位于
`protocols/<id>/protocol.yaml`，提示词与配置一同保存到每次 run 的 `input/`。
修改协议时必须同步这里的登记与差异说明。本文描述现有实现；早期 Milestone 1
Spec、Plan 和验收报告保留为历史设计记录。

## 协议登记

| ID | version | workflow | 输入含义 | 起点 | 收敛含义 |
|---|---:|---|---|---|---|
| `spec-mrac-v1` | 1 | `generate-audit-repair`（默认） | 原始任务 | 生成 implementation Spec | 同一产物连续两次无 blocking issue |
| `spec-flow-simple-v1` | 1 | `spec-init-freeze` | 待审计的原始 Spec | 原样复制输入 Spec，初始化审计 | 同一 Spec 字节连续两次经裁决的冻结 clean |

- [spec-mrac-v1 配置](../protocols/spec-mrac-v1/protocol.yaml)
- [spec-flow-simple-v1 配置](../protocols/spec-flow-simple-v1/protocol.yaml)

两者共享只读执行、固定仓库缓存、输入哈希检查和证据存储，但不共享审计问题 schema
或收敛判断。新协议没有 generate 阶段，不能把普通 issue 文本自动扩写成新的 Spec。

## 选择、版本与复现

case 与 protocol 独立，在 run 中组合。协议选择优先级：`run --protocol <id>` →
运行层默认值 `spec-mrac-v1`（代码中的 `DEFAULT_PROTOCOL_ID`）。CLI 和 Python API 使用
同一个默认值。显式传入无效协议会报错，不回退默认值。

case schema 不包含协议选择字段，制作模板和内置 case 均不声明 `protocol`。
旧 case 或历史输入快照若保留 `protocol` 字段，loader 将其视为不参与执行的历史数据，
不读取它的 id、不校验其内容、不用它选择协议；无需先改写旧 case 才能运行。

`run.yaml` 记录实际 `protocol_id`、`protocol_version`、选择来源
`protocol_selection`（`default` / `explicit`）及 `requested_config.protocol_id`
（未指定时为 null），不再记录 `case_default_protocol`。运行选择不修改 case。

```powershell
# 同一 case 使用运行层默认协议 spec-mrac-v1
uv run python -m mracbench run --case <case-id> --model gpt-5.6-luna --reasoning-effort high

# 显式选择新协议；12 是总 audit 预算，包含初始化审计
uv run python -m mracbench run --case <case-id> --protocol spec-flow-simple-v1 --model gpt-5.6-luna --reasoning-effort high --max-rounds 12

# 只恢复新协议中完整保存的 PAUSED run
uv run python -m mracbench resume --run-dir C:\mrac-runs\<run-id>
```

协议 ID 标识一套实验语义。新增不同测量对象、阶段图或审计边界时建立独立 ID。
同一 ID 下修改提示词、schema、裁决或结果含义时递增整数 `version`；仅修复不影响行为的
排版、链接不需要升级。ID 里的 `v1` 是名称的一部分，实际修订版本以 YAML `version` 为准。
每次 run 固定实际配置、提示词原文及 SHA-256；恢复使用 run 内快照，不重读当前项目的
case 或协议文件，也不重新选择默认协议。已经开始或结束的实验不因编辑项目协议或
变更默认值而改变。旧 run 中的 `case_default_protocol` 仅保留为当时的记录，不作恢复依据。

新增协议必须登记：目的、来源及固定版本、输入契约、阶段、模型角色、输出 schema、
收敛/停止条件、预算与恢复、证据格式、兼容性和验证用例。当前只支持下述两种明确的
workflow，不支持在 YAML 中编排任意阶段图。

## 旧协议：spec-mrac-v1

流程为 `generate → audit → 必要时 repair → audit`。三个阶段均可读取原始任务和固定
仓库，audit 另接收当前 Spec，repair 另接收本轮 audit。每次调用独立、无历史会话。

audit 返回 `status` 和 `issues`；每项含 `id/severity/title/description/evidence/required_change`。
severity 是 `blocking` 或 `non_blocking`。无 blocking 即 clean；非阻塞建议可以保留。
没有主控裁决阶段。最终预算轮不再 repair。默认最多 8 次 audit，结果为 `CONVERGED`、
`NON_CONVERGED` 或执行错误。此协议不支持 resume。原有提示词和判定行为保持不变。

## 新协议的参考来源和适配边界

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
accepted/rejected/deferred；结构性例外阻塞；由基线唯一蕴含的修复；初始化修复直接
进入冻结；两个独立 clean；连续六轮接受 P0–P2 后暂停并保留 pending fix。

Bench 适配点：

1. 提取到 Spec 冻结为止，不接入 Beads、分支创建、Plan、产品代码实现、PR、CLI-L1。
2. 原交互主控的裁决和修复由显式模型调用承担，各自使用完整的受控输入；不传隐式
   对话历史。裁决、修复与审计默认使用同一指定模型/推理强度，每次都是新的调用。
3. 外部相关 Spec 必须封装为 case 中的固定附件，禁止读取任意实时外部路径。
4. 总 audit 预算是额外的 benchmark 硬上限，与六轮软暂停分开计算。
5. 源流程冻结后进入 PLAN；这里在冻结时返回 `CONVERGED`，记录 `flow.phase: FROZEN`。
6. 审计采用同一 adapter 的新 `codex exec --ephemeral` 实现独立性，不创建用户侧任务。
7. 当前只恢复 PAUSED，不支持崩溃断点续跑、BLOCKED 原 run 恢复或给已有 run 扩充预算。
   BLOCKED 需先获得产品决策/解决依赖，更新输入并递增 case version 后启动新 run。

这些差异属于实验定义，不能将这个协议描述为完整 MRAC-Flow Simple 产品工作流。

## 输入与阶段

case 的 `task.file` 是唯一不可变原始 Spec；先校验 `task.sha256`，再原样复制到
`artifacts/spec.initial.md`。复制保留原始 UTF-8 字节、BOM、CRLF；不调用模型生成或
整理初始文档。修复输出则作为新的 UTF-8 完整 Markdown 产物保存。

`repository.commit` 是受控实现基线，必须是完整 SHA。若 Spec 明确指定另一个实现基线，
并且某项裁决/修复需要该版本才能证明，主控必须报告缺失依赖并阻塞；不自行 fetch
其他提交，也不使用分支最新版本补证。

| 阶段 | 明确提供的输入 | 读取边界与职责 |
|---|---|---|
| `spec-init` | 当前 Spec、原始 Spec 及 hash、固定仓库及 SHA、相关附件 | 可以读取固定基线代码和仓库内相关 Spec；只报告阻碍 Spec 声明收益完成的问题及证据，不报告纯代码缺陷，不给修复方案 |
| `spec-freeze-loop` | 当前 Spec、Spec 字节 hash、audit ID | 仅审独立 Spec 的价值、自洽、所有权、权威状态、生命周期/失败边界、完整性和可观察验收；无工具调用、代码、Plan、原始意图或历史审计 |
| `review` | 本轮 findings、审计种类、当前 Spec、原始意图、固定基线、附件 | 逐条裁决现有发现；冻结阶段不能引入仅由代码或意图比较产生的新发现 |
| `repair-init` | 全部接受项、当前 Spec、原始意图、固定基线、附件 | 一次完整修复初始化接受项，随后进入冻结循环 |
| `repair-freeze` | 全部接受项、当前 Spec、原始意图、固定基线、附件 | 只澄清已有意图；每项须有基线蕴含证据；修复后重新审计 |

初始化读取直接相关的代码/设计；裁决和修复的代码读取限于 Spec 命名的符号及直接语义
依赖。原始意图优先于当前代码。无法证明唯一已有规则时不能自行创造产品行为。

冻结审计使用专用的空工作目录，prompt 不注入仓库路径、原始任务、附件或旧审计。
禁止工具读取属于提示词边界；Codex read-only sandbox 不是严格的读取白名单。
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
- accepted 可附 `exception`：`product-decision`、`scope-expansion`、
  `external-dependency`，任一成立立即 BLOCKED。

主控不返回 next_action，控制器根据裁决推导状态。reason 只用于 rejected；审计发现
的严重度和措辞不由主控重写。schema 校验能验证裁决完整性，但反证是否充分仍是模型
判断，不构成机械证明。

repair 返回二选一：

```json
{"audit_id":"...","disposition":"continue","spec":"# 完整 Spec\n正文","fixes":[{"finding_id":"F1","summary":"修正说明","evidence":"原始意图与固定基线证明"}]}
```

```json
{"audit_id":"...","disposition":"block","reason":"无法消除的歧义、越界设计或依赖"}
```

continue 必须覆盖全部接受项，返回有一级标题及正文的完整 Spec，并改变当前字节。
只保存新的 artifact，不覆写已有版本。block 不接受部分 Spec，不将未解决项记为完成。
fixes 是主控的修复与证据声明；下一轮独立审计继续检验文档。

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
例外先 BLOCKED，满足冻结先 CONVERGED，否则预算耗尽为 NON_CONVERGED，不继续修复，
也不再产生 PAUSED。相同轮同时满足硬预算和六轮软暂停时，硬预算优先。

`resume` 只接受 PAUSED。它取得 run 锁，校验输入、产物、审计/裁决/修复、raw 和原结果
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
| BLOCKED | 4 | 产品决策、范围或依赖无法在现有授权内解决 |
| CASE_ERROR / REPOSITORY_ERROR / AGENT_ERROR / TIMEOUT / PARSE_ERROR / PROTOCOL_VIOLATION / INTERNAL_ERROR | 2 | 配置、执行、格式或边界错误，不是正常未收敛 |

恢复校验错误以退出码 2 输出错误，不重写原 run 的终态。wall time 累加各次实际执行时长，
不计用户等待恢复的时间。暂不支持任意错误状态自动重试或崩溃续跑。

## 新协议证据

除通用 run 文件外，新协议保存：

```text
input/execution-config.json      # 固定模型、推理强度、预算、adapter 与协议选择
input/related-spec-01.md         # 可选附件的原始字节
audits/spec-init-01.json
audits/spec-freeze-loop-02.json
reviews/spec-init-01.json        # 每个 finding 的原始裁决
repairs/repair-01.json           # 修复正文、逐项证明，或 block 原因
pauses/pause-01.json             # failure streak、pending fix、文档 hash
resumptions/resume-01.json       # 原 PAUSED result 与 hash、恢复时间
audit-workspaces/<stage>/        # 冻结审计的独立空 cwd
```

trajectory 记录审计种类、ID、artifact/hash、reported count、accepted count、接受项分级、
裁决路径和 clean streak。`blocking_issue_count` 在新协议中仅统计接受的 P0–P2；
accepted P3 也会触发修复，因此不能只用这个数推断 clean。`deferred_p3` 是带 audit ID
和 artifact hash 的历次延期记录，不自动宣称旧版本上的延期项仍存在于最终文档。

冻结证明仅覆盖该协议所定义的 Spec 审计范围。它不证明最终 Spec 的绝对正确性、
全量原意蕴含、实现可行性或产品测试通过。

## 维护与验收

修改后执行 `uv run pytest -q`、`uv run ruff check mracbench tests` 和
`uv run ruff format --check mracbench tests`。关键回归包括：

- 旧协议的输出、错误和预算行为；无协议字段的 case 可默认/显式运行；遗留字段不影响选择。
- 原始字节复制、初始化无生成、初始化不计入冻结 clean、初始化修复直接进入冻结。
- 分阶段输入隔离、固定附件及 hash；审计保持独立。
- 每项恰好裁决一次、P3 接受/延期、结构性例外与 repair 主动 block。
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
