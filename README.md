# ErrGrind

ErrGrind 通过数学错题中的思考 Evidence 进行 Grill 诊断、Teach 讨论与 Drill 练习。
一次练习正确不代表长期机制已修复；设计边界见 [design/](design/README.md)。

## 本机 WebUI

在仓库目录中安装并启动（需要 Python 3.11+）：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[web,codex]'
.venv/bin/errgrind web
```

打开 **http://127.0.0.1:8765**。隔离测试可添加 `--db /tmp/errgrind-test.db`。使用现有 ErrGrind 配置、Prompt 与 SQLite 数据库。
激活 `.venv` 后也可以直接使用 `errgrind web`；或者继续使用 `.venv/bin/python -m errgrind.web`。`--host`、`--port` 和 `--db` 参数保持不变。
可在 WebUI 的 **Settings** 中调整 Provider、从当前账号目录选择 Model、设置 Codex effort、API Key、OpenCode 地址、Drill 上下文条数和 Grill 最大轮数；修改后会自动保存并显示状态。模型目录只表示 Provider 对当前凭据列出的模型，不会逐个发起生成验证；目录暂时不可用时会保留当前或默认回退项。自动保存完成后，当前 Web 进程的后续模型请求立即使用新值；若通过 CLI 修改配置，需重启 Web 进程。使用 Codex 时，先运行 `.venv/bin/errgrind`，在首次配置或 CLI 的 `/config` 中选择 Codex，然后按提示完成浏览器或设备码登录；Web Settings 只读取现有 OAuth 模型目录并调整已保存的模型与 effort，不负责 OAuth 登录。

- **一个 workspace**：左侧提供 New error、Drill、最近 Error history 和底部 Settings；Grill、Teach、Judge 等是当前 Error 对话中的活动，不是独立页面。
- **Error**：打开后在同一个连续对话工作区中查看 Original Error、Grill、本次诊断、Teach 和下一步动作。原题自然出现在开头，顶部可随时重新打开题目和元数据。
- **New error**：文字与可选的 PNG/JPEG/WebP 图片直接作为多模态输入交给当前模型；模型在同一个对话工作区中整理题目、当时思路和参考答案，用户可继续补充或纠正。点击 Grill 时确认当前整理结果并创建 Error，原始图片作为 Error 附件保留。
- **Grill / Teach**：在同一个 Error 对话工作区中开始、恢复、暂停或继续讨论；完成的 Grill 只读，Teach 保存后仍可继续。两者都可以用同一个 `+ 图片` 入口发送图片；图片随用户消息持久化，失败后刷新可恢复，消息中显示已保存的图片附件。
- **Drill**：一次临时练习只展示题目、答案输入和“正确/错误”结果。答案文字与图片直接交给 Judge；答错时按现有 Core 语义产生新的 Error，并保留答案图片 provenance。

模型工作时显示等待秒数并禁止重复操作；Grill 不模拟逐 token 输出。
Markdown 与数学公式在浏览器展示，原始文本保持不变；渲染资源随包提供，无需外部 CDN。

默认只监听 localhost。远程机器使用 SSH 转发，例如
`ssh -L 8765:127.0.0.1:8765 user@server`，然后打开本机 URL。
需要局域网手机测试时可显式添加 `--host 0.0.0.0`；**没有内建账号系统，能访问端口的人就能访问数据和调用模型，请勿暴露公网**。
CLI 继续作为 debug/fallback frontend。

## 范围与限制

这是单用户、单进程 Web adapter，工作流由 `ErrGrindApplication` 管理，SQLite 是业务事实来源。
不同时启动多个 Web worker，也不要在 Web 正在执行同一 Error 时用 CLI 修改它。
未判分 Drill 的准备结果及页面请求去重信息只在当前 Web 进程保留；重启后需要重新 Prepare。
已完成 Judge 的 attempt 与可能派生的 Error 会持久化到 SQLite。
本版不提供历史 Drill 浏览器、账号、MCP、V2 Pattern/Policy 或未来 Judge schema。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Web integration tests 使用临时数据库和 fake LLM，不调用真实 provider。
仅安装基础依赖时 Web 测试会因缺少 Web 依赖而跳过；安装 `web` extra 后可运行 Web 测试。需要 Codex OAuth 的用户还应安装 `codex` extra（上面的完整安装命令已同时启用两个 extra）。

## React + assistant-ui 前端

正式 React 前端源码位于 [`frontend/`](frontend/README.md)，生产构建由同一个 Flask/Waitress
进程提供。assistant-ui 负责通用对话、附件和滚动等 UI primitive；Flask、
`ErrGrindApplication` 与 SQLite 继续保持 workflow、持久化和 provenance 权威。迁移决策见
[React + assistant-ui WebUI 正式迁移](design/decisions/2026-09-14-assistant-ui-migration.md)。
