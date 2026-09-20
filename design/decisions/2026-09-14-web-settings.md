# Web Settings 编辑共享配置

## Context

WebUI 的 Settings 最初只展示启动时读取的 Provider、Model 和 reasoning effort。2026-09-14 的实现将完整配置编辑迁入 Web，但为了离线回退，Model ID 仍为手动输入。2026-09-19 决定由 Settings 根据当前 Provider 和凭据读取模型目录，并将 Model 改为选择项。Web 和 CLI 继续共享 `~/.config/errgrind/config.json`，而模型请求在 Web adapter 中按需创建客户端。

## Decision

Settings 提供 Provider、Model、Codex reasoning effort、API Key、OpenCode API 地址、Drill 上下文条数和 Grill 最大轮数的编辑表单。Model 使用选择框：页面打开时自动读取当前 Provider 的目录；切换 Provider 后，Codex 直接读取 OAuth 目录，其他 Provider 在已有同 Provider 凭据或用户输入新 Key 后读取目录。浏览器只请求 ErrGrind 自身的只读目录接口，由后端调用 Gemini `models.list`、DeepSeek/OpenCode 的兼容 `/models` 或现有 Codex catalog，并只返回白名单模型元数据。

目录探测不持久化表单中的临时 Key，也不把 Key 放入 URL、响应或日志。DeepSeek 固定使用官方地址；OpenCode 允许配置兼容地址，但已保存 Key 只可用于已保存地址，修改地址时必须重新输入 Key。OpenCode Go 的目录同时包含 Responses、Anthropic Messages 和兼容 Chat Completions 模型；当前 ErrGrind adapter 只展示它实际支持的 Chat Completions 模型，自定义兼容地址则信任该地址返回的目录。目录只表示当前凭据可见且协议匹配的模型，不通过逐模型生成来验证可调用性。目录失败或当前模型不在目录时，保留已保存模型；切换 Provider 时保留该 Provider 的默认回退项并明确标记为未验证，因此短暂网络故障不会阻断配置恢复。Codex reasoning effort 优先使用所选模型随目录返回的支持范围。

Codex 的登录仍由 CLI 完成，WebUI 只读取其现有 OAuth 凭据并调整已保存的模型与 effort。

设置项修改后由页面自动保存，不再提供单独的“保存设置”按钮。选择项和数字使用短延迟保存；API Key 与 OpenCode 地址在编辑期间保持待保存，完成输入并离开字段后再提交，避免把半截凭据或地址写入配置。页面只提交满足表单约束的完整候选配置，并串行处理写入；保存期间产生的新修改会在当前请求完成后继续保存，旧响应不得覆盖较新的本地输入。页面明确显示待保存、保存中、已保存或保存失败状态。短暂不完整的数字输入不提交，失败时保留用户编辑，供修正后重试。

保存使用现有配置文件；先校验并持久化完整候选配置，再替换当前 Web 进程的配置，后续模型请求使用新值。切换 Provider 时不沿用原 Provider 的 API Key；切到 Codex 时删除 API Key 和 OpenCode 地址。API Key 只接受新输入，页面不回显已保存或失败提交的密钥；自动保存成功后只清空已经提交且期间未再次修改的 Key 输入。写入保留 CSRF 和一次性提交保护，配置文件采用私有权限和原子替换。

## Rationale

Settings 是低频设置入口，不改变 Error-centric 信息架构。配置和模型目录属于 adapter/provider 能力，不属于 Grill、Teach 或 Drill 工作流，因此不在 Web 路由中重组业务调用。复用统一 provider discovery 可以避免 Web 与 CLI 各自维护易漂移的模型清单；保留显式的当前/默认回退项，则兼顾动态目录与离线恢复。

## Consequences

已运行的 Web 进程能在自动保存完成后使用新配置；保存状态会提示修改是否已经落盘。其他独立进程仍需重新加载配置。打开 Settings 和修改 Provider/凭据时会产生只读目录请求，但不会产生模型生成 token；目录失败会显示安全错误并保留回退选择。目录列出不等于模型已通过 ErrGrind 的真实生成验证。Codex 尚未登录时，用户仍需在终端完成登录。
