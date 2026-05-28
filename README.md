# agentllm

面向生产的模块化智能代理运行时，具备以下特性：

- OpenAI 兼容的 LLM 提供商支持
- 工具调用
- MCP 集成
- 交互式 CLI 入口
- 分层项目结构，便于长期维护

## 快速开始

### 1. 配置环境

推荐的环境变量：

```env
API_KEY=your_api_key
BASE_URL=https://api.deepseek.com/v1
MODEL=deepseek-v4-flash
```

运行时还支持以下兼容性命名：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`
- `TIMEOUT_SECONDS`
- `OPENAI_TIMEOUT_SECONDS`

你可以将它们配置在 [agentllm/.env](/d:/AIclass/agentllm_by_codex/agentllm/.env:1) 文件中。

### 2. 启动真实代理

```powershell
python agent_llm.py
```

### 3. 启用 MCP 启动

```powershell
$env:ENABLE_MCP="1"
python agent_llm.py
```

### 4. 退出 CLI

输入：

```text
exit
```

或

```text
quit
```

## 项目结构

```text
agentllm/
  application/   # 引导模块和 CLI
  clients/       # 直接 LLM 客户端
  core/          # 代理运行时、类型定义、记忆、安全、策略
  infra/         # 配置、日志、错误类型
  integrations/  # 面向集成的入口点
  mcp/           # MCP 协议客户端实现
  mcp_servers/   # 本地 MCP 服务器
  providers/     # 模型提供商
  tools/         # 工具抽象和注册表
```

完整架构概览请参见 [docs/architecture.md](</d:/AIclass/agentllm_by_codex/docs/architecture.md>)。

## 主要入口点

- [agent_llm.py](/d:/AIclass/agentllm_by_codex/agent_llm.py:1): 轻量级可执行入口
- [agentllm/application/cli.py](/d:/AIclass/agentllm_by_codex/agentllm/application/cli.py:1): 交互式 CLI
- [agentllm/application/bootstrap.py](/d:/AIclass/agentllm_by_codex/agentllm/application/bootstrap.py:1): 生产环境代理组装

## MCP

当设置 `ENABLE_MCP=1` 时，CLI 会启动工作区 MCP 服务器：

- [agentllm/mcp_servers/workspace.py](/d:/AIclass/agentllm_by_codex/agentllm/mcp_servers/workspace.py:1)

典型的 MCP 能力请求示例：

- `列出 agentllm 目录里的文件`
- `读取 agentllm/core/agent.py`
- `搜索项目里和 memory 相关的实现`

## 测试

运行所有测试：

```powershell
python -m unittest discover -s tests -v
```

## 设计目标

- 将编排逻辑与基础设施分离
- 保持提供商集成可替换
- 保持 MCP 可选
- 避免运行时硬性崩溃
- 保留轻量级可执行入口
