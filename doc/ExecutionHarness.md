# 调用完成、超时与 Spec 文件工具

`codex_exec` harness v3 沿用 v2 的 agent 回合完成与 CLI 进程退出监督。每次调用的
`raw/<stage>/invocation.json` 保存 `harness_version: 3`、实际命令和执行条件。
整轮上限仍使用原有 `timeout_seconds`，case3 实验设为 1800 秒。

## 完成检测与收尾

runner 实时读取 JSONL，检测 `turn.completed`。完成后最多等待 30 秒，且不超过
整轮剩余预算；CLI 仍不退出时清理进程树，保留真实退出码、最终回复和调用证据。

只有完成事件存在、没有失败事件或未完成工具，且本次写出的非空最终回复与
事件流最终消息一致时，清理后的调用才允许进入原有流程的产物检查。
`validated_completed_turn: true` 表示这个传输层检查通过，不代表 Spec 已收敛。
Spec 文件范围、编码、大小、差异和下一轮审计仍由原流程验证。
缺少最终回复、回复不一致或遗留工具会记为 `EXECUTION_ENVIRONMENT_ERROR`，
不会把完成事件直接当成成功。

正常退出继续使用实际退出状态；`turn.failed` 即使伴随零退出码也计调用错误。
既有超时结果、运行证据和稳定报告不作追溯改写。

## 工具监督与时间记录

Shell/命令和 MCP 工具从 `item.started` 到 `item.completed` 的连续执行上限为
300 秒，超时清理进程树并记录环境错误。`file_change` 的事件可能只表达候选补丁，
不适用 Shell 超时，但未完成的文件变更会阻止完成后恢复。

`progress` 每秒更新，包含：

- `agent_active_seconds`：启动后至完成事件的可观察时间。
- `tool_active_seconds`：工具执行区间的并集，重叠工具不会重复累计。
- `non_tool_seconds`：其余时间，包含模型等待、推理和客户端开销；不能当成纯 API 延迟。
- `post_completion_seconds`：完成事件后等待退出及清理的时间。
- `pending_tools`、`failed_tools` 和 `warnings`：未完成工具、失败次数及告警。

v3 增加 `progress.pending_tool_diagnostics`：记录未结束工具的类型、年龄、是否适用
超时，以及可用的 MCP 服务/工具名，不保存参数或工具输出。`completion_validation`
分别记录完成事件、失败事件、未结束工具、最终消息存在性及文件/事件流文本一致性，
并列出 `failure_reasons`。这些字段只解释判定，不改变 v2 的成功条件。
文件修改事件仍不直接套用 300 秒上限；未闭合工具仍会阻止完成后恢复。

`startup_seconds` 与 `teardown_seconds` 单列启动准备及退出后的证据/费用记录开销，
不会将费用查询误记为模型或工具执行时间。`diagnostics` 记录补丁验证失败、失效
进程 ID 和沙箱拒绝的次数。

五分钟没有 stdout 事件、同一工具连续多次失败会留下告警；无输出本身不会终止
模型推理。阶段没有新时间记录时，应按未知处理，不能给历史调用补零。
外部服务的日志仍先脱敏再被监督器读取，密钥不进入诊断字段。

## Spec 修复的文件操作

repository Spec flow 的独立修复 checkout 提供 `mrac_spec_file` MCP：

- `spec_read` 返回 UTF-8 编号行和当前 SHA-256。
- `spec_edit` 使用当前 SHA-256 和唯一匹配的文本片段编辑指定 Spec，保留 UTF-8 BOM
  及统一的 CRLF 换行；拒绝过期哈希、模糊匹配、路径逃逸、链接和 Git 元数据路径。

这些工具只可访问本轮指定的 Spec，不可修改产品代码。旧有 checkout 改动范围检查
继续执行。Windows 调用同时明确提示原生 Shell 为 PowerShell，文件读写使用 UTF-8。

Spec @6 为修复补上与审计相同的 `mrac_repository` 只读服务：固定 HEAD、源码与规则
来自原始仓库快照，当前候选则从 `mrac_spec_file` 读取。提示优先使用这些工具，
避免不必要地依赖原生 Shell/apply_patch；未禁用原生工具或放宽文件写入边界。
独立审计、严格 JSON、轮数预算、收尾条件及超时规则保持原配置。协议工具策略
随 Spec @6 固定，历史 @5 仍使用原工具配置；harness v3 新增诊断字段。
runner 代码身份哈希会变化，统计会与旧代码条件分组。
新模型实验需要重新提交以冻结新身份，受管旧运行不能直接套用新环境恢复。

## 验证

本地子进程测试覆盖完成后挂起、未完成工具、缺失或不一致的最终回复、工具超时、
真实进程树清理、失败事件、分块 UTF-8、凭证脱敏，以及 2/10 并行的完成与收尾。
这些验证不产生模型 API 调用，也不替代修复前后的真实模型对照实验。

事件语义参考 [OpenAI 非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode)。
