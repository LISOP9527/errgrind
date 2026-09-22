# ErrGrind WebUI

这是 ErrGrind 的 React + assistant-ui 前端。生产使用时由同一个 Flask/Waitress 进程提供构建产物：

```bash
# terminal 1, repository root
.venv/bin/errgrind web --port 8765 --db /tmp/errgrind-webui.db

# terminal 2, repository root
cd frontend
npm ci
npm run dev
```

打开 `http://127.0.0.1:8765/`（开发时打开 `http://127.0.0.1:5173/`）。Vite 只代理 API；它不拥有业务状态。

React workspace 使用 assistant-ui 的 `ExternalStoreRuntime` 和 attachment adapter
负责消息呈现、图片选择/移除、已发送图片和自动滚动；Record 草稿、Error 持久化及 Grill 转换仍由
Flask → `ErrGrindApplication` 完成。

## 浏览器测试

首次安装 Chromium（Debian/Ubuntu 如缺少系统库，需让有 sudo 权限的用户执行）：

```bash
npx playwright install --with-deps --only-shell chromium
```

运行桌面和手机尺寸的 Chromium 冒烟测试：

```bash
npm run test:e2e
```

测试固定使用一个 worker，并自动启动仅监听 `127.0.0.1:8766` 的 Web 服务；配置和数据库写入
`/tmp/errgrind-playwright-*`，不会读取或改写日常 ErrGrind 数据。设置 `PLAYWRIGHT_BASE_URL`
时会跳过本地服务启动，改为测试指定地址。

## 当前限制

Record 的 `question`、`user_thoughts`、`reference_answer` 在当前 Application/SQLite contract 中仍是
文本列，Error 另外保留有序的 initial original attachments。Record 图片上传后会通过 pending attachment
机制立即持久化，后续纯文字调整和刷新不会让原图丢失；finalize 会把同一批附件原子归属到新 Error。
字段级 `question/user_thoughts/reference_answer → attachment refs` 仍未建模，刷新后前端也不能重建原来的
本地缩略图，只显示附件已保留。不要用 OCR 或模型转录伪造字段级图片语义。
