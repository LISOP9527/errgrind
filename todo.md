# 当前进度与验证记录

## 2026-09-09：本机 thin WebUI

- 新增可选 `web` extra：Flask/Jinja、Waitress 单进程线程服务、vanilla fetch；`python -m errgrind.web` 默认监听 `127.0.0.1:8765`。
- Error list/detail、三个字段文字与上传 OCR 校对、Grill start/answer/pause/resume、Teach start/reply/finish、Drill spec/draft/Judge 与错误派生跳转均通过 application。新增 `record_error` 校验、来源与 public 返回，CLI 复用。
- HTML 只显示公开内容；Markdown 与 KaTeX 资源本地提供，禁用 raw HTML 与可信 TeX 扩展。POST 使用 CSRF、一次性服务端 token 与进程内互斥；等待显示秒数及真实 Drill stage，失败保留草稿/已保存对话。
- 验证：集成最新 main 后完整离线回归 **249 项通过**；覆盖 provider/contract/persistence 错误映射、OCR 临时文件清理、Grill/Teach 回答失败后恢复、并发与重复 Judge、隐藏诊断/答案隔离及数学 HTML 转义。
- 浏览器 smoke：Chromium + 临时 SQLite + synthetic fake LLM，完成上传 OCR → 人工校对 Record → Grill 暂停/恢复/完成 → Teach 回复/结束 → Drill spec/draft → Judge → 新 Error；390×844 手机尺寸与 1280×900 桌面检查通过，公式正常、无横向页面溢出或脚本错误。未调用真实模型，未读取正式业务数据库。
- 限制：无账号系统，仅支持单进程；未判分 Drill 准备结果限当前进程。使用与安全说明见 [README](README.md)，不包含 provider compatibility、V2 或 Judge schema 改造。

## 2026-09-08：Drill 目标查询与简洁判题结果

- 新增 `/drills` 历史选择与 `/drills <ID>` 直接查询，展示已判分题目和五项目标机制字段；不限最近 20 条，缺失字段显示“未记录”。
- 查询经 Application 返回字段白名单，不调用模型、不写数据库；目标仍是来源 Error 的机制假设。
- 判题后仅显示“正确”或“错误”；完整判分记录与答错派生 Error 的原子保存语义保持不变。
- 验证：完整离线回归 **226 项通过**；真实数据库只读执行 `/drills 1` 的查询与渲染路径通过，未写入或调用模型；`git diff --check` 通过。
- 设计见 [Drill 目标查询与判题结果展示](design/decisions/2026-09-08-drill-target-query-and-verdict.md)。

## 2026-09-08：Drill 答题 OCR

- Drill 答题面板支持 F2 / Ctrl+O 添加图片，识别结果追加到可编辑草稿，支持重复添加；确认后才提交 Judge。
- Application 使用当前演练作答语义转录，不纠错、不混入参考解析；只保存最终答案文本。识别失败或中断保留草稿，取消作答不写入判分记录。
- 验证：完整离线回归 219 项通过，覆盖真实快捷键、草稿追加与中断、Application 转录和命令层确认；未调用真实 OCR API。
- 设计见 [Drill 答题图片输入](design/decisions/2026-09-08-drill-answer-ocr.md)。

## 2026-09-07：Codex 模型目录直连与 Astra 可见性

- `models()` 改为直接 GET Codex 后端目录，有效凭据下不启动 SDK/app-server；保留可见性、优先级、默认模型与 effort 元数据，目录失败继续允许手动输入。
- 根因实测：目录必须传 `client_version`；`0.147.0` 不返回 Astra，已验证的目录协议版本 `0.153.4` 返回 `gpt-6-astra`（最低 `0.153.0`）。目录协议版本与登录 SDK 分离，未来升级需重新验证。
- 目录只提取白名单元数据，丢弃 `base_instructions`、`model_messages` 等字段，避免重新引入 Codex Prompt。401 刷新最多一次，瞬态失败有限重试。
- 验证：真实 `/model` 查询路径已返回 Astra 及 low、medium、high、xhigh、max、ultra 六档 effort，测试时禁止 SDK 启动；完整离线回归 **186 项通过**，`git diff --check` 通过。

## 2026-09-07：Codex OAuth 生成直连 Responses

- 参考 Pi 的请求协议，普通、流式、JSON、图片转录直接请求 Codex Responses；不创建 Codex thread，不附加 agent Prompt、AGENTS、技能、工具或环境上下文。历史保留原生角色，SQLite 仍是唯一会话事实来源。
- 官方 SDK 保留登录、刷新与模型目录；生成只读现有文件登录中的 access token/account id，不另存凭据。401 最多刷新一次，只有无输出的瞬态失败重试，输出后中断不重放。钥匙串独占登录暂不支持直连。
- 严格 Schema 使用 `text.format`，图片使用 data URL；Codex 可选依赖补充 HTTPX SOCKS 支持，本机已安装，未修改代理设置。
- 验证：完整离线回归 **181 项通过**。真实 Sol 直连通过多轮历史（`24`）、严格 JSON（`DIRECT_OK`）、流式（`STREAM_OK`）与合成图片 OCR（`x+1=2`）；集成探针禁止 SDK 启动，确认生成不经过 app-server。未发送业务数据库中的错题。
- 设计与当前兼容边界见 [直连决策](design/decisions/2026-09-07-codex-direct-responses.md)。

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


## 2026-09-08：按阶段记录模型实际用量

- 默认启用不含正文/凭证的本地轮转 JSONL 日志，记录 action、stage、模型、输入/缓存/输出/推理用量、耗时与请求结果。
- 关联 Grill/Teach/OCR 与 Drill Spec/Draft/Judge 的调用，区分应用契约修复和 JSON 解析重试；缺失 usage 保留为未知。
- 增加 `python -m errgrind.usage_report --days 7` 本地汇总，保留逐字段统计覆盖次数；当前不调整模型、Prompt 或重试策略。
- 验证：仅含本次日志变更的暂存版本通过 206 项离线测试；包含既有目录改动的工作区通过 211 项。`git diff --check` 通过，测试日志隔离在临时目录，未调用真实 API。
