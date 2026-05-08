"""
冲突检测 — 多 Agent 同时修改同一记忆时的冲突发现

设计：
- 乐观并发：写入时不加锁，事后检测冲突
- 同一 key 在短时间内被不同 Agent 修改 → 标记为潜在冲突
- 冲突信息附加到结果，不阻断正常操作
"""

import time
from .store import EventStore, _now


def check_conflict(store: EventStore, key: str, namespace: str = "default",
                   window_minutes: int = 60) -> dict:
    """检查某个 key 在时间窗口内是否有多个 Agent 修改"""
    conn = store.conn
    cutoff = _now_offset(-window_minutes * 60)

    cursor = conn.execute(
        """SELECT DISTINCT agent_id, COUNT(*) as edit_count, MAX(created_at) as last_edit
           FROM events
           WHERE namespace=? AND key=?
             AND operation IN ('add','update')
             AND created_at >= ?
           GROUP BY agent_id
           ORDER BY last_edit DESC""",
        (namespace, key, cutoff)
    )
    rows = cursor.fetchall()

    agents_involved = [dict(r) for r in rows]
    has_conflict = len(agents_involved) > 1

    return {
        "key": key,
        "namespace": namespace,
        "has_conflict": has_conflict,
        "agents_involved": agents_involved,
        "window_minutes": window_minutes,
        "checked_at": _now(),
    }


def list_conflicts(store: EventStore, namespace: str = "default",
                   window_minutes: int = 60) -> list[dict]:
    """列出命名空间下所有有冲突的 key"""
    conn = store.conn
    cutoff = _now_offset(-window_minutes * 60)

    cursor = conn.execute(
        """SELECT key, COUNT(DISTINCT agent_id) as agent_count
           FROM events
           WHERE namespace=? AND operation IN ('add','update')
             AND created_at >= ?
           GROUP BY key
           HAVING agent_count > 1
           ORDER BY agent_count DESC""",
        (namespace, cutoff)
    )

    conflicts = []
    for row in cursor.fetchall():
        detail = check_conflict(store, row["key"], namespace, window_minutes)
        conflicts.append(detail)

    return conflicts
