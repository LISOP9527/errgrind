# ErrGrind MVP 可复用回归 Case

本文档用于每次调试后的手工回归。Case 以当前 `DESIGN.md`、`CLAUDE.md` 和实际 CLI
为依据，验证的 MVP 闭环是：

```text
真实数学 Error -> Grill 发现 Error Pattern -> Teach 修正 Pattern
                -> Drill 检验迁移 -> 答错后形成新的 Error
```

本文档不是用一次演示证明“已经减少了未来错误”。它验证的是这个目标成立前必须具备的
功能、数据和内容质量。非数学题、Near Miss、Chat/Coding Evidence、GUI/Mobile 均不在
本轮范围内。

## 1. 执行约定

### 1.1 优先级

- `P0`：每次调试后都执行；任一失败即不能认为本次调试通过。
- `P1`：相关模块变更后执行；提交前建议执行。
- `P2`：Prompt、模型、Provider、首次安装或发布前执行。

### 1.2 结果定义

- `PASS`：步骤和所有预期结果都满足。
- `FAIL`：行为不符、数据丢失、状态错误、未捕获异常或界面卡死。
- `BLOCKED`：缺少 API、指定 Provider 或可控故障环境，不能写成 `PASS`。
- `FLAKY`：LLM 语义结果首次失败、同配置立即重跑后通过。它不是稳定通过，需保留输出。

LLM 文案不要求逐字一致，只按每个 Case 的语义标准判断。记录 Provider、Model 和 Prompt
版本；否则不同轮次的结果不可比较。

### 1.3 数据安全

程序会直接写入仓库内的 `data/errgrind.db`，配置会写入
`~/.config/errgrind/config.json`。完整回归应在一次性仓库副本、容器或专用测试账号中执行，
不要拿真实错题库做删除、空库、首次配置和故障注入测试。

若只能在日常环境测试：

- 所有题目前加本轮唯一前缀，如 `[CASE-20260723-A]`。
- 不执行标注为“需隔离环境”的 Case。
- 测完只通过 `/resume` 的删除确认框清理本轮记录。
- 不在测试记录中粘贴 API Key。

建议终端至少 `100 x 30`，使用项目虚拟环境，并先执行：

```bash
.venv/bin/python -m compileall -q errgrind
.venv/bin/python -c "import errgrind; from errgrind.cli.app import run_session"
```

两条命令都应退出码为 `0`，且没有 traceback。

### 1.4 每轮执行记录

每次回归复制一行填写，不要修改 Case 的预期结果。

| 日期 | Commit/工作区说明 | Python | Provider / Model | 执行 Case | PASS | FAIL | BLOCKED/FLAKY | 缺陷编号 |
|---|---|---|---|---|---:|---:|---|---|
| YYYY-MM-DD | `git rev-parse --short HEAD` + dirty/clean | 3.11+ | 例：DeepSeek / deepseek-chat | 例：P0 全部 | 0 | 0 | - | - |

单个失败记录模板：

```text
Case ID:
实际结果:
预期结果:
复现次数:
Provider / Model:
相关 Error 前缀或 ID:
终端最后一条系统提示（不得含 API Key）:
```

## 2. 固定测试数据

每轮复用相同题目，才能比较 Prompt 或模型变化。将 `<RUN>` 替换为本轮唯一标识。

### Fixture A：定义域与验根（核心样本）

```text
题目：
[CASE-<RUN>-A] 解方程 sqrt(x + 1) = x - 1。

你的思路：
两边平方得 x + 1 = (x - 1)^2，整理为 x(x - 3) = 0，
所以 x = 0 或 x = 3。我得到候选值后就结束了。

参考答案及解析：
原式要求 x - 1 >= 0，因此 x >= 1。平方后候选为 x = 0、3，
代回原式，只有 x = 3 成立，所以解为 x = 3。
```

Grill 被追问时按真实含义回答，不要求问题顺序一致：

```text
我把两边平方当成了完全等价的变形。
我当时没有先检查定义域，也没有把候选值代回原式。
我做含根号方程时常在得到代数根后直接结束。
```

预期 Pattern：把可能扩大解集的变形当成等价变形，并缺少“检查条件 + 代回验根”的收尾步骤。

### Fixture B：不等式变形的适用条件

```text
题目：
[CASE-<RUN>-B] 解不等式 (x - 1) / (x + 2) > 0。

你的思路：
两边乘以 x + 2，得到 x - 1 > 0，所以 x > 1。

参考答案及解析：
x = -2 无定义。按临界点 -2、1 分区间判断符号，解集为
(-infinity, -2) union (1, +infinity)。不能在不知道 x + 2 正负时直接同乘并保持不等号方向。
```

预期 Pattern：机械执行不等式变形，未先验证乘数符号和定义域等适用条件。

### Fixture C：条件概率的样本空间

```text
题目：
[CASE-<RUN>-C] 某班 60% 的学生参加数学社，40% 参加物理社，
25% 同时参加两社。随机选一名已知参加数学社的学生，求其也参加物理社的概率。

你的思路：
题目说同时参加两社的是 25%，所以答案就是 25%。

参考答案及解析：
已知参加数学社后，样本空间缩小为这 60% 的学生。
所求为 P(物理|数学) = 25% / 60% = 5/12。
```

预期 Pattern：看到联合比例后直接作答，忽略“已知”条件已经改变了分母和样本空间。

### Fixture D：录入和界面边界样本

```text
题目：
[CASE-<RUN>-D]
设 f(x) = x^2 - 2x + 1。
求 f(x) 的最小值，并说明取到最小值时的 x。

你的思路：留空
参考答案及解析：留空
```

## 3. 状态与持久化判定表

UI 提示和数据库状态必须同时符合下表。不能只看“成功”提示。

| 时点 | `status` | `grilling_conversation` | `grilling_summary` | `teach_conversation` |
|---|---|---|---|---|
| `/record` 完成 | `pending-grill` | 空 | 空 | 空 |
| Grill 中断/API 失败且已有进度 | `pending-grill` | 非空，保留到最后一条已发生消息 | 空或旧值已清理 | 原值按 re-grill 规则处理 |
| Grill 正常完成 | `pending-teach` | 非空、合法 JSON | 非空 | 新题为空；re-grill 后旧值为空 |
| Teach 中断/API 失败且已有进度 | 状态保持进入 Teach 前的值 | 不变 | 不变 | 非空，保留进度 |
| Teach 正常完成 | `done` | 不变 | 不变 | 非空、合法 JSON |
| Drill 答错 | 新增一条 `pending-grill` | 空 | 空 | 空 |

需要核对落库字段时，可在仓库根目录执行下面的只读检查。它只显示字段是否存在，不打印
对话正文或 Key：

```bash
.venv/bin/python - <<'PY'
import json
import sqlite3

def json_state(value):
    if value is None:
        return "-"
    try:
        return "ok" if isinstance(json.loads(value), list) else "INVALID"
    except (TypeError, json.JSONDecodeError):
        return "INVALID"

conn = sqlite3.connect("file:data/errgrind.db?mode=ro", uri=True)
for row in conn.execute("""
    SELECT id, status, substr(replace(question, char(10), ' '), 1, 45),
           grilling_conversation, grilling_summary, teach_conversation
    FROM error_records
    WHERE question LIKE '[CASE-%'
    ORDER BY created_at DESC, id DESC
"""):
    print(row[0], row[1], row[2],
          "grill=" + json_state(row[3]),
          "summary=" + ("yes" if row[4] else "-"),
          "teach=" + json_state(row[5]))
conn.close()
PY
```

补充不变量：

- 状态只能是 `pending-grill`、`pending-teach`、`done`。
- 对话 JSON 中每条消息都有 `role` 和 `content`，角色顺序合理，没有截断 JSON。
- Grill 完成标记 `[GRILLING_END]` 不应显示给用户，也不应残留在最终摘要中。
- 取消、失败和答对不能意外新增 Error。
- 所有列表按最近创建优先；Drill 上下文按最近更新优先，且最多取 `drill_context_n` 条。

## 4. P0：每次调试后的最小回归

### EG-P0-01 启动、帮助与命令边界

前置：已有有效测试配置；测试库可为空。

步骤：

1. 执行 `errgrind`（未安装命令时执行 `.venv/bin/python -m errgrind`）。
2. 输入 `/` 和 `/re`，观察命令补全，再清空输入。
3. 输入 `/help`，分别用 Enter、Esc 或 `q` 关闭弹窗。
4. 输入普通文本 `hello`。
5. 输入不存在的命令 `/not-exist`。
6. 输入空行。

预期：

- 启动页显示当前 Provider、Model 和三种状态计数；提示 `/help`、`/resume`。
- slash 补全只给出匹配的已注册命令和说明，不会擅自提交命令。
- 帮助包含且只需覆盖 `/record`、`/resume`、`/drill`、`/status`、`/config`、`/model`、`/help`、`/exit`。
- 普通文本提示必须使用 slash command；未知命令给出可理解提示；空行无副作用。
- 弹窗关闭后回到主命令行，没有 traceback、残留输入或重复执行。

### EG-P0-02 录入完整 Error

前置：记录 `/status` 的总数 `N`。

步骤：使用 `/record` 录入 Fixture A，三项均提交；随后打开 `/resume`。

预期：

- 提示录入成功，总数变为 `N + 1`，`pending-grill` 增加 1。
- 新记录排在列表最前，状态为“待审讯”。
- 题目、思路、参考答案按原意完整保存；多行输入没有被拼接或截断。
- 数据字段符合“`/record` 完成”一行。

### EG-P0-03 录入取消与可选字段

步骤：

1. 再次 `/record`，第一步直接 Enter 提交空题目。
2. 再次 `/record`，第一步输入任意题目，第二步按 Esc。
3. 使用 `/record` 录入 Fixture D，第二、三步留空提交。

预期：

- 前两次都提示取消且不新增记录；不留下半条记录。
- Fixture D 成功新增，只有题目必填；空白可选字段保存为空值而不是字符串噪声。
- Alt+Enter 能插入换行，Enter 提交，Esc 取消，三种操作不混淆。

### EG-P0-04 工作台导航、默认动作与 Teach 守卫

前置：Fixture A、D 均为 `pending-grill`。

步骤：

1. `/resume` 后用上下方向键在 A、D 间切换。
2. 在 D 上按 `t`。
3. 回到工作台，在 A 上按 Enter，并直接接着执行 EG-P0-05。

预期：

- 左侧选中项与右侧题目、状态同步，长/多行内容不覆盖底部快捷键。
- 没有 Grill 对话时按 `t` 只提示“请先 grill”，不调用 Teach、不改状态。
- `pending-grill` 上按 Enter 等价于 `g`；其他状态上按 Enter 等价于 `t`。
- `q`/Esc 返回主命令行。

### EG-P0-05 Grill 正常完成及内容质量

前置：Fixture A 为 `pending-grill`，使用有效模型；若从 EG-P0-04 连续执行，Grill 已由 Enter 启动。

步骤：若尚未启动，在 `/resume` 选中 A 并按 `g`；使用 Fixture A 的回答继续，直到 Grill 自动结束。

预期：

- 首次调用后导师直接提问；每个未结束回复只推进一个核心问题。
- Grill 不讲正确解法、不提示 `x >= 1` 或“代回即可”，也不把原因停留在“粗心/不会”。
- 追问围绕用户当时为何认为变形等价、是否检查条件及是否有稳定习惯，不机械重复。
- 结束总结准确指出 Fixture A 的预期 Pattern，并说明它为何会产生伪根；总结不夹带教学或改进建议。
- `[GRILLING_END]` 不可见；状态转为 `pending-teach`，字段符合状态表。
- 若模型第一条回复就给出准确总结并结束，也允许通过，仍需满足以上内容标准。

### EG-P0-06 Teach 正常完成及内容质量

前置：Fixture A 已通过 EG-P0-05，状态为 `pending-teach`。

步骤：

1. 在 A 上按 Enter 或 `t`。
2. 首轮讲解后追问：“为什么平方会产生不满足原方程的候选根？”
3. 确认回答后输入独立的一句 `理解了`。

预期：

- 首轮先关联 Grill 得出的 Pattern，再解释正确解法；不是一篇与用户错误无关的通用讲义。
- 明确说明定义域、非等价变形和代回验根，推导结果正确。
- 给出可迁移的检查顺序，例如“先条件、再变形、最后验根”。
- 对追问只解释未理解部分，不从头重复整篇讲解。
- 结束后状态为 `done`，Teach 对话完整保存，重启后仍可见该状态。

### EG-P0-07 Status 计数一致性

前置：至少有一个 `pending-grill`（D）和一个 `done`（A）。

步骤：执行 `/status`，关闭后再进 `/resume` 人工核对各状态数量。

预期：

- `pending-grill + pending-teach + done = total`。
- 三个数量与工作台逐条计数一致；百分比基于 total，合计允许因显示四舍五入有微小误差。
- 空库时所有数量为 0，不除零、不显示异常值。

### EG-P0-08 Drill 答错形成新 Evidence

前置：至少一条记录已完成 Grill，记录执行前总数 `N`。

步骤：

1. 执行 `/drill`。
2. 对生成题提交一个明显错误且带错误思路的答案，例如“答案是 0；我没有检查任何条件，直接猜测”。
3. 关闭评估弹窗，进入 `/resume` 查看最新记录。

预期：

- 生成题不是选择题，不照抄历史题或只换数字，难度与历史题相近或略高。
- 题目确实能触发至少一个已有 Pattern；用户只看到题目，不泄露参考答案、target pattern 或设计理由。
- Judge 因答案/思路明显错误判错，反馈不超过 3 句话并指出主要问题。
- 总数变为 `N + 1`；最新 Error 的题目是 Drill 题，`user_thoughts` 是完整作答，
  `reference_answer` 是生成的参考解，状态为 `pending-grill`。

### EG-P0-09 重启持久化与正常退出

步骤：

1. 输入 `/exit`，重新启动。
2. 查看启动统计、`/status` 和 `/resume`。
3. 在主提示符按一次 Ctrl+C，再启动一次并按 Ctrl+D。

预期：

- `/exit`、Ctrl+C、Ctrl+D 都安静退出，无 traceback；数据库连接正常关闭。
- 重启后三处计数一致，A 仍为 `done`，D 和 Drill 新记录仍为 `pending-grill`。
- 已保存题目、摘要和对话没有乱码、丢失或状态回退。

## 5. P1：状态机、恢复与数据边界

### EG-P1-01 无 Drill 上下文

前置：需隔离环境；数据库为空，或只有尚未完成 Grill 的记录。

步骤：执行 `/drill`。

预期：提示先完成至少一条 Grill；不调用模型、不新增 Error、不改变现有记录。

### EG-P1-02 Grill 中断后继续

前置：Fixture B 为 `pending-grill`。

步骤：

1. 开始 Grill，至少完成一轮问答。
2. 在等待下一次用户输入时按 Ctrl+C。
3. 检查状态后重新进入该题按 `g`。

预期：

- 中断被捕获并提示进度已保存，应用不退出、不报 traceback。
- 记录回到 `pending-grill`，已有对话 JSON 完整。
- 继续时先展示近期上下文或直接沿已有对话推进，不把已回答的问题从头再问。
- 最终完成后只有一条连贯 Grill 对话，状态为 `pending-teach`。

### EG-P1-03 Teach 中断及两类恢复

前置：Fixture B 已完成 Grill。

步骤：

1. 开始 Teach，首轮讲解出现后按 Ctrl+C；重新进入并继续。
2. 再提出一个问题，在模型响应期间制造可恢复 API 失败；恢复网络后再次进入 Teach。
3. 最后输入 `理解了`。

预期：

- 两次中断/失败都不丢 Grill 数据，Teach 进度已保存，状态在完成前保持不变。
- 若最后一条是待回答的 user 消息，恢复后自动补全这条回答且不重复追加 user 消息。
- 恢复后的讲解上下文连贯；完成后只保留有效的最终 Teach 对话并转为 `done`。

### EG-P1-04 Re-grill 与 Re-teach

前置：Fixture A 为 `done`，保存旧摘要和旧 Teach 是否存在的观察结果。

步骤：

1. 在 A 上按 `g`，继续已有 Grill 对话并完成。
2. 查看状态和内容，再按 `t` 完成一次新 Teach。
3. 在 `done` 状态再次按 `t`，完成 re-teach。

预期：

- Re-grill 继续旧 Grill 对话，不创建重复 Error；旧 Teach 和旧摘要先清理，完成后生成新摘要并处于 `pending-teach`。
- 新 Teach 使用最新 Grill 上下文，完成后为 `done`。
- Re-teach 使用 fresh Teach 上下文并覆盖旧 Teach，不把两次完整讲解错误拼接；完成后仍为 `done`。

### EG-P1-05 Grill 首轮直接结束

前置：新建一道非常简单、思路已充分暴露 Pattern 的数学 Error。

步骤：开始 Grill；允许测试模型在第一条回复末尾直接输出结束标记。

预期：即使没有额外用户轮次，也保存 system/user/assistant 对话和准确摘要，状态转为
`pending-teach`；结束标记不可见、不可残留。不能因“零轮用户追问”卡在 `pending-grill`。

### EG-P1-06 `grill_max_turns` 边界

前置：在 `/config` 暂设 `grill_max_turns = 1`，新建 Fixture C。

步骤：开始 Grill，给出一条仍不足以结束分析的回答，让模型继续追问并到达轮数上限。

预期：应用不静默丢失本轮内容、不崩溃；明确告知达到上限，保存 partial 对话并保持
`pending-grill`，下次可继续。完成后恢复原配置值。

### EG-P1-07 Drill 答对、取消与空白

前置：存在有效 Drill 上下文；记录总数 `N`。

步骤：

1. `/drill` 生成题后按 Esc 取消。
2. 再次生成题，提交空白。
3. 再次生成题，提交可核验的完整正确答案和正确思路。

预期：

- 前两次不调用 Judge、不新增 Error；均回到主命令行。
- 第三次 Judge 判对并简短反馈，总数始终为 `N`，历史 Error 状态不变。

### EG-P1-08 Drill 上下文条数和排序

前置：需隔离环境；准备至少 3 条有不同摘要的已 Grill 记录，并能通过可控模型/代理观察请求。

步骤：将 `drill_context_n` 设为 2，按顺序更新三条记录，使更新时间可区分，再执行 `/drill`。

预期：请求只包含最近更新的 2 条“题目 + 审讯摘要”，不含未 Grill 记录、Teach 全文、
API Key 或第三条旧记录。完成后恢复配置。

### EG-P1-09 删除确认与取消

前置：新建一个仅用于删除的 `[CASE-<RUN>-DELETE]` 记录，记下总数 `N`。

步骤：

1. `/resume` 选中它按 `d`，先按 Enter 或 `n` 取消。
2. 再次按 `d`，按 `y` 确认。

预期：第一次总数仍为 `N`；第二次总数为 `N - 1`，目标记录消失，其他记录和状态不变。
确认提示所指题目与当前选中项一致。

### EG-P1-10 数值配置校验与即时生效

步骤：在 `/config` 分别编辑 `drill_context_n` 和 `grill_max_turns`，依次尝试空字符串、
`abc`、`0`、`-1`、`1`、一个正常正整数，并在任一步按 Esc。

预期：

- 非整数提示“请输入整数”；小于 1 提示“必须 >= 1”；无效值不落盘。
- Esc 只取消当前操作，不退出应用、不覆盖旧值。
- 有效值立即显示在配置列表并写入配置；重启后仍生效。
- 测试结束恢复原值。

### EG-P1-11 主界面与弹窗隔离

步骤：依次打开并关闭 `/record`、`/resume`、`/status`、`/config`、`/help` 和 Drill 答题页；
在 `80 x 24` 与较宽终端各执行一次。

预期：主页面只保留 slash command、分隔线和少量成功/错误/系统反馈；表单、详情、帮助、
状态、确认框都在独立全屏弹窗。任何尺寸下文字不覆盖快捷键，不出现无法退出的弹窗。

## 6. P1：API 与异常恢复

下列 Case 应使用测试 Key、可控代理或 Fake LLM；不可通过消耗真实数据来制造故障。

### EG-ERR-01 Grill API 失败

步骤：让首次请求失败；再让已有一轮用户回复后的请求失败。

预期：显示中文 API 错误且不泄露 Key；首次失败不伪造对话或摘要，后续失败保存到最后一条
user 消息并回到 `pending-grill`。恢复后能补全该消息，不重复提问。

### EG-ERR-02 Teach API 失败

步骤：分别让首轮 Teach 请求和追问后的请求失败。

预期：首轮失败不写空 Teach、不改状态；追问失败保存到最后一条 user 消息。恢复后补全，
Grill 摘要不被覆盖。

### EG-ERR-03 Drill 生成或判分失败

步骤：分别让生成请求失败、返回非法 JSON、缺少 `question`、缺少 `reference_answer`；
再让 Judge 失败、返回非法 JSON或缺少可选反馈字段。

预期：每次均给出可理解错误并回主界面，不显示 traceback；生成/判分没有可靠完成前绝不新增
Error。缺少 feedback 可按空反馈处理，但 `is_correct` 缺失必须按保守策略判错或明确报错，
不能误判成功。

### EG-ERR-04 重试与退出

前置：可统计请求次数的代理。

步骤：前两次请求返回超时/5xx，第三次成功；另一次让所有重试失败；在退避等待或流式响应时
按 Ctrl+C。

预期：请求次数不超过客户端配置的最大重试次数，退避后成功只产生一份消息；全部失败只报告
一次最终错误。Ctrl+C 无 traceback，不产生半条 assistant JSON，已有用户进度可恢复。

## 7. P2：Prompt 质量回归

Prompt 或默认模型变更时，Fixture A、B、C 各完整执行一次 Grill；至少选其中两题执行 Teach，
并以三条摘要共同执行一次 Drill。每题按下表逐项打分，`是 = 1`、`否 = 0`。

### 7.1 Grill 评分（每题 5 分）

| 标准 | A | B | C |
|---|---:|---:|---:|
| 沿学生实际思路追问，而非猜测另一个错误原因 |  |  |  |
| 每次只问一个有信息增益的问题 |  |  |  |
| 全程不讲解、不暗示答案 |  |  |  |
| Pattern 具体、可迁移，未停留在“粗心/不会” |  |  |  |
| 总结准确解释“为何会错”，且及时结束 |  |  |  |

通过标准：每题至少 `4/5`，且“全程不讲解、不暗示答案”为强制项。

### 7.2 Teach 评分（每题 5 分）

| 标准 | 题 1 | 题 2 |
|---|---:|---:|
| 明确引用并纠正 Grill 发现的 Pattern |  |  |
| 数学结论、推导和适用条件正确 |  |  |
| 说明用户原思路为何当时看似合理、又为何失效 |  |  |
| 给出可迁移的检查流程，而非只讲原题 |  |  |
| 对后续追问聚焦回答，不重启整篇讲解 |  |  |

通过标准：每题至少 `4/5`，且“数学正确”为强制项。

### 7.3 Drill 评分（5 分）

| 标准 | 分数 |
|---|---:|
| 新情境，不是原题换数字 |  |
| 非选择题，信息充分且存在明确可判定答案 |  |
| 能诱发至少一个历史 Pattern |  |
| 难度与样本接近或略高，不过度拼接三个知识点 |  |
| 参考答案正确，Judge 能区分正确答案、错误答案和蒙对 |  |

通过标准：至少 `4/5`，且“参考答案正确”为强制项。若首次未通过，同一 Provider/Model 立即
重跑一次；第二次仍失败记 `FAIL`，一次失败一次通过记 `FLAKY`。

## 8. P2：首次配置、Provider 与模型

### EG-CFG-01 首次启动

前置：需隔离环境；没有 ErrGrind 配置文件。

步骤：启动应用，分别测试取消流程和完整选择 Provider、输入测试 Key、选择/手输 Model。

预期：取消时安静退出且不写半份配置；完成时配置保存、Key 不回显，随后进入主界面。
模型列表获取失败时可手输 Model ID，不因网络失败卡死。

### EG-CFG-02 切换 Provider

步骤：通过 `/config` 依次验证 Gemini、DeepSeek、OpenCode Go；在选择、Key、URL、Model
各阶段至少测试一次 Esc；最后发起一次实际 Grill 或等价最小请求。

预期：只有完整配置成功后才切换运行中的 client；取消不能留下 Provider/Key/Model 的混搭。
OpenCode 默认 URL 为当前 Go URL；三个 Provider 的成功请求均使用刚选的 Model。

### EG-CFG-03 `/model` 高频切换

步骤：在同一 Provider 下执行 `/model`，分别测试列表选择、列表拉取失败后手输、Esc 取消，
随后执行一次模型调用并重启。

预期：成功选择即时生效并持久化；取消保留旧 Model；主界面展示值、请求实际使用值、配置文件
值三者一致。

## 9. 按改动范围选择 Case

| 改动文件/范围 | 最少执行 |
|---|---|
| 任意 Python 代码 | 预检命令 + 全部 P0 |
| `cli/app.py`、启动/退出 | P0 + EG-CFG-01 + EG-ERR-04 |
| `cli/ui.py` | P0-01～04、P0-07～09、P1-09～11 |
| `cli/commands.py` | 全部 P0 + 相关 P1 状态机 Case |
| `db/schema.py`、`db/ops.py`、models | 全部 P0 + P1-01～10，重点核对状态表和重启 |
| `llm/client.py`、`llm/gemini.py` | P0-05、06、08 + 全部 API 异常 Case + 对应 Provider |
| `prompts/grilling.md` | P0-05 + P1-02、05、06 + P2 Prompt 全套 |
| `prompts/teach.md` | P0-06 + P1-03、04 + P2 Teach 评分 |
| `prompts/drill.md`、`judge.md` | P0-08 + P1-01、07、08 + ERR-03 + P2 Drill 评分 |
| 配置默认值、安装脚本 | P0-01、09 + P1-10 + P2 配置全套 |

## 10. 本轮通过门槛

一次调试可标记为“回归通过”必须同时满足：

1. 预检两条命令通过。
2. 全部 P0 为 `PASS`，不能用 `BLOCKED` 代替核心闭环。
3. 与改动范围对应的 P1/P2 已执行且没有 `FAIL`。
4. 没有未捕获异常、数据丢失、非法状态或 Key 泄露。
5. LLM 相关 `FLAKY` 已记录 Provider、Model、输入与输出，不能静默算作通过。

回归结束后恢复本轮改过的配置，并在 `/resume` 中按唯一前缀逐条确认、删除测试记录；删除前
保留失败 Case 所需的输出或缺陷记录。
