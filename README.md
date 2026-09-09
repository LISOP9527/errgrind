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

- **Errors**：查看题目、回顾性思路、参考答案及公开 Grill/Teach 对话。
- **Record**：三个字段分别输入文字或上传 PNG/JPEG/WebP 图片。图片发送给当前 provider；
  OCR 只追加到可编辑草稿，校对后点击保存才创建 Error。原图不进入业务数据库，临时上传文件随请求删除。
- **Grill**：开始/恢复、逐次回答、暂停；完成后只读。失败后可从已保存对话恢复。
- **Teach**：开始/继续讨论、保存结束；结束后仍能继续。
- **Drill**：Prepare 显示 spec/draft 阶段，题目生成后提交答案与推理进行 Judge；
  显示反馈，错误判分产生的新 Error 可直接打开。

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
