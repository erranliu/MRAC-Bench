# 调用完成、超时与 Spec 文件工具

`codex_exec` harness v2 将 agent 回合完成与 CLI 进程退出分别监督。每次调用的
`raw/<stage>/invocation.json` 保存 `harness_version: 2`、实际命令和执行条件。
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

审计与修复协议模板、独立审计和审计轮数规则保持原配置。工具集和执行环境提示
属于新的 harness 条件；runner 代码身份哈希会变化，统计会与旧代码条件分组。
新模型实验需要重新提交以冻结新身份，受管旧运行不能直接套用新环境恢复。

## 验证

本地子进程测试覆盖完成后挂起、未完成工具、缺失或不一致的最终回复、工具超时、
真实进程树清理、失败事件、分块 UTF-8、凭证脱敏，以及 2/10 并行的完成与收尾。
这些验证不产生模型 API 调用，也不替代修复前后的真实模型对照实验。

事件语义参考 [OpenAI 非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode)。
