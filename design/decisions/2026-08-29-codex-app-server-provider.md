# Codex provider 通过官方 app-server 接入

## Context

ErrGrind 需要允许用户使用自己的 ChatGPT Codex 订阅，同时保持现有
`chat`、`stream_chat`、`chat_json` 三种 LLM 调用方式。Codex 登录包含浏览器
OAuth、device-code、token 持久化和刷新；如果 ErrGrind 自行复制第三方客户端的
OAuth 实现，就必须直接持有 refresh token，并依赖未承诺稳定的认证细节和后端请求
格式。

ErrGrind 是本地 Python CLI，不需要成为 OAuth 凭据管理器，也不应读取或解析
`~/.codex/auth.json`。

## Decision

- 新增 `codex` provider，通过 OpenAI 官方 `openai-codex` Python SDK 启动本地
  Codex app-server。
- ChatGPT 浏览器登录、device-code 登录、凭据保存和 token 刷新全部交给
  app-server；ErrGrind 配置文件只保存 provider 和 model，不保存 Codex token。
- 每次 ErrGrind 请求创建一个只读、拒绝所有执行审批的 ephemeral Codex thread，
  禁止 shell、文件修改和网络工具，把 Codex 仅作为文本模型使用。
- 现有消息历史在适配器边界转换，业务层和数据库继续保存标准
  `{role, content}` 会话，不保存 Codex thread id。
- JSON 请求使用 app-server 的 `output_schema`，并保留本地 JSON 解析与重试作为
  防御性校验。
- `openai-codex` 作为 `codex` 可选依赖，由 `install.sh` 在 runtime 可用时安装；
  runtime 不可用不能阻止 Gemini、DeepSeek 或 OpenCode 用户安装 ErrGrind。
- `install.sh` 会核对已有 `.venv` 与当前 `python3` 的平台、主次版本和架构；若旧环境
  来自 Termux/Android Python 而当前已进入 PRoot Linux，则先改名备份旧环境，再用
  PRoot Python 重建，确保 pip 选择正确的 manylinux runtime。

## Rationale

- 官方 SDK/app-server 是公开、版本化的集成边界，认证生命周期由 OpenAI 维护。
- ErrGrind 不接触 access token 或 refresh token，降低泄露、权限和迁移风险。
- ephemeral thread 避免出现数据库历史与 Codex thread 历史两份状态源；SQLite
  仍是 ErrGrind 会话的唯一事实来源。
- 只读 sandbox 与 deny-all approval 将 provider 权限限制为生成文本所需的最小范围。

## Consequences

- 启用 Codex 时安装体积会增加，因为官方 Python SDK 带有固定版本的 Codex runtime；
  不使用 Codex 的基础安装不承担这项依赖。
- 解释器不兼容时重建 `.venv` 会重新下载依赖，但旧环境会保留为
  `.venv.backup-*`，不会直接删除。
- Codex provider 依赖 ChatGPT 账户可用的 Codex 模型和订阅限额；它不是 Platform
  API key 的替代计费通道。
- Codex 本质上以 agent thread 为中心。适配器必须持续用离线回归测试约束消息转换、
  流式 delta、JSON 输出、登录取消和失败重试。
- SDK 升级需要先验证公开 API 和回归测试，再放宽 `pyproject.toml` 中的版本上限。
