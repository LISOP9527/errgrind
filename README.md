# ErrGrind

ErrGrind 通过数学错题中的思考 Evidence 进行 Grill 诊断、Teach 讨论与 Drill 练习。
一次练习正确不代表长期机制已修复；设计边界见 [design/](design/README.md)。

## 本机 WebUI

在仓库目录中安装并启动（需要 Python 3.11+）：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[web]'
.venv/bin/python -m errgrind.web
```

打开 **http://127.0.0.1:8765**。隔离测试可添加 `--db /tmp/errgrind-test.db`。使用现有 ErrGrind 配置、Prompt 与 SQLite 数据库。
首次使用请先运行 `.venv/bin/errgrind` 配置 provider/model；已有用户可用 CLI `/config` 修改。
Web 不提供 OAuth 登录或完整配置界面，配置改变后重启 Web 进程。

- **一个 workspace**：左侧提供 New error、Drill、最近 Error history 和底部 Config；Grill、Teach、Judge 等是当前 Error 时间线中的活动，不是独立页面。
- **Error**：打开后按 Original Error → Grill → 本次诊断 → Teach → 下一步 → 当前输入的连续时间线查看。原题自然出现在开头，顶部可随时重新打开题目和元数据。
- **New error**：先用一段文字和可选的 PNG/JPEG/WebP 图片描述错题；模型整理出的结构化草稿必须由用户编辑、确认后才保存。缺少的用户思路不会由模型补写，图片只用于本次整理，原图不入库，临时文件随请求删除。也可以直接填写并确认草稿。
- **Grill / Teach**：在同一条 Error 时间线中开始、恢复、暂停或继续讨论；完成的 Grill 只读，Teach 保存后仍可继续。
- **Drill**：一次临时练习只展示题目、答案输入和“正确/错误”结果。答案图片会先转成可编辑草稿；答错时按现有 Core 语义产生新的 Error 并直接进入它。

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
本版不提供历史 Drill 浏览器、账号、MCP、V2 Pattern/Policy、完整设置界面或未来 Judge schema。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Web integration tests 使用临时数据库和 fake LLM，不调用真实 provider。
仅安装基础依赖时 Web 测试跳过；安装 `web` extra 后运行全部测试。
