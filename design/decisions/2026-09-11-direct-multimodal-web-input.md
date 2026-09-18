# 直接多模态 Web 输入

## Context

Web 的图片输入曾经先调用 OCR，再把模型生成的转录文字放回编辑器。这会让一次
Record 产生额外模型调用，也把模型视觉理解错误伪装成用户原话；Grill、Teach 和
Drill 还无法在 provider 失败后可靠地重用同一张图片。

## Decision

- Record 将文字和零或多张已验证图片放进同一个 draft model call。结构化
  `question`、`user_thoughts`、`reference_answer` 仍返回为可编辑预览，用户确认前不创建 Error。
- Grill、Teach 和 Drill 的 Web composer 使用统一 `+ 图片` 入口。图片作为当前用户 turn
  的直接多模态内容发送；空文字只在有图片时允许。页面和公共时间线不显示 OCR 转录。
- 以 SQLite `attachments` BLOB 关系表保存验证后的 bytes、MIME、SHA-256 及 Error、消息、
  Drill attempt 或 pending key 归属。普通 conversation JSON 和模板只保存/读取 attachment ID，
  不写入 base64 或浏览器隐藏状态。Grill/Teach 在模型调用前保存用户 turn；Drill 在 Judge 前
  保存 pending attachments，判分成功后归属 attempt，答错时为衍生 Error 复制一份由该 Error
  级联管理的附件。
- Application 只使用 provider-neutral `MultimodalMessage` / `ImagePart`。Codex 映射为
  Responses `input_text` / `input_image`，OpenAI-compatible 映射为 `text` / `image_url`，
  Gemini 映射为 `text` / `inline_data`。所有图片继续经过既有 `load_image` 类型与大小限制。
- Grill image Evidence 使用持久化的 `initial_attachment:N` 或
  `message:N:attachment:M` source ref，`quote` 必须为空。真实文字 Evidence 的精确 substring
  校验保持不变；模型视觉转录永远不是 verbatim user quote。

## Rationale

SQLite 与业务记录同库，能在本地单进程 Web 刷新和 provider 失败后原子恢复，也能用外键级联
清理 Error/attempt 附件；ID 关系避免把大块 base64 塞入普通会话 JSON。直接多模态请求让
provider 看到用户实际上传的 artifact，保留 Record 的人工确认边界而不增加 OCR 调用。

## Consequences

确认 Record 后原图会成为 Error provenance 的一部分；图片不会在公共 HTML 中回显缩略图，只有
紧凑附件标记。pending Drill 的字节依赖当前 Web 进程持有的 key；若进程重启，未判分的临时
练习仍需重新 Prepare。旧 CLI OCR 方法和 prompt 可暂留，但 Web 不再调用 OCR route 或 transcribe。
