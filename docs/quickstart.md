# MemFabric — 跨 Agent 统一记忆织物

所有 AI Agent 共享的记忆基础设施。换 Agent 就像换手机——数据跟着你走。

## 核心概念

- **记忆织物（Memory Fabric）**：所有 Agent 共享的统一记忆层
- **命名空间（Namespace）**：每个 Agent 有自己的记忆空间，可选择性共享
- **append-only**：所有写入不可变，完整审计追溯
- **MCP 协议**：标准 MCP Server，任何 Agent 框架适配

## 安装

```bash
pip install memfabric
```

## 接入你的 Agent

### Claude Code

在项目根目录或 `~/.claude/.mcp.json` 添加:

```json
{
  "mcpServers": {
    "memfabric": {
      "command": "python3",
      "args": ["-m", "memfabric.server"]
    }
  }
}
```

### Codex

在 `.codex/config.json` 中添加:

```json
{
  "mcp_servers": {
    "memfabric": {
      "command": "python3",
      "args": ["-m", "memfabric.server"]
    }
  }
}
```

### Cursor

Settings → Features → MCP Servers → Add:

```json
{
  "mcpServers": {
    "memfabric": {
      "command": "python3",
      "args": ["-m", "memfabric.server"]
    }
  }
}
```

### Hermes Agent

在 `config.yaml` 中:

```yaml
mcp_servers:
  memfabric:
    command: python3
    args: ["-m", "memfabric.server"]
```

### OpenClaw

在 `openclaw.json` 中:

```json
{
  "mcp": {
    "servers": {
      "memfabric": {
        "command": "python3",
        "args": ["-m", "memfabric.server"]
      }
    }
  }
}
```

## 使用示例

### 存储记忆

```
请帮我把这段信息记下来：我喜欢的 Python 格式化工具是 black，行宽 100

→ Agent 调用 memory_add(key="user-pref-python-formatter", value="使用 black，行宽 100", tags=["preference","python"])
```

### 搜索记忆

```
我之前说过的 Python 格式化偏好是什么？

→ Agent 调用 memory_search(query="Python 格式化偏好")
→ 返回: { key: "user-pref-python-formatter", value: "使用 black，行宽 100", score: 0.92 }
```

### 查看记忆历史

```
memory_get(key="user-pref-python-formatter")

→ 返回最新值 + 完整修改历史（谁在什么时候改了什么）
```

### 跨 Agent 共享

Claude Code 中存储的记忆，Hermes Agent 在 Telegram 聊天中也能搜到：

```python
# Claude Code 中
memory_add(key="project-deployment-server", value="生产环境: 192.168.1.100", namespace="shared")

# Hermes Agent 中（不同 Agent，同一用户）
memory_search(query="生产服务器地址", namespace=["shared"])
```

## 工具列表

| 工具 | 功能 |
|------|------|
| `memory_add` | 写入记忆 |
| `memory_search` | 混合搜索（向量+关键词） |
| `memory_get` | 精确查询 + 完整历史 |
| `memory_link` | 建立记忆关联 |
| `memory_recall` | 上下文自动召回 |
| `memory_forget` | 软删除（保留审计） |

## 配置

配置文件位于 `~/.memfabric/config.yaml`:

```yaml
max_entry_chars: 2200
max_search_results: 8
min_search_score: 0.35
embedding_model: all-MiniLM-L6-v2
vector_weight: 0.7
text_weight: 0.3
default_namespace: default
```

## 数据存储

```
~/.memfabric/
├── store.db          # SQLite append-only event log
├── vectors/          # ChromaDB 向量索引
└── config.yaml       # 配置文件
```

## 设计哲学

MemFabric 不是另一个 Agent 框架——它是 Agent 世界的数据层。

- **卖铲子**：不管哪个 Agent 框架赢，都需要记忆基础设施
- **可审计**：append-only log，完整因果链，适合企业合规
- **零运维**：stdio 模式，Agent 启动时自动拉起，无需独立服务
- **隐私优先**：数据在本地，加密可选
