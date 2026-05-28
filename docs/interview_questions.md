# agentllm 项目面试题与参考答案

这份文档从“高级技术面试官”的角度，围绕 `agentllm` 项目的真实代码结构设计面试问题。题目覆盖架构、Agent 运行时、工具系统、MCP、记忆、安全、测试和工程化扩展。

建议复习方式：

- 先尝试自己回答，再看参考答案。
- 回答时尽量结合具体模块名和文件名。
- 如果被追问，不要只讲“怎么做”，也要讲“为什么这样做”和“代价是什么”。

## 一、项目整体理解

### 1. 请用一句话介绍这个项目。

**参考答案：**

`agentllm` 是一个模块化的本地 Agent 运行时，它把 CLI 入口、模型 provider、Agent 推理循环、工具注册与执行、MCP 集成、记忆管理和安全策略拆成独立模块，使系统可以逐步替换 provider、扩展工具、接入 MCP 服务，并保持核心运行时相对稳定。

**面试官可能追问：**

为什么不直接写一个脚本把模型调用和工具调用都放在一起？

**补充回答：**

如果只是 demo，一个脚本更快；但这个项目已经有工具、MCP、记忆、安全、配置和 provider 等多个变化点。分层以后，新增模型服务、替换记忆策略、扩展工具或把 CLI 换成 Web API 时，不需要重写核心 Agent loop。

### 2. 这个项目的核心调用链是什么？

**参考答案：**

核心链路是：

```text
agent_llm.py / application.cli
  -> application.bootstrap
  -> core.agent.Agent
  -> core.policy.LLMPolicy
  -> providers.openai.OpenAIProvider
  -> tools.registry.ToolRegistry
  -> builtin tools / MCPTool
  -> core.memory.Memory
  -> core.safety.SafetyPolicy
```

用户输入从 CLI 进入，`Agent.run()` 负责安全检查、写入记忆、请求 policy、执行工具、继续推理，最后返回 `AgentResult`。

### 3. 你觉得这个项目目前最重要的设计优点是什么？

**参考答案：**

最重要的优点是边界比较清楚：

- `Agent` 只负责运行时编排。
- `Policy` 只负责决策接口。
- `Provider` 只负责模型协议适配。
- `ToolRegistry` 只负责工具注册和查询。
- `Memory` 只负责上下文状态。
- `SafetyPolicy` 只负责输入和工具调用约束。

这种设计让项目有比较好的可测试性和可替换性。例如测试 Agent loop 时，可以用 fake provider；新增工具时，不需要修改 provider；新增 provider 时，也不需要改工具层。

### 4. 为什么让 `agent_llm.py` 只作为一个很薄的入口？

**参考答案：**

`agent_llm.py` 是顶层脚本入口，真正的 CLI 行为应该集中在 `agentllm/application/cli.py`。这样可以避免入口脚本和 application 层各自维护一套 REPL、配置加载、MCP 关闭逻辑，降低行为不一致的风险。

现在入口可以保持为：

```python
from agentllm.application.cli import run_cli

def main() -> None:
    run_cli()

if __name__ == "__main__":
    main()
```

这样 `agent_llm.py` 只负责启动程序，`application/cli.py` 负责交互流程，`application/bootstrap.py` 负责组装 Agent。三者职责边界更清楚。

## 二、Agent 运行时

### 5. 请解释 `Agent.run()` 的执行流程。

**参考答案：**

`Agent.run()` 大致分为几个阶段：

1. 对用户输入做 `strip()` 和敏感信息脱敏。
2. 使用 `SafetyPolicy.validate_user_input()` 检查输入是否合法。
3. 把用户消息写入 memory。
4. 在最大步数范围内循环调用 `policy.next_action()`。
5. provider 返回 assistant 消息和可能的 tool calls。
6. 如果没有 tool calls，说明模型已经给出最终回答，返回 `AgentResult`。
7. 如果有 tool calls，Agent 校验并执行工具。
8. 工具结果转换成 `tool` 消息写回 memory。
9. 下一轮继续把更新后的上下文交给模型。

这是一种同步的 ReAct 风格推理循环。

### 6. 为什么 `Agent` 返回 `AgentResult`，而不是直接打印输出？

**参考答案：**

这是为了让核心运行时和展示层解耦。CLI 可以打印 `AgentResult.output`，Web API 可以把它转成 JSON，测试可以直接断言 `messages` 和 `steps`。如果 `Agent` 内部直接打印，就会和终端强绑定，后续复用会困难。

### 7. `Agent` 为什么不直接调用 `OpenAIProvider`，而是通过 `LLMPolicy`？

**参考答案：**

`Agent` 依赖的是 `AgentPolicy` 协议，而不是具体 provider。这是策略模式的一种用法。当前 `LLMPolicy` 只是把请求转给 provider，但未来可以扩展出更复杂的策略，例如：

- 先判断是否需要工具。
- 多 provider 路由。
- 成本优先或速度优先的模型选择。
- 失败时 fallback 到另一个模型。
- 测试时使用 deterministic fake policy。

这样 `Agent` 的编排逻辑不需要关心策略内部怎么决策。

### 8. 最大推理步数有什么意义？

**参考答案：**

最大推理步数由 `SafetyPolicy.max_steps` 控制，主要是防止模型陷入无限工具调用循环。例如模型反复调用 `read_file` 但不输出最终回答，如果没有上限，Agent 可能一直运行。设置最大步数后，Agent 可以返回一个结构化错误结果，避免进程失控。

## 三、类型与抽象

### 9. `Message`、`ToolCall`、`ToolResult`、`AgentAction` 这些类型的价值是什么？

**参考答案：**

这些 dataclass 把 Agent 内部协议固定下来，避免每层都直接操作裸 dict。

- `Message` 表示对话消息。
- `ToolCall` 表示模型请求调用工具。
- `ToolResult` 表示工具执行结果。
- `AgentAction` 表示 provider 给 Agent 的下一步动作。
- `AgentResult` 表示 Agent 对外返回结果。

好处是边界清楚、可读性强、测试更方便，也方便以后把某一层替换掉。

### 10. 这个项目为什么使用 `Protocol`？

**参考答案：**

`Protocol` 用来表达结构化接口，而不是强制继承。比如 `Memory`、`AgentPolicy`、`LLMProvider` 只要求对象实现某些方法或属性，不要求继承特定基类。

这让项目更灵活：测试时可以写一个简单 fake 对象，只要方法签名满足协议即可。缺点是运行时约束较弱，更多依赖类型检查和测试保证。

## 四、Provider 与模型协议

### 11. `OpenAIProvider` 的职责是什么？

**参考答案：**

`OpenAIProvider` 的职责是协议适配：

1. 把内部 `AgentContext` 序列化成 OpenAI-compatible `/chat/completions` 请求。
2. 把 `ToolSpec` 转成 function tool schema。
3. 把模型返回的 message、tool calls、reasoning content 解析成 `AgentAction`。
4. 处理 HTTP 请求、重试、错误转换和 metadata。

它不应该负责工具执行，也不应该直接操作 memory。这样 provider 层可以独立替换。

### 12. 为什么要把工具历史消息里的 `tool_call_id` 带回 provider？

**参考答案：**

在 OpenAI-compatible tool calling 协议里，assistant 发起 tool call 后，后续的 tool 消息需要通过 `tool_call_id` 对应回那一次工具调用。否则模型服务可能无法判断哪个工具结果对应哪个调用，严重时会报协议错误。

所以 `ToolResult.to_message()` 会生成 `role="tool"` 的消息，并携带 `tool_call_id`。

### 13. 当前 provider 的重试策略有什么优点和不足？

**参考答案：**

优点是简单明确：只对常见临时错误重试，例如 `408`、`429`、`5xx`，避免配置错误或协议错误被长时间掩盖。

不足是还比较基础：

- backoff 策略比较简单。
- 没有 jitter。
- 没有按错误类型细分日志。
- 没有暴露 retry 配置。
- 没有请求级 trace id 贯穿整个 Agent run。

生产环境可以引入指数退避、jitter、超时预算和更完整的 observability。

### 14. 如果要支持流式输出，你会改哪里？

**参考答案：**

优先改 provider 和应用层：

- provider 增加 streaming 接口，例如 `stream(context) -> Iterator[Delta]`。
- `Agent` 需要支持边生成边返回，或者新增 `run_stream()`。
- CLI 层负责逐 token 打印。
- 工具调用仍然需要在完整 tool call 参数解析完成后执行。

关键是不要把 streaming 逻辑硬塞进当前 `run()`，否则会破坏已有同步调用和测试。可以保留 `run()`，新增流式路径。

## 五、工具系统

### 15. `Tool`、`FunctionTool` 和 `ToolRegistry` 分别解决什么问题？

**参考答案：**

- `Tool` 定义工具的最小契约：有 `spec`，能 `run(arguments)`。
- `FunctionTool` 用来把普通 Python handler 包装成工具，降低新增工具的成本。
- `ToolRegistry` 管理工具注册、查找和 schema 聚合。

这样模型只看到 `ToolSpec`，Agent 通过 registry 找到实际工具执行，工具实现细节不会泄漏到 provider 层。

### 16. 新增一个工具需要做哪些事？

**参考答案：**

通常需要：

1. 在 `agentllm/tools/builtin.py` 中写一个 `make_xxx_tool()`。
2. 定义 handler，输入是 `JsonDict`，输出是 `ToolResult`。
3. 定义 JSON Schema，包括参数类型、必填字段和 `additionalProperties`。
4. 在 `application/bootstrap.py` 的 `_build_base_registry()` 中注册。
5. 增加针对成功、失败和边界输入的测试。

如果工具有状态或生命周期，可以直接实现 `Tool`，而不是使用 `FunctionTool`。

### 17. 当前文件读写工具有什么安全风险？

**参考答案：**

`read_file` 当前根据传入路径直接 resolve 并读取，只检查文件是否存在和是不是目录。`write_file` 拦截了一些危险目录，但仍然不是完整沙箱。

风险包括：

- 模型可能读取用户不想暴露的本地文件。
- 模型可能写入 workspace 外部路径。
- Windows 和 Unix 的危险路径规则不同，手写 blacklist 容易漏。

更好的做法是统一加 workspace allowlist：所有文件工具只能访问项目目录或用户显式配置的目录，并在 resolve 后检查目标路径是否仍在允许根目录内。

### 18. 模型幻觉出一个不存在的工具名会发生什么？

**参考答案：**

`SafetyPolicy.validate_tool_call()` 会先检查工具是否在 allowed tools 中。如果工具名不允许，会抛出 `SecurityError`，Agent 会返回被安全策略阻止的错误结果。

即使绕过安全检查，`ToolRegistry.get()` 找不到工具也会抛出 `ValueError`。目前更理想的体验是把未知工具转换成一个 `ToolResult(ok=False)`，让模型有机会自我修正，而不是终止整个 run。

## 六、MCP 集成

### 19. 这个项目里的 MCP 分成哪几层？

**参考答案：**

MCP 相关代码大致分三层：

- `agentllm/mcp_client/client.py`：实现 stdio 和 remote HTTP MCP client。
- `agentllm/integrations/mcp/client.py`：对外暴露稳定导入入口。
- `agentllm/tools/mcp.py`：把远程 MCP tool 包装成本地 `Tool`。

本地还有一个示例 MCP server：`agentllm/mcp_servers/workspace.py`，提供 workspace 内的文件读取、目录列表和文本搜索。

### 20. 为什么 MCP client 明明是异步的，项目还要包一层同步 facade？

**参考答案：**

因为当前 `Agent.run()` 和 `Tool.run()` 都是同步接口。如果直接把 async MCP client 暴露给同步 Agent，会导致调用链不一致。

`BlockingMCPClient` 在后台事件循环中运行 async client，对外提供同步方法。这样现有 Agent 不需要改成 async，也能调用 MCP 工具。

代价是实现复杂度增加，并且如果未来项目整体迁移到 async，需要重新设计这层 bridge。

### 21. stdio MCP 和 remote HTTP MCP 的适用场景有什么区别？

**参考答案：**

stdio MCP 适合本地工具服务，例如 workspace 文件服务、数据库本地代理、命令行工具包装。它容易启动和调试，但通常绑定本机进程。

remote HTTP MCP 适合远程服务或共享工具平台，例如团队统一的知识库、搜索服务、内部 API 网关。它更适合服务化，但需要处理鉴权、网络失败、session 和超时。

## 七、记忆系统

### 22. `ConversationMemory`、`WindowMemory`、`SummaryMemory` 的区别是什么？

**参考答案：**

- `ConversationMemory`：保留完整消息，简单但上下文会不断增长。
- `WindowMemory`：只保留最近 N 条消息，成本稳定，但会遗忘早期信息。
- `SummaryMemory`：把旧消息压缩成摘要，同时保留近期消息，适合长对话，但摘要质量会影响后续回答。

当前 bootstrap 默认使用 `WindowMemory(max_messages=16)`，是一个保守选择。

### 23. `SummaryMemory` 为什么要按完整轮次压缩？

**参考答案：**

因为 Agent 对话不是简单的 user/assistant 交替，还可能有 assistant 发起工具调用，随后 tool 返回结果。如果只按数量硬切，可能把一次工具调用的上下文切断，导致摘要里缺少关键因果关系。

按完整轮次压缩可以尽量保留：

```text
user 问题
assistant 决定调用工具
tool 返回结果
assistant 最终回答
```

这样的结构对后续理解更可靠。

### 24. 当前 memory 测试失败时，你会如何排查？

**参考答案：**

我会先看失败断言和当前实现是否在“行为”还是“文案”上不一致。比如测试期望摘要里出现“用户提到”“助手回应”，但实现输出可能是“用户说”“助手回复”。如果压缩逻辑没问题，只是文案变化，应该统一测试和实现的约定。

同时还要检查 `summary_prefix` 是否和测试预期一致。如果测试期望 `Conversation summary:`，而实现是中文前缀，也会导致失败。

更好的测试方式是少依赖自然语言文案，多断言行为结构，例如：

- 旧消息被移入 summary。
- recent messages 保留正确。
- tool 消息没有被截断到丢失工具名。
- summarizer 被调用的次数正确。

## 八、安全与可靠性

### 25. 当前 `SafetyPolicy` 能防住什么，防不住什么？

**参考答案：**

能防住一些基础问题：

- 输入过长。
- 明显的 prompt injection 关键词。
- 不在白名单里的工具调用。
- 工具参数过大。
- 单步工具调用过多。

但防不住更复杂的问题：

- 语义变体的 prompt injection。
- 工具返回内容中的间接 prompt injection。
- 文件系统越权访问。
- 模型通过合法工具做危险操作。
- 数据泄露类攻击。

所以它是第一层防线，不是完整安全系统。

### 26. `SecretRedactor` 的作用和局限是什么？

**参考答案：**

`SecretRedactor` 用正则把 API key、token、password、私钥等敏感信息替换成 `[REDACTED]`，避免它们进入 memory、日志或模型上下文。

局限是正则只能覆盖常见格式，不能保证识别所有秘密。它也可能误伤普通文本。生产环境还需要更系统的 secret scanning、日志策略和权限控制。

### 27. 工具失败时为什么返回 `ToolResult(ok=False)`，而不是直接抛异常？

**参考答案：**

因为工具失败本身也是模型可以理解的上下文。比如搜索失败、文件不存在、PDF 无法解析，模型可以基于失败原因向用户解释，或者选择其他工具。

如果直接抛异常，整个 Agent run 可能中断，交互体验较差。当前 `FunctionTool.run()` 也会兜底捕获异常并转换成失败结果。

## 九、测试与工程化

### 28. 如何在不真实调用模型 API 的情况下测试 Agent loop？

**参考答案：**

可以实现一个 fake provider 或 fake policy，让它按预设顺序返回：

1. 第一次返回 assistant 消息和 tool call。
2. Agent 执行 fake tool。
3. 第二次返回最终回答。

这样可以断言：

- 用户消息是否进入 memory。
- assistant tool call 是否进入 memory。
- tool result 是否正确写回。
- 最大 steps 是否正确。
- 最终 `AgentResult.output` 是否符合预期。

这种测试不依赖网络，也不消耗模型费用。

### 29. 你会优先补哪些测试？

**参考答案：**

我会优先补这些：

1. `Agent.run()` 的完整工具调用循环测试。
2. `OpenAIProvider` 解析普通回答、tool calls、异常响应的测试。
3. `SafetyPolicy` 对输入、工具名、参数大小的拦截测试。
4. 文件读写工具的路径安全测试。
5. bootstrap 在启用和不启用 MCP 时的组装测试。

这些测试能覆盖最容易出线上问题的边界。

### 30. 当前项目缺少依赖声明会带来什么问题？

**参考答案：**

缺少 `pyproject.toml` 或 `requirements.txt` 会导致：

- 新环境不知道要安装哪些依赖。
- Pylance 或类型检查器找不到包。
- 可选功能如 `pypdf`、`python-docx`、`mcp` 是否可用不明确。
- CI 难以稳定复现测试环境。

建议用 `pyproject.toml` 管理依赖，并把可选能力拆成 extras，例如：

```toml
[project.optional-dependencies]
pdf = ["pypdf"]
docx = ["python-docx"]
mcp = ["mcp[cli]"]
dev = ["pytest", "ruff", "pyright"]
```

## 十、架构扩展题

### 31. 如果要新增一个 DeepSeek provider，你会怎么做？

**参考答案：**

如果 DeepSeek 使用 OpenAI-compatible 协议，可以复用 `OpenAIProvider`，只需要配置不同的 `BASE_URL` 和 `MODEL`。

如果协议有差异，可以新增 `DeepSeekProvider`，实现 `complete(context: AgentContext) -> AgentAction`，内部负责请求和响应解析。然后在 bootstrap 根据配置选择 provider。

关键是不改 `Agent`、`ToolRegistry` 和 `Memory`，只替换 provider 层。

### 32. 如果要做 RAG，本项目应该在哪一层扩展？

**参考答案：**

可以从工具层开始扩展，把 RAG 能力作为工具暴露给模型，例如：

- `index_documents`
- `search_knowledge_base`
- `read_chunk`

长期可以单独增加 `knowledge` 或 `retrieval` 模块，负责文档切分、embedding、向量检索和缓存。Agent 仍然通过工具调用使用这些能力。

这样 RAG 不会污染 provider，也不会让 Agent loop 变复杂。

### 33. 如果要把 CLI 换成 Web API，你会改哪些地方？

**参考答案：**

核心 `Agent` 不需要大改。主要新增一个 Web application 层：

- FastAPI 或 Flask endpoint 接收用户输入。
- 调用 `build_agent()` 或复用 agent factory。
- 把 `AgentResult` 转成 JSON。
- 处理会话 ID 和 memory 生命周期。

需要重点设计的是多用户隔离：每个用户或会话应该有独立 memory，不能共享同一个全局 Agent memory。

### 34. 如果要支持多用户并发，这个项目当前有什么挑战？

**参考答案：**

当前 Agent 持有 memory，适合单会话同步 CLI。多用户并发时会有挑战：

- memory 不能全局共享。
- ToolRegistry 可以共享，但有状态工具要注意线程安全。
- provider 请求可以并发，但需要限流。
- MCP client 是否能并发调用要确认。
- 日志需要带 request id 或 session id。

解决方案是引入 session manager，为每个会话创建或加载独立 memory，并把 Agent 设计成按会话运行。

### 35. 如果面试官问“这个项目离生产级还有哪些差距”，你怎么答？

**参考答案：**

我会从几个方向回答：

- 依赖管理：需要 `pyproject.toml`、extras、CI。
- 安全：文件工具需要 workspace 沙箱，危险操作需要确认机制。
- 可观测性：需要 request id、trace、usage、latency、工具耗时。
- 测试：需要覆盖 Agent loop、provider、tool、safety、bootstrap。
- 配置：memory、max steps、tool limits、provider 参数都应该配置化。
- 并发：当前更偏单用户同步 CLI，多用户服务化需要 session 隔离。
- 文档：需要安装说明、配置说明、工具开发指南和 MCP 使用指南。

这个回答既承认不足，也体现你知道下一步如何演进。

## 十一、综合开放题

### 36. 你在这个项目里最想重构哪一块？为什么？

**参考答案：**

我会优先重构入口和配置：

1. 让 `agent_llm.py` 只调用 `application.cli.run_cli()`，消除重复 REPL 逻辑。
2. 增加 `pyproject.toml`，明确依赖和可选功能。
3. 把 `max_steps`、`max_messages`、`SYSTEM_PROMPT`、MCP 开关等统一纳入配置对象。

原因是这些属于工程地基。地基稳定后，再做 streaming、RAG、多 provider、多 MCP server 会更顺。

### 37. 你如何评价这个项目的抽象粒度？

**参考答案：**

整体抽象粒度是合理的，没有过度设计。`Agent`、`Policy`、`Provider`、`Tool`、`Memory`、`Safety` 都对应真实变化点。

不过有些地方可以继续收敛：

- 顶层兼容模块和新分层模块之间要明确推荐导入路径。
- `agent_llm.py` 和 `application.cli` 的入口逻辑应该统一。
- memory 的自然语言摘要文案最好和测试约定固定下来。

也就是说，大方向正确，但还需要工程化打磨。

### 38. 如果让你现场演示这个项目，你会展示什么？

**参考答案：**

我会展示三条路径：

1. 普通问答：说明 CLI、provider、memory 基础链路可用。
2. 工具调用：让模型调用 `calculator`、`utc_now` 或 `read_file`，展示 tool calling loop。
3. MCP：设置 `ENABLE_MCP=1`，展示本地 workspace MCP server 的工具发现和调用。

演示时我会同时解释：模型只决定调用什么工具，真正的工具执行权在 Agent runtime 和 safety policy 手里。

### 39. 如果模型返回了恶意工具参数，系统如何处理？还能怎么增强？

**参考答案：**

当前系统会检查工具名是否允许、参数 JSON 大小是否超限。但具体参数是否合法主要由工具 handler 自己负责。

可以增强为：

- 使用 JSON Schema 对工具参数做运行时校验。
- 对文件路径统一做 workspace sandbox。
- 对写文件、删除文件、执行命令等高风险工具加人工确认。
- 给工具分权限等级。
- 对工具调用做审计日志。

### 40. 这个项目最适合作为什么方向继续发展？

**参考答案：**

我认为最适合发展成“本地可扩展 Agent 框架”或“个人知识工作助手”。

原因是它已经具备几个关键基础：

- 模型 provider 可替换。
- 工具系统已经成型。
- MCP 接入已经有雏形。
- 文件、PDF、DOCX、搜索、天气等工具已经覆盖常见场景。
- memory 和 safety 已经有基础抽象。

下一步如果补上依赖管理、文件沙箱、RAG、CLI 命令和测试，就会从课程项目变成一个比较完整的本地 Agent runtime。
