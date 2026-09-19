# 错题录入与图片输入

`/record` 是统一录题入口。Web 接受文字和零或多张 PNG、JPEG、WebP 图片，并把每一轮
文字补充、当前可编辑草稿和本轮图片作为一次多模态 user turn 交给当前模型，更新
`question`、`user_thoughts`、`reference_answer` 结构化草稿。模型没有从材料中得到的
`user_thoughts` 必须保持为空，不得根据题目推断。

## Web Record 的迭代调整

Record 不是一次提交即结束的整理请求。第一次输入和之后每次调整都遵循同一条路径：

1. 前端发送新的自然语言补充、当前预览中的三个可编辑字段以及当前选择的图片。
2. Application 保持严格的 JSON 对象和字符串字段契约；字段值暂时为空属于语义上的
   incomplete draft，不是模型输出契约失败。模型应保留没有被本轮明确修改的当前字段，
   最新用户明确的纠正覆盖旧内容。
3. 成功后返回更新后的可编辑预览和确定性的 ready/incomplete 状态。原始补充输入被清空，
   用户可以继续输入下一轮；图片仍作为当前待确认图片，直到最终保存或用户替换它们。

文字 `question` 是确认保存前的通常必填字段；如果当前草稿已有一张或多张原始图片，
则允许将纯图片作为题目完成确认，文字字段保持为空。参考答案可选，`user_thoughts` 仍可缺失，
并且不能由模型为了完整性自行补写。中间草稿字段可在页面/sessionStorage 中恢复，但已上传的
图片会立即写入服务端 pending attachment，刷新或模型失败后仍可继续使用；只有用户确认时才调用
`record_error`，由它把 pending 图片原子归属到新 Error，并执行文字或图片输入的最终校验。

模型返回的草稿仍然只是可编辑预览：用户可以多轮补充和手动修订，必须校对并确认后才创建
`pending-grill` Error。
没有参考答案或用户思路时可以保持为空；确认时不要求模型先转录图片，也不把 OCR 文本插入
编辑器。已确认的原始图片作为 Error 的 initial attachment 保存，供后续 Grill 直接使用。

## Web 对话和 Drill

- Grill、Teach 和 Drill 使用统一的 `+ 图片` 附件入口。文字可以为空，但至少要有文字或图片。
- 图片随具体 Error conversation turn 或 Drill attempt 保存；模型失败后，刷新页面可以重试而
  不必重新上传。对话消息直接显示已保存的图片附件，不显示模型转录。
- Grill 的图片 Evidence 使用 durable attachment reference，`quote` 必须为空；真实文字
  Evidence 仍必须通过 exact-substring 校验。图片不是模型生成的用户逐字引文。
- Drill Judge 直接接收文字和图片。答错派生 Error 时复制相关附件，因此不会把图像答案降级为
  `[image]` 文本；DrillSpec 和参考答案仍不会在提交前进入 Web 表单。

## 边界和兼容

Frontend 只负责选择文件、收集表单和展示草稿/进度；`ErrGrindApplication` 负责加载图片、
保存附件、恢复消息、调用模型和维护状态。Provider adapter 将同一个 neutral multimodal
message 映射成各自的请求协议。所有图片继续通过 `load_image` 的真实 bytes、MIME 和 20 MB
限制校验；服务端使用固定临时文件名，不信任浏览器文件名。

SQLite `attachments` 表保存 BLOB、MIME、SHA-256 和归属关系；conversation JSON 只保存附件
ID，不塞入 base64，也不写入模板或隐藏浏览器状态。Error 删除时由外键清理其附件，Drill
pending/attempt 使用独立 provenance 关系。旧 CLI `/ocr`、字段 OCR 和 Drill OCR 方法可以
暂留兼容，但 Web 不再提供或调用这些 route。
