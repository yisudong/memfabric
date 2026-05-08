"""
MemFabric — 跨 Agent 统一记忆织物

所有 AI Agent 共享的记忆基础设施。
Agent 是应用层，MemFabric 是数据层。

核心设计原则：
1. append-only event log — 所有写操作不可变，可完整审计
2. 跨 Agent 命名空间 — 每个 Agent 独立记忆空间，可配置共享
3. 因果追踪 — 每次修改记录前驱 event，构建完整血缘链
4. 向量+关键词混合搜索 — 语义理解和精确匹配兼顾
5. MCP 标准协议 — 一次开发，所有 Agent 通用
"""
