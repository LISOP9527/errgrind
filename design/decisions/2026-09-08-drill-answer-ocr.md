# Drill 答题图片输入

## Context

数学演练的答案与演算常写在纸上。Drill 答题需要复用录题已有的图片输入能力，让用户
校对识别文字后再提交判分，并区分当前演练作答与对过去 Error 思路的回顾。

## Decision

- Drill 答题面板保留题目展示，支持 F2 / Ctrl+O 添加图片；路径指向运行 ErrGrind 的机器。
- OCR 只忠实转录当前答案与解题思路，不解题、不纠错、不混入参考解析。
- 识别结果追加到已有草稿，可编辑并重复添加图片；用户按 Enter 后才提交 Judge。
- 选图取消、识别失败或识别中断均保留草稿；取消答题面板不判分、不写入 attempt。
- Application 负责转录 Prompt、模型调用和文本校验；frontend 负责选图、草稿和确认。
- 沿用 PNG、JPEG、WebP 与单图 20 MB 限制，只保存最终提交的文本，不保存原图。

## Rationale

复用字段图片输入的操作方式，减少手工转写，同时保留数学 OCR 必要的人工校对边界。
单独标明当前演练作答，避免将其表述为过去 Error 的回顾性 Evidence。

## Consequences

确认后的答案沿用原有 Drill 判分和原子记录流程；答错派生的 Error 继续保留 Drill 来源。
OCR 草稿本身不产生判分记录，也不证明长期 Pattern 或未来错误减少。
