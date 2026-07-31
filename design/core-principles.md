# 核心原则

Error 的价值，在于它暴露了 Pattern。

Pattern 的价值，在于它能够预测未来的 Error。

State 的价值，在于保存这些 Pattern。

Policy 的价值，在于决定最有效的下一步。

Action 的价值，在于真正减少未来的 Error。

因此：

> **ErrGrind 并不是在分析 Error，而是在利用 Evidence 建立用户模型，并利用这个模型帮助用户减少未来的 Error。**

以后所有新增功能，都应该回答一个问题：

> **它是否能够帮助系统减少未来的 Error？**

如果不能，那么即使它很有趣，也不应该优先实现。
