"""
MCP Server — 跨 Agent 统一记忆织物入口

通过 MCP stdio 协议向所有 Agent 框架暴露记忆工具。
无需独立服务，Agent 启动时自动拉起为子进程。

工具列表：
  memory_add     — 写入新记忆
  memory_search  — 语义/关键词混合搜索
  memory_get     — 精确查询（含完整修改历史）
  memory_link    — 建立记忆间关联
  memory_recall  — 基于上下文自动召回相关记忆
  memory_forget  — 软删除记忆

用法：
  python -m memfabric.server           # stdio 模式（任何支持 MCP 的 Agent 自动拉起）
  pip install memfabric                # 安装为可执行命令
  memfabric                            # 或直接运行
"""

import sys
import json
import os
import asyncio
from pathlib import Path

from .config import load_config
from .store import EventStore
from .vector import VectorIndex
from .namespace import NamespaceManager
from . import lineage as lineage_mod
from . import conflict as conflict_mod


# ============================================================
# 核心引擎
# ============================================================

class MemFabric:
    """记忆织物核心引擎"""

    def __init__(self, home: Path | None = None):
        self.config = load_config(home)
        self.store = EventStore(self.config.store_path)
        self.vector = VectorIndex(self.config.vector_path, self.config.embedding_model)
        self.namespaces = NamespaceManager()
        # 注册默认 agent
        self.namespaces.register_agent("default", "default")

    def _ensure_agent(self, agent_id: str):
        """懒注册 Agent"""
        if agent_id not in self.namespaces._agent_defaults:
            self.namespaces.register_agent(agent_id)

    # ---- 写入 ----

    def memory_add(self, agent_id: str, key: str, value: str,
                   namespace: str | None = None, tags: list[str] | None = None) -> dict:
        """写入新记忆"""
        self._ensure_agent(agent_id)
        ns = namespace or self.namespaces.default_for(agent_id)

        if not self.namespaces.can_write(agent_id, ns):
            return {"ok": False, "error": f"无写入权限: namespace={ns}"}

        result = self.store.add(agent_id, key, value, ns, tags)

        # 同步到向量索引
        try:
            self.vector.add(result["event_id"], key, value, ns, agent_id, tags)
        except Exception as e:
            pass  # 向量索引失败不影响主流程

        result["ok"] = True
        return result

    # ---- 搜索 ----

    def memory_search(self, agent_id: str, query: str,
                      namespace: str | list[str] | None = None,
                      limit: int = 6, min_score: float = 0.0) -> dict:
        """混合搜索：向量语义 + 关键词匹配"""
        self._ensure_agent(agent_id)

        # 确定可搜索的命名空间
        if namespace is None:
            namespaces = self.namespaces.list_readable(agent_id)
        elif isinstance(namespace, str):
            namespaces = [namespace]
        else:
            namespaces = namespace

        # 权限过滤
        allowed = [ns for ns in namespaces if self.namespaces.can_read(agent_id, ns)]
        if not allowed:
            return {"ok": True, "results": [], "query": query, "method": "hybrid"}

        # 向量搜索
        vector_results = []
        try:
            vector_results = self.vector.search(query, allowed, limit, min_score)
        except Exception:
            pass

        # 关键词搜索
        keyword_results = self.store.search_keyword(query, allowed, limit)

        # 混合融合
        merged = _hybrid_merge(vector_results, keyword_results,
                               vector_weight=self.config.vector_weight,
                               text_weight=self.config.text_weight,
                               limit=limit)

        # 去重（按 key）
        seen = set()
        results = []
        for r in merged:
            key_ns = f"{r['namespace']}:{r['key']}"
            if key_ns not in seen:
                seen.add(key_ns)
                results.append(r)
                if len(results) >= limit:
                    break

        return {
            "ok": True,
            "results": results,
            "query": query,
            "total": len(results),
            "namespaces_searched": allowed,
            "method": "hybrid",
        }

    # ---- 精确查询 ----

    def memory_get(self, agent_id: str, key: str,
                   namespace: str | None = None) -> dict:
        """精确查询记忆（含完整修改历史）"""
        self._ensure_agent(agent_id)
        ns = namespace or self.namespaces.default_for(agent_id)

        if not self.namespaces.can_read(agent_id, ns):
            return {"ok": False, "error": f"无读取权限: namespace={ns}"}

        result = lineage_mod.trace_key(self.store, key, ns)
        if result["current"] is None:
            return {"ok": True, "found": False, "key": key, "namespace": ns}

        return {
            "ok": True,
            "found": True,
            "key": key,
            "namespace": ns,
            "value": result["current"]["value"],
            "agent_id": result["current"]["agent_id"],
            "tags": result["current"]["tags"],
            "updated_at": result["current"]["updated_at"],
            "total_events": result["total_events"],
            "history": result["history"],
        }

    # ---- 关联 ----

    def memory_link(self, agent_id: str, source_key: str, target_key: str,
                    relation_type: str, namespace: str | None = None) -> dict:
        """建立两个记忆之间的关联"""
        self._ensure_agent(agent_id)
        ns = namespace or self.namespaces.default_for(agent_id)

        if not self.namespaces.can_write(agent_id, ns):
            return {"ok": False, "error": f"无写入权限: namespace={ns}"}

        result = self.store.link(agent_id, source_key, target_key, relation_type, ns)
        result["ok"] = True
        return result

    # ---- 召回 ----

    def memory_recall(self, agent_id: str, context_hint: str = "",
                      limit: int = 5) -> dict:
        """基于上下文自动召回最相关的记忆"""
        self._ensure_agent(agent_id)

        namespaces = self.namespaces.list_readable(agent_id)

        if context_hint:
            # 有上下文提示时，用语义搜索
            search_result = self.memory_search(
                agent_id, context_hint, namespaces, limit=limit
            )
            return {
                "ok": True,
                "mode": "semantic",
                "context_hint": context_hint,
                "results": search_result.get("results", []),
            }
        else:
            # 无提示时，返回最近更新的记忆（跨所有可读命名空间）
            all_recent = []
            for ns in namespaces:
                recent = self.store.list_namespace(ns, limit=limit // len(namespaces) + 1)
                all_recent.extend(recent)
            all_recent.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
            return {
                "ok": True,
                "mode": "recent",
                "results": all_recent[:limit],
            }

    # ---- 忘记 ----

    def memory_forget(self, agent_id: str, key: str, reason: str = "",
                      namespace: str | None = None) -> dict:
        """软删除记忆"""
        self._ensure_agent(agent_id)
        ns = namespace or self.namespaces.default_for(agent_id)

        if not self.namespaces.can_write(agent_id, ns):
            return {"ok": False, "error": f"无写入权限: namespace={ns}"}

        result = self.store.forget(agent_id, key, reason, ns)

        # 从向量索引移除
        try:
            self.vector.remove(result["event_id"])
        except Exception:
            pass

        result["ok"] = True
        return result


# ============================================================
# MCP Server
# ============================================================

def _build_mcp_response(request_id: str | int, result: dict) -> str:
    """构建 MCP JSON-RPC 响应"""
    response = {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }
    return json.dumps(response, ensure_ascii=False) + "\n"


def _build_mcp_error(request_id: str | int | None, code: int, message: str) -> str:
    """构建 MCP JSON-RPC 错误"""
    response = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
    return json.dumps(response, ensure_ascii=False) + "\n"


# MCP Tools 定义
TOOL_SCHEMAS = [
    {
        "name": "memory_add",
        "description": "写入一条新记忆到持久化记忆织物。所有 Agent 可共享访问。"
                       "用于存储用户偏好、重要决策、环境配置等需要跨会话保留的信息。"
                       "不要存储任务进度、临时状态或会话级内容。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "记忆的唯一标识，如 'user-pref-editor' 或 'project-api-endpoint'"},
                "value": {"type": "string", "description": "记忆内容，支持多行文本"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "标签列表，如 ['preference','coding']"},
                "namespace": {"type": "string", "description": "命名空间，默认为当前 Agent 的专属空间。使用 'shared' 可写入共享空间"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "memory_search",
        "description": "在记忆织物中搜索。混合向量语义搜索和关键词匹配。"
                       "用于查找之前存储的偏好、决策、用户信息等。"
                       "跨命名空间搜索，可以发现其他 Agent 写入的共享记忆。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询，自然语言或关键词"},
                "limit": {"type": "integer", "description": "返回结果数上限，默认 6"},
                "namespace": {"type": "array", "items": {"type": "string"}, "description": "限定搜索的命名空间列表，不指定则搜索所有可读空间"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_get",
        "description": "精确查询某条记忆的完整内容和修改历史。"
                       "返回最新值、所有历史版本和修改者信息（完整血缘链）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "记忆的唯一标识"},
                "namespace": {"type": "string", "description": "命名空间，默认使用当前 Agent 的默认空间"},
            },
            "required": ["key"],
        },
    },
    {
        "name": "memory_link",
        "description": "建立两条记忆之间的关联关系。"
                       "用于构建知识图谱，比如将 'bug-fix-x' 关联到 'error-pattern-y'。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_key": {"type": "string", "description": "源记忆的 key"},
                "target_key": {"type": "string", "description": "目标记忆的 key"},
                "relation_type": {"type": "string", "description": "关系类型: 'related', 'caused-by', 'depends-on', 'supersedes'"},
                "namespace": {"type": "string", "description": "命名空间"},
            },
            "required": ["source_key", "target_key", "relation_type"],
        },
    },
    {
        "name": "memory_recall",
        "description": "基于当前上下文自动召回最相关的记忆。"
                       "在每次对话开始时或需要上下文时调用，获取与当前任务相关的记忆。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "context_hint": {"type": "string", "description": "当前对话的上下文提示，用于语义匹配。如 '正在处理部署脚本' 或 '讨论 Python 性能优化'"},
                "limit": {"type": "integer", "description": "返回结果数上限，默认 5"},
            },
            "required": [],
        },
    },
    {
        "name": "memory_forget",
        "description": "软删除一条记忆。记忆内容保留在历史记录中可审计，但不再出现在当前状态和搜索结果中。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "要删除的记忆 key"},
                "reason": {"type": "string", "description": "删除原因，用于审计追踪"},
                "namespace": {"type": "string", "description": "命名空间"},
            },
            "required": ["key"],
        },
    },
]

# Agent ID 从环境变量或进程信息推断
AGENT_ID = os.environ.get("MEMFABRIC_AGENT_ID",
                          os.environ.get("USER", "default"))


def handle_request(mf: MemFabric, request: dict) -> str:
    """处理单个 MCP JSON-RPC 请求"""
    method = request.get("method", "")
    req_id = request.get("id", 0)

    try:
        # ---- 初始化 ----
        if method == "initialize":
            return _build_mcp_response(req_id, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {
                    "name": "memfabric",
                    "version": "0.1.0",
                },
                "capabilities": {
                    "tools": {},
                },
            })

        # ---- 列出工具 ----
        elif method == "tools/list":
            return _build_mcp_response(req_id, {
                "tools": TOOL_SCHEMAS,
            })

        # ---- 调用工具 ----
        elif method == "tools/call":
            params = request.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            agent_id = arguments.pop("_agent_id", AGENT_ID)

            if tool_name == "memory_add":
                result = mf.memory_add(
                    agent_id=agent_id,
                    key=arguments["key"],
                    value=arguments["value"],
                    namespace=arguments.get("namespace"),
                    tags=arguments.get("tags"),
                )
            elif tool_name == "memory_search":
                result = mf.memory_search(
                    agent_id=agent_id,
                    query=arguments["query"],
                    namespace=arguments.get("namespace"),
                    limit=arguments.get("limit", 6),
                )
            elif tool_name == "memory_get":
                result = mf.memory_get(
                    agent_id=agent_id,
                    key=arguments["key"],
                    namespace=arguments.get("namespace"),
                )
            elif tool_name == "memory_link":
                result = mf.memory_link(
                    agent_id=agent_id,
                    source_key=arguments["source_key"],
                    target_key=arguments["target_key"],
                    relation_type=arguments["relation_type"],
                    namespace=arguments.get("namespace"),
                )
            elif tool_name == "memory_recall":
                result = mf.memory_recall(
                    agent_id=agent_id,
                    context_hint=arguments.get("context_hint", ""),
                    limit=arguments.get("limit", 5),
                )
            elif tool_name == "memory_forget":
                result = mf.memory_forget(
                    agent_id=agent_id,
                    key=arguments["key"],
                    reason=arguments.get("reason", ""),
                    namespace=arguments.get("namespace"),
                )
            else:
                return _build_mcp_error(req_id, -32601, f"未知工具: {tool_name}")

            return _build_mcp_response(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
            })

        # ---- 未知方法 ----
        else:
            return _build_mcp_error(req_id, -32601, f"未知方法: {method}")

    except Exception as e:
        return _build_mcp_error(req_id, -32603, str(e))


# ============================================================
# 入口
# ============================================================

def main():
    """MCP stdio 模式入口 — Agent 通过子进程拉起"""

    # 读取 stdio 输入
    try:
        request_str = sys.stdin.readline()
        if not request_str:
            return
        request = json.loads(request_str)
    except (json.JSONDecodeError, EOFError):
        return

    mf = MemFabric()

    # 处理请求
    response = handle_request(mf, request)

    # 输出响应
    sys.stdout.write(response)
    sys.stdout.flush()


def run_stdio_loop():
    """持续 stdio 循环 — 处理多个 MCP 请求（用于 Agent 长时间运行）"""
    mf = MemFabric()

    # MCP 协议：第一行必须是 initialize
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = handle_request(mf, request)
        sys.stdout.write(response)
        sys.stdout.flush()

        # notifications 不需要响应，跳过写入
        if "id" not in request:
            continue


def _hybrid_merge(vector_results: list[dict], keyword_results: list[dict],
                  vector_weight: float = 0.7, text_weight: float = 0.3,
                  limit: int = 8) -> list[dict]:
    """混合融合：向量结果和关键词结果按权重合并排序"""
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}

    # 向量结果（score 已归一化 0-1）
    for r in vector_results:
        eid = r.get("event_id", r.get("key", ""))
        scores[eid] = scores.get(eid, 0) + vector_weight * r.get("score", 0.5)
        items[eid] = {**r, "source": "vector"}

    # 关键词结果（无 score，给固定分）
    for r in keyword_results:
        eid = r.get("event_id", r.get("key", ""))
        scores[eid] = scores.get(eid, 0) + text_weight * 0.6
        if eid not in items:
            items[eid] = {**r, "source": "keyword", "score": 0.6}

    # 按最终得分排序
    sorted_ids = sorted(scores, key=lambda k: scores[k], reverse=True)
    result = []
    for eid in sorted_ids[:limit]:
        item = dict(items[eid])
        item["score"] = round(scores[eid], 4)
        result.append(item)

    return result


if __name__ == "__main__":
    run_stdio_loop()
