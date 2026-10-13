# Related Work 调研（2026-10）

## 背景与结论

验证 GitHub 上是否已有项目实现 ErrGrind 的核心闭环：真实错误锚点 → 证据 → Grill（竞争性假设的区分式取证）→ Teach → Drill → Judge（答对≠修好）→ 跨错误可证伪 Pattern。

**结论：没有同款。** 扫描 30 个查询族、852 个去重候选后，完整闭环无人实现；但每个片段都有人做过，且 2026 年内密集涌现。ErrGrind 的差异化在四件事同时成立：真实错误为锚、Grill 是假设区分式取证而非教学法 Socratic、错误血缘（答错派生新 Error）、跨错误可证伪 Pattern。

原始数据：`candidates.tsv`（去重候选，按 stars 排序）、`results.jsonl`（每行含命中 query）、`queries.txt`（可复现查询表）。第二轮补跑重建（首轮数据随 /tmp 丢失），关键命中以 `manual-known-hit` 标记补齐。

## 最接近：Diagnos（已实测验证）

[neterfk-coder/Diagnos-proyect.edu](https://github.com/neterfk-coder/Diagnos-proyect.edu)（黑客松项目，MIT，demo 在线 diagnos-hazel.vercel.app，游客模式可用）。一元一次方程错题：拍过程 → 定位首个断步 → 归入 12 条文献有据的 misconception 目录 → 只提问不给答案的 Socratic 对话 → 生成 3 道递进练习 → 双轴判分。

代码层验证（约束真实存在，非 README 宣称）：

- `lib/misconceptions.js`：12 条代数 misconception 闭目录（SIG-01 等），各引文献（Matz 1982、Küchemann 1981 等）。
- `app/api/diagnose/route.js`：首断步/后续拖下水分开标记；`explicacion` 有 BAD/GOOD 对，禁止泄露规则；strict json_schema。
- `app/api/socratic/route.js`：只提问、每轮一个短问题、连卡两轮给最小反例提示（仍不给解）、学生自悟后 `[DESCUBIERTO]` 哨兵。
- `app/api/exercises/route.js`：固定 3 题 basico/intermedio/desafio；desafio 必须含错误规则推出荒谬结果的边界情形。
- `app/api/verificar/route.js`：`correcto`（答案对）与 `reincide`（该 misconception 是否重现）双轴独立判定。

实机验证（提交新构造的移项不变号错误 `5x-7=23 → 5x=23-7`）：

- 诊断正确标记第 2 步断裂、归 SIG-01，解释只描述错误信念。
- 两次直接索要答案均被拒并换角度重问；装自悟后 `[DESCUBIERTO]` 触发、解锁练习。
- 练习三层正确（CHALLENGE `2x+5=2x-7` 正是"自爆结果"设计）。
- 重犯同错误 → 判 reincide + 追问不给答案；正确解答 → Overcome。
- **缺陷实锤**：提交过程全错但未犯该 misconception 的解法，反馈明说"步骤不成立、答案不对"却计入 "2/3 overcome"。`components/PracticaDirigida.jsx:19` `superado = !veredicto.reincide` —— overcome 只用 reincide 轴，correcto 轴算出不用。"答对≠修好"管住了，"答错≠没修好"没管。

与 ErrGrind 的本质差异：诊断是闭目录单轮分类（对话不改诊断），Socratic 是已知答案的教学法引导而非竞争性假设取证；无错误血缘；无跨题 Pattern；持久化层按 classroom JWT 聚合、面向教师热力图——数据模型里没有"个人学习者纵向身份"，自学地基不存在。机制层面 Grill 要求学习者有强反思意向，教师的强制力对此无效，教师向不是"窄市场"而是不成立。

## 其余值得记录的命中

| 项目 | 撞到的片段 | 缺的 |
|---|---|---|
| [Juroz4/kouhu_bot](https://github.com/Juroz4/kouhu_bot) | QQ 机器人直接命中"grill me 拷问口胡"场景：/cf /lg 抽题 → /提交 口胡思路 → DeepSeek 判对错+hint | 一次性判题，无诊断无追踪 |
| [hexa3/lucid](https://github.com/hexa3/lucid) | 费曼讲解 → embedding 召回 misconception 目录 + LLM 验证（要求引用学生原话当证据，类证据契约）→ mastery + SM-2 | 锚是主动讲解非真实错误；taxonomy 手写 4 主题；hackathon demo（mock LLM） |
| [ArnaudGuiovanna/tutor-mcp](https://github.com/ArnaudGuiovanna/tutor-mcp) | MCP 把任意 LLM 变 ITS：BKT mastery + FSRS + misconception 记忆 + transfer probe + 可审计决策 | 通用学习引擎，非错误调试器 |
| [parvpatodia/Feynman-Loop](https://github.com/parvpatodia/Feynman-Loop) | explain-back 判分必须引用用户原话且代码核验（provenance 思路） | 无错误锚点无诊断 |
| [digitalboy/RCAE_graph_data](https://github.com/digitalboy/RCAE_graph_data) | 数学根因分析+练习系统，知识图谱定位薄弱根因 | 主系统闭源，仅数据集公开 |
| 中文错题本家族（[ai-wrong-notebook](https://github.com/tjunsh/ai-wrong-notebook)、[E-Bu](https://github.com/aymwoo/E-Bu)、[edustar](https://github.com/zqdwinding-create/edustar)、[zhixue-wrongbook](https://github.com/QiuMo246/zhixue-wrongbook)、student-kpscan、gongkao-ai 等十余个） | "拍照 → AI 错因归类 → 举一反三 → 间隔复习"已成套路 | 错因=知识点分类，无假设拷问，无"答对≠修好" |
| grill-me skill 生态（[mattpocock/skills](https://github.com/mattpocock/skills) 原版 + zhudan930612/grill-me 等十余 fork/变体） | "拷问"作为动词已普及 | 全部无状态 prompt skill |
| KT/BKT 学术栈（pyBKT、OATutor、DeepKnowledgeTracing、pykt-toolkit） | mastery 概率追踪成熟 | 题库驱动、无错误叙述、无反思拷问 |

## 市场结构解释

做诊断深度的都是教师向产品（需班级规模摊薄成本、heatmap 才有商业价值）；做个人工具的都是错题本式浅分类。"自学者的错误调试器"是商业上无人愿占的格子——ErrGrind 为自己而做恰好落在无人区。

## 可带走的设计点

- Diagnos 的 CHALLENGE 题（让错误规则推出 `5 = -7` 式自爆结果）与 ErrGrind 区分式 probe 互相印证。
- `reincide` 轴实现方式（判"该 misconception 是否重现"独立于答案对错）可参考；其"只用一个轴"的缺陷反向证明 math_status 与 mechanism_evidence 都需进进度。
- 若做"口胡拷问"独立入口，kouhu_bot 验证了 QQ 群场景存在（NoneBot2+NapCatQQ 栈可直接复用）。
