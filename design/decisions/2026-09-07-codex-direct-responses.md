# Codex OAuth 生成直连 Responses

## Context

ErrGrind 需要让模型只接收本次业务 Prompt 与对话。替换 app-server 的
`base_instructions` 不能证明同时移除了全局 AGENTS、技能与运行环境等附加上下文。
用户要求参考 Pi 的实现，使 Codex OAuth 调用接近普通模型 API。

Pi 的 [Codex transport](https://github.com/earendil-works/pi/blob/7d8ab31a477ecc07b36f56ffcae58c79307a68be/packages/ai/src/api/openai-codex-responses.ts)
直接向 ChatGPT Codex Responses 后端发送调用方的 `instructions` 和 `input`，不运行
Codex agent。本决策替代旧 app-server 决策中的生成路径和不读取登录文件的约束；
保留官方 SDK 管理登录、刷新与模型目录。

## Decision

- `chat`、`stream_chat`、`chat_json` 和图片转录统一通过 HTTP SSE 请求固定的
  `https://chatgpt.com/backend-api/codex/responses`，不创建 Codex thread，也不回退到
  agent 生成。HTTP 重定向关闭，凭据不能随重定向发送至其他地址。
- 业务 system 消息成为 `instructions`；历史 user/assistant 消息保留原生角色。
  不附加 Codex 或 Pi 的系统 Prompt、工具定义、AGENTS、技能或环境信息。
- 每次提交完整业务历史，固定 `store=false`、`stream=true`；不传服务端会话或前次
  response id。SQLite 保持会话事实来源，不改变 Application 的状态与持久化规则。
- JSON Schema 映射到 `text.format`；保留本地 JSON 解析与有限 repair。图片由既有
  校验器读取，以 data URL 发送，文件路径不进入请求；原图仍不入库。
- OAuth 登录、凭据保存和刷新继续使用官方 SDK。生成时只读 `$CODEX_HOME/auth.json`
  （默认 `~/.codex/auth.json`）中的 access token 与 account id，不自行刷新、复制或
  写入凭据。接近到期时先由 SDK 刷新；HTTP 401 在没有输出时最多刷新并重试一次。
- SDK 仅在登录、刷新或查询目录时延迟启动；有效凭据下纯生成不启动 app-server。
  仅存储于钥匙串的凭据暂不支持直连，显示明确错误，不静默降级到 agent。
- 保留模型 ID、effort 与现有 CLI 入口。未设置 effort 时省略请求字段，使用后端默认，
  不再继承用户 Codex 配置中的默认生成参数。Codex 可选依赖增加 HTTPX SOCKS 支持，
  兼容已有代理环境，不修改用户代理设置。
- 仅对无输出时的瞬态网络错误、408、429、5xx 进行有限重试；输出后不自动重放。
  Ctrl+C、EOF 与回调异常关闭 HTTP 流并交还 Application。只有成功终态且存在可见
  assistant 文本才算成功；工具、拒绝、截断或失败响应不会作为完整答案返回。

## Rationale

认证与 agent prompt 组装是不同层。保留官方认证生命周期、独立构造生成 payload，
即可去掉客户端 Codex 上下文，不需要复制完整 Pi runtime，也不用重新实现 OAuth。
HTTPX 是已有依赖，SSE 满足当前同步和流式接口，暂不引入 WebSocket 或连接会话缓存。

## Consequences

- ErrGrind 会在内存中使用访问令牌；不得在错误、日志、配置或测试快照中打印真实凭据。
  HTTP 和协议错误不回显原始响应，JSON repair 的内部输出不作为公共错误返回。
- 生成依赖 ChatGPT Codex 后端协议，不等于稳定的 Platform Responses API 合约。
  不承诺移除服务端策略或模型固有行为；可验证的保证是客户端请求内容由 ErrGrind 控制。
- 离线测试检查实际 HTTP 请求与原生角色、Schema、图片、终态、中断、不重放与刷新边界。
  真实调用验证只使用合成输入，不读取业务数据库或发送历史错题。
- 2026-09-07 最小真实直连验证返回 HTTP 200、`response.completed` 与符合严格 Schema 的
  `{"result":"DIRECT_OK"}`，没有创建 Codex thread。
