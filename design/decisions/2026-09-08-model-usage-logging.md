# 模型用量日志：先测量，再优化

## Context

界面可见回复不能代表完整的模型用量。Grill 有隐藏诊断输出及随轮次增长的状态输入；Drill 有
Spec、Draft、Judge 三阶段；JSON 修复和网络重试还可能重复调用。此前 provider 只返回文本，
没有保留服务端 usage，无法判断主要消耗来自输入、推理、输出还是修复。

## Decision

增加默认启用、仅保存元数据的本地 JSONL 日志。保持现有 Prompt、模型选择、诊断和重试策略。
不根据字符长度估算 token，不推算价格，不用缺失字段冒充零消耗。

### 文件与查看方式

- 默认文件：`~/.local/state/errgrind/usage.jsonl`；设置了 `XDG_STATE_HOME` 时使用其下的
  `errgrind/usage.jsonl`。
- 每个文件上限 5 MiB，保留 3 个轮转备份（`.1` 最新，`.3` 最旧）。超出保留窗口的记录会被替换。
- 新目录权限为 `0700`，日志文件为 `0600`；Linux 使用文件锁保护多进程追加和轮转。
- 写入失败只发出一次不含路径或底层异常的提示，不阻断学习流程或掩盖 Ctrl+C。
- 离线测试应将 `XDG_STATE_HOME` 指向临时目录，避免模拟数据进入正式用量统计。

在项目目录运行，无需登录，不调用模型，也不打开业务数据库：

```bash
.venv/bin/python -m errgrind.usage_report --days 7
```

省略 `--days` 汇总所有保留的日志。报告按 action、stage、provider、model、reasoning_effort
分组，输出已报告 token 的合计和每项统计的覆盖次数；损坏或不完整 JSONL 行会被跳过并计数。
报告包含轮转文件。没有历史日志时返回空分组，不制造用量。

### 记录边界与字段

每次 provider 请求尝试结束时写一条 `model_attempt`，包括失败、流中断和用户取消。
日志不记录 Prompt、题目、用户原话、模型正文、诊断账本、图片/路径、请求体、响应体、
URL、凭证、账号标识或原始异常信息。只记录内部选择的标签、ID、非负数值与异常类型。

| 字段 | 含义 |
|---|---|
| `timestamp`, `duration_ms` | UTC 开始时间与本次尝试耗时；不包含应用层修复或请求间退避等待 |
| `operation_id`, `attempt_id`, `attempt_index` | 一次应用操作的关联 ID、唯一尝试 ID、操作内从 1 开始的尝试序号 |
| `action`, `stage`, `error_id` | 操作、阶段、已有 Error ID；录题 OCR 尚无 Error ID |
| `provider`, `model`, `reasoning_effort` | 当前请求使用的配置；未显式指定 effort 时为 null，不猜测服务端默认值 |
| `repair_attempt`, `json_attempt` | 应用契约修复与 provider JSON 解析尝试，均从 1 开始；大于 1 表示重试/修复 |
| `status`, `error_type`, `http_status` | 请求尝试成功、失败或中断；只保存异常类型和可获取的 HTTP 状态 |
| `input_tokens`, `cached_input_tokens` | 服务端报告的输入及缓存命中输入 |
| `output_tokens`, `reasoning_tokens`, `total_tokens` | 服务端报告的输出、推理及总量；采用下方 provider 口径 |
| `usage_reported` | 是否至少收到一项有效用量数值；不代表所有字段完整 |
| `sdk_max_retries` | OpenAI 兼容客户端底层 SDK 的重试设置；其他 provider 为 null |

正常恢复回放、保存退出、查看与删除不生成模型日志。Grill 每轮为 `grill/turn`，Teach 每轮为
`teach/reply`，Drill 为 `drill/spec`、`drill/draft`、`drill/judge`，整图 OCR 为
`ocr/transcribe`，录题字段 OCR 为 `record/ocr_question`、`record/ocr_user_thoughts`、
`record/ocr_reference_answer`。

一个 `operation_id` 覆盖一次 application 方法调用及内部重试。Drill prepare 的 Spec/Draft
共用 ID；稍后提交作答的 Judge 使用新 ID，并记录 source Error ID。它不是长期会话 ID，也不是
Drill attempt 数据库 ID。直接调用 provider 且没有业务 scope 时标为 `unscoped/generation`。

### 用量来源与计数口径

- Codex 从 Responses SSE 完成、失败或未完成事件的 `response.usage` 读取。只提取白名单
  数字，不保存 response。`cached_input_tokens` 和 `reasoning_tokens` 是输入/输出的子项，
  不能再加到 total 上。
- OpenAI 兼容接口从 `usage` 读取，包括 `prompt_tokens_details.cached_tokens`、
  `completion_tokens_details.reasoning_tokens`，兼容 DeepSeek 的
  `prompt_cache_hit_tokens`。流式调用请求 `stream_options.include_usage=true`，接收无正文的
  usage chunk；不为获取 usage 额外调用模型或重新生成。兼容端是否返回这些字段取决于服务端。
- Gemini 从 `usageMetadata` 读取。`output_tokens` 保留 `candidatesTokenCount`，
  `reasoning_tokens` 保留 `thoughtsTokenCount`；Gemini 的候选输出不包含单独报告的推理字段，
  与 Codex/OpenAI 的输出口径不同。总量直接使用 `totalTokenCount`，不自行重算。
- 流中多次出现 usage 时更新累计快照，不把相同快照反复累加。缺失的 token 字段保存为 null。
- 没有收到末尾 usage 的中断、429、网络错误等仍留下尝试记录，消耗未知。进程被强制杀死时
  `finally` 无法执行，因此可能完全缺少该次记录。
- OpenAI SDK 内部重试未展开成单独日志；一条日志对应外层 `create()` 尝试，不能将日志行数
  宣称为精确 HTTP 请求数。保留 `sdk_max_retries` 说明这一限制，不调整原来的 SDK 重试策略。
- `status=success` 表示 provider 正常返回，不代表业务 JSON 契约已通过或数据库保存成功。
  不合法 JSON 的生成也会计入用量；后续修复通过尝试序号识别。
- 汇总的 token 是**已报告用量的合计**。`reporting_attempts` 标明每个字段的覆盖次数，
  `missing_usage_attempts` 标明完全没有用量的请求。不能将其视为完整账单或费用。

字段口径参考：[Gemini UsageMetadata](https://ai.google.dev/api/generate-content#UsageMetadata)、
[DeepSeek 缓存字段](https://api-docs.deepseek.com/guides/kv_cache/)。
Codex/OpenAI 字段映射由离线 SSE/SDK 响应 fixture 验证。

## Rationale

Application 确定业务归属，provider 记录真正发生的生成尝试。用 ContextVar 传递标签，避免
向 Prompt 插入日志元数据，也避免改变公共结果对象。日志与业务 SQLite 独立，磁盘故障不能
改变用户输入持久化、诊断恢复或 Drill 原子记录语义。完整内部输出继续只在现有业务边界使用。

## Consequences

后续先收集真实使用样本，比较各阶段已报告的输入、缓存、输出、推理和修复开销，再决定优化。
本次不压缩上下文、不减少诊断字段、不更换模型、不调整 reasoning effort 或重试次数。
历史 token 消耗无法补算。日志只覆盖保留窗口，实际服务端 usage 可用性仍需后续真实使用确认。
