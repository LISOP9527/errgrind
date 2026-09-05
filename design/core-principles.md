# 核心原则

Error 的价值，在于它提供了关于思考过程的 Evidence。

诊断以 authentic Error 作为 anchor，目标是推断 Error 发生当时的 failure mechanism。录题时的思路往往是 retrospective reconstruction，只是可能不完整的 noisy Evidence；必须把过去的思路和用户当前已经形成的理解分开。Teach、Drill 等干预后的观察可以保存，但当前设计不追踪 post-interference state transition 或 causal chain，也不能把这类观察归因为某次干预。

Grill 的价值，在于减少我们对这次 Error 为什么发生的不确定性。

Grill 的 primary objective 是 diagnosis 而非 Teach；问题不得为了教学、提示答案或纠正用户而设计，但允许产生 incidental learning effect。

Pattern 的价值，在于它是一个可证伪、跨 Evidence 的假设，并且能够预测未来的行为或 Error。

一次 Grill 只能形成当前 Error 的 episode-level diagnosis。长期 Pattern 需要未来 authentic、独立的 Evidence 来支持、更新或推翻，不能把一次诊断称为 confirmed、verified 或 mastered Pattern。

State 的价值，在于保存当前有用的判断，而不是永久标签。

Policy 的价值，在于决定继续收集 Evidence，还是进行干预。

Action 的价值，在于减少诊断不确定性或改变未来行为，并最终服务于减少未来 Error。

新 Evidence 必须能够更新或推翻 State。因此：

> **ErrGrind 不是把一次 Error 直接归类为 Pattern，而是利用 Evidence 建立可更新的用户模型，并利用这个模型帮助用户减少未来的 Error。**

以后所有新增功能，都应该回答一个问题：

> **它是否能够帮助系统减少未来的 Error？**

如果不能，那么即使它很有趣，也不应该优先实现。
