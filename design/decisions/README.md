# Decision Index

这个目录用于记录关键设计决策。

## 记录格式

每个决策建议单独建文件：

```text
YYYY-MM-DD-short-title.md
```

建议包含：

- Context：当时的问题和约束
- Decision：做出的选择
- Rationale：为什么这样选
- Consequences：带来的影响和后续注意事项

## 当前决策

- [Grill 与 Teach 会话生命周期](2026-07-27-conversation-lifecycle.md)：完成的 Grill 只读，partial Grill 可恢复，Teach 作为可持续进入的单一会话。
- [终端内容渲染边界](2026-07-27-terminal-content-rendering.md)：所有动态内容通过统一 UI 入口渲染，终端适配不污染标准 Markdown + LaTeX 数据。
- [Drill 两阶段规格隔离](2026-07-28-drill-spec-isolation.md)：先从历史 Error 生成公共 DrillSpec，再由看不到原题的 Draft 出题；MVP 不引入多级自审。
