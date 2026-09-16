# MRAC Case Maker

将用户指定的**已定稿 Spec 文件**和**任务实现前的仓库 commit**打包成一个 case。

## 规则

1. Spec 是唯一任务来源。上下文只用于定位文件和仓库，不从对话、记忆或其他文档提取、补充或改写内容。
2. 缺少独立完整的 Spec 或明确基线时，停止制作，列出缺项，让用户补齐；不自行生成 Spec 或猜测 SHA。
3. 原样按字节复制 Spec 为 `spec.md`，保留编码、BOM 和换行，计算文件的 SHA-256。Spec 必须是非空 UTF-8 文件。
4. 使用可拉取的仓库 URL 和完整 commit SHA，验证该 commit 存在且任务尚未实现。不自动提交、推送或切换用户工作区。
5. Spec 或基线改变时递增 case version，不静默覆盖旧版本。不生成专用 protocol 或额外提示词。
6. case 不绑定 protocol，不写入 `protocol` 字段。协议由 Bench 运行时选择；未显式选择时使用运行层默认值。

## 输出

只生成两个文件，放在用户指定的目录；默认使用 `c:/mrac-cases/<case-id>/`，可整体复制到 MRAC Bench 的 `cases/` 下。

```text
<case-id>/
  case.yaml
  spec.md
```

`case.yaml` 使用以下模板，替换所有占位符。id 与目录名一致，使用小写字母、数字和连字符；首次 version 为 1。

```yaml
id: <case-id>
version: 1
repository:
  url: "<repository URL>"
  commit: "<完整 commit SHA>"
task:
  file: spec.md
  sha256: "<spec.md 原始字节的 SHA-256，64 位十六进制>"
track:
  type: spec
```

交付前确认复制件与原文件逐字节相同，哈希一致，指定 commit 可拉取。纳入 Git 时，为产物目录设置 `-text` 属性以避免换行转换。当前 M1 不支持含 submodule 的仓库。

最后只报告 case 路径、固定 commit 和校验结果。固定 Spec 是任务输入；模型生成和修复的 spec 属于 run 产物。制作 case 不启动模型 benchmark。
