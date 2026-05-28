# agentllm 架构设计

## 项目定位

`agentllm` 是一个模块化的 Agent 运行时。它的核心目标不是把所有逻辑堆在一个脚本里，而是把模型调用、工具执行、记忆、安全策略、MCP 集成和 CLI 启动流程拆开，让每一层都可以独立替换和扩展。

当前项目支持：

- OpenAI-compatible Chat Completions provider
- 同步 Agent 推理循环
- 本地内置工具
- MCP 工具发现与调用
- 短期窗口记忆和摘要记忆
- 基础安全策略与敏感信息脱敏
- 交互式 CLI 入口

## 总体调用链

```text
agent_llm.py
  -> agentllm.application.cli
  -> agentllm.application.bootstrap
  -> agentllm.core.agent.Agent
     -> agentllm.core.policy.LLMPolicy
        -> agentllm.providers.openai.OpenAIProvider
     -> agentllm.tools.registry.ToolRegistry
        -> builtin tools
        -> MCPTool adapters
     -> agentllm.core.memory.Memory
     -> agentllm.core.safety.SafetyPolicy
```

一次标准对话的大致流程：

1. CLI 读取用户输入。
2. `Agent.run()` 对输入做脱敏和安全检查。
3. 用户消息写入记忆。
4. `LLMPolicy` 把上下文交给 provider。
5. provider 调用 OpenAI-compatible API。
6. 模型返回最终回答，或返回一个或多个工具调用。
7. Agent 执行工具，并把工具结果作为 `tool` 消息写回记忆。
8. 如果还没有最终回答，Agent 继续下一轮推理。
9. 达到最终回答或最大步骤数后返回 `AgentResult`。

## 分层说明

### application

相关文件：

- `agentllm/application/cli.py`
- `agentllm/application/bootstrap.py`

职责：

- 加载运行时配置。
- 创建 provider、policy、memory、safety policy 和 tool registry。
- 决定是否启用 MCP。
- 启动交互式 CLI。
- 在退出时关闭 MCP client。

`bootstrap.py` 是组装层，不应该承载复杂业务逻辑。它的价值是让核心 Agent 可以在 CLI、测试、Web 服务或其他入口中复用。

### core

相关文件：

- `agentllm/core/agent.py`
- `agentllm/core/types.py`
- `agentllm/core/policy.py`
- `agentllm/core/memory.py`
- `agentllm/core/safety.py`

职责：

- 定义 Agent 运行时的核心抽象。
- 维护对话状态。
- 执行 ReAct 风格的推理和工具调用循环。
- 应用安全策略。
- 返回结构化结果，而不是直接打印。

核心数据结构：

- `Message`：统一表示 user、assistant、tool 等消息。
- `ToolCall`：模型请求调用工具时的结构。
- `ToolResult`：工具执行后的统一结果。
- `ToolSpec`：暴露给模型看的工具描述和 JSON Schema。
- `AgentContext`：provider 所需的系统提示词、消息历史和工具列表。
- `AgentAction`：provider 返回给 Agent 的下一步动作。
- `AgentResult`：Agent 对外返回的最终结果。

### providers

相关文件：

- `agentllm/providers/openai.py`

职责：

- 将内部的 `AgentContext` 序列化为 OpenAI-compatible `/chat/completions` 请求。
- 将模型响应解析成 `AgentAction`。
- 处理 tool calls、reasoning content、usage metadata 和基础重试。
- 屏蔽具体模型服务的协议细节，让上层 Agent 不直接依赖 HTTP 响应结构。

当前 provider 使用 `urllib`，保持依赖较轻。后续如果要支持 streaming、异步调用或更丰富的超时控制，可以把 provider 层扩展成多个实现。

### tools

相关文件：

- `agentllm/tools/base.py`
- `agentllm/tools/registry.py`
- `agentllm/tools/builtin.py`
- `agentllm/tools/mcp.py`

职责：

- 定义 `Tool` 和 `FunctionTool` 抽象。
- 注册并查找可用工具。
- 将工具 schema 暴露给模型。
- 执行本地工具或 MCP 工具。

当前内置工具包括：

- `calculator`
- `utc_now`
- `web_search`
- `read_file`
- `read_pdf_file`
- `read_docx_file`
- `get_weather`
- `write_file`

工具层需要特别注意安全边界。读写文件类工具后续建议统一限制在 workspace 或显式 allowlist 内，避免模型拿到过宽的文件系统能力。

### memory

相关文件：

- `agentllm/core/memory.py`
- `agentllm/memory.py`

当前实现：

- `ConversationMemory`：保留完整短期消息。
- `WindowMemory`：只保留最近 N 条消息。
- `SummaryMemory`：把旧消息压缩成摘要，同时保留近期上下文。
- `RuleCompressor`：不调用模型的规则摘要器。

现在 bootstrap 默认使用 `WindowMemory(max_messages=16)`。如果要做更长对话，建议把 memory 类型和参数改成配置项，例如：

```env
MEMORY_MODE=window
MAX_MESSAGES=16
SUMMARY_MAX_CHARS=1200
```

后续可以扩展持久化记忆，例如 JSON、SQLite、向量库或项目级知识库。

### safety

相关文件：

- `agentllm/core/safety.py`
- `agentllm/safety.py`

职责：

- 限制用户输入长度。
- 拦截明显的 prompt injection 模式。
- 限制可调用工具集合。
- 限制单步工具调用数量和工具参数大小。
- 对 API key、token、password 等敏感信息做脱敏。

安全策略目前是轻量版本，适合作为运行时防线的起点。更严格的生产环境还需要补充路径沙箱、工具权限分级、审计日志和危险操作确认机制。

### MCP

相关文件：

- `agentllm/mcp_client/client.py`
- `agentllm/integrations/mcp/client.py`
- `agentllm/tools/mcp.py`
- `agentllm/mcp_servers/workspace.py`

MCP 相关模块分成三层：

- `mcp_client`：实现 stdio 和 remote HTTP 两种 MCP client。
- `integrations/mcp`：对外暴露稳定导入入口。
- `tools/mcp.py`：把远程 MCP tool 包装成本地 `Tool`。

当前支持：

- stdio MCP server
- remote HTTP MCP server
- `tools/list`
- `tools/call`
- 同步 Agent 对异步 MCP client 的 blocking facade

本地 `workspace.py` MCP server 提供文件读取、目录列表和文本搜索能力，并把路径限制在项目 workspace 内。

## 配置加载

相关文件：

- `agentllm/infra/config.py`

`OpenAISettings.load()` 会从环境变量或 `.env` 加载：

- `API_KEY` 或 `OPENAI_API_KEY`
- `BASE_URL` 或 `OPENAI_BASE_URL`
- `MODEL` 或 `OPENAI_MODEL`
- `TIMEOUT_SECONDS` 或 `OPENAI_TIMEOUT_SECONDS`

`.env` 查找顺序：

1. 当前工作目录下的 `.env`
2. `agentllm/.env`

配置层只负责读取和校验，不负责创建 Agent。这样可以让配置在 CLI、测试和其他入口中复用。

## 错误处理策略

当前错误处理以“不中断整个会话”为目标：

- 启动配置错误在 CLI 层提示。
- provider HTTP 和网络错误转换成项目内异常。
- 工具内部异常转换成 `ToolResult(ok=False, ...)`。
- Agent 运行时异常转换成 `AgentResult`。
- MCP client 连接或请求错误转换成 MCP 相关异常。

这种策略适合交互式 Agent：一次工具失败不应该直接杀死整个进程，模型可以拿到失败信息后继续解释或选择下一步。

## 兼容层

为了避免破坏旧导入，项目保留了一些顶层兼容模块：

- `agentllm/agent.py`
- `agentllm/config.py`
- `agentllm/errors.py`
- `agentllm/memory.py`
- `agentllm/policy.py`
- `agentllm/safety.py`
- `agentllm/types.py`

新代码建议优先从 `agentllm/core`、`agentllm/infra`、`agentllm/providers`、`agentllm/tools` 等分层模块导入。

## 主要扩展点

### 新增 provider

实现 `LLMProvider.complete(context: AgentContext) -> AgentAction`，然后在 bootstrap 中替换 `OpenAIProvider` 即可。

适合扩展：

- OpenAI Responses API provider
- DeepSeek provider
- Ollama / 本地模型 provider
- 测试用 fake provider

### 新增工具

推荐使用 `FunctionTool` 包装普通函数：

1. 定义 handler。
2. 定义 JSON Schema。
3. 在 `_build_base_registry()` 中注册。

如果工具需要状态、连接池或复杂生命周期，可以直接实现 `Tool`。

### 新增 MCP server

可以新增独立 MCP server 文件，并通过 `build_agent_with_mcp()` 或 `build_agent_with_remote_mcp()` 注册工具。

后续建议引入 `mcp.json`，支持多个 MCP server 配置、headers、环境变量和工具命名空间。

### 新增 memory 后端

实现 `Memory` 协议：

- `append(message)`
- `all_messages()`
- `system_prompt`

可以扩展为：

- JSON 文件记忆
- SQLite 记忆
- summary + vector hybrid memory
- per-project memory

## 测试范围

相关文件：

- `tests/test_memory.py`
- `tests/test_mcp_tool.py`

当前测试重点覆盖：

- SummaryMemory 的压缩行为。
- MCP 工具包装和调用转发。
- 同步 runtime 对异步 MCP client 的桥接。

后续建议补充：

- `Agent.run()` 的工具调用循环测试。
- provider 响应解析测试。
- safety policy 拦截测试。
- builtin tools 的文件安全测试。
- CLI/bootstrap 配置测试。

## 当前技术债

建议优先处理：

1. 统一文档、注释和测试文件的 UTF-8 编码。
2. 增加 `pyproject.toml` 或 `requirements.txt`，声明核心依赖和可选依赖。
3. 修复 memory 测试与当前摘要文案之间的不一致。
4. 给文件读写工具增加统一 workspace 沙箱。
5. 把 memory、max steps、tool call limit 等运行时参数配置化。

这些处理完成后，项目会更适合继续扩展成完整的本地 Agent 框架。
