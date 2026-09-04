# Pattern State 演进提案

## 为什么暂时不直接新增 `patterns` 表

当前 `grilling_summary` 是针对一次 Error 得出的自然语言结论：它是 episode-level diagnosis，仍只是一个待验证的假设，不是跨 Error 的长期 Pattern。

如果把每条摘要直接当成一个 Pattern，会产生大量措辞不同、机制相同的重复项；如果让 LLM 自动合并，又可能把表面相似、实际机制不同的 Error 错误归类。两种做法都会让 Thinking Model 看起来很完整，却失去可证伪性。

因此，Pattern State 应逐层建立，而不是从一次 Grill 直接跳到“用户具有某个稳定 Pattern”。

## 核心对象

### Pattern Observation

一次 Grill 从一个 Error 中提出的结构化观察，至少包含：

- `mechanism`：导致本次 Error 的思维机制；
- `trigger`：什么局面触发了该机制；
- `failure_behavior`：用户当时实际做了什么；
- `evidence_refs`：Grill 对话中支持这一判断的用户消息索引、角色和原文片段；
- `interpretation`：LLM 根据这些原始证据作出的解释；
- `alternative_explanations`：仍未排除的其它解释及各自的支持/反对证据引用。

Observation 必须关联原始 Error 和不可变的 Grill 对话版本。写入时要验证每个引用确实存在、角色为 `user`、原文片段与消息内容一致；没有可定位用户证据的推测只能保持 `proposed` 或 `uncertain`。Observation 是证据上的解释，不是用户的永久标签。
诊断还应保留候选替代解释、支持与反对它们的 Evidence，以及哪些未来 Evidence 会改变当前判断。未来的结构化假设可以参与下一问题的选择，而不只是事后提取摘要。

### Pattern Candidate

多个 Observation 可能指向同一个可迁移机制。Candidate 是这些 Observation 的可审查聚合，保存关联依据、首次与最近出现时间，以及相互矛盾的证据。

Candidate 不使用 LLM 自报的“置信度百分比”。在当前阶段，更可审查的信号是独立真实 Error 的数量、时间跨度、触发场景多样性、反例和人工确认状态；这些信号只能支持或反驳 Pattern 假设，不能单独证明其因果有效性。

### Action Result

Teach、Drill、Review 等干预产生的结果。当前 `drill_attempts` 是第一类 Action ledger。

Action Result 可以帮助 Policy 选择下一步，但不能直接证明 Pattern 已消失。尤其是一次 Drill 答对，只能说明本次受控任务中的表现。

### Future Error Assessment

未来真实学习场景中，新 Error 与一个已经冻结的 Candidate revision 的关系：

- `recurs`：新 Error 再次出现相同机制；
- `possibly_related`：有相似迹象，但证据不足以确认复现；
- `not_related`：这次 Error 来自其它机制；
- `uncertain`：证据不足，暂不归类。

`recurs`、`possibly_related` 等是对 Evidence 的审查关系，不是 LLM 输出的机制真值；未完成人工审查的建议不得计入 recurrence。

`not_related` 不是反向证据：系统只观察到了 Error，没有观察同一触发条件下的全部成功机会。只有在来源为真实学习事件、或明确标记为独立抽样检查的 opportunity 中，记录了相同 trigger 且未出现目标 failure，才可能构成反向 Evidence。系统 Drill 的结果只能作为 Action Result，不得单独构成 Future Error 的反向 Evidence。

Assessment 必须引用明确的 Pattern revision 与 `assessment_baseline_at`，新 Error 的事件顺序必须晚于该基线，并且不能同时作为定义该 revision 的训练 Evidence。一个 Error 可以分别关联多个 Pattern，但每条关系都要独立审查。

## 不可破坏的边界

1. `/record` 当前通过用户主动提交题目和思路，暂视为用户确认的真实 Error；`/ocr` 只有用户完成字段校对并提交后才具有同样语义。`record` 与 `ocr` 只是采集方式，不是独立性证明；严格 recurrence 需要用户确认的 `learning_event_id` 或明确的 `unverified` 状态，并遵守去重规则。题目与参考答案是解释上下文，用户作答、思路和 Error 事件才是行为 Evidence。`drill` 始终是干预数据，不能进入 Future Error 统计；迁移后来源为 `unknown` 的记录，在用户确认前也不得进入 recurrence。
2. LLM 可以提出 Observation、Candidate 关联和 Assessment 建议，但 State 转换由确定性 Policy 与可审查规则完成。
3. 单次 Observation 不得显示为稳定 Pattern；单次 Action 成功不得显示为“已掌握”或“已解决”。
4. State 必须保留回到原始 Error、对话和模型输出的路径；删除原始 Evidence 时，相关推断必须失效、降级或删除。
5. 不用“很久没出现”自动推断已解决。缺少 Evidence 与反向 Evidence 不是一回事。
6. Pattern 的措辞应描述可迁移的思维机制，不能退化为知识点、题型标签、人格判断或“粗心”。
7. 只观察 Error 没有学习机会总数这一分母，因此 recurrence 可以证伪“Pattern 已消失”，却不能单独证明 Error 发生率下降。

## 建议的数据模型

以下结构是方向，不是已经决定的 Schema：

```text
error_records
  └─ pattern_observations
       └─ pattern_links ── patterns

patterns
  ├─ action_events / drill_attempts
  └─ future_error_assessments ── future error_records
```

### `pattern_observations`

- 一条 Observation 只属于一个 Error；一个 Error 可以有零到多条 Observation。
- 保存结构化字段、原始摘要、对话摘要哈希、证据引用、原始模型建议，以及提取所用 provider/model、prompt/schema 版本、耗时和创建时间。
- 默认状态为 `proposed`；用户或后续审查可标为 `accepted`、`rejected`、`superseded`。
- LLM 只能创建 `proposed`；`accepted` 必须来自用户确认。修改会创建新 revision，原始模型建议不可覆盖；被新 revision 替代的是 `superseded`，用户判定不成立的是 `rejected`。
- 删除原始 Error 时默认级联删除 Observation 及其可反推原文的派生内容；不静默保留“匿名”副本。

### `patterns`

- 保存相对稳定的规范化描述，而不是复制某一次摘要。
- 生命周期建议为 `candidate`、`corroborated`、`archived`；避免使用暗示已经治愈的 `mastered`。`archived` 仅表示用户不再主动跟踪，不表示 Pattern 已消失，也不能由时间自动触发。
- 统计真实 Error Observation 数、覆盖场景和时间跨度；干预结果单独统计。

### `pattern_links`

- 保存 Observation 为什么关联到某个 Candidate。
- 同时保留模型建议和最终决策，不能覆盖原始建议。
- 允许 Observation 暂时不归类，或在 review 后移动到其它 Candidate。
- 合并或拆分 Candidate 会创建 revision，保存前后 ID、操作者、理由和时间，历史 Assessment 仍引用当时的 revision。

### `future_error_assessments`

- 只评估事件顺序晚于所引用 Pattern revision 的 `assessment_baseline_at`、且没有参与定义该 revision 的真实 Error。
- 保存建议关系、最终关系、判断依据和审查状态。
- `drill` 来源不得进入“未来真实 Error 是否减少”的分子；当前系统没有机会总数，尚不存在可信分母。

### Evidence identity 与重复记录

- 每次真实学习事件需要独立的 `learning_event_id`；`record` 和 `ocr` 只是采集方式，不是独立性的证明。
- 同一题、同一作答或同一学习事件的重复录入只算一份 Evidence，并通过 `duplicate_of_error_id` 保留审计关系。
- 自动相似度只能提出重复建议，不能静默合并；没有完成重复审查的记录不能推动 Candidate 升级。
- Drill 衍生题后来被手动重录，不会因此变成独立真实 Evidence；lineage 不明时保持 `uncertain`。

## 最小确定性 Policy

在没有足够数据前，Policy 应宁可输出“不确定”，也不要制造精确感。

建议的初始规则：

1. 一个 accepted Observation 只能建立 `candidate`。
2. Candidate 是否升级为 `corroborated`，至少要有两个不同 `learning_event_id`、时间不同、用户已确认且去重完成的真实 Error；具体阈值应根据真实使用再调整。
3. Drill 的正确/错误只更新 Action 历史，不改变 Candidate 的证据级别。
4. 新真实 Error 到来时，优先请求关联审查；只有 accepted 的 `recurs` Assessment 才增加复现证据。
5. Teach/Drill 之后长时间无真实 Error 时，只记录“尚无新证据”，不自动进入 `archived`。

这些规则应写成纯函数并拥有固定 fixture 测试，LLM 不直接执行数据库状态转换。

## 分阶段落地

### Stage 0：来源与干预账本

已在 `2026-09-01-evidence-provenance-drill-ledger` 决策中实现。它解决数据是否来自真实 Error、OCR 还是 Drill，以及 Action 结果能否追溯的问题。

当前 MVP 已完成这一阶段；`grilling_summary` 仍是兼容性的自然语言输出，不能单独视为长期 Pattern。

### Stage 0.5：结构化 episode diagnosis（已实现）

每个 Error 的 Grill 还会保存一个 nullable 的 `GrillDiagnosticState`：

- hypotheses、Evidence ledger 和 Probe 只属于这一次 Error；
- Evidence 必须能回指 initial user thoughts 或真实 Grill user message 的原文片段；
- LLM 每轮只输出 delta，由 Application 确定性合并，旧 hypothesis claim、Evidence 和 Probe 不被重写或删除；
- Probe 显式区分 `reasoning_question` 与诊断用 `variant_problem`；variant 不进入普通 Drill ledger；
- episode diagnosis 可以完成为 supported 或 undetermined，但不会创建 Candidate、跨 Error 合并或长期 Pattern。

没有 Candidate merge、accepted/rejected human review、Pattern promotion 或长期 Pattern State。原 Stage 1/2/3 仍是未来工作。

下面的 Stage 1 仅是未来提案，不代表当前实现计划。

### Stage 1：结构化 Observation，但不自动建模

- Grill 完成后生成结构化 Observation；
- 提取失败不破坏现有 Grill 完成状态，自然语言摘要继续可用；
- LLM 只能写入 `proposed`，不会自动创建 Candidate、改变 Error 状态或安排 Action；
- UI 明确显示“Pattern 假设”，用户操作通过确定性 DB API 接受、创建修改 revision 或拒绝；
- 旧记录可按需补提取，不做静默批量迁移。

### Stage 2：Candidate 关联与人工 review

- LLM 从已有 Candidate 中提出关联或新建建议；
- 用户在轻量 review 中确认；
- 保留未归类和相互矛盾的 Observation。

### Stage 3：Policy 与 Future Error Assessment

- 新真实 Error 到来时，对已有 Candidate 做关联建议；
- `/status` 展示待审查 Evidence 和有依据的下一步 Action；
- 开始统计 recurrence，而不是只统计工作流完成数。

### Stage 4：效果评估

- 预先定义观察窗口和指标；
- 区分使用频率变化、题目难度变化和真正的 Error 机制变化；
- 在声称 Error 率下降前，引入可审计的学习机会分母，而不是用“记录条数变少”代替；
- 导出可人工复核的时间线，避免只给一个不可解释的“进步分数”。

## Stage 1 的验收门槛

只有同时满足以下条件，才建议开始实现 Stage 1：

- 结构化字段能从完整 Grill 对话得到，不仅从最后一句摘要猜测；每条 evidence 引用都能定位并校验到 `user` 原消息；
- 所有 provider 使用相同严格 schema，并有无网络离线测试；
- 旧数据库无损迁移；生成和 review 使用事务，失败不留下半条 Observation；删除 Error 的级联语义有测试；
- API 失败、Ctrl+C 和旧记录都能安全退回现有自由文本流程；旧记录只按需生成，不静默批处理；
- provider/model、prompt/schema 版本、对话摘要哈希、原始模型建议和 review revision 都被保存；
- LLM 只能生成 `proposed`；只有用户确认能进入 `accepted`，修改与拒绝不会覆盖原始建议；
- UI 使用“假设/观察”措辞，不出现“你就是这种 Pattern”的标签化表达；
- 调用前说明完整 Grill 对话会发送给当前 provider；记录耗时和失败原因，但绝不保存 API key；
- 至少准备一组人工审阅 fixture，用于比较提取结果是否忠实于原对话。

## 仍需用户决定的问题

1. Pattern review 应发生在 Grill 结束后立即进行，还是集中放到独立工作台？
2. Candidate 的合并/拆分是否必须每次人工确认，还是允许低风险自动建议后批量 review？
3. 是否愿意在记录 Error 时增加轻量的学习事件/重复确认，以换取更可信的 recurrence？
4. 为验证“减少未来 Error”，最小可接受的学习机会分母是什么：作业题数、学习时长、相关题型机会，还是抽样 Micro Check？
