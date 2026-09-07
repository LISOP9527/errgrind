# 当前进度与验证记录

## 2026-09-06：录题字段内图片输入

- `/record` 的题目、思路和参考答案窗口分别支持添加图片；识别文字追加到草稿，可继续编辑并重复添加图片，最终保存为一条 `pending-grill` Error。
- 字段 OCR 使用独立 `text` 契约，思路图、答案图无需包含题目。Application 组装字段 Prompt 并校验草稿，三类 provider 复用现有图片传输。
- 成功使用 OCR 的记录保留 `origin=ocr`，纯文字录入为 `record`；原图不入库。旧 `/ocr [路径]` 保留整图识别入口。
- F2 / Ctrl+O 添加图片，识别后回到当前字段继续编辑；失败或中断保留已有草稿。`/record` 与 `/ocr` 共用字段编辑和必填校验。
- 验证（2026-09-07）：取消代理环境变量后完整离线回归 167 项通过，包含真实 prompt_toolkit 按键输入、重复图片、草稿保留、字段路由、取消不入库及三类 provider 的格式测试；`git diff --check` 通过。未调用真实图片识别 API。

## 2026-09-06：Codex 模型选择与 reasoning effort

- `/model` 在选择 Codex 模型后配置 effort；新增 `/effort` 与 `/config` 入口，保存 `reasoning_effort`，并传给普通、流式、JSON、OCR 请求。默认不覆盖 Codex 自身设置。
- 模型列表原本已通过 SDK 动态查询；现保留 effort 元数据，始终提供手动模型 ID 入口。目录失败时给出提示，未知模型的 effort 可手动输入。
- 本机实际目录查询未返回 Astra（包括隐藏模型、无下一页）；实际 Sol 元数据正常包含 low、medium、high、xhigh、max、ultra。未调用真实生成 API。
- 配置取消保留旧状态；保存失败关闭新 client。完整离线回归 141 项通过；真实目录读取另行验证元数据解析。`git diff --check` 通过。

## 2026-09-06：Structured Grill contract 与回顾性诊断边界

- Review 修复：所有 Teach 返回值复用公开 Error 脱敏；Grill 解析、语义校验与 API 失败不向用户回显内部模型响应。repair 仍只在临时请求中进行。
- partial 恢复在请求中使用当前 Grill Prompt，保留数据库中的历史 system message 和 Evidence message index；没有区分度的真实回答仍记录 observation，首轮 JSON 示例可以通过本地 validator。
- Gemini `chat_json` 通过 `responseJsonSchema` 发送严格 JSON Schema；未在本轮调用真实生成 API。
- `finish_supported` 必须有 Evidence 支持 best hypothesis，并保留非空的 `what_would_change_judgment`；最新用户回答的 Evidence 必须关联当前 Probe。
- ordinary Drill 只接收 legacy NULL state 或 structured `supported` state；完整检查嵌套 Evidence/Probe/predictions、ID 引用、支持 best 的 Evidence 与可证伪条件，`undetermined` 和损坏 state 保守排除。limit 在筛选后生效；reasoning/variant 完成的合法记录均有 runtime 集成测试。
- Grill Prompt 与设计明确以 authentic Error-time failure mechanism 为目标，retrospective reconstruction 是 noisy Evidence；不追踪 post-interference 因果状态链，允许 incidental learning effect。
- 验证：取消 SOCKS proxy 环境变量后完整 offline suite 为 129 项通过；未取消时受环境缺少 `socksio` 影响，OCR OpenAI-compatible 测试失败。产品依赖未改动。`git diff --check` 与 Python compile check 通过；未调用真实模型 API。

## 2026-09-05：结构化 episode Grill diagnosis 与诊断变式 Probe

- Grill 为每个 Error 保存可审计的 hypotheses、grounded Evidence 和 Probe state；模型每轮只输出 delta，Application 负责确定性 merge。
- `reasoning_question` 与 `variant_problem` 成为显式 Grill Probe；variant 回答不进入普通 Drill ledger，也不自动派生 Error。
- 数据库 Schema 升级为 v3；旧记录不回填，旧 completed/partial Grill 保持兼容。
- Grill structured output 成功校验并持久化后才通过兼容 callback 展示一次，因此不再是真 token streaming。
- 验证：去掉本机代理变量后运行完整 offline suite，104 项测试全部通过。

## 2026-09-03：Core/Application 工作流拆分

- 新增 `errgrind.application.ErrGrindApplication` 作为 UI 无关的应用边界，Grill、Teach、Drill 的 Prompt 组装、LLM 调用、契约校验和持久化不再由 CLI 编排。
- CLI 保留输入循环、Ctrl+C / EOF 交互、Rich / prompt_toolkit 渲染、popup 与进度提示；`record`、OCR、status 和 config 本次有意保留在 CLI / bootstrap 边界。
- Grill 保持 partial 会话恢复和完成后只读；Teach 保持首次保存进入 `done` 且可继续对话；Drill 保持 Spec 白名单隔离、Judge provenance 和原子 attempt / lineage 写入。
- 验证：`.venv/bin/python -m unittest discover -s tests -v`，`Ran 79 tests ... OK`。未调用真实 API，测试只使用临时 SQLite 数据库。

## 2026-09-02：暂缓 Pattern Observation 校验

- 当前 MVP 继续直接使用 `grilling_summary` 表达本次 Grill 的 episode-level diagnosis，不新增 Observation schema、Evidence quote 校验、持久化或 review 流程。
- 结构化 Pattern Observation 保留为未来设计提案，只有用户再次确认进入该阶段后才实现。

## 2026-09-01：Judge provenance Schema v2

- `drill_attempts` 保存 Judge provider/model、未 format Judge 模板 SHA-256 和 canonical Judge schema SHA-256；v1 旧 attempt 迁移为 `unknown`。
- `/status` 明确删除 Error 会改变累计数，不同 Judge 版本不可直接比较。
- 验证：`PYTHONPATH=/root/errgrind-sol-lab /root/errgrind/.venv/bin/python -m unittest discover -s tests -q`，`Ran 72 tests ... OK`。

## 2026-09-01：Evidence provenance 与 Drill Action ledger

- `error_records` 增加来源与结构化 source 字段；旧记录迁移为 `unknown`，record/OCR/Drill 衍生记录保留来源。
- 新增 `drill_attempts` 原子账本，保存 source Error、完整 DrillSpec、作答、判分和衍生 Error。
- SQLite Schema 记录 `user_version`，旧程序会拒绝写入更高版本的数据库。
- 注意：Drill 正确率是干预记录，不是未来真实 Error 减少的证明；Pattern 一等实体仍待后续设计。
- 验证：`PYTHONPATH=/root/errgrind-sol-lab /root/errgrind/.venv/bin/python -m unittest discover -s tests -v`，`Ran 72 tests ... OK`。

## 2026-08-31：OCR 录题与 Codex 真实全流程

已实现：

- `/ocr [图片路径]` 支持 PNG、JPEG、WebP（最大 20 MB），按文件内容签名校验，不信任扩展名。
- Codex 使用官方 `LocalImageInput`；Gemini 使用 `inline_data`；OpenAI 兼容 provider 使用标准
  `image_url` data URL。
- OCR 严格拆分题目、学生思路、参考答案，三个字段逐项预填供用户校对；任一步取消都不入库。
- 数据库只保存校对后的文本，不保存原图，后续状态机与 `/record` 一致。
- Codex DrillSpec、题目草稿、判分增加各自的严格 JSON schema；修复通用工作流词和数学函数名被
  源文本泄漏校验误判的问题。

离线验证：

```text
.venv/bin/python -m unittest discover -s tests -v
Ran 61 tests ... OK
```

真实验证使用已登录的 Codex `gpt-5.6-sol` 和隔离数据库
`/tmp/errgrind-real-e2e-y6wz9j2f/errgrind.db`，没有写入正式错题库：

1. 一张带轻微倾斜和扫描噪声的数学错题 PNG 成功识别题目、学生错误演算、参考答案；公式转成
   LaTeX，三个区域没有混淆。真实 `/ocr` 校对后创建 `pending-grill` 记录。
2. 由易到难完成 5 组真实 Grill → Teach：百分比变化基准、根式方程增根、条件概率样本空间、
   不可导临界点、反向使用级数判别法。每组经 2–3 次学生回答后形成可迁移机制候选；长期 Pattern
   仍需跨 Error 或后续行为 Evidence 支持。随后 Teach
   保存并进入 `done`。
3. `/drill` 正确分支生成几何命题证明/反例题，参考级作答被判正确，记录数保持 5。
4. `/drill` 错误分支识别出缺少条件验证的作答，记录数从 5 增至 6，新记录状态为
   `pending-grill`。
5. 最终隔离库状态：`done=5`、`pending-grill=1`、`pending-teach=0`、`total=6`。

真实测试发现并修复：

- 原 Codex `chat_json` 的宽松 schema 被真实 API 以 `invalid_json_schema` 拒绝；现由业务调用传入
  字段完整且 `additionalProperties=false` 的严格 schema。
- 源文本泄漏校验曾把 `Error Pattern`、`sqrt` 等通用词当作原题指纹，导致 DrillSpec 三次重试
  后失败；现只拦截真正具有题目特异性的标记和表达式。

尚未宣称：Gemini、DeepSeek、OpenCode 的图片 OCR 只完成离线请求格式回归，未在本轮发送真实
图片请求；其中 DeepSeek/OpenCode 是否支持图片仍取决于具体模型。
