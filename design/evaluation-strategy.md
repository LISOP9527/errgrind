# “减少未来 Error”的验证策略

## 先区分四种不同的成功

ErrGrind 的最终目标是减少未来 Error，但当前可观察到的信号分属不同层级，不能互相替代。

1. **流程完成**：用户录入、完成 Grill/Teach、做过 Drill。
2. **模型有效**：Pattern 假设能够解释并预测后来独立发生的 Error。
3. **行为迁移**：干预后，用户在新情境中更常表现出目标思维行为。
4. **真实 Error 减少**：在可比的学习机会中，相关 Error 的发生率下降。

`done` 数量只能说明流程完成；Drill 正确只能提供一次受控任务中的行为信号；未来 Error 再次出现可以反驳“已经解决”，但只有在存在学习机会分母时，才能估计 Error 发生率是否下降。

## 当前数据能回答什么

现有 `error_records`、来源字段和 `drill_attempts` 可以回答：

- Error 通过用户录入、OCR 校对还是 Drill 衍生进入系统；
- 某次 Drill 当前仍关联到哪个 source Error 和出题规格；
- 用户在这次 Drill 中的作答与判分结果；
- Drill 答错后生成的新 Error 当前仍保留的 source 关联。

这些字段不能单独证明两条记录来自独立学习事件，也不能证明 Pattern recurrence；Drill 统计还必须按 Judge provenance 分段，不能跨版本直接比较。

它们暂时不能回答：

- 用户遇到了多少次可能触发某 Pattern 的机会；
- 没有录入 Error 是因为确实没犯、没遇到，还是停止记录；
- 后来题目是否更难、学习领域是否改变；
- 行为改变来自 Teach/Drill，还是来自课堂、教材、时间或其它因素；
- 一条自然语言 Grill 摘要是否真的是稳定、可复现的 Pattern。

因此，当前看板不得把“错题记录减少”“done 增多”或“Drill 正确率”显示成产品效果分数。

## Pattern 预测验证：先独立发现，再关联

如果新 Error 的 Grill 一开始就看到历史 Pattern，LLM 很容易产生确认偏差，把新证据解释成已有分类。未来 recurrence 验证应采用两阶段盲法：

1. **Independent Grill**：只看新 Error、本次用户思路和参考答案，独立生成新的 Pattern Observation；不提供历史 Candidate。
2. **Association Review**：完成并冻结 Observation 后，另一步才比较已有 Candidate，提出 `recurs`、`possibly_related`、`not_related` 或 `uncertain` 建议。

匹配步骤不能修改 Independent Grill 的原始 Observation。人工 review 同时看到原始 Evidence 引用、两个独立描述和匹配理由。

这种方法仍不能自动证明因果，但能减少“因为系统想找到复现，所以总能找到复现”的循环论证。

## 需要的分母：Pattern opportunity

验证 Error 率下降需要记录用户遇到过多少次相关机会。一个 opportunity 至少需要：

- 时间与学习事件；
- 触发结构或 Candidate revision；
- 任务难度与领域的粗粒度描述；
- 是否出现目标 failure behavior；
- 信息来自真实学习、抽样检查还是系统 Drill；
- 用户是否实际完成，还是跳过/放弃。

MVP 可以从低负担方案开始，而不是记录全部学习行为：

- 每次录入真实 Error 时，询问近期大约遇到多少个同类机会；
- 定期抽样一个与 Candidate 相关的 Micro Check；
- 支持用户批量记录一次作业中的相关题数与相关 Error 数；
- 如果未来接入学习平台，读取明确授权的完成事件。

这些来源的可信度不同，统计时必须分层，不能把系统 Drill 与真实学习机会混在一起。

## 建议指标

### 模型质量

- **Observation acceptance**：用户接受、修改、拒绝的比例；修改不能算原建议正确。
- **Evidence grounding**：每条 Observation 中能被验证到用户原消息的 evidence 引用比例。
- **Association review agreement**：系统提出的 `recurs` 建议中，经人工审查接受的比例；它只衡量关联建议与人工审查的一致性，不称为预测 Precision，也不等于 Pattern 的真实预测能力。
- **Uncertain rate**：证据不足时系统选择不确定的比例；过低可能意味着过度归类。
- **Candidate stability**：Candidate 被合并、拆分、拒绝的频率与原因。

### 行为迁移

- 只有在已记录、来源明确且可比较的 `Pattern opportunity` 中，才统计 `success_signal` 或功能等价行为出现比例；
- 相同 Candidate 在 Teach/Drill 前后的变化需按难度、领域和来源分层；
- 用户答案正确但思路证据不足时单独统计，不能强行记为行为成功或失败。

在仅有 Error/Drill 数据时，行为迁移比例必须显示为“不可估计”，不能用已记录 Drill 次数或 Error 条数替代分母。

### 真实结果

- 每 100 个可比 opportunity 中，目标 failure behavior 导致的 Error 数；
- 复现前的 opportunity 数和 time-to-recurrence；
- 与无关 Pattern、其它数学 Error 和总体记录习惯的变化同时展示。

不建议首版生成单一“认知分”“掌握度”或“进步百分比”。这些数字会掩盖分母、样本量和证据来源差异。

## 最小分析单位与时间顺序

以下是 Stage 2/3 之后的分析前提，不是当前 MVP 已具备的字段；在 Pattern revision 尚未建立前，不得声称已完成预测评估。

- 每个真实学习事件拥有稳定的 `learning_event_id`，重复录入不能增加样本量。
- Pattern 的预测针对冻结的 `pattern_revision_id`；只有 `assessment_baseline_at` 之后、且没有参与定义该 revision 的事件才算未来样本。
- 当未来建立 Pattern revision 后，Action 才引用明确 revision 与发生时间；当前 `drill_attempts` 只能作为不绑定 Pattern revision 的 Action ledger。先发生的 Action 才能与后来的行为变化建立时间关系。
- 相同秒内的事件需要稳定顺序号，不能只依赖 SQLite 秒级时间戳。
- 模型、prompt、schema 或判分规则升级后要保留版本，必要时分段分析。

## 主要混淆因素

至少要在解释结果时检查：

- **暴露变化**：用户不再学习相关内容，自然不会出现相应 Error；
- **记录变化**：用户更勤或更少录错题，并不代表真实 Error 率变化；
- **难度变化**：新任务明显更简单或更难；
- **选择偏差**：系统只对容易提炼的 Pattern 进行干预；
- **同时干预**：课堂、老师、教材或其它练习共同影响结果；
- **Judge 漂移**：provider、model、prompt 或 schema 改变导致判分标准变化；
- **回归均值**：一次异常糟糕表现后自然回升，被误认为 Action 效果。

这些因素不一定都能在个人 MVP 中消除，但必须保留足够信息让用户看见，而不是隐藏在一个结论里。

## MVP 可接受的声明边界

在只有 Error 与 Drill 数据时，可以说：

- “系统记录了哪些 Pattern 假设和干预结果”；
- “经人工接受的 Assessment 将某个后来独立 Error 关联为可能复现某 Pattern”；
- “用户在某次新 Drill 中展示/没有展示目标思维行为”。

不能说：

- “Pattern 已掌握/已治愈”；
- “未来 Error 已减少”；
- “Teach 或 Drill 导致了进步”；
- “没有再次录入，所以 Pattern 已消失”。

当系统拥有经过审查的 Pattern、冻结 revision、独立 Future Assessment 和可信 opportunity 分母后，才可以报告带样本量、来源和限制的趋势。因果声明还需要更强的对照设计，不能仅凭前后变化。

## 当前 Grill 实验的早期评估

在评估长期 recurrence 之前，先评估 Grill 是否在做有效诊断：

- 真实成因是否进入候选解释集合；
- 是否过早收敛到单一解释；
- 每个问题是否具有足够的区分度和信息价值；
- 是否通过提示、教学或诱导性措辞污染用户 Evidence；
- Evidence 不足时是否诚实保留不确定性；
- 从问题到 episode-level diagnosis 的问题数量与用户负担；
- 用户疲劳和放弃通常发生在第几个问题之后。

这些指标评估诊断质量与交互成本，不把一次 Grill 结果当作已确认的长期 Pattern。

## 推荐的下一项产品实验

先不要扩展更多 Action。选择少量真实 Error，人工审阅其 Pattern Observation；后续新 Error 使用两阶段盲法关联。同时以尽量低负担的方式记录相关 opportunity。观察用户是否愿意完成 review、Observation 是否忠实、Candidate 是否真的复现，以及 opportunity 数据是否可持续采集。

这个实验首先验证 Thinking Model 是否值得建立，再决定是否投入自动聚类、复杂 Policy 或进步看板。
