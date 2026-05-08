"""
因果追踪 — append-only event sourcing 的查询层

提供完整的记忆修改历史、变更追溯和审计能力。
"""

import json
from .store import EventStore


def trace_key(store: EventStore, key: str, namespace: str = "default") -> dict:
    """查询某个 key 的完整修改历史"""
    conn = store.conn
    cursor = conn.execute(
        """SELECT event_id, agent_id, operation, value, parent_event_id, tags, created_at
           FROM events
           WHERE namespace=? AND key=?
           ORDER BY created_at ASC""",
        (namespace, key)
    )
    events = []
    for row in cursor.fetchall():
        events.append({
            "event_id": row["event_id"],
            "agent_id": row["agent_id"],
            "operation": row["operation"],
            "value": row["value"][:300] + "..." if len(row["value"]) > 300 else row["value"],
            "parent_event_id": row["parent_event_id"],
            "tags": json.loads(row["tags"]),
            "created_at": row["created_at"],
        })

    result = store.get(key, namespace)
    return {
        "key": key,
        "namespace": namespace,
        "current": result,
        "total_events": len(events),
        "history": events,
    }


def trace_agent(store: EventStore, agent_id: str, limit: int = 50) -> list[dict]:
    """查询某个 Agent 的所有操作历史"""
    conn = store.conn
    cursor = conn.execute(
        """SELECT event_id, namespace, key, operation, value, created_at
           FROM events WHERE agent_id=?
           ORDER BY created_at DESC LIMIT ?""",
        (agent_id, limit)
    )
    return [
        {
            "event_id": row["event_id"],
            "namespace": row["namespace"],
            "key": row["key"],
            "operation": row["operation"],
            "value": row["value"][:200] + "..." if len(row["value"]) > 200 else row["value"],
            "created_at": row["created_at"],
        }
        for row in cursor.fetchall()
    ]


def export_snapshot(store: EventStore, namespace: str = "default") -> list[dict]:
    """导出命名空间的当前记忆快照"""
    return store.list_namespace(namespace, limit=1000)


def export_provenance(store: EventStore, namespace: str = "default",
                      limit: int = 500) -> list[dict]:
    """导出命名空间的完整事件日志（审计用途）"""
    conn = store.conn
    cursor = conn.execute(
        """SELECT event_id, agent_id, key, operation, value, parent_event_id, tags, created_at
           FROM events WHERE namespace=?
           ORDER BY created_at ASC LIMIT ?""",
        (namespace, limit)
    )
    return [dict(row) for row in cursor.fetchall()]
