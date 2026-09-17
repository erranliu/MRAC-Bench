# 外部模型 Provider

单次 runner 和受管批次均支持为 `codex exec` 显式选择 Responses provider。Codex 仍负责工具调用与执行环境，MRAC runner 仍负责协议和预算，调度核心不包含 GLM 或其他模型的特殊分支。

## 配置

一个 provider YAML/JSON 文件描述公开的连接参数：

```yaml
schema_version: 1
id: zai
name: Z.AI
base_url: https://api.z.ai/api/v1
wire_api: responses
env_key: ZAI_API_KEY
model_catalog_file: glm-models.json
```

- `id` 是稳定的自定义标识，只允许字母开头的字母、数字、下划线和连字符。`openai`、`ollama`、`lmstudio`、`amazon-bedrock` 保留给 Codex 内置 provider。
- `base_url` 支持 HTTP(S)，不接受 URL 内嵌认证信息、query 或 fragment。localhost HTTP 可用于本地兼容服务。
- `wire_api` 当前只支持 `responses`。Chat Completions 地址不能直接作为 Codex Responses 地址使用。
- `env_key` 是环境变量名称，不是密钥本身。省略或设置 null 表示无需 Bearer 凭证，例如本地服务。
- `model_catalog_file` 相对 provider 文件所在目录解析。也可用 `model_catalog: {models: [...]}` 内嵌目录，二者不能同时使用。目录可省略；提供时校验模型 slug、唯一性及显式 reasoning effort。
- 拒绝 `api_key`、`experimental_bearer_token` 等内联凭证或其他未知字段；自定义 provider 统一设置 `requires_openai_auth=false`。

随附 [Z.AI 配置](../examples/providers/zai.yaml) 和 [GLM-5.3-Flash 模型目录](../examples/providers/glm-models.json)。endpoint 来自 [Z.AI Codex 接入文档](https://docs.z.ai/devpack/tool/codex)，模型代码来自 [GLM-5.3-Flash 文档](https://docs.z.ai/guides/vlm/glm-5.3-flash)。不同服务/区域/套餐应使用其实际支持的 Responses 地址与凭证。

## 单次运行

在本机终端设置 `ZAI_API_KEY`，然后执行：

```powershell
uv run mracbench run --case psf__requests-1963 --protocol spec-mrac-v2 --model glm-5.3-flash --reasoning-effort high --provider-file examples/providers/zai.yaml
```

受管单次使用相同选项：

```powershell
uv run mracbench run --managed --case C000001 --protocol spec-mrac-v2 --model glm-5.3-flash --reasoning-effort high --provider-file examples/providers/zai.yaml --bench-home C:\mrac-data
```

没有 provider-file 时维持原有内置 OpenAI 行为。恢复不接受另一个 provider-file，自动读取原 run 的快照；改变 endpoint、模型目录或 provider 身份需要新建实验。

## 混合批次

在批次顶层声明 providers，每个 model_configs 条目通过名称选择：

```yaml
providers:
  zai:
    file: providers/zai.yaml

# groups 中的 model_configs
model_configs:
  - provider: openai
    model: gpt-5.6-luna
    reasoning_effort: high
    resource_group: openai-account
  - provider: zai
    model: glm-5.3-flash
    reasoning_effort: high
    resource_group: zai-account
```

完整可提交示例见 [batch-providers.yaml](../examples/batch-providers.yaml)。file 相对批次 YAML 所在目录解析，其内部 id 必须与声明名相同；也可直接在 providers.<名称> 下内嵌连接参数。未指定 provider 等价于 openai，其他未声明名称会在提交前被拒绝。

```powershell
uv run mracbench batch submit examples/batch-providers.yaml --request-id compare-providers-001 --bench-home C:\mrac-data
uv run mracbench orchestrator serve --total 2 --group openai-account=1 --group zai-account=1 --bench-home C:\mrac-data
```

**凭证应设置在启动 serve 的进程环境中。** 只在 submit 的终端设置密钥，已经运行的服务不会获得它。服务可在无凭证时接受并保存配置；缺少所选 env_key 的任务执行时明确返回 PROVIDER_ERROR，不回退到 OpenAI，也不自动重试。补齐环境后重新启动服务，再按该协议允许的动作 recover 或 rerun；支持恢复的 checkpoint 保持原 provider 和原预算。

resource_group 仍由用户明确配置。不同 provider 可在同一批次并行，同一模型名称在不同 provider 下具有不同运行身份；报告增加 Provider 列。

## 快照、凭证与调用证据

提交时把 provider 与完整模型目录解析成值对象，复制到冻结 bundle/provider.json，并纳入输入清单和 run_spec 摘要。单次运行保存 input/provider.json；run.yaml 的有效配置及 result.agent.provider 记录 provider 身份。恢复在任何模型调用、checkpoint 改动和预算扩展前核验 adapter 与保存配置一致。

仍使用 `--ignore-user-config`，通过每次调用的 `-c model_provider=...` 和 `-c model_providers.<id>.*=...` 注入配置。自定义模型目录写入 raw/<stage>/model-catalog.json 后显式指定路径，不需要改动用户的全局 Codex 配置。

凭证值仅在启动调用时从环境读取，不进入 argv、provider 配置、计划或运行元数据。所选凭证若被子进程输出，stdout/stderr 在写入证据前进行流式脱敏；最终回复先落到临时文件，脱敏后进入 final.txt。环境变量名称会保留，值的轮换不会修改输入摘要。

PROVIDER_ERROR 表示本地 provider/凭证配置错误，未启动 CLI 时不计 audit invocation。服务认证、网络和输出解析失败仍遵循原来的 AGENT_ERROR/TIMEOUT/解析错误语义，不改变协议收敛判断。沙盒依旧由阶段决定。

## 验证

新增测试覆盖配置拒绝、模型目录、并行路由、provider 文件修改后的快照稳定性、恢复身份、缺失凭证、跨块日志脱敏和超时收尾。

可选原生 CLI 传输测试使用本地 Responses 桩，不调用外部模型：

```powershell
$env:MRAC_CODEX_WIRE_SMOKE = '1'
uv run pytest -q tests/test_provider_wire.py
```

已用本机 Codex CLI 0.145.0 验证示例模型目录、provider 参数、Bearer 环境凭证及 /v1/responses 路由。该检查验证客户端集成，不等同于使用真实 Z.AI 凭证调用 GLM 服务。

2026-09-17 验证记录：完整 `uv run pytest -q` 为 276 passed、1 skipped（637.27 秒）；跳过项是上述 opt-in 原生 CLI 测试，显式启用后单独执行为 1 passed（4.75 秒）。Ruff、格式检查和 sdist/wheel 构建通过。现有协议的预算和收敛规则未变；真实 GLM 服务调用需要操作者在运行服务的环境中配置有效凭证。
