# 核心原则

Error 的价值，在于它提供了关于思考过程的 Evidence。

Grill 的价值，在于减少我们对这次 Error 为什么发生的不确定性。

Pattern 的价值，在于它是一个可证伪、跨 Evidence 的假设，并且能够预测未来的行为或 Error。

State 的价值，在于保存当前有用的判断，而不是永久标签。

Policy 的价值，在于决定继续收集 Evidence，还是进行干预。

Action 的价值，在于真正减少未来的 Error。

新 Evidence 必须能够更新或推翻 State。因此：

> **ErrGrind 不是把一次 Error 直接归类为 Pattern，而是利用 Evidence 建立可更新的用户模型，并利用这个模型帮助用户减少未来的 Error。**

以后所有新增功能，都应该回答一个问题：

> **它是否能够帮助系统减少未来的 Error？**

如果不能，那么即使它很有趣，也不应该优先实现。
