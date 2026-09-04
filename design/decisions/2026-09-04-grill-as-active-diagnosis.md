# Grill 作为主动诊断

## Context

旧版 Grill 继承了“苏格拉底式教学”的 framing，并隐含地把一次 Error 推向一个 Pattern。这混淆了帮助用户解题的教学问题与区分候选成因的诊断问题，也容易把 LLM 的合理猜测误当成 Evidence。

## Decision

Grill 定义为主动诊断：每一轮只问一个问题，问题应区分当前仍合理的候选原因，优先诊断信息增益，避免教学、提示和诱导造成 Evidence 污染，并允许以“证据不足，无法可靠区分”结束。一次 Grill 只产生本次 Error 的 episode-level diagnosis，不是已确认的长期 Pattern；长期 Pattern 需要跨 Error 或后续行为的独立 Evidence，并可被新 Evidence 更新或推翻。

本次只调整 Prompt 与设计文档语义；结构化 Hypothesis、Evidence、Pattern State 仍然 deferred，不在本任务实现。

## Rationale

LLM 能生成听起来合理的解释，但合理解释本身不是用户行为 Evidence。诊断性提问要最大化区分候选解释的信息增益，而教学性提问要推动用户理解或得到答案；两者目标不同，不能用 Socratic tutoring 的 framing 代替 active diagnosis。

## Consequences

后续 Prompt、设计文档和评估必须区分 diagnosis 与 teaching，也必须区分一次 episode diagnosis 与长期 Pattern。Grill 可以暂时离开原题以获取更有区分度的 Evidence；不确定性应作为有效结果保留。
