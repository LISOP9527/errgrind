# OCR 作为需人工校对的录题入口

后续扩展：[在录题字段内整合图片识别](2026-09-06-record-field-image-input.md) 将字段图片输入
加入 `/record`；本文的整图识别契约继续用于兼容 `/ocr [路径]`。

## Context

数学错题常来自截图、试卷照片和手写草稿。直接要求用户逐字录入会增加阻力，但数学 OCR
会混淆分数、上下标、根号、几何标签和手写符号。若未经确认就入库，错误文本会继续污染
Grill 的 episode-level diagnosis，以及 Teach 和 Drill 使用的机制判断。

## Decision

- 新增 `/ocr [图片路径]`，支持 PNG、JPEG、WebP，单张图片最大 20 MB。
- OCR 一次提取 `question`、`user_thoughts`、`reference_answer` 三个字段；输出契约和识别规则
 统一写在 `prompts/ocr.md`。
- 三个字段必须在写入前依次展示给用户编辑。题目和用户思路沿用 `/record` 的必填规则，参考答案可空；
  任一步取消都不创建数据库记录。
- 图片能力留在 provider 适配器边界：Codex 使用 Responses 的 `input_image` data URL
  （由 [2026-09-07 直连决策](2026-09-07-codex-direct-responses.md) 替代原 `LocalImageInput`），
  Gemini 使用 `inline_data`，OpenAI 兼容 provider 使用标准 `image_url` 消息。
- MVP 只保存用户确认后的文本，不复制或持久化原图，现有数据库 schema 和后续工作流保持不变。

## Rationale

- 人工校对是数学 OCR 的必要质量边界，能避免“识别成功”被误当作“数学内容正确”。
- 统一 OCR 契约让 CLI 不依赖具体 provider 的图片协议，后续更换模型时不影响数据库和状态机。
- 不保存原图可避免引入附件生命周期、隐私、磁盘清理和迁移问题。

## Consequences

- OCR 减少录入工作，但不能替代用户确认；模糊字符会用 `[无法辨认]` 暴露出来。
- DeepSeek/OpenCode 是否支持图片取决于用户选择的具体模型，失败时会显示 provider 返回的中文错误，
  不会产生半条记录。
- 如果未来需要回看原图或重新识别，应单独设计附件表、隐私策略和清理规则，而不是把图片塞进
  `error_records` 文本字段。
