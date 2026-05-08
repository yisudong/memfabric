"""
命名空间管理 — 多 Agent 记忆隔离与共享

设计：
- 每个 Agent 默认拥有独立命名空间（按 agent_id 隔离）
- 命名空间可配置为共享（跨 Agent 可见）或私有（仅限自己）
- 搜索时可指定多个命名空间
"""


class NamespaceManager:
    """命名空间管理器"""

    def __init__(self):
        # namespace -> set of agent_ids that can read
        self._readable: dict[str, set[str]] = {}
        # namespace -> set of agent_ids that can write
        self._writable: dict[str, set[str]] = {}
        # agent_id -> default namespace
        self._agent_defaults: dict[str, str] = {}

    def register_agent(self, agent_id: str, default_namespace: str = "default"):
        """注册 Agent，创建默认命名空间"""
        ns = f"agent:{agent_id}" if default_namespace == "default" else default_namespace
        self._agent_defaults[agent_id] = ns
        if ns not in self._readable:
            self._readable[ns] = set()
            self._writable[ns] = set()
        self._readable[ns].add(agent_id)
        self._writable[ns].add(agent_id)
        # 所有 agent 默认可读 "default" 和 "shared" 命名空间
        for shared_ns in ["default", "shared"]:
            if shared_ns not in self._readable:
                self._readable[shared_ns] = set()
                self._writable[shared_ns] = set()
            self._readable[shared_ns].add(agent_id)
            self._writable[shared_ns].add(agent_id)

    def default_for(self, agent_id: str) -> str:
        """获取 Agent 的默认命名空间"""
        return self._agent_defaults.get(agent_id, "default")

    def can_read(self, agent_id: str, namespace: str) -> bool:
        """检查 Agent 是否有读取权限"""
        if namespace in ("default", "shared"):
            return True
        if namespace.startswith("agent:") and namespace == f"agent:{agent_id}":
            return True
        return agent_id in self._readable.get(namespace, set())

    def can_write(self, agent_id: str, namespace: str) -> bool:
        """检查 Agent 是否有写入权限"""
        if namespace in ("default", "shared"):
            return True
        if namespace.startswith("agent:") and namespace == f"agent:{agent_id}":
            return True
        return agent_id in self._writable.get(namespace, set())

    def share(self, namespace: str, reader_agent_id: str):
        """向其他 Agent 共享命名空间的读取权限"""
        if namespace not in self._readable:
            self._readable[namespace] = set()
        self._readable[namespace].add(reader_agent_id)

    def list_readable(self, agent_id: str) -> list[str]:
        """列出 Agent 可读的所有命名空间"""
        result = []
        for ns, agents in self._readable.items():
            if agent_id in agents:
                result.append(ns)
        return result if result else ["default"]
