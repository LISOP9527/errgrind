# 统一 Error 调查与独立产品 agent fork

## Context

当前 Web 先把题目、用户思路和参考答案整理成三个字段，确认后创建
`pending-grill` Error，再启动单独的 Grill。用户实际做的是一段连续调查：交付真实
错题材料、核对系统理解、回忆当时的行为，再回答用于区分候选原因的问题。
这一交互分界来自当前实现，不是产品必须保留的分界。

ErrGrind 同时需要独立产品和 MCP 入口。独立产品需要控制界面、模型行为和研究条件；
MCP 需要在聊天宿主中降低使用门槛。两者应共享 Error、Evidence、诊断和干预的业务规则，
不能各自建立一套持久化权威。宿主转述用户输入或自行追问时，其作用也必须留下来源。

## Decision

- 新独立产品把 Record 与 Grill 合为围绕一次 Error 的连续调查。用户提交的首条文字或图片
  就建立可恢复的 episode；agent 可交替整理材料、核对事实和提出诊断问题。Teach 仍在
  本次诊断结束后开始，Drill 仍是可稍后发起的独立练习。
- 用户看到的 Error 是一段由模型起草、可由用户修改和确认的完整描述，不显示固定的
  `question`、`user_thoughts`、`reference_answer` 三栏。输入界面建议用户提供题目或图片、
  当时的作答或思路、后来知道的结果；未知部分保持未知，不要求按格式填写。诊断结束后，
  描述可用明确归属和不确定性措辞纳入本次诊断；用户核对亲历事实不等于认可机制推断。
- episode 分别保存原始输入与附件、公开 Error 描述的版本、带来源引用的诊断状态。
  这些可以共用物理存储，但必须有一个业务事实来源。确认描述是对整理结果的认可，
  不会把模型措辞变成用户当时的原话。原始错误作答、事后回忆、当前理解、模型推断、
  宿主转述和干预后观察保持可区分。
- 确认 authentic Error 锚点与结束 episode diagnosis 是两个业务提交点，前台无需显示为
  两个页面或两个 agent。诊断可以在澄清锚点时收集 Evidence，但在锚点得到核对之前不得
  定案。对已确认锚点的实质更正保留旧版本，并使依赖旧版本的诊断接受复核。
- 独立产品的 runtime 可以承担模型编排、上下文、会话和流式交互；共享 Core 负责来源、
  提交条件、状态转换、对外可见结果、恢复和 Drill 原子记录。现有 Python Application
  的内部编排与三字段 schema 可以迁移或替换，不作为新产品的固定接口。MCP 与独立产品
  使用同一套业务操作；UI 与宿主不直接拼装第二套 workflow。
- 以 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的
  [`00102833`](https://github.com/deepseek-ai/deepseek-harness/tree/00102833dfaee1da9f48a3a8eae9d34005a75218)
  为首个独立产品 fork 验证基础。它的 MIT 许可、Web 界面、图片附件、会话持久化、
  可组合插件和可选 subagent 为此次验证提供基础；选择它不等于承诺长期跟随上游，
  也不等于已经验证其 WebUI 适合 ErrGrind。

## Rationale

一段连续调查减少重复叙述和人为阶段切换。提前保存 episode 使图片、用户修改和
中断前的回答可恢复；分别提交锚点和诊断，则保留真实错误与机制推断之间的证据界限。
一个公开描述符合用户阅读和校对习惯；内部保留来源引用，才能审计图片识别、宿主转述、
诊断假设和后续干预。

首个 fork 优先继承现成 Web 交互、模型调用、图片和会话能力。上游 Web profile 默认
注入 coding agent 人格、workspace 和文件工具，需通过产品配置或改造移除。
上游图片通路会规范化图像；ErrGrind 所需的原始图片字节必须另行保存并关联，不能
把规范化副本冒充原件。这两项是技术验证内容，不预设只改配置就足够。

## Consequences

- 当前 Python/React 产品在迁移前继续按既有 Application、SQLite、三字段草稿和
  `pending-grill → pending-teach → done` 契约运行；本决策不要求立即迁移数据库或关闭旧 UI。
  新产品达到真实数学错题闭环后，目标是用 fork 的 UI 和 runtime 替换现有 WebUI，
  不长期维护第三套正式界面。
- 新 Core 不以单个线性状态表示全部进展：至少能分辨 episode 是否有已核对的 Error 锚点、
  诊断是否已结束、Teach 是否可继续，以及独立的 Drill attempt。已完成诊断保留原始结果；
  后来的事实更正通过版本和复核关系表达。
- Grill 的一次结果仍只是 episode-level diagnosis。Teach 和 Drill 的观察带干预语境，
  不能回填为 Error-time Evidence，也不能自动升级成长期 Pattern。
- 首批验证使用同一条真实数学错题，覆盖图片原件、描述多轮修正、受控且有区分度的
  追问、诊断不确定结果、中断恢复、Teach、Drill 判分与衍生 Error、手机端，以及禁用
  coding 工具后的权限面。记录需改动的上游包、配置层和自有 Core 代码；若关键路径只能
  靠大面积改写 WebUI 或无法维持原始证据与恢复，则重新比较其他基础。
- MCP 宿主对输入的转述、提示和追问必须按可观察到的来源保存。无法取得用户原件时，
  不得把宿主文本标为用户逐字 Evidence。

本决策取代[版本规划](../version-plan.md)中“fork 有意推迟到 V2、现在不选项目”的
时间安排；不改变数学 MVP、长期 Pattern 的证据门槛或现有代码的运行事实。
既有 [Application 工作流边界](2026-09-03-application-workflow-boundary.md)和
[Single-workspace WebUI](2026-09-10-single-workspace-webui.md)继续描述当前产品，
其中固定 Application 和三字段 Record 草稿不约束新 fork。
