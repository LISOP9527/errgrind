# Web Settings 编辑共享配置

## Context

WebUI 的 Settings 只展示启动时读取的 Provider、Model 和 reasoning effort。调整配置仍需打开 CLI；CLI `/config` 的 Drill 上下文条数、Grill 最大轮数，以及 `/model` 的模型选择也无法在 WebUI 中编辑。Web 和 CLI 共享 `~/.config/errgrind/config.json`，而模型请求在 Web adapter 中按需创建客户端。

## Decision

Settings 提供 Provider、Model ID、Codex reasoning effort、API Key、OpenCode API 地址、Drill 上下文条数和 Grill 最大轮数的编辑表单。模型 ID 可手动输入；页面不在打开时发起模型目录或认证请求。Codex 的登录仍由 CLI 完成，WebUI 只调整其已保存的模型与 effort。

保存使用现有配置文件；先校验并持久化完整候选配置，再替换当前 Web 进程的配置，后续模型请求使用新值。切换 Provider 时不沿用原 Provider 的 API Key；切到 Codex 时删除 API Key 和 OpenCode 地址。API Key 只接受新输入，页面不回显已保存或失败提交的密钥。写入保留 CSRF 和一次性提交保护，配置文件采用私有权限和原子替换。

## Rationale

Settings 是低频设置入口，不改变 Error-centric 信息架构。配置属于 adapter 的运行设置，不属于 Grill、Teach 或 Drill 工作流，因此不在 Web 路由中重组业务调用。手动模型 ID 与 CLI 的离线回退能力一致，也避免在打开设置时产生可能失败的远端请求。

## Consequences

已运行的 Web 进程能立刻使用自身保存的新配置。其他独立进程仍需重新加载配置。Codex 尚未登录时，用户需在终端完成登录；设置表单不会假装已验证 Provider 凭据或模型可用性。
