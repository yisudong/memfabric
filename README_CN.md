# MemFabric

<p align="center">
  <img src="assets/logo_enhanced.png" alt="MemFabric Logo" width="280">
</p>

<p align="center">
  <strong>所有 AI Agent 共享的统一记忆层。</strong>
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#工具列表">工具列表</a> ·
  <a href="#架构设计">架构设计</a>
</p>

---

MemFabric 是**跨 Agent 统一记忆织物**——所有 AI Agent 共享的记忆基础设施。换 Agent 就像换手机，数据跟着你走。

## 为什么需要 MemFabric？

今天每个 AI Agent（Claude Code、Cursor、Codex、Hermes、OpenClaw...）都有各自独立的记忆系统。你的编码 Agent 记住了代码偏好，你的个人助手记住了会议时间——但它们**彼此之间完全不互通**。你的记忆碎片散落在不同工具里。

MemFabric 解决的就是这个问题：**一个记忆层，所有 Agent 通用**。

```
现状：                              使用 MemFabric 后：

Agent A ──→ 记忆 A（孤立）          Agent A ──┐
Agent B ──→ 记忆 B（孤立）          Agent B ──┤
Agent C ──→ 记忆 C（孤立）          Agent C ──┼── MemFabric ──→ 统一记忆
Agent D ──→ 记忆 D（孤立）          Agent D ──┤
Agent E ──→ 记忆 E（孤立）          Agent E ──┘
```

## 核心特性

- **跨 Agent 记忆共享** — 一次写入，所有 Agent 可读。命名空间隔离 + 可配置共享策略。
- **MCP 标准协议** — 标准 MCP Server，一次开发，所有支持 MCP 的 Agent 框架通用。
- **零运维** — stdio 模式。Agent 启动时自动拉起为子进程，无需独立服务、守护进程、或复杂配置。
- **Append-Only 事件日志** — 所有记忆修改不可变，完整审计追溯。谁在什么时候改了什么，一清二楚。
- **混合搜索** — 向量语义搜索 + 关键词匹配，可配置权重。
- **因果追踪** — 每条记忆携带完整修改历史（血缘链），支持回溯。
- **冲突检测** — 自动检测多个 Agent 同时修改同一记忆的冲突。

## 快速开始

### 安装

```bash
pip install memfabric
```

### 接入你的 Agent

在你的 Agent MCP 配置中添加（所有主流 Agent 框架均支持）：

**Claude Code**（`.mcp.json`）：
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

**Codex、Cursor、Windsurf、Gemini CLI、Copilot CLI** 均使用相同的 MCP stdio 模式。详见 [configs/](configs/)。

### 开始使用

接入后，你的 Agent 自动获得 6 个记忆工具：

```
你："记住我所有编辑器都喜欢用深色主题"

→ Agent 调用 memory_add(
    key="pref-dark-mode",
    value="用户在所有编辑器中偏好深色主题",
    tags=["preference", "ui"])
```

```
你："我之前说的编辑器偏好是什么？"

→ Agent 调用 memory_search(query="编辑器 深色主题 偏好")
→ 返回："用户在所有编辑器中偏好深色主题" (score: 0.94)
```

```
在 Claude Code 中存储：
  "api-endpoint = https://api.example.com/v2"

在 Telegram 上通过 Hermes Agent 问：
  "我们 API 地址是什么？"

→ Hermes Agent 搜索共享命名空间，找到 Claude Code 存储的结果
```

## 工具列表

| 工具 | 功能 |
|------|------|
| `memory_add` | 写入记忆（key + value + tags + namespace） |
| `memory_search` | 混合搜索（向量语义 + 关键词），跨可读命名空间 |
| `memory_get` | 精确查询一条记忆，含完整修改血缘 |
| `memory_link` | 建立记忆之间的关联（构建知识图谱） |
| `memory_recall` | 基于当前上下文自动召回最相关记忆 |
| `memory_forget` | 软删除记忆（历史保留，可审计） |

## 架构设计

```
┌──────────────────────────────────────────────────┐
│                   用户本地机器                      │
│                                                    │
│  Claude Code ──┐                                  │
│  Cursor ───────┤                                  │
│  Codex ────────┼── stdio (MCP) ──→ MemFabric       │
│  Hermes ───────┤    不走网络                       │
│  OpenClaw ─────┘    无需后台进程                    │
│                                                    │
│  ~/.memfabric/                                     │
│  ├── store.db       ← SQLite append-only 日志       │
│  ├── vectors/       ← ChromaDB 语义索引              │
│  └── config.yaml    ← 命名空间共享策略配置            │
└──────────────────────────────────────────────────┘
```

## 数据模型

所有记忆以 **append-only 事件日志** 方式存储：

```sql
CREATE TABLE events (
    event_id    TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,        -- 哪个 Agent 写入的
    namespace   TEXT NOT NULL,        -- 命名空间（隔离单元）
    key         TEXT NOT NULL,        -- 记忆标识
    value       TEXT NOT NULL,        -- 记忆内容
    operation   TEXT NOT NULL,        -- add | update | link | forget
    parent_event_id TEXT,             -- 上一版本（因果链）
    tags        TEXT,                 -- JSON 标签数组
    created_at  TEXT NOT NULL         -- 微秒精度时间戳
);
```

## 命名空间模型

每个 Agent 拥有独立的命名空间（`agent:<id>`），外加两个内置共享空间：

| 命名空间 | 可见性 |
|----------|--------|
| `agent:<id>` | 仅该 Agent 可读写 |
| `shared` | 所有 Agent 可读写 |
| `default` | 所有 Agent 可读写 |

共享策略在 `~/.memfabric/config.yaml` 中配置。

## 配置

```yaml
# ~/.memfabric/config.yaml
max_entry_chars: 2200       # 单条记忆最大字符数
max_search_results: 8       # 每次搜索返回结果上限
min_search_score: 0.35      # 最低相关性分数
vector_weight: 0.7          # 向量搜索权重
text_weight: 0.3            # 关键词搜索权重
default_namespace: default
```

## 设计哲学

MemFabric **不是**又一个新的 Agent 框架。它是 Agent 生态的**数据层**。

- **卖铲子策略**：不管 Agent 框架战争谁赢，都需要好的记忆基础设施。
- **默认可审计**：append-only log，完整因果链，企业合规友好。
- **隐私优先**：数据在本地，加密可选。
- **零绑定**：MCP 开放标准，Agent 可随时接入和退出。

## 竞品对比

| | Agent 内置记忆 | MemFabric |
|---|---|---|
| **范围** | 单 Agent 孤立 | **跨 Agent** 共享记忆 |
| **协议** | 各框架私有 | **MCP 开放标准** |
| **治理** | 无 | **Append-only lineage**，可审计 |
| **冲突处理** | 无 | **多源冲突检测** |
| **可迁移性** | 绑定单一 Agent | **换 Agent 不换记忆** |

## 许可证

MIT

---

<p align="center">
  <sub>为多 Agent 时代而生。一个记忆织物，无限 Agent。</sub>
</p>
