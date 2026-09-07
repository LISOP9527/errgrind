# 版本规划 / Version Plan

本文安排 ErrGrind 从当前数学 MVP 到 V1、V2 的开发顺序，并保存有意推迟的方向。
这是 roadmap，不是 ADR、已完成清单或冻结架构；版本表示产品阶段，不承诺发布日期。
已有设计与决策继续约束实现，实际进度见 [todo](../todo.md)，设计入口见[设计索引](README.md)。

## 开发顺序

```text
Now / V1 Core
    Grill → Teach → Drill/Judge reliability
                  ↓
V1 Product / UX
    UX + WebUI + MCP + real-world usability
                  ↓
V2
    Long-term State / Pattern State
    Policy
    More Actions / Evidence Sources
    Multi-domain / Multi-subject expansion
    Dedicated agent harness / fork
```

V1 Core 与 V1 Product 是同一个 V1 方向的连续阶段：先把固定工作流做可靠，再优先降低真实使用的成本。
必要的可用性交互可以随 Core 一起改进，但复杂 Policy 不应抢在核心闭环与 UX 验证之前。
V2 的五类方向是待研究的扩展，不要求同时落地，也不预先决定它们的内部算法或技术选型。

## 产品定位与 Evidence 边界

ErrGrind 是 learning/error debugger，不是通用 AI tutor。核心对象是与真实 Error 相关、
可能导致未来 Error、可以被 Evidence 支持、削弱、验证或修正的 failure mechanism / Pattern hypothesis。
它不追求完整用户画像。已有[核心原则](core-principles.md)中的“用户模型”和
[长期架构](long-term-architecture.md)中的 Thinking Model，应按这一范围理解。

当前最重要的产品闭环是：

```text
Error → Grill → Teach → Drill → new Error / Evidence
```

- Grill 是 active diagnosis：区分导致真实 Error 的竞争解释，不是 Socratic tutoring。
- Teach 是 intervention：依据本次诊断帮助用户改变可能导致错误的思考机制。
- Drill 是 targeted practice / controlled test，当前仍主要属于 intervention；可以产生真实的新 Error，
  但不是 Teach 的即时考试，也不凭一次正确就证明机制已改变。
- 一次 episode diagnosis 不等于长期确认 Pattern；未来真实学习中的独立 Evidence 更重要。

这条产品阶段方向不等于数据库状态枚举，也不要求每次操作线性执行。既有 Error 状态仍为
`pending-grill → pending-teach → done`，会话中断、完成后只读和 Teach 可继续等语义沿用
[会话生命周期决策](decisions/2026-07-27-conversation-lifecycle.md)。

V1 的主要 Evidence anchor 是真实发生的 Error，以及相关 Grill 中用户的回顾与回答。
题目和参考答案提供解释上下文，模型的解释或预测不能冒充行为 Evidence。
录题思路属于 retrospective reconstruction，是可能遗漏或有偏差的 noisy Evidence；必须区分过去思路与当前理解。
Grill 推断 Error-time failure mechanism，不恢复完整 post-interference cognitive state 或干预后因果链。
相关观察可以保存供审计，但不能据此声称某次 Teach/Drill 导致了变化。

Drill 中实际犯下的数学错误同样是真实 Error，但来源是 controlled/intervention context。
必须保留 provenance 和 lineage；它不自动成为自然学习中的独立 Future Error，不进入该类 recurrence 统计，
也不自动提升长期 Pattern 的证据级别。详见[来源与 Drill 账本](decisions/2026-09-01-evidence-provenance-drill-ledger.md)、
[Pattern State 提案](pattern-state-proposal.md)和[验证策略](evaluation-strategy.md)。

普通聊天、通用画像、无关 memory 不自动成为 Evidence。长期架构列出的 broader chat、Coding、Near Miss、
Micro Check 与外部行为数据只是未来候选来源，仍须证明它们有助于识别哪些 thinking mechanisms 会导致 Error。
更多数据不意味着建立 generic AI tutor memory。

## V1 Core：把核心闭环做到可长期使用

V1 的目标是“把 ErrGrind 最核心的 error-debugging loop 做到真正可长期使用”，而不是拥有复杂 agent intelligence。
以下是打磨方向，不表示所有项目都尚未实现；已有 structured episode diagnosis 与来源账本是基础。

### Grill

- 打磨 structured hypothesis / evidence / probe state，使 competing explanations 真正可区分、Evidence 可回指用户原话。
- 保证 `supported` / `undetermined` 语义可靠；证据不足是有效结果，不强行产生 Pattern。
- 保留 retrospective noisy Evidence 与当前理解的区别，不追问完整的干预后认知历史。
- variant probe 只在竞争解释对行为有不同预测、确有诊断价值时使用；它属于 Grill，不进入普通 Drill ledger。
- 控制问题数量、重复、leading 和用户负担；诊断是首要目标，允许 incidental learning effect，不能把学习效果当作诊断证明。

沿用[主动诊断](decisions/2026-09-04-grill-as-active-diagnosis.md)、
[结构化诊断与变式](decisions/2026-09-04-structured-grill-diagnosis-and-variant-probes.md)及
[回顾性与干预后边界](decisions/2026-09-05-retrospective-grill-and-post-interference-boundary.md)。

### Teach

Teach 要真正消费 Grill 的 episode diagnosis，针对 failure mechanism 设计干预，帮助用户理解并修正思考过程，
避免退化成普通题目讲解。通过兼容摘要等方式消费诊断，不要求公开内部诊断账本；诊断不确定时，干预也应保留相应限制。
V1 保持 Grill / Teach 清晰的产品边界，无需提前统一成复杂 Action engine。

### Drill / Judge

Drill 应越来越针对 failure mechanism，而不只是生成类似题。继续保持
[DrillSpec → Draft → Judge 的信息隔离](decisions/2026-07-28-drill-spec-isolation.md)，
通过真实样例和使用反馈改进质量，不为单题强保证恢复多级自审链。
每次完成判分都保存 attempt；当前错误判分会派生新的 `pending-grill` Error，正确判分也保留记录。

已知的 V1 Core polishing 任务是拆清 Judge 的“数学正确性”和“是否观察到目标 mechanism evidence”。
当前单一 `is_correct` boolean 也承载了作答过简、目标思路证据不足的情况；
不能把“没观察到证据”直接解释成“真实发生了数学错误”。后续需一起评估判分呈现与派生 Error 的语义，
但本文不决定最终字段、判分规则或迁移方案，也不改变当前运行行为。

### Input / reliability

OCR 等能力服务于真实使用，属于输入层；遵守[图片输入与校对边界](record-input.md)，不凌驾于核心闭环。
可靠性、resume、persistence、错误处理是可用性的必要条件：输入与已有会话不能因失败丢失，
Grill 中断后可继续，完成后只读，Teach 可保存并再次进入，Drill 判分与衍生记录保持原子性和来源可追溯。

## V1 Product / UX：核心可用之后的下一优先级

学习软件的 UX 直接影响用户是否愿意提供高成本 Evidence。Grill 本身已有认知负担，
因此交互成本、响应速度、进度感和恢复能力是产品有效性的一部分，不只是表面 polish。
核心固定状态机可用后，应先投入 UX/UI，而不是立即实现复杂 Policy。

### UX

- 降低录入 Error 的成本与 Grill 回答负担，观察用户在哪些环节疲劳或放弃。
- 让用户理解当前在澄清哪一类思考环节与不确定性，而不只看到“第几题”；表达必须中性，避免提示目标答案或诱导回答。
- 明确 session / progress 状态，提供良好的 pause/resume，让用户知道哪些内容已保存、回来后从哪里继续。
- 控制 latency 与 context growth；上下文整理不能为了省 token 而丢掉 Evidence grounding、原话引用与恢复所需信息。
- 提供清晰、可理解的 Evidence / diagnosis 呈现，但通过 application 的公开结果呈现，
  不直接暴露内部 State、Grill 诊断账本、variant 答案或预测；具体呈现方式仍需设计与验证。

### UI / interfaces

V1 产品层规划 WebUI 与 MCP interface，CLI 继续作为入口。Frontend / host 不拥有核心业务逻辑：
CLI、WebUI、MCP 都是 application/core 的薄 adapter，不能复制工作流或自己组合 DB/LLM 调用。
Application/core 继续作为业务规则的统一权威边界，SQLite 中的 ErrGrind 会话和记录仍是工作流事实来源。
沿用[架构解耦原则](ui-decoupling.md)与[Application 边界决策](decisions/2026-09-03-application-workflow-boundary.md)。

旧决策中的“本次不实现 MCP”描述当时范围；这里把 MCP 排入 V1 Product，并不表示已经实现，
也不要求引入复杂 Policy 或 dedicated harness。GUI / Mobile 的既有解耦方向继续有效，本文不另作 Mobile 发版承诺。

### V1 成功标准与进入 V2 的前提

- 用户能在实际学习中长期录入 Error，而非仅完成演示样例。
- 能完成 Grill → Teach → Drill 的有效闭环；证据不足时保留 `undetermined`，不为完成流程强推训练。
- session 可恢复，已有输入、会话与结果可靠保存。
- Evidence / diagnosis 的公开呈现清楚，用户能理解诊断与干预的关系。
- 交互成本与等待可接受，用户愿意持续回答 Grill 并回来使用。
- 实际体验明显区别于“直接把错题发给普通 ChatGPT”：有依据的机制诊断、针对性干预和可追溯的后续练习。

漂亮 UI 本身不是 V1 目标。应通过真实使用检验以上标准，再扩大 agent 能力；
流程完成或 Drill 正确率不等于“未来 Error 已减少”，效果声明继续遵守[验证策略](evaluation-strategy.md)。

## V2：从固定 workflow 向学习 agent 演进

V2 以 V1 已证明核心闭环与 UX 为前提。以下保留研究方向，不冻结 schema、算法、host 或 fork 项目。

### 1. Long-term State / Pattern State

```text
multiple Error episodes → candidate Pattern → repeated independent Evidence
                        → strengthening / weakening / revision / reopening
```

State 只建模与 Error 风险和学习决策有关的部分，不建立完整人格、兴趣或聊天历史式 generic user model。
Pattern 始终是可修正 hypothesis，不是假定 truth；缺少新 Evidence 不等于反证或已经解决。
跨 Error 聚合、人工 review、独立事件去重、revision、反证与重新打开等工作，继续参考
[Pattern State 演进提案](pattern-state-proposal.md)，不另建第二套对象定义。
其中 Stage 0 / 0.5 是当前基础，Stage 1–4 保留为 V2 研究与渐进落地候选；编号不是版本号，
已有门槛仍需重新评估，本文不自动批准 Stage 1 实现。

### 2. Policy

V1 以相对固定的 Error → Grill → Teach → Drill 工作流为主。V2 可根据 State 与 Evidence 决定：

- 是否继续 Grill，或当前支持是否已经足够；
- 是否 Teach、生成 diagnostic variant 或 Drill；
- 是否延迟后再测试、收集额外 Evidence，或暂时不采取动作。

长期抽象继续沿用 [Evidence → State → Policy → Action → New Evidence](long-term-architecture.md)。
LLM 可以提出建议，系统状态转换仍应由可审查的 Policy 规则控制；本规划不设计完整 Policy algorithm。

### 3. More Actions / More Evidence Sources

未来可以评估 micro-check、targeted diagnostic task、delayed verification、reflection，
以及其他被验证有价值的 Action 或辅助 Evidence source。
所有新增能力必须服务于“识别、验证、改变可能导致 Error 的机制”。
需要区分原始观察与解释、受控干预与独立 Evidence；不能因加入更多来源而滑向普通 memory AI tutor。
已有 Review、Near Miss、Coding 和外部行为数据方向仍保留，具体适用性需验证。

### 4. Multi-domain / Multi-subject expansion

过去所说的 `multigoal` 在这里明确指从数学扩展到多个学科或任务领域，**不是一个 Action 有多个 goal**。
V1 继续优先数学，因为输入和验证更可控，Error / correctness / Drill 更容易定义，适合先验证核心闭环。

V2 再研究物理、化学、语言学习、写作，以及其他适合 Error-driven debugging 的领域。
长期架构已有 Coding 候选也保留在此范围内。核心抽象可能复用，但不能假定数学的 Error ontology、Judge、
Drill 可以直接通用：domain-specific Evidence、Judge、Action 和输入形式可能不同，需逐领域验证。

### 5. Dedicated agent harness / fork

保留长期评估 dedicated harness / fork 的方向。当 Long-term State、Policy、多种 Action、多 Evidence source、
retrieval/context management、多 host 与更复杂 session lifecycle 出现后，当前简单 CLI/application workflow
可能不足以承载运行需求。届时可以比较：

- fork 一个成熟、轻量的 agent/harness；
- 基于现有 agent runtime 做 ErrGrind specialization；
- 继续让 ErrGrind core 通过 MCP 被外部 host 调用。

当前不决定 fork 哪个项目，也不实现。复用原则是：

> Reuse implementations aggressively; import ontologies conservatively.

可以大胆复用 runtime、context、tool、retrieval 基础设施，但 ErrGrind 的 Evidence / Pattern / Policy / Action
概念体系不能被上游 ontology 反向绑架。MCP 作为 V1 入口不意味着必须选择其中一种 V2 runtime 路径。

## Explicit non-goals / deferred 方向

| 方向 | V1 边界与后续处理 |
| --- | --- |
| 完整长期 Pattern graph、跨 Error 聚合与 promotion | 有意推迟到 V2；保留 Pattern State 提案与验证门槛 |
| 复杂 adaptive Policy | 有意推迟到 V2；V1 先验证固定工作流 |
| 更多 Action、主动测量与辅助 Evidence source | V2 按 Error mechanism 价值逐项验证；V1 保留已有 Grill Probe |
| 多学科扩张 | 有意推迟到 V2；V1 聚焦数学 |
| Dedicated harness / fork | 有意推迟到 V2 评估；不提前选项目 |
| 大规模 recommendation system | V1 不优先；V2 也需先证明 State / Policy 与实际需求 |
| 通用用户画像、为了 agent 化而 agent 化 | V1 不做；V2 的 State / agent 扩展仍受产品定位约束，不承诺转向通用 tutor |

Deferred 不等于 abandoned：长期 State、Policy、更多 Action / Evidence、多领域和 harness/fork 都保留。
但推迟某类能力，不代表 V2 自动接纳与 Error 无关的通用画像或无目的的 agent 化。

## 与既有文档的兼容性

- 长期架构描述终局抽象，本文安排开发顺序；“围绕 State / Policy 设计”不要求 V1 先实现复杂 Policy。
- [暂缓 Pattern Observation](decisions/2026-09-02-defer-pattern-observation-validation.md)仍约束长期 Observation / review；
  后续结构化 Grill 决策已引入 episode 内 Evidence grounding，不能把旧“暂缓校验”扩大解释成当前 Grill 没有校验。
- [验证策略](evaluation-strategy.md)中的 Pattern review、盲法关联、opportunity 和效果评估实验继续保留，
  其长期 State 依赖应按 V2 门槛展开；V1 先做 Grill 诊断质量、用户负担与真实可用性观察。
- `reference/skills/` 中的通用 tutor、调度和领域建模材料是参考，不是 ErrGrind 已采纳的产品 ontology，
  不能用其教学循环覆盖 active diagnosis。

后续版本规划可依据真实使用修订。涉及具体不可逆设计时，再更新主题文档或新增 ADR；
不要把本文中的候选方向直接当成实现授权或已验证方案。
