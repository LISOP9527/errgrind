# drill-me 调度参考

本文件定义了记忆账本的格式和调度算法。严格按其中的算术执行——不要即兴调整间隔。算法是简化版的 FSRS（自由间隔重复调度器）：每张卡片有一个**稳定性**（学习者的回忆概率衰减到 ~90% 需要多少天）和一个**难度**评级，会抑制学习者觉得难的概念的稳定性增长。

在读写账本之前，始终通过执行 `date +%Y-%m-%d` 获取今天的日期。账本中的所有日期都是绝对日期（`YYYY-MM-DD`）。绝不要写相对日期。

## 账本结构

```
~/.drill-me/
├── index.md              # 每行一个主题：名称、卡片数、到期卡数、下次到期日
└── topics/<slug>.md      # 每个主题一个文件
```

`<slug>` 是用短横线连接的主题名称（例如 `rust-lifetimes`、`this-repo-auth-flow`）。开始会话前，列出 `~/.drill-me/topics/` 并将请求的主题与现有 slug 做模糊匹配——"rust lifetimes" 必须继续使用 `rust-lifetimes.md`，不要创建重复。如果匹配有歧义，问用户。

## 主题文件格式

```markdown
---
topic: Rust lifetimes
slug: rust-lifetimes
source: model knowledge        # 或：codebase @ /path, file @ /path, url
created: 2026-06-10
learner_level: developing      # novice | developing | solid（你持续做出的判断）
learner_notes: Knows borrowing well; confuses 'static with static items. Prefers code-first examples.
---

## 卡片

| # | 概念 | S（天） | D（1-5） | last | due | flags | history |
|---|---------|----------|---------|------|-----|-------|---------|
| 1 | Why lifetimes exist (dangling refs) | 6.0 | 2 | 2026-06-10 | 2026-06-16 | | G,G |
| 2 | Lifetime elision rules | 1.0 | 4 | 2026-06-10 | 2026-06-11 | cw | A |
| 3 | 'static meaning | 2.6 | 3 | 2026-06-10 | 2026-06-13 | | H |

## 尚未教授

- Lifetime bounds on generics (`T: 'a`)
- Higher-ranked trait bounds (intro only)
```

列含义：
- **S** —— 稳定性，以天为单位，保留一位小数。
- **D** —— 难度，1（对该学习者容易）到 5（难）。新卡片从 3 开始。
- **last / due** —— 上次复习日期、下次到期日（`due = last + round(S)` 天，最少 1 天）。
- **flags** —— `cw` = 上次自信但答错了（优先重测）；`leech` = 累计失败 4 次及以上（需要换角度重新教授，而不是重新测验）。
- **history** —— 最近约 8 次评分，最新的在最后：`A`=Again, `H`=Hard, `G`=Good, `E`=Easy。

"尚未教授"列表是剩余的提纲，这样回访会话就知道接下来有什么。

## 评分检索尝试

每次检索问题之后，静默评分（绝不要向学习者展示字母评分或背后的数学计算——他们看到的是正常的辅导，账本看到的是 FSRS）：

- **Again** —— 答错了，或者即使经过提示的第 1-3 级也答不出来。
- **Hard** —— 答出来了，但速度慢、不完整、或仅靠提示才答出。
- **Good** —— 以正常努力正确答出。
- **Easy** —— 瞬间、自信、正确、且解释也很扎实。

## 稳定性更新（复习现有卡片时）

设 `elapsed` = 距离 `last` 的天数，`R = elapsed / S`（逾期程度：1.0 = 正好在到期时复习）。将 `R` 限制在 [0.25, 2.0] 区间内。逾期的成功回忆收益更大（更难的检索 → 更大的记忆增益）；提前复习收益更少。

**成功时**（Hard/Good/Easy）：

```
base   = Hard: 1.4   Good: 2.2   Easy: 3.0
factor = base × (1 + 0.35 × (R − 1))        # 逾期奖励 / 提前衰减
factor = factor × (1.3 − 0.1 × D)           # D=1 → ×1.2, D=3 → ×1.0, D=5 → ×0.8
S'     = max(S × factor, S + 0.5)
```

**Again 时**（失败）：

```
S' = max(1.0, S × 0.3)
```

难度漂移：Again → D+1, Hard → D+0.5（向上取整）, Easy → D−1。限制在 [1, 5]。Good 不改变 D。

然后：`last = today`，`due = today + round(S')`，将评分追加到 history，设置或清除 `cw`（如果学习者信心评了 4-5 但答错了就设置；在成功重测后清除），如果 history 显示 4 次或更多次 A 就设置 `leech`。

## 新卡片（首次教授并测验某个概念时）

首次检索评分的初始稳定性：

```
Again: S = 1.0    Hard: S = 1.5    Good: S = 3.0    Easy: S = 6.0
```

初始 D = 3，根据教学效果调整 ±1（需要完整示例和两次提示 → 4；仅凭预测试猜对 → 2）。

## 会话排序

1. **到期的卡片优先**，排序方式：`cw` 标记的卡片排最前，然后是最逾期的（`elapsed / S` 降序）。在 1 天内到期的卡片算作到期。
2. **Leech 卡片**被重新教授（新角度、新示例），而不是仅仅被重新测验。
3. **只有到期队列清空后才教新内容**——或者如果到期卡片超过 10 张，在教了 10 张后说明情况并将剩余顺延。
4. 每轮会话的新卡片上限约 7 张。深度胜过广度。

## 会话内重复（扩展检索）

在本轮会话中被评为 **Again** 或 **Hard** 的卡片会在同一轮会话内被重新提问：在大约 3 轮对话交换之后，以及在接近结束时再问一次，每次使用不同的表面形式（新措辞、新示例、或反转方向）。只有本轮会话内最终的评分才被写入账本。

## index.md

每次会话后重新生成：

```markdown
# drill-me index

| topic | cards | due now | next due | last session |
|-------|-------|---------|----------|--------------|
| [Rust lifetimes](topics/rust-lifetimes.md) | 12 | 3 | 2026-06-11 | 2026-06-10 |
```
